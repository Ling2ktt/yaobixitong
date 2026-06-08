"""
策略模块: Quant Trend Engine Long Only v3 - BTC/USD, 4H
移植自 TradingView Pine Script

功能: 纯规则趋势跟踪策略，仅做多，5x杠杆
可调教项: 所有Pine Script输入参数
"""

import numpy as np
import pandas as pd
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from loguru import logger


class SignalType(Enum):
    """信号类型"""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    CLOSE = "CLOSE"


@dataclass
class StrategySignal:
    """策略信号"""
    signal: SignalType
    symbol: str
    price: float
    timestamp: datetime
    score: float = 0.0
    reason: str = ""
    stop_price: Optional[float] = None
    trail_price: Optional[float] = None
    leverage: float = 5.0
    confidence: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'action': self.signal.value,
            'symbol': self.symbol,
            'price': self.price,
            'stop_loss': self.stop_price,
            'take_profit': None,
            'leverage': self.leverage,
            'confidence': self.confidence,
            'reason': self.reason,
            'score': self.score,
            'timestamp': self.timestamp.isoformat()
        }


@dataclass
class StrategyState:
    """策略状态（跨K线持久化）"""
    last_exit_bar: Optional[int] = None
    trail_stop: Optional[float] = None
    high_since_entry: Optional[float] = None
    entry_bar_index: Optional[int] = None
    prev_pos_size: float = 0.0
    position_size: float = 0.0
    entry_price: Optional[float] = None


