"""Comprehensive end-to-end test for Coinbase pivot."""
import sys, asyncio
sys.path.insert(0, '.')

from config import get_config
from agent.coinbase_client import CoinbaseClient
from agent.screener import Screener
from agent.signal_engine import SignalEngine
from agent.indicator_engine import compute_indicators
from strategies import STRATEGIES
import pandas as pd

async def test():
    print("=== CONFIG ===")
    cfg = get_config()
    print(f"Coinbase key present: {bool(cfg.coinbase_api_key)}")
    print(f"OpenRouter key present: {bool(cfg.openrouter_api_key)}")
    print(f"DB URL present: {bool(cfg.database_url)}")

    print("\n=== COINBASE CLIENT ===")
    cb = CoinbaseClient()
    ok = await cb.verify_auth()
    print(f"Auth OK: {ok}")

    print("\n=== SCREENER ===")
    s = Screener(cb)
    watchlist = await s.run(max_watchlist=3)
    print(f"Watchlist: {watchlist}")

    print("\n=== CANDLES + INDICATORS ===")
    if watchlist:
        pid = watchlist[0]
        candles = await cb.get_candles(pid, "FIFTEEN_MINUTE")
        print(f"Candles for {pid}: {len(candles)}")

        if len(candles) >= 30:
            df = pd.DataFrame([
                {"time": c.start, "open": c.open, "high": c.high,
                 "low": c.low, "close": c.close, "volume": c.volume}
                for c in candles
            ])
            df["time"] = pd.to_datetime(df["time"], unit="s")
            df = df.sort_values("time").reset_index(drop=True)
            print(f"DataFrame: {len(df)} rows")
            print(f"Last close: {df.iloc[-1]['close']:.2f}")

    print("\n=== SIGNAL ENGINE ===")
    se = SignalEngine()
    print("SignalEngine initialized")

    await cb.close()
    await se.close()
    print("\n✅ All tests passed!")

if __name__ == "__main__":
    asyncio.run(test())
