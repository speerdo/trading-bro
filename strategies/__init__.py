"""
Strategy Registry

Import all strategies here so the UI and agent can discover them.
"""

from strategies.base import BaseStrategy
from strategies.rsi_macd import RsiMacdStrategy
from strategies.bollinger import BollingerStrategy
from strategies.ema_pullback import EmaPullbackStrategy

STRATEGIES: dict[str, BaseStrategy] = {
    "rsi_macd": RsiMacdStrategy(),
    "bollinger": BollingerStrategy(),
    "ema_pullback": EmaPullbackStrategy(),
}

__all__ = ["STRATEGIES", "BaseStrategy"]
