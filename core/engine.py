"""
旺财自动交易系统 - 核心引擎 (支持 AI + 规则策略 + 外部AI 三种模式)

决策模式（config/system.yaml 中设置）：
  decision_mode: "ai"            # AI大模型决策（调用OpenAI/Anthropic API）
  decision_mode: "rule"          # 规则策略决策（Quant Trend Engine）
  decision_mode: "ai_external"  # 外部AI决策（读取QClaw AI Agent输出的JSON文件）
"""

import asyncio
import math
import signal
import sys
import traceback
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from pathlib import Path
import json
import re

from loguru import logger

from core.config_loader import load_config
from modules.market_data import MarketDataModule
from modules.info_aggregator import InfoAggregatorModule
from modules.account_manager import AccountManagerModule
from modules.ai_decision import AIDecisionModule, ActionType, TradeDecision
from modules.risk_control import RiskControlModule
from modules.order_executor import OrderExecutorModule
from modules.logger_notifier import LoggerNotifierModule, AlertLevel, AlertMessage
from modules.daily_review import DailyReviewModule
from modules.strategy_quant_trend import QuantTrendStrategy, StrategySignal, SignalType
from modules.strategy_factory import normalize_strategy_mode
from modules.yanchi_strategy import YanChiStrategy, SignalType as YanChiSignalType

# 可选模块 — ai_external / wyckoff_smc 模式未启用时模块可能不存在
try:
    from modules.ai_external_decision import AIExternalBridge
except ImportError:
    AIExternalBridge = None
try:
    from modules.strategy_wyckoff_smc import WyckoffSMCStrategy
except ImportError:
    WyckoffSMCStrategy = None

from modules.strategy_trinity import TrinityStrategy, StrategySignal as TrinitySignal, SignalType as TrinitySignalType
from modules.token_screener import TokenScreener
from modules.trinity_llm_decide import TrinityLLMDecider


# Fix #12: SimpleSnapshot 定义为模块级类，避免多处重复定义
class SimpleSnapshot:
    """简单的快照对象，用于没有真实快照时提供模拟数据"""
    def __init__(self, price):
        self.avg_price = price


