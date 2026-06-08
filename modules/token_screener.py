"""
代币预筛选器 - Trinity 三位一体友好版
========================================
在送 Trinity (PA+SMC+Wyckoff) 深度分析前，先用代码快速筛掉明显不适合的三代币。
大幅减少 Trinity 分析调用次数，提升决策效率。

筛选逻辑（Trinity Prefilter）：
  1. 合约有效性（价格 > 0，基础数据可用）
  2. 最低价格过滤（过滤死币/归零币）
  3. 流动性检查（24h成交量 > 最低阈值）
  4. K线数据可用性（至少20根K线可供结构分析）
  5. 波动性检查（ATR% 在合理区间，无波动或巨幅波动的不适合结构分析）
  6. 基础质量评分 → 排序 → 取 Top N

放弃的指标（与 Trinity PA+SMC+Wyckoff 不匹配）：
  - RSI：Trinity 不依赖动量震荡器，RSI 超买超卖在趋势行情中会误杀
  - EMA 排列硬过滤：Trinity 的 SMC 结构分析取代了均线趋势判断
  - 动量评分：由 Wyckoff 成交量分析和 SMC 结构突破替代

输出：
  - 通过筛选的代币列表（按评分排序）
  - 每个代币的筛选报告
  - 筛掉的代币及原因
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class TokenScore:
    """单币种筛选评分"""
    symbol: str
    passed: bool = False
    total_score: float = 0.0       # 0-100
    checks: Dict[str, bool] = field(default_factory=dict)
    scores: Dict[str, float] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)
    
    # 基本信息
    price: float = 0.0
    volume_24h: float = 0.0
    atr_pct: float = 0.0
    trend: str = "NEUTRAL"
    rsi: Optional[float] = None     # 保留字段兼容，Trinity 不使用
    ema_slope: float = 0.0


@dataclass  
class ScreenerResult:
    """筛选结果"""
    timestamp: str = ""
    total_scanned: int = 0
    passed_count: int = 0
    rejected_count: int = 0
    passed: List[TokenScore] = field(default_factory=list)
    rejected: List[TokenScore] = field(default_factory=list)
    top_symbols: List[str] = field(default_factory=list)


class TokenScreener:
    """
    Trinity 三位一体友好预筛选器
    
    Trinity 基于 PA + SMC + Wyckoff，不依赖 RSI/EMA 交叉等传统动量指标。
    本筛选器只做轻量结构性预过滤，确保送入 Trinity 的代币都有足够的数据质量和流动性。
    
    配置项:
        min_volume_usdt: 最小24h成交量(USDT)，默认50万
        min_price: 最小价格，默认0.001
        min_atr_pct: 最小波动率(ATR%)，默认1.0%
        max_atr_pct: 最大波动率(ATR%)，默认40%（过高波动不利于结构分析）
        min_klines: 最少K线数量，默认20
        max_tokens: 最多通过的代币数，默认28（当前主流扫描池规模）
    """
    
    def __init__(self, config: Dict = None):
        cfg = config or {}
        self.min_volume_usdt = cfg.get("min_volume_usdt", 500_000)   # 50万USDT
        self.min_price = cfg.get("min_price", 0.001)
        self.min_atr_pct = cfg.get("min_atr_pct", 1.0)               # 最低1%波动
        self.max_atr_pct = cfg.get("max_atr_pct", 40.0)              # 最高40%波动（防止极端波动干扰结构）
        self.min_klines = cfg.get("min_klines", 20)                  # 最小K线数
        self.max_tokens = cfg.get("max_tokens", 28)                  # Top N；当前候选池较小，默认全量进入 Trinity
        
        # 评分配重 - Trinity 友好：强调流动性、波动适中、数据质量
        # 已移除 momentum/RSI 权重
        self.weights = {
            "volume": 40,      # 流动性最重要（+15 from old momentum）
            "volatility": 35,  # 波动性适中（+15 from old momentum）
            "data_quality": 25, # 基础数据质量（new: K线完整性 + 价格有效性）
        }
    
    # ================================================================
    #  主入口
    # ================================================================
    
    def screen(self, market_data: Dict[str, Dict]) -> ScreenerResult:
        """
        对所有代币执行快速筛选
        
        Args:
            market_data: {
                "BTC/USDT": {
                    "price": float,
                    "volume_24h": float (USDT),
                    "klines_1h": pd.DataFrame (至少50根),
                    "klines_4h": pd.DataFrame (至少20根),
                    "ticker": {...}
                }, ...
            }
        
        Returns:
            ScreenerResult
        """
        result = ScreenerResult(
            timestamp=datetime.now().isoformat(),
            total_scanned=len(market_data)
        )
        
        for symbol, data in market_data.items():
            score = self._score_token(symbol, data)
            
            if score.passed:
                result.passed.append(score)
                result.passed_count += 1
            else:
                result.rejected.append(score)
                result.rejected_count += 1
        
        # 按评分排序，取Top N（优先保留主流/高市值币）
        result.passed.sort(key=lambda x: x.total_score, reverse=True)
        
        # 硬性保留当前主流/高流动性扫描池（不挤占 slots，额外保留）。
        # Step0 只做数据质量/流动性门卫，不再因为评分排序把主流币挤出 Trinity 深度分析。
        priority_set = {
            # 主流币
            'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT',
            # 美股代币化 (TradFi-Perps, 协议已签)
            'AAPL/USDT', 'TSLA/USDT', 'GOOGL/USDT', 'AMZN/USDT', 'MSFT/USDT',
            'NVDA/USDT', 'META/USDT', 'MSTR/USDT', 'COIN/USDT', 'AMD/USDT',
            'PLTR/USDT', 'HOOD/USDT', 'UBER/USDT',
            # RWA代币化
            'ONDO/USDT',
            # 高市值加密
            'DOGE/USDT', 'ADA/USDT', 'AVAX/USDT', 'LINK/USDT', 'DOT/USDT',
            'SUI/USDT', 'APT/USDT', 'ARB/USDT', 'OP/USDT', 'NEAR/USDT',
            'AAVE/USDT', 'UNI/USDT', 'CRV/USDT', 'PEPE/USDT', 'WIF/USDT',
            'FET/USDT', 'TAO/USDT', 'WLD/USDT', 'FIL/USDT',
            # 中市值活跃代币
            'MU/USDT', 'SAGA/USDT', 'RENDER/USDT', 'INJ/USDT', 'TIA/USDT',
            'DYDX/USDT', 'PENDLE/USDT', 'DOGS/USDT', 'DYM/USDT', 'PHA/USDT',
            'DRIFT/USDT', 'DEXE/USDT', 'RIF/USDT',
        }
        priority_passed = [s for s in result.passed if s.symbol in priority_set]
        other_passed = [s for s in result.passed if s.symbol not in priority_set]
        result.passed = priority_passed + other_passed[:max(0, self.max_tokens - len(priority_passed))]
        result.passed_count = len(result.passed)
        result.top_symbols = [s.symbol for s in result.passed]
        
        return result
    
    def screen_from_klines(self, klines_dict: Dict[str, Dict[str, pd.DataFrame]],
                           tickers: Dict[str, Dict] = None) -> ScreenerResult:
        """
        更简单的接口：直接从K线+ticker数据筛选
        
        Args:
            klines_dict: {"BTC/USDT": {"1h": df, "4h": df}}
            tickers: {"BTC/USDT": {"last": 70000, "volume": 1e9}}
        """
        market_data = {}
        
        for symbol in klines_dict:
            klines = klines_dict[symbol]
            df_1h = klines.get("1h")
            
            # 价格
            price = df_1h.iloc[-1]['close'] if df_1h is not None and len(df_1h) > 0 else 0
            
            # 成交量（估算）
            volume_24h = 0
            if tickers and symbol in tickers:
                t = tickers[symbol]
                # ccxt ticker 的 quoteVolume 是USDT成交量
                volume_24h = t.get('quoteVolume', 0) or t.get('baseVolume', 0) * price
            
            market_data[symbol] = {
                "price": price,
                "volume_24h": volume_24h,
                "klines_1h": df_1h,
                "klines_4h": klines.get("4h"),
                "ticker": tickers.get(symbol, {}) if tickers else {},
            }
        
        return self.screen(market_data)
    
    # ================================================================
    #  单币种评分
    # ================================================================
    
    def _score_token(self, symbol: str, data: Dict) -> TokenScore:
        """对单个代币执行所有检查并打分（Trinity 友好版）"""
        score = TokenScore(symbol=symbol)
        score.price = data.get("price", 0)
        score.volume_24h = data.get("volume_24h", 0)
        
        df_1h = data.get("klines_1h")
        df_4h = data.get("klines_4h")
        
        data_quality_score = 0  # 数据质量子评分
        
        # === 检查1: 价格有效性 ===
        if score.price <= 0:
            score.checks["price_valid"] = False
            score.reasons.append("价格无效")
            return score
        score.checks["price_valid"] = True
        data_quality_score += 10
        
        # === 检查2: 最低价格过滤（死币） ===
        if score.price < self.min_price:
            score.checks["min_price"] = False
            score.reasons.append(f"价格过低 ${score.price:.6f} < ${self.min_price}")
            return score
        score.checks["min_price"] = True
        data_quality_score += 5
        
        # === 检查3: 流动性检查 ===
        if score.volume_24h < self.min_volume_usdt:
            score.checks["liquidity"] = False
            score.reasons.append(
                f"24h成交量不足 ${score.volume_24h:,.0f} < ${self.min_volume_usdt:,.0f}"
            )
            return score
        score.checks["liquidity"] = True
        
        # 成交量评分 (对数尺度，权重更高)
        vol_log = np.log10(max(score.volume_24h, 1))
        vol_score = min(vol_log / 8 * self.weights["volume"], self.weights["volume"])
        score.scores["volume"] = round(vol_score, 1)
        
        # === 检查4: K线数据可用性 ===
        if df_1h is None or len(df_1h) < self.min_klines:
            score.checks["klines_available"] = False
            score.reasons.append(f"K线数据不足 ({len(df_1h) if df_1h is not None else 0} < {self.min_klines})")
            return score
        score.checks["klines_available"] = True
        data_quality_score += 5
        
        # 4H K线数据加分（有4H数据说明结构分析更充分）
        if df_4h is not None and len(df_4h) >= self.min_klines:
            data_quality_score += 5
        
        # === 检查5: 波动性检查（Trinity 需要足够的波动来形成结构） ===
        atr_data = self._calc_atr(df_1h)
        score.atr_pct = atr_data["atr_pct"]
        
        if atr_data["atr_pct"] < self.min_atr_pct:
            score.checks["volatility"] = False
            score.reasons.append(
                f"波动率过低 {atr_data['atr_pct']:.1f}% < {self.min_atr_pct}%"
            )
            # 波动不足不直接拒绝，只是降分（可能处于Wyckoff吸筹区间）
        elif atr_data["atr_pct"] > self.max_atr_pct:
            score.checks["volatility"] = False
            score.reasons.append(
                f"波动率过高 {atr_data['atr_pct']:.1f}% > {self.max_atr_pct}%"
            )
            # 波动过高也不直接拒绝，降分（极端行情不适合结构性入场）
        else:
            score.checks["volatility"] = True
        
        # 波动率评分（Trinity 偏好 2-20% 的适中波动，过低过高都不好）
        vol_pct = atr_data["atr_pct"]
        if 2 <= vol_pct <= 20:
            vol_score = self.weights["volatility"]
        elif 1.0 <= vol_pct <= 35:
            vol_score = self.weights["volatility"] * 0.6
        else:
            vol_score = self.weights["volatility"] * 0.2
        score.scores["volatility"] = round(vol_score, 1)
        
        # === 检查6: Trinity 基础趋势感知（轻量，不做硬过滤） ===
        # 注意：Trinity 的 SMC + Wyckoff 会做更深度的结构分析
        # 这里的轻量趋势仅用于报告参考，不做阻断
        trend = self._quick_trend_sense(df_1h)
        score.trend = trend["direction"]
        score.ema_slope = trend["ema_slope"]
        # RSI 在 Trinity 中不使用，保留为 None 以确保输出兼容
        score.rsi = None
        
        # 数据质量评分
        score.scores["data_quality"] = round(data_quality_score, 1)
        
        # === 计算总分 ===
        score.total_score = round(sum(score.scores.values()), 1)
        
        # === 通过判定 ===
        # 必要条件：有流动性 + 有价格 + 有效合约 + K线可用
        must_have = [
            score.checks.get("liquidity", False),
            score.checks.get("min_price", False),
            score.checks.get("price_valid", False),
            score.checks.get("klines_available", False),
        ]
        score.passed = all(must_have) and score.total_score >= 20
        
        return score
    
    # ================================================================
    #  轻量技术指标计算
    # ================================================================
    
    def _calc_atr(self, df: pd.DataFrame, period: int = 14) -> Dict:
        """计算ATR和ATR%"""
        if len(df) < period:
            return {"atr": 0, "atr_pct": 0}
        
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr1 = high - low
        tr2 = np.abs(high - close.shift())
        tr3 = np.abs(low - close.shift())
        tr = np.maximum(tr1, np.maximum(tr2, tr3))
        atr = tr.rolling(period).mean().iloc[-1]
        
        price = close.iloc[-1]
        atr_pct = (atr / price * 100) if price > 0 else 0
        
        return {"atr": round(float(atr), 4), "atr_pct": round(float(atr_pct), 2)}

    def _calc_rsi(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """保留给旧检查脚本的 RSI 兼容实现。"""
        close = df["close"].astype(float)
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return rsi.fillna(50)
    
    def _quick_trend_sense(self, df: pd.DataFrame) -> Dict:
        """
        轻量趋势感知（仅用于报告参考，不做硬过滤）
        
        Trinity 的 SMC 结构分析 + Wyckoff 阶段判断才是主趋势判断。
        此处仅做基础的 EMA 感知用于前端展示，不做任何阻断。
        
        返回:
            direction: BULLISH/BEARISH/NEUTRAL
            strength: 1-5
            ema_slope: EMA斜率
            rsi: None (Trinity 不使用 RSI)
        """
        if len(df) < 30:
            return {"direction": "NEUTRAL", "strength": 1, "ema_slope": 0, "rsi": None}
        
        close = df['close']
        
        # EMA（仅用作展示参考）
        ema20 = close.ewm(span=20, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()
        
        # EMA斜率
        if len(ema20) >= 5:
            ema_start = ema20.iloc[-5]
            ema_end = ema20.iloc[-1]
            ema_slope = (ema_end - ema_start) / max(ema_start, 1)
        else:
            ema_slope = 0
        
        # EMA排列
        current_price = close.iloc[-1]
        current_ema20 = ema20.iloc[-1]
        current_ema50 = ema50.iloc[-1]
        
        above_ema20 = current_price > current_ema20
        above_ema50 = current_price > current_ema50
        ema20_above_50 = current_ema20 > current_ema50
        
        # 趋势K线占比
        recent = df.tail(20)
        bull_bars = sum(1 for _, r in recent.iterrows() if r['close'] > r['open'])
        bull_ratio = bull_bars / len(recent)
        
        # 轻量判定（不做硬过滤）
        if ema20_above_50 and above_ema20 and bull_ratio > 0.5:
            direction = "BULLISH"
            strength = min(5, int((bull_ratio - 0.3) * 6) + 1 + (1 if above_ema50 else 0))
        elif not ema20_above_50 and not above_ema20 and bull_ratio < 0.5:
            direction = "BEARISH"
            strength = min(5, int((0.7 - bull_ratio) * 6) + 1 + (1 if not above_ema50 else 0))
        else:
            direction = "NEUTRAL"
            if abs(bull_ratio - 0.5) < 0.15:
                strength = 1
            elif bull_ratio > 0.5:
                strength = 2
            else:
                strength = 2
        
        return {
            "direction": direction,
            "strength": strength,
            "ema_slope": round(float(ema_slope), 6),
            "rsi": None,  # Trinity 不使用 RSI
        }
    
    # ================================================================
    #  报告生成
    # ================================================================
    
    def generate_report(self, result: ScreenerResult) -> str:
        """生成筛选报告"""
        lines = [
            "=" * 60,
            f"代币筛选报告 - {result.timestamp[:19]}",
            "=" * 60,
            f"总扫描: {result.total_scanned} | 通过: {result.passed_count} | 过滤: {result.rejected_count}",
            f"Top {len(result.passed)} 入选: {', '.join(result.top_symbols)}",
            "",
            "--- 通过筛选 ---",
        ]
        
        if result.passed:
            for i, s in enumerate(result.passed):
                lines.append(
                    f"  #{i+1} {s.symbol:12s} | "
                    f"评分:{s.total_score:5.1f} | "
                    f"价格:${s.price:,.4f} | "
                    f"量:${s.volume_24h:,.0f} | "
                    f"波动:{s.atr_pct:.1f}% | "
                    f"趋势参考:{s.trend:8s}"
                )
        else:
            lines.append("  (无)")

        if result.rejected:
            lines.append("")
            lines.append("--- 被过滤 ---")
            for s in result.rejected:
                lines.append(
                    f"  ❌ {s.symbol:12s} | "
                    f"评分:{s.total_score:.0f} | "
                    f"{'; '.join(s.reasons[:2])}"
                )
        
        return "\n".join(lines)
    
    def to_ai_context(self, result: ScreenerResult, max_detail: int = 5) -> str:
        """
        将筛选结果转为可供AI使用的上下文
        
        Args:
            result: 筛选结果
            max_detail: 最多详细描述几个代币
        
        Returns:
            格式化的AI上下文字符串
        """
        if not result.passed:
            return "本轮筛选：无代币通过预筛选，建议观望。"
        
        lines = [
            f"## 代码层预筛选结果",
            f"扫描{result.total_scanned}个代币，{result.passed_count}个通过技术筛选：",
            ""
        ]
        
        for i, token in enumerate(result.passed[:max_detail]):
            lines.append(f"### {i+1}. {token.symbol}")
            lines.append(f"- 价格: ${token.price:,.4f}")
            lines.append(f"- 24h成交量: ${token.volume_24h:,.0f}")
            lines.append(f"- 波动率(ATR%): {token.atr_pct:.1f}%")
            lines.append(f"- 趋势参考: {token.trend}（仅展示，不参与硬过滤）")
            lines.append(f"- 筛选评分: {token.total_score:.0f}/100")
            if token.reasons:
                lines.append(f"- 注意事项: {'; '.join(token.reasons)}")
            lines.append("")
        
        if len(result.passed) > max_detail:
            lines.append(f"...还有 {len(result.passed)-max_detail} 个代币")
        
        lines.append(f"**被过滤的代币({result.rejected_count}个):** "
                     f"{', '.join(s.symbol for s in result.rejected[:10])}")
        
        return "\n".join(lines)
