"""
Test Signal Engine (Phase 7) with real OpenRouter API key.
Fetches BTC data, computes indicators, evaluates signal via Kimi K2.6.
"""
import asyncio
import sys
from pathlib import Path

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.data_client import HyperliquidDataClient
from agent.indicator_engine import compute_indicators
from agent.signal_engine import SignalEngine
from strategies import STRATEGIES
from config import get_config


async def test_signal_engine():
    cfg = get_config()
    print(f"OPENROUTER_API_KEY present: {'Yes' if cfg.openrouter_api_key else 'No'}")
    print(f"DATABASE_URL present: {'Yes' if cfg.database_url else 'No'}")

    if not cfg.openrouter_api_key:
        print("ERROR: OPENROUTER_API_KEY missing")
        return 1

    client = HyperliquidDataClient()
    engine = SignalEngine()

    try:
        print("\nFetching BTC 15m candles...")
        df_15m = await client.get_candles("BTC", "15m", limit=100)
        print(f"  Got {len(df_15m)} bars")

        print("Fetching BTC 1h candles...")
        df_1h = await client.get_candles("BTC", "1h", limit=100)
        print(f"  Got {len(df_1h)} bars")

        if len(df_15m) < 30 or len(df_1h) < 30:
            print("ERROR: Not enough candle data")
            return 1

        print("\nComputing indicators...")
        indicators = compute_indicators(df_15m, df_1h)
        print(f"  15m RSI: {indicators['15m']['rsi']:.2f}")
        print(f"  15m MACD hist: {indicators['15m']['macd_hist']:.4f}")
        print(f"  1h EMA50: {indicators['1h']['ema50']:.2f}")

        strategy = STRATEGIES["rsi_macd"]
        print(f"\nEvaluating signal for BTC with strategy '{strategy.name}'...")
        signal = await engine.evaluate("BTC", strategy, indicators)

        print("\n=== SIGNAL RESULT ===")
        print(f"  Direction: {signal.direction}")
        print(f"  Confidence: {signal.confidence}")
        print(f"  Reasoning: {signal.reasoning}")
        print(f"  Entry Price: {signal.entry_price}")
        print(f"  Invalidation: {signal.invalidation}")
        print("=====================\n")

        if signal.direction in ("long", "short") and signal.confidence >= 0.65:
            print("✅ Signal IS actionable")
        else:
            print("⏸️ Signal skipped (direction=none or confidence too low)")

        print("\nPhase 7 test PASSED ✅")
        return 0

    except Exception as exc:
        print(f"\n❌ Phase 7 test FAILED: {exc}")
        import traceback
        traceback.print_exc()
        return 1

    finally:
        await client.close()
        await engine.close()


if __name__ == "__main__":
    exit(asyncio.run(test_signal_engine()))
