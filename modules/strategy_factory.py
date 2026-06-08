from __future__ import annotations

from typing import Any, Dict, Optional

from .strategy_quant_trend import QuantTrendStrategy
from .strategy_trinity import TrinityStrategy
from .yanchi_strategy import YanChiStrategy


_MODE_ALIASES = {
    "ai": "ai",
    "rule": "rule",
    "quant": "rule",
    "quanttrend": "rule",
    "quant_trend": "rule",
    "trinity": "trinity",
    "三位一体": "trinity",
    "wyckoff_smc": "wyckoff_smc",
    "wyckoffsmc": "wyckoff_smc",
    "yanchi": "yanchi",
    "yanchi": "yanchi",
    "yanchibit": "yanchi",
    "颜驰": "yanchi",
    "颜驰bit": "yanchi",
    "颜驰交易策略": "yanchi",
    "ai_external": "ai_external",
    "aiexternal": "ai_external",
}


def normalize_strategy_mode(mode: Any) -> str:
    if mode is None:
        return "ai"

    normalized = str(mode).strip().lower()
    if not normalized:
        return "ai"

    lookup = normalized.replace(" ", "").replace("-", "").replace("_", "")
    if normalized in _MODE_ALIASES:
        return _MODE_ALIASES[normalized]
    if lookup in _MODE_ALIASES:
        return _MODE_ALIASES[lookup]

    return normalized


def build_strategy(mode: Any, config: Optional[Dict[str, Any]] = None):
    normalized = normalize_strategy_mode(mode)
    config = config or {}

    if normalized == "rule":
        return QuantTrendStrategy(config)
    if normalized == "trinity":
        return TrinityStrategy(config)
    if normalized == "yanchi":
        return YanChiStrategy(config)

    return None
