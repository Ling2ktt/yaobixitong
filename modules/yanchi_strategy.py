from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger


class SignalType(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class StrategySignal:
    signal: SignalType
    symbol: str
    price: float
    timestamp: datetime
    score: float = 0.0
    reason: str = ""
    stop_price: Optional[float] = None
    take_profit_levels: List[float] = field(default_factory=list)
    leverage: float = 1.0
    confidence: float = 0.0
    position_size: float = 0.0
    risk_percent: float = 0.02
    confluence_breakdown: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.signal.value,
            "symbol": self.symbol,
            "price": self.price,
            "stop_loss": self.stop_price,
            "take_profit": self.take_profit_levels,
            "leverage": self.leverage,
            "confidence": self.confidence,
            "reason": self.reason,
            "score": self.score,
            "position_size": self.position_size,
            "risk_percent": self.risk_percent,
            "confluence_breakdown": self.confluence_breakdown,
            "timestamp": self.timestamp.isoformat(),
        }


class YanChiStrategy:
    """颜驰合约策略：纯代码版，偏向突破-回踩共振。"""

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        params = params or {}
        self.min_confluence_score = float(params.get("min_confluence_score", 4.5))
        self.fixed_position_usdt = float(params.get("fixed_position_usdt", 10.0))
        self.leverage = float(params.get("leverage", 1.0))
        self.min_rr_ratio = float(params.get("min_rr_ratio", 2.0))
        self.ema_fast = int(params.get("ema_fast", 20))
        self.ema_slow = int(params.get("ema_slow", 50))
        self.atr_len = int(params.get("atr_len", 14))
        self.structure_lookback = int(params.get("structure_lookback", 40))
        self.volume_lookback = int(params.get("volume_lookback", 20))
        self.retest_atr_buffer = float(params.get("retest_atr_buffer", 0.25))
        self.extended_atr_limit = float(params.get("extended_atr_limit", 2.5))

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy().reset_index(drop=True)
        df["ema_fast"] = df["close"].ewm(span=self.ema_fast, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=self.ema_slow, adjust=False).mean()
        df["atr"] = self._atr(df, self.atr_len)
        df["vol_ma"] = df["volume"].rolling(self.volume_lookback).mean()
        df["range"] = (df["high"] - df["low"]).replace(0, np.nan)
        df["body"] = (df["close"] - df["open"]).abs()
        df["body_ratio"] = (df["body"] / df["range"]).fillna(0.0)
        df["upper_wick"] = df["high"] - df[["open", "close"]].max(axis=1)
        df["lower_wick"] = df[["open", "close"]].min(axis=1) - df["low"]
        df["structure_high"] = df["high"].rolling(self.structure_lookback).max().shift(1)
        df["structure_low"] = df["low"].rolling(self.structure_lookback).min().shift(1)
        return df

    def generate_signal(
        self,
        df: pd.DataFrame,
        symbol: str = "BTC/USDT",
        df_1h: Optional[pd.DataFrame] = None,
        df_4h: Optional[pd.DataFrame] = None,
    ) -> StrategySignal:
        source = self._select_source_df(df, df_1h=df_1h, df_4h=df_4h)
        if source is None or len(source) < max(self.ema_slow, self.structure_lookback, self.volume_lookback) + 5:
            price = float(source["close"].iloc[-1]) if source is not None and len(source) else 0.0
            return StrategySignal(
                signal=SignalType.HOLD,
                symbol=symbol,
                price=price,
                timestamp=datetime.now(),
                reason="K线数据不足",
            )

        df_ind = self.calculate_indicators(source)
        latest = df_ind.iloc[-1]
        prev = df_ind.iloc[-2]

        if any(pd.isna(latest[col]) for col in ("ema_fast", "ema_slow", "atr", "structure_high", "structure_low")):
            return StrategySignal(
                signal=SignalType.HOLD,
                symbol=symbol,
                price=float(latest["close"]),
                timestamp=datetime.now(),
                reason="指标未就绪",
            )

        price = float(latest["close"])
        atr = float(latest["atr"]) if float(latest["atr"]) > 0 else 0.0
        if atr <= 0:
            return StrategySignal(
                signal=SignalType.HOLD,
                symbol=symbol,
                price=price,
                timestamp=datetime.now(),
                reason="ATR无效",
            )

        if abs(price - float(latest["ema_fast"])) > atr * self.extended_atr_limit:
            return StrategySignal(
                signal=SignalType.HOLD,
                symbol=symbol,
                price=price,
                timestamp=datetime.now(),
                reason="价格过度偏离均线",
            )

        long_score, long_notes = self._score_direction(df_ind, "long")
        short_score, short_notes = self._score_direction(df_ind, "short")

        if abs(long_score - short_score) < 1e-9:
            return StrategySignal(
                signal=SignalType.HOLD,
                symbol=symbol,
                price=price,
                timestamp=datetime.now(),
                score=long_score,
                reason="多空打平，等待更清晰的共振",
                leverage=self.leverage,
            )

        direction = "long" if long_score > short_score else "short"
        score = max(long_score, short_score)
        notes = long_notes if direction == "long" else short_notes
        if score < self.min_confluence_score:
            return StrategySignal(
                signal=SignalType.HOLD,
                symbol=symbol,
                price=price,
                timestamp=datetime.now(),
                score=score,
                reason="共振分不足",
                leverage=self.leverage,
                confluence_breakdown={"long": long_score, "short": short_score},
            )

        stop_price = self._build_stop(df_ind, direction)
        take_profit_levels = self._build_take_profits(price, stop_price, direction)
        risk = abs(price - stop_price)
        rr = (abs(take_profit_levels[0] - price) / risk) if risk > 0 and take_profit_levels else 0.0
        if rr < self.min_rr_ratio:
            return StrategySignal(
                signal=SignalType.HOLD,
                symbol=symbol,
                price=price,
                timestamp=datetime.now(),
                score=score,
                reason="盈亏比不足",
                leverage=self.leverage,
                confluence_breakdown={"long": long_score, "short": short_score},
            )

        position_size = (self.fixed_position_usdt * self.leverage) / price if price > 0 else 0.0
        confidence = min(score / 10.0, 1.0)
        return StrategySignal(
            signal=SignalType.BUY if direction == "long" else SignalType.SELL,
            symbol=symbol,
            price=price,
            timestamp=datetime.now(),
            score=score,
            reason=" | ".join(notes),
            stop_price=stop_price,
            take_profit_levels=take_profit_levels,
            leverage=self.leverage,
            confidence=confidence,
            position_size=position_size,
            risk_percent=min(risk / price, 1.0) if price > 0 else 0.02,
            confluence_breakdown={"long": long_score, "short": short_score},
        )

    def _select_source_df(
        self,
        df: pd.DataFrame,
        df_1h: Optional[pd.DataFrame] = None,
        df_4h: Optional[pd.DataFrame] = None,
    ) -> Optional[pd.DataFrame]:
        candidates = [df_1h, df_4h, df]
        for candidate in candidates:
            if candidate is not None and len(candidate) > 0:
                return candidate
        return None

    def _score_direction(self, df: pd.DataFrame, direction: str) -> Tuple[float, List[str]]:
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        atr = float(latest["atr"])
        score = 0.0
        notes: List[str] = []

        if direction == "long":
            trend_ok = latest["ema_fast"] > latest["ema_slow"] and latest["close"] > latest["ema_fast"]
            breakout_ok = latest["close"] > latest["structure_high"]
            retest_ok = (
                prev["low"] <= float(latest["structure_high"]) + atr * self.retest_atr_buffer
                and latest["close"] >= float(latest["structure_high"])
            )
            volume_ok = latest["volume"] >= (latest["vol_ma"] or latest["volume"])
            candle_ok = latest["close"] > latest["open"] and latest["body_ratio"] >= 0.55
            momentum_ok = latest["close"] > prev["close"]

            if trend_ok:
                score += 1.5
                notes.append("均线多头排列")
            if breakout_ok:
                score += 1.0
                notes.append("突破结构高点")
            if retest_ok:
                score += 1.0
                notes.append("回踩确认")
            if volume_ok:
                score += 0.8
                notes.append("量能确认")
            if candle_ok:
                score += 0.8
                notes.append("强势阳线")
            if momentum_ok:
                score += 0.4
                notes.append("收盘动能向上")
            if latest["high"] > prev["high"] and latest["low"] > prev["low"]:
                score += 0.5
                notes.append("高低点抬升")
        else:
            trend_ok = latest["ema_fast"] < latest["ema_slow"] and latest["close"] < latest["ema_fast"]
            breakout_ok = latest["close"] < latest["structure_low"]
            retest_ok = (
                prev["high"] >= float(latest["structure_low"]) - atr * self.retest_atr_buffer
                and latest["close"] <= float(latest["structure_low"])
            )
            volume_ok = latest["volume"] >= (latest["vol_ma"] or latest["volume"])
            candle_ok = latest["close"] < latest["open"] and latest["body_ratio"] >= 0.55
            momentum_ok = latest["close"] < prev["close"]

            if trend_ok:
                score += 1.5
                notes.append("均线空头排列")
            if breakout_ok:
                score += 1.0
                notes.append("跌破结构低点")
            if retest_ok:
                score += 1.0
                notes.append("反抽确认")
            if volume_ok:
                score += 0.8
                notes.append("量能确认")
            if candle_ok:
                score += 0.8
                notes.append("强势阴线")
            if momentum_ok:
                score += 0.4
                notes.append("收盘动能向下")
            if latest["high"] < prev["high"] and latest["low"] < prev["low"]:
                score += 0.5
                notes.append("高低点下移")

        return score, notes

    def _build_stop(self, df: pd.DataFrame, direction: str) -> float:
        latest = df.iloc[-1]
        atr = float(latest["atr"])
        if direction == "long":
            swing_low = float(df["low"].tail(8).min())
            return min(swing_low, float(latest["close"]) - atr * 1.5)
        swing_high = float(df["high"].tail(8).max())
        return max(swing_high, float(latest["close"]) + atr * 1.5)

    def _build_take_profits(self, price: float, stop_price: float, direction: str) -> List[float]:
        risk = abs(price - stop_price)
        if risk <= 0:
            return []

        if direction == "long":
            return [
                round(price + risk * 2.0, 8),
                round(price + risk * 3.0, 8),
                round(price + risk * 4.0, 8),
            ]
        return [
            round(price - risk * 2.0, 8),
            round(price - risk * 3.0, 8),
            round(price - risk * 4.0, 8),
        ]

    def _atr(self, df: pd.DataFrame, length: int) -> pd.Series:
        high = df["high"]
        low = df["low"]
        close = df["close"]
        prev_close = close.shift(1)
        tr = pd.concat(
            [
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        return tr.rolling(length).mean()
