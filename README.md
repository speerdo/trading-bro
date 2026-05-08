# TradeBrain (Burt)

> An AI-powered crypto trading agent with personality, memory, and risk management.

## What is TradeBrain?

TradeBrain is a personal, locally-run AI trading agent for Hyperliquid perpetual futures. It continuously screens all 200+ perp markets, evaluates setups using Kimi K2.6 via OpenRouter, and executes leveraged long/short positions — all with mandatory risk controls.

**Paper trading is ON by default.** One toggle switches to live trading.

### Meet Burt

Burt is the agent's personality layer. He's conversational, dry, self-aware, and remembers your past trades. He talks to you like a slightly nerdy friend who knows a lot about trading. He runs in Discord, sends proactive updates, learns from outcomes, and forms long-term semantic memories (powered by pgvector embeddings on Neon).

## Key Capabilities

| Feature | Status |
|---------|--------|
| Market Screener (Option B) | ✅ Scores all 200+ HL markets every 4h |
| AI Signal Evaluation (Kimi K2.6) | ⏳ Skeleton ready — needs `OPENROUTER_API_KEY` |
| Risk Manager (stops, sizing, circuit breaker) | ✅ Full implementation |
| Paper Trading | ✅ Ready for testing |
| Live Trading (CCXT/Hyperliquid) | ⏳ Needs `HL_WALLET_ADDRESS` + `HL_API_PRIVATE_KEY` |
| Position Monitor | ✅ Skeleton ready |
| Discord Bot (Burt personality) | ⏳ Needs `DISCORD_BOT_TOKEN` |
| Semantic Memory (pgvector) | ✅ Schema + embedding API ready |
| SvelteKit Dashboard | ⏳ Phase 16 (UI not yet built) |
| Neon Postgres Logging | ✅ All tables created |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        TradeBrain Agent                      │
├─────────────────────────────────────────────────────────────┤
│  Screener → Indicators → Signal Engine → Risk → Executor    │
│     ↑         ↑              ↑                               │
│  HL REST   pandas-ta     OpenRouter (Kimi K2.6)            │
│  HL WS                                                  │
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
| Exchange | Hyperliquid perps via CCXT |
| Market Data | Hyperliquid REST + WebSocket (free) |
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

New strategies inherit from `BaseStrategy` and auto-register in the UI.

## Risk Management (Non-Negotiable)

- **Position sizing**: At-risk dollars / stop distance, capped at 20% margin
- **Stop loss**: ATR-based (default) or fixed % — placed simultaneously with entry
- **Take profit**: Configurable R:R (default 2.0)
- **Circuit breaker**: Halts ALL trading after daily loss limit (default 5%)
- **Leverage cap**: Default 3x, max 20x in UI
- **Min confidence**: 0.65 to act on a signal

## Quick Start

```bash
# 1. Clone & setup
git clone <repo>
cd tradebrain
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env with your API keys (see below)

# 3. Initialize database
python scripts/setup_db.py

# 4. Test the screener (no API keys needed)
python -c "
import asyncio
from agent.screener import run_screener
print(asyncio.run(run_screener()))
"

# 5. Start the agent
python -m agent.main

# 6. Start the dashboard (separate terminal)
cd ui && npm install && npm run dev
# → open http://localhost:5173
```

## Required Environment Variables

| Variable | Phase | Where to Get |
|---|---|---|
| `DATABASE_URL` | ✅ Ready | [neon.tech](https://neon.tech) |
| `OPENROUTER_API_KEY` | ⏳ Phase 7 | [openrouter.ai](https://openrouter.ai) |
| `HL_WALLET_ADDRESS` | ⏳ Phase 9 | Your Hyperliquid wallet |
| `HL_API_PRIVATE_KEY` | ⏳ Phase 9 | [app.hyperliquid.xyz/API](https://app.hyperliquid.xyz/API) |
| `DISCORD_BOT_TOKEN` | ⏳ Phase 12 | [discord.com/developers](https://discord.com/developers) |
| `DISCORD_CHANNEL_ID` | ⏳ Phase 12 | Right-click channel → Copy ID |
| `DISCORD_USER_ID` | ⏳ Phase 12 | Right-click your name → Copy ID |

## Project Structure

```
tradebrain/
├── agent/
│   ├── main.py              # Entry point, startup, main loop
│   ├── api.py               # FastAPI backend
│   ├── screener.py          # Market screener
│   ├── data_client.py       # Hyperliquid REST + WebSocket
│   ├── indicator_engine.py  # Manual pandas TA indicators
│   ├── signal_engine.py     # OpenRouter / Kimi K2.6 client
│   ├── risk_manager.py      # Position sizing + circuit breaker
│   ├── executor.py          # CCXT order placement + paper trading
│   ├── position_monitor.py  # Track open positions
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

- ✅ Phase 1-6: Project scaffolding, database, data client, indicators, screener, strategies
- ⏳ Phase 7: Signal engine (blocked on `OPENROUTER_API_KEY`)
- ✅ Phase 8: Risk manager
- ⏳ Phase 9: Live executor (blocked on `HL_WALLET_ADDRESS` + `HL_API_PRIVATE_KEY`)
- ✅ Phase 10: Position monitor skeleton
- ⏳ Phase 11-12: Burt memory + Discord bot (blocked on `DISCORD_BOT_TOKEN`)
- ⏳ Phase 13-16: Notifier, FastAPI backend, main loop, SvelteKit dashboard
- 🔮 Phase 17+: Integration testing, backtesting, go-live

**Do not enable live trading until paper mode runs cleanly for 2 weeks.**

## License

Private / personal use. Trade at your own risk.
