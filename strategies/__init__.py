"""
Strategy Registry

Import all strategies here so the UI and agent can discover them.
"""

from strategies.base import BaseStrategy
from strategies.rsi_macd import RsiMacdStrategy
from strategies.bollinger import BollingerStrategy
from strategies.ema_pullback import EmaPullbackStrategy
from strategies.donchian_breakout import DonchianBreakoutStrategy

STRATEGIES: dict[str, BaseStrategy] = {
    "rsi_macd": RsiMacdStrategy(),
    "bollinger": BollingerStrategy(),
    "ema_pullback": EmaPullbackStrategy(),
    "donchian_breakout": DonchianBreakoutStrategy(),
}

__all__ = ["STRATEGIES", "BaseStrategy"]
