"""
策略模块: PA + SMC + 威科夫 三位一体合约交易策略

功能: 
1. 威科夫四阶段循环判断 (吸筹/拉升/派发/下跌)
2. SMC市场结构分析 (BOS/CHoCH/MSS + Displacement)
3. PA价格行为形态识别 (Pin Bar/吞没/孕线/双K反转)
4. 多时间框架共振分析 (4H/1H/15M)
5. 信号共振评分 (6因子加权)
6. 三级分批止盈 + 追踪止损

适用: 主流币种合约 (BTC/ETH/SOL等)
时间框架: 4H (方向) + 1H (结构) + 15M (入场)
"""

import requests
import pandas as pd
import numpy as np
from typing import Dict, Any, Optional, List, Tuple, Union
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from loguru import logger
import time
import json


# ==================== 数据结构 ====================

class SignalType(Enum):
    """信号类型"""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    CLOSE = "CLOSE"


class WyckoffPhase(Enum):
    """威科夫阶段"""
    ACCUMULATION = "accumulation"  # 吸筹
    MARKUP = "markup"              # 拉升
    DISTRIBUTION = "distribution"  # 派发
    MARKDOWN = "markdown"          # 下跌
    UNKNOWN = "unknown"


class SMCStructure(Enum):
    """SMC市场结构"""
    BOS_BULLISH = "bos_bullish"    # 看涨结构突破
    BOS_BEARISH = "bos_bearish"    # 看跌结构突破
    CHOCH_BULLISH = "choch_bullish" # 看涨特征改变
    CHOCH_BEARISH = "choch_bearish" # 看跌特征改变
    MSS_BULLISH = "mss_bullish"    # 看涨结构转变
    MSS_BEARISH = "mss_bearish"    # 看跌结构转变
    NONE = "none"


class PAPattern(Enum):
    """PA形态"""
    PIN_BAR_BULLISH = "pin_bar_bullish"
    PIN_BAR_BEARISH = "pin_bar_bearish"
    ENGULFING_BULLISH = "engulfing_bullish"
    ENGULFING_BEARISH = "engulfing_bearish"
    INSIDE_BAR = "inside_bar"
    TWO_BAR_REVERSAL_BULLISH = "two_bar_reversal_bullish"
    TWO_BAR_REVERSAL_BEARISH = "two_bar_reversal_bearish"
    NONE = "none"


@dataclass
class TradeZone:
    """交易区域"""
    type: str  # 'bullish_ob' / 'bearish_ob' / 'bullish_fvg' / 'bearish_fvg' / 'bsl' / 'ssl'
    price_low: float
    price_high: float
    timestamp: datetime
    index: int
    freshness: int = 2  # 2=新鲜, 1=已测试1次, 0=已测试2次以上
    strength: float = 1.0  # 强度评分 (0-1)


@dataclass
class WyckoffSignal:
    """威科夫信号"""
    signal_type: str  # 'spring' / 'ut' / 'sos' / 'sow' / 'lps' / 'lpsy'
    price: float
    timestamp: datetime
    volume_ratio: float = 1.0  # 成交量比率
    confidence: float = 0.0    # 置信度 (0-1)


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
    take_profit_levels: List[float] = field(default_factory=list)
    take_profit_quantities: List[float] = field(default_factory=list)
    leverage: float = 1.0  # Fix: 默认1.0（与config trinity.risk.leverage对齐）
    confidence: float = 0.0
    position_size: float = 0.0  # 仓位大小（币）
    risk_percent: float = 0.02  # 风险比例 (2%)
    resonance_breakdown: Dict[str, float] = field(default_factory=dict)  # 共振评分明细
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'action': self.signal.value,
            'symbol': self.symbol,
            'price': self.price,
            'stop_loss': self.stop_price,
            'take_profit': self.take_profit_levels,
            'leverage': self.leverage,
            'confidence': self.confidence,
            'reason': self.reason,
            'score': self.score,
            'position_size': self.position_size,
            'risk_percent': self.risk_percent,
            'resonance_breakdown': self.resonance_breakdown,
            'timestamp': self.timestamp.isoformat()
        }


@dataclass
class TrinityAnalysis:
    """三位一体分析结果"""
    symbol: str
    wyckoff_phase: WyckoffPhase
    smc_structure: SMCStructure
    pa_pattern: PAPattern
    trade_zones: List[TradeZone]
    liquidity_levels: Dict[str, float]  # bsl, ssl
    current_price: float
    volume_profile: Dict[str, float]  # volume_avg, volume_ratio
    timestamp: datetime
    score: float = 0.0
    direction: str = "neutral"  # bullish / bearish / neutral


# ==================== PA 分析器 ====================

