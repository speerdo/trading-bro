"""
Position Monitor — Background task that tracks open positions.

Checks every 30 seconds:
- Paper positions: simulate P&L against live mid prices
- Live positions: sync with exchange via fetch_positions()

On close (stop/TP hit or manual close):
- Update trade record in DB
- Update daily P&L for circuit breaker
- Trigger Burt notification
- Trigger memory formation
"""

import asyncio
from dataclasses import dataclass
from typing import Any

from loguru import logger

from agent.executor import Executor, PaperPosition
from agent.data_client import HyperliquidDataClient
from agent.database import get_db
from agent.risk_manager import RiskManager
import config


@dataclass
class PositionSnapshot:
    symbol: str
    direction: str
    entry_price: float
    current_price: float
    unrealized_pnl: float
    stop_loss: float
    take_profit: float
    status: str  # "open" | "stopped" | "taken_profit" | "closed"


class PositionMonitor:
    """Monitors open positions and handles exits."""

    CHECK_INTERVAL = 30  # seconds

    def __init__(self, executor: Executor, data_client: HyperliquidDataClient,
                 risk_manager: RiskManager):
        self.executor = executor
        self.client = data_client
        self.risk = risk_manager
        self.cfg = config.get_config()
        self._task: asyncio.Task | None = None
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background monitor task."""
        if self._task is not None and not self._task.done():
            logger.warning("PositionMonitor already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="position_monitor")
        logger.info("PositionMonitor started")

    def stop(self) -> None:
        """Stop the monitor."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("PositionMonitor stopped")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._check_all_positions()
            except Exception as exc:
                logger.error(f"PositionMonitor error: {exc}")
            await asyncio.sleep(self.CHECK_INTERVAL)

    async def _check_all_positions(self) -> None:
        """Check all open positions for exits."""
        positions = self.executor.get_open_positions()
        if not positions:
            return

        symbols = [p.symbol for p in positions]
        current_prices: dict[str, float] = {}

        # Fetch current mid prices via REST (lightweight)
        for symbol in symbols:
            try:
                price = self.client.get_current_price(symbol)
                if price is None:
                    # Fallback: fetch via candles
                    df = await self.client.get_candles(symbol, "1m", limit=1)
                    if not df.empty:
                        price = float(df.iloc[-1]["close"])
                if price:
                    current_prices[symbol] = price
            except Exception as exc:
                logger.warning(f"Failed to fetch price for {symbol}: {exc}")

        for pos in positions:
            price = current_prices.get(pos.symbol)
            if price is None:
                continue

            snapshot = self._evaluate_position(pos, price)

            if snapshot.status != "open":
                await self._handle_exit(pos, snapshot)
            else:
                logger.debug(
                    f"{pos.symbol} open @ {pos.entry_price:.2f} "
                    f"current={price:.2f} uP&L=${snapshot.unrealized_pnl:+.2f}"
                )

    def _evaluate_position(self, pos: PaperPosition, current_price: float) -> PositionSnapshot:
        """Determine if a position has hit stop or TP."""
        if pos.direction == "long":
            unrealized = (current_price - pos.entry_price) / pos.entry_price * pos.size_usdc
            if current_price <= pos.stop_loss:
                status = "stopped"
            elif current_price >= pos.take_profit:
                status = "taken_profit"
            else:
                status = "open"
        else:  # short
            unrealized = (pos.entry_price - current_price) / pos.entry_price * pos.size_usdc
            if current_price >= pos.stop_loss:
                status = "stopped"
            elif current_price <= pos.take_profit:
                status = "taken_profit"
            else:
                status = "open"

        return PositionSnapshot(
            symbol=pos.symbol,
            direction=pos.direction,
            entry_price=pos.entry_price,
            current_price=current_price,
            unrealized_pnl=unrealized,
            stop_loss=pos.stop_loss,
            take_profit=pos.take_profit,
            status=status,
        )

    async def _handle_exit(self, pos: PaperPosition, snapshot: PositionSnapshot) -> None:
        """Close a position and update records."""
        result = await self.executor.close_position(pos.symbol, snapshot.current_price)
        if not result.success:
            logger.error(f"Failed to close {pos.symbol}: {result.error}")
            return

        # Update risk manager daily loss
        pnl = snapshot.unrealized_pnl
        self.risk.apply_loss(pnl)

        # Trigger notifications (Burt or webhook)
        await self._notify_close(pos, snapshot)

        # Form memory (if Burt memory engine is available)
        await self._form_memory(pos, snapshot)

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    async def _notify_close(self, pos: PaperPosition, snapshot: PositionSnapshot) -> None:
        """Send close notification."""
        mode = "PAPER" if pos.is_paper else "LIVE"
        emoji = "🟢" if snapshot.unrealized_pnl >= 0 else "🔴"
        logger.info(
            f"{emoji} {mode} CLOSE: {pos.symbol} {pos.direction.upper()} "
            f"P&L=${snapshot.unrealized_pnl:+.2f} (exit={snapshot.status})"
        )
        # Actual Burt/webhook notification delegated to notifier module (Phase 13)

    # ------------------------------------------------------------------
    # Memory
    # ------------------------------------------------------------------

    async def _form_memory(self, pos: PaperPosition, snapshot: PositionSnapshot) -> None:
        """Form a memory from trade outcome for Burt."""
        try:
            db = await get_db()
            outcome = "win" if snapshot.unrealized_pnl >= 0 else "loss"
            memory_content = (
                f"{pos.direction.upper()} {pos.symbol} at {pos.entry_price:.2f}, "
                f"exited at {snapshot.current_price:.2f} with P&L ${snapshot.unrealized_pnl:+.2f}. "
                f"Strategy: {pos.strategy}, confidence: {pos.confidence:.2f}."
            )
            await db.store_memory(
                memory_type="lesson",
                content=memory_content,
                source="trade_outcome",
                symbol=pos.symbol,
                strategy=pos.strategy,
                importance=0.7 if abs(snapshot.unrealized_pnl) > pos.risk_usdc * 2 else 0.5,
            )
        except Exception as exc:
            logger.warning(f"Failed to form memory: {exc}")
