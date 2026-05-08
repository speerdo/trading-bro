"""
Base strategy ABC + shared dataclasses.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class SignalResult:
    """Output of strategy.parse_response() — a single signal evaluation."""
    direction: str            # "long" | "short" | "none"
    confidence: float = 0.0   # 0.0 - 1.0
    reasoning: str = ""       # max 150 chars
    entry_price: float | None = None
    invalidation: str = ""    # what would cancel the setup


class BaseStrategy(ABC):
    """
    Abstract base for all TradeBrain strategies.

    A strategy defines:
      - build_prompt(indicators) -> str     (injected into the LLM prompt)
      - parse_response(resp, indicators) -> SignalResult
    """

    name: str = ""
    description: str = ""

    @abstractmethod
    def build_prompt(self, indicators: dict, symbol: str) -> str:
        """Return the strategy-specific prompt fragment for Kimi K2.6."""
        ...

    @abstractmethod
    def parse_response(self, response: dict, indicators: dict) -> SignalResult:
        """Parse the LLM JSON response into a SignalResult."""
        ...

    def fallback_signal(self) -> SignalResult:
        """Return a default "none" signal (used on parse failure)."""
        return SignalResult(
            direction="none",
            confidence=0.0,
            reasoning="Parse failure or no clear setup",
            invalidation="",
        )
