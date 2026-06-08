"""
三位一体策略 - 核心编排引擎

融合 Wyckoff(结构层) + SMC(机构层) + PA(执行层)
输出最终交易信号: LONG / SHORT / HOLD
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime

from .trinity_wyckoff import WyckoffAnalyzer, WyckoffSignal
from .trinity_smc import SMCAnalyzer, SMCSignal
from .trinity_pa import PAAnalyzer, PASignal


@dataclass
class TrinitySignal:
    """三位一体最终交易信号"""
    signal: str = "HOLD"            # LONG, SHORT, HOLD
    grade: str = "D"                # A, B, C, D
    score: int = 0                  # 0-160
    confidence: int = 0             # 0-100
    timestamp: str = ""
    symbol: str = ""
    
    # 入场信息
    entry: Dict = field(default_factory=dict)
    stop_loss: Optional[float] = None
    take_profit: List[float] = field(default_factory=list)
    position_pct: float = 0.0       # 建议仓位百分比
    
    # 各层信号
    wyckoff: Optional[Dict] = None
    smc: Optional[Dict] = None
    pa: Optional[Dict] = None
    
    # 风控信息
    risk_per_trade: float = 0.02
    max_position_usdt: float = 0.0
    
    # 日志
    warnings: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    decision_path: List[str] = field(default_factory=list)


class TrinityEngine:
    """三位一体策略引擎"""
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.wyckoff = WyckoffAnalyzer()
        self.smc = SMCAnalyzer()
        self.pa = PAAnalyzer()
        
        # 风控参数
        self.risk_per_trade = self.config.get("risk_per_trade", 0.02)
        self.max_positions = self.config.get("max_positions", 2)
        self.max_daily_loss = self.config.get("max_daily_loss", 0.06)
        self.leverage = self.config.get("leverage", 5)
        self.min_score_long = self.config.get("min_score_long", 70)
        self.min_score_short = self.config.get("min_score_short", 70)
        
        # 评分权重（各层检查项权重，可回测优化）
        self.scoring = {
            # Wyckoff层
            "wyckoff_phase_clear": 30,      # C/D/E阶段或趋势
            "wyckoff_spring_utad": 15,       # Spring/UTAD通过
            # SMC层
            "smc_structure_align": 10,       # 结构方向一致
            "smc_liquidity_sweep": 15,       # 流动性猎杀
            "smc_in_ote": 10,                # 在OTE区间
            "smc_quality_ob": 15,            # 高质量OB
            # PA层
            "pa_h2l2_ready": 20,             # H2/L2信号就绪
            "pa_signal_quality": 15,         # 信号K线质量≥60
            "pa_ema_proximity": 10,          # 近20EMA
            "pa_no_barbwire": 5,             # 非铁丝网
            "pa_trend_strength": 10,         # 趋势强度≥3
            # 汇合加分
            "fvg_confluence": 10,            # FVG汇合
        }
    
    def analyze(self, df_dict: Dict[str, pd.DataFrame], 
                symbol: str = "BTCUSDT",
                account_balance: float = 100.0) -> TrinitySignal:
        """
        主分析入口
        
        Args:
            df_dict: {'daily': df, '4h': df, '1h': df, '15m': df}
            symbol: 交易对
            account_balance: 账户余额(USDT)
        
        Returns:
            TrinitySignal
        """
        signal = TrinitySignal()
        signal.timestamp = datetime.now().isoformat()
        signal.symbol = symbol
        signal.decision_path.append("=== 三位一体策略分析开始 ===")
        
        # ============ 第一层: Wyckoff 结构分析 ============
        signal.decision_path.append("\n[第一层] Wyckoff结构分析")
        
        # 优先使用日线，备用4H
        wyckoff_df = df_dict.get("daily")
        if wyckoff_df is None or (hasattr(wyckoff_df, 'empty') and wyckoff_df.empty):
            wyckoff_df = df_dict.get("4h")
        if wyckoff_df is None or len(wyckoff_df) < 50:
            signal.warnings.append("Wyckoff数据不足")
            signal.signal = "HOLD"
            return signal
        
        wyckoff_signal = self.wyckoff.analyze(wyckoff_df, timeframe="daily")
        signal.wyckoff = self._serialize_wyckoff(wyckoff_signal)
        signal.decision_path.append(f"  阶段: {wyckoff_signal.phase}")
        signal.decision_path.append(f"  偏向: {wyckoff_signal.bias}")
        signal.decision_path.append(f"  置信: {wyckoff_signal.confidence}")
        signal.decision_path.append(f"  事件: {wyckoff_signal.key_events}")
        
        # 威科夫中性 = 不交易
        if wyckoff_signal.bias == "NEUTRAL":
            signal.signal = "HOLD"
            signal.grade = "D"
            signal.decision_path.append("  → Wyckoff中性，不交易")
            return signal
        
        # ============ 第二层: SMC 机构层 ============
        signal.decision_path.append("\n[第二层] SMC机构层分析")
        
        smc_df = df_dict.get("1h")
        if smc_df is None or len(smc_df) < 30:
            smc_df = df_dict.get("4h")
        if smc_df is None or len(smc_df) < 30:
            signal.warnings.append("SMC数据不足")
        
        if smc_df is not None:
            smc_signal = self.smc.analyze(smc_df, timeframe="1h")
            signal.smc = self._serialize_smc(smc_signal)
            signal.decision_path.append(f"  结构: {smc_signal.structure}")
            signal.decision_path.append(f"  流动性猎杀: {smc_signal.liquidity_sweep}")
            signal.decision_path.append(f"  OB类型: {smc_signal.order_block.get('type')}")
            signal.decision_path.append(f"  FVG: {smc_signal.fvg.get('type')}")
            
            # 检查结构一致性
            structure_ok = self._check_structure_align(wyckoff_signal, smc_signal)
            if not structure_ok:
                signal.warnings.append("SMC结构方向与Wyckoff不一致")
                signal.decision_path.append("  ⚠ SMC结构与Wyckoff矛盾!")
        else:
            smc_signal = None
        
        # ============ 第三层: PA 执行层 ============
        signal.decision_path.append("\n[第三层] PA执行层分析")
        
        pa_df = df_dict.get("15m")
        if pa_df is None or len(pa_df) < 20:
            pa_df = df_dict.get("1h")
        if pa_df is None or len(pa_df) < 20:
            signal.warnings.append("PA数据不足")
        
        smc_pois = smc_signal.poi_list if smc_signal else []
        
        if pa_df is not None:
            pa_signal = self.pa.analyze(
                pa_df, 
                wyckoff_bias=wyckoff_signal.bias,
                smc_pois=smc_pois
            )
            signal.pa = self._serialize_pa(pa_signal)
            signal.decision_path.append(f"  始终在场: {pa_signal.always_in}")
            signal.decision_path.append(f"  趋势强度: {pa_signal.trend_strength}/5")
            signal.decision_path.append(f"  回调腿数: {pa_signal.callback_legs}")
            signal.decision_path.append(f"  H2就绪: {pa_signal.h2_ready}")
            signal.decision_path.append(f"  L2就绪: {pa_signal.l2_ready}")
            signal.decision_path.append(f"  信号K线质量: {pa_signal.signal_bar_quality}")
            signal.decision_path.append(f"  铁丝网: {pa_signal.is_barbwire}")
            signal.decision_path.append(f"  高潮警告: {pa_signal.climax_warning}")
        else:
            pa_signal = None
        
        # ============ 评分与决策 ============
        signal.decision_path.append("\n[评分] 三位一体评分")
        
        score_result = self._calculate_score(
            wyckoff_signal, smc_signal, pa_signal
        )
        signal.score = score_result["total"]
        signal.grade = score_result["grade"]
        signal.reasons = score_result["reasons"]
        signal.decision_path.extend(score_result["details"])
        
        # 检查必须项
        required_check = self._check_required(wyckoff_signal, pa_signal)
        if not required_check["passed"]:
            signal.signal = "HOLD"
            signal.grade = "D"
            signal.warnings.append(required_check["reason"])
            signal.decision_path.append(f"  ❌ 必须项不满足: {required_check['reason']}")
            return signal
        
        # ============ 生成最终信号 ============
        signal.decision_path.append(f"\n[决策] 最终得分: {signal.score}/160 → {signal.grade}级")
        
        if signal.grade == "A":
            signal.position_pct = 1.0   # 满仓
            signal.decision_path.append("  → A级信号，满仓")
        elif signal.grade == "B":
            signal.position_pct = 0.5   # 半仓
            signal.decision_path.append("  → B级信号，半仓")
        elif signal.grade == "C":
            signal.position_pct = 0.33  # 1/3仓
            signal.decision_path.append("  → C级信号，1/3仓")
        else:
            signal.position_pct = 0.0
            signal.decision_path.append("  → D级信号，不交易")
        
        # 方向
        if wyckoff_signal.bias == "BULL" and pa_signal and pa_signal.h2_ready:
            signal.signal = "LONG"
        elif wyckoff_signal.bias == "BEAR" and pa_signal and pa_signal.l2_ready:
            signal.signal = "SHORT"
        else:
            signal.signal = "HOLD"
        
        # ============ 计算具体入场参数 ============
        if signal.signal in ("LONG", "SHORT"):
            signal = self._calculate_trade_params(
                signal, wyckoff_signal, smc_signal, pa_signal, 
                account_balance, pa_df
            )
        
        # ============ 风控检查 ============
        signal.decision_path.append("\n[风控检查]")
        
        # 检查高潮警告
        if pa_signal and pa_signal.climax_warning:
            signal.warnings.append("高潮耗尽警告，建议减仓")
            signal.decision_path.append("  ⚠ 高潮警告 → 建议减仓")
            if signal.grade == "A":
                signal.grade = "B"
                signal.position_pct = 0.5
        
        # 检查资金费率（加密货币特有）
        if self.config.get("check_funding_rate", True) and signal.signal == "LONG":
            # TODO: 接入资金费率API
            signal.decision_path.append("  ✓ 资金费率检查 (待接入API)")
        
        signal.decision_path.append(f"\n=== 最终决策: {signal.signal} ({signal.grade}级, {signal.score}分) ===")
        
        return signal
    
    def _calculate_score(self, wyckoff: WyckoffSignal,
                         smc: Optional[SMCSignal],
                         pa: Optional[PASignal]) -> Dict:
        """计算三位一体综合评分"""
        total = 0
        reasons = []
        details = []
        
        # ---- Wyckoff 层 ----
        wyckoff_ok = wyckoff.phase not in ("NEUTRAL",)
        if wyckoff_ok:
            score = self.scoring["wyckoff_phase_clear"]
            total += score
            reasons.append(f"Wyckoff {wyckoff.phase} (+{score})")
            details.append(f"  Wyckoff阶段明确 ({wyckoff.phase}): +{score}")
        else:
            details.append(f"  Wyckoff不明确: +0")
        
        if wyckoff.spring_detected:
            total += self.scoring["wyckoff_spring_utad"]
            reasons.append(f"Spring通过 (+{self.scoring['wyckoff_spring_utad']})")
            details.append(f"  Spring检测: +{self.scoring['wyckoff_spring_utad']}")
        elif wyckoff.utad_detected:
            total += self.scoring["wyckoff_spring_utad"]
            reasons.append(f"UTAD通过 (+{self.scoring['wyckoff_spring_utad']})")
            details.append(f"  UTAD检测: +{self.scoring['wyckoff_spring_utad']}")
        
        # ---- SMC 层 ----
        if smc:
            structure_align = (
                (wyckoff.bias == "BULL" and smc.structure == "BULLISH") or
                (wyckoff.bias == "BEAR" and smc.structure == "BEARISH")
            )
            if structure_align:
                total += self.scoring["smc_structure_align"]
                reasons.append(f"SMC结构一致 (+{self.scoring['smc_structure_align']})")
                details.append(f"  SMC结构方向一致: +{self.scoring['smc_structure_align']}")
            
            if smc.sweep_confirmed:
                total += self.scoring["smc_liquidity_sweep"]
                reasons.append(f"{smc.liquidity_sweep}猎杀 (+{self.scoring['smc_liquidity_sweep']})")
                details.append(f"  流动性猎杀({smc.liquidity_sweep}): +{self.scoring['smc_liquidity_sweep']}")
            
            ob = smc.order_block
            if ob.get("type") and ob["type"] in ("BULL", "BEAR"):
                ob_dir_match = (
                    (wyckoff.bias == "BULL" and ob["type"] == "BULL") or
                    (wyckoff.bias == "BEAR" and ob["type"] == "BEAR")
                )
                if ob_dir_match and ob.get("quality", 0) >= 40:
                    total += self.scoring["smc_quality_ob"]
                    reasons.append(f"高质量OB (+{self.scoring['smc_quality_ob']})")
                    details.append(f"  高质量OB({ob['type']}): +{self.scoring['smc_quality_ob']}")
            
            # OTE检查
            ote = smc.ote_zone
            if ote.get("sweet"):
                total += self.scoring["smc_in_ote"]
                reasons.append(f"在OTE区间 (+{self.scoring['smc_in_ote']})")
                details.append(f"  OTE区间有效: +{self.scoring['smc_in_ote']}")
            
            # FVG汇合
            if smc.fvg.get("type"):
                total += self.scoring["fvg_confluence"]
                reasons.append(f"FVG汇合 (+{self.scoring['fvg_confluence']})")
                details.append(f"  FVG汇合: +{self.scoring['fvg_confluence']}")
        
        # ---- PA 层 ----
        if pa:
            h2l2_ready = (wyckoff.bias == "BULL" and pa.h2_ready) or \
                         (wyckoff.bias == "BEAR" and pa.l2_ready)
            if h2l2_ready:
                total += self.scoring["pa_h2l2_ready"]
                signal_name = "H2" if wyckoff.bias == "BULL" else "L2"
                reasons.append(f"{signal_name}就绪 (+{self.scoring['pa_h2l2_ready']})")
                details.append(f"  {signal_name}信号就绪: +{self.scoring['pa_h2l2_ready']}")
            
            if pa.signal_bar_quality >= 60:
                total += self.scoring["pa_signal_quality"]
                reasons.append(f"信号K线质量{pa.signal_bar_quality} (+{self.scoring['pa_signal_quality']})")
                details.append(f"  信号K线质量({pa.signal_bar_quality}): +{self.scoring['pa_signal_quality']}")
            
            if pa.ema_position in ("AT", "BELOW" if wyckoff.bias == "BULL" else "ABOVE"):
                total += self.scoring["pa_ema_proximity"]
                reasons.append(f"近20EMA (+{self.scoring['pa_ema_proximity']})")
                details.append(f"  EMA接近: +{self.scoring['pa_ema_proximity']}")
            
            if not pa.is_barbwire:
                total += self.scoring["pa_no_barbwire"]
                reasons.append(f"非铁丝网 (+{self.scoring['pa_no_barbwire']})")
                details.append(f"  非铁丝网: +{self.scoring['pa_no_barbwire']}")
            
            if pa.trend_strength >= 3:
                total += self.scoring["pa_trend_strength"]
                reasons.append(f"趋势强度{pa.trend_strength} (+{self.scoring['pa_trend_strength']})")
                details.append(f"  趋势强度({pa.trend_strength}/5): +{self.scoring['pa_trend_strength']}")
        
        # ---- 评级 ----
        details.append(f"\n  总分: {total}/160")
        
        if total >= 120:
            grade = "A"
        elif total >= 90:
            grade = "B"
        elif total >= 70:
            grade = "C"
        else:
            grade = "D"
        
        return {"total": total, "grade": grade, "reasons": reasons, "details": details}
    
    def _check_required(self, wyckoff: WyckoffSignal, 
                        pa: Optional[PASignal]) -> Dict:
        """检查必须项"""
        # 必须1: Wyckoff偏向明确
        if wyckoff.bias == "NEUTRAL":
            return {"passed": False, "reason": "Wyckoff偏向中性"}
        
        # 必须2: PA有入场信号
        if pa is None:
            return {"passed": False, "reason": "PA数据缺失"}
        
        has_h2l2 = (wyckoff.bias == "BULL" and pa.h2_ready) or \
                   (wyckoff.bias == "BEAR" and pa.l2_ready)
        if not has_h2l2:
            return {"passed": False, "reason": "PA无H2/L2信号"}
        
        # 必须3: 信号K线质量
        if pa.signal_bar_quality < 40:
            return {"passed": False, "reason": f"信号K线质量过低({pa.signal_bar_quality})"}
        
        # 必须4: 非铁丝网
        if pa.is_barbwire:
            return {"passed": False, "reason": "市场处于铁丝网状态"}
        
        return {"passed": True, "reason": ""}
    
    def _check_structure_align(self, wyckoff: WyckoffSignal, 
                                smc: SMCSignal) -> bool:
        """检查Wyckoff与SMC结构是否一致"""
        if wyckoff.bias == "BULL" and smc.structure in ("BULLISH",):
            return True
        if wyckoff.bias == "BEAR" and smc.structure in ("BEARISH",):
            return True
        # SMC为RANGE也可以接受（在区间突破前）
        if smc.structure == "RANGE":
            return True
        return False
    
    def _calculate_trade_params(self, signal: TrinitySignal,
                                 wyckoff: WyckoffSignal,
                                 smc: Optional[SMCSignal],
                                 pa: PASignal,
                                 balance: float,
                                 pa_df) -> TrinitySignal:
        """计算具体交易参数"""
        
        # ---- 止损 ----
        # 从三层中取最保守的止损
        stops = []
        
        # PA层止损
        if pa.stop_loss:
            stops.append(pa.stop_loss)
        
        # SMC层止损
        if smc and smc.order_block.get("type"):
            ob = smc.order_block
            if signal.signal == "LONG" and ob.get("distal"):
                stops.append(ob["distal"] * 0.99)  # OB远端下方1%
            elif signal.signal == "SHORT" and ob.get("distal"):
                stops.append(ob["distal"] * 1.01)  # OB远端上方1%
        
        # Wyckoff层止损
        if signal.signal == "LONG":
            if wyckoff.range_low:
                stops.append(wyckoff.range_low * 0.99)
            if wyckoff.spring_detected:
                spring_price = wyckoff.details.get("spring", {}).get("price")
                if spring_price:
                    stops.append(spring_price * 0.99)
        else:
            if wyckoff.range_high:
                stops.append(wyckoff.range_high * 1.01)
            if wyckoff.utad_detected:
                utad_price = wyckoff.details.get("utad", {}).get("price")
                if utad_price:
                    stops.append(utad_price * 1.01)
        
        # 取最保守的止损
        if stops:
            signal.stop_loss = max(stops) if signal.signal == "LONG" else min(stops)
        else:
            # 默认: 当前价格的3%
            current_price = pa.entry_price or 0
            signal.stop_loss = current_price * 0.97 if signal.signal == "LONG" else current_price * 1.03
        
        # ---- 入场价 ----
        if pa.entry_price:
            signal.entry = {"price": pa.entry_price, "type": "STOP"}
        else:
            current_price = pa_df.iloc[-1]['close'] if pa_df is not None else 0
            signal.entry = {"price": current_price, "type": "MARKET"}
        
        # ---- 获利目标 ----
        entry = signal.entry.get("price", 0)
        sl = signal.stop_loss
        if entry > 0 and sl:
            risk_pct = abs(entry - sl) / entry
            signal.take_profit = [
                round(entry * (1 + risk_pct) if signal.signal == "LONG" else entry * (1 - risk_pct), 2),  # TP1: 1×风险
                round(entry * (1 + risk_pct * 2) if signal.signal == "LONG" else entry * (1 - risk_pct * 2), 2),  # TP2: 2×风险
                round(entry * (1 + risk_pct * 3) if signal.signal == "LONG" else entry * (1 - risk_pct * 3), 2),  # TP3: 3×风险
            ]
        
        # ---- 仓位 ----
        risk_amount = balance * self.risk_per_trade
        stop_distance_pct = abs(entry - sl) / entry
        
        if stop_distance_pct > 0:
            position_usdt = risk_amount / stop_distance_pct
            signal.max_position_usdt = min(
                round(position_usdt * signal.position_pct, 2),
                balance * self.leverage * 0.8  # 不超过可用保证金80%
            )
        
        return signal
    
    # ---- 序列化辅助方法 ----
    
    def _serialize_wyckoff(self, s: WyckoffSignal) -> Dict:
        return {
            "phase": s.phase,
            "bias": s.bias,
            "confidence": s.confidence,
            "key_events": s.key_events,
            "range_high": s.range_high,
            "range_low": s.range_low,
            "spring_detected": s.spring_detected,
            "utad_detected": s.utad_detected,
            "sos_confirmed": s.sos_confirmed,
            "sow_confirmed": s.sow_confirmed,
            "trend_structure": s.trend_structure,
        }
    
    def _serialize_smc(self, s: SMCSignal) -> Dict:
        return {
            "structure": s.structure,
            "bos_count": s.bos_count,
            "choch": s.choch,
            "mss": s.mss,
            "liquidity_sweep": s.liquidity_sweep,
            "sweep_confirmed": s.sweep_confirmed,
            "order_block": s.order_block,
            "fvg": s.fvg,
            "ote_zone": s.ote_zone,
            "poi_list": s.poi_list[:5],  # 限制POI数量
            "breaker_detected": s.breaker_detected,
        }
    
    def _serialize_pa(self, s: PASignal) -> Dict:
        return {
            "always_in": s.always_in,
            "trend_strength": s.trend_strength,
            "callback_legs": s.callback_legs,
            "h2_ready": s.h2_ready,
            "l2_ready": s.l2_ready,
            "signal_bar_quality": s.signal_bar_quality,
            "signal_bar_type": s.signal_bar_type,
            "ema_position": s.ema_position,
            "is_barbwire": s.is_barbwire,
            "climax_warning": s.climax_warning,
            "measured_move_target": s.measured_move_target,
            "entry_price": s.entry_price,
            "stop_loss": s.stop_loss,
        }
    
    def get_status_report(self, signal: TrinitySignal) -> str:
        """生成结构化状态报告"""
        lines = [
            f"=== 三位一体策略 - {signal.symbol} ===",
            f"时间: {signal.timestamp}",
            f"信号: {signal.signal} | 等级: {signal.grade} | 评分: {signal.score}/160",
            f"",
            f"--- Wyckoff ---",
            f"阶段: {signal.wyckoff.get('phase', 'N/A') if signal.wyckoff else 'N/A'}",
            f"偏向: {signal.wyckoff.get('bias', 'N/A') if signal.wyckoff else 'N/A'}",
            f"置信: {signal.wyckoff.get('confidence', 0) if signal.wyckoff else 0}",
            f"事件: {signal.wyckoff.get('key_events', []) if signal.wyckoff else []}",
            f"",
            f"--- SMC ---",
            f"结构: {signal.smc.get('structure', 'N/A') if signal.smc else 'N/A'}",
            f"猎杀: {signal.smc.get('liquidity_sweep', 'None') if signal.smc else 'N/A'}",
            f"OB: {signal.smc.get('order_block', {}).get('type', 'None') if signal.smc else 'N/A'}",
            f"FVG: {signal.smc.get('fvg', {}).get('type', 'None') if signal.smc else 'N/A'}",
            f"",
            f"--- PA ---",
            f"方向: {signal.pa.get('always_in', 'N/A') if signal.pa else 'N/A'}",
            f"趋势: {signal.pa.get('trend_strength', 0) if signal.pa else 0}/5",
            f"H2: {signal.pa.get('h2_ready', False) if signal.pa else False} | L2: {signal.pa.get('l2_ready', False) if signal.pa else False}",
            f"信号K线: {signal.pa.get('signal_bar_quality', 0) if signal.pa else 0}分 ({signal.pa.get('signal_bar_type', 'N/A') if signal.pa else 'N/A'})",
            f"铁丝网: {signal.pa.get('is_barbwire', False) if signal.pa else False} | 高潮: {signal.pa.get('climax_warning', False) if signal.pa else False}",
            f"",
            f"--- 交易参数 ---",
            f"入场: {signal.entry}",
            f"止损: {signal.stop_loss}",
            f"止盈: {signal.take_profit}",
            f"仓位: {signal.position_pct*100}% (${signal.max_position_usdt})",
            f"",
            f"--- 原因 ---",
        ]
        
        for r in signal.reasons:
            lines.append(f"  + {r}")
        
        if signal.warnings:
            lines.append(f"")
            lines.append(f"--- 警告 ---")
            for w in signal.warnings:
                lines.append(f"  ⚠ {w}")
        
        return "\n".join(lines)