class PriceActionAnalyzer:
    """PA价格行为分析器"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.pin_bar_ratio = config.get('pin_bar_ratio', 2.0)
        self.engulfing_ratio = config.get('engulfing_ratio', 1.5)
        self.structure_lookback = config.get('structure_lookback', 50)
    
    def detect_market_structure(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        检测市场结构 (HH/HL, LH/LL)
        
        Returns:
            {
                'trend': 'bullish' / 'bearish' / 'neutral',
                'hh': [...],
                'hl': [...],
                'lh': [...],
                'll': [...]
            }
        """
        if len(df) < 20:
            return {'trend': 'neutral', 'hh': [], 'hl': [], 'lh': [], 'll': []}
        
        highs = df['high'].values
        lows = df['low'].values
        
        # 寻找摆动点
        hh, hl, lh, ll = [], [], [], []
        
        for i in range(2, len(df) - 2):
            # Higher High
            if (highs[i] > highs[i-1] and highs[i] > highs[i-2] and 
                highs[i] > highs[i+1] and highs[i] > highs[i+2]):
                hh.append({'index': i, 'price': highs[i], 'timestamp': df['timestamp'].iloc[i]})
            
            # Higher Low  
            if (lows[i] > lows[i-1] and lows[i] > lows[i-2] and
                lows[i] > lows[i+1] and lows[i] > lows[i+2]):
                hl.append({'index': i, 'price': lows[i], 'timestamp': df['timestamp'].iloc[i]})
            
            # Lower High
            if (highs[i] < highs[i-1] and highs[i] < highs[i-2] and
                highs[i] < highs[i+1] and highs[i] < highs[i+2]):
                lh.append({'index': i, 'price': highs[i], 'timestamp': df['timestamp'].iloc[i]})
            
            # Lower Low
            if (lows[i] < lows[i-1] and lows[i] < lows[i-2] and
                lows[i] < lows[i+1] and lows[i] < lows[i+2]):
                ll.append({'index': i, 'price': lows[i], 'timestamp': df['timestamp'].iloc[i]})
        
        # 判断趋势
        if len(hh) >= 2 and len(hl) >= 2:
            trend = 'bullish'
        elif len(lh) >= 2 and len(ll) >= 2:
            trend = 'bearish'
        else:
            trend = 'neutral'
        
        return {
            'trend': trend,
            'hh': hh[-5:],  # 最近5个
            'hl': hl[-5:],
            'lh': lh[-5:],
            'll': ll[-5:]
        }
    
    def detect_pin_bar(self, df: pd.DataFrame, index: int) -> Optional[PAPattern]:
        """检测Pin Bar形态"""
        if index < 0 or index >= len(df):
            return None
        
        candle = df.iloc[index]
        open_price = candle['open']
        close_price = candle['close']
        high = candle['high']
        low = candle['low']
        
        body = abs(close_price - open_price)
        upper_shadow = high - max(open_price, close_price)
        lower_shadow = min(open_price, close_price) - low
        
        # 看涨Pin Bar (锤子线)
        if lower_shadow > body * self.pin_bar_ratio and upper_shadow < body * 0.5:
            return PAPattern.PIN_BAR_BULLISH
        
        # 看跌Pin Bar (倒锤子线)
        if upper_shadow > body * self.pin_bar_ratio and lower_shadow < body * 0.5:
            return PAPattern.PIN_BAR_BEARISH
        
        return None
    
    def detect_engulfing(self, df: pd.DataFrame, index: int) -> Optional[PAPattern]:
        """检测吞没形态"""
        if index < 1 or index >= len(df):
            return None
        
        prev_candle = df.iloc[index-1]
        curr_candle = df.iloc[index]
        
        prev_body = abs(prev_candle['close'] - prev_candle['open'])
        curr_body = abs(curr_candle['close'] - curr_candle['open'])
        
        # 看涨吞没
        if (prev_candle['close'] < prev_candle['open'] and  # 前一根阴线
            curr_candle['close'] > curr_candle['open'] and  # 当前阳线
            curr_candle['close'] > prev_candle['open'] and  # 阳线收盘 > 阴线开盘
            curr_candle['open'] < prev_candle['close'] and  # 阳线开盘 < 阴线收盘
            curr_body > prev_body * self.engulfing_ratio):  # 吞没比例达标
            return PAPattern.ENGULFING_BULLISH
        
        # 看跌吞没
        if (prev_candle['close'] > prev_candle['open'] and  # 前一根阳线
            curr_candle['close'] < curr_candle['open'] and  # 当前阴线
            curr_candle['close'] < prev_candle['open'] and  # 阴线收盘 < 阳线开盘
            curr_candle['open'] > prev_candle['close'] and  # 阴线开盘 > 阳线收盘
            curr_body > prev_body * self.engulfing_ratio):  # 吞没比例达标
            return PAPattern.ENGULFING_BEARISH
        
        return None
    
    def detect_inside_bar(self, df: pd.DataFrame, index: int) -> Optional[PAPattern]:
        """检测孕线形态"""
        if index < 1 or index >= len(df):
            return None
        
        prev_candle = df.iloc[index-1]
        curr_candle = df.iloc[index]
        
        # 当前K线完全被前一根K线包含
        if (curr_candle['high'] <= prev_candle['high'] and
            curr_candle['low'] >= prev_candle['low']):
            return PAPattern.INSIDE_BAR
        
        return None
    
    def detect_two_bar_reversal(self, df: pd.DataFrame, index: int) -> Optional[PAPattern]:
        """检测双K线反转"""
        if index < 1 or index >= len(df):
            return None
        
        prev_candle = df.iloc[index-1]
        curr_candle = df.iloc[index]
        
        # 看涨双K反转 (阴线创新低后阳线收盘高于阴线开盘)
        if (prev_candle['close'] < prev_candle['open'] and  # 阴线
            curr_candle['close'] > curr_candle['open'] and  # 阳线
            prev_candle['low'] < df['low'].iloc[max(0, index-10):index-1].min() and  # 创新低
            curr_candle['close'] > prev_candle['open']):  # 收盘高于阴线开盘
            return PAPattern.TWO_BAR_REVERSAL_BULLISH
        
        # 看跌双K反转 (阳线创新高后阴线收盘低于阳线开盘)
        if (prev_candle['close'] > prev_candle['open'] and  # 阳线
            curr_candle['close'] < curr_candle['open'] and  # 阴线
            prev_candle['high'] > df['high'].iloc[max(0, index-10):index-1].max() and  # 创新高
            curr_candle['close'] < prev_candle['open']):  # 收盘低于阳线开盘
            return PAPattern.TWO_BAR_REVERSAL_BEARISH
        
        return None
    
    def detect_all_patterns(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """检测所有PA形态"""
        patterns = []
        
        for i in range(1, len(df)):
            # 按优先级检测
            pattern = None
            pattern_type = None
            
            # 1. 吞没形态
            pattern = self.detect_engulfing(df, i)
            if pattern:
                pattern_type = "engulfing"
            
            # 2. Pin Bar
            if not pattern:
                pattern = self.detect_pin_bar(df, i)
                if pattern:
                    pattern_type = "pin_bar"
            
            # 3. 双K反转
            if not pattern:
                pattern = self.detect_two_bar_reversal(df, i)
                if pattern:
                    pattern_type = "two_bar_reversal"
            
            # 4. 孕线
            if not pattern:
                pattern = self.detect_inside_bar(df, i)
                if pattern:
                    pattern_type = "inside_bar"
            
            if pattern:
                patterns.append({
                    'pattern': pattern,
                    'type': pattern_type,
                    'index': i,
                    'price': df['close'].iloc[i],
                    'timestamp': df['timestamp'].iloc[i]
                })
        
        return patterns


# ==================== SMC 分析器 ====================

class SMCAnalyzer:
    """SMC聪明钱概念分析器"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.ob_lookback = config.get('ob_lookback', 200)
        self.fvg_lookback = config.get('fvg_lookback', 200)
        self.displacement_mult = config.get('displacement_mult', 1.5)
        self.liquidity_buffer = config.get('liquidity_buffer', 0.005)
    
    def detect_bos_choch(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        检测BOS/CHoCH/MSS
        
        Returns:
            {
                'structure': SMCStructure,
                'displacement': bool,
                'break_index': int,
                'break_price': float
            }
        """
        if len(df) < 50:
            return {'structure': SMCStructure.NONE, 'displacement': False}
        
        highs = df['high'].values
        lows = df['low'].values
        
        # 寻找最近的结构点
        lookback = min(30, len(df) // 2)
        
        # 检查BOS (结构突破)
        for i in range(lookback, len(df) - 5):
            # 看涨BOS: 突破前高
            if highs[i] > max(highs[i-lookback:i]):
                # 检查是否有Displacement (大实体K线)
                if self._has_displacement(df, i, 'bullish'):
                    return {
                        'structure': SMCStructure.BOS_BULLISH,
                        'displacement': True,
                        'break_index': i,
                        'break_price': highs[i]
                    }
            
            # 看跌BOS: 跌破前低
            if lows[i] < min(lows[i-lookback:i]):
                if self._has_displacement(df, i, 'bearish'):
                    return {
                        'structure': SMCStructure.BOS_BEARISH,
                        'displacement': True,
                        'break_index': i,
                        'break_price': lows[i]
                    }
        
        # 检查CHoCH (特征改变)
        for i in range(lookback, len(df) - 5):
            # 看涨CHoCH: 突破前一个LH
            if i > 1 and highs[i] > highs[i-2]:
                if self._has_displacement(df, i, 'bullish'):
                    return {
                        'structure': SMCStructure.CHOCH_BULLISH,
                        'displacement': True,
                        'break_index': i,
                        'break_price': highs[i]
                    }
            
            # 看跌CHoCH: 跌破前一个HL
            if i > 1 and lows[i] < lows[i-2]:
                if self._has_displacement(df, i, 'bearish'):
                    return {
                        'structure': SMCStructure.CHOCH_BEARISH,
                        'displacement': True,
                        'break_index': i,
                        'break_price': lows[i]
                    }
        
        return {'structure': SMCStructure.NONE, 'displacement': False}
    
    def _has_displacement(self, df: pd.DataFrame, index: int, direction: str) -> bool:
        """检查是否有Displacement (大实体K线)"""
        if index < 0 or index >= len(df):
            return False
        
        candle = df.iloc[index]
        body = abs(candle['close'] - candle['open'])
        avg_body = df['close'].rolling(20).apply(lambda x: abs(x.diff()).mean(), raw=False).iloc[index]
        
        if direction == 'bullish':
            return body > avg_body * self.displacement_mult and candle['close'] > candle['open']
        else:  # bearish
            return body > avg_body * self.displacement_mult and candle['close'] < candle['open']
    
    def detect_liquidity_levels(self, df: pd.DataFrame) -> Dict[str, float]:
        """
        检测流动性水平 (BSL/SSL)
        
        Returns:
            {'bsl': float, 'ssl': float}
        """
        if len(df) < 20:
            return {'bsl': 0, 'ssl': 0}
        
        # 简单实现: 最近20根K线的最高点和最低点
        recent = df.tail(20)
        bsl = recent['high'].max() * (1 + self.liquidity_buffer)  # 买方流动性 (上方)
        ssl = recent['low'].min() * (1 - self.liquidity_buffer)   # 卖方流动性 (下方)
        
        return {'bsl': bsl, 'ssl': ssl}
    
    def detect_liquidity_hunt(self, df: pd.DataFrame, liquidity_levels: Dict[str, float]) -> bool:
        """
        检测流动性猎杀
        
        Returns:
            True/False
        """
        if len(df) < 5:
            return False
        
        recent = df.tail(5)
        current_price = df['close'].iloc[-1]
        
        # 检查是否短暂突破流动性水平后收回
        for i in range(len(recent)):
            candle = recent.iloc[i]
            
            # 检查买方流动性猎杀 (短暂突破BSL后跌回)
            if candle['high'] > liquidity_levels['bsl'] and candle['close'] < liquidity_levels['bsl']:
                logger.info(f"[SMC] 买方流动性猎杀检测到: {candle['high']:.2f} > BSL {liquidity_levels['bsl']:.2f}")
                return True
            
            # 检查卖方流动性猎杀 (短暂跌破SSL后涨回)
            if candle['low'] < liquidity_levels['ssl'] and candle['close'] > liquidity_levels['ssl']:
                logger.info(f"[SMC] 卖方流动性猎杀检测到: {candle['low']:.2f} < SSL {liquidity_levels['ssl']:.2f}")
                return True
        
        return False
    
    def detect_order_blocks(self, df: pd.DataFrame) -> List[TradeZone]:
        """
        检测Order Block (增强版)
        
        Bullish OB: 下跌阴线 + 后面阳线突破 + 流动性猎杀
        Bearish OB: 上涨阳线 + 后面阴线跌破 + 流动性猎杀
        """
        obs = []
        
        for i in range(2, len(df)):
            # Bullish OB: 下跌阴线 + 后面阳线突破 + 检查流动性猎杀
            if (df['close'].iloc[i-2] < df['open'].iloc[i-2] and  # 阴线
                df['close'].iloc[i-1] > df['open'].iloc[i-1] and  # 阳线
                df['high'].iloc[i-1] > df['high'].iloc[i-2]):     # 突破
                
                # 检查前一根K线是否有流动性猎杀 (扫低)
                if i >= 4 and df['low'].iloc[i-3] < df['low'].iloc[i-4:i-2].min():
                    ob = TradeZone(
                        type='bullish_ob',
                        price_low=df['low'].iloc[i-2],
                        price_high=df['open'].iloc[i-2],
                        timestamp=df['timestamp'].iloc[i-2],
                        index=i-2,
                        freshness=2,
                        strength=1.0
                    )
                    obs.append(ob)
            
            # Bearish OB: 上涨阳线 + 后面阴线跌破 + 检查流动性猎杀
            if (df['close'].iloc[i-2] > df['open'].iloc[i-2] and  # 阳线
                df['close'].iloc[i-1] < df['open'].iloc[i-1] and  # 阴线
                df['low'].iloc[i-1] < df['low'].iloc[i-2]):       # 跌破
                
                # 检查前一根K线是否有流动性猎杀 (扫高)
                if i >= 4 and df['high'].iloc[i-3] > df['high'].iloc[i-4:i-2].max():
                    ob = TradeZone(
                        type='bearish_ob',
                        price_high=df['high'].iloc[i-2],
                        price_low=df['open'].iloc[i-2],
                        timestamp=df['timestamp'].iloc[i-2],
                        index=i-2,
                        freshness=2,
                        strength=1.0
                    )
                    obs.append(ob)
        
        return obs
    
    def detect_fair_value_gaps(self, df: pd.DataFrame) -> List[TradeZone]:
        """
        检测Fair Value Gap (增强版)
        
        Bullish FVG: K线1低点 > K线3高点
        Bearish FVG: K线1高点 < K线3低点
        """
        fvgs = []
        
        for i in range(2, len(df)):
            # Bullish FVG: K线1低点 > K线3高点
            if df['low'].iloc[i-2] > df['high'].iloc[i]:
                fvg = TradeZone(
                    type='bullish_fvg',
                    price_low=df['high'].iloc[i],
                    price_high=df['low'].iloc[i-2],
                    timestamp=df['timestamp'].iloc[i],
                    index=i,
                    freshness=2,
                    strength=0.8  # FVG强度略低于OB
                )
                fvgs.append(fvg)
            
            # Bearish FVG: K线1高点 < K线3低点
            if df['high'].iloc[i-2] < df['low'].iloc[i]:
                fvg = TradeZone(
                    type='bearish_fvg',
                    price_high=df['low'].iloc[i],
                    price_low=df['high'].iloc[i-2],
                    timestamp=df['timestamp'].iloc[i],
                    index=i,
                    freshness=2,
                    strength=0.8
                )
                fvgs.append(fvg)
        
        return fvgs


# ==================== 威科夫分析器 ====================

class WyckoffAnalyzer:
    """威科夫操盘法分析器"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.range_min_bars = config.get('range_min_bars', 20)
        self.spring_wick_ratio = config.get('spring_wick_ratio', 0.6)
        self.ut_wick_ratio = config.get('ut_wick_ratio', 0.6)
        self.volume_spike_mult = config.get('volume_spike_mult', 2.0)
    
    def detect_phase(self, df: pd.DataFrame) -> WyckoffPhase:
        """
        判断威科夫阶段
        
        Returns:
            WyckoffPhase
        """
        if len(df) < 50:
            return WyckoffPhase.UNKNOWN
        
        # 1. 寻找盘整区间
        range_high = df['high'].rolling(20).max().iloc[-1]
        range_low = df['low'].rolling(20).min().iloc[-1]
        range_height = range_high - range_low
        
        if range_height == 0:
            return WyckoffPhase.UNKNOWN
        
        current_price = df['close'].iloc[-1]
        position_in_range = (current_price - range_low) / range_height
        
        # 2. 成交量分析
        volume_avg = df['volume'].rolling(20).mean().iloc[-1]
        volume_current = df['volume'].iloc[-1]
        volume_ratio = volume_current / volume_avg if volume_avg > 0 else 1.0
        
        # 3. 阶段判断逻辑
        # 吸筹: 价格在区间下半部，下跌时缩量，上涨时放量
        if position_in_range < 0.5:
            # 检查是否有Spring信号
            if self.detect_spring(df) is not None:
                return WyckoffPhase.ACCUMULATION
            # 检查成交量特征
            down_volume = df[df['close'] < df['open']]['volume'].tail(5).mean()
            up_volume = df[df['close'] > df['open']]['volume'].tail(5).mean()
            if up_volume > down_volume * 1.2:  # 上涨放量 > 下跌放量
                return WyckoffPhase.ACCUMULATION
        
        # 拉升: 价格突破区间上沿，放量
        elif position_in_range > 0.8 and volume_ratio > 1.5:
            if self.detect_sos(df) is not None:
                return WyckoffPhase.MARKUP
            return WyckoffPhase.MARKUP
        
        # 派发: 价格在区间上半部，上涨时缩量，下跌时放量
        elif position_in_range > 0.5:
            # 检查是否有UT信号
            if self.detect_upthrust(df) is not None:
                return WyckoffPhase.DISTRIBUTION
            # 检查成交量特征
            up_volume = df[df['close'] > df['open']]['volume'].tail(5).mean()
            down_volume = df[df['close'] < df['open']]['volume'].tail(5).mean()
            if down_volume > up_volume * 1.2:  # 下跌放量 > 上涨放量
                return WyckoffPhase.DISTRIBUTION
        
        # 下跌: 价格跌破区间下沿，放量
        elif position_in_range < 0.2 and volume_ratio > 1.5:
            if self.detect_sow(df) is not None:
                return WyckoffPhase.MARKDOWN
            return WyckoffPhase.MARKDOWN
        
        return WyckoffPhase.UNKNOWN
    
    def detect_spring(self, df: pd.DataFrame) -> Optional[WyckoffSignal]:
        """检测Spring (弹簧效应)"""
        if len(df) < 10:
            return None
        
        # 寻找支撑位
        support = df['low'].rolling(20).min().iloc[-2]
        
        for i in range(max(0, len(df)-5), len(df)):
            candle = df.iloc[i]
            
            # Spring特征: 价格跌破支撑但快速收回
            if (candle['low'] < support * 0.99 and  # 跌破支撑
                candle['close'] > support and       # 收盘在支撑上方
                candle['close'] > candle['open']):  # 阳线
                
                # 检查下影线比例
                lower_wick = min(candle['open'], candle['close']) - candle['low']
                body = abs(candle['close'] - candle['open'])
                
                if lower_wick > body * self.spring_wick_ratio:
                    # 检查成交量
                    volume_avg = df['volume'].rolling(20).mean().iloc[i]
                    volume_ratio = candle['volume'] / volume_avg if volume_avg > 0 else 1.0
                    
                    return WyckoffSignal(
                        signal_type='spring',
                        price=candle['close'],
                        timestamp=candle['timestamp'],
                        volume_ratio=volume_ratio,
                        confidence=min(volume_ratio / 2, 1.0)
                    )
        
        return None
    
    def detect_upthrust(self, df: pd.DataFrame) -> Optional[WyckoffSignal]:
        """检测Upthrust (上冲回落)"""
        if len(df) < 10:
            return None
        
        # 寻找阻力位
        resistance = df['high'].rolling(20).max().iloc[-2]
        
        for i in range(max(0, len(df)-5), len(df)):
            candle = df.iloc[i]
            
            # UT特征: 价格突破阻力但快速跌回
            if (candle['high'] > resistance * 1.01 and  # 突破阻力
                candle['close'] < resistance and       # 收盘在阻力下方
                candle['close'] < candle['open']):     # 阴线
                
                # 检查上影线比例
                upper_wick = candle['high'] - max(candle['open'], candle['close'])
                body = abs(candle['close'] - candle['open'])
                
                if upper_wick > body * self.ut_wick_ratio:
                    # 检查成交量
                    volume_avg = df['volume'].rolling(20).mean().iloc[i]
                    volume_ratio = candle['volume'] / volume_avg if volume_avg > 0 else 1.0
                    
                    return WyckoffSignal(
                        signal_type='ut',
                        price=candle['close'],
                        timestamp=candle['timestamp'],
                        volume_ratio=volume_ratio,
                        confidence=min(volume_ratio / 2, 1.0)
                    )
        
        return None
    
    def detect_sos(self, df: pd.DataFrame) -> Optional[WyckoffSignal]:
        """检测SOS (强势信号)"""
        if len(df) < 10:
            return None
        
        # 寻找盘整区间上沿
        range_high = df['high'].rolling(20).max().iloc[-2]
        
        for i in range(max(0, len(df)-3), len(df)):
            candle = df.iloc[i]
            
            # SOS特征: 放量突破区间上沿
            if (candle['close'] > range_high and  # 突破上沿
                candle['close'] > candle['open']):  # 阳线
                
                # 检查成交量
                volume_avg = df['volume'].rolling(20).mean().iloc[i]
                volume_ratio = candle['volume'] / volume_avg if volume_avg > 0 else 1.0
                
                if volume_ratio > self.volume_spike_mult:
                    return WyckoffSignal(
                        signal_type='sos',
                        price=candle['close'],
                        timestamp=candle['timestamp'],
                        volume_ratio=volume_ratio,
                        confidence=min(volume_ratio / 3, 1.0)
                    )
        
        return None
    
    def detect_sow(self, df: pd.DataFrame) -> Optional[WyckoffSignal]:
        """检测SOW (弱势信号)"""
        if len(df) < 10:
            return None
        
        # 寻找盘整区间下沿
        range_low = df['low'].rolling(20).min().iloc[-2]
        
        for i in range(max(0, len(df)-3), len(df)):
            candle = df.iloc[i]
            
            # SOW特征: 放量跌破区间下沿
            if (candle['close'] < range_low and  # 跌破下沿
                candle['close'] < candle['open']):  # 阴线
                
                # 检查成交量
                volume_avg = df['volume'].rolling(20).mean().iloc[i]
                volume_ratio = candle['volume'] / volume_avg if volume_avg > 0 else 1.0
                
                if volume_ratio > self.volume_spike_mult:
                    return WyckoffSignal(
                        signal_type='sow',
                        price=candle['close'],
                        timestamp=candle['timestamp'],
                        volume_ratio=volume_ratio,
                        confidence=min(volume_ratio / 3, 1.0)
                    )
        
        return None
    
    # [Dead code removed: detect_lps, detect_lpsy - 70 lines]


# ==================== 信号共振评分器 ====================

class ResonanceScorer:
    """信号共振评分器"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.weights = {
            'wyckoff_phase': config.get('wyckoff_phase_weight', 3),
            'smc_structure': config.get('smc_structure_weight', 2),
            'liquidity_hunt': config.get('liquidity_hunt_weight', 3),
            'pa_pattern': config.get('pa_pattern_weight', 2),
            'ob_fvg_support': config.get('ob_fvg_support_weight', 1),
            'higher_tf_align': config.get('higher_tf_align_weight', 2)
        }
        self.min_score = config.get('min_score_to_trade', 7)
    
    def score(self, analysis: TrinityAnalysis, higher_tf_direction: str) -> Tuple[float, Dict[str, float]]:
        """
        计算信号共振评分
        
        Returns:
            (总分, 评分明细)
        """
        scores = {}
        total = 0
        
        # 1. 威科夫阶段 (0-3分)
        if analysis.wyckoff_phase in [WyckoffPhase.ACCUMULATION, WyckoffPhase.MARKUP]:
            wyckoff_score = 3 if analysis.direction == 'bullish' else 0
        elif analysis.wyckoff_phase in [WyckoffPhase.DISTRIBUTION, WyckoffPhase.MARKDOWN]:
            wyckoff_score = 3 if analysis.direction == 'bearish' else 0
        else:
            wyckoff_score = 0
        scores['wyckoff_phase'] = wyckoff_score
        total += wyckoff_score * self.weights['wyckoff_phase']
        
        # 2. SMC结构 (0-2分)
        if (analysis.direction == 'bullish' and 
            analysis.smc_structure in [SMCStructure.BOS_BULLISH, SMCStructure.CHOCH_BULLISH, SMCStructure.MSS_BULLISH]):
            smc_score = 2
        elif (analysis.direction == 'bearish' and 
              analysis.smc_structure in [SMCStructure.BOS_BEARISH, SMCStructure.CHOCH_BEARISH, SMCStructure.MSS_BEARISH]):
            smc_score = 2
        else:
            smc_score = 0
        scores['smc_structure'] = smc_score
        total += smc_score * self.weights['smc_structure']
        
        # 3. 流动性猎杀 (0-3分) - 简化: 检查是否有OB/FVG
        liquidity_score = 0
        for zone in analysis.trade_zones:
            if (analysis.direction == 'bullish' and 'bullish' in zone.type) or \
               (analysis.direction == 'bearish' and 'bearish' in zone.type):
                liquidity_score = 3
                break
        scores['liquidity_hunt'] = liquidity_score
        total += liquidity_score * self.weights['liquidity_hunt']
        
        # 4. PA形态 (0-2分)
        if (analysis.direction == 'bullish' and 
            analysis.pa_pattern in [PAPattern.PIN_BAR_BULLISH, PAPattern.ENGULFING_BULLISH, 
                                   PAPattern.TWO_BAR_REVERSAL_BULLISH]):
            pa_score = 2
        elif (analysis.direction == 'bearish' and 
              analysis.pa_pattern in [PAPattern.PIN_BAR_BEARISH, PAPattern.ENGULFING_BEARISH,
                                     PAPattern.TWO_BAR_REVERSAL_BEARISH]):
            pa_score = 2
        elif analysis.pa_pattern == PAPattern.INSIDE_BAR:
            pa_score = 1
        else:
            pa_score = 0
        scores['pa_pattern'] = pa_score
        total += pa_score * self.weights['pa_pattern']
        
        # 5. OB/FVG支撑 (0-1分)
        ob_fvg_score = 1 if analysis.trade_zones else 0
        scores['ob_fvg_support'] = ob_fvg_score
        total += ob_fvg_score * self.weights['ob_fvg_support']
        
        # 6. 大周期一致 (0-2分)
        higher_tf_score = 2 if analysis.direction == higher_tf_direction else 0
        scores['higher_tf_align'] = higher_tf_score
        total += higher_tf_score * self.weights['higher_tf_align']
        
        return total, scores
    
    def is_tradeable(self, score: float) -> bool:
        """是否可交易"""
        return score >= self.min_score


# ==================== 主策略类 ====================

class TrinityStrategy:
    """
    PA + SMC + 威科夫 三位一体策略
    
    使用方式:
    1. 初始化: strategy = TrinityStrategy(params)
    2. 生成信号: signal = strategy.generate_signal(df, symbol='BTC/USDT')
    """
    
    def __init__(self, params: Optional[Dict[str, Any]] = None):
        params = params or {}
        
        # === 时间框架配置 ===
        self.trend_tf = params.get('trend_tf', '4h')
        self.signal_tf = params.get('signal_tf', '1h')
        self.entry_tf = params.get('entry_tf', '15m')
        
        # === 模块初始化 ===
        pa_config = params.get('pa', {})
        smc_config = params.get('smc', {})
        wyckoff_config = params.get('wyckoff', {})
        scoring_config = params.get('scoring', {})
        risk_config = params.get('risk', {})
        tp_config = params.get('take_profit', {})
        
        self.pa_analyzer = PriceActionAnalyzer(pa_config)
        self.smc_analyzer = SMCAnalyzer(smc_config)
        self.wyckoff_analyzer = WyckoffAnalyzer(wyckoff_config)
        self.scorer = ResonanceScorer(scoring_config)
        
        # === 风险管理参数 ===
        self.leverage = risk_config.get('leverage', 1.0)  # Fix: 默认1.0（与config对齐）
        self.max_risk_per_trade = risk_config.get('max_risk_per_trade', 0.02)
        self.stop_loss_buffer = risk_config.get('stop_loss_buffer', 0.005)
        # Fix: 仓位上限 — 从risk config读取max_single_order限制
        self.max_single_order_usdt = risk_config.get('max_single_order_usdt', 10.0)
        
        # === 止盈策略参数 ===
        self.tp_tier1_pct = tp_config.get('tier1_pct', 0.50)
        self.tp_tier2_pct = tp_config.get('tier2_pct', 0.30)
        self.tp_tier3_pct = tp_config.get('tier3_pct', 0.20)
        self.tp1_rr_ratio = tp_config.get('tp1_rr_ratio', 2.0)
        self.tp2_rr_ratio = tp_config.get('tp2_rr_ratio', 3.0)
        self.tp3_rr_ratio = tp_config.get('tp3_rr_ratio', 4.0)
        self.min_rr_ratio = tp_config.get('min_rr_ratio', 2.0)
        self.trailing_enabled = tp_config.get('trailing_enabled', True)
        
        # === 15M 入场确认 ===
        # 默认关闭 15M PA 形态硬确认：只要价格回到 1H 交易区域且盈亏比满足即可入场。
        # 如需恢复旧逻辑，可在 config/system.yaml 的 trinity 下设置 require_15m_pa_confirmation: true。
        self.require_15m_pa_confirmation = params.get('require_15m_pa_confirmation', False)
        
        # === 状态 ===
        self.analyses = {}
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        # 代理配置（从环境变量或直接配置）
        proxy_url = params.get('proxy', '')
        if proxy_url:
            self.session.proxies = {'http': proxy_url, 'https': proxy_url}
            logger.info(f"[Trinity] 代理已配置: {proxy_url}")
        
        logger.info("[Trinity] 策略初始化完成 | "
                   f"杠杆:{self.leverage}x | "
                   f"最小盈亏比:{self.min_rr_ratio}:1 | "
                   f"最小共振分:{self.scorer.min_score}")
    
    def fetch_binance_klines(self, symbol: str, interval: str, limit: int = 500) -> pd.DataFrame:
        """
        获取Binance K线数据（带重试）
        
        Args:
            symbol: 交易对 (如 'BTCUSDT')
            interval: 时间框架 ('4h', '1h', '15m')
            limit: K线数量
            
        Returns:
            DataFrame with columns: ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        """
        # 合约交易系统必须使用 Binance USD-M futures K线接口。
        # 旧接口 api/v3/klines 是现货K线，部分合约符号（如 MUUSDT）在现货不存在，
        # 会导致 Trinity 内部 1H/15M 分析 HTTP 400，虽然 market_data 的 futures 路径正常。
        url = "https://fapi.binance.com/fapi/v1/klines"
        params = {
            'symbol': symbol,
            'interval': interval,
            'limit': limit
        }
        
        max_retries = 2
        last_error = None
        
        for attempt in range(max_retries + 1):
            try:
                response = self.session.get(url, params=params, timeout=15)
                
                # 检查HTTP状态码
                if response.status_code != 200:
                    logger.warning(f"[Trinity] K线HTTP {response.status_code} {symbol} {interval}")
                    if response.status_code == 429:  # 限流
                        time.sleep(5 * (attempt + 1))
                        continue
                    if attempt < max_retries:
                        time.sleep(2 ** attempt)
                        continue
                    return pd.DataFrame()
                
                data = response.json()
                
                # Binance 错误响应（非list格式）
                if not isinstance(data, list):
                    logger.warning(f"[Trinity] K线API错误 {symbol} {interval}: {data}")
                    return pd.DataFrame()
                
                if len(data) == 0:
                    logger.debug(f"[Trinity] K线无数据 {symbol} {interval}")
                    return pd.DataFrame()
                
                # 转为DataFrame
                df = pd.DataFrame(data, columns=[
                    'timestamp', 'open', 'high', 'low', 'close', 'volume',
                    'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                    'taker_buy_quote', 'ignore'
                ])
                
                # 数据类型转换
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    df[col] = df[col].astype(float)
                
                return df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
                
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    logger.warning(f"[Trinity] K线失败 {symbol} {interval} (重试{attempt+1}/{max_retries}): {e}")
                    time.sleep(2 ** attempt)
                else:
                    logger.error(f"[Trinity] 获取K线失败 {symbol} {interval}: {e}")
        
        return pd.DataFrame()
    
    def analyze_4h(self, symbol: str, df=None) -> Dict[str, Any]:
        """
        4小时分析 (定方向)
        
        Returns:
            {
                'direction': 'bullish' / 'bearish' / 'neutral',
                'wyckoff_phase': WyckoffPhase,
                'pa_structure': {...},
                'current_price': float
            }
        """
        logger.info(f"[Trinity] 4小时分析: {symbol}")
        
        # 获取数据: 优先使用预取df
        if df is None or (hasattr(df, 'empty') and df.empty) or len(df) < 40:
            df = self.fetch_binance_klines(symbol, self.trend_tf, limit=500)
        
        if df.empty or len(df) < 40:
            logger.warning(f"[Trinity] {symbol} 4H数据不足")
            return {'direction': 'neutral', 'wyckoff_phase': WyckoffPhase.UNKNOWN,
                    'pa_structure': {'trend': 'neutral'}, 'current_price': 0, 'df': pd.DataFrame()}
        
        # 2. PA市场结构分析
        pa_structure = self.pa_analyzer.detect_market_structure(df)
        
        # 3. 威科夫阶段判断
        wyckoff_phase = self.wyckoff_analyzer.detect_phase(df)
        
        # 4. 确定方向
        direction = 'neutral'
        if wyckoff_phase in [WyckoffPhase.ACCUMULATION, WyckoffPhase.MARKUP]:
            direction = 'bullish'
        elif wyckoff_phase in [WyckoffPhase.DISTRIBUTION, WyckoffPhase.MARKDOWN]:
            direction = 'bearish'
        elif pa_structure['trend'] != 'neutral':
            direction = pa_structure['trend']
        
        logger.info(f"[Trinity]   ✅ 方向: {direction}")
        logger.info(f"[Trinity]   ✅ 威科夫阶段: {wyckoff_phase.value}")
        logger.info(f"[Trinity]   ✅ PA结构: {pa_structure['trend']}")
        
        return {
            'direction': direction,
            'wyckoff_phase': wyckoff_phase,
            'pa_structure': pa_structure,
            'current_price': df['close'].iloc[-1],
            'df': df
        }
    
    def analyze_1h(self, symbol: str, direction: str, df: pd.DataFrame = None) -> Dict[str, Any]:
        """
        1小时分析 (找结构)
        
        Args:
            symbol: 交易对 (如 'BTCUSDT')
            direction: 4H方向 ('bullish'/'bearish'/'neutral')
            df: 预取的1H K线数据 (Fix #4 Round4: 避免重复API调用)
        
        Returns:
            {
                'smc_structure': SMCStructure,
                'liquidity_levels': {...},
                'liquidity_hunt': bool,
                'trade_zones': [...],
                'current_price': float
            }
        """
        logger.info(f"[Trinity] 1小时分析: {symbol} (方向: {direction})")
        
        # 1. 获取数据: 优先使用预取df
        if df is None or (hasattr(df, 'empty') and df.empty) or len(df) < 40:
            df = self.fetch_binance_klines(symbol, self.signal_tf, limit=500)
        
        if df.empty or len(df) < 40:
            logger.warning(f"[Trinity] {symbol} 1H数据不足")
            return {'smc_structure': SMCStructure.NONE, 'liquidity_levels': {},
                    'liquidity_hunt': False, 'trade_zones': [], 'current_price': 0, 'df': pd.DataFrame()}
        
        # 2. SMC结构分析
        smc_result = self.smc_analyzer.detect_bos_choch(df)
        
        # 3. 流动性水平
        liquidity_levels = self.smc_analyzer.detect_liquidity_levels(df)
        
        # 4. 流动性猎杀检测
        liquidity_hunt = self.smc_analyzer.detect_liquidity_hunt(df, liquidity_levels)
        
        # 5. 检测交易区域 (OB/FVG)
        obs = self.smc_analyzer.detect_order_blocks(df)
        fvgs = self.smc_analyzer.detect_fair_value_gaps(df)
        
        # 6. 过滤: 只保留符合方向的区域
        trade_zones = []
        if direction == 'bullish':
            trade_zones = [ob for ob in obs if 'bullish' in ob.type]
            trade_zones += [fvg for fvg in fvgs if 'bullish' in fvg.type]
        elif direction == 'bearish':
            trade_zones = [ob for ob in obs if 'bearish' in ob.type]
            trade_zones += [fvg for fvg in fvgs if 'bearish' in fvg.type]
        
        # 按新鲜度排序
        trade_zones = sorted(trade_zones, key=lambda x: (x.freshness, x.strength), reverse=True)
        
        logger.info(f"[Trinity]   ✅ SMC结构: {smc_result['structure'].value}")
        logger.info(f"[Trinity]   ✅ 流动性猎杀: {liquidity_hunt}")
        logger.info(f"[Trinity]   ✅ 交易区域数: {len(trade_zones)}")
        
        return {
            'smc_structure': smc_result['structure'],
            'liquidity_levels': liquidity_levels,
            'liquidity_hunt': liquidity_hunt,
            'trade_zones': trade_zones[:5],  # 只返回前5个
            'current_price': df['close'].iloc[-1],
            'df': df
        }
    
    def check_15m_entry(self, symbol: str, analysis: TrinityAnalysis) -> Optional[Dict[str, Any]]:
        """
        15分钟监控 (检查入场)
        
        Returns:
            {
                'entry_price': float,
                'stop_loss': float,
                'take_profit_levels': List[float],
                'pa_pattern': PAPattern,
                'zone': TradeZone
            } or None
        """
        logger.info(f"[Trinity] 15分钟入场检查: {symbol}")
        
        # 1. 获取15分钟数据
        df = self.fetch_binance_klines(symbol, self.entry_tf, limit=100)
        
        if df.empty or len(df) < 10:
            logger.warning(f"[Trinity] {symbol} 15M数据不足")
            return None
        
        current_price = df['close'].iloc[-1]
        
        # 2. 检查价格是否回到交易区域
        for zone in analysis.trade_zones:
            if zone.price_low <= current_price <= zone.price_high:
                logger.info(f"[Trinity]   ✅ 价格回到交易区域: "
                           f"{zone.price_low:.6f} - {zone.price_high:.6f}")
                
                # 3. 可选的15M PA形态确认
                # 新规则：默认不再要求15M出现同方向PA形态；
                # 价格回到1H交易区域且盈亏比满足，就允许入场。
                pa_pattern = PAPattern.NONE
                if self.require_15m_pa_confirmation:
                    pa_patterns = self.pa_analyzer.detect_all_patterns(df)
                    valid_patterns = []
                    for pattern in pa_patterns:
                        if analysis.direction == 'bullish' and pattern['pattern'].value.endswith('BULLISH'):
                            valid_patterns.append(pattern)
                        elif analysis.direction == 'bearish' and pattern['pattern'].value.endswith('BEARISH'):
                            valid_patterns.append(pattern)
                    
                    if not valid_patterns:
                        logger.info(f"[Trinity]   ⏳ 无符合方向的PA形态")
                        continue
                    
                    latest_pattern = valid_patterns[-1]
                    pa_pattern = latest_pattern['pattern']
                    logger.info(f"[Trinity]   ✅ 发现PA形态: {pa_pattern.value}")
                else:
                    logger.info("[Trinity]   ✅ 15M PA形态确认已关闭：价格回到1H交易区域即可计算入场")
                
                # 4. 计算止损止盈
                if analysis.direction == 'bullish':
                    entry = current_price
                    stop_loss = zone.price_low * (1 - self.stop_loss_buffer)
                    
                    # 计算盈亏比
                    risk = entry - stop_loss
                    min_tp = entry + risk * self.min_rr_ratio
                    
                    # 三级止盈目标（可配置R倍数，默认 2R/3R/4R）
                    tp1 = entry + risk * self.tp1_rr_ratio
                    tp2 = entry + risk * self.tp2_rr_ratio
                    tp3 = entry + risk * self.tp3_rr_ratio
                    
                    take_profit_levels = [tp1, tp2, tp3]
                    
                else:  # bearish
                    entry = current_price
                    stop_loss = zone.price_high * (1 + self.stop_loss_buffer)
                    
                    # 计算盈亏比
                    risk = stop_loss - entry
                    min_tp = entry - risk * self.min_rr_ratio
                    
                    # 三级止盈目标（可配置R倍数）
                    tp1 = entry - risk * self.tp1_rr_ratio
                    tp2 = entry - risk * self.tp2_rr_ratio
                    tp3 = entry - risk * self.tp3_rr_ratio
                    
                    take_profit_levels = [tp1, tp2, tp3]
                
                # 检查盈亏比是否达标
                if analysis.direction == 'bullish' and tp1 < min_tp:
                    logger.warning(f"[Trinity]   ❌ 盈亏比不足: TP1={tp1:.2f} < 最小={min_tp:.2f}")
                    continue
                elif analysis.direction == 'bearish' and tp1 > min_tp:
                    logger.warning(f"[Trinity]   ❌ 盈亏比不足: TP1={tp1:.2f} > 最小={min_tp:.2f}")
                    continue
                
                return {
                    'entry_price': entry,
                    'stop_loss': stop_loss,
                    'take_profit_levels': take_profit_levels,
                    'pa_pattern': pa_pattern,
                    'zone': zone
                }
        
        logger.info(f"[Trinity]   ⏳ 价格未回到区域，当前价: {current_price:.6f}")
        return None
    
    def generate_signal(self, df: pd.DataFrame, symbol: str = "", total_equity: float = 10000, df_1h: pd.DataFrame = None) -> StrategySignal:
        """
        生成交易信号 (主入口)
        
        Args:
            df: K线数据 (4H，预取或空)
            symbol: 交易对 (如 'BTC/USDT')
            total_equity: 账户总权益 (用于仓位计算)
            df_1h: 1H K线数据 (预取，Fix #4 Round4: 避免策略内部重复拉取)
            
        Returns:
            StrategySignal
        """
        if not symbol:
            logger.warning("[Trinity] 未提供symbol，无法生成信号")
            return StrategySignal(
                signal=SignalType.HOLD,
                symbol=symbol,
                price=0.0,
                timestamp=datetime.now(),
                reason="未提供symbol"
            )
        
        # 统一格式
        binance_symbol = symbol.replace('/', '')
        
        logger.info(f"[Trinity] 生成信号: {symbol}")
        
        # 获取K线数据：优先使用预取数据，否则自行获取
        df_4h = None
        # Fix P0-1: 不再覆盖 df_1h 参数，保留调用方传入的预取1H数据
        if df is not None:
            df_4h = df  # 传入的df作为4H数据
            # 不将df_4h赋值给df_1h！1小时数据应由analyze_1h自行获取或由调用方传入
            # 如果调用方传入了timeframe_data，可以从中提取1h
            if hasattr(df, 'attrs') and df.attrs.get('timeframe') == '1h':
                df_1h = df
            # 否则df_1h保持None，analyze_1h会自动从Binance API拉取
        logger.debug(f"[Trinity] generate_signal: df_4h={'预取' if df_4h is not None else '空'}, "
                     f"df_1h={'预取' if df_1h is not None else '空（将自动获取）'}")
        
        # 1. 4小时分析 (定方向)
        result_4h = self.analyze_4h(binance_symbol, df=df_4h)
        current_price_4h = result_4h.get('current_price', 0)
        if result_4h.get('direction') == 'neutral':
            return StrategySignal(
                signal=SignalType.HOLD,
                symbol=symbol,
                price=current_price_4h,
                timestamp=datetime.now(),
                reason="4H方向不明"
            )
        
        # 2. 1小时分析 (找结构)
        # Fix #4 (Round4): 优先使用传入的1H预取数据，避免重复API调用
        result_1h = self.analyze_1h(binance_symbol, result_4h.get('direction', 'neutral'), df=df_1h)
        current_price_1h = result_1h.get('current_price', current_price_4h)
        if len(result_1h.get('trade_zones', [])) == 0:
            return StrategySignal(
                signal=SignalType.HOLD,
                symbol=symbol,
                price=current_price_1h,
                timestamp=datetime.now(),
                reason="无交易区域"
            )
        
        # 3. 构建分析结果
        analysis = TrinityAnalysis(
            symbol=symbol,
            wyckoff_phase=result_4h.get('wyckoff_phase', WyckoffPhase.UNKNOWN),
            smc_structure=result_1h.get('smc_structure', SMCStructure.NONE),
            pa_pattern=PAPattern.NONE,  # 将在15M分析中填充
            trade_zones=result_1h.get('trade_zones', []),
            liquidity_levels=result_1h.get('liquidity_levels', {}),
            current_price=current_price_1h,
            volume_profile={'volume_avg': 0, 'volume_ratio': 1.0},
            timestamp=datetime.now(),
            direction=result_4h.get('direction', 'neutral')
        )
        
        # 4. 15分钟入场检查
        entry_signal = self.check_15m_entry(binance_symbol, analysis)
        if entry_signal is None:
            return StrategySignal(
                signal=SignalType.HOLD,
                symbol=symbol,
                price=analysis.current_price,
                timestamp=datetime.now(),
                reason="15M无入场信号"
            )
        
        # 5. 更新PA形态
        analysis.pa_pattern = entry_signal['pa_pattern']
        
        # 6. 信号共振评分 (需要更高时间框架方向)
        higher_tf_direction = result_4h['pa_structure']['trend']
        score, score_breakdown = self.scorer.score(analysis, higher_tf_direction)
        
        if not self.scorer.is_tradeable(score):
            return StrategySignal(
                signal=SignalType.HOLD,
                symbol=symbol,
                price=entry_signal['entry_price'],
                timestamp=datetime.now(),
                score=score,
                reason=f"共振评分不足: {score}/{self.scorer.min_score}",
                resonance_breakdown=score_breakdown
            )
        
        # 7. 确定信号类型
        if analysis.direction == 'bullish':
            signal_type = SignalType.BUY
        else:
            signal_type = SignalType.SELL
        
        # 8. 计算仓位
        risk_amount = total_equity * self.max_risk_per_trade
        risk_per_trade = abs(entry_signal['entry_price'] - entry_signal['stop_loss'])
        
        if risk_per_trade == 0:
            position_size = 0
        else:
            position_size = risk_amount / risk_per_trade
        
        # Fix: 仓位上限 — 名义价值不超过 max_single_order_usdt * leverage
        # position_size是币数，名义价值 = position_size * entry_price
        max_notional = self.max_single_order_usdt * self.leverage
        current_notional = position_size * entry_signal['entry_price']
        if current_notional > max_notional and max_notional > 0:
            position_size = max_notional / entry_signal['entry_price']
            logger.info(f"[Trinity] 仓位受限: 名义价值${current_notional:.2f} > "
                       f"上限${max_notional:.2f}, 调整为{position_size:.4f}币")
        
        # 9. 计算分批止盈数量
        tp_quantities = [
            position_size * self.tp_tier1_pct,
            position_size * self.tp_tier2_pct,
            position_size * self.tp_tier3_pct
        ]
        
        # 10. 生成最终信号
        signal = StrategySignal(
            signal=signal_type,
            symbol=symbol,
            price=entry_signal['entry_price'],
            timestamp=datetime.now(),
            score=score,
            reason=f"[Trinity] {analysis.direction.upper()} 共振评分:{score} "
                  f"威科夫:{analysis.wyckoff_phase.value} "
                  f"SMC:{analysis.smc_structure.value} "
                  f"PA:{analysis.pa_pattern.value}",
            stop_price=entry_signal['stop_loss'],
            take_profit_levels=entry_signal['take_profit_levels'],
            take_profit_quantities=tp_quantities,
            leverage=self.leverage,
            confidence=min(score / 31.0, 1.0),  # 满分31 (6因子加权)
            position_size=position_size,
            risk_percent=self.max_risk_per_trade,
            resonance_breakdown=score_breakdown
        )
        
        logger.info(f"[Trinity] ✅ 生成信号: {symbol} -> {signal_type.value} "
                   f"@ {signal.price:.2f} | 止损: {signal.stop_price:.2f}")
        logger.info(f"[Trinity]   共振评分: {score} (明细: {score_breakdown})")
        logger.info(f"[Trinity]   仓位: {position_size:.4f} 币 | 风险: {self.max_risk_per_trade*100:.1f}%")
        logger.info(f"[Trinity]   止盈: {[f'${tp:.2f}' for tp in signal.take_profit_levels]}")
        
        return signal


# ==================== 测试函数 ====================

def test_strategy():
    """测试策略"""
    print("🧪 测试 Trinity 三位一体策略...")
    
    # 1. 初始化策略
    strategy = TrinityStrategy()
    
    # 2. 测试BTC
    print(f"\n📊 测试 BTC/USDT...")
    symbol = "BTC/USDT"
    signal = strategy.generate_signal(pd.DataFrame(), symbol=symbol, total_equity=10000)
    
    print(f"\n🎯 信号: {signal.signal.value}")
    print(f"  价格: ${signal.price:.2f}")
    print(f"  止损: ${signal.stop_price:.2f}")
    print(f"  止盈: {[f'${tp:.2f}' for tp in signal.take_profit_levels]}")
    print(f"  仓位: {signal.position_size:.4f} BTC")
    print(f"  评分: {signal.score}")
    print(f"  理由: {signal.reason}")
    
    print("\n✅ 测试完成!")


if __name__ == "__main__":
    test_strategy()