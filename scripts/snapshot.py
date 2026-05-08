"""
Live indicator snapshot — verify the indicator engine against TradingView.

Usage:
    venv/bin/python -m scripts.snapshot BTC
    venv/bin/python -m scripts.snapshot ETH PERP
    venv/bin/python -m scripts.snapshot BIP-20DEC30-CDE
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

from agent.coinbase_client import CoinbaseClient
from agent.indicator_engine import compute_indicators


def candles_to_df(candles: list) -> pd.DataFrame:
    df = pd.DataFrame([
        {"time": c.start, "open": c.open, "high": c.high,
         "low": c.low, "close": c.close, "volume": c.volume}
        for c in candles
    ])
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df.sort_values("time").reset_index(drop=True)


async def resolve(cb: CoinbaseClient, query: str) -> str | None:
    products = await cb.list_future_products()
    q = query.strip().upper()
    for p in products:
        if p.product_id.upper() == q or p.display_name.upper() == q:
            return p.product_id
    for p in products:
        if p.display_name.upper().startswith(q):
            return p.product_id
    return None


async def main(query: str) -> int:
    cb = CoinbaseClient()
    product_id = await resolve(cb, query)
    if not product_id:
        print(f"No product matching '{query}'")
        await cb.close()
        return 1

    print(f"Resolved: {query}  ->  {product_id}")
    candles_15m = await cb.get_candles(product_id, "FIFTEEN_MINUTE")
    candles_1h = await cb.get_candles(product_id, "ONE_HOUR")
    print(f"Candles: 15m={len(candles_15m)}  1h={len(candles_1h)}")

    if len(candles_15m) < 30 or len(candles_1h) < 20:
        print("Not enough candles to compute indicators.")
        await cb.close()
        return 1

    indicators = compute_indicators(candles_to_df(candles_15m), candles_to_df(candles_1h))

    print("\n=== 15m ===")
    for k, v in indicators["15m"].items():
        if isinstance(v, float):
            print(f"  {k:18s} {v:.6g}")
        else:
            print(f"  {k:18s} {v}")
    print("\n=== 1h ===")
    for k, v in indicators["1h"].items():
        if isinstance(v, float):
            print(f"  {k:18s} {v:.6g}")
        else:
            print(f"  {k:18s} {v}")

    print("\n--- raw JSON ---")
    print(json.dumps(indicators, indent=2, default=str))
    await cb.close()
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    query = " ".join(sys.argv[1:])
    sys.exit(asyncio.run(main(query)))