class WangCaiEngine:
    """
    旺财自动交易系统核心引擎

    支持三种决策模式：
    - ai:            大模型AI决策（原有模式，调用OpenAI/Anthropic API）
    - rule:          规则策略决策（QuantTrend Engine，移植自TradingView）
    - ai_external:   外部AI决策（读取QClaw AI Agent输出的JSON文件，推荐模式）
    """

    def __init__(self, config_path: str = "config/system.yaml"):
        self.config = load_config(config_path)
        self.system_config = self.config.get('system', {})
        self.running = False
        self._shutdown_event = asyncio.Event()

        # === 循环计数器 + 最新信号 ===
        self.cycle_count = 0
        self.latest_signal = {}  # 供前端查询

        # === 决策模式 ===
        self.decision_mode = normalize_strategy_mode(self.system_config.get('decision_mode', 'ai'))
        if self.decision_mode not in ('ai', 'rule', 'ai_external', 'wyckoff_smc', 'trinity', 'yanchi'):
            logger.warning("[Engine] 未知决策模式: {}，降级为 ai", self.decision_mode)
            self.decision_mode = 'ai'

        # 初始化日志
        self._setup_logging()

        # 初始化各模块
        self._init_modules()

        # 决策模式显示映射
        mode_display = {
            'ai': "🤖 AI大模型",
            'rule': "📐 规则策略 (QuantTrend)",
            'ai_external': "🌐 外部AI决策",
            'wyckoff_smc': "📊 Wyckoff + SMC 策略",
            'trinity': "🔺 三位一体 (PA+SMC+Wyckoff)",
            'yanchi': "🔥 颜驰合约策略"
        }.get(self.decision_mode, "❓ 未知模式")
        
        logger.info("🐕 旺财自动交易系统初始化完成")
        logger.info("   版本: {} | 模式: {} | 主循环间隔: {}s",
                    self.system_config.get('version', '1.0.0'),
                    self.system_config.get('mode', 'paper'),
                    self.system_config.get('main_loop', {}).get('interval_seconds', 60))
        logger.info("   决策模式: {}", mode_display)

    def _create_yanchi_strategy(self) -> YanChiStrategy:
        """每次分析都创建新实例，避免策略内部状态串号。"""
        params = dict(getattr(self, 'yanchi_strategy_config', {}) or {})
        risk_config = getattr(self, 'config', {}).get('risk', {}) if getattr(self, 'config', None) else {}
        params.setdefault('fixed_position_usdt', risk_config.get('max_single_order_usdt', 10.0))
        params.setdefault('leverage', risk_config.get('leverage_default', 1.0))
        params.setdefault('min_confluence_score', 4.5)
        params.setdefault('min_rr_ratio', 2.0)
        return YanChiStrategy(params)

    def _run_yanchi_analysis(
        self,
        symbol: str,
        df_1h: Optional[Any] = None,
        df_4h: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        颜驰策略的结构化分析结果。
        兼容缓存读取和显式传入的K线数据，便于单测和实盘共用。
        """
        result = {
            'symbol': symbol,
            'action': 'HOLD',
            'signal': 'HOLD',
            'price': 0.0,
            'score': 0.0,
            'confidence': 0.0,
            'reason': '无可用数据',
            'stop_loss': None,
            'take_profit': [],
            'leverage': 1.0,
            'position_size': 0.0,
            'risk_percent': 0.02,
            'confluence_breakdown': {},
        }

        try:
            if df_1h is None and hasattr(self.market_data, '_klines_cache'):
                df_1h = self.market_data._klines_cache.get(f"{symbol}_1h")
            if df_4h is None and hasattr(self.market_data, '_klines_cache'):
                df_4h = self.market_data._klines_cache.get(f"{symbol}_4h")

            source_df = df_1h if df_1h is not None and len(df_1h) > 0 else df_4h
            if source_df is None or len(source_df) < 50:
                result['reason'] = 'K线数据不足'
                return result

            strategy = self._create_yanchi_strategy()
            signal = strategy.generate_signal(
                source_df,
                symbol=symbol,
                df_1h=df_1h,
                df_4h=df_4h,
            )

            result.update({
                'action': signal.signal.value,
                'signal': signal.signal.value,
                'price': signal.price,
                'score': round(signal.score, 2),
                'confidence': round(signal.confidence, 2),
                'reason': signal.reason,
                'stop_loss': signal.stop_price,
                'take_profit': signal.take_profit_levels,
                'leverage': signal.leverage,
                'position_size': signal.position_size,
                'risk_percent': signal.risk_percent,
                'confluence_breakdown': signal.confluence_breakdown,
            })
        except Exception as e:
            logger.warning("[YanChi] {} 分析异常: {}", symbol, str(e)[:120])
            logger.debug(traceback.format_exc())

        return result

    def _build_yanchi_decision(self, c: dict) -> Optional[TradeDecision]:
        """从颜驰策略结果构建 TradeDecision。"""
        action_value = str(c.get('action', 'HOLD')).upper()
        if action_value == 'HOLD':
            return None

        return TradeDecision(
            action=ActionType.BUY if action_value in ('BUY', 'LONG') else ActionType.SELL,
            symbol=c['symbol'],
            amount=float(c.get('position_size', 0.0)),
            price=float(c.get('price', 0.0)) if c.get('price') else None,
            reason=f"[YanChi] {c.get('reason', '')}",
            confidence=float(c.get('confidence', 0.0)),
            stop_loss=c.get('stop_loss'),
            take_profit=c.get('take_profit'),
            leverage=float(c.get('leverage', 1.0)),
            timeframe='1h',
            strategy='yanchi',
        )

    async def _decision_yanchi(self, snapshots, portfolio):
        """颜驰合约策略：50币种批量分析，固定 10U / 1x，RR >= 2。"""
        logger.info("[4/8] 颜驰合约策略分析中...")

        yanchi_config = self.config.get('yanchi', {})
        symbols = self.config.get('exchanges', {}).get('binance', {}).get(
            'preferred_markets',
            yanchi_config.get('symbols', ['BTC/USDT', 'ETH/USDT', 'SOL/USDT'])
        )

        valid_symbols = []
        for sym in symbols:
            base = sym.split('/')[0] if '/' in sym else sym.replace('USDT', '')
            if len(base) >= 2:
                valid_symbols.append(sym)
        symbols = valid_symbols

        if self.screening_enabled and len(symbols) > 5:
            try:
                screened = await self._run_token_screening(symbols, snapshots)
                if screened:
                    symbols = screened
            except Exception as e:
                logger.warning("[YanChi] 预筛选失败，改为全量分析: {}", e)

        analysis_details = []
        candidates = []
        error_count = 0
        semaphore = asyncio.Semaphore(5)

        async def analyze_one(symbol: str) -> Optional[Dict[str, Any]]:
            nonlocal error_count
            async with semaphore:
                detail = {"symbol": symbol, "status": "pending"}
                try:
                    snapshot = snapshots.get(symbol)
                    if not snapshot or snapshot.avg_price <= 0:
                        detail["status"] = "no_data"
                        detail["reason"] = "无行情数据"
                        analysis_details.append(detail)
                        return None

                    df_1h = await self.market_data.fetch_klines(symbol, timeframe='1h', limit=200)
                    df_4h = await self.market_data.fetch_klines(symbol, timeframe='4h', limit=200)
                    result = await asyncio.to_thread(self._run_yanchi_analysis, symbol, df_1h, df_4h)

                    detail.update({
                        "status": "analyzed",
                        "action": result["action"],
                        "score": result["score"],
                        "confidence": result["confidence"],
                        "reason": result["reason"],
                        "price": result["price"],
                    })
                    analysis_details.append(detail)

                    if str(result["action"]).upper() == 'HOLD':
                        return None
                    if result.get('confidence', 0.0) < 0.5:
                        detail["status"] = "low_confidence"
                        return None
                    return result
                except Exception as e:
                    error_count += 1
                    detail["status"] = "error"
                    detail["reason"] = str(e)
                    analysis_details.append(detail)
                    logger.error("[YanChi] {} 分析失败: {}", symbol, e)
                    return None

        results = await asyncio.gather(*[analyze_one(s) for s in symbols])
        candidates = [r for r in results if r is not None]
        candidates.sort(key=lambda x: x['score'], reverse=True)

        status = {
            "status": "signals" if candidates else "no_candidates",
            "cycle": self.cycle_count,
            "timestamp": datetime.now().isoformat(),
            "screening": {
                "total": len(symbols),
                "passed": len(candidates),
                "top_tokens": [
                    {
                        "symbol": c["symbol"],
                        "score": round(c["score"], 1),
                        "price": c["price"],
                        "trend": c["action"],
                    }
                    for c in candidates[:10]
                ],
            },
            "analysis": {
                "analyzed": len(analysis_details),
                "errors": error_count,
                "details": analysis_details[:20],
            },
            "signals": [
                {
                    "symbol": c["symbol"],
                    "action": c["action"],
                    "score": c["score"],
                    "confidence": c["confidence"],
                    "price": c["price"],
                    "reason": c["reason"],
                }
                for c in candidates
            ],
        }
        self._write_strategy_status_json(status)

        if not candidates:
            logger.info("[YanChi] 本轮没有符合条件的候选币种")
            return

        self.latest_signal = {
            "candidates": len(candidates),
            "top_candidates": [
                {"symbol": c["symbol"], "score": c["score"], "action": c["action"]}
                for c in candidates[:5]
            ],
            "timestamp": datetime.now().isoformat(),
            "cycle": self.cycle_count,
        }

        for decision_payload in candidates:
            if portfolio.total_positions >= self.risk_control.position_sizing.max_positions:
                logger.warning("[YanChi] 已达最大持仓数({})，停止开仓", self.risk_control.position_sizing.max_positions)
                break

            decision = self._build_yanchi_decision(decision_payload)
            if decision is None or not decision.is_valid:
                continue

            held_symbols = [p.symbol.replace('/', '') for p in self.order_executor.get_positions()]
            if decision.symbol.replace('/', '') in held_symbols:
                logger.warning("[YanChi] {} 已有持仓，跳过重复开仓", decision.symbol)
                continue

            snapshot = snapshots.get(decision.symbol) or SimpleSnapshot(decision.price or 0)
            self.logger_notifier.save_decision(decision.to_dict())
            await self.logger_notifier.notify_decision(decision.to_dict())
            logger.info("[YanChi] {} -> {} | score={:.2f} | conf={:.2f}",
                        decision.symbol, decision.action.value, decision_payload['score'], decision.confidence)
            await self._execute_decision(decision, snapshot, portfolio)

    # ------------------------------------------------------------------ #
    #  椋庢帶 + 涓嬪崟锛圓I / Rule / External 鍏辩敤锛?
    #  日志 / 模块初始化
    # ------------------------------------------------------------------ #

    def _setup_logging(self):
        log_level = self.system_config.get('log_level', 'INFO')
        log_dir = Path('logs')
        log_dir.mkdir(exist_ok=True)
        # Fix #3 (Round3): Windows stdout 默认 GBK，强制设为 UTF-8 避免中文乱码
        if sys.platform == 'win32' and hasattr(sys.stdout, 'reconfigure'):
            try:
                sys.stdout.reconfigure(encoding='utf-8', errors='replace')
                sys.stderr.reconfigure(encoding='utf-8', errors='replace')
            except Exception:
                pass
        logger.remove()
        logger.add(
            sys.stdout,
            level=log_level,
            format=(
                "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
                "<level>{message}</level>"
            ),
        )
        logger.add(
            log_dir / "wangcai_{time:YYYY-MM-DD}.log",
            rotation="00:00",
            retention="30 days",
            compression="gz",
            level=log_level,
            encoding="utf-8",
        )

    def _init_modules(self):
        """初始化所有模块"""
        # 1. 行情数据模块
        market_config = self.config.get('exchanges', {})
        # 注入代理配置（供 MarketDataModule 使用）
        proxy = self.config.get('proxy', '')
        if proxy:
            market_config['proxy'] = proxy
        market_config['symbols'] = self.config.get('exchanges', {}).get('binance', {}).get('preferred_markets', [])
        market_config['timeframes'] = ['1h', '4h']  # 精简：AI+技术指标核心周期
        self.market_data = MarketDataModule(market_config)

        # 2. 信息聚合模块
        info_config = self.config.get('info', {
            'sources': ['fear_greed', 'funding'],
            'keywords': ['Bitcoin', 'BTC', 'Ethereum', 'ETH']
        })
        proxy = self.config.get('proxy', '')
        if proxy and 'proxy' not in info_config:
            info_config['proxy'] = proxy
        self.info_aggregator = InfoAggregatorModule(info_config)

        # 3. 账户管理模块
        account_config = self.config.get('account', {
            'sync_interval': 30,
            'min_balance_value': 1.0
        })
        self.account_manager = AccountManagerModule(account_config)
        for name, exchange in self.market_data.exchanges.items():
            self.account_manager.register_exchange(name, exchange)

        # 4a. AI 决策模块
        ai_config = self.config.get('ai', {})
        self.ai_decision = AIDecisionModule(ai_config)

        # 4b. 规则策略模块（QuantTrend）
        if self.decision_mode == 'rule':
            strategy_params = self.config.get('strategy', {})
            self.quant_strategy = QuantTrendStrategy(strategy_params)
            logger.info("[Engine] 规则策略模块已加载（QuantTrend）")
        else:
            self.quant_strategy = None

        # 4c. 外部AI决策桥接（ai_external模式）
        if self.decision_mode == 'ai_external':
            if AIExternalBridge is not None:
                ext_ai_config = self.config.get('ai_external', {})
                self.ai_bridge = AIExternalBridge(ext_ai_config)
                logger.info("[Engine] 外部AI决策桥接已加载（QClaw AI Agent）")
            else:
                logger.warning("[Engine] AIExternalBridge 模块不可用，降级")
                self.ai_bridge = None
        else:
            self.ai_bridge = None

        # 4d. Wyckoff + SMC 策略模块
        if self.decision_mode == 'wyckoff_smc':
            if WyckoffSMCStrategy is not None:
                strategy_params = self.config.get('wyckoff_smc', {})
                self.wyckoff_smc_strategy = WyckoffSMCStrategy(strategy_params)
                logger.info("[Engine] Wyckoff + SMC 策略模块已加载（Alpha代币专用）")
            else:
                logger.warning("[Engine] WyckoffSMCStrategy 模块不可用，降级")
                self.wyckoff_smc_strategy = None
        else:
            self.wyckoff_smc_strategy = None


        # 4e. 三位一体策略模块（ai/trinity模式都需要，提供程序化评分给AI参考）
        if self.decision_mode in ('trinity', 'ai'):
            trinity_params = self.config.get('trinity', {})
            proxy = self.config.get('proxy', '')
            if proxy:
                trinity_params['proxy'] = proxy
            # Fix: 注入全局风控参数到trinity策略，确保仓位上限一致
            risk_config = self.config.get('risk', {})
            if 'risk' not in trinity_params:
                trinity_params['risk'] = {}
            trinity_params['risk']['max_single_order_usdt'] = risk_config.get('max_single_order_usdt', 10.0)
            if 'leverage' not in trinity_params.get('risk', {}):
                trinity_params['risk']['leverage'] = risk_config.get('leverage_default', 1.0)
            self.trinity_strategy = TrinityStrategy(trinity_params)
            logger.info("[Engine] 三位一体策略模块已加载 (PA+SMC+威科夫)")
        else:
            self.trinity_strategy = None

        # 4e1. 颜驰策略配置（逐币新实例，避免共享状态）
        self.yanchi_strategy_config = self.config.get('yanchi', {})
        if self.decision_mode == 'yanchi':
            logger.info("[Engine] 颜驰策略已启用（每个币种使用独立实例）")

        # 4f. 代币预筛选器（代码层快速过滤，减少AI调用次数）
        screener_config = self.config.get('screener', {})
        self.token_screener = TokenScreener({
            "min_volume_usdt": screener_config.get("min_volume_usdt", 500_000),
            "min_price": screener_config.get("min_price", 0.001),
            "min_atr_pct": screener_config.get("min_atr_pct", 1.0),
            "max_atr_pct": screener_config.get("max_atr_pct", 40.0),
            "min_klines": screener_config.get("min_klines", 20),
            "max_tokens": screener_config.get("max_tokens", 10),
        })
        self.screening_enabled = screener_config.get("enabled", True)

        # 4g. LLM二次审核器（可选，对Trinity信号做最终定性判断）
        llm_config = self.config.get('trinity_llm', {})
        ai_cfg = self.config.get('ai', {})
        self.trinity_llm = TrinityLLMDecider({
            "enabled": llm_config.get("enabled", False),
            "provider": llm_config.get("provider", ai_cfg.get("provider", "openai")),
            "api_key": llm_config.get("api_key", "") or ai_cfg.get("api_key", ""),
            "api_base": llm_config.get("api_base", ai_cfg.get("base_url", "")),
            "model": llm_config.get("model", ai_cfg.get("model", "gpt-4")),
            "temperature": llm_config.get("temperature", 0.3),
            "timeout": llm_config.get("timeout", 30),
            "fallback_approve": llm_config.get("fallback_approve", False),
        })
        logger.info("[Engine] 代币筛选器已加载 (enabled={}, max={}, minVol={})",
                   self.screening_enabled,
                   screener_config.get("max_tokens", 10),
                   screener_config.get("min_volume_usdt", 500_000))
        # 5. 风控模块
        risk_config = self.config.get('risk', {})
        self.risk_control = RiskControlModule(risk_config)

        # 6. 订单执行模块
        order_config = self.config.get('order', {
            'preferred_exchange': 'auto',
            'default_order_type': 'market',
            'split_orders': False
        })
        # 注入币安API凭证
        binance_config = self.config.get('exchanges', {}).get('binance', {})
        order_config['api_key'] = binance_config.get('api_key', '')
        order_config['api_secret'] = binance_config.get('api_secret', '')
        order_config['testnet'] = binance_config.get('sandbox', False)  # Fix: 默认实盘
        order_config['proxy'] = self.config.get('proxy', '')
        self.order_executor = OrderExecutorModule(order_config)
        
        # 7. 从交易所恢复已有持仓（引擎重启时）
        self._recover_positions_from_exchange()
        
        for name, exchange in self.market_data.exchanges.items():
            self.order_executor.register_exchange(name, exchange)

        # 7. 记录通知模块
        notifier_config = {
            'database': self.config.get('database', {'type': 'sqlite', 'sqlite_path': 'data/wangcai.db'}),
            'channels': ['console'],
            'min_alert_level': 'INFO'
        }
        if self.config.get('notifications', {}).get('telegram', {}).get('enabled'):
            notifier_config['channels'].append('telegram')
            notifier_config['telegram'] = self.config['notifications']['telegram']
        if self.config.get('notifications', {}).get('webhook', {}).get('enabled'):
            notifier_config['channels'].append('webhook')
            notifier_config['webhook'] = self.config['notifications']['webhook']

        self.logger_notifier = LoggerNotifierModule(notifier_config)

        # 8. 每日复盘模块
        review_config = {
            'report_time': '23:30',
            'ai_provider': ai_config.get('provider', 'openai'),
            'ai_model': ai_config.get('model', 'gpt-4'),
            'ai_api_key': ai_config.get('api_key', ''),
            'report_format': 'markdown'
        }
        # 解析 report_time 为 report_hour 和 report_minute
        rt = review_config.get('report_time', '23:30')
        parts = rt.split(':')
        review_config['report_hour'] = int(parts[0])
        review_config['report_minute'] = int(parts[1]) if len(parts) > 1 else 30
        self.daily_review = DailyReviewModule(review_config)

    # ------------------------------------------------------------------ #
    #  主循环
    # ------------------------------------------------------------------ #

    async def run_single_cycle(self):
        """执行单次主循环"""
        self.cycle_count += 1
        cycle_start = datetime.now()
        logger.info("🔄 主循环 #{} 开始 | {}", self.cycle_count, cycle_start.strftime('%H:%M:%S'))

        try:
            # ===== Step 1 + 2 + 3: 并行采集行情、信息聚合、账户数据 =====
            async def _do_market():
                logger.info("[1/8] 行情数据采集(分批)...")
                s = await self.market_data.get_batched_snapshots(
                    batch_size=15, sleep_between=8.0
                )
                summary = {}
                for symbol, snapshot in s.items():
                    summary[symbol] = {
                        'price': snapshot.avg_price,
                        'spread': snapshot.spread,
                        'indicators': self.market_data.get_technical_indicators(symbol)
                    }
                logger.info("[1/8] 行情采集完成, {}个代币", len(s))
                return s, summary

            async def _do_info():
                logger.info("[2/8] 信息聚合...")
                result = await self.info_aggregator.aggregate(
                    self.market_data.exchanges
                )
                logger.info("[2/8] 信息聚合完成")
                return result

            async def _do_account():
                logger.info("[3/8] 账户同步...")
                pf = await self.account_manager.sync_all()
                logger.info("[3/8] 账户同步完成")
                return pf

            results = await asyncio.gather(
                _do_market(),
                _do_info(),
                _do_account()
            )
            snapshots, market_summary = results[0]
            market_info = results[1]
            portfolio = results[2]

            self.logger_notifier.save_account_snapshot({
                'timestamp': datetime.now().isoformat(),
                'accounts': {
                    name: {
                        'total_equity_usdt': acc.total_equity_usdt,
                        'available_usdt': acc.available_usdt,
                        'position_count': acc.position_count,
                        'daily_pnl': acc.daily_pnl,
                        'total_pnl': acc.total_pnl
                    }
                    for name, acc in portfolio.accounts.items()
                }
            })

            # ===== Step 4: 决策（AI / 规则 / 外部AI / WyckoffSMC）=====
            if self.decision_mode == 'ai_external':
                await self._decision_external(snapshots=snapshots,
                                             portfolio=portfolio)
            elif self.decision_mode == 'ai':
                await self._decision_ai(symbols=list(snapshots.keys()),
                                       snapshots=snapshots,
                                       market_info=market_info,
                                       portfolio=portfolio,
                                       market_summary=market_summary)
            elif self.decision_mode == 'wyckoff_smc':
                await self._decision_wyckoff_smc(snapshots=snapshots,
                                            portfolio=portfolio)
            elif self.decision_mode == 'trinity':
                await self._decision_trinity(snapshots=snapshots,
                                            portfolio=portfolio)
            elif self.decision_mode == 'yanchi':
                await self._decision_yanchi(snapshots=snapshots,
                                            portfolio=portfolio)
            else:
                await self._decision_rule(symbols=list(snapshots.keys()),
                                         snapshots=snapshots,
                                         portfolio=portfolio)

            # ===== Step 7: 记录与通知 =====
            logger.info("[7/8] 记录与通知 | 本轮分析:{}个代币 | 持仓:{} | 权益:${:.2f}",
                       len(snapshots), portfolio.total_positions, portfolio.total_equity)

            # 仓位监控（止损/止盈检查）
            await self._monitor_positions(snapshots)

            cycle_end = datetime.now()
            duration = (cycle_end - cycle_start).total_seconds()
            
            # 释放K线缓存（每轮结束后清理，下次重新拉取）
            self.market_data.clear_klines_cache()
            
            logger.info("✅ 主循环 #{} 完成 | 耗时: {:.2f}s | 回到步骤1...", self.cycle_count, duration)

        except Exception as e:
            logger.error("❌ 主循环异常: {}", e, exc_info=True)
            await self.logger_notifier.notify(AlertMessage(
                level=AlertLevel.ERROR,
                title="主循环异常",
                content=str(e),
                tags=['error']
            ))

    # ------------------------------------------------------------------ #
    #  AI 决策分支
    # ------------------------------------------------------------------ #

    def _run_trinity_analysis(self, symbol: str):
        """程序化PA+SMC+威科夫分析，返回结构化信号"""
        result = {
            'symbol': symbol, 'trend': 'neutral',
            'wyckoff_phase': 'unknown', 'smc_structure': 'none',
            'pa_patterns': [], 'liquidity_bsl': 0, 'liquidity_ssl': 0,
            'order_blocks': 0, 'total_score': 0, 'scoring': {},
        }
        try:
            ts = self.trinity_strategy
            if ts is None:
                if not getattr(self, '_trinity_warned', False):
                    logger.warning("[Trinity] 策略未初始化! decision_mode={}", self.decision_mode)
                    self._trinity_warned = True
                return result
            
            cache_key = f"{symbol}_1h"
            df = self.market_data._klines_cache.get(cache_key)
            if df is None or df.empty or len(df) < 50:
                if symbol in ('BTC/USDT', 'ETH/USDT', 'SOL/USDT'):
                    # 列出实际缓存的key帮助调试
                    sample_keys = [k for k in list(self.market_data._klines_cache.keys())[:5] if symbol.split('/')[0] in k]
                    logger.warning("[Trinity] {} 1h K线缺失 | key={} | 匹配样本: {}", 
                                 symbol, cache_key, sample_keys)
                return result

            # 威科夫阶段
            wyckoff_phase = ts.wyckoff_analyzer.detect_phase(df)
            result['wyckoff_phase'] = wyckoff_phase.value

            # SMC结构 + 流动性
            smc = ts.smc_analyzer.detect_bos_choch(df)
            result['smc_structure'] = smc.get('structure', 'none')
            liq = ts.smc_analyzer.detect_liquidity_levels(df)
            result['liquidity_bsl'] = round(liq.get('bsl', 0), 6)
            result['liquidity_ssl'] = round(liq.get('ssl', 0), 6)
            result['order_blocks'] = len(ts.smc_analyzer.detect_order_blocks(df))

            # PA形态
            patterns = ts.pa_analyzer.detect_all_patterns(df)
            result['pa_patterns'] = [str(p.get('pattern', '?')) if isinstance(p, dict) else str(p) for p in patterns[-5:]]

            # 综合评分 — 判定方向（默认 neutral，显式判断 bullish/bearish）
            direction = 'neutral'
            if 'bos_bullish' in str(result['smc_structure']) or wyckoff_phase.value in ('accumulation', 'markup'):
                direction = 'bullish'
            elif 'bos_bearish' in str(result['smc_structure']) or wyckoff_phase.value in ('distribution', 'markdown'):
                direction = 'bearish'

            from modules.strategy_trinity import TrinityAnalysis, SMCStructure, PAPattern
            # Fix #3: 确保smc_structure默认值是SMCStructure.NONE而非BOS_BULLISH
            # 兼容字符串和枚举类型
            raw_smc = smc.get('structure', SMCStructure.NONE)
            if isinstance(raw_smc, str):
                try:
                    raw_smc = SMCStructure(raw_smc.lower())
                except (ValueError, AttributeError):
                    raw_smc = SMCStructure.NONE
            smc_enum = raw_smc if isinstance(raw_smc, SMCStructure) else SMCStructure.NONE
            analysis = TrinityAnalysis(
                symbol=symbol,
                wyckoff_phase=wyckoff_phase,
                smc_structure=smc_enum,
                pa_pattern=PAPattern.NONE,
                trade_zones=ts.smc_analyzer.detect_order_blocks(df),
                liquidity_levels={'bsl': result['liquidity_bsl'], 'ssl': result['liquidity_ssl']},
                current_price=df['close'].iloc[-1],
                volume_profile={'volume_avg': float(df['volume'].mean()), 'volume_ratio': float(df['volume'].iloc[-1] / df['volume'].mean()) if df['volume'].mean() > 0 else 1.0},
                timestamp=datetime.now(),
                direction=direction,
            )
            score, detail = ts.scorer.score(analysis, direction)
            result['scoring'] = detail
            result['total_score'] = round(score, 1)
            result['trend'] = direction
            # 仅主流通币记录日志
            if symbol in ('BTC/USDT', 'ETH/USDT', 'SOL/USDT'):
                logger.info("[Trinity] {} 评分={}/31 阶段={} SMC={} PA={}个", 
                           symbol, result['total_score'], result['wyckoff_phase'],
                           result['smc_structure'], len(result['pa_patterns']))
        except Exception as e:
            logger.warning("[Trinity] {} 分析异常: {}", symbol, str(e)[:120])
            logger.debug(traceback.format_exc())
        return result

    # ------------------------------------------------------------------ #
    #  代币快速预筛选（代码层，减少后续深度分析量）
    # ------------------------------------------------------------------ #

    async def _run_token_screening(self, symbols: List[str], snapshots: Dict) -> List[str]:
        """
        使用TokenScreener快速过滤代币
        
        检查: 24h成交量、价格、波动率、趋势方向
        返回: 通过筛选的代币列表（按评分排序，最多max_tokens个）
        """
        klines_dict = {}
        tickers = {}
        
        for symbol in symbols:
            try:
                # 获取1H K线
                df_1h = await self.market_data.fetch_klines(symbol, timeframe='1h', limit=100)
                if df_1h is not None and not df_1h.empty:
                    if symbol not in klines_dict:
                        klines_dict[symbol] = {}
                    klines_dict[symbol]['1h'] = df_1h
                
                # 获取4H K线用于趋势判断
                df_4h = await self.market_data.fetch_klines(symbol, timeframe='4h', limit=50)
                if df_4h is not None and not df_4h.empty:
                    if symbol not in klines_dict:
                        klines_dict[symbol] = {}
                    klines_dict[symbol]['4h'] = df_4h
            except Exception as e:
                logger.debug("[Screener] {} K线失败: {}", symbol, str(e)[:50])
        
        # 从snapshot获取ticker数据
        has_volume = False
        for symbol in symbols:
            snap = snapshots.get(symbol)
            if snap and hasattr(snap, 'tickers'):
                first_ex = list(snap.tickers.values())[0] if snap.tickers else None
                if first_ex:
                    vol = getattr(first_ex, 'quoteVolume', 0) or 0
                    tickers[symbol] = {
                        'last': getattr(first_ex, 'last', 0) or 0,
                        'quoteVolume': vol,
                    }
                    if vol > 0:
                        has_volume = True
        
        # 无ticker成交量时，从K线数据估算24h成交量
        if not has_volume:
            for symbol, kls in klines_dict.items():
                df_1h = kls.get('1h')
                if df_1h is not None and len(df_1h) >= 24:
                    vol_24h = float(df_1h['volume'].tail(24).sum() * df_1h['close'].iloc[-1])
                    if vol_24h > 0:
                        tickers[symbol] = {
                            'last': float(df_1h['close'].iloc[-1]),
                            'quoteVolume': vol_24h,
                        }
                        has_volume = True
            if has_volume:
                logger.info("[Screener] 从K线估算了{}个代币的成交量", 
                           sum(1 for s in tickers if tickers[s].get('quoteVolume', 0) > 0))
        
        if not klines_dict:
            return symbols  # 无K线数据，不筛选
        
        # 无成交量数据时跳过筛选（如sandbox模式），直接全量分析
        if not has_volume:
            logger.info("[Screener] ticker无成交量数据，跳过筛选")
            return symbols
        
        # 执行筛选
        result = self.token_screener.screen_from_klines(klines_dict, tickers)
        self._last_screener_result = result  # 缓存供前端查询
        
        # 输出报告
        report = self.token_screener.generate_report(result)
        logger.info("\n{}", report)
        
        return result.top_symbols

    async def _decision_ai(self, symbols, snapshots, market_info, portfolio, market_summary):
        """AI 批量决策逻辑 - 预筛选 + 分批分析"""
        logger.info("[Decision][AI] 开始分析 | trinity_strategy={} | klines_cache={}", 
                   self.trinity_strategy is not None,
                   len(self.market_data._klines_cache))
        # 收集所有代币的行情数据
        market_data_list = []
        for symbol in symbols:
            snapshot = snapshots.get(symbol)
            if not snapshot or snapshot.avg_price <= 0:
                continue
            # 过滤掉无效代币（价格为0或无数据的）
            indicators = market_summary.get(symbol, {}).get('indicators', {})
            
            # 程序化PA+SMC+威科夫三位一体分析
            trinity = self._run_trinity_analysis(symbol)
            enriched = dict(indicators)  # 保留基础指标
            
            market_data_list.append({
                'symbol': symbol,
                'price': snapshot.avg_price,
                'indicators': enriched,
                'trinity': trinity,  # 结构化分析结果代替原始OHLC
            })
        
        if not market_data_list:
            logger.warning("[Decision][AI] 无可用行情数据")
            return
        
        # 预筛选：价格>0的代币都送AI（不做过度筛选，让AI自己判断）
        # 优先持仓代币排在前面
        has_position = set()
        for md in market_data_list:
            positions = portfolio.get_position_by_symbol(md['symbol'])
            if positions:
                has_position.add(md['symbol'])
        
        screened = []
        # 持仓代币优先
        for md in market_data_list:
            if md['symbol'] in has_position:
                screened.append(md)
        # 其余有效代币
        for md in market_data_list:
            if md['symbol'] not in has_position:
                screened.append(md)
        
        # 最多每批10个代币（避免JSON过大被截断）
        BATCH_SIZE = 10
        total_batches = (len(screened) + BATCH_SIZE - 1) // BATCH_SIZE
        logger.info("[Decision][AI] 预筛选: {} → {} 个代币 | 分{}批 | 持仓:{}",
                   len(market_data_list), len(screened), total_batches, len(has_position))
        
        # 构造市场宏观信息
        info_input = {
            'summary': market_info.to_text_summary(max_items=5),
            'overall_sentiment': market_info.overall_sentiment,
            'fear_greed_index': market_info.fear_greed_index
        }
        
        # 构造完整账户持仓信息
        all_positions = []
        seen_symbols = set()
        for symbol in symbols:
            positions = portfolio.get_position_by_symbol(symbol)
            for p in positions:
                if p.symbol not in seen_symbols:
                    seen_symbols.add(p.symbol)
                    all_positions.append({
                        'symbol': p.symbol, 'side': p.side,
                        'amount': p.amount, 'entry_price': p.entry_price,
                        'pnl': p.unrealized_pnl
                    })
        
        account_input = {
            'summary': portfolio.to_text_summary(),
            'positions': all_positions,
            'total_equity': portfolio.total_equity,
            'available_usdt': portfolio.total_available,
            'position_count': portfolio.total_positions
        }
        
        # 分批调用AI
        all_decisions = []
        for batch_idx in range(total_batches):
            start = batch_idx * BATCH_SIZE
            batch_markets = screened[start:start + BATCH_SIZE]
            
            logger.info("[Decision][AI] 第{}/{}批 | 代币数: {}",
                       batch_idx + 1, total_batches, len(batch_markets))
            
            # Fix: decide_batch 不存在，逐个调用 decide()
            for market in batch_markets:
                try:
                    d = await self.ai_decision.decide(
                        market_data=market,
                        info=info_input,
                        account=account_input
                    )
                    if d:
                        all_decisions.append(d)
                except Exception:
                    pass
        
        if not all_decisions:
            logger.warning("[Decision][AI] 分批决策全部返回空")
            return
        
        # 逐条执行有效决策
        for decision in all_decisions:
            try:
                self.logger_notifier.save_decision(decision.to_dict())
                await self.logger_notifier.notify_decision(decision.to_dict())
                
                if decision.action == ActionType.HOLD or not decision.is_valid:
                    logger.info("[Decision][AI] {} -> HOLD (置信度: {:.2f})",
                                decision.symbol, decision.confidence)
                    continue
                
                # 计算实际仓位数量
                # Fix: TradeDecision 无 metadata 字段，amount 已在策略层正确设置
                if decision.action in (ActionType.BUY, ActionType.SELL) and decision.amount == 0:
                    logger.warning("[Decision][AI] {} amount=0，跳过", decision.symbol)
                    continue
                
                snapshot = snapshots.get(decision.symbol)
                if not snapshot:
                    logger.warning("[Decision][AI] {} 无行情快照，跳过执行", decision.symbol)
                    continue
                
                logger.info("[Decision][AI] {} -> {} {} (置信度: {:.2f})",
                             decision.symbol, decision.action.value, decision.amount, decision.confidence)
                
                await self._execute_decision(decision, snapshot, portfolio)
                
            except Exception as e:
                logger.error("[Decision][AI] {} 决策执行异常: {}", decision.symbol, e)
                continue

    # ------------------------------------------------------------------ #
    #  规则策略决策分支（QuantTrend）
    # ------------------------------------------------------------------ #

    def _write_strategy_status_json(self, payload: Dict[str, Any]):
        """原子写入策略状态文件，供 Web 前端读取。

        使用 ensure_ascii=True，避免 Windows/历史文件编码导致前端按 UTF-8 读取失败。
        """
        path = Path(__file__).parent.parent / "data" / "strategy_status.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, ensure_ascii=True, indent=2)
            f.write("\n")
        tmp_path.replace(path)

    async def _decision_rule(self, symbols, snapshots, portfolio):
        """
        规则策略决策：
        1. 获取4H K线数据
        2. 计算策略指标
        3. 生成交易信号
        4. 执行（与AI分支共用 _execute_decision）
        """
        for symbol in symbols:
            try:
                snapshot = snapshots.get(symbol)
                if not snapshot:
                    continue

                # 获取4H K线（策略使用4H周期）
                df_4h = await self.market_data.fetch_klines(symbol, timeframe='4h', limit=300)
                if df_4h is None or df_4h.empty:
                    logger.warning("[Decision][Rule] {} 4H K线获取失败，跳过", symbol)
                    continue

                # 计算策略指标 + 生成信号
                signal = self.quant_strategy.generate_signal(df_4h, symbol=symbol)

                # 存储最新策略状态（供前端展示）
                self.latest_signal = {
                    "symbol": symbol,
                    "action": signal.signal.value,
                    "score": round(signal.score, 2),
                    "reason": signal.reason[:200],
                    "leverage": signal.leverage,
                    "stop_price": signal.stop_price,
                    "trail_price": signal.trail_price,
                    "confidence": round(signal.confidence, 2),
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "cycle": self.cycle_count,
                }
                # 写入文件供前端读取
                self._write_strategy_status_json(self.latest_signal)

                if signal.signal == SignalType.HOLD:
                    logger.info("[Decision][Rule] {} -> HOLD (评分: {:.2f})",
                                symbol, signal.score)
                    continue

                # 将 StrategySignal 转换为 TradeDecision
                decision = self._signal_to_decision(signal, portfolio)
                if decision is None:
                    continue

                self.logger_notifier.save_decision(decision.to_dict())
                await self.logger_notifier.notify_decision(decision.to_dict())

                logger.info("[Decision][Rule] {} -> {} {} (评分: {:.2f})",
                             symbol, decision.action.value, decision.amount, signal.score)

                await self._execute_decision(decision, snapshot, portfolio)

            except Exception as e:
                logger.error("[Cycle][Rule] {} 策略决策异常: {}", symbol, e)
                continue

    def _signal_to_decision(self, signal: StrategySignal,
                             portfolio: Any) -> Optional[TradeDecision]:
        """
        将规则策略信号转换为 TradeDecision
        复用 AI 决策下游的风控/下单逻辑
        """

        if signal.signal == SignalType.HOLD:
            return None

        # 计算仓位 size（与Pine Script逻辑一致）
        equity = max(portfolio.total_equity, 1.0)
        leverage = signal.leverage or 1.0  # Fix: 默认1.0（与config对齐）
        price = signal.price or 0.0
        if price <= 0:
            return None

        position_value = equity * leverage
        qty = position_value / price  # 合约张数

        action = ActionType.BUY if signal.signal == SignalType.BUY else ActionType.SELL

        return TradeDecision(
            action=action,
            symbol=signal.symbol,
            amount=qty,
            price=signal.price,
            reason=f"[QuantTrend] {signal.reason}",
            confidence=min(signal.score / 10.0, 1.0),
            stop_loss=signal.stop_price,
            take_profit=None,
            timeframe='4h',
            strategy='quant_trend'
        )

    # ------------------------------------------------------------------ #
    #  外部AI决策分支（QClaw AI Agent）
    # ------------------------------------------------------------------ #

    async def _decision_external(self, snapshots, portfolio):
        """外部AI决策：读取 QClaw AI Agent 输出的决策文件"""
        if not self.ai_bridge:
            logger.error("[Engine] 外部AI桥接未初始化")
            return

        if not self.ai_bridge.has_new_decision():
            logger.debug("[Decision][External] 无新决策文件，跳过")
            return

        logger.info("[4/8] 外部AI决策读取...")

        decisions = self.ai_bridge.get_pending_decisions(
            total_equity=portfolio.total_equity
        )

        if not decisions:
            logger.info("[Decision][External] 无有效待执行决策")
            return

        logger.info("[Decision][External] 获取到 {} 个待执行决策", len(decisions))

        executed_count = 0
        for decision in decisions:
            if portfolio.total_positions >= self.risk_control.position_sizing.max_positions:
                logger.warning("[Decision][External] 已达最大持仓数({})，停止开仓",
                              self.risk_control.position_sizing.max_positions)
                break

            try:
                self.logger_notifier.save_decision(decision.to_dict())
                await self.logger_notifier.notify_decision(decision.to_dict())

                if decision.action == ActionType.HOLD or not decision.is_valid:
                    continue

                logger.info("[Decision][External] {} -> {} (confidence: {:.2f})",
                            decision.symbol, decision.action.value,
                            decision.confidence)

                snapshot = snapshots.get(decision.symbol)
                if not snapshot:
                    snapshot = SimpleSnapshot(decision.price or 0)

                await self._execute_decision(decision, snapshot, portfolio)
                self.ai_bridge.mark_executed(decision)
                executed_count += 1

            except Exception as e:
                logger.error("[Cycle][External] decision exec error: {}", e)
                continue

        if executed_count > 0:
            logger.info("[Decision][External] executed {} decisions", executed_count)

    async def _decision_wyckoff_smc(self, snapshots, portfolio):
        """
        Wyckoff + SMC 策略决策分支
        1. 筛选Alpha代币
        2. 4H定方向 + 1H找结构 + 15M入场
        3. 生成交易信号
        4. 执行（与AI/Rule共用 _execute_decision）
        """
        logger.info("[4/8] Wyckoff + SMC 策略分析...")
        
        # 1. 筛选Alpha代币（如果还没有）
        if not self.wyckoff_smc_strategy.screened_coins:
            coins = self.wyckoff_smc_strategy.screen_coins()
            if not coins:
                logger.warning("[Decision][WyckoffSMC] 无符合条件的Alpha代币")
                return
            
            # 更新监控的交易对
            new_symbols = [coin['symbol'].upper() + '/USDT' for coin in coins]
            logger.info("[Decision][WyckoffSMC] 筛选出 {} 个Alpha代币: {}",
                       len(new_symbols), new_symbols[:5])
        
        # 2. 为每个币种生成信号
        for coin in self.wyckoff_smc_strategy.screened_coins:
            symbol = coin['symbol'].upper() + '/USDT'
            
            try:
                # 分析币种（4H + 1H）
                analysis = self.wyckoff_smc_strategy.analyze_symbol(symbol)
                if analysis is None:
                    continue
                
                # 生成信号（检查15M入场）
                # 注意：df参数未使用（方法内部用Binance API获取数据）
                signal = self.wyckoff_smc_strategy.generate_signal(
                    None, symbol=symbol
                )
                
                # Fix #5: WyckoffSignalType 未定义，使用字符串比较避免 NameError
                signal_value = str(signal.signal) if hasattr(signal.signal, 'value') else str(signal.signal)
                if signal_value.upper() == 'HOLD':
                    logger.info("[Decision][WyckoffSMC] {} -> HOLD (评分: {:.2f})",
                                symbol, signal.score)
                    continue
                
                # 将 StrategySignal 转换为 TradeDecision
                decision = self._wyckoff_signal_to_decision(signal, portfolio)
                if decision is None:
                    continue
                
                self.logger_notifier.save_decision(decision.to_dict())
                await self.logger_notifier.notify_decision(decision.to_dict())
                
                logger.info("[Decision][WyckoffSMC] {} -> {} (置信度: {:.2f})",
                             symbol, decision.action.value, decision.confidence)
                
                # 获取snapshot
                snapshot = snapshots.get(symbol)
                if not snapshot:
                    snapshot = SimpleSnapshot(decision.price or 0)
                
                await self._execute_decision(decision, snapshot, portfolio)
                
            except Exception as e:
                logger.error("[Cycle][WyckoffSMC] {} 策略决策异常: {}", symbol, e)
                continue

    def _wyckoff_signal_to_decision(self, signal: Any,
                                     portfolio: Any) -> Optional[TradeDecision]:
        """
        将WyckoffSMC策略信号转换为 TradeDecision
        复用 AI 决策下游的风控/下单逻辑
        """
        signal_value = str(signal.signal) if hasattr(signal.signal, 'value') else str(signal.signal)
        if signal_value.upper() == 'HOLD':
            return None
        
        # 计算仓位 size（基于风险）
        equity = max(portfolio.total_equity, 1.0)
        leverage = signal.leverage or 1.0  # Fix: 默认1.0（与config对齐）
        price = signal.price or 0.0
        if price <= 0:
            return None
        
        # 仓位计算（基于风险）
        wyckoff_cfg = self.config.get('wyckoff_smc', {})
        risk_percent = wyckoff_cfg.get('max_risk_per_trade', 0.02)  # Fix: 读配置
        risk_amount = equity * risk_percent
        price_risk = abs(price - signal.stop_price) if signal.stop_price else price * 0.02
        
        if price_risk == 0:
            qty = equity * leverage / price
        else:
            qty = risk_amount / price_risk
        
        action = ActionType.BUY if signal_value.upper() == 'BUY' else ActionType.SELL
        
        return TradeDecision(
            action=action,
            symbol=signal.symbol,
            amount=qty,
            price=signal.price,
            reason=f"[WyckoffSMC] {signal.reason}",
            confidence=signal.confidence,
            stop_loss=signal.stop_price,
            take_profit=None,  # 由策略内部计算
            timeframe='4h',
            strategy='wyckoff_smc'
        )

    # ------------------------------------------------------------------ #
    #  风控 + 下单（AI / Rule / External 共用）
    # ------------------------------------------------------------------ #

    async def _execute_decision(self, decision, snapshot, portfolio):
        """风控审核 + 订单执行（AI和规则策略共用）"""
        # ===== Step 5: 风控审核 =====
        logger.info("[5/8] 风控审核...")
        daily_pnl = self.account_manager.get_daily_pnl()

        decision_dict = decision.to_dict()
        # 将 price 映射为 entry_price，确保盈亏比检查数据流正确
        if 'price' in decision_dict and 'entry_price' not in decision_dict:
            decision_dict['entry_price'] = decision_dict['price']
        risk_report = self.risk_control.check(
            decision=decision_dict,
            account={
                'total_equity': portfolio.total_equity,
                'available_usdt': portfolio.total_available,
                'position_count': portfolio.total_positions,
                # Fix: 传递已持仓代币符号，用于重复下单检查
                'existing_symbols': [p.symbol for p in self.order_executor.get_positions()],
            },
            daily_stats={'total_pnl': daily_pnl}
        )

        self.logger_notifier.save_risk_check({
            'timestamp': datetime.now().isoformat(),
            'decision_id': f"{decision.symbol}_{datetime.now().timestamp():.0f}",
            'overall_level': risk_report.overall_level.value,
            'is_passed': risk_report.is_passed,
            'checks': [
                {'rule_name': c.rule_name, 'passed': c.passed,
                 'level': c.level.value, 'message': c.message}
                for c in risk_report.checks
            ]
        })

        await self.logger_notifier.notify_risk({
            'is_passed': risk_report.is_passed,
            'overall_level': risk_report.overall_level.value,
            'checks': [
                {'passed': c.passed, 'level': c.level.value}
                for c in risk_report.checks
            ]
        })

        if not risk_report.is_passed:
            logger.warning("[Risk] {} 风控拒绝: {}",
                          decision.symbol, risk_report.overall_level.value)
            return

        logger.info("[Risk] {} 风控通过", decision.symbol)

        # ===== Step 6: 交易所下单 =====
        logger.info("[6/8] 订单执行...")
        if self.system_config.get('mode') == 'live':
            # 处理 CLOSE 平仓信号
            if decision.action == ActionType.CLOSE:
                logger.info("[Order] 执行平仓信号: {}", decision.symbol)
                positions = self.order_executor.get_positions()
                # Fix #1 (Round4): Symbol格式对齐 — pos.symbol='BTCUSDT', decision.symbol='BTC/USDT'
                decision_symbol_raw = decision.symbol.replace('/', '')  # BTC/USDT → BTCUSDT
                for pos in positions:
                    try:  # Fix P0-3: 平仓循环加异常保护
                        # 兼容两种格式匹配
                        if pos.symbol == decision.symbol or pos.symbol == decision_symbol_raw:
                            if pos.direction == 'LONG':
                                pnl = (snapshot.avg_price - pos.entry_price) * pos.position_size
                            else:
                                pnl = (pos.entry_price - snapshot.avg_price) * pos.position_size
                            logger.info("[Order] 平仓 {} | 方向={} | order_id={}",
                                       decision.symbol, pos.direction, pos.order_id)
                            if self.order_executor.close_position(pos.order_id, f"AI平仓信号"):
                                await self.risk_control.record_trade_result(float(pnl))
                                logger.info("[Order] ✅ 平仓完成: {} | PnL=${:.2f}", decision.symbol, pnl)
                            else:
                                logger.error("[Order] ⚠️ 平仓失败: {} | order_id={}", decision.symbol, pos.order_id)
                    except Exception as pe:
                        logger.error("[Order] ⚠️ 平仓异常: {} | {}", decision.symbol, pe)
                return

            # Fix P0-2: 复用风控阶段的 decision_dict，不再重复 to_dict()
            # 补充 order_executor 期望的字段映射
            if 'entry_price' not in decision_dict and 'price' in decision_dict:
                decision_dict['entry_price'] = decision_dict['price']
            if 'position_size' not in decision_dict:
                decision_dict['position_size'] = decision_dict.get('amount', 0)
            # 映射 BUY/SELL → LONG/SHORT（order_executor 接口需要）
            if decision_dict.get('action') == 'BUY':
                decision_dict['action'] = 'LONG'
            elif decision_dict.get('action') == 'SELL':
                decision_dict['action'] = 'SHORT'
            order_results = self.order_executor.execute_decision(
                decision_dict
            )
            for result in order_results:
                status = result.get('status', '')
                if status == 'executed':
                    trade_record = {
                        'timestamp': datetime.now().isoformat(),
                        'order_id': result.get('order_id', ''),
                        'symbol': result.get('symbol', decision.symbol),
                        'action': decision.action.value,
                        'side': 'buy' if decision.action == ActionType.BUY else 'sell',
                        'amount': result.get('position_size', 0),
                        'price': result.get('entry_price', decision.price or 0),
                        'filled_amount': result.get('position_size', 0),
                        'average_price': result.get('entry_price', 0),
                        'fee': result.get('fee', 0),
                        'exchange': result.get('exchange', 'binance'),
                        'status': 'filled',
                        'raw': result
                    }
                    self.logger_notifier.save_trade(trade_record)
                    await self.logger_notifier.notify_trade(trade_record)
                    # Fix P0-2: 开仓成功不调用 record_trade_result — 只有平仓后才更新 daily_pnl/consecutive_losses
                    # 以下行已删除: pnl_value = ... ; await self.risk_control.record_trade_result(...)
                else:
                    err_msg = result.get('message', 'unknown error')
                    # Fix #4 (Round4): 检测 -4411 TradFi-Perps 错误
                    if '-4411' in str(err_msg) or 'TradFi' in str(err_msg):
                        logger.error("[Order] ⚠️ 跳过 {} — 该代币需要签署币安TradFi-Perps协议", decision.symbol)
                        err_msg = f"TradFi-Perps协议未签署(-4411): {decision.symbol}需要手动签署协议"
                    logger.error("[Order] 订单失败: {}", err_msg)
                    await self.logger_notifier.notify(AlertMessage(
                        level=AlertLevel.ERROR,
                        title=f"订单失败: {decision.symbol}",
                        content=err_msg,
                        tags=['order_error', decision.symbol]
                    ))
        else:
            logger.info("[Order] 📝 模拟下单: {} {} {} {} (Paper Trading)",
                       decision.action.value, decision.symbol,
                       decision.amount, decision.price or '市价')
            self.logger_notifier.save_trade({
                'timestamp': datetime.now().isoformat(),
                'order_id': f"PAPER_{datetime.now().timestamp():.0f}",
                'symbol': decision.symbol,
                'action': decision.action.value,
                'side': 'buy' if decision.action == ActionType.BUY else 'sell',
                'amount': decision.amount,
                'price': decision.price or snapshot.avg_price,
                'exchange': 'paper',
                'status': 'filled',
                'raw': {'mode': 'paper', 'decision': decision.to_dict()}
            })

    # ------------------------------------------------------------------ #
    #  运行控制
    # ------------------------------------------------------------------ #

    async def run(self):
        """主运行循环"""
        self.running = True
        interval = self.system_config.get('main_loop', {}).get('interval_seconds', 60)

        logger.info("🚀 旺财自动交易系统启动 | 主循环间隔: {}s", interval)
        logger.info("   当前模式: {}",
                    "🔴 实盘" if self.system_config.get('mode') == 'live' else "🟡 模拟")

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                asyncio.get_event_loop().add_signal_handler(
                    sig, lambda: asyncio.create_task(self.shutdown())
                )
            except NotImplementedError:
                pass

        # 启动每日复盘后台任务（每天一次，不参与主循环）
        asyncio.create_task(self._daily_review_loop())

        while self.running:
            try:
                await asyncio.wait_for(
                    self.run_single_cycle(),
                    timeout=interval * 2
                )
            except asyncio.TimeoutError:
                logger.warning("⏱️ 主循环超时")
            except Exception as e:
                logger.error("💥 主循环致命异常: {}", e, exc_info=True)

            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=interval
                )
                break
            except asyncio.TimeoutError:
                continue

        logger.info("🛑 旺财自动交易系统已停止")

    async def _daily_review_loop(self):
        """每日复盘 - 后台任务，每天午夜执行一次"""
        logger.info("[每日复盘] 后台任务已启动，每天00:00自动执行")

        while self.running:
            try:
                now = datetime.now()
                # 计算到下一个午夜的秒数
                midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
                seconds_until_midnight = (midnight - now).total_seconds()
                logger.info("[每日复盘] 距下次执行: {:.0f}分钟", seconds_until_midnight / 60)
                
                # 等到午夜
                await asyncio.sleep(seconds_until_midnight)
                
                if not self.running:
                    break
                    
                logger.info("[8/8] 📊 每日复盘开始...")
                await self.daily_review.generate_report()
                logger.info("[8/8] ✅ 每日复盘完成")
                    
            except Exception as e:
                logger.error("[8/8] 每日复盘异常: {}", e)
                await asyncio.sleep(3600)  # 出错后等1小时重试

    async def shutdown(self):
        """优雅关闭"""
        logger.info("🛑 收到关闭信号，正在优雅停止...")
        self.running = False
        self._shutdown_event.set()
        await self.info_aggregator.close()
        logger.info("👋 旺财已安全退出")

    def get_status(self) -> Dict[str, Any]:
        return {
            'running': self.running,
            'mode': self.system_config.get('mode', 'paper'),
            'decision_mode': self.decision_mode,
            'timestamp': datetime.now().isoformat(),
            'modules': {
                'market_data': self.market_data.health_check(),
                'account_manager': {
                    'exchanges': list(self.account_manager.exchanges.keys())
                },
                'risk_control': self.risk_control.get_status(),
                'order_executor': {
                    'positions': len(self.order_executor.get_positions())
                }
            }
        }

    # ------------------------------------------------------------------ #
    #  三位一体策略决策分支 (PA+SMC+威科夫, AI增强版)
    # ------------------------------------------------------------------ #

    def _save_strategy_status(self, candidates, analysis_details, total_scanned, error_count):
        """保存策略状态到JSON文件（供前端展示）"""
        
        # 筛选结果（来自 Step 0 的 ScreenerResult）
        screening = {"total": total_scanned, "passed": 0, "top_tokens": []}
        if hasattr(self, '_last_screener_result') and self._last_screener_result:
            s = self._last_screener_result
            screening["passed"] = s.passed_count
            screening["rejected"] = total_scanned - s.passed_count
            for tok in s.passed[:10]:
                screening["top_tokens"].append({
                    "symbol": tok.symbol, "score": round(tok.total_score, 1),
                    "price": tok.price, "volume_24h": tok.volume_24h,
                    "atr_pct": round(tok.atr_pct, 1), "trend": tok.trend, "rsi": round(tok.rsi, 1) if tok.rsi is not None else None
                })
        
        # 分析详情
        analysis = {"analyzed": len(analysis_details), "errors": error_count,
                     "details": analysis_details[:20]}  # 最多20条
        
        status = {
            "status": "signals" if candidates else "no_candidates",
            "cycle": self.cycle_count,
            "timestamp": datetime.now().isoformat(),
            "screening": screening,
            "analysis": analysis,
            "signals": [{"symbol": c['symbol'], "action": c['action'], 
                         "score": c['score'], "confidence": c['confidence'],
                         "price": c['price'], "reason": c['reason']} for c in candidates],
        }
        
        self._write_strategy_status_json(status)

    def _build_trinity_decision(self, c: dict) -> TradeDecision:
        """Fix P1-2: 从 candidate dict 构建 TradeDecision（消除重复代码）

        Args:
            c: candidate 字典 (from analyze_one)

        Returns:
            TradeDecision 对象
        """
        return TradeDecision(
            symbol=c['symbol'],
            action=ActionType.BUY if c['action'] in ('LONG', 'BUY') else ActionType.SELL,
            price=c['price'],
            confidence=c['confidence'],
            amount=c.get('position_size', 0.0),
            leverage=c.get('leverage', 1.0),
            stop_loss=c.get('stop_loss'),
            take_profit=c.get('take_profit'),
            reason=c.get('reason', ''),
        )

    def _save_trinity_status(self, candidates, analysis_details, total_scanned, error_count):
        """旧接口兼容，复用统一的状态写入逻辑。"""
        self._save_strategy_status(candidates, analysis_details, total_scanned, error_count)

    def _trinity_signal_to_decision(self, c: dict) -> TradeDecision:
        """旧接口兼容，直接复用 trinity 决策构建。"""
        return self._build_trinity_decision(c)

    async def _decision_trinity(self, snapshots, portfolio):
        """
        三位一体策略 (AI增强版) — 三步管线:
        
        Step 1: 代码预筛选 — 多时间框架共振分析, 过滤候选代币
        Step 2: AI审核     — 大模型审核候选, 验证信号可靠性, 最终判断
        Step 3: 执行订单   — 通过审核的决策经风控后下单
        
        回退机制: AI不可用时自动跳过审核, 直接执行代码筛选结果
        """
        logger.info("[4/8] 三位一体策略分析 (AI增强)...")
        
        trinity_config = self.config.get('trinity', {})
        # 从 preferred_markets 获取监控列表（而非写死的 trinity.symbols）
        symbols = self.config.get('exchanges', {}).get('binance', {}).get('preferred_markets', 
                  trinity_config.get('symbols', ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']))
        
        # Fix #11: 过滤掉不符合 Binance 合约命名规范的符号（基础币种名需要 >=2 字符）
        valid_symbols = []
        for sym in symbols:
            if '/' in sym:
                base = sym.split('/')[0]
            else:
                base = sym.replace('USDT', '') if sym.endswith('USDT') else sym
            # Binance 合约要求基础币种名 >= 2 字符
            if len(base) >= 2:
                valid_symbols.append(sym)
            else:
                logger.warning(f"[Trinity][Fix#11] 跳过不合规符号（基础币种名过短）: {sym}")
        symbols = valid_symbols
        
        original_count = len(symbols)
        
        # ===== Step 0: 快速预筛选（成交量/趋势/波动率，减少后续深度分析量）=====
        if self.screening_enabled and len(symbols) > 5:
            logger.info("[Trinity][Step0] 快速预筛选 {} 个代币...", len(symbols))
            try:
                active_symbols = await self._run_token_screening(symbols, snapshots)
                skipped = original_count - len(active_symbols)
                if skipped > 0:
                    logger.info("[Trinity][Step0] 预筛选跳过 {} 个代币，保留 {} 个进入深度分析",
                               skipped, len(active_symbols))
                symbols = active_symbols
            except Exception as e:
                logger.warning("[Trinity][Step0] 预筛选异常，跳过: {}", e)
        
        # ===== Step 1: 代码预筛选 =====
        logger.info("[Trinity][Step1] 代码预筛选 {} 个代币...", len(symbols))
        candidates = []
        analysis_details = []  # 所有代币的分析详情（含失败/拒绝原因）
        error_count = 0
        
        # 并行分析（限5并发，避免币安API限流）
        semaphore = asyncio.Semaphore(5)
        
        async def analyze_one(symbol: str) -> Optional[Dict[str, Any]]:
            """分析单个代币，返回信号或None"""
            nonlocal error_count
            async with semaphore:
                detail = {"symbol": symbol, "status": "pending"}
                try:
                    snapshot = snapshots.get(symbol)
                    if not snapshot or snapshot.avg_price <= 0:
                        detail["status"] = "no_data"
                        detail["reason"] = "无行情数据"
                        analysis_details.append(detail)
                        return None
                    
                    # 从MarketDataModule获取K线（走代理，避免直连被墙）
                    df_4h = await self.market_data.fetch_klines(symbol, timeframe='4h', limit=200)
                    if df_4h is None or (hasattr(df_4h, 'empty') and df_4h.empty):
                        detail["status"] = "no_klines"
                        detail["reason"] = "4H K线获取失败"
                        analysis_details.append(detail)
                        return None
                    
                    # Fix: 预拉取1小时K线，确保缓存有数据供策略内部 analyze_1h 备用
                    # 也确保 _run_trinity_analysis 能读取到1h数据
                    df_1h = await self.market_data.fetch_klines(symbol, timeframe='1h', limit=200)
                    
                    # Fix #4 (Round4): 将预取的1H K线传递给策略，避免策略内部重复拉取
                    # 在线程池中运行同步的 generate_signal
                    signal = await asyncio.to_thread(
                        self.trinity_strategy.generate_signal,
                        df=df_4h, symbol=symbol, total_equity=portfolio.total_equity,
                        df_1h=df_1h
                    )
                    
                    detail["status"] = "analyzed"
                    detail["reason"] = signal.reason
                    detail["price"] = signal.price
                    detail["score"] = round(signal.score, 2)
                    detail["confidence"] = round(signal.confidence, 2)
                    analysis_details.append(detail)
                    
                    if signal.signal == TrinitySignalType.HOLD:
                        return None
                    
                    # 筛选条件对齐策略: 评分≥7(策略内置) + 置信度≥0.5 双重保险
                    if signal.confidence < 0.5:
                        detail["status"] = "low_confidence"
                        return None
                    
                    detail["status"] = "signal"
                    detail["action"] = signal.signal.value
                    
                    logger.info("[Trinity][Screen] {} → {} | 评分:{:.1f} | 置信度:{:.2f}", 
                               symbol, signal.signal.value, signal.score, signal.confidence)
                    
                    return {
                        'symbol': signal.symbol,
                        'action': signal.signal.value,
                        'price': signal.price,
                        'score': round(signal.score, 2),
                        'confidence': signal.confidence,
                        'reason': signal.reason,
                        'stop_loss': signal.stop_price,
                        'take_profit': signal.take_profit_levels,
                        'leverage': signal.leverage,
                        'position_size': signal.position_size,
                        'risk_percent': signal.risk_percent,
                        'resonance_breakdown': signal.resonance_breakdown,
                    }
                except Exception as e:
                    error_count += 1
                    detail["status"] = "error"
                    detail["reason"] = str(e)
                    analysis_details.append(detail)
                    logger.error("[Trinity][Screen] {} 分析失败: {} | {}", symbol, e, traceback.format_exc().split('\n')[-2].strip())
                    return None
        
        # 并行执行所有分析
        results = await asyncio.gather(*[analyze_one(s) for s in symbols])
        candidates = [r for r in results if r is not None]
        
        # 按评分排序
        candidates.sort(key=lambda x: x['score'], reverse=True)
        
        # ===== 构建并保存状态文件（供前端展示）=====
        self._save_strategy_status(candidates, analysis_details, original_count, error_count)
        
        if not candidates:
            logger.info("[Trinity][Step1] 无符合条件的候选代币")
            return
        
        logger.info("[Trinity][Step1] 预筛选完成: {} 个候选 (Top3: {})", 
                   len(candidates), [c['symbol'] for c in candidates[:3]])
        
        # 更新最新信号
        self.latest_signal = {
            "candidates": len(candidates),
            "top_candidates": [{"symbol": c['symbol'], "score": c['score'], 
                               "action": c['action']} for c in candidates[:5]],
            "timestamp": datetime.now().isoformat(),
            "cycle": self.cycle_count,
        }
        
        # ===== Step 2: 决策判断（AI审核 / 代码直接执行）=====
        skip_ai = trinity_config.get('advanced', {}).get('skip_ai_review', False)

        if skip_ai:
            logger.info("[Trinity][Step2] 纯代码执行模式（AI审核已关闭）")
            ai_decisions = [self._build_trinity_decision(c) for c in candidates]
        else:
            logger.info("[Trinity][Step2] AI审核 {} 个候选...", len(candidates))
            
            # 构建账户上下文
            account_info = {
                'total_equity': portfolio.total_equity,
                'available_usdt': portfolio.total_available,
                'position_count': portfolio.total_positions,
            }
            
            # 当前持仓
            current_positions = []
            for sym in symbols:
                positions = portfolio.get_position_by_symbol(sym)
                for p in positions:
                    current_positions.append({
                        'symbol': p.symbol, 'side': p.side,
                        'amount': p.amount, 'entry_price': p.entry_price,
                        'pnl': p.unrealized_pnl
                    })
            
            # 市场宏观信息
            market_info = {
                'overall_sentiment': self.info_aggregator.last_sentiment if hasattr(self.info_aggregator, 'last_sentiment') else 0,
                'fear_greed_index': self.info_aggregator.last_fear_greed if hasattr(self.info_aggregator, 'last_fear_greed') else 'N/A',
                'btc_dominance': getattr(self.info_aggregator, 'last_btc_dominance', 'N/A'),
            }
            
            try:
                # Fix: review_trinity_candidates 可能不存在(模块缺失时)
                if hasattr(self.ai_decision, 'review_trinity_candidates'):
                    ai_decisions = await self.ai_decision.review_trinity_candidates(
                        candidates=candidates,
                        account_info=account_info,
                        current_positions=current_positions,
                        market_info=market_info
                    )
                else:
                    raise AttributeError("review_trinity_candidates not available")
            except Exception as e:
                logger.warning("[Trinity][Step2] AI审核不可用({})，跳过审核直接用代码信号", e)
                ai_decisions = [self._build_trinity_decision(c) for c in candidates]
        
        logger.info("[Trinity][Step2] 决策完成 | 候选:{} | 通过:{}", 
                   len(candidates), len(ai_decisions))
        
        # 更新信号并保存状态
        approved_count = len([d for d in ai_decisions if d.action != ActionType.HOLD])
        self.latest_signal['ai_approved'] = approved_count
        self.latest_signal['ai_rejected'] = len(candidates) - approved_count
        self.latest_signal['ai_skipped'] = skip_ai
        self._save_strategy_status(candidates, analysis_details, original_count, error_count)
        
        if not ai_decisions:
            logger.info("[Trinity][Step2] 无有效决策")
            return
        
        # ===== Step 3: 执行订单 =====
        logger.info("[Trinity][Step3] 执行 {} 个AI审核通过的决策...", len(ai_decisions))
        
        for decision in ai_decisions:
            try:
                # Fix: 同一代币已持仓则跳过，防止重复开仓
                held_symbols = [p.symbol.replace('/', '') for p in self.order_executor.get_positions()]
                decision_symbol_clean = decision.symbol.replace('/', '')
                if decision_symbol_clean in held_symbols and decision.action not in (ActionType.CLOSE, ActionType.HOLD):
                    logger.warning("[Trinity][Step3] ⛔ {} 已有持仓，跳过重复开仓", decision.symbol)
                    continue

                # 按评分排序, 检查是否超过最大持仓数
                if portfolio.total_positions >= self.risk_control.position_sizing.max_positions:
                    logger.warning("[Trinity][Step3] 已达最大持仓数({}), 停止开仓",
                                 self.risk_control.position_sizing.max_positions)
                    break
                
                self.logger_notifier.save_decision(decision.to_dict())
                await self.logger_notifier.notify_decision(decision.to_dict())
                
                if decision.action == ActionType.HOLD or not decision.is_valid:
                    continue
                
                # 转换 amount (percentage → quantity)
                # Fix #2 (Round4): 修复百分比转换逻辑 — skip_ai路径传入的是position_size(币数)，
                # 不是百分比。只有AI决策模式才可能传百分比值(1-100)。
                # 判断标准：skip_ai模式下amount=position_size(币数)，跳过转换；
                # AI模式下amount若在1-100之间视为百分比。
                if skip_ai:
                    # 纯代码模式：amount已经是币数，直接使用
                    pass
                elif decision.amount > 0 and decision.amount <= 100:  # AI模式百分比格式
                    pct = decision.amount / 100.0
                    snap = snapshots.get(decision.symbol)
                    if snap:
                        position_value = portfolio.total_equity * pct * decision.leverage
                        decision.amount = position_value / snap.avg_price if snap.avg_price > 0 else 0
                
                snapshot = snapshots.get(decision.symbol)
                if not snapshot:
                    snapshot = SimpleSnapshot(decision.price or 0)
                
                logger.info("[Trinity][Step3] {} → {} {} | 置信度:{:.2f}",
                           decision.symbol, decision.action.value, 
                           decision.amount, decision.confidence)
                
                await self._execute_decision(decision, snapshot, portfolio)
                
            except Exception as e:
                logger.error("[Trinity][Step3] {} 执行失败: {}", decision.symbol, e)
                continue
        
        logger.info("[Trinity] 三位一体策略本轮完成")

    async def _monitor_positions(self, snapshots: Dict[str, Any]):
        """
        监控当前持仓，触发止损/止盈

        Fix P1-3: 优先依赖交易所保护单（STOP_MARKET/TAKE_PROFIT_MARKET），
        本地监控只在没有交易所保护单时才主动 close_position。
        如果交易所保护单存在，只记录/跳过，避免双重触发。

        Fix: 分批止盈 — 逐级检查TP，非最后级做部分平仓(PARTIAL_TP)，
        最后级做全量平仓(CLOSED)。止损始终全量平仓。
        """
        if not hasattr(self, 'order_executor') or not self.order_executor:
            return

        positions = self.order_executor.get_positions()
        for pos in list(positions):
            # 使用副本迭代，防止 close_position 修改列表导致跳过
            symbol = pos.symbol  # 'BTCUSDT' 格式
            # Fix #1 (Round4): Symbol格式对齐 — 尝试两种格式匹配snapshot
            # pos.symbol='BTCUSDT' 而 snapshots key='BTC/USDT'
            snap = snapshots.get(symbol)
            if snap is None and '/' not in symbol:
                # 尝试 BTCUSDT → BTC/USDT 格式匹配
                for quote in ('USDT', 'BUSD', 'BTC'):
                    if symbol.endswith(quote):
                        alt_symbol = symbol[:-len(quote)] + '/' + quote
                        snap = snapshots.get(alt_symbol)
                        if snap:
                            break
            if snap is None:
                continue
            current_price = snap.avg_price
            direction_mult = 1 if pos.direction == 'LONG' else -1

            # === Check stop loss（始终全量平仓）===
            if pos.stop_loss and direction_mult * (current_price - pos.stop_loss) <= 0:
                close_reason = f"止损 {pos.stop_loss}"
                # 交易所止损单存在时跳过本地平仓，但要取消止盈挂单
                if getattr(pos, 'has_exchange_stop', False):
                    logger.info(f"[Monitor] {symbol} 触发{close_reason} @ {current_price}，"
                                f"交易所止损单存在，取消TP挂单并跳过本地平仓")
                    # Fix: 止损触发后必须取消止盈挂单，否则止盈单会成为孤儿订单
                    self.order_executor.cancel_algo_orders(symbol)
                    continue

                logger.warning(f"[Monitor] 触发{close_reason} {symbol} @ {current_price}")
                realized_pnl = (current_price - pos.entry_price) * pos.remaining_quantity \
                    if pos.direction == 'LONG' \
                    else (pos.entry_price - current_price) * pos.remaining_quantity
                # Fix P0-3: 平仓加异常保护，仅在成功时记录 PnL
                try:
                    if self.order_executor.close_position(pos.order_id, close_reason):
                        await self.risk_control.record_trade_result(float(realized_pnl))
                    else:
                        logger.error(f"[Monitor] ⚠️ 止损平仓失败: {symbol} {pos.order_id}")
                except Exception as ce:
                    logger.error(f"[Monitor] ⚠️ 止损平仓异常: {symbol} | {ce}")
                continue

            # === Check take profit（逐级检查，分批处理）===
            for i, tp in enumerate(pos.take_profit_levels):
                if direction_mult * (current_price - tp) >= 0:
                    # 已命中TP级别i
                    tp_qty = pos.take_profit_quantities[i] \
                        if i < len(pos.take_profit_quantities) else 0

                    # 判断是否最后一级TP
                    is_last_level = (i == len(pos.take_profit_levels) - 1)

                    # 交易所TP单存在时跳过本地处理
                    if getattr(pos, 'has_exchange_tp', False):
                        logger.info(f"[Monitor] {symbol} TP{i+1}命中 @ {current_price}，"
                                    f"交易所TP单存在，跳过本地处理")
                        # 如果是最后一级且交易所处理了，更新本地状态
                        if is_last_level:
                            logger.info(f"[Monitor] {symbol} 最后一级TP已由交易所处理")
                        continue

                    # 本地处理TP命中
                    if is_last_level:
                        # 最后一级 → 全量平仓
                        close_reason = f"止盈{i+1}(末级) {tp}"
                        logger.warning(f"[Monitor] 触发{close_reason} {symbol} @ {current_price}")
                        realized_pnl = (current_price - pos.entry_price) * pos.remaining_quantity \
                            if pos.direction == 'LONG' \
                            else (pos.entry_price - current_price) * pos.remaining_quantity
                        # Fix P0-3: 平仓加异常保护
                        try:
                            if self.order_executor.close_position(pos.order_id, close_reason):
                                await self.risk_control.record_trade_result(float(realized_pnl))
                            else:
                                logger.error(f"[Monitor] ⚠️ 止盈平仓失败: {symbol} {pos.order_id}")
                        except Exception as ce:
                            logger.error(f"[Monitor] ⚠️ 止盈平仓异常: {symbol} | {ce}")
                    else:
                        # 非最后一级 → 部分平仓
                        # Fix P2-3: tp_qty=0时跳过break，继续检查下一级
                        if tp_qty <= 0:
                            logger.warning(f"[Monitor] {symbol} TP{i+1} 数量为0，跳过部分平仓，检查下一级")
                            continue
                        close_reason = f"部分止盈{i+1} {tp}"
                        logger.info(f"[Monitor] 触发{close_reason} {symbol} @ {current_price}")
                        if hasattr(self.order_executor, '_partial_close_position'):
                            try:
                                success = self.order_executor._partial_close_position(
                                    pos.order_id, tp_qty, close_reason
                                )
                                if success:
                                    partial_pnl = (current_price - pos.entry_price) * tp_qty \
                                        if pos.direction == 'LONG' \
                                        else (pos.entry_price - current_price) * tp_qty
                                    await self.risk_control.record_trade_result(float(partial_pnl))
                            except Exception as pe:
                                logger.error(f"[Monitor] ⚠️ 部分平仓异常: {symbol} | {pe}")
                    break  # 每次只处理一个TP级别

    def _recover_positions_from_exchange(self):
        """
        从币安交易所恢复已有持仓（引擎重启时使用）
        """
        import time, hmac, hashlib, requests
        
        try:
            api_key = self.order_executor.api_key
            api_secret = self.order_executor.api_secret
            if not api_key or not api_secret:
                logger.info("[Recover] 未配置API凭证，跳过持仓恢复")
                return
            
            session = self.order_executor.session
            base_url = self.order_executor.futures_base_url
            
            ts = int(time.time() * 1000)
            qs = f'timestamp={ts}'
            sig = hmac.new(api_secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
            url = f'{base_url}/fapi/v2/positionRisk?{qs}&signature={sig}'
            logger.info(f"[Recover] 正在从交易所获取持仓信息...")
            resp = session.get(url, headers={'X-MBX-APIKEY': api_key}, timeout=30)
            positions = resp.json()
            
            from modules.order_executor import Order, OrderStatus
            self._synced_positions = {}
            for p in positions:
                amt = float(p['positionAmt'])
                if amt == 0:
                    continue
                
                symbol = p['symbol']
                entry_price = float(p['entryPrice'])
                direction = 'LONG' if amt > 0 else 'SHORT'
                qty = abs(amt)
                # 从API响应中读取实际杠杆倍数（不再硬编码为1.0）
                actual_leverage = float(p.get('leverage', 1.0))
                
                # 根据三位一体策略配置计算止损/止盈（与策略一致）
                trinity_cfg = self.config.get('trinity', {})
                risk_cfg = trinity_cfg.get('risk', {})
                tp_cfg = trinity_cfg.get('take_profit', {})
                
                max_risk_pct = risk_cfg.get('max_risk_per_trade', 0.02)      # 默认2%
                min_rr = tp_cfg.get('min_rr_ratio', 2.0)                     # 最小盈亏比2:1
                
                if direction == 'LONG':
                    sl = round(entry_price * (1 - max_risk_pct), 6)
                    # Fix: 如果当前价已低于止损，跳过恢复（避免立即触发）
                    current_price = float(p.get('markPrice', entry_price))
                    if current_price < sl:
                        logger.warning("[Recover] {} 当前价 ${:.4f} < 止损 ${:.4f}，"
                                      "跳过恢复（仓位已深度亏损）", symbol, current_price, sl)
                        continue
                    tp1 = round(entry_price * (1 + max_risk_pct * min_rr), 6)       # 2:1
                    tp2 = round(entry_price * (1 + max_risk_pct * min_rr * 1.5), 6) # 3:1
                    tp3 = round(entry_price * (1 + max_risk_pct * min_rr * 2.0), 6) # 4:1
                else:
                    sl = round(entry_price * (1 + max_risk_pct), 6)
                    # Fix: 如果当前价已高于止损，跳过恢复
                    current_price = float(p.get('markPrice', entry_price))
                    if current_price > sl:
                        logger.warning("[Recover] {} 当前价 ${:.4f} > 止损 ${:.4f}，"
                                      "跳过恢复（仓位已深度亏损）", symbol, current_price, sl)
                        continue
                    tp1 = round(entry_price * (1 - max_risk_pct * min_rr), 6)
                    tp2 = round(entry_price * (1 - max_risk_pct * min_rr * 1.5), 6)
                    tp3 = round(entry_price * (1 - max_risk_pct * min_rr * 2.0), 6)
                
                # 计算分批止盈数量，使用 step_size 保护避免小仓位归零
                step_info = self.order_executor._get_symbol_step_size(symbol)
                step_size = step_info['step_size']
                part1 = math.floor(qty * 0.5 / step_size) * step_size
                part2 = math.floor(qty * 0.3 / step_size) * step_size
                part3 = max(0, qty - part1 - part2)
                
                order = Order(
                    order_id=f'recovered_{symbol}_{int(time.time())}',
                    symbol=symbol,
                    direction=direction,
                    status=OrderStatus.OPENED,
                    entry_price=entry_price,
                    entry_time=datetime.now(),
                    position_size=qty,
                    leverage=actual_leverage,  # 从交易所API读取实际杠杆
                    stop_loss=sl,
                    take_profit_levels=[tp1, tp2, tp3],
                    take_profit_quantities=[part1, part2, part3],
                    remaining_quantity=qty
                )
                
                self.order_executor.positions.append(order)
                self._synced_positions[symbol] = order
                logger.info(f"[Recover] 恢复持仓: {symbol} {direction} {qty}币 @ {entry_price} "
                       f"SL={sl} TP={tp1}/{tp2}/{tp3} RR={min_rr}:1")
            
            if not self._synced_positions:
                logger.info("[Recover] 交易所无持仓")
                
        except Exception as e:
            logger.warning(f"[Recover] 持仓恢复失败: {e}")
    
async def main():
    engine = WangCaiEngine()
    try:
        await engine.run()
    except KeyboardInterrupt:
        await engine.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
