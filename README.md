# TradeBrain (Burt)

> An AI-powered crypto trading agent with personality, memory, and risk management.
> **Now trading on Coinbase Advanced Perps (FCM)** — CFTC-regulated, USA-compliant.

## What is TradeBrain?

TradeBrain is a personal, locally-run AI trading agent for **Coinbase Financial Markets (FCM)** perp-style futures. It continuously screens ~20 FCM perp markets, evaluates setups using Kimi K2.6 via OpenRouter, and executes leveraged long/short positions — all with mandatory risk controls and Section 1256 tax treatment.

**Paper trading is ON by default.** One toggle switches to live trading.

### Meet Burt

Burt is the agent's personality layer. He's conversational, dry, self-aware, and remembers your past trades. He talks to you like a slightly nerdy friend who knows a lot about trading. He runs in Discord, sends proactive updates, learns from outcomes, and forms long-term semantic memories (powered by pgvector embeddings on Neon).

## Key Capabilities

| Feature | Status |
|---------|--------|
| FCM Market Screener | ✅ Discovers ~22 perp products, scores top 5 |
| AI Signal Evaluation (Kimi K2.6) | ✅ Working with OpenRouter |
| Risk Manager (stops, sizing, circuit breaker) | ✅ Full implementation |
| Paper Trading | ✅ Ready for testing |
| Live Trading (Coinbase FCM) | ⏳ Needs futures buying power |
| Position Monitor | ✅ Full implementation |
| Discord Bot (Burt personality) | ✅ Skeleton ready — needs `DISCORD_BOT_TOKEN` |
| Semantic Memory (pgvector) | ✅ Full implementation |
| SvelteKit Dashboard | ✅ Built with Svelte 5 runes |
| Neon Postgres Logging | ✅ All tables created |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        TradeBrain Agent                      │
├─────────────────────────────────────────────────────────────┤
│  Screener → Indicators → Signal Engine → Risk → Executor    │
│     ↑         ↑              ↑                               │
│  Coinbase   pandas       OpenRouter (Kimi K2.6)            │
│  FCM API                                               │
├─────────────────────────────────────────────────────────────┤
│  Position Monitor (30s) | Burt Bot (Discord) | FastAPI    │
│       ↓                        ↓                  ↓         │
│   Neon DB                  Neon DB            SvelteKit    │
│   (trades, signals,        (memories,          Dashboard   │
│    screener, config)        discord_msgs)       localhost  │
└─────────────────────────────────────────────────────────────┘
```

## Tech Stack

| Layer | Technology |
|---|---|
| Signal Brain | Kimi K2.6 via OpenRouter |
| Exchange | Coinbase Financial Markets (FCM) — CFTC-regulated US DCM |
| Market Data | Native JWT-signed Coinbase Brokerage v3 client |
| Indicators | Manual pandas implementation (RSI, MACD, BB, ATR, EMA) |
| Backend API | FastAPI + uvicorn |
| Frontend | SvelteKit 2 + Svelte 5 runes |
| Database | Neon Postgres + pgvector |
| Notifications | Discord webhook + discord.py bot |
| Language | Python 3.11+ / TypeScript |

## Strategies

Three built-in strategies provide signal prompts to the AI:

1. **RSI + MACD Momentum** (default) — Trend reversals on 15m with 1h EMA filter
2. **Bollinger Band Mean Reversion** — Fade overextension back to the mean
3. **EMA Trend + Pullback** — Enter pullbacks to 20 EMA in strong trends

## Risk Management (Non-Negotiable)

- **Position sizing**: At-risk dollars / stop distance, capped at 20% margin
- **Stop loss**: ATR-based (default) or fixed % — placed simultaneously with entry
- **Take profit**: Configurable R:R (default 2.0)
- **Circuit breaker**: Halts ALL trading after daily loss limit (default 5%)
- **Leverage cap**: Default 3x, max 10x (FCM limit)
- **Min confidence**: 0.65 to act on a signal

## Quick Start

```bash
# 1. Clone & setup
cd tradebrain
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env with your API keys (see below)

