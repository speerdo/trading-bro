"""
Executor — Order placement + paper trading

Paper mode: simulate in-memory positions against Coinbase mark prices.
Live mode: place real orders via /api/v3/brokerage/orders.
"""

import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

import config
from agent.coinbase_client import CoinbaseClient
from agent.database import get_db


@dataclass
class PaperPosition:
    product_id: str
    display_name: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    size_usdc: float
    margin_usdc: float
    leverage: int
    risk_usdc: float
    opened_at: float = field(default_factory=time.time)
    strategy: str = ""
    confidence: float = 0.0
    reasoning: str = ""
    is_paper: bool = True
    status: str = "open"
    exit_price: float | None = None
    pnl_usdc: float = 0.0
    tax_treatment: str = "1256"
    product_type: str = "perp"

    @property
    def symbol(self) -> str:
        """Backward-compat alias — `product_id` is the canonical exchange identifier."""
        return self.product_id


@dataclass
class OrderResult:
    success: bool
    order_id: str | None = None
    error: str = ""
    filled_price: float | None = None


class Executor:

    def __init__(self, cb: CoinbaseClient):
        self.cfg = config.get_config()
        self.cb = cb
        self.paper_positions: dict[str, PaperPosition] = {}
        self.live_positions: dict[str, Any] = {}
        self._notifier: Any = None

    def set_notifier(self, notifier: Any) -> None:
        self._notifier = notifier

    async def enter_position(
        self, symbol: str, direction: str, entry_price: float,
        stop_loss: float, take_profit: float, size_usdc: float,
        margin_usdc: float, leverage: int, risk_usdc: float,
        strategy: str = "", confidence: float = 0.0, reasoning: str = "",
        display_name: str = "", product_type: str = "perp",
    ) -> OrderResult:
        if self.cfg.paper_trading:
            return await self._enter_paper(
                symbol, display_name or symbol, direction, entry_price,
                stop_loss, take_profit, size_usdc, margin_usdc, leverage,
                risk_usdc, strategy, confidence, reasoning
            )
        return await self._enter_live(symbol, direction, entry_price, stop_loss, ...)

    async def _enter_paper(self, product_id: str, display_name: str, direction: str,
                            entry_price: float, stop_loss: float, take_profit: float,
                            size_usdc: float, margin_usdc: float, leverage: int,
                            risk_usdc: float, strategy: str, confidence: float,
                            reasoning: str) -> OrderResult:
        if product_id in self.paper_positions:
            return OrderResult(success=False, error=f"Already open: {product_id}")
        pos = PaperPosition(
            product_id=product_id, display_name=display_name, direction=direction,
            entry_price=entry_price, stop_loss=stop_loss, take_profit=take_profit,
            size_usdc=size_usdc, margin_usdc=margin_usdc, leverage=leverage,
            risk_usdc=risk_usdc, strategy=strategy, confidence=confidence,
            reasoning=reasoning, is_paper=True,
        )
        self.paper_positions[product_id] = pos
        logger.info(
            f"📄 PAPER ENTRY: {direction.upper()} {display_name} @ {entry_price:.2f}"
        )
        await self._log_trade(pos, order_id="")
        if self._notifier:
            try:
                await self._notifier.notify_trade_opened(
                    display_name, direction, entry_price, size_usdc,
                    leverage, stop_loss, take_profit,
                )
            except Exception as exc:
                logger.warning(f"Entry notification failed: {exc}")
        return OrderResult(success=True, order_id=f"paper-{product_id}")

    async def close_position(self, product_id: str, exit_price: float | None = None) -> OrderResult:
        pos = self.paper_positions.get(product_id)
        if pos is None:
            return OrderResult(success=False, error=f"No position: {product_id}")
        if exit_price is None:
            exit_price = pos.entry_price
        pnl = self._calc_pnl(pos, exit_price)
        pos.exit_price = exit_price
        pos.pnl_usdc = pnl
        pos.status = "closed"
        del self.paper_positions[product_id]
        logger.info(f"📄 PAPER CLOSE: {pos.display_name} @ {exit_price:.2f} P&L=${pnl:+.2f}")
        await self._update_trade_close(pos)
        if self._notifier:
            try:
                await self._notifier.notify_trade_closed(
                    pos.display_name, pos.direction, pos.entry_price,
                    exit_price, pnl,
                )
            except Exception as exc:
                logger.warning(f"Close notification failed: {exc}")
        return OrderResult(success=True, filled_price=exit_price)

    @staticmethod
    def _calc_pnl(pos: PaperPosition, current: float) -> float:
        if pos.direction == "long":
            return (current - pos.entry_price) / pos.entry_price * pos.size_usdc
        return (pos.entry_price - current) / pos.entry_price * pos.size_usdc

    def get_open_positions(self) -> list[PaperPosition]:
        return list(self.paper_positions.values())

    def has_position(self, product_id: str) -> bool:
        return product_id in self.paper_positions

    def get_position(self, product_id: str) -> PaperPosition | None:
        return self.paper_positions.get(product_id)

    async def _log_trade(self, pos: PaperPosition, order_id: str) -> None:
        try:
            db = await get_db()
            await db.log_trade({
                "symbol": pos.display_name,
                "direction": pos.direction,
                "strategy": pos.strategy,
                "confidence": pos.confidence,
                "entry_price": pos.entry_price,
                "stop_loss": pos.stop_loss,
                "take_profit": pos.take_profit,
                "size_usdc": pos.size_usdc,
                "margin_usdc": pos.margin_usdc,
                "leverage": pos.leverage,
                "risk_usdc": pos.risk_usdc,
                "is_paper": pos.is_paper,
                "status": "open",
                "reasoning": pos.reasoning,
                "order_id": order_id,
                "product_id": pos.product_id,
                "display_name": pos.display_name,
                "tax_treatment": pos.tax_treatment,
                "product_type": pos.product_type,
            })
        except Exception as exc:
            logger.warning(f"Failed to log trade: {exc}")

    async def _update_trade_close(self, pos: PaperPosition) -> None:
        try:
            db = await get_db()
            row = await db.fetchrow(
                "SELECT id FROM trades WHERE product_id = $1 AND status = 'open' ORDER BY created_at DESC LIMIT 1",
                pos.product_id,
            )
            if row:
                await db.close_trade(row["id"], pos.exit_price or 0, pos.pnl_usdc, "closed")
        except Exception as exc:
            logger.warning(f"Failed to update close: {exc}")
