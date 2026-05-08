"""
Risk Manager — Position sizing, stops, circuit breaker.
"""

import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

import config


@dataclass
class RiskParams:
    """Dynamic risk state (updated from DB hot-reload)."""
    balance_usdc: float = 100_000.0  # default dummy balance (updated in real run)
    leverage: int = 3
    risk_per_trade_pct: float = 0.01
    daily_loss_limit_pct: float = 0.05
    atr_multiplier: float = 1.5
    take_profit_rr: float = 2.0
    fixed_stop_pct: float = 0.02
    stop_loss_method: str = "atr"
    min_confidence: float = 0.65
    circuit_breaker_active: bool = False
    daily_loss_usdc: float = 0.0
    manual_pause: bool = False


def compute_position_size(entry_price: float, stop_price: float,
                          balance: float, risk_pct: float, leverage: int) -> tuple[float, float, float]:
    """
    Returns (notional_size, margin_required, risk_usdc).
    Safety cap: margin <= 20% of balance.
    """
    risk_dollars = balance * risk_pct
    stop_distance_pct = abs(entry_price - stop_price) / entry_price
    if stop_distance_pct <= 0:
        logger.warning("Stop distance is zero — cannot compute position size")
        return 0.0, 0.0, 0.0

    notional_size = risk_dollars / stop_distance_pct
    margin_required = notional_size / leverage

    # Safety cap: max 20% of balance
    max_margin = balance * 0.20
    if margin_required > max_margin:
        scale = max_margin / margin_required
        notional_size *= scale
        risk_dollars *= scale
        margin_required = notional_size / leverage
        logger.warning(f"Position scaled down to fit 20% margin cap: margin=${margin_required:.2f}")

    return notional_size, margin_required, risk_dollars


def compute_stops(entry_price: float, atr: float | None,
                  fixed_pct: float = 0.02,
                  atr_mult: float = 1.5,
                  method: str = "atr",
                  rr: float = 2.0,
                  direction: str = "long") -> tuple[float, float]:
    """
    Returns (stop_loss_price, take_profit_price).
    """
    if method == "atr" and atr is not None and atr > 0:
        stop_distance = atr * atr_mult
    else:
        stop_distance = entry_price * fixed_pct

    if direction == "long":
        sl = entry_price - stop_distance
        tp = entry_price + (stop_distance * rr)
    else:
        sl = entry_price + stop_distance
        tp = entry_price - (stop_distance * rr)

    return sl, tp


class RiskManager:
    """Tracks daily loss, circuit breaker, and validates trades."""

    def __init__(self):
        self.cfg = config.get_config()
        self.state = RiskParams(
            leverage=self.cfg.default_leverage,
            risk_per_trade_pct=self.cfg.default_risk_per_trade,
            daily_loss_limit_pct=self.cfg.default_daily_loss_limit,
        )
        self._last_reset_day = int(time.time() / 86400)

    # ------------------------------------------------------------------
    # Sync from DB / UI
    # ------------------------------------------------------------------

    async def sync(self) -> None:
        """Called at top of each signal loop iteration."""
        from agent.database import get_db
        try:
            db = await get_db()
            keys = [
                "leverage", "risk_per_trade", "daily_loss_limit",
                "atr_multiplier", "take_profit_rr", "stop_loss_method",
                "min_confidence",
            ]
            for key in keys:
                val = await db.get_config_value(key)
                if val is not None:
                    setattr(self.state, key, self._coerce(key, val))
        except Exception as exc:
            logger.warning(f"RiskManager sync failed: {exc}")

        # Midnight UTC circuit breaker reset
        current_day = int(time.time() / 86400)
        if current_day > self._last_reset_day:
            self.state.daily_loss_usdc = 0.0
            self.state.circuit_breaker_active = False
            self._last_reset_day = current_day
            logger.info("Circuit breaker auto-reset (midnight UTC)")

    @staticmethod
    def _coerce(key: str, val: str) -> Any:
        if key in ("leverage",):
            return int(val)
        if key in ("risk_per_trade", "daily_loss_limit", "atr_multiplier",
                   "take_profit_rr", "min_confidence"):
            return float(val)
        return val

    # ------------------------------------------------------------------
    # Pre-trade checks
    # ------------------------------------------------------------------

    def check_trade_allowed(self, signal: Any, symbol: str) -> str:
        """Returns empty string if allowed, otherwise returns skip reason."""
        if self.state.manual_pause:
            return "Manual pause is active"
        if self.state.circuit_breaker_active:
            return "Circuit breaker active"
        if signal.direction == "none":
            return "No directional signal"
        if signal.confidence < self.state.min_confidence:
            return f"Confidence {signal.confidence:.2f} < {self.state.min_confidence}"
        return ""

    # ------------------------------------------------------------------
    # Circuit breaker
    # ------------------------------------------------------------------

    def apply_loss(self, pnl_usdc: float) -> None:
        if pnl_usdc >= 0:
            return
        self.state.daily_loss_usdc += abs(pnl_usdc)
        limit = self.state.balance_usdc * self.state.daily_loss_limit_pct
        if self.state.daily_loss_usdc >= limit:
            self.state.circuit_breaker_active = True
            logger.error(
                f"╔══════════════════════════════════════╗\n"
                f"║   CIRCUIT BREAKER TRIGGERED          ║\n"
                f"║   Daily loss: ${self.state.daily_loss_usdc:.2f} >= ${limit:.2f}   ║\n"
                f"╚══════════════════════════════════════╝"
            )

    def reset_circuit_breaker(self) -> None:
        self.state.daily_loss_usdc = 0.0
        self.state.circuit_breaker_active = False
        logger.info("Circuit breaker MANUALLY reset")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def calculate_trade_params(self, direction: str, entry_price: float,
                               atr: float | None) -> tuple[float, float, float, float]:
        """Returns (stop_loss, take_profit, notional_size, margin_required)."""
        sl, tp = compute_stops(
            entry_price, atr,
            atr_mult=self.state.atr_multiplier,
            fixed_pct=self.state.fixed_stop_pct,
            method=self.state.stop_loss_method,
            rr=self.state.take_profit_rr,
            direction=direction,
        )
        notional, margin, risk = compute_position_size(
            entry_price, sl,
            self.state.balance_usdc,
            self.state.risk_per_trade_pct,
            self.state.leverage,
        )
        return sl, tp, notional, margin
