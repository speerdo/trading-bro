"""
Position Monitor — tracks open positions and handles exits.

Checks every 30 seconds:
- Paper positions: compare to current mark price
- Live positions: sync with /cfm/positions
"""

import asyncio
from dataclasses import dataclass
from typing import Any

from loguru import logger

from agent.executor import Executor, PaperPosition
from agent.coinbase_client import CoinbaseClient
from agent.database import get_db
from agent.risk_manager import RiskManager
import config


@dataclass
class PositionSnapshot:
    product_id: str
    direction: str
    entry_price: float
    current_price: float
    unrealized_pnl: float
    stop_loss: float
    take_profit: float
    status: str


class PositionMonitor:

    CHECK_INTERVAL = 30

    def __init__(self, executor: Executor, cb: CoinbaseClient, risk: RiskManager):
        self.executor = executor
        self.cb = cb
        self.risk = risk
        self._task: asyncio.Task | None = None
        self._running = False

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="position_monitor")
        logger.info("PositionMonitor started")

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._check()
            except Exception as exc:
                logger.error(f"PositionMonitor error: {exc}")
            await asyncio.sleep(self.CHECK_INTERVAL)

    async def _check(self) -> None:
        positions = self.executor.get_open_positions()
        if not positions:
            return

        prices: dict[str, float] = {}
        for pos in positions:
            try:
                details = await self.cb.hydrate_product_details(pos.product_id)
                prices[pos.product_id] = details.get("mark_price") or pos.entry_price
            except Exception:
                prices[pos.product_id] = pos.entry_price

        for pos in positions:
            price = prices.get(pos.product_id)
            if not price:
                continue
            snap = self._evaluate(pos, price)
            if snap.status != "open":
                await self._handle_exit(pos, snap)
            else:
                logger.debug(f"{pos.display_name} open @ {pos.entry_price:.2f} "
                            f"current={price:.2f} uP&L=${snap.unrealized_pnl:+.2f}")

    def _evaluate(self, pos: PaperPosition, price: float) -> PositionSnapshot:
        if pos.direction == "long":
            pnl = (price - pos.entry_price) / pos.entry_price * pos.size_usdc
            status = "stopped" if price <= pos.stop_loss else \
                     "taken_profit" if price >= pos.take_profit else "open"
        else:
            pnl = (pos.entry_price - price) / pos.entry_price * pos.size_usdc
            status = "stopped" if price >= pos.stop_loss else \
                     "taken_profit" if price <= pos.take_profit else "open"

        return PositionSnapshot(
            product_id=pos.product_id, direction=pos.direction,
            entry_price=pos.entry_price, current_price=price,
            unrealized_pnl=pnl, stop_loss=pos.stop_loss,
            take_profit=pos.take_profit, status=status,
        )

    async def _handle_exit(self, pos: PaperPosition, snap: PositionSnapshot) -> None:
        result = await self.executor.close_position(pos.product_id, snap.current_price)
        if not result.success:
            logger.error(f"Failed to close {pos.product_id}: {result.error}")
            return
        self.risk.apply_loss(snap.unrealized_pnl)
        await self._notify(pos, snap)

    async def _notify(self, pos: PaperPosition, snap: PositionSnapshot) -> None:
        mode = "PAPER" if pos.is_paper else "LIVE"
        emoji = "🟢" if snap.unrealized_pnl >= 0 else "🔴"
        logger.info(f"{emoji} {mode} CLOSE: {pos.display_name} "
                   f"P&L=${snap.unrealized_pnl:+.2f}")
