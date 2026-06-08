"""
三位一体策略模块 (PA + SMC + Wyckoff)

使用方法:
    from modules.trinity_engine import TrinityEngine
    
    engine = TrinityEngine(config={
        "risk_per_trade": 0.02,
        "leverage": 5,
        "max_positions": 2
    })
    
    signal = engine.analyze(
        df_dict={'daily': df_d, '4h': df_4h, '1h': df_1h, '15m': df_15m},
        symbol="BTCUSDT",
        account_balance=100.0
    )
    
    print(engine.get_status_report(signal))
"""

from .trinity_engine import TrinityEngine, TrinitySignal
from .trinity_wyckoff import WyckoffAnalyzer, WyckoffSignal
from .trinity_smc import SMCAnalyzer, SMCSignal
from .trinity_pa import PAAnalyzer, PASignal
from .yanchi_strategy import YanChiStrategy, StrategySignal as YanChiSignal, SignalType as YanChiSignalType
from .strategy_factory import normalize_strategy_mode, build_strategy

__all__ = [
    "TrinityEngine",
    "TrinitySignal",
    "WyckoffAnalyzer",
    "WyckoffSignal",
    "SMCAnalyzer",
    "SMCSignal",
    "PAAnalyzer",
    "PASignal",
    "YanChiStrategy",
    "YanChiSignal",
    "YanChiSignalType",
    "normalize_strategy_mode",
    "build_strategy",
]
