# TradeBrain (Burt) — Action Plan

> This is the step-by-step build plan for TradeBrain with Burt personality.
> Complete each phase in order. Check boxes as you go.
> **Do not start Phase 3+ until environment variables are provided.**

---

## PHASE 0: Environment Variables & API Keys

**You must obtain these before building. The agent will pause and ask for each.**

### Required (cannot proceed without these)

- [x] `OPENROUTER_API_KEY` — Sign up at [openrouter.ai](https://openrouter.ai), add credits, copy API key
- [ ] `HL_WALLET_ADDRESS` — Your Hyperliquid main wallet address (0x...)
- [ ] `HL_API_PRIVATE_KEY` — Generate at [app.hyperliquid.xyz/API](https://app.hyperliquid.xyz/API): paste main wallet → Generate → Authorize → copy private key
- [x] `DATABASE_URL` — Create a Neon Postgres database at [neon.tech](https://neon.tech), copy the connection string (postgresql://...)

### Required for Burt (Discord bot)

- [ ] `DISCORD_BOT_TOKEN` — Create app at [discord.com/developers](https://discord.com/developers/applications) → Bot → copy token
- [ ] `DISCORD_CHANNEL_ID` — Create a `#burt` channel in your server, right-click → Copy ID (enable Developer Mode in Discord settings)
- [ ] `DISCORD_USER_ID` — Right-click your own username → Copy ID

### Optional (system works without these)

- [ ] `DISCORD_WEBHOOK_URL` — Server Settings → Integrations → Webhooks → copy URL (for fallback notifications)
- [ ] `MOONDEV_API_KEY` — Sign up at [moondev.com](https://moondev.com) for supplementary liquidation/position data

### Defaults (no action needed, configurable later via UI)

```
PAPER_TRADING=true
DEFAULT_LEVERAGE=3
DEFAULT_RISK_PER_TRADE=0.01
DEFAULT_DAILY_LOSS_LIMIT=0.05
DEFAULT_STRATEGY=rsi_macd
DEFAULT_SIGNAL_INTERVAL=300
DEFAULT_MAX_WATCHLIST=5
HL_TESTNET=false
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
  - ccxt, coincurve, aiohttp, websockets
  - pandas, pandas-ta
  - asyncpg, pgvector
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
  - [x] `trades` table
  - [x] `agent_config` table
  - [x] `screener_runs` table
- [x] Implement Burt tables from BURT.md Section 4.2:
  - [x] Enable pgvector extension (`CREATE EXTENSION IF NOT EXISTS vector`)
  - [x] `memories` table (with vector(1536) column)
  - [x] `discord_messages` table
  - [x] `daily_consolidations` table
- [x] Create all indexes
- [x] Add config hot-reload: `db.sync_config()` reads `agent_config` table
- [x] Run `setup_db.py` against Neon and verify tables exist

---

## PHASE 3: Hyperliquid Data Client

> **No API key needed — Hyperliquid read API is free and unauthenticated**

- [x] Create `agent/data_client.py`
- [x] Implement REST client for `https://api.hyperliquid.xyz/info`:
  - [x] `get_meta_and_asset_ctxs()` — all markets + metadata
  - [x] `get_candles(coin, interval, start, end)` — OHLCV candle snapshots
  - [x] Response field mapping (`o`→`open`, `h`→`high`, etc.) to pandas DataFrame
- [x] Implement WebSocket client for `wss://api.hyperliquid.xyz/ws`:
  - [x] Subscribe to `allMids` (live prices)
  - [x] Subscribe to `candle` updates per coin
  - [x] Auto-reconnect logic
- [x] (Optional) MoonDev API client if `MOONDEV_API_KEY` is set:
  - [x] `get_liquidations(coin)`
  - [x] `get_position_snapshots(coin)`
  - [x] Graceful fallback if key not set
- [x] Test: fetch BTC 15m candles, print last 5 rows

---

## PHASE 4: Indicator Engine

- [x] Create `agent/indicator_engine.py`
- [x] Implement `compute_indicators(df_15m, df_1h) -> dict` using pandas-ta:
  - [x] RSI(14) on 15m
  - [x] MACD(12,26,9) on 15m
  - [x] Bollinger Bands(20,2) on 15m
  - [x] ATR(14) on 15m
  - [x] Volume ratio (current / 20-bar SMA) on 15m
  - [x] EMA(20) and EMA(50) on 1H
  - [x] RSI(14) on 1H
- [x] Return structured dict with last-bar + prev-bar values
- [x] Handle NaN values gracefully (insufficient history)
- [x] Test: compute indicators on real BTC data, verify values are reasonable

---

## PHASE 5: Screener

- [x] Create `agent/screener.py`
- [x] Implement scoring algorithm (BLUEPRINT.md Section 4.1):
  - [x] Volume score (30% weight)
  - [x] Volatility/ATR score (25% weight, peaks at 2-3%)
  - [x] Funding rate score (20% weight)
  - [x] Trend clarity score (15% weight, EMA separation)
  - [x] Open interest score (10% weight)
- [x] Implement minimum thresholds (volume > $5M, leverage >= 10x, price > $0.0001)
- [x] Use `asyncio.gather` with semaphore (max 10 concurrent) for candle fetching
- [x] Return top N coins by composite score
- [x] Log screener run to `screener_runs` table
- [x] Test: run screener, print top 10 with score breakdowns

---

## PHASE 6: Strategies

- [x] Create `strategies/base.py` — `BaseStrategy` ABC + `SignalResult` dataclass
  - [x] `build_prompt(market_data) -> str`
  - [x] `parse_response(response, market_data) -> SignalResult`
- [x] Create `strategies/__init__.py` — strategy registry `STRATEGIES` dict
- [x] Create `strategies/rsi_macd.py` — Strategy 1: RSI + MACD Momentum
  - [x] Long conditions (RSI cross above 30 + MACD cross + 1H trend filter)
  - [x] Short conditions (RSI cross below 70 + bearish MACD + 1H below EMA50)
- [x] Create `strategies/bollinger.py` — Strategy 2: BB Mean Reversion
  - [x] Long: wick below BB lower + RSI < 35 + close back above
  - [x] Short: wick above BB upper + RSI > 65 + close back below
- [x] Create `strategies/ema_pullback.py` — Strategy 3: EMA Trend + Pullback
  - [x] Long: 1H uptrend + 15m pullback to 20 EMA + bullish candle
  - [x] Short: inverse

---

## PHASE 7: Signal Engine (Kimi K2.6 via OpenRouter)

> **Requires: `OPENROUTER_API_KEY`**

- [x] Create `agent/signal_engine.py`
- [x] Implement OpenRouter API client:
  - [x] Model: `openai/kimi-k2.6`
  - [x] Endpoint: `https://openrouter.ai/api/v1/chat/completions`
  - [x] Headers: `Authorization`, `HTTP-Referer`, `X-Title`
  - [x] Settings: `temperature: 0.1`, `max_tokens: 400`, `response_format: json_object`
- [x] Build signal evaluation prompt from strategy + indicator data
- [x] Parse JSON response into `SignalResult`
- [x] Handle parse failures gracefully (return `direction: "none"`)
- [x] Log every signal to `signals` table (acted on or not)
- [x] Test: evaluate a signal for BTC with RSI/MACD strategy, verify valid JSON response

---

## PHASE 8: Risk Manager

- [x] Create `agent/risk_manager.py`
- [x] Implement position sizing:
  - [x] `risk_dollars = balance × risk_per_trade_pct`
  - [x] `notional_size = risk_dollars / stop_distance_pct`
  - [x] Safety cap: margin < 20% of account balance
- [x] Implement stop loss calculation:
  - [x] ATR method (default): `ATR(14) × atr_multiplier`
  - [x] Fixed % method (fallback)
- [x] Implement take profit: `risk_distance × take_profit_rr`
- [x] Implement daily loss circuit breaker:
  - [x] Track `daily_loss_usdc`
  - [x] Halt trading when `daily_loss >= balance × daily_loss_limit_pct`
  - [x] Reset at midnight UTC or manual reset
- [x] Pre-trade checks:
  - [x] `direction != "none"`
  - [x] `confidence >= min_confidence`
  - [x] No existing position on this coin
  - [x] Circuit breaker not active

---

## PHASE 9: Executor (CCXT / Hyperliquid)

> **Requires: `HL_WALLET_ADDRESS`, `HL_API_PRIVATE_KEY`**

- [x] Create `agent/executor.py`
- [x] Initialize CCXT Hyperliquid client (`ccxt.hyperliquid`)
- [x] Pair format: `BTC/USDC:USDC` (verify via `exchange.markets`)
- [x] Implement order flow:
  - [x] `set_leverage(leverage, symbol, isolated)` — must be called before orders
  - [x] `create_market_order()` — entry
  - [x] `create_stop_order()` — stop loss (`reduceOnly`, `triggerType: 'mark'`)
  - [x] `create_limit_order()` — take profit (`reduceOnly`)
- [x] **Non-negotiable**: no entry order without simultaneous stop loss
- [x] Implement paper trading mode:
  - [x] In-memory `paper_positions` dict
  - [x] Simulate P&L using live `allMids` prices
  - [x] Log paper trades to DB with `is_paper=true`
- [x] Test: verify connectivity with `fetch_balance()` (paper mode)

---

## PHASE 10: Position Monitor

- [x] Create `agent/position_monitor.py`
- [x] Run as separate `asyncio.Task`, check every 30 seconds
- [x] For live positions: compare `fetch_positions()` against `open_positions` dict
- [x] For paper positions: compare entry to current mid price
- [x] On position close:
  - [x] Fetch fills for actual exit price + P&L
  - [x] Update trade record in DB (`exit_price`, `pnl_usdc`, `status`, `closed_at`)
  - [x] Update daily P&L for circuit breaker
  - [x] Trigger Burt notification
  - [x] Trigger memory formation

---

## PHASE 11: Memory Engine (Burt's Brain)

> **Requires: `OPENROUTER_API_KEY` (for embeddings), `DATABASE_URL` (pgvector)**

- [x] Create `agent/memory_engine.py`
- [x] Implement embedding generation:
  - [x] Model: `openai/text-embedding-3-small` via OpenRouter
  - [x] 1536 dimensions
  - [x] Batch embedding support
- [x] Register pgvector type on asyncpg connection pool
- [x] Implement memory CRUD:
  - [x] `store_memory(content, type, source, symbol?, strategy?)` — embed + insert
  - [x] `search_memories(query, limit=5)` — embed query + cosine similarity search
  - [x] `update_importance(memory_id, new_importance)`
- [x] Implement memory formation from trade outcomes (automatic)
- [x] Implement memory formation from user feedback (parsed from Discord)
- [x] Implement nightly consolidation:
  - [x] Schedule at 10:30 PM ET via apscheduler
  - [x] Query day's trades + signals
  - [x] Send consolidation prompt to Kimi K2.6
  - [x] Store lessons + observations as memories
  - [x] Store summary in `daily_consolidations`

---

## PHASE 12: Burt — Personality & Discord Bot

> **Requires: `DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID`, `DISCORD_USER_ID`**

- [x] Create `agent/burt.py`
- [x] Implement `Burt` class:
  - [x] Discord client setup (use `discord.Client`, NOT `commands.Bot`)
  - [x] Run in same asyncio event loop as trading agent (`asyncio.create_task`)
- [x] Implement `on_message` handler:
  - [x] Only respond in designated channel
  - [x] Only respond to designated user
  - [x] Store messages in `discord_messages` table
  - [x] Generate response via Kimi K2.6 with full context (state + memories + working memory)
- [x] Implement Burt's system prompt (BURT.md Section 5.4)
- [x] Implement proactive messaging:
  - [x] Morning brief at 6 AM ET
  - [x] Trade opened/closed notifications in Burt's voice
  - [x] Circuit breaker alerts
  - [x] End of day summary at 10 PM ET
  - [x] Max 1 proactive message per hour (except urgent)
  - [x] Active hours check (6 AM - 10 PM ET)
- [x] Implement natural language command handling (BURT.md Section 6):
  - [x] "what are you looking at" → screener results
  - [x] "what's open" → open positions
  - [x] "how'd we do today" → daily summary
  - [x] "go live" → double confirmation safety gate
  - [x] "stop trading" / "resume" → pause/unpause
  - [x] "close [coin]" → close with confirmation
  - [x] "remember that" / "forget about X" → memory operations
- [x] Implement `notify_trade_opened()`, `notify_trade_closed()`, `notify_circuit_breaker()`
- [x] Burt goes quiet after 10 PM ET (one emergency exception allowed)

---

## PHASE 13: Notifier (Fallback Webhook)

- [x] Create `agent/notifier.py`
- [x] If Burt is running (Discord bot active), delegate to `burt.notify_*()` methods
- [x] If Burt is not running (no bot token), fall back to Discord webhook:
  - [x] Trade opened embed (green for long, red for short)
  - [x] Trade closed embed (green for profit, red for loss)
  - [x] Circuit breaker embed (red, 0xff0000)
  - [x] Daily summary embed
- [x] Embed colors: `0x00ff88` long/profit, `0xff4455` short/loss, `0xffaa00` warning

---

## PHASE 14: FastAPI Backend

- [x] Create `agent/api.py`
- [x] Run at `localhost:8000` via uvicorn in background thread
- [x] CORS: allow `http://localhost:5173` only
- [x] Implement endpoints:
  - [x] `GET /api/status` — config, agent state, circuit breaker
  - [x] `PATCH /api/config` — update config (write to DB + in-memory)
  - [x] `GET /api/trades` — recent trades (limit 50)
  - [x] `GET /api/signals` — recent signals (limit 100)
  - [x] `GET /api/stats` — today's P&L, win rate, trade count
  - [x] `GET /api/watchlist` — current watchlist with scores
  - [x] `POST /api/screener/run` — trigger immediate re-run
  - [x] `GET /api/positions` — current open positions
  - [x] `POST /api/positions/{id}/close` — manually close position
  - [x] `POST /api/circuit-breaker/reset` — manual reset

---

## PHASE 15: Main Agent Loop

- [x] Create `agent/main.py` — entry point
- [x] Implement startup sequence (BLUEPRINT.md Section 15):
  1. [x] Load and validate config (fail fast on missing required keys)
  2. [x] Connect to Neon DB (asyncpg pool)
  3. [x] Run DB migrations (CREATE TABLE IF NOT EXISTS)
  4. [x] Connect CCXT Hyperliquid (`load_markets`)
  5. [x] Verify API connectivity (`fetch_balance`)
  6. [x] Run initial screener → set active watchlist
  7. [x] Start FastAPI server in background thread
  8. [x] Start position monitor background task
  9. [x] Start Burt (Discord bot) as asyncio task
  10. [x] Log startup to Discord
  11. [x] Enter main signal loop
- [x] Main signal loop (every `signal_interval` seconds):
  - [x] `await db.sync_config()` at top of each iteration
  - [x] Re-run screener every 4 hours
  - [x] For each coin in watchlist: fetch data → compute indicators → evaluate signal → risk check → execute/skip
- [x] Graceful shutdown (close positions? notification? cleanup)
- [ ] Test: run agent, verify startup sequence completes and loop executes

---

## PHASE 16: SvelteKit Dashboard

- [x] Initialize SvelteKit 2 project in `ui/` directory
- [x] Install dependencies: `npm install`
- [x] Create `src/lib/api.ts` — fetch wrappers for FastAPI backend at `localhost:8000`
- [x] Create `src/lib/types.ts` — shared TypeScript types
- [x] Use **Svelte 5 runes** (`$state`, `$derived`, `$effect`) — NOT Svelte 4 stores
- [x] Create `src/routes/+layout.svelte` — app shell, nav, dark theme
- [x] Create `src/routes/+page.svelte` — main dashboard with:
  - [x] **Header bar**: agent status dot, Paper/Live badge, today's P&L, strategy name, circuit breaker status
  - [x] **Left sidebar — Controls**:
    - [x] Paper/Live toggle (typing "CONFIRM" to go live)
    - [x] Strategy selector dropdown
    - [x] Leverage slider (1-20x)
    - [x] Risk per trade slider (0.5-5%)
    - [x] Daily loss limit slider (1-20%)
    - [x] Min confidence slider (0.5-0.9)
    - [x] Stop loss method toggle (ATR / Fixed %)
    - [x] ATR multiplier / Fixed % inputs
    - [x] Take profit R:R slider (1-5)
    - [x] Max watchlist size (1-10)
    - [x] Signal interval selector
    - [x] "Re-run screener" button
    - [x] Current watchlist chips with scores
  - [x] **Main area — Tabs**:
    - [x] Positions tab (polls every 10s, manual close button)
    - [x] Trades tab (color-coded P&L)
    - [x] Signals tab (confidence bar, reasoning)
    - [x] Screener tab (full score breakdown)
- [x] Auto-save config changes (debounced 500ms, PATCH to `/api/config`)
- [x] Polling: status 5s, positions 10s, trades/signals 30s

---

## PHASE 17: Integration Testing & Stability

- [ ] Run full system in paper mode for at least 1 hour
- [ ] Verify screener selects reasonable coins
- [ ] Verify signals are generated and logged
- [ ] Verify paper trades are placed with correct stop/TP
- [ ] Verify Discord notifications (Burt's voice) fire on trade events
- [ ] Verify circuit breaker triggers at daily loss limit
- [ ] Verify UI reflects live agent state
- [ ] Verify config changes from UI propagate to agent
- [ ] Verify Burt responds to Discord messages
- [ ] Verify memory formation from trade outcomes
- [ ] Verify nightly consolidation runs
- [ ] Stress test: run for 8 hours without crashing

---

## PHASE 18: Go-Live Checklist (After 2 Weeks Paper)

> **Do not enable live trading until Phase 17 runs cleanly for 2 weeks.**

- [ ] Paper trading results reviewed and acceptable
- [ ] All risk parameters tuned
- [ ] Hyperliquid API wallet funded with USDC
- [ ] Live mode toggle tested (double confirmation in UI + Discord)
- [ ] Emergency stop tested (circuit breaker, manual close, pause)
- [ ] Switch `PAPER_TRADING=false` or use UI/Discord toggle

---

## Environment Variable Summary

| Variable | Required | Phase Needed | Where to Get It |
|---|---|---|---|
| `OPENROUTER_API_KEY` | Yes | Phase 7 | [openrouter.ai](https://openrouter.ai) |
| `HL_WALLET_ADDRESS` | Yes | Phase 9 | Your Hyperliquid wallet |
| `HL_API_PRIVATE_KEY` | Yes | Phase 9 | [app.hyperliquid.xyz/API](https://app.hyperliquid.xyz/API) |
| `DATABASE_URL` | Yes | Phase 2 | [neon.tech](https://neon.tech) |
| `DISCORD_BOT_TOKEN` | Yes (for Burt) | Phase 12 | [discord.com/developers](https://discord.com/developers/applications) |
| `DISCORD_CHANNEL_ID` | Yes (for Burt) | Phase 12 | Right-click channel → Copy ID |
| `DISCORD_USER_ID` | Yes (for Burt) | Phase 12 | Right-click your name → Copy ID |
| `DISCORD_WEBHOOK_URL` | Optional | Phase 13 | Server Settings → Webhooks |
| `MOONDEV_API_KEY` | Optional | Phase 3 | [moondev.com](https://moondev.com) |
