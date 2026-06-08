"""
三位一体策略 - SMC (聪明钱概念) 机构层分析模块

基于 Smart Money Concepts，识别：
- 市场结构: BOS/CHoCH/MSS
- 流动性: SSL/BSL猎杀
- 订单块 (Order Blocks)
- 公允价值缺口 (FVG)
- OTE最优入场区
"""

import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class SMCSignal:
    """SMC层输出信号"""
    structure: str = "RANGE"       # BULLISH, BEARISH, RANGE
    bos_count: int = 0
    choch: bool = False
    mss: bool = False
    liquidity_sweep: Optional[str] = None  # "SSL", "BSL", None
    sweep_confirmed: bool = False
    order_block: Dict = field(default_factory=lambda: {"type": None, "proximal": None, "distal": None, "quality": 0})
    fvg: Dict = field(default_factory=lambda: {"type": None, "top": None, "bottom": None})
    ote_zone: Dict = field(default_factory=lambda: {"low": None, "sweet": None, "high": None})
    poi_list: List[Dict] = field(default_factory=list)
    breaker_detected: bool = False
    details: Dict = field(default_factory=dict)


class SMCAnalyzer:
    """SMC聪明钱分析器"""
    
    def __init__(self):
        self.swing_window = 5     # 摆动点检测窗口
        self.bos_lookback = 50    # BOS回溯范围
        
    def analyze(self, df, timeframe: str = "1h") -> SMCSignal:
        """
        主分析入口
        
        Args:
            df: DataFrame with OHLCV columns
            timeframe: 时间框架
        
        Returns:
            SMCSignal
        """
        if len(df) < 50:
            return SMCSignal()
        
        signal = SMCSignal()
        
        # 1. 检测市场结构
        structure = self._detect_structure(df)
        signal.structure = structure["type"]
        signal.details["structure"] = structure
        
        # 2. 检测BOS/CHoCH/MSS
        bos_choch = self._detect_bos_choch(df)
        signal.bos_count = bos_choch["bos_count"]
        signal.choch = bos_choch["choch"]
        signal.mss = bos_choch["mss"]
        signal.details["bos_choch"] = bos_choch
        
        # 3. 检测流动性猎杀
        sweep = self._detect_liquidity_sweep(df)
        signal.liquidity_sweep = sweep["type"]
        signal.sweep_confirmed = sweep["confirmed"]
        signal.details["liquidity_sweep"] = sweep
        
        # 4. 识别订单块
        ob = self._find_order_block(df)
        signal.order_block = ob
        signal.details["order_block"] = ob
        
        # 5. 识别FVG
        fvg = self._find_fvg(df)
        signal.fvg = fvg
        signal.details["fvg"] = fvg
        
        # 6. 计算OTE区间
        ote = self._calculate_ote(df)
        signal.ote_zone = ote
        signal.details["ote"] = ote
        
        # 7. 检测Breaker Block
        breaker = self._detect_breaker(df)
        signal.breaker_detected = breaker["detected"]
        signal.details["breaker"] = breaker
        
        # 8. 汇总POI
        signal.poi_list = self._collect_poi(signal)
        
        return signal
    
    def _detect_structure(self, df) -> Dict:
        """检测市场结构类型"""
        recent = df.tail(self.bos_lookback)
        
        # 找摆动高点和低点
        highs, lows = self._find_swings(recent)
        
        hh_count = sum(1 for i in range(1, len(highs)) if highs[i][1] > highs[i-1][1])
        lh_count = len(highs) - 1 - hh_count if len(highs) > 1 else 0
        
        hl_count = sum(1 for i in range(1, len(lows)) if lows[i][1] > lows[i-1][1])
        ll_count = len(lows) - 1 - hl_count if len(lows) > 1 else 0
        
        if hh_count > lh_count * 1.3 and hl_count > ll_count * 1.3:
            structure = "BULLISH"
        elif lh_count > hh_count * 1.3 and ll_count > hl_count * 1.3:
            structure = "BEARISH"
        else:
            structure = "RANGE"
        
        return {
            "type": structure,
            "hh_count": hh_count,
            "lh_count": lh_count,
            "hl_count": hl_count,
            "ll_count": ll_count,
            "highs": highs[-5:] if highs else [],
            "lows": lows[-5:] if lows else []
        }
    
    def _find_swings(self, df) -> Tuple[List, List]:
        """找到局部摆动高点和低点"""
        highs = []
        lows = []
        n = self.swing_window
        
        for i in range(n, len(df) - n):
            # 摆动高点
            if df.iloc[i]['high'] == df.iloc[i-n:i+n+1]['high'].max():
                highs.append((i, df.iloc[i]['high']))
            # 摆动低点
            if df.iloc[i]['low'] == df.iloc[i-n:i+n+1]['low'].min():
                lows.append((i, df.iloc[i]['low']))
        
        # 合并相邻相同方向的摆动点
        highs = self._merge_swings(highs)
        lows = self._merge_swings(lows)
        
        return highs, lows
    
    def _merge_swings(self, swings: List) -> List:
        """合并过于接近的摆动点"""
        if len(swings) < 2:
            return swings
        merged = [swings[0]]
        for s in swings[1:]:
            last = merged[-1]
            if abs(s[0] - last[0]) < 3:  # 太接近，保留极值
                if (s[1] - last[1]) * (1 if len(merged) > 1 else -1) < 0:
                    continue  # 保持最后一个
            merged.append(s)
        return merged
    
    def _detect_bos_choch(self, df) -> Dict:
        """检测BOS(结构突破), CHoCH(特征改变), MSS(市场结构转换)"""
        result = {"bos_count": 0, "choch": False, "mss": False}
        
        recent = df.tail(self.bos_lookback)
        highs, lows = self._find_swings(recent)
        
        if len(highs) < 3 or len(lows) < 3:
            return result
        
        # 判断当前结构类型
        structure = self._detect_structure(df)
        is_bull = structure["type"] == "BULLISH"
        is_bear = structure["type"] == "BEARISH"
        
        # BOS检测: 突破最近的相关摆动点
        current_price = recent.iloc[-1]['close']
        
        if is_bull:
            # 牛市BOS: 突破更早期的高点
            for h in reversed(highs[:-2]):
                if current_price > h[1]:
                    result["bos_count"] += 1
        elif is_bear:
            # 熊市BOS: 跌破更早期的低点
            for l in reversed(lows[:-2]):
                if current_price < l[1]:
                    result["bos_count"] += 1
        
        # CHoCH检测: 逆势首次突破结构点
        if is_bull and len(lows) >= 2 and len(highs) >= 1:
            last_low = lows[-1][1]
            prev_higher_low = None
            for l in reversed(lows[:-1]):
                if l[1] > last_low:
                    prev_higher_low = l
                    break
            if prev_higher_low and current_price < prev_higher_low[1]:
                result["choch"] = True
                # 检查是否MSS(大实体突破)
                body_pct = abs(recent.iloc[-1]['close'] - recent.iloc[-1]['open']) / max(recent.iloc[-1]['high'] - recent.iloc[-1]['low'], 0.0001)
                if body_pct > 0.6:
                    result["mss"] = True
        
        elif is_bear and len(highs) >= 2 and len(lows) >= 1:
            last_high = highs[-1][1]
            prev_lower_high = None
            for h in reversed(highs[:-1]):
                if h[1] < last_high:
                    prev_lower_high = h
                    break
            if prev_lower_high and current_price > prev_lower_high[1]:
                result["choch"] = True
                body_pct = abs(recent.iloc[-1]['close'] - recent.iloc[-1]['open']) / max(recent.iloc[-1]['high'] - recent.iloc[-1]['low'], 0.0001)
                if body_pct > 0.6:
                    result["mss"] = True
        
        return result
    
    def _detect_liquidity_sweep(self, df) -> Dict:
        """检测流动性猎杀: SSL(猎杀多头) 或 BSL(猎杀空头)"""
        result = {"type": None, "confirmed": False, "price": None}
        
        recent = df.tail(20)
        highs, lows = self._find_swings(recent)
        
        if len(highs) < 2 or len(lows) < 2:
            return result
        
        # SSL猎杀: 跌破前低后快速收回 (Spring类似)
        prev_low = lows[-2][1] if len(lows) >= 2 else None
        if prev_low:
            for i in range(len(recent) - 5, len(recent)):
                # 跌破了前低
                if recent.iloc[i]['low'] < prev_low:
                    # 检查是否在2根K线内收回
                    recovered = False
                    for j in range(1, min(4, len(recent) - i)):
                        if recent.iloc[i + j]['close'] > prev_low:
                            recovered = True
                            break
                    if recovered:
                        result["type"] = "SSL"
                        result["confirmed"] = True
                        result["price"] = recent.iloc[i]['low']
                        break
        
        # BSL猎杀: 涨破前高后快速回落 (UTAD类似)
        if not result["confirmed"]:
            prev_high = highs[-2][1] if len(highs) >= 2 else None
            if prev_high:
                for i in range(len(recent) - 5, len(recent)):
                    if recent.iloc[i]['high'] > prev_high:
                        fell_back = False
                        for j in range(1, min(4, len(recent) - i)):
                            if recent.iloc[i + j]['close'] < prev_high:
                                fell_back = True
                                break
                        if fell_back:
                            result["type"] = "BSL"
                            result["confirmed"] = True
                            result["price"] = recent.iloc[i]['high']
                            break
        
        return result
    
    def _find_order_block(self, df) -> Dict:
        """寻找最近的订单块 (Order Block)"""
        result = {"type": None, "proximal": None, "distal": None, "mid": None, "quality": 0}
        
        recent = df.tail(30)
        
        # 找看涨OB: 上涨前最后一根阴线
        bull_ob = self._find_bull_ob(recent)
        # 找看跌OB: 下跌前最后一根阳线
        bear_ob = self._find_bear_ob(recent)
        
        # 选择最近的一个
        if bull_ob["found"] and bear_ob["found"]:
            if bull_ob["location"] > bear_ob["location"]:  # 更近
                self._fill_ob_result(result, "BULL", bull_ob)
            else:
                self._fill_ob_result(result, "BEAR", bear_ob)
        elif bull_ob["found"]:
            self._fill_ob_result(result, "BULL", bull_ob)
        elif bear_ob["found"]:
            self._fill_ob_result(result, "BEAR", bear_ob)
        
        # 质量评估
        if result["type"]:
            result["quality"] = self._evaluate_ob_quality(recent, result)
        
        return result
    
    def _find_bull_ob(self, df) -> Dict:
        """找看涨订单块"""
        result = {"found": False, "location": -1, "ob_open": None, "ob_low": None, "ob_close": None}
        
        for i in range(len(df) - 5, 2, -1):
            # 当前K线是阴线?
            if df.iloc[i]['close'] < df.iloc[i]['open']:
                # 后续有上涨位移(2根K线内有大阳线)?
                has_displacement = False
                for j in range(1, min(4, len(df) - i)):
                    next_close = df.iloc[i + j]['close']
                    next_open = df.iloc[i + j]['open']
                    if next_close > next_open:
                        candle_range = df.iloc[i + j]['high'] - df.iloc[i + j]['low']
                        if candle_range > 0:
                            body_ratio = (next_close - next_open) / candle_range
                            if body_ratio > 0.5:  # 实体>50%
                                has_displacement = True
                                break
                
                # 突破前期摆动高点? (简易BOS检查)
                prev_high = df.iloc[i-3:i]['high'].max()
                recent_high = df.iloc[i+1:i+min(6, len(df)-i)]['high'].max()
                has_bos = recent_high > prev_high
                
                if has_displacement and has_bos:
                    result["found"] = True
                    result["location"] = i
                    result["ob_open"] = df.iloc[i]['open']
                    result["ob_low"] = df.iloc[i]['low']
                    result["ob_close"] = df.iloc[i]['close']
                    break
        
        return result
    
    def _find_bear_ob(self, df) -> Dict:
        """找看跌订单块"""
        result = {"found": False, "location": -1, "ob_open": None, "ob_high": None, "ob_close": None}
        
        for i in range(len(df) - 5, 2, -1):
            # 当前K线是阳线?
            if df.iloc[i]['close'] > df.iloc[i]['open']:
                # 后续有下跌位移
                has_displacement = False
                for j in range(1, min(4, len(df) - i)):
                    next_close = df.iloc[i + j]['close']
                    next_open = df.iloc[i + j]['open']
                    if next_close < next_open:
                        candle_range = df.iloc[i + j]['high'] - df.iloc[i + j]['low']
                        if candle_range > 0:
                            body_ratio = (next_open - next_close) / candle_range
                            if body_ratio > 0.5:
                                has_displacement = True
                                break
                
                # BOS检查
                prev_low = df.iloc[i-3:i]['low'].min()
                recent_low = df.iloc[i+1:i+min(6, len(df)-i)]['low'].min()
                has_bos = recent_low < prev_low
                
                if has_displacement and has_bos:
                    result["found"] = True
                    result["location"] = i
                    result["ob_open"] = df.iloc[i]['open']
                    result["ob_high"] = df.iloc[i]['high']
                    result["ob_close"] = df.iloc[i]['close']
                    break
        
        return result
    
    def _fill_ob_result(self, result: Dict, ob_type: str, ob_data: Dict):
        """填充OB结果"""
        result["type"] = ob_type
        if ob_type == "BULL":
            result["proximal"] = ob_data["ob_open"]       # 近端=开盘价
            result["distal"] = ob_data["ob_low"]           # 远端=最低价
            result["mid"] = (ob_data["ob_open"] + ob_data["ob_low"]) / 2  # 均值阈值
        else:
            result["proximal"] = ob_data["ob_open"]       # 近端=开盘价
            result["distal"] = ob_data["ob_high"]          # 远端=最高价
            result["mid"] = (ob_data["ob_open"] + ob_data["ob_high"]) / 2
    
    def _evaluate_ob_quality(self, df, ob: Dict) -> int:
        """评估OB质量 (0-100)"""
        score = 0
        
        if not ob["type"]:
            return 0
        
        recent = df.tail(20)
        
        # OB区域宽度适中(2-8%)
        if ob["proximal"] and ob["distal"]:
            width = abs(ob["proximal"] - ob["distal"]) / ob["distal"]
            if 0.02 <= width <= 0.08:
                score += 20
            elif 0.01 <= width <= 0.12:
                score += 10
        
        # 价格是否在OB附近(±5%)
        current = recent.iloc[-1]['close']
        if ob["mid"]:
            distance = abs(current - ob["mid"]) / ob["mid"]
            if distance < 0.05:
                score += 30
            elif distance < 0.10:
                score += 15
        
        # 位移强度检查
        score += 15  # 基础分(位移已在寻找时检查)
        
        # 最近成交量
        avg_vol = recent['volume'].mean()
        if recent.iloc[-1]['volume'] > avg_vol * 0.8:
            score += 10
        
        return min(score, 100)
    
    def _find_fvg(self, df) -> Dict:
        """寻找公允价值缺口 (FVG)"""
        result = {"type": None, "top": None, "bottom": None}
        
        recent = df.tail(10)
        if len(recent) < 3:
            return result
        
        for i in range(len(recent) - 2):
            c1 = recent.iloc[i]
            c2 = recent.iloc[i + 1]
            c3 = recent.iloc[i + 2]
            
            # 看涨FVG: C3的最低点 > C1的最高点
            if c3['low'] > c1['high']:
                # C2必须是大的位移K线
                body2 = abs(c2['close'] - c2['open'])
                range2 = c2['high'] - c2['low']
                if range2 > 0 and body2 / range2 > 0.5:
                    result["type"] = "BULL"
                    result["top"] = c1['high']
                    result["bottom"] = c3['low']
                    break
            
            # 看跌FVG: C3的最高点 < C1的最低点
            if c3['high'] < c1['low']:
                body2 = abs(c2['close'] - c2['open'])
                range2 = c2['high'] - c2['low']
                if range2 > 0 and body2 / range2 > 0.5:
                    result["type"] = "BEAR"
                    result["top"] = c1['low']
                    result["bottom"] = c3['high']
                    break
        
        return result
    
    def _calculate_ote(self, df) -> Dict:
        """计算OTE最优入场区间 (62-79%斐波那契回撤)"""
        result = {"low": None, "sweet": None, "high": None}
        
        recent = df.tail(50)
        highs, lows = self._find_swings(recent)
        
        if len(highs) < 2 or len(lows) < 2:
            return result
        
        # 找最近一次完整的冲动走势
        structure = self._detect_structure(df)
        
        if structure["type"] == "BULLISH":
            # 从最近低点到最高点
            swing_low = min(l[-1][1] for l in [lows[-2:]] if l)
            swing_high = max(h[-1][1] for h in [highs[-2:]] if h)
            
            if swing_high and swing_low and swing_high > swing_low:
                diff = swing_high - swing_low
                result["low"] = swing_high - diff * 0.79     # 79%
                result["sweet"] = swing_high - diff * 0.705  # 70.5%
                result["high"] = swing_high - diff * 0.62    # 62%
        
        elif structure["type"] == "BEARISH":
            swing_high = max(h[-1][1] for h in [highs[-2:]] if h)
            swing_low = min(l[-1][1] for l in [lows[-2:]] if l)
            
            if swing_high and swing_low and swing_high > swing_low:
                diff = swing_high - swing_low
                result["low"] = swing_low + diff * 0.62      # 62%
                result["sweet"] = swing_low + diff * 0.705   # 70.5%
                result["high"] = swing_low + diff * 0.79     # 79%
        
        return result
    
    def _detect_breaker(self, df) -> Dict:
        """检测Breaker Block (订单块失败变体)"""
        result = {"detected": False, "type": None}
        
        # 简化：如果价格穿越了之前的OB区域，可能形成Breaker
        recent = df.tail(20)
        current = recent.iloc[-1]['close']
        
        # 之前是否有OB被突破?
        ob = self._find_order_block(df)
        if ob["type"] == "BULL" and ob["distal"]:
            if current < ob["distal"]:  # 跌破看涨OB
                result["detected"] = True
                result["type"] = "BEAR_BREAKER"
        elif ob["type"] == "BEAR" and ob["distal"]:
            if current > ob["distal"]:  # 涨破看跌OB
                result["detected"] = True
                result["type"] = "BULL_BREAKER"
        
        return result
    
    def _collect_poi(self, signal: SMCSignal) -> List[Dict]:
        """汇总所有兴趣点 (POI)"""
        pois = []
        
        # OB作为POI
        if signal.order_block["type"]:
            direction = "LONG" if signal.order_block["type"] == "BULL" else "SHORT"
            pois.append({
                "type": "OB",
                "level": signal.order_block["mid"],
                "direction": direction,
                "quality": signal.order_block["quality"]
            })
        
        # FVG作为POI
        if signal.fvg["type"]:
            direction = "LONG" if signal.fvg["type"] == "BULL" else "SHORT"
            mid = (signal.fvg["top"] + signal.fvg["bottom"]) / 2
            pois.append({
                "type": "FVG",
                "level": mid,
                "direction": direction,
                "quality": 80
            })
        
        # OTE甜点作为POI
        if signal.ote_zone["sweet"]:
            direction = "LONG" if signal.structure == "BULLISH" else "SHORT"
            pois.append({
                "type": "OTE_SWEET",
                "level": signal.ote_zone["sweet"],
                "direction": direction,
                "quality": 70
            })
        
        # 流动性猎杀作为额外信息
        if signal.liquidity_sweep:
            direction = "LONG" if signal.liquidity_sweep == "SSL" else "SHORT"
            pois.append({
                "type": f"LIQUIDITY_SWEEP_{signal.liquidity_sweep}",
                "direction": direction,
                "quality": 90
            })
        
        return pois
