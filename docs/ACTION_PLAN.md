# TradeBrain (Burt) — Action Plan

> This is the step-by-step build plan for TradeBrain with Burt personality.
> Complete each phase in order. Check boxes as you go.
> **Updated for Coinbase Advanced Perps (FCM) — CFTC-regulated, USA-compliant.**

---

## PHASE 0: Environment Variables & API Keys

**You must obtain these before building. The agent will pause and ask for each.**

### Required (cannot proceed without these)

- [x] `OPENROUTER_API_KEY` — Sign up at [openrouter.ai](https://openrouter.ai), add credits, copy API key
- [x] `COINBASE_API_KEY` — Create at [portal.cdp.coinbase.com](https://portal.cdp.coinbase.com), copy key name (`organizations/.../apiKeys/...`)
- [x] `COINBASE_API_SECRET` — Same portal, copy EC private key PEM (escape `\n` in `.env`)
- [x] `DATABASE_URL` — Create a Neon Postgres database at [neon.tech](https://neon.tech), copy the connection string

### Required for Burt (Discord bot)

- [ ] `DISCORD_BOT_TOKEN` — Create app at [discord.com/developers](https://discord.com/developers/applications) → Bot → copy token
- [ ] `DISCORD_CHANNEL_ID` — Create a `#burt` channel in your server, right-click → Copy ID
- [ ] `DISCORD_USER_ID` — Right-click your own username → Copy ID

### Optional (system works without these)

- [ ] `DISCORD_WEBHOOK_URL` — Server Settings → Integrations → Webhooks → copy URL

### Defaults (no action needed, configurable later via UI)

```
PAPER_TRADING=true
DEFAULT_LEVERAGE=3
DEFAULT_RISK_PER_TRADE=0.01
DEFAULT_DAILY_LOSS_LIMIT=0.05
DEFAULT_STRATEGY=rsi_macd
DEFAULT_SIGNAL_INTERVAL=300
DEFAULT_MAX_WATCHLIST=5
BURT_ACTIVE_HOURS_START=6
BURT_ACTIVE_HOURS_END=22
```

---

## PHASE 1: Project Scaffolding & Configuration

- [x] Initialize git repo
- [x] Create directory structure:
  ```
  tradebrain/
  ├── agent/
  ├── strategies/
  ├── ui/
  ├── scripts/
  └── logs/
  ```
- [x] Create `.gitignore` (Python + Node + `.env` + `logs/` + `venv/`)
- [x] Create `.env.example` with all variables listed above
- [x] Create `requirements.txt` with all Python dependencies:
  - pyjwt, cryptography, aiohttp, websockets
  - pandas, numpy
  - asyncpg
  - fastapi, uvicorn, python-dotenv, pydantic, httpx
  - loguru, apscheduler
  - discord.py, pytz
- [x] Create Python virtual environment and install deps
- [x] Create `config.py` — central config loader from `.env`, all defaults, validation
- [x] Verify config loads correctly with placeholder values

---

## PHASE 2: Database Layer

> **Requires: `DATABASE_URL`**

- [x] Create `agent/database.py` — asyncpg pool manager, connection helpers
- [x] Create `scripts/setup_db.py` — schema creation script
- [x] Implement all tables from BLUEPRINT.md Section 10:
  - [x] `signals` table
  - [x] `trades` table (with Coinbase FCM columns: `product_id`, `display_name`, `tax_treatment='1256'`, `product_type='perp'`)
  - [x] `agent_config` table
  - [x] `screener_runs` table
- [x] Implement Burt tables from BURT.md Section 4.2:
  - [x] Enable pgvector extension
  - [x] `memories` table
  - [x] `discord_messages` table
  - [x] `daily_consolidations` table
- [x] Create all indexes
- [x] Add config hot-reload: `db.sync_config()` reads `agent_config` table
- [x] Run `setup_db.py` against Neon and verify tables exist

---

## PHASE 3: Coinbase Native Client (JWT / ES256)

> **Requires: `COINBASE_API_KEY`, `COINBASE_API_SECRET`**

- [x] Create `agent/coinbase_client.py` — native JWT-signed client
- [x] Implement JWT auth (ES256) with CDP keypair per request
- [x] Implement product discovery:
  - [x] `list_future_products()` — all FUTURE products, filter to `% PERP`
  - [x] `hydrate_product_details()` — funding rate, OI per product
  - [x] `hydrate_all()` — concurrent hydration with rate-limit semaphore
- [x] Implement candle fetching:
  - [x] `get_candles(product_id, granularity)` — OHLCV (max 300 per request)
  - [x] `get_candles_multi()` — concurrent for multiple products
- [x] Implement account endpoints:
  - [x] `get_accounts()` — spot balances
  - [x] `get_futures_balance_summary()` — `futures_buying_power`
  - [x] `get_futures_positions()` — open CFM positions
- [x] Implement orders:
  - [x] `place_order()` — POST /orders with `product_type=FUTURE`
  - [x] `cancel_orders()` — batch cancel
  - [x] `list_open_orders()` — open orders
  - [x] `get_fills()` — fill history
- [x] Implement sweeps: `schedule_sweep()`, `get_sweeps()`
- [x] Rate limit handling (10 req/s private, 15 req/s public) with retry
- [x] Test: verify auth, discover perp products, fetch BTC candles

---

## PHASE 4: Indicator Engine

- [x] Create `agent/indicator_engine.py`
- [x] Implement `compute_indicators(df_15m, df_1h) -> dict`:
  - [x] RSI(14) on 15m
  - [x] MACD(12,26,9) on 15m
  - [x] Bollinger Bands(20,2) on 15m
  - [x] ATR(14) on 15m
  - [x] Volume ratio (current / 20-bar SMA) on 15m
  - [x] EMA(20) and EMA(50) on 1H
  - [x] RSI(14) on 1H
- [x] Return structured dict with last-bar + prev-bar values
- [x] Handle NaN values gracefully
- [x] Test: compute indicators on real BTC data

---

## PHASE 5: FCM Screener

- [x] Create `agent/screener.py`
- [x] Implement scoring algorithm:
  - [x] Volume score (30% weight) — `approximate_quote_24h_volume`
  - [x] Volatility/ATR score (25% weight)
  - [x] Funding rate score (20% weight) — from `future_product_details.funding_rate`
  - [x] Trend clarity score (15% weight)
  - [x] Open interest score (10% weight)
- [x] Implement minimum thresholds ($5M volume, max_leverage >= 5x, price > $0.0001)
- [x] Use `asyncio.gather` with semaphore (max 4 concurrent) for hydration
- [x] Return top N product_ids by composite score
- [x] Log screener run to `screener_runs` table
- [x] Test: run screener, print top products with score breakdowns

---

## PHASE 6: Strategies

- [x] Create `strategies/base.py`
- [x] Create `strategies/__init__.py`
- [x] Create `strategies/rsi_macd.py`
- [x] Create `strategies/bollinger.py`
- [x] Create `strategies/ema_pullback.py`

---

## PHASE 7: Signal Engine (Kimi K2.6 via OpenRouter)

- [x] Create `agent/signal_engine.py`
- [x] Implement OpenRouter API client:
  - [x] Model: `moonshotai/kimi-k2.6`
  - [x] Endpoint, headers, settings
- [x] Build signal evaluation prompt
- [x] Parse JSON response with robust extraction (handles reasoning tokens)
- [x] Handle parse failures gracefully
- [x] Log every signal to `signals` table
- [x] Test: evaluate a signal for BTC PERP

---

## PHASE 8: Risk Manager

- [x] Create `agent/risk_manager.py`
- [x] Implement position sizing, stop loss, take profit
- [x] Implement daily loss circuit breaker
- [x] Pre-trade checks

---

## PHASE 9: Executor (Coinbase FCM)

> **Requires: `COINBASE_API_KEY`, `COINBASE_API_SECRET`**

- [x] Create `agent/executor.py`
- [x] Paper trading mode:
  - [x] In-memory `paper_positions` dict
  - [x] Log paper trades with `tax_treatment='1256'`, `product_type='perp'`
- [x] Live order skeleton:
  - [x] Market entry via `place_order()`
  - [x] STOP_LIMIT_GTC reduce-only stop loss
  - [x] LIMIT_GTC reduce-only take profit
- [x] Test: paper trade entry/close

---

## PHASE 10: Position Monitor

- [x] Create `agent/position_monitor.py`
- [x] Check every 30 seconds
- [x] Paper: simulate P&L against mark price
- [x] Update DB, circuit breaker, notifications on close

---

## PHASE 11: Memory Engine

- [x] Create `agent/memory_engine.py`
- [x] Embedding generation via OpenRouter
- [x] Memory CRUD + RAG search
- [x] Trade outcome memory formation
- [x] Nightly consolidation

---

## PHASE 12: Burt — Discord Bot

- [x] Create `agent/burt.py`
- [x] Skeleton ready — activates with `DISCORD_BOT_TOKEN`

---

## PHASE 13: Notifier

- [x] Create `agent/notifier.py`

---

## PHASE 14: FastAPI Backend

- [x] Create `agent/api.py`

---

## PHASE 15: Main Agent Loop

- [x] Create `agent/main.py`
- [x] Startup sequence for Coinbase:
  1. [x] Load config
  2. [x] Connect to DB
  3. [x] Verify Coinbase API auth
  4. [x] Verify futures provisioned
  5. [x] Run initial screener
  6. [x] Start FastAPI
  7. [x] Start position monitor
  8. [x] Enter signal loop
- [x] Signal loop with maintenance window check
- [ ] **Test: run agent, verify startup sequence completes**

---

## PHASE 16: SvelteKit Dashboard

- [x] Initialize SvelteKit 2 project
- [x] Build dashboard with positions, trades, signals, screener tabs
- [x] Build succeeds

---

## PHASE 17: Integration Testing & Stability

- [ ] Run full system in paper mode for at least 1 hour
- [ ] Verify screener selects reasonable FCM perps
- [ ] Verify signals are generated and logged
- [ ] Verify paper trades are placed with correct stop/TP
- [ ] Verify circuit breaker triggers at daily loss limit
- [ ] Verify UI reflects live agent state
- [ ] Stress test: run for 8 hours without crashing

---

## PHASE 18: Go-Live Checklist (After 2 Weeks Paper)

> **Do not enable live trading until Phase 17 runs cleanly for 2 weeks.**

- [ ] Paper trading results reviewed and acceptable
- [ ] All risk parameters tuned
- [ ] Coinbase futures account funded (sweep USD spot → futures)
- [ ] Live mode toggle tested (double confirmation in UI + Discord)
- [ ] Emergency stop tested (circuit breaker, manual close, pause)
- [ ] Switch `PAPER_TRADING=false`

---

## Environment Variable Summary

| Variable | Required | Phase Needed | Where to Get It |
|---|---|---|---|
| `OPENROUTER_API_KEY` | Yes | Phase 7 | [openrouter.ai](https://openrouter.ai) |
| `COINBASE_API_KEY` | Yes | Phase 3 | [portal.cdp.coinbase.com](https://portal.cdp.coinbase.com) |
| `COINBASE_API_SECRET` | Yes | Phase 3 | Same as above — EC private key PEM |
| `DATABASE_URL` | Yes | Phase 2 | [neon.tech](https://neon.tech) |
| `DISCORD_BOT_TOKEN` | Yes (for Burt) | Phase 12 | [discord.com/developers](https://discord.com/developers) |
| `DISCORD_CHANNEL_ID` | Yes (for Burt) | Phase 12 | Right-click channel → Copy ID |
| `DISCORD_USER_ID` | Yes (for Burt) | Phase 12 | Right-click your name → Copy ID |
| `DISCORD_WEBHOOK_URL` | Optional | Phase 13 | Server Settings → Webhooks |
