"""
TradeBrain — Main Agent Loop (Coinbase Advanced Perps Edition)

Entry point. Startup sequence:
1. Load config
2. Connect to DB
3. Verify Coinbase API auth + futures provisioned
4. Run initial screener
5. Start FastAPI
6. Start position monitor
7. Enter signal loop
"""

import asyncio
import signal

import uvicorn
from loguru import logger

import config
from agent.api import app, set_agent_state
from agent.coinbase_client import CoinbaseClient
from agent.database import get_db
from agent.executor import Executor
from agent.indicator_engine import compute_indicators
from agent.position_monitor import PositionMonitor
from agent.risk_manager import RiskManager
from agent.screener import Screener
from agent.signal_engine import SignalEngine
from strategies import STRATEGIES


class TradeBrainAgent:

    def __init__(self):
        self.cfg = config.get_config()
        self.db = None
        self.cb = CoinbaseClient()
        self.executor = Executor(self.cb)
        self.risk = RiskManager()
        self.screener = Screener(self.cb)
        self.signal_engine = SignalEngine()
        self.monitor = PositionMonitor(self.executor, self.cb, self.risk)
        self.watchlist: list[str] = []
        self._shutdown = asyncio.Event()
        self._api_task = None

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    async def startup(self) -> None:
        logger.info("╔══════════════════════════════════════╗")
        logger.info("║      TradeBrain Starting [CB]      ║")
        logger.info("╚══════════════════════════════════════╝")

        self.db = await get_db()
        logger.info("✅ Database connected")

        ok = await self.cb.verify_auth()
        if not ok:
            logger.warning("⚠️  Coinbase auth failed — check COINBASE_API_KEY + SECRET")

        await self.cb.verify_futures_provisioned()

        logger.info("Running initial screener...")
        self.watchlist = await self.screener.run()
        logger.info(f"Watchlist ({len(self.watchlist)}): {self.watchlist}")

        set_agent_state(self.executor, self.risk, self.screener)
        self._api_task = asyncio.create_task(self._run_api())
        self.monitor.start()
        logger.info("✅ FastAPI + position monitor started")

    async def _run_api(self) -> None:
        cfg = uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="warning")
        server = uvicorn.Server(cfg)
        await server.serve()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        await self.startup()
        try:
            await self._loop()
        except asyncio.CancelledError:
            logger.info("Loop cancelled")
        finally:
            await self.shutdown()

    async def _loop(self) -> None:
        interval = self.cfg.default_signal_interval
        screener_counter = 0
        screener_interval = max(1, 4 * 3600 // interval)

        while not self._shutdown.is_set():
            try:
                await self.db.sync_config()
                await self.risk.sync()

                screener_counter += 1
                if screener_counter >= screener_interval:
                    screener_counter = 0
                    self.watchlist = await self.screener.run()

                for symbol in self.watchlist:
                    await self._evaluate(symbol)

                await asyncio.wait_for(self._shutdown.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue
            except Exception as exc:
                logger.error(f"Loop error: {exc}")
                await asyncio.sleep(5)

    async def _evaluate(self, product_id: str) -> None:
        if self.executor.has_position(product_id):
            return

        signal = type("Sig", (), {"direction": "none", "confidence": 0.0})()
        skip = self.risk.check_trade_allowed(signal, product_id)
        if skip and ("Manual pause" in skip or "Circuit breaker" in skip):
            return

        try:
            candles_15m = await self.cb.get_candles(product_id, "FIFTEEN_MINUTE")
            candles_1h  = await self.cb.get_candles(product_id, "ONE_HOUR")

            if len(candles_15m) < 30 or len(candles_1h) < 20:
                return

            indicators = compute_indicators(
                self._candles_to_df(candles_15m),
                self._candles_to_df(candles_1h),
            )

            strategy = STRATEGIES.get(self.cfg.default_strategy)
            if not strategy:
                return

            sig = await self.signal_engine.evaluate(product_id, strategy, indicators)
            skip = self.risk.check_trade_allowed(sig, product_id)
            if skip:
                logger.info(f"Skip {product_id}: {skip}")
                return

            entry = sig.entry_price or indicators["15m"]["price"]
            sl, tp, notional, margin = self.risk.calculate_trade_params(
                sig.direction, entry, indicators["15m"]["atr"]
            )
            if notional <= 0 or margin <= 0:
                return

            result = await self.executor.enter_position(
                symbol=product_id,
                direction=sig.direction,
                entry_price=entry,
                stop_loss=sl,
                take_profit=tp,
                size_usdc=notional,
                margin_usdc=margin,
                leverage=self.risk.state.leverage,
                risk_usdc=self.risk.state.balance_usdc * self.risk.state.risk_per_trade_pct,
                strategy=strategy.name,
                confidence=sig.confidence,
                reasoning=sig.reasoning,
            )
            if result.success:
                logger.info(f"✅ Position opened: {product_id} {sig.direction}")
            else:
                logger.warning(f"Failed to open {product_id}: {result.error}")

        except Exception as exc:
            logger.error(f"Error evaluating {product_id}: {exc}")

    @staticmethod
    def _candles_to_df(candles: list):
        import pandas as pd
        if not candles:
            return pd.DataFrame()
        df = pd.DataFrame([
            {"time": c.start, "open": c.open, "high": c.high,
             "low": c.low, "close": c.close, "volume": c.volume}
            for c in candles
        ])
        df["time"] = pd.to_datetime(df["time"], unit="s")
        return df.sort_values("time").reset_index(drop=True)

    async def shutdown(self) -> None:
        logger.info("Shutting down...")
        self._shutdown.set()
        self.monitor.stop()
        if self._api_task:
            self._api_task.cancel()
        await self.signal_engine.close()
        await self.cb.close()
        if self.db:
            await self.db.close()
        logger.info("Shutdown complete")

    def _sigint(self) -> None:
        self._shutdown.set()


def main() -> None:
    agent = TradeBrainAgent()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, agent._sigint)
    try:
        loop.run_until_complete(agent.run())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
