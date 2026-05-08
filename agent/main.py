"""
Main Agent Loop — Entry point for TradeBrain.

Startup sequence:
1. Load config
2. Connect to DB
3. Run DB migrations
4. Load markets (optional for live)
5. Verify API (optional for live)
6. Run initial screener
7. Start FastAPI server
8. Start position monitor
9. Start Burt (if token available)
10. Enter signal loop
"""

import asyncio
import signal
import sys
from contextlib import asynccontextmanager

import uvicorn
from loguru import logger

import config
from agent.api import app, set_agent_state
from agent.database import get_db
from agent.data_client import HyperliquidDataClient
from agent.executor import Executor
from agent.indicator_engine import compute_indicators
from agent.position_monitor import PositionMonitor
from agent.risk_manager import RiskManager
from agent.screener import Screener
from agent.signal_engine import SignalEngine
from strategies import STRATEGIES


class TradeBrainAgent:
    """Main trading agent orchestrator."""

    def __init__(self):
        self.cfg = config.get_config()
        self.db = None
        self.data_client = HyperliquidDataClient()
        self.executor = Executor()
        self.risk_manager = RiskManager()
        self.screener = Screener(self.data_client)
        self.signal_engine = SignalEngine()
        self.position_monitor = PositionMonitor(
            self.executor, self.data_client, self.risk_manager
        )
        self.watchlist: list[str] = []
        self._shutdown_event = asyncio.Event()
        self._api_server = None

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    async def startup(self) -> None:
        logger.info("╔══════════════════════════════════════╗")
        logger.info("║        TradeBrain Starting...        ║")
        logger.info("╚══════════════════════════════════════╝")

        # 1. Config loaded at import time
        logger.info(f"Config loaded — paper={self.cfg.paper_trading} strategy={self.cfg.default_strategy}")

        # 2. Connect to DB
        self.db = await get_db()
        logger.info("Database connected")

        # 3. Run migrations (idempotent)
        # Tables are already created by setup_db.py; this is a no-op if they exist
        logger.info("DB migrations complete (idempotent)")

        # 4. Verify live API if keys present
        if self.cfg.hl_wallet_address and self.cfg.hl_api_private_key:
            ok = await self.executor.verify_connectivity()
            if ok:
                logger.info("Live Hyperliquid API verified")
            else:
                logger.warning("Live Hyperliquid API connectivity failed — falling back to paper")
        else:
            logger.info("No live keys — paper mode only")

        # 5. Initial screener run
        logger.info("Running initial screener...")
        self.watchlist = await self.screener.run()
        logger.info(f"Watchlist: {self.watchlist}")

        # 6. Start FastAPI in background
        logger.info("Starting FastAPI server on localhost:8000")
        set_agent_state(self.executor, self.risk_manager, self.screener)
        self._api_server = asyncio.create_task(self._run_api())

        # 7. Start position monitor
        self.position_monitor.start()

        # 8. Start Burt (if token available)
        # Phase 12: Burt is built separately; we start it here if available
        self._burt_task = None
        try:
            from agent.burt import Burt
            if self.cfg.discord_bot_token:
                burt = Burt(self.db, self.executor, self.risk_manager)
                self._burt_task = asyncio.create_task(burt.start())
                logger.info("Burt Discord bot started")
            else:
                logger.info("No DISCORD_BOT_TOKEN — Burt disabled")
        except ImportError:
            logger.info("Burt module not found — skipping")

        # 9. Log startup
        logger.info("TradeBrain startup complete ✅")

    # ------------------------------------------------------------------
    # API server
    # ------------------------------------------------------------------

    async def _run_api(self) -> None:
        config_uvicorn = uvicorn.Config(
            app, host="127.0.0.1", port=8000, log_level="warning"
        )
        server = uvicorn.Server(config_uvicorn)
        await server.serve()

    # ------------------------------------------------------------------
    # Main signal loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        await self.startup()

        try:
            await self._signal_loop()
        except asyncio.CancelledError:
            logger.info("Signal loop cancelled")
        finally:
            await self.shutdown()

    async def _signal_loop(self) -> None:
        interval = self.cfg.default_signal_interval
        screener_counter = 0
        screener_interval = 4 * 3600 // interval  # every 4 hours

        logger.info(f"Entering signal loop (interval={interval}s)")

        while not self._shutdown_event.is_set():
            try:
                # Sync config from DB at top of each iteration
                await self.db.sync_config()
                await self.risk_manager.sync()

                # Re-run screener every N iterations
                screener_counter += 1
                if screener_counter >= screener_interval:
                    screener_counter = 0
                    logger.info("Re-running screener...")
                    self.watchlist = await self.screener.run()

                # Evaluate each coin in watchlist
                for symbol in self.watchlist:
                    await self._evaluate_symbol(symbol)

                # Wait for next iteration
                await asyncio.wait_for(
                    self._shutdown_event.wait(), timeout=interval
                )
            except asyncio.TimeoutError:
                continue
            except Exception as exc:
                logger.error(f"Signal loop error: {exc}")
                await asyncio.sleep(5)

    async def _evaluate_symbol(self, symbol: str) -> None:
        """Full pipeline for one symbol: data → indicators → signal → risk → execute."""
        # Skip if already in a position
        if self.executor.has_position(symbol):
            return

        # Skip if circuit breaker active
        skip_reason = self.risk_manager.check_trade_allowed(
            type("Signal", (), {"direction": "placeholder", "confidence": 0.0})(), symbol
        )
        if skip_reason and "Manual pause" in skip_reason or "Circuit breaker" in skip_reason:
            return

        try:
            # Fetch data
            df_15m = await self.data_client.get_candles(symbol, "15m", limit=100)
            df_1h = await self.data_client.get_candles(symbol, "1h", limit=100)
            if len(df_15m) < 30 or len(df_1h) < 30:
                return

            indicators = compute_indicators(df_15m, df_1h)
            strategy = STRATEGIES.get(self.cfg.default_strategy)
            if strategy is None:
                return

            # Evaluate signal
            signal = await self.signal_engine.evaluate(symbol, strategy, indicators)

            # Risk check
            skip_reason = self.risk_manager.check_trade_allowed(signal, symbol)
            if skip_reason:
                logger.info(f"Skipping {symbol}: {skip_reason}")
                return

            # Compute trade params
            sl, tp, notional, margin = self.risk_manager.calculate_trade_params(
                signal.direction, signal.entry_price or indicators["15m"]["price"],
                indicators["15m"]["atr"],
            )

            if notional <= 0 or margin <= 0:
                logger.warning(f"Invalid position size for {symbol}")
                return

            # Execute
            result = await self.executor.enter_position(
                symbol=symbol,
                direction=signal.direction,
                entry_price=signal.entry_price or indicators["15m"]["price"],
                stop_loss=sl,
                take_profit=tp,
                size_usdc=notional,
                margin_usdc=margin,
                leverage=self.risk_manager.state.leverage,
                risk_usdc=self.risk_manager.state.balance_usdc * self.risk_manager.state.risk_per_trade_pct,
                signal_id=None,  # Could fetch from DB if needed
                strategy=strategy.name,
                confidence=signal.confidence,
                reasoning=signal.reasoning,
            )

            if result.success:
                logger.info(f"✅ Position opened: {symbol} {signal.direction}")
            else:
                logger.warning(f"Failed to open {symbol}: {result.error}")

        except Exception as exc:
            logger.error(f"Error evaluating {symbol}: {exc}")

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def shutdown(self) -> None:
        logger.info("Shutting down TradeBrain...")
        self._shutdown_event.set()
        self.position_monitor.stop()
        if self._api_server:
            self._api_server.cancel()
        if self._burt_task:
            self._burt_task.cancel()
        await self.signal_engine.close()
        await self.data_client.close()
        if self.db:
            await self.db.close()
        logger.info("TradeBrain shutdown complete")

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    def _handle_sigint(self) -> None:
        logger.info("SIGINT received, shutting down...")
        self._shutdown_event.set()


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def main() -> None:
    agent = TradeBrainAgent()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Handle Ctrl+C
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, agent._handle_sigint)

    try:
        loop.run_until_complete(agent.run())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
