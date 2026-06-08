"""
三位一体策略 - 威科夫(Wyckoff)结构层分析模块

基于 Richard Wyckoff 操盘法，检测市场宏观阶段：
- 吸筹 (Accumulation)、上涨 (Markup)、派发 (Distribution)、下跌 (Markdown)
- 关键事件: SC/BC、AR、ST、Spring、UTAD、SOS、SOW、LPS、LPSY
"""

import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class WyckoffSignal:
    """威科夫层输出信号"""
    phase: str = "NEUTRAL"  # ACCUMULATION_A/B/C/D/E, MARKUP, DISTRIBUTION_A/B/C/D/E, MARKDOWN
    bias: str = "NEUTRAL"   # BULL, BEAR, NEUTRAL
    confidence: int = 0     # 0-100
    key_events: List[str] = field(default_factory=list)
    range_high: Optional[float] = None
    range_low: Optional[float] = None
    spring_detected: bool = False
    utad_detected: bool = False
    sos_confirmed: bool = False
    sow_confirmed: bool = False
    climax_detected: bool = False
    trend_structure: str = "NEUTRAL"  # HH_HL, LH_LL, MIXED
    details: Dict = field(default_factory=dict)


class WyckoffAnalyzer:
    """威科夫结构分析器"""
    
    def __init__(self):
        self.lookback = 200   # 默认回溯K线数
        self.atr_period = 14
        
    def analyze(self, df, timeframe: str = "4h") -> WyckoffSignal:
        """
        主分析入口
        
        Args:
            df: DataFrame with OHLCV columns
            timeframe: 时间框架 (daily, 4h, 1h)
        
        Returns:
            WyckoffSignal
        """
        if len(df) < 50:
            return WyckoffSignal()
        
        signal = WyckoffSignal()
        
        # 1. 计算基础指标
        df = self._calculate_indicators(df)
        
        # 2. 检测高潮K线 (SC/BC)
        climax = self._detect_climax(df)
        signal.climax_detected = climax["detected"]
        signal.details["climax"] = climax
        
        # 3. 检测趋势结构
        trend = self._detect_trend_structure(df)
        signal.trend_structure = trend["type"]
        signal.details["trend"] = trend
        
        # 4. 识别交易区间
        range_info = self._find_trading_range(df)
        signal.range_high = range_info["high"]
        signal.range_low = range_info["low"]
        signal.details["range"] = range_info
        
        # 5. 检测Spring/UTAD
        if range_info["high"] and range_info["low"]:
            spring = self._detect_spring(df, range_info)
            utad = self._detect_utad(df, range_info)
            signal.spring_detected = spring["detected"]
            signal.utad_detected = utad["detected"]
            signal.details["spring"] = spring
            signal.details["utad"] = utad
        
        # 6. 检测SOS/SOW
        if range_info["high"] and range_info["low"]:
            sos = self._detect_sos(df, range_info)
            sow = self._detect_sow(df, range_info)
            signal.sos_confirmed = sos["confirmed"]
            signal.sow_confirmed = sow["confirmed"]
            signal.details["sos"] = sos
            signal.details["sow"] = sow
        
        # 7. 检测二次测试
        if climax["detected"]:
            st = self._detect_secondary_test(df, climax)
            signal.details["secondary_test"] = st
        
        # 8. 综合判断阶段和偏向
        self._classify_phase(signal)
        
        # 9. 计算置信度
        signal.confidence = self._calculate_confidence(signal)
        
        return signal
    
    def _calculate_indicators(self, df):
        """计算技术指标"""
        df = df.copy()
        
        # ATR
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        tr = np.maximum(high_low, np.maximum(high_close, low_close))
        df['atr'] = tr.rolling(self.atr_period).mean()
        
        # 实体大小
        df['body'] = np.abs(df['close'] - df['open'])
        df['body_ratio'] = df['body'] / (df['high'] - df['low']).clip(lower=0.0001)
        
        # K线总范围
        df['range'] = df['high'] - df['low']
        
        # 收盘位置（0=最低, 1=最高）
        df['close_position'] = (df['close'] - df['low']) / df['range'].clip(lower=0.0001)
        
        # 成交量均值和标准差
        df['vol_ma'] = df['volume'].rolling(50).mean()
        df['vol_std'] = df['volume'].rolling(50).std()
        
        # 摆动高点和低点（简化版：5根K线窗口）
        df['swing_high'] = df['high'].rolling(5, center=True).max()
        df['swing_low'] = df['low'].rolling(5, center=True).min()
        
        return df
    
    def _detect_climax(self, df) -> Dict:
        """检测高潮K线 (卖出高潮SC 或 买入高潮BC)"""
        result = {
            "detected": False,
            "type": None,       # "SC" or "BC"
            "index": -1,
            "price": None,
            "volume_ratio": 0
        }
        
        # 回溯最近50根K线
        recent = df.tail(50)
        body_mean = recent['body'].mean()
        body_std = recent['body'].std()
        vol_mean = recent['vol_ma'].iloc[-1] if not np.isnan(recent['vol_ma'].iloc[-1]) else recent['volume'].mean()
        
        # 遍历寻找异常K线
        for i in range(len(recent) - 10, len(recent)):
            row = recent.iloc[i]
            
            # 条件1: 实体大于1.5倍均值（高潮K线）
            is_large_body = row['body'] > body_mean * 1.5 and row['body'] > body_std * 1.5
            
            # 条件2: 成交量大于2倍均值
            is_high_vol = row['volume'] > vol_mean * 2.0
            
            # 条件3: 收盘位置极端
            is_bearish = row['close_position'] < 0.25  # 收在低位 = 卖出高潮SC
            is_bullish = row['close_position'] > 0.75  # 收在高位 = 买入高潮BC
            
            if is_large_body and is_high_vol:
                recent_idx = len(df) - len(recent) + i
                
                if is_bearish and not result["detected"]:
                    result = {
                        "detected": True,
                        "type": "SC",
                        "index": recent_idx,
                        "price": row['low'],
                        "volume_ratio": row['volume'] / vol_mean if vol_mean > 0 else 0
                    }
                elif is_bullish and not result["detected"]:
                    result = {
                        "detected": True,
                        "type": "BC",
                        "index": recent_idx,
                        "price": row['high'],
                        "volume_ratio": row['volume'] / vol_mean if vol_mean > 0 else 0
                    }
        
        return result
    
    def _detect_trend_structure(self, df) -> Dict:
        """检测趋势结构: HH/HL = 看涨, LH/LL = 看跌"""
        result = {"type": "NEUTRAL", "hh_count": 0, "lh_count": 0, "hl_count": 0, "ll_count": 0}
        
        recent = df.tail(60)
        
        # 简化：找局部摆动点
        highs = []
        lows = []
        for i in range(5, len(recent) - 5):
            if recent.iloc[i]['high'] == recent.iloc[i-5:i+6]['high'].max():
                highs.append((i, recent.iloc[i]['high']))
            if recent.iloc[i]['low'] == recent.iloc[i-5:i+6]['low'].min():
                lows.append((i, recent.iloc[i]['low']))
        
        # 检查高点和低点序列
        hh, lh = 0, 0
        hl, ll = 0, 0
        
        for i in range(1, len(highs)):
            if highs[i][1] > highs[i-1][1]:
                hh += 1
            if highs[i][1] < highs[i-1][1]:
                lh += 1
        
        for i in range(1, len(lows)):
            if lows[i][1] > lows[i-1][1]:
                hl += 1
            if lows[i][1] < lows[i-1][1]:
                ll += 1
        
        result["hh_count"] = hh
        result["lh_count"] = lh
        result["hl_count"] = hl
        result["ll_count"] = ll
        
        # 判断趋势类型
        if hh > lh * 1.5 and hl > ll * 1.5:
            result["type"] = "HH_HL"  # 牛市结构
        elif lh > hh * 1.5 and ll > hl * 1.5:
            result["type"] = "LH_LL"  # 熊市结构
        else:
            result["type"] = "MIXED"  # 混合/区间
        
        return result
    
    def _find_trading_range(self, df) -> Dict:
        """识别当前交易区间"""
        result = {"high": None, "low": None, "mid": None, "width_pct": 0}
        
        recent = df.tail(100)
        
        # 找最近60根K线的高低范围
        lookback = min(60, len(recent))
        window = recent.tail(lookback)
        
        range_high = window['high'].max()
        range_low = window['low'].min()
        range_width = (range_high - range_low) / range_low * 100
        
        # 只有当区间宽度在合理范围(3-30%)才认为是有效交易区间
        if 3 <= range_width <= 30:
            result["high"] = range_high
            result["low"] = range_low
            result["mid"] = (range_high + range_low) / 2
            result["width_pct"] = round(range_width, 2)
        
        return result
    
    def _detect_spring(self, df, range_info: Dict) -> Dict:
        """检测Spring(弹簧/震仓): 跌破区间低点后快速收回"""
        result = {"detected": False, "index": -1, "price": None}
        
        if not range_info["low"]:
            return result
        
        range_low = range_info["low"]
        recent = df.tail(20)
        
        for i in range(2, len(recent)):
            # 条件: 跌破区间低点
            below_range = recent.iloc[i]['low'] < range_low
            # 条件: 2根K线内收回区间
            recovered = False
            for j in range(1, min(4, len(recent) - i)):
                if recent.iloc[i + j]['close'] > range_low:
                    recovered = True
                    break
            # 条件: 下跌时成交量不大（散户抛售已尽）
            vol_check = recent.iloc[i]['volume'] < recent['volume'].mean() * 1.5
            
            if below_range and recovered and vol_check:
                result["detected"] = True
                result["index"] = len(df) - len(recent) + i
                result["price"] = recent.iloc[i]['low']
                break
        
        return result
    
    def _detect_utad(self, df, range_info: Dict) -> Dict:
        """检测UTAD(派发后上冲): 突破区间高点后快速回落"""
        result = {"detected": False, "index": -1, "price": None}
        
        if not range_info["high"]:
            return result
        
        range_high = range_info["high"]
        recent = df.tail(20)
        
        for i in range(2, len(recent)):
            # 条件: 突破区间高点
            above_range = recent.iloc[i]['high'] > range_high
            # 条件: 2根K线内回到区间
            fell_back = False
            for j in range(1, min(4, len(recent) - i)):
                if recent.iloc[i + j]['close'] < range_high:
                    fell_back = True
                    break
            # 条件: 突破时成交量不大
            vol_check = recent.iloc[i]['volume'] < recent['volume'].mean() * 1.5
            
            if above_range and fell_back and vol_check:
                result["detected"] = True
                result["index"] = len(df) - len(recent) + i
                result["price"] = recent.iloc[i]['high']
                break
        
        return result
    
    def _detect_sos(self, df, range_info: Dict) -> Dict:
        """检测SOS(强势信号): 突破区间阻力 + 放量 + 持续"""
        result = {"confirmed": False}
        
        if not range_info["high"]:
            return result
        
        range_high = range_info["high"]
        recent = df.tail(10)
        
        for i in range(len(recent) - 2):
            breakout = recent.iloc[i]['close'] > range_high
            # 下一根K线也收在阻力上方
            sustain = (i + 1 < len(recent)) and recent.iloc[i + 1]['close'] > range_high
            # 成交量放大
            vol_up = recent.iloc[i]['volume'] > df['volume'].tail(20).mean()
            
            if breakout and sustain and vol_up:
                result["confirmed"] = True
                break
        
        return result
    
    def _detect_sow(self, df, range_info: Dict) -> Dict:
        """检测SOW(弱势信号): 跌破区间支撑 + 放量 + 持续"""
        result = {"confirmed": False}
        
        if not range_info["low"]:
            return result
        
        range_low = range_info["low"]
        recent = df.tail(10)
        
        for i in range(len(recent) - 2):
            breakdown = recent.iloc[i]['close'] < range_low
            sustain = (i + 1 < len(recent)) and recent.iloc[i + 1]['close'] < range_low
            vol_up = recent.iloc[i]['volume'] > df['volume'].tail(20).mean()
            
            if breakdown and sustain and vol_up:
                result["confirmed"] = True
                break
        
        return result
    
    def _detect_secondary_test(self, df, climax: Dict) -> Dict:
        """检测二次测试(ST): 回测高潮区域"""
        result = {"passed": False, "low_volume": False}
        
        if not climax["detected"]:
            return result
        
        climax_idx = climax["index"]
        climax_price = climax["price"]
        
        if climax_idx < 0 or climax_idx >= len(df):
            return result
        
        # 查看高潮后的K线
        after = df.iloc[climax_idx + 1:]
        if len(after) < 5:
            return result
        
        # 找是否回测了高潮价格区域（+-5%）
        test_zone = climax_price * 1.05 if climax["type"] == "SC" else climax_price * 0.95
        
        for i in range(len(after)):
            if climax["type"] == "SC":
                if after.iloc[i]['low'] <= climax_price * 1.03:  # 回测到SC低点的103%内
                    vol_low = after.iloc[i]['volume'] < climax["volume_ratio"] * df['volume'].mean() * 0.7
                    result["passed"] = True
                    result["low_volume"] = vol_low
                    break
            else:  # BC
                if after.iloc[i]['high'] >= climax_price * 0.97:  # 回测到BC高点的97%内
                    vol_low = after.iloc[i]['volume'] < climax["volume_ratio"] * df['volume'].mean() * 0.7
                    result["passed"] = True
                    result["low_volume"] = vol_low
                    break
        
        return result
    
    def _classify_phase(self, signal: WyckoffSignal):
        """根据检测到的事件综合判断威科夫阶段"""
        trend = signal.trend_structure
        climax = signal.details.get("climax", {})
        spring = signal.details.get("spring", {})
        utad = signal.details.get("utad", {})
        sos = signal.details.get("sos", {})
        sow = signal.details.get("sow", {})
        st = signal.details.get("secondary_test", {})
        
        events = []
        
        # 阶段判断逻辑
        if trend == "HH_HL" and sos.get("confirmed"):
            # 上涨趋势已确认
            signal.phase = "MARKUP"
            signal.bias = "BULL"
            events = ["SOS_confirmed", "HH_HL_structure"]
        elif trend == "LH_LL" and sow.get("confirmed"):
            # 下跌趋势已确认
            signal.phase = "MARKDOWN"
            signal.bias = "BEAR"
            events = ["SOW_confirmed", "LH_LL_structure"]
        elif spring.get("detected"):
            # Spring确认 = 吸筹C阶段
            signal.phase = "ACCUMULATION_C"
            signal.bias = "BULL"
            events = ["Spring_detected"]
        elif utad.get("detected"):
            # UTAD确认 = 派发C阶段
            signal.phase = "DISTRIBUTION_C"
            signal.bias = "BEAR"
            events = ["UTAD_detected"]
        elif climax.get("type") == "SC" and st.get("passed"):
            # 卖出高潮+二次测试通过 = 吸筹
            signal.phase = "ACCUMULATION_B"
            signal.bias = "BULL"
            events = ["SC_detected", "ST_passed"]
        elif climax.get("type") == "BC" and st.get("passed"):
            # 买入高潮+二次测试通过 = 派发
            signal.phase = "DISTRIBUTION_B"
            signal.bias = "BEAR"
            events = ["BC_detected", "ST_passed"]
        elif climax.get("type") == "SC":
            signal.phase = "ACCUMULATION_A"
            signal.bias = "NEUTRAL"
            events = ["SC_detected"]
        elif climax.get("type") == "BC":
            signal.phase = "DISTRIBUTION_A"
            signal.bias = "NEUTRAL"
            events = ["BC_detected"]
        elif trend == "HH_HL":
            signal.phase = "MARKUP"
            signal.bias = "BULL"
            events = ["HH_HL_structure"]
        elif trend == "LH_LL":
            signal.phase = "MARKDOWN"
            signal.bias = "BEAR"
            events = ["LH_LL_structure"]
        else:
            signal.phase = "NEUTRAL"
            signal.bias = "NEUTRAL"
            events = ["No_clear_phase"]
        
        signal.key_events = events
    
    def _calculate_confidence(self, signal: WyckoffSignal) -> int:
        """计算威科夫层置信度 (0-100)"""
        score = 0
        
        # 基础: 有明确阶段
        if signal.phase != "NEUTRAL":
            score += 20
        
        # 有Spring/UTAD 高加分
        if signal.spring_detected:
            score += 30
        if signal.utad_detected:
            score += 30
        
        # SOS/SOW 确认
        if signal.sos_confirmed:
            score += 25
        if signal.sow_confirmed:
            score += 25
        
        # 趋势结构明确
        if signal.trend_structure in ("HH_HL", "LH_LL"):
            score += 15
        
        # 有高潮+ST通过
        climax = signal.details.get("climax", {})
        st = signal.details.get("secondary_test", {})
        if climax.get("detected") and st.get("passed") and st.get("low_volume"):
            score += 30
        elif climax.get("detected"):
            score += 10
        
        # 有交易区间
        if signal.range_high and signal.range_low:
            score += 10
        
        # C阶段(Spring/UTAD) = 最佳入场时机
        if signal.phase in ("ACCUMULATION_C", "DISTRIBUTION_C"):
            score = min(score + 20, 100)
        
        return min(score, 100)
