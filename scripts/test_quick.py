"""Quick integration test without LLM call (faster)."""
import asyncio, sys
sys.path.insert(0, '.')

from agent.coinbase_client import CoinbaseClient
from agent.screener import Screener
from agent.indicator_engine import compute_indicators
import pandas as pd

async def test():
    cb = CoinbaseClient()
    try:
        print("=== SCREENER ===")
        s = Screener(cb)
        watchlist = await s.run(max_watchlist=3)
        print(f"Watchlist: {watchlist}")
        
        print("\n=== CANDLES + INDICATORS ===")
        if watchlist:
            pid = watchlist[0]
            candles_15m = await cb.get_candles(pid, "FIFTEEN_MINUTE")
            candles_1h = await cb.get_candles(pid, "ONE_HOUR")
            print(f"15m candles: {len(candles_15m)}")
            print(f"1h candles: {len(candles_1h)}")
            
            df_15m = pd.DataFrame([
                {"time": c.start, "open": c.open, "high": c.high,
                 "low": c.low, "close": c.close, "volume": c.volume}
                for c in candles_15m
            ])
            df_1h = pd.DataFrame([
                {"time": c.start, "open": c.open, "high": c.high,
                 "low": c.low, "close": c.close, "volume": c.volume}
                for c in candles_1h
            ])
            
            if len(df_15m) >= 30 and len(df_1h) >= 20:
                indicators = compute_indicators(df_15m, df_1h)
                print(f"RSI: {indicators['15m']['rsi']:.2f}")
                print(f"MACD hist: {indicators['15m']['macd_hist']:.4f}")
                print(f"ATR: {indicators['15m']['atr']:.4f}")
            else:
                print("Not enough candle data")
        
        print("\n=== ACCOUNT ===")
        summary = await cb.get_futures_balance_summary()
        print(f"Futures balance summary: {summary}")
        
        print("\n✅ Quick integration test passed!")
    finally:
        await cb.close()

if __name__ == "__main__":
    asyncio.run(test())
