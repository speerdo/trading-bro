"""
Hyperliquid Data Client

Handles REST + WebSocket for market data.
Also includes optional MoonDev API fallback.
"""

import asyncio
import json
import time
from typing import Any

import aiohttp
import pandas as pd
import websockets
from loguru import logger

import config

HL_REST_URL = "https://api.hyperliquid.xyz/info"
HL_WS_URL = "wss://api.hyperliquid.xyz/ws"
MOONDEV_BASE = "https://api.moondev.com"


class HyperliquidDataClient:
    """Async client for Hyperliquid market data."""

    def __init__(self):
        self.cfg = config.get_config()
        self.session: aiohttp.ClientSession | None = None
        self._all_mids: dict[str, str] = {}
        self._candle_buffers: dict[str, list[dict]] = {}
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._ws_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def _ensure_session(self) -> None:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            )

    async def close(self) -> None:
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()
        if self.session:
            await self.session.close()
            self.session = None
        logger.info("Data client closed")

    # ------------------------------------------------------------------
    # REST helpers
    # ------------------------------------------------------------------

    async def post_info(self, payload: dict) -> Any:
        """Generic POST to HL /info endpoint."""
        await self._ensure_session()
        async with self.session.post(HL_REST_URL, json=payload) as resp:
            resp.raise_for_status()
            return await resp.json()

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    async def get_meta_and_asset_ctxs(self) -> tuple[list[dict], list[dict]]:
        """
        Fetch all perpetual markets + current context.
        Returns (universe list, asset_ctxs list).
        """
        data = await self.post_info({"type": "metaAndAssetCtxs"})

        # Hyperliquid returns a list: [meta, assetCtxs]
        if isinstance(data, list) and len(data) == 2:
            meta, ctxs = data
            universe = meta.get("universe", [])
            return universe, ctxs

        # Defensive: unexpected structure
        if isinstance(data, dict):
            return data.get("universe", []), data.get("assetCtxs", [])

        logger.warning(f"Unexpected metaAndAssetCtxs response type: {type(data)}")
        return [], []

    # ------------------------------------------------------------------
    # Candles
    # ------------------------------------------------------------------

    async def get_candles(
        self,
        coin: str,
        interval: str = "15m",
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 5000,
    ) -> pd.DataFrame:
        """
        Fetch OHLCV candle snapshot and return a pandas DataFrame
        with columns: time, open, high, low, close, volume.
        """
        now_ms = int(time.time() * 1000)
        if end_time is None:
            end_time = now_ms
        if start_time is None:
            interval_ms = self._interval_to_ms(interval)
            start_time = end_time - (limit * interval_ms)

        payload = {
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": interval,
                "startTime": start_time,
                "endTime": end_time,
            },
        }
        raw = await self.post_info(payload)
        if not raw:
            logger.warning(f"Empty candle response for {coin}")
            return pd.DataFrame()

        rows = []
        for bar in raw:
            rows.append({
                "time": bar.get("t", 0),
                "open": float(bar.get("o", 0)),
                "high": float(bar.get("h", 0)),
                "low": float(bar.get("l", 0)),
                "close": float(bar.get("c", 0)),
                "volume": float(bar.get("v", 0)),
            })

        df = pd.DataFrame(rows)
        df["time"] = pd.to_datetime(df["time"], unit="ms")
        return df.sort_values("time").reset_index(drop=True)

    async def get_candles_multi(
        self,
        coins: list[str],
        interval: str = "15m",
        max_concurrent: int = 10,
    ) -> dict[str, pd.DataFrame]:
        """Fetch candles for multiple coins concurrently with a semaphore."""
        sem = asyncio.Semaphore(max_concurrent)
        results: dict[str, pd.DataFrame] = {}

        async def _fetch(coin: str) -> None:
            async with sem:
                try:
                    df = await self.get_candles(coin, interval)
                    results[coin] = df
                except Exception as e:
                    logger.warning(f"Failed to fetch candles for {coin}: {e}")
                    results[coin] = pd.DataFrame()

        await asyncio.gather(*(_fetch(c) for c in coins))
        return results

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------

    async def start_websocket(self, coins: list[str], interval: str = "15m") -> None:
        """Connect and subscribe to allMids + per-coin candle streams."""
        self._ws_task = asyncio.create_task(
            self._ws_loop(coins, interval),
            name="hl_websocket",
        )
        logger.info(f"WebSocket task started for: {coins}")

    async def _ws_loop(self, coins: list[str], interval: str) -> None:
        while True:
            try:
                async with websockets.connect(HL_WS_URL) as ws:
                    self._ws = ws

                    # Subscribe to all mid prices
                    await ws.send(
                        json.dumps({
                            "method": "subscribe",
                            "subscription": {"type": "allMids"},
                        })
                    )

                    # Subscribe to candle updates per coin
                    for coin in coins:
                        await ws.send(
                            json.dumps({
                                "method": "subscribe",
                                "subscription": {
                                    "type": "candle",
                                    "coin": coin,
                                    "interval": interval,
                                },
                            })
                        )

                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                            self._handle_ws_message(msg)
                        except Exception:
                            # Malformed or ping — silently drop
                            pass
            except (websockets.ConnectionClosed, websockets.ConnectionClosedError) as exc:
                logger.warning(f"WebSocket closed: {exc}. Reconnect in 5s…")
                await asyncio.sleep(5)
            except Exception as exc:
                logger.error(f"WebSocket error: {exc}. Reconnect in 10s…")
                await asyncio.sleep(10)

    def _handle_ws_message(self, msg: dict) -> None:
        channel = msg.get("channel")
        if channel == "allMids":
            self._all_mids.update(msg.get("data", {}))
        elif channel == "candle":
            data = msg.get("data")
            if data and isinstance(data, dict):
                coin = data.get("coin", "UNKNOWN")
                self._candle_buffers.setdefault(coin, []).append(data)
                # Trim to last 2000 bars to keep memory bounded
                buf = self._candle_buffers[coin]
                if len(buf) > 2000:
                    self._candle_buffers[coin] = buf[-2000:]

    def get_current_price(self, coin: str) -> float | None:
        """Latest mid price from WebSocket cache (None if not yet received)."""
        val = self._all_mids.get(coin)
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------------
    # MoonDev (optional / supplementary)
    # ------------------------------------------------------------------

    async def moondev_request(
        self, endpoint: str, params: dict | None = None
    ) -> Any:
        key = self.cfg.moondev_api_key
        if not key:
            raise RuntimeError("MoonDev API key not configured")
        await self._ensure_session()
        url = f"{MOONDEV_BASE}{endpoint}"
        async with self.session.get(
            url,
            headers={"X-API-Key": key},
            params=params,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_moondev_prices(self) -> dict:
        return await self.moondev_request("/api/prices")

    async def get_moondev_candles(self, coin: str, interval: str = "15m") -> dict:
        return await self.moondev_request(
            f"/api/candles/{coin}", {"interval": interval}
        )

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _interval_to_ms(interval: str) -> int:
        multipliers = {
            "1m": 1,
            "3m": 3,
            "5m": 5,
            "15m": 15,
            "30m": 30,
            "1h": 60,
            "2h": 120,
            "4h": 240,
            "6h": 360,
            "8h": 480,
            "12h": 720,
            "1d": 1440,
            "1w": 10080,
        }
        mins = multipliers.get(interval, 15)
        return mins * 60 * 1000
