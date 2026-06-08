"""
三位一体策略 - PA (Brooks价格行为学) 执行层分析模块

基于 Al Brooks 价格行为学，提供：
- "始终在场"方向判断
- H2/L2入场信号
- 信号K线质量评估
- 铁丝网/高潮检测
- 趋势强度评分
"""

import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class PASignal:
    """PA层输出信号"""
    always_in: str = "NEUTRAL"       # LONG, SHORT, NEUTRAL
    trend_strength: int = 0          # 1-5
    callback_legs: int = 0           # 当前回调腿数
    h2_ready: bool = False           # H2做多信号就绪
    l2_ready: bool = False           # L2做空信号就绪
    signal_bar_quality: int = 0      # 信号K线质量 0-100
    signal_bar_type: str = ""        # 信号K线类型
    ema_position: str = "NEUTRAL"    # ABOVE, BELOW, AT
    is_barbwire: bool = False        # 是否为铁丝网
    climax_warning: bool = False     # 高潮耗尽警告
    measured_move_target: Optional[float] = None
    entry_price: Optional[float] = None       # 建议入场价
    stop_loss: Optional[float] = None         # 建议止损价
    details: Dict = field(default_factory=dict)


class PAAnalyzer:
    """Brooks价格行为分析器"""
    
    def __init__(self):
        self.ema_period = 20
        self.swing_window = 5
        self.barbwire_overlap = 0.70  # 铁丝网重叠度阈值
        self.climax_multiplier = 1.5  # 高潮K线倍数
        
    def analyze(self, df, wyckoff_bias: str = "NEUTRAL",
                smc_pois: List[Dict] = None) -> PASignal:
        """
        主分析入口
        
        Args:
            df: DataFrame with OHLCV columns
            wyckoff_bias: 威科夫层偏向 (BULL/BEAR/NEUTRAL)
            smc_pois: SMC层的兴趣点列表
        
        Returns:
            PASignal
        """
        if len(df) < 30:
            return PASignal()
        
        signal = PASignal()
        
        # 1. 计算指标
        df = self._calculate_indicators(df)
        
        # 2. "始终在场"方向
        ai = self._detect_always_in(df)
        signal.always_in = ai["direction"]
        signal.details["always_in"] = ai
        
        # 3. 趋势强度
        strength = self._evaluate_trend_strength(df)
        signal.trend_strength = strength["score"]
        signal.details["trend_strength"] = strength
        
        # 4. 回调腿数计数
        legs = self._count_callback_legs(df, wyckoff_bias)
        signal.callback_legs = legs["count"]
        signal.details["callback_legs"] = legs
        
        # 5. H2/L2信号检测
        h2l2 = self._detect_h2l2(df, wyckoff_bias)
        signal.h2_ready = h2l2["h2_ready"]
        signal.l2_ready = h2l2["l2_ready"]
        signal.details["h2l2"] = h2l2
        
        # 6. 信号K线质量评估
        quality = self._evaluate_signal_bar(df, wyckoff_bias)
        signal.signal_bar_quality = quality["score"]
        signal.signal_bar_type = quality["type"]
        signal.details["signal_bar"] = quality
        
        # 7. EMA位置判断
        signal.ema_position = self._check_ema_position(df)
        
        # 8. 铁丝网检测
        signal.is_barbwire = self._detect_barbwire(df)
        
        # 9. 高潮警告
        signal.climax_warning = self._detect_climax_warning(df)
        
        # 10. 测量移动目标
        signal.measured_move_target = self._calculate_measured_move(df)
        
        # 11. 计算入场/止损
        entry_sl = self._calculate_entry_sl(df, wyckoff_bias, h2l2)
        signal.entry_price = entry_sl["entry"]
        signal.stop_loss = entry_sl["stop_loss"]
        
        # 12. 与SMC兴趣点对比验证
        if smc_pois:
            signal.details["poi_proximity"] = self._check_poi_proximity(
                df.iloc[-1]['close'], smc_pois
            )
        
        return signal
    
    def _calculate_indicators(self, df):
        """计算技术指标"""
        df = df.copy()
        
        # 20 EMA
        df['ema20'] = df['close'].ewm(span=self.ema_period, adjust=False).mean()
        
        # 实体大小
        df['body'] = np.abs(df['close'] - df['open'])
        df['body_pct'] = df['body'] / (df['high'] - df['low']).clip(lower=0.0001)
        
        # 收盘位置
        df['close_pos'] = (df['close'] - df['low']) / (df['high'] - df['low']).clip(lower=0.0001)
        
        # 上影线和下影线
        df['upper_wick'] = df['high'] - np.maximum(df['close'], df['open'])
        df['lower_wick'] = np.minimum(df['close'], df['open']) - df['low']
        
        # 上/下影线占比
        range_clipped = (df['high'] - df['low']).clip(lower=0.0001)
        df['upper_wick_pct'] = df['upper_wick'] / range_clipped
        df['lower_wick_pct'] = df['lower_wick'] / range_clipped
        
        # 是阳线还是阴线
        df['is_bull'] = df['close'] > df['open']
        
        # K线范围均值(用于高潮检测)
        df['range_ma'] = df['high'] - df['low']
        df['range_ma'] = df['range_ma'].rolling(10).mean()
        
        # 成交量均值
        df['vol_ma20'] = df['volume'].rolling(20).mean()
        
        return df
    
    def _detect_always_in(self, df) -> Dict:
        """判断"始终在场"方向"""
        result = {"direction": "NEUTRAL", "confidence": 0}
        
        recent = df.tail(20)
        
        # 因子1: EMA斜率
        ema_slope = self._calc_ema_slope(df)
        
        # 因子2: 趋势K线比例
        bull_bars = sum(1 for _, r in recent.iterrows() if r['is_bull'] and r['body_pct'] > 0.5)
        bear_bars = sum(1 for _, r in recent.iterrows() if not r['is_bull'] and r['body_pct'] > 0.5)
        trend_ratio = (bull_bars - bear_bars) / len(recent)
        
        # 因子3: 收盘位置平均
        avg_close_pos = recent['close_pos'].mean()
        
        # 因子4: 价格相对EMA位置
        current = recent.iloc[-1]['close']
        ema = recent.iloc[-1]['ema20']
        ema_distance = (current - ema) / ema if ema > 0 else 0
        
        # 综合判断
        bull_score = 0
        bear_score = 0
        
        if ema_slope > 0.0005:
            bull_score += 30
        elif ema_slope < -0.0005:
            bear_score += 30
        
        if trend_ratio > 0.2:
            bull_score += 25
        elif trend_ratio < -0.2:
            bear_score += 25
        
        if avg_close_pos > 0.6:
            bull_score += 20
        elif avg_close_pos < 0.4:
            bear_score += 20
        
        if ema_distance > 0.01:
            bull_score += 15
        elif ema_distance < -0.01:
            bear_score += 15
        
        # HH/HL vs LH/LL
        highs, lows = self._find_swings(recent)
        hh, lh = 0, 0
        hl, ll = 0, 0
        for i in range(1, len(highs)):
            if highs[i][1] > highs[i-1][1]: hh += 1
            else: lh += 1
        for i in range(1, len(lows)):
            if lows[i][1] > lows[i-1][1]: hl += 1
            else: ll += 1
        
        if hh > lh and hl > ll:
            bull_score += 10
        elif lh > hh and ll > hl:
            bear_score += 10
        
        # 最终判断
        if bull_score > bear_score + 15:
            result["direction"] = "LONG"
            result["confidence"] = min(bull_score, 100)
        elif bear_score > bull_score + 15:
            result["direction"] = "SHORT"
            result["confidence"] = min(bear_score, 100)
        else:
            result["direction"] = "NEUTRAL"
            result["confidence"] = min(max(bull_score, bear_score), 100)
        
        return result
    
    def _calc_ema_slope(self, df) -> float:
        """计算EMA斜率"""
        recent = df.tail(10)
        if len(recent) < 3:
            return 0.0
        ema_start = recent.iloc[0]['ema20']
        ema_end = recent.iloc[-1]['ema20']
        if ema_start <= 0:
            return 0.0
        return (ema_end - ema_start) / ema_start / len(recent)
    
    def _find_swings(self, df) -> Tuple[List, List]:
        """找到局部摆动点"""
        highs = []
        lows = []
        n = self.swing_window
        
        for i in range(n, len(df) - n):
            if df.iloc[i]['high'] == df.iloc[i-n:i+n+1]['high'].max():
                highs.append((i, df.iloc[i]['high']))
            if df.iloc[i]['low'] == df.iloc[i-n:i+n+1]['low'].min():
                lows.append((i, df.iloc[i]['low']))
        
        return highs, lows
    
    def _evaluate_trend_strength(self, df) -> Dict:
        """评估趋势强度 (1-5)"""
        result = {"score": 1, "factors": {}}
        
        recent = df.tail(20)
        
        # 因子1: 连续顺势K线数
        bull_streak = 0
        bear_streak = 0
        max_bull = 0
        max_bear = 0
        for _, r in recent.iterrows():
            if r['is_bull'] and r['body_pct'] > 0.4:
                bull_streak += 1
                bear_streak = 0
            elif not r['is_bull'] and r['body_pct'] > 0.4:
                bear_streak += 1
                bull_streak = 0
            else:
                bull_streak = 0
                bear_streak = 0
            max_bull = max(max_bull, bull_streak)
            max_bear = max(max_bear, bear_streak)
        
        max_streak = max(max_bull, max_bear)
        result["factors"]["max_streak"] = max_streak
        
        # 因子2: 回调深度
        highs, lows = self._find_swings(recent)
        
        # 因子3: EMA距离
        current = recent.iloc[-1]['close']
        ema = recent.iloc[-1]['ema20']
        ema_dist = abs(current - ema) / ema if ema > 0 else 0
        result["factors"]["ema_distance"] = round(ema_dist * 100, 2)
        
        # 因子4: K线收盘极端度
        avg_extreme = recent['close_pos'].apply(
            lambda x: abs(x - 0.5) * 2
        ).mean()
        result["factors"]["avg_extreme"] = round(avg_extreme, 2)
        
        # 综合评分
        score = 1
        
        if max_streak >= 5:
            score += 2
        elif max_streak >= 3:
            score += 1
        
        if ema_dist < 0.02:
            score += 1  # 近EMA = 回调中 = 趋势好
        
        if avg_extreme > 0.6:
            score += 1  # 收盘极端 = 有方向性
        
        result["score"] = min(score, 5)
        
        return result
    
    def _count_callback_legs(self, df, wyckoff_bias: str) -> Dict:
        """计数回调腿数 (用于H1/H2/H3判断)"""
        result = {"count": 0, "description": ""}
        
        if wyckoff_bias == "NEUTRAL":
            return result
        
        recent = df.tail(30)
        highs, lows = self._find_swings(recent)
        
        if wyckoff_bias == "BULL":
            # 在牛市中，计算下跌回调的腿数
            # 从最近的高点开始，数向下的摆动
            if len(highs) >= 2:
                last_high_idx = highs[-1][0]
                legs = 0
                
                # 从最后一个高点开始数下跌摆动
                current_low = None
                for i in range(len(lows) - 1, -1, -1):
                    if lows[i][0] >= last_high_idx:
                        if current_low is None:
                            current_low = lows[i]
                            legs = 1
                        elif lows[i][1] < current_low[1]:  # 新低
                            legs += 1
                            current_low = lows[i]
                
                result["count"] = legs
                result["description"] = f"H{legs}" if legs > 0 else "No回调"
        
        elif wyckoff_bias == "BEAR":
            if len(lows) >= 2:
                last_low_idx = lows[-1][0]
                legs = 0
                
                current_high = None
                for i in range(len(highs) - 1, -1, -1):
                    if highs[i][0] >= last_low_idx:
                        if current_high is None:
                            current_high = highs[i]
                            legs = 1
                        elif highs[i][1] > current_high[1]:  # 新高
                            legs += 1
                            current_high = highs[i]
                
                result["count"] = legs
                result["description"] = f"L{legs}" if legs > 0 else "No反弹"
        
        return result
    
    def _detect_h2l2(self, df, wyckoff_bias: str) -> Dict:
        """检测H2/L2入场信号"""
        result = {"h2_ready": False, "l2_ready": False, "signal_bar_idx": -1}
        
        if wyckoff_bias == "NEUTRAL":
            return result
        
        recent = df.tail(10)
        if len(recent) < 5:
            return result
        
        current = recent.iloc[-1]
        prev = recent.iloc[-2]
        
        if wyckoff_bias == "BULL":
            # H2信号: 当前K线高点 > 前一根K线高点（突破回调高点）
            # 且前一根是下跌后的十字星或小阴线
            # 且回调已经2腿以上
            legs = self._count_callback_legs(df, "BULL")
            
            if legs["count"] >= 2:
                # 检查信号K线（当前K线）
                if current['high'] > prev['high']:
                    # 前一根是回调K线（阴线或小阳线）
                    if not prev['is_bull'] or prev['body_pct'] < 0.3:
                        # 确认K线突破
                        result["h2_ready"] = True
                        result["signal_bar_idx"] = len(df) - 1
        
        if wyckoff_bias == "BEAR":
            legs = self._count_callback_legs(df, "BEAR")
            
            if legs["count"] >= 2:
                if current['low'] < prev['low']:
                    if prev['is_bull'] or prev['body_pct'] < 0.3:
                        result["l2_ready"] = True
                        result["signal_bar_idx"] = len(df) - 1
        
        return result
    
    def _evaluate_signal_bar(self, df, wyckoff_bias: str) -> Dict:
        """评估信号K线质量 (0-100)"""
        recent = df.tail(10)
        if len(recent) < 3:
            return {"score": 0, "type": "none"}
        
        current = recent.iloc[-1]
        
        # 实体占比评分 (0-40)
        body_score = min(current['body_pct'] * 40, 40)
        
        # 收盘位置评分 (0-30)
        if wyckoff_bias == "BULL":
            close_score = current['close_pos'] * 30
        elif wyckoff_bias == "BEAR":
            close_score = (1 - current['close_pos']) * 30
        else:
            close_score = abs(current['close_pos'] - 0.5) * 30
        
        # 影线评分 (0-20)
        if wyckoff_bias == "BULL":
            # 下影线=拒绝低价(好), 上影线=被拒绝(差)
            wick_score = min(current['lower_wick_pct'] * 30, 20)
        elif wyckoff_bias == "BEAR":
            wick_score = min(current['upper_wick_pct'] * 30, 20)
        else:
            wick_score = 5
        
        # K线大小评分 (0-10)
        avg_range = recent['range_ma'].iloc[-1]
        candle_range = current['high'] - current['low']
        size_ratio = candle_range / avg_range if avg_range > 0 else 1
        size_score = min(size_ratio * 5, 10)
        
        total = body_score + close_score + wick_score + size_score
        
        # 前一K线（用于判断内包/外包）
        prev_high = None
        prev_low = None
        if len(recent) >= 2:
            prev = recent.iloc[-2]
            prev_high = prev['high']
            prev_low = prev['low']
        
        # 判断K线类型
        bar_type = "doji"
        if current['body_pct'] > 0.6:
            bar_type = "trend_bar"
        elif current['body_pct'] > 0.3:
            bar_type = "moderate"
        elif candle_range > avg_range * 1.5:
            bar_type = "climax"
        elif prev_high is not None and prev_low is not None and \
             current['high'] <= prev_high and current['low'] >= prev_low:
            bar_type = "inside"
        elif prev_high is not None and prev_low is not None and \
             current['high'] > prev_high and current['low'] < prev_low:
            bar_type = "outside"
        
        return {"score": round(total), "type": bar_type}
    
    def _check_ema_position(self, df) -> str:
        """价格相对20EMA的位置"""
        current = df.iloc[-1]['close']
        ema = df.iloc[-1]['ema20']
        
        if ema <= 0:
            return "NEUTRAL"
        
        distance = (current - ema) / ema
        
        if distance > 0.01:
            return "ABOVE"
        elif distance < -0.01:
            return "BELOW"
        else:
            return "AT"
    
    def _detect_barbwire(self, df) -> bool:
        """检测铁丝网(3+根高度重叠K线)"""
        recent = df.tail(10)
        if len(recent) < 5:
            return False
        
        # 检查最近5根K线的重叠度
        overlap_count = 0
        for i in range(len(recent) - 1):
            curr = recent.iloc[i]
            next_bar = recent.iloc[i + 1]
            
            overlap_min = max(curr['low'], next_bar['low'])
            overlap_max = min(curr['high'], next_bar['high'])
            
            if overlap_max > overlap_min:
                curr_range = curr['high'] - curr['low']
                overlap = overlap_max - overlap_min
                if curr_range > 0 and overlap / curr_range > self.barbwire_overlap:
                    overlap_count += 1
        
        return overlap_count >= 3
    
    def _detect_climax_warning(self, df) -> bool:
        """检测高潮耗尽警告"""
        recent = df.tail(10)
        if len(recent) < 3:
            return False
        
        avg_range = recent['range_ma'].iloc[-3:].mean()
        
        # 检查最近K线是否异常大
        for i in range(len(recent) - 2, len(recent)):
            candle_range = recent.iloc[i]['high'] - recent.iloc[i]['low']
            if avg_range > 0 and candle_range > avg_range * self.climax_multiplier:
                # 再检查收盘是否极端
                close_pos = recent.iloc[i]['close_pos']
                if close_pos > 0.85 or close_pos < 0.15:
                    return True
        
        return False
    
    def _calculate_measured_move(self, df) -> Optional[float]:
        """计算测量移动目标 (腿1≈腿2)"""
        recent = df.tail(50)
        highs, lows = self._find_swings(recent)
        
        if len(highs) < 3 or len(lows) < 3:
            return None
        
        # 找最近完成的波段
        # 简化: 用最近两个完整的异向摆动
        swings = []
        all_swings = sorted(
            [(h[0], h[1], 'H') for h in highs] + [(l[0], l[1], 'L') for l in lows],
            key=lambda x: x[0]
        )
        
        # 至少需要3个交替的摆动点
        if len(all_swings) < 3:
            return None
        
        last_three = all_swings[-3:]
        types = [s[2] for s in last_three]
        
        # H-L-H 或 L-H-L 模式
        if types == ['H', 'L', 'H'] or types == ['L', 'H', 'L']:
            leg1 = abs(last_three[1][1] - last_three[0][1])
            if types == ['H', 'L', 'H']:
                # 牛市测量: 从低点+腿1长度
                target = last_three[1][1] + leg1
            else:
                # 熊市测量: 从高点-腿1长度
                target = last_three[1][1] - leg1
            return target
        
        return None
    
    def _calculate_entry_sl(self, df, wyckoff_bias: str, h2l2: Dict) -> Dict:
        """计算建议入场价和止损价"""
        result = {"entry": None, "stop_loss": None}
        
        recent = df.tail(10)
        current = recent.iloc[-1]
        
        if wyckoff_bias == "BULL" and h2l2["h2_ready"]:
            # 入场: 信号K线高点上方1 tick
            result["entry"] = current['high'] * 1.0001
            # 止损: 信号K线低点下方1%
            result["stop_loss"] = current['low'] * 0.99
        elif wyckoff_bias == "BEAR" and h2l2["l2_ready"]:
            result["entry"] = current['low'] * 0.9999
            result["stop_loss"] = current['high'] * 1.01
        
        return result
    
    def _check_poi_proximity(self, current_price: float, pois: List[Dict]) -> Dict:
        """检查当前价格与SMC兴趣点的距离"""
        result = {"nearest_poi": None, "distance_pct": float('inf')}
        
        for poi in pois:
            if poi.get("level"):
                distance = abs(current_price - poi["level"]) / current_price
                if distance < result["distance_pct"]:
                    result["distance_pct"] = distance
                    result["nearest_poi"] = poi["type"]
        
        return result