class QuantTrendStrategy:
    """
    Quant Trend Engine - 规则策略引擎
    
    移植自 Pine Script: Quant Trend Engine Long Only v3
    
    可调教参数（在初始化时传入，或修改默认值）：
    """
    
    def __init__(self, params: Optional[Dict[str, Any]] = None):
        params = params or {}
        
        # === EMA 长度 ===
        self.fast_len = params.get('fast_len', 18)
        self.mid_len = params.get('mid_len', 50)
        self.slow_len = params.get('slow_len', 120)
        self.smooth_len = params.get('smooth_len', 3)
        
        # === 阈值 ===
        self.min_score = params.get('min_score', 5.0)
        self.exit_score_thresh = params.get('exit_score_thresh', 2.5)
        self.min_sep_perc = params.get('min_sep_perc', 0.30)
        self.min_slow_slope_perc = params.get('min_slow_slope_perc', 0.03)
        self.min_eff = params.get('min_eff', 0.33)
        self.min_atr_regime = params.get('min_atr_regime', 0.95)
        self.min_breakout_atr = params.get('min_breakout_atr', 0.15)
        self.pullback_atr_mult = params.get('pullback_atr_mult', 0.90)
        self.reclaim_atr_mult = params.get('reclaim_atr_mult', 0.15)
        self.cooldown_bars = params.get('cooldown_bars', 5)
        
        # === 风险管理 ===
        self.leverage = params.get('leverage', 5.0)
        self.hard_stop_perc = params.get('hard_stop_perc', 2.0)
        self.trail_atr_mult = params.get('trail_atr_mult', 2.8)
        self.profit_lock_atr_mult = params.get('profit_lock_atr_mult', 20.8)
        
        # === 其他长度 ===
        self.pullback_len = params.get('pullback_len', 8)
        self.breakout_len = params.get('breakout_len', 20)
        self.eff_len = params.get('eff_len', 18)
        self.persist_len = params.get('persist_len', 7)
        self.mom_len = params.get('mom_len', 12)
        self.slope_len = params.get('slope_len', 10)
        self.atr_len = params.get('atr_len', 14)
        self.atr_base_len = params.get('atr_base_len', 40)
        
        # === 状态 ===
        self.state = StrategyState()
        
        # === 缓存 ===
        self._df: Optional[pd.DataFrame] = None
        
        logger.info("[Strategy] QuantTrend策略初始化 | 快EMA:{} 中EMA:{} 慢EMA:{} | 杠杆:{}x",
                   self.fast_len, self.mid_len, self.slow_len, self.leverage)
    
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        计算所有技术指标
        
        Args:
            df: K线数据，需包含 ['open', 'high', 'low', 'close', 'volume']
                 按时间升序排列
                 
        Returns:
            添加了所有指标的DataFrame
        """
        df = df.copy()
        
        # === 双平滑EMA ===
        # fast = ema(ema(close, fast_len), smooth_len)
        df['fast_inner'] = df['close'].ewm(span=self.fast_len, adjust=False).mean()
        df['fast'] = df['fast_inner'].ewm(span=self.smooth_len, adjust=False).mean()
        
        df['mid_inner'] = df['close'].ewm(span=self.mid_len, adjust=False).mean()
        df['mid'] = df['mid_inner'].ewm(span=self.smooth_len, adjust=False).mean()
        
        df['slow_inner'] = df['close'].ewm(span=self.slow_len, adjust=False).mean()
        df['slow'] = df['slow_inner'].ewm(span=self.smooth_len, adjust=False).mean()
        
        # === ATR ===
        df['atr'] = self._calculate_atr(df, self.atr_len)
        df['atr_base'] = df['atr'].rolling(self.atr_base_len).mean()
        
        # === 斜率 (slope_len根K线前) ===
        df['fast_prev'] = df['fast'].shift(self.slope_len)
        df['mid_prev'] = df['mid'].shift(self.slope_len)
        df['slow_prev'] = df['slow'].shift(self.slope_len)
        
        df['fast_slope'] = np.where(
            df['fast_prev'] > 0,
            (df['fast'] - df['fast_prev']) / df['fast_prev'] * 100,
            0.0
        )
        df['mid_slope'] = np.where(
            df['mid_prev'] > 0,
            (df['mid'] - df['mid_prev']) / df['mid_prev'] * 100,
            0.0
        )
        df['slow_slope'] = np.where(
            df['slow_prev'] > 0,
            (df['slow'] - df['slow_prev']) / df['slow_prev'] * 100,
            0.0
        )
        
        # === 路径效率 ===
        df['eff_net_move'] = df['close'].diff(self.eff_len).abs()
        df['eff_step_move'] = 0.0
        # 简化：用替代方法计算逐步移动
        df['close_diff'] = df['close'].diff().abs()
        df['eff_step_move'] = df['close_diff'].rolling(self.eff_len).sum()
        df['efficiency'] = np.where(
            df['eff_step_move'] > 0,
            df['eff_net_move'] / df['eff_step_move'],
            0.0
        )
        
        # === 动量持久性 ===
        df['mom_close_ago'] = df['close'].shift(self.mom_len)
        df['mom_raw'] = np.where(
            df['mom_close_ago'] > 0,
            (df['close'] - df['mom_close_ago']) / df['mom_close_ago'] * 100,
            0.0
        )
        
        # 上涨K线数量
        df['up_bar'] = (df['close'] > df['close'].shift(1)).astype(int)
        df['persist_ratio'] = df['up_bar'].rolling(self.persist_len).mean()
        
        # === 波动率机制 ===
        df['atr_regime'] = np.where(
            df['atr_base'] > 0,
            df['atr'] / df['atr_base'],
            0.0
        )
        
        # === 突破质量 ===
        df['hh'] = df['high'].rolling(self.breakout_len).max().shift(1)
        df['breakout_dist'] = df['close'] - df['hh']
        df['breakout_strength'] = np.where(
            df['atr'] > 0,
            df['breakout_dist'] / df['atr'],
            0.0
        )
        
        # === 回调/ reclaim ===
        df['pullback_low'] = df['low'].rolling(self.pullback_len).min()
        df['dist_from_fast_atr'] = np.where(
            df['atr'] > 0,
            (df['fast'] - df['pullback_low']) / df['atr'],
            0.0
        )
        
        return df
    
    def _calculate_atr(self, df: pd.DataFrame, length: int) -> pd.Series:
        """计算ATR"""
        high = df['high']
        low = df['low']
        close = df['close']
        prev_close = close.shift(1)
        
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs()
        ], axis=1).max(axis=1)
        
        return tr.rolling(length).mean()
    
    def generate_signal(self, df: pd.DataFrame, 
                       symbol: str = "BTC/USDT") -> StrategySignal:
        """
        生成交易信号
        
        Args:
            df: 已计算指标的DataFrame（或原始K线数据）
            symbol: 交易对名称
            
        Returns:
            StrategySignal: 交易信号
        """
        # 如果还没计算指标，先计算
        if 'fast' not in df.columns:
            df = self.calculate_indicators(df)
        
        self._df = df
        
        # 取最新一根K线
        if len(df) < self.slow_len + self.smooth_len + self.slope_len + self.atr_base_len:
            logger.warning("[Strategy] K线数据不足，无法计算信号")
            return StrategySignal(
                signal=SignalType.HOLD,
                symbol=symbol,
                price=float(df['close'].iloc[-1]),
                timestamp=datetime.now(),
                reason="K线数据不足"
            )
        
        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else latest
        
        close_val = float(latest['close'])
        high_val = float(latest['high'])
        
        # === 1. 趋势结构 ===
        bull_stack = (latest['fast'] > latest['mid'] and 
                     latest['mid'] > latest['slow'])
        
        sep_perc = 0.0
        if latest['slow'] != 0:
            sep_perc = abs(latest['fast'] - latest['slow']) / latest['slow'] * 100
        sep_ok = sep_perc >= self.min_sep_perc
        
        slope_ok = (latest['slow_slope'] >= self.min_slow_slope_perc and
                    latest['mid_slope'] > 0 and
                    latest['fast_slope'] > 0)
        
        # === 2. 路径效率 ===
        eff_ok = latest['efficiency'] >= self.min_eff
        
        # === 3. 动量 ===
        mom_ok = (latest['mom_raw'] > 0 and 
                  latest['persist_ratio'] >= 0.57)
        
        # === 4. 波动率机制 ===
        atr_ok = latest['atr_regime'] >= self.min_atr_regime
        
        # === 5. 突破质量 ===
        breakout_ok = (close_val > latest['hh'] and 
                      latest['breakout_strength'] >= self.min_breakout_atr)
        
        # === 6. 回调/reclaim ===
        deep_enough_pullback = latest['dist_from_fast_atr'] >= self.pullback_atr_mult
        
        reclaim_fast = (close_val > latest['fast'] and 
                       prev['close'] <= prev['fast'])
        reclaim_mid = (close_val > latest['mid'] and 
                      prev['close'] <= prev['mid'])
        reclaim_strength = 0.0
        if latest['atr'] > 0:
            reclaim_strength = (close_val - latest['fast']) / latest['atr']
        reclaim_ok = ((reclaim_fast or reclaim_mid) and 
                      reclaim_strength >= self.reclaim_atr_mult)
        
        # === 7. 金叉检测 ===
        # 使用历史数据检测金叉
        recent_bull_cross = self._check_recent_bull_cross(df, 14)
        
        # === 8. 趋势评分 ===
        trend_score = 0.0
        trend_score += 1.50 if bull_stack else 0.0
        trend_score += 0.90 if sep_ok else 0.0
        trend_score += 1.10 if slope_ok else 0.0
        trend_score += 1.00 if eff_ok else 0.0
        trend_score += 0.80 if atr_ok else 0.0
        trend_score += 1.00 if mom_ok else 0.0
        trend_score += 1.25 if breakout_ok else 0.0
        trend_score += 1.10 if reclaim_ok else 0.0
        
        # === 入场模型 ===
        trend_continuation = bull_stack and breakout_ok and slope_ok and eff_ok and mom_ok
        pullback_reentry = bull_stack and sep_ok and slope_ok and deep_enough_pullback and reclaim_ok and eff_ok
        early_trend_entry = recent_bull_cross and bull_stack and sep_ok and slope_ok and atr_ok and mom_ok
        
        # === 冷却检查 ===
        cooldown_ok = (self.state.last_exit_bar is None or
                       len(df) - self.state.last_exit_bar > self.cooldown_bars)
        
        # === 最终入场条件 ===
        position_size = self.state.position_size
        enter_long = (position_size == 0 and 
                     cooldown_ok and 
                     trend_score >= self.min_score and 
                     close_val > latest['slow'] and
                     (trend_continuation or pullback_reentry or early_trend_entry))
        
        # === 持仓管理 ===
        just_opened = (position_size > 0 and self.state.prev_pos_size == 0)
        if just_opened:
            self.state.entry_bar_index = len(df) - 1
            self.state.entry_price = close_val
        
        can_exit = (position_size > 0 and 
                   self.state.entry_bar_index is not None and
                   len(df) - 1 > self.state.entry_bar_index)
        
        # === 出场逻辑 ===
        bear_cross = self._check_bear_cross(df)
        structure_break = (close_val < latest['mid'] and 
                          latest['fast'] < latest['mid'])
        score_weak = trend_score <= self.exit_score_thresh
        momentum_failure = (latest['persist_ratio'] < 0.40 and 
                            latest['mom_raw'] < 0)
        regime_failure = (latest['atr_regime'] < 0.80 and 
                         latest['efficiency'] < 0.25)
        
        exit_long = (position_size > 0 and can_exit and 
                    (bear_cross or structure_break or score_weak or 
                     momentum_failure or regime_failure))
        
        # === 风控逻辑 ===
        hard_stop_price = None
        if position_size > 0 and self.state.entry_price:
            hard_stop_price = self.state.entry_price * (1 - self.hard_stop_perc / 100)
        
        # 追踪止损
        if position_size > 0:
            if self.state.high_since_entry is None:
                self.state.high_since_entry = high_val
            else:
                self.state.high_since_entry = max(self.state.high_since_entry, high_val)
            
            raw_trail = close_val - latest['atr'] * self.trail_atr_mult
            profit_lock = 0.0
            if self.state.high_since_entry:
                profit_lock = self.state.high_since_entry - latest['atr'] * self.profit_lock_atr_mult
            combined_trail = max(raw_trail, profit_lock)
            
            if self.state.trail_stop is None:
                self.state.trail_stop = combined_trail
            else:
                self.state.trail_stop = max(self.state.trail_stop, combined_trail)
        
        # === 生成信号 ===
        signal = SignalType.HOLD
        reason = ""
        stop_price = None
        
        if enter_long:
            signal = SignalType.BUY
            reason = (f"趋势评分:{trend_score:.2f} | "
                     f"延续:{trend_continuation} | "
                     f"回调:{pullback_reentry} | "
                     f"早期:{early_trend_entry}")
            self.state.position_size = 1.0  # 标记持仓
            
        elif exit_long:
            signal = SignalType.SELL
            reason = (f"死叉:{bear_cross} | "
                     f"结构:{structure_break} | "
                     f"分数弱:{score_weak} | "
                     f"动量败:{momentum_failure}")
            self.state.position_size = 0.0
            self._reset_state_on_exit()
            
        elif position_size > 0 and can_exit:
            # 检查止损
            if hard_stop_price and close_val <= hard_stop_price:
                signal = SignalType.SELL
                reason = f"硬止损触发 @ {hard_stop_price:.2f}"
                self.state.position_size = 0.0
                self._reset_state_on_exit()
            elif self.state.trail_stop and close_val <= self.state.trail_stop:
                signal = SignalType.SELL
                reason = f"追踪止损触发 @ {self.state.trail_stop:.2f}"
                self.state.position_size = 0.0
                self._reset_state_on_exit()
        
        # 更新prev_pos_size
        self.state.prev_pos_size = position_size
        
        # 置信度（基于趋势评分归一化到0-1）
        confidence = min(trend_score / 10.0, 1.0)
        
        return StrategySignal(
            signal=signal,
            symbol=symbol,
            price=close_val,
            timestamp=datetime.now(),
            score=trend_score,
            reason=reason,
            stop_price=hard_stop_price,
            trail_price=self.state.trail_stop,
            leverage=self.leverage,
            confidence=confidence
        )
    
    def _check_recent_bull_cross(self, df: pd.DataFrame, lookback: int) -> bool:
        """检查近期是否出现金叉"""
        if len(df) < lookback + 2:
            return False
        
        for i in range(len(df) - lookback, len(df) - 1):
            if i < 1:
                continue
            fast_now = df['fast'].iloc[i]
            fast_prev = df['fast'].iloc[i-1]
            mid_now = df['mid'].iloc[i]
            mid_prev = df['mid'].iloc[i-1]
            slow_now = df['slow'].iloc[i]
            slow_prev = df['slow'].iloc[i-1]
            
            # 金叉：fast上穿mid，或fast上穿slow，或mid上穿slow
            if ((fast_now > mid_now and fast_prev <= mid_prev) or
                (fast_now > slow_now and fast_prev <= slow_prev) or
                (mid_now > slow_now and mid_prev <= slow_prev)):
                return True
        
        return False
    
    def _check_bear_cross(self, df: pd.DataFrame) -> bool:
        """检查死叉"""
        if len(df) < 2:
            return False
        
        fast_now = df['fast'].iloc[-1]
        fast_prev = df['fast'].iloc[-2]
        mid_now = df['mid'].iloc[-1]
        mid_prev = df['mid'].iloc[-2]
        
        return ((fast_now < mid_now and fast_prev >= mid_prev) or
                (fast_now < df['slow'].iloc[-1] and fast_prev >= df['slow'].iloc[-2]))
    
    def _reset_state_on_exit(self):
        """平仓后重置状态"""
        self.state.last_exit_bar = len(self._df) - 1 if self._df is not None else None
        self.state.trail_stop = None
        self.state.high_since_entry = None
        self.state.entry_bar_index = None
        self.state.entry_price = None
    
    def get_position_info(self) -> Dict[str, Any]:
        """获取当前持仓信息"""
        return {
            'position_size': self.state.position_size,
            'entry_price': self.state.entry_price,
            'trail_stop': self.state.trail_stop,
            'high_since_entry': self.state.high_since_entry,
            'unrealized_pnl_pct': (
                (self._df['close'].iloc[-1] / self.state.entry_price - 1) * 100
                if self.state.entry_price and self._df is not None
                else 0.0
            )
        }
    
    def update_params(self, params: Dict[str, Any]):
        """动态更新策略参数（可调教）"""
        for key, value in params.items():
            if hasattr(self, key):
                setattr(self, key, value)
                logger.info("[Strategy] 参数已更新: {} = {}", key, value)
