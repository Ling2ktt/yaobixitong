#!/usr/bin/env python3
"""
风险管理模块

功能：
1. 仓位管理（10 USDT/单）
2. 杠杆控制（3x）
3. 盈亏比检查（> 2）
4. 最大订单数限制（5个）
5. 订单质量评分
6. 日亏损限制（当日累计亏损超过阈值则熔断）
7. 熔断机制（连续亏损/日亏损触发冷却）
"""
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta
from loguru import logger
import time


class RiskCheckResult(Enum):
    """风险检查结果"""
    PASS = "PASS"           # 通过
    FAIL = "FAIL"           # 失败
    WARNING = "WARNING"     # 警告


@dataclass
class PositionSizing:
    """仓位配置"""
    capital_per_trade: float = 10.0  # 每单本金 (USDT)
    leverage: float = 1.0             # 杠杆倍数（与config risk.leverage_default对齐）
    max_positions: int = 5             # 最大持仓数
    min_risk_reward: float = 2.0      # 最小盈亏比


@dataclass
class OrderQuality:
    """订单质量评分"""
    score: float = 0.0          # 总分 (0-100)
    wyckoff_score: float = 0.0  # Wyckoff评分 (0-30)
    smc_score: float = 0.0       # SMC评分 (0-30)
    momentum_score: float = 0.0  # 动量评分 (0-20)
    risk_score: float = 0.0      # 风险评分 (0-20)
    
    def calculate_total(self) -> float:
        """计算总分"""
        self.score = (
            self.wyckoff_score +
            self.smc_score +
            self.momentum_score +
            self.risk_score
        )
        return self.score