# 3. Initialize database
python scripts/setup_db.py

# 4. Test the screener
python -c "
import asyncio
from agent.coinbase_client import CoinbaseClient
from agent.screener import Screener
async def test():
    cb = CoinbaseClient()
    s = Screener(cb)
    print(await s.run())
    await cb.close()
asyncio.run(test())
"

# 5. Start the agent
python -m agent.main

# 6. Start the dashboard (separate terminal)
cd ui && npm install && npm run dev
# → open http://localhost:5173
```

## Required Environment Variables

| Variable | Status | Where to Get |
|---|---|---|
| `DATABASE_URL` | ✅ Ready | [neon.tech](https://neon.tech) |
| `OPENROUTER_API_KEY` | ✅ Ready | [openrouter.ai](https://openrouter.ai) |
| `COINBASE_API_KEY` | ✅ Ready | [portal.cdp.coinbase.com](https://portal.cdp.coinbase.com) — CDP key name |
| `COINBASE_API_SECRET` | ✅ Ready | Same as above — EC private key PEM |
| `DISCORD_BOT_TOKEN` | ⏳ Optional | [discord.com/developers](https://discord.com/developers) |
| `DISCORD_CHANNEL_ID` | ⏳ Optional | Right-click channel → Copy ID |
| `DISCORD_USER_ID` | ⏳ Optional | Right-click your name → Copy ID |

## Project Structure

```
tradebrain/
├── agent/
│   ├── main.py              # Entry point, startup, main loop
│   ├── api.py               # FastAPI backend
│   ├── coinbase_client.py   # Native JWT Coinbase Brokerage v3 client
│   ├── screener.py          # FCM perp universe discovery + scoring
│   ├── indicator_engine.py  # Manual pandas TA indicators
│   ├── signal_engine.py     # OpenRouter / Kimi K2.6 client
│   ├── risk_manager.py      # Position sizing + circuit breaker
│   ├── executor.py          # Order placement + paper trading
│   ├── position_monitor.py  # Track open positions
│   ├── maintenance.py       # Friday/quarterly maintenance window check
│   ├── regime.py            # BTC dominance + market regime context
│   ├── database.py          # Neon asyncpg client
│   ├── notifier.py          # Discord notifications
│   ├── burt.py              # Personality + Discord bot
│   └── memory_engine.py     # Semantic memory + RAG
├── strategies/
│   ├── base.py              # BaseStrategy ABC
│   ├── rsi_macd.py
│   ├── bollinger.py
│   └── ema_pullback.py
├── ui/                      # SvelteKit 2 dashboard
├── scripts/
│   └── setup_db.py          # One-time DB schema creation
├── config.py                # Central config loader
├── requirements.txt
└── .env                     # Your secrets (gitignored)
```

## Safety

- Paper trading is the default. Switching to live requires manual confirmation.
- No entry order is placed without a simultaneous stop loss order.
- Circuit breaker halts all trading at the daily loss limit.
- The agent runs entirely on your local machine. No cloud compute. Your keys stay local.

## Phase Roadmap

- ✅ Phase 1-8: Project scaffolding, database, indicators, screener, strategies, signal engine, risk manager
- ✅ Phase 9: Executor (paper trading ready, live skeleton for Coinbase FCM)
- ✅ Phase 10: Position monitor
- ✅ Phase 11: Memory engine
- ✅ Phase 12: Burt (Discord bot skeleton)
- ✅ Phase 13: Notifier
- ✅ Phase 14: FastAPI backend
- ✅ Phase 15: Main agent loop
- ✅ Phase 16: SvelteKit dashboard
- 🔮 Phase 17+: Integration testing, backtesting, go-live

**Do not enable live trading until paper mode runs cleanly for 2 weeks.**

## License

Private / personal use. Trade at your own risk.