class RiskControlModule:
    """风险管理模块"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        初始化风险管理模块
        
        Args:
            config: 配置字典
        """
        config = config or {}
        
        # 仓位配置
        self.position_sizing = PositionSizing(
            capital_per_trade=config.get('capital_per_trade', config.get('max_single_order_usdt', 10.0)),
            leverage=config.get('leverage', config.get('leverage_default', 1.0)),  # Fix: 兼容leverage_default
            max_positions=config.get('max_positions', 5),
            min_risk_reward=config.get('min_risk_reward', 2.0)
        )
        
        # 风控参数
        self.max_daily_loss_usdt = config.get('max_daily_loss_usdt', 500.0)  # 日亏损限额（USDT）
        self.max_daily_loss_pct = config.get('max_daily_loss_pct', 0.05)     # 日亏损百分比（5%）
        
        # 熔断机制
        self.circuit_breaker_enabled = config.get('circuit_breaker_enabled', True)
        # 支持嵌套结构：平级读取失败时从 circuit_breaker 子节点读取
        cb = config.get('circuit_breaker', {})
        self.consecutive_loss_limit = config.get('consecutive_losses', cb.get('consecutive_losses', 3))
        self.circuit_breaker_cooldown = config.get('cooldown_minutes', cb.get('cooldown_minutes', 30))
        self.daily_loss_trigger_pct = config.get('daily_loss_pct', cb.get('daily_loss_pct', 0.05))
        
        # 状态跟踪
        self.current_positions: List[Dict[str, Any]] = []
        self.order_history: List[Dict[str, Any]] = []
        self.daily_pnl: float = 0.0  # 当日累计盈亏
        self.consecutive_losses: int = 0  # 连续亏损次数
        self.circuit_breaker_active: bool = False  # 熔断是否激活
        self.circuit_breaker_start: Optional[datetime] = None  # 熔断开始时间
        self.last_trade_time: Optional[datetime] = None  # 上次交易时间
        
        logger.info("[RiskControl] 初始化完成 | "
                    f"每单本金: ${self.position_sizing.capital_per_trade} | "
                    f"杠杆: {self.position_sizing.leverage}x | "
                    f"最大持仓: {self.position_sizing.max_positions} | "
                    f"最小盈亏比: {self.position_sizing.min_risk_reward}")
        logger.info(f"[RiskControl] 风控参数 | "
                    f"日亏损限额: ${self.max_daily_loss_usdt} ({self.max_daily_loss_pct*100:.1f}%) | "
                    f"熔断: {self.circuit_breaker_enabled} (连续{self.consecutive_loss_limit}次亏损/日亏{self.daily_loss_trigger_pct*100:.1f}%)")
    
    def check_position_limit(self, current_count: Optional[int] = None) -> RiskCheckResult:
        """
        检查持仓数量限制
        
        Returns:
            RiskCheckResult
        """
        if current_count is None:
            current_count = len(self.current_positions)
        max_allowed = self.position_sizing.max_positions
        
        if current_count >= max_allowed:
            logger.warning(f"[RiskControl] ⚠️ 持仓数量已达上限: {current_count}/{max_allowed}")
            return RiskCheckResult.FAIL
        
        logger.debug(f"[RiskControl] ✅ 持仓数量正常: {current_count}/{max_allowed}")
        return RiskCheckResult.PASS

    def _check_duplicate_symbol(self, symbol: str,
                                 existing_symbols: List[str]) -> Tuple[RiskCheckResult, str]:
        """检查是否重复开仓同一代币

        兼容两种格式：BTCUSDT ↔ BTC/USDT

        Args:
            symbol: 待开仓的代币符号（如 'BTC/USDT' 或 'BTCUSDT'）
            existing_symbols: 当前持有代币列表

        Returns:
            (检查结果, 消息)
        """
        if not symbol:
            return RiskCheckResult.PASS, "空符号，跳过重复检查"

        # 统一为无斜杠格式比较
        decision_clean = symbol.replace('/', '')
        for existing in existing_symbols:
            existing_clean = existing.replace('/', '') if existing else ''
            if decision_clean == existing_clean:
                logger.warning(f"[RiskControl] 🔴 重复开仓拒绝: {symbol} 已有持仓")
                return RiskCheckResult.FAIL, f"该代币 {symbol} 已有持仓，禁止重复开仓"

        return RiskCheckResult.PASS, f"{symbol} 无重复持仓"

    def check_risk_reward(
        self,
        entry_price: float,
        stop_loss: float,
        take_profit: float
    ) -> Tuple[RiskCheckResult, float]:
        """
        检查盈亏比
        
        Args:
            entry_price: 入场价
            stop_loss: 止损价
            take_profit: 止盈价
            
        Returns:
            (检查结果, 盈亏比)
        """
        # 计算风险（亏损）
        if entry_price > stop_loss:
            # 多头
            risk = entry_price - stop_loss
            reward = take_profit - entry_price
        else:
            # 空头
            risk = stop_loss - entry_price
            reward = entry_price - take_profit
        
        # 避免除零
        if risk == 0:
            logger.error("[RiskControl] ❌ 风险为0（入场价=止损价）")
            return RiskCheckResult.FAIL, 0.0
        
        # 计算盈亏比
        risk_reward_ratio = reward / risk
        
        if risk_reward_ratio < self.position_sizing.min_risk_reward:
            logger.warning(f"[RiskControl] ⚠️ 盈亏比不足: {risk_reward_ratio:.2f} < {self.position_sizing.min_risk_reward}")
            return RiskCheckResult.FAIL, risk_reward_ratio
        
        logger.debug(f"[RiskControl] ✅ 盈亏比合格: {risk_reward_ratio:.2f} >= {self.position_sizing.min_risk_reward}")
        return RiskCheckResult.PASS, risk_reward_ratio
    
    def calculate_position_size(
        self,
        entry_price: float,
        stop_loss: float
    ) -> Tuple[float, float]:
        """
        计算仓位大小
        
        Args:
            entry_price: 入场价
            stop_loss: 止损价
            
        Returns:
            (仓位大小(币), 实际本金(USDT))
        """
        capital = self.position_sizing.capital_per_trade
        leverage = self.position_sizing.leverage
        
        # 计算止损百分比
        if entry_price > stop_loss:
            # 多头
            stop_percentage = (entry_price - stop_loss) / entry_price
        else:
            # 空头
            stop_percentage = (stop_loss - entry_price) / entry_price
        
        # 计算仓位大小（币）
        # 公式：仓位 = (本金 * 杠杆) / 入场价
        position_value = capital * leverage  # 仓位价值 (USDT)
        position_size = position_value / entry_price  # 仓位大小 (币)
        
        logger.debug(f"[RiskControl] 仓位计算: "
                    f"本金=${capital} | 杠杆={leverage}x | "
                    f"仓位价值=${position_value} | 仓位大小={position_size:.4f}币 | "
                    f"止损%={stop_percentage*100:.2f}%")
        
        return position_size, capital
    
    def score_order_quality(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        wyckoff_signal: Dict[str, Any],
        smc_signal: Dict[str, Any]
    ) -> OrderQuality:
        """
        评分订单质量
        
        Args:
            symbol: 交易对
            direction: 方向 (LONG/SHORT)
            entry_price: 入场价
            stop_loss: 止损价
            take_profit: 止盈价
            wyckoff_signal: Wyckoff信号
            smc_signal: SMC信号
            
        Returns:
            OrderQuality对象
        """
        quality = OrderQuality()
        
        # === 1. Wyckoff评分 (0-30) ===
        # 阶段匹配度
        wyckoff_phase = wyckoff_signal.get('phase', 'unknown')
        if direction == 'LONG' and wyckoff_phase == 'accumulation':
            quality.wyckoff_score += 15
        elif direction == 'SHORT' and wyckoff_phase == 'distribution':
            quality.wyckoff_score += 15
        
        # SPRING/UTAD确认
        if wyckoff_signal.get('spring_detected'):
            quality.wyckoff_score += 10
        if wyckoff_signal.get('utad_detected'):
            quality.wyckoff_score += 10
        
        # 成交量确认
        if wyckoff_signal.get('volume_confirmed'):
            quality.wyckoff_score += 5
        
        # === 2. SMC评分 (0-30) ===
        # OB触碰
        if smc_signal.get('ob_touched'):
            quality.smc_score += 10
        
        # FVG填充
        if smc_signal.get('fvg_filled'):
            quality.smc_score += 10
        
        # 流动性 sweep
        if smc_signal.get('liquidity_sweep'):
            quality.smc_score += 10
        
        # === 3. 动量评分 (0-20) ===
        # 7日涨幅
        momentum_7d = smc_signal.get('momentum_7d', 0)
        if direction == 'LONG' and momentum_7d > 10:
            quality.momentum_score += 10
        elif direction == 'SHORT' and momentum_7d < -10:
            quality.momentum_score += 10
        
        # RSI
        rsi = smc_signal.get('rsi', 50)
        if direction == 'LONG' and rsi < 30:
            quality.momentum_score += 10
        elif direction == 'SHORT' and rsi > 70:
            quality.momentum_score += 10
        
        # === 4. 风险评分 (0-20) ===
        # 盈亏比
        _, rr_ratio = self.check_risk_reward(entry_price, stop_loss, take_profit)
        if rr_ratio >= 3:
            quality.risk_score += 10
        elif rr_ratio >= 2:
            quality.risk_score += 5
        
        # 止损距离
        stop_distance = abs(entry_price - stop_loss) / entry_price
        if stop_distance < 0.05:  # < 5%
            quality.risk_score += 10
        elif stop_distance < 0.10:  # < 10%
            quality.risk_score += 5
        
        # 计算总分
        quality.calculate_total()
        
        logger.info(f"[RiskControl] 订单质量评分: {symbol} {direction}\n"
                    f"  总分: {quality.score:.1f}/100\n"
                    f"  Wyckoff: {quality.wyckoff_score:.1f}/30\n"
                    f"  SMC: {quality.smc_score:.1f}/30\n"
                    f"  动量: {quality.momentum_score:.1f}/20\n"
                    f"  风险: {quality.risk_score:.1f}/20")
        
        return quality
    
    def select_top_orders(
        self,
        orders: List[Dict[str, Any]],
        max_orders: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        选择质量最高的N个订单
        
        Args:
            orders: 订单列表
            max_orders: 最大订单数（默认使用配置值）
            
        Returns:
            筛选后的订单列表
        """
        max_orders = max_orders or self.position_sizing.max_positions
        
        # 按质量评分排序
        sorted_orders = sorted(
            orders,
            key=lambda x: x.get('quality_score', 0),
            reverse=True
        )
        
        # 选择前N个
        top_orders = sorted_orders[:max_orders]
        
        logger.info(f"[RiskControl] 订单筛选: {len(orders)} -> {len(top_orders)} (前{len(top_orders)}个)")
        
        for i, order in enumerate(top_orders, 1):
            symbol = order.get('symbol', 'N/A')
            score = order.get('quality_score', 0)
            logger.info(f"  {i}. {symbol} (评分: {score:.1f})")
        
        return top_orders
    
    def add_position(self, position: Dict[str, Any]):
        """
        添加持仓
        
        Args:
            position: 持仓信息
        """
        self.current_positions.append(position)
        logger.info(f"[RiskControl] 持仓 +1 | 当前: {len(self.current_positions)}/{self.position_sizing.max_positions}")
    
    def remove_position(self, symbol: str):
        """
        移除持仓
        
        Args:
            symbol: 交易对
        """
        self.current_positions = [
            p for p in self.current_positions
            if p.get('symbol') != symbol
        ]
        logger.info(f"[RiskControl] 持仓 -1 | 当前: {len(self.current_positions)}/{self.position_sizing.max_positions}")
    
    def get_current_positions(self) -> List[Dict[str, Any]]:
        """
        获取当前持仓
        
        Returns:
            持仓列表
        """
        return self.current_positions
    
    def can_open_new_position(self) -> bool:
        """
        是否可以开新仓
        
        Returns:
            True/False
        """
        return len(self.current_positions) < self.position_sizing.max_positions

    def _check_circuit_breaker(self) -> Tuple[RiskCheckResult, str]:
        """
        检查熔断状态
        
        检查项：
        1. 熔断是否激活且未到冷却时间
        2. 如果冷却时间已过，自动解除熔断
        
        Returns:
            (检查结果, 消息)
        """
        if not self.circuit_breaker_enabled:
            return RiskCheckResult.PASS, "熔断机制未启用"
        
        # 检查熔断是否激活
        if self.circuit_breaker_active and self.circuit_breaker_start:
            elapsed = (datetime.now() - self.circuit_breaker_start).total_seconds() / 60
            cooldown = self.circuit_breaker_cooldown
            
            if elapsed < cooldown:
                remaining = cooldown - elapsed
                msg = (f"熔断中！剩余冷却时间 {remaining:.1f} 分钟 "
                       f"(连续亏损 {self.consecutive_losses} 次)")
                logger.warning(f"[RiskControl] 🔴 {msg}")
                return RiskCheckResult.FAIL, msg
            else:
                # 冷却时间已过，自动解除
                logger.info(f"[RiskControl] ✅ 熔断冷却完成 ({elapsed:.1f}分钟)，自动解除")
                self.reset_circuit_breaker()
                return RiskCheckResult.PASS, "熔断已自动解除（冷却完成）"
        
        return RiskCheckResult.PASS, "熔断正常"
    
    def _check_daily_loss(self, total_equity: float) -> Tuple[RiskCheckResult, str]:
        """
        检查日亏损限制
        
        Args:
            total_equity: 账户总权益
            
        Returns:
            (检查结果, 消息)
        """
        # 当日盈利则不触发
        if self.daily_pnl >= 0:
            return RiskCheckResult.PASS, f"当日盈亏 +${self.daily_pnl:.2f}，正常"
        
        daily_loss_abs = abs(self.daily_pnl)
        
        # 检查绝对亏损限额
        if daily_loss_abs >= self.max_daily_loss_usdt:
            msg = (f"日亏损 ${daily_loss_abs:.2f} >= 限额 ${self.max_daily_loss_usdt:.2f}")
            logger.warning(f"[RiskControl] 🔴 {msg}")
            return RiskCheckResult.FAIL, msg
        
        # 检查百分比亏损限额
        if total_equity > 0:
            loss_pct = daily_loss_abs / total_equity
            if loss_pct >= self.daily_loss_trigger_pct:
                msg = (f"日亏损 {loss_pct*100:.2f}% >= 熔断线 {self.daily_loss_trigger_pct*100:.1f}%")
                logger.warning(f"[RiskControl] 🔴 {msg}")
                return RiskCheckResult.FAIL, msg
        
        return RiskCheckResult.PASS, f"当日亏损 ${daily_loss_abs:.2f}，在限额内"
    
    def record_trade_result(self, pnl: float):
        """
        记录交易结果，更新日亏损和连续亏损状态
        
        Args:
            pnl: 本次交易盈亏（正=盈利，负=亏损）
        """
        self.daily_pnl += pnl
        self.last_trade_time = datetime.now()
        
        if pnl < 0:
            self.consecutive_losses += 1
            logger.info(f"[RiskControl] 交易亏损 ${abs(pnl):.2f} | "
                       f"连续亏损: {self.consecutive_losses} | "
                       f"当日累计: ${self.daily_pnl:.2f}")
            
            # 触发熔断：连续亏损超限 或 日亏损超限
            should_break = False
            if self.circuit_breaker_enabled:
                if self.consecutive_losses >= self.consecutive_loss_limit:
                    logger.warning(f"[RiskControl] 🔴 连续亏损 {self.consecutive_losses} 次，触发熔断")
                    should_break = True
            if should_break:
                self.activate_circuit_breaker()
        else:
            # 盈利则重置连续亏损计数
            if self.consecutive_losses > 0:
                logger.info(f"[RiskControl] 交易盈利 ${pnl:.2f}，重置连续亏损计数")
            self.consecutive_losses = 0
    
    def activate_circuit_breaker(self):
        """手动激活熔断"""
        self.circuit_breaker_active = True
        self.circuit_breaker_start = datetime.now()
        cooldown = self.circuit_breaker_cooldown
        resume_time = self.circuit_breaker_start + timedelta(minutes=cooldown)
        logger.warning(f"[RiskControl] 🔴 熔断已激活 | "
                      f"冷却 {cooldown} 分钟 | "
                      f"预计恢复: {resume_time.strftime('%H:%M:%S')}")
    
    def reset_circuit_breaker(self):
        """重置熔断状态"""
        self.circuit_breaker_active = False
        self.circuit_breaker_start = None
        self.consecutive_losses = 0
        logger.info("[RiskControl] ✅ 熔断已解除，重置连续亏损计数")
    
    def reset_daily_stats(self):
        """重置每日统计（跨日时调用）"""
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.circuit_breaker_active = False
        self.circuit_breaker_start = None
        logger.info("[RiskControl] 每日统计已重置")
    
    def get_status(self) -> Dict[str, Any]:
        """获取风控状态摘要"""
        status = {
            "daily_pnl": self.daily_pnl,
            "consecutive_losses": self.consecutive_losses,
            "position_count": len(self.current_positions),
            "circuit_breaker_active": self.circuit_breaker_active,
        }
        if self.circuit_breaker_active and self.circuit_breaker_start:
            elapsed = (datetime.now() - self.circuit_breaker_start).total_seconds() / 60
            status["circuit_breaker_remaining_minutes"] = max(0, self.circuit_breaker_cooldown - elapsed)
        return status
    
    def check(self, decision: Dict[str, Any], account: Dict[str, Any] = None,
              daily_stats: Dict[str, Any] = None) -> 'RiskReport':
        """综合风险检查（engine 调用入口）
        
        Args:
            decision: 交易决策字典（含 symbol, action, entry_price, stop_loss, take_profit 等）
            account: 账户信息（total_equity, available_usdt, position_count）
            daily_stats: 当日统计（总权益、总盈亏等）
            
        Returns:
            RiskReport 对象
        """
        account = account or {}
        daily_stats = daily_stats or {}
        checks = []

        # Fix: 日亏损数据以 record_trade_result 累积值为准
        # account_manager 的快照计算需要2+次同步才准确，启动初期可能为0
        # 只在传入值明显更可靠时（非0且有变化）才同步
        if 'total_pnl' in daily_stats and abs(daily_stats['total_pnl']) > 0.01:
            # engine 传入的 account_manager 值非零时同步（跨重启恢复）
            if abs(daily_stats['total_pnl'] - self.daily_pnl) > 0.1:
                logger.info("[RiskControl] 日盈亏同步: 累积${:.2f} → 快照${:.2f}",
                           self.daily_pnl, daily_stats['total_pnl'])
                self.daily_pnl = daily_stats['total_pnl']
        
        # 1. 熔断检查（最高优先级）
        cb_result, cb_msg = self._check_circuit_breaker()
        checks.append(RiskCheckItem(
            name="circuit_breaker",
            result=cb_result,
            message=cb_msg
        ))
        
        # 2. 日亏损检查
        total_equity = account.get('total_equity', 0) or daily_stats.get('total_equity', 0)
        if total_equity > 0:
            dl_result, dl_msg = self._check_daily_loss(total_equity)
            checks.append(RiskCheckItem(
                name="daily_loss",
                result=dl_result,
                message=dl_msg
            ))
        else:
            checks.append(RiskCheckItem(
                name="daily_loss",
                result=RiskCheckResult.PASS,
                message="账户总权益未知，跳过日亏损检查"
            ))
        
        # 3. 持仓数量检查
        live_position_count = account.get('position_count')
        pos_result = self.check_position_limit(live_position_count)
        checks.append(RiskCheckItem(
            name="position_limit",
            result=pos_result,
            message=f"持仓 {live_position_count if live_position_count is not None else len(self.current_positions)}/{self.position_sizing.max_positions}"
        ))

        # 3b. 重复下单检查：已持有的代币不允许再开仓
        action = decision.get('action', '')
        if action not in ('CLOSE', 'HOLD', 'close', 'hold'):
            existing_symbols = account.get('existing_symbols', [])
            decision_symbol = decision.get('symbol', '')
            dup_result = self._check_duplicate_symbol(decision_symbol, existing_symbols)
            checks.append(RiskCheckItem(
                name="duplicate_symbol",
                result=dup_result[0],
                message=dup_result[1]
            ))

        # 4. 盈亏比检查（如果有入场价/止损/止盈）
        entry = decision.get('entry_price') or decision.get('entry', 0)
        sl = decision.get('stop_loss', 0)
        tp = decision.get('take_profit', 0)
        # Fix #3 (Round4): take_profit 可能是列表(Trinity多级止盈)，取第一级做盈亏比检查
        if isinstance(tp, list):
            tp = tp[0] if tp else 0
        if entry and sl and tp:
            rr_result, rr_ratio = self.check_risk_reward(entry, sl, tp)
            checks.append(RiskCheckItem(
                name="risk_reward",
                result=rr_result,
                message=f"盈亏比 {rr_ratio:.2f} (最小 {self.position_sizing.min_risk_reward})"
            ))
        else:
            # 无止损止盈信息时跳过（HOLD信号等）
            checks.append(RiskCheckItem(
                name="risk_reward",
                result=RiskCheckResult.PASS,
                message="无入场价信息，跳过盈亏比检查"
            ))
        
        # 5. 账户余额检查
        available = account.get('available_usdt', 0)
        if available < self.position_sizing.capital_per_trade:
            checks.append(RiskCheckItem(
                name="balance",
                result=RiskCheckResult.FAIL,
                message=f"可用余额 ${available:.2f} < 每单本金 ${self.position_sizing.capital_per_trade}"
            ))
        else:
            checks.append(RiskCheckItem(
                name="balance",
                result=RiskCheckResult.PASS,
                message=f"可用余额 ${available:.2f} >= 每单本金 ${self.position_sizing.capital_per_trade}"
            ))
        
        # 综合判断
        has_fail = any(c.result == RiskCheckResult.FAIL for c in checks)
        overall = RiskCheckResult.FAIL if has_fail else RiskCheckResult.PASS
        
        # 记录检查结果
        if overall == RiskCheckResult.FAIL:
            logger.warning(f"[RiskControl] 🔴 风险检查未通过 | 失败项: {[c.name for c in checks if c.result == RiskCheckResult.FAIL]}")
        else:
            logger.info(f"[RiskControl] ✅ 风险检查通过")
        
        return RiskReport(
            overall_level=overall,
            is_passed=not has_fail,
            checks=checks
        )


# 风险报告数据结构
@dataclass
class RiskCheckItem:
    """单项风险检查结果"""
    rule_name: str = ""
    name: str = ""  # 兼容：name 作为 rule_name 的别名
    result: RiskCheckResult = RiskCheckResult.PASS
    message: str = ""

    @property
    def passed(self) -> bool:
        return self.result != RiskCheckResult.FAIL

    @property
    def level(self) -> RiskCheckResult:
        return self.result

    def __post_init__(self):
        # name 和 rule_name 同步
        if self.name and not self.rule_name:
            self.rule_name = self.name
        elif self.rule_name and not self.name:
            self.name = self.rule_name


@dataclass
class RiskReport:
    """综合风险检查报告"""
    overall_level: RiskCheckResult = RiskCheckResult.PASS
    is_passed: bool = True
    checks: List[RiskCheckItem] = field(default_factory=list)


# 测试代码
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    
    print("测试风险管理模块\n")
    print("=" * 80)
    
    # 1. 初始化
    risk_control = RiskControlModule({
        'capital_per_trade': 10.0,
        'leverage': 3.0,
        'max_positions': 5,
        'min_risk_reward': 2.0
    })
    
    print("\n[测试1] 持仓数量检查")
    print("-" * 80)
    
    result = risk_control.check_position_limit()
    print(f"结果: {result}")
    
    print("\n[测试2] 盈亏比检查")
    print("-" * 80)
    
    # 多头示例
    entry = 100.0
    stop = 95.0
    tp = 110.0
    
    result, rr = risk_control.check_risk_reward(entry, stop, tp)
    print(f"入场: ${entry} | 止损: ${stop} | 止盈: ${tp}")
    print(f"结果: {result} | 盈亏比: {rr:.2f}")
    
    print("\n[测试3] 仓位计算")
    print("-" * 80)
    
    position_size, capital = risk_control.calculate_position_size(entry, stop)
    print(f"入场: ${entry} | 止损: ${stop}")
    print(f"仓位大小: {position_size:.4f} 币")
    print(f"实际本金: ${capital}")
    
    print("\n[测试4] 订单质量评分")
    print("-" * 80)
    
    wyckoff_signal = {
        'phase': 'accumulation',
        'spring_detected': True,
        'volume_confirmed': True
    }
    
    smc_signal = {
        'ob_touched': True,
        'fvg_filled': False,
        'liquidity_sweep': True,
        'momentum_7d': 15.0,
        'rsi': 28.0
    }
    
    quality = risk_control.score_order_quality(
        symbol="DOGEUSDT",
        direction="LONG",
        entry_price=0.105,
        stop_loss=0.100,
        take_profit=0.115,
        wyckoff_signal=wyckoff_signal,
        smc_signal=smc_signal
    )
    
    print(f"总分: {quality.score:.1f}/100")
    
    print("\n" + "=" * 80)
    print("测试完成！")
