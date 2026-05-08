# TradeBrain (Burt) — Action Plan

> This is the step-by-step build plan for TradeBrain with Burt personality.
> Complete each phase in order. Check boxes as you go.
> **Do not start Phase 3+ until environment variables are provided.**

---

## PHASE 0: Environment Variables & API Keys

**You must obtain these before building. The agent will pause and ask for each.**

### Required (cannot proceed without these)

- [ ] `OPENROUTER_API_KEY` — Sign up at [openrouter.ai](https://openrouter.ai), add credits, copy API key
- [ ] `HL_WALLET_ADDRESS` — Your Hyperliquid main wallet address (0x...)
- [ ] `HL_API_PRIVATE_KEY` — Generate at [app.hyperliquid.xyz/API](https://app.hyperliquid.xyz/API): paste main wallet → Generate → Authorize → copy private key
- [ ] `DATABASE_URL` — Create a Neon Postgres database at [neon.tech](https://neon.tech), copy the connection string (postgresql://...)

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

- [ ] Initialize git repo
- [ ] Create directory structure:
  ```
  tradebrain/
  ├── agent/
  ├── strategies/
  ├── ui/
  ├── scripts/
  └── logs/
  ```
- [ ] Create `.gitignore` (Python + Node + `.env` + `logs/` + `venv/`)
- [ ] Create `.env.example` with all variables listed above
- [ ] Create `requirements.txt` with all Python dependencies:
  - ccxt, coincurve, aiohttp, websockets
  - pandas, pandas-ta
  - asyncpg, pgvector
  - fastapi, uvicorn, python-dotenv, pydantic, httpx
  - loguru, apscheduler
  - discord.py, pytz
- [ ] Create Python virtual environment and install deps
- [ ] Create `config.py` — central config loader from `.env`, all defaults, validation
- [ ] Verify config loads correctly with placeholder values

---

## PHASE 2: Database Layer

> **Requires: `DATABASE_URL`**

- [ ] Create `agent/database.py` — asyncpg pool manager, connection helpers
- [ ] Create `scripts/setup_db.py` — schema creation script
- [ ] Implement all tables from BLUEPRINT.md Section 10:
  - [ ] `signals` table
  - [ ] `trades` table
  - [ ] `agent_config` table
  - [ ] `screener_runs` table
- [ ] Implement Burt tables from BURT.md Section 4.2:
  - [ ] Enable pgvector extension (`CREATE EXTENSION IF NOT EXISTS vector`)
  - [ ] `memories` table (with vector(1536) column)
  - [ ] `discord_messages` table
  - [ ] `daily_consolidations` table
- [ ] Create all indexes
- [ ] Add config hot-reload: `db.sync_config()` reads `agent_config` table
- [ ] Run `setup_db.py` against Neon and verify tables exist

---

## PHASE 3: Hyperliquid Data Client

> **No API key needed — Hyperliquid read API is free and unauthenticated**

- [ ] Create `agent/data_client.py`
- [ ] Implement REST client for `https://api.hyperliquid.xyz/info`:
  - [ ] `get_meta_and_asset_ctxs()` — all markets + metadata
  - [ ] `get_candles(coin, interval, start, end)` — OHLCV candle snapshots
  - [ ] Response field mapping (`o`→`open`, `h`→`high`, etc.) to pandas DataFrame
- [ ] Implement WebSocket client for `wss://api.hyperliquid.xyz/ws`:
  - [ ] Subscribe to `allMids` (live prices)
  - [ ] Subscribe to `candle` updates per coin
  - [ ] Auto-reconnect logic
- [ ] (Optional) MoonDev API client if `MOONDEV_API_KEY` is set:
  - [ ] `get_liquidations(coin)`
  - [ ] `get_position_snapshots(coin)`
  - [ ] Graceful fallback if key not set
- [ ] Test: fetch BTC 15m candles, print last 5 rows

---

## PHASE 4: Indicator Engine

- [ ] Create `agent/indicator_engine.py`
- [ ] Implement `compute_indicators(df_15m, df_1h) -> dict` using pandas-ta:
  - [ ] RSI(14) on 15m
  - [ ] MACD(12,26,9) on 15m
  - [ ] Bollinger Bands(20,2) on 15m
  - [ ] ATR(14) on 15m
  - [ ] Volume ratio (current / 20-bar SMA) on 15m
  - [ ] EMA(20) and EMA(50) on 1H
  - [ ] RSI(14) on 1H
- [ ] Return structured dict with last-bar + prev-bar values
- [ ] Handle NaN values gracefully (insufficient history)
- [ ] Test: compute indicators on real BTC data, verify values are reasonable

---

## PHASE 5: Screener

- [ ] Create `agent/screener.py`
- [ ] Implement scoring algorithm (BLUEPRINT.md Section 4.1):
  - [ ] Volume score (30% weight)
  - [ ] Volatility/ATR score (25% weight, peaks at 2-3%)
  - [ ] Funding rate score (20% weight)
  - [ ] Trend clarity score (15% weight, EMA separation)
  - [ ] Open interest score (10% weight)
- [ ] Implement minimum thresholds (volume > $5M, leverage >= 10x, price > $0.0001)
- [ ] Use `asyncio.gather` with semaphore (max 10 concurrent) for candle fetching
- [ ] Return top N coins by composite score
- [ ] Log screener run to `screener_runs` table
- [ ] Test: run screener, print top 10 with score breakdowns

---

## PHASE 6: Strategies

- [ ] Create `strategies/base.py` — `BaseStrategy` ABC + `SignalResult` dataclass
  - [ ] `build_prompt(market_data) -> str`
  - [ ] `parse_response(response, market_data) -> SignalResult`
- [ ] Create `strategies/__init__.py` — strategy registry `STRATEGIES` dict
- [ ] Create `strategies/rsi_macd.py` — Strategy 1: RSI + MACD Momentum
  - [ ] Long conditions (RSI cross above 30 + MACD cross + 1H trend filter)
  - [ ] Short conditions (RSI cross below 70 + bearish MACD + 1H below EMA50)
- [ ] Create `strategies/bollinger.py` — Strategy 2: BB Mean Reversion
  - [ ] Long: wick below BB lower + RSI < 35 + close back above
  - [ ] Short: wick above BB upper + RSI > 65 + close back below
- [ ] Create `strategies/ema_pullback.py` — Strategy 3: EMA Trend + Pullback
  - [ ] Long: 1H uptrend + 15m pullback to 20 EMA + bullish candle
  - [ ] Short: inverse

---

## PHASE 7: Signal Engine (Kimi K2.6 via OpenRouter)

> **Requires: `OPENROUTER_API_KEY`**

- [ ] Create `agent/signal_engine.py`
- [ ] Implement OpenRouter API client:
  - [ ] Model: `openai/kimi-k2.6`
  - [ ] Endpoint: `https://openrouter.ai/api/v1/chat/completions`
  - [ ] Headers: `Authorization`, `HTTP-Referer`, `X-Title`
  - [ ] Settings: `temperature: 0.1`, `max_tokens: 400`, `response_format: json_object`
- [ ] Build signal evaluation prompt from strategy + indicator data
- [ ] Parse JSON response into `SignalResult`
- [ ] Handle parse failures gracefully (return `direction: "none"`)
- [ ] Log every signal to `signals` table (acted on or not)
- [ ] Test: evaluate a signal for BTC with RSI/MACD strategy, verify valid JSON response

---

## PHASE 8: Risk Manager

- [ ] Create `agent/risk_manager.py`
- [ ] Implement position sizing:
  - [ ] `risk_dollars = balance × risk_per_trade_pct`
  - [ ] `notional_size = risk_dollars / stop_distance_pct`
  - [ ] Safety cap: margin < 20% of account balance
- [ ] Implement stop loss calculation:
  - [ ] ATR method (default): `ATR(14) × atr_multiplier`
  - [ ] Fixed % method (fallback)
- [ ] Implement take profit: `risk_distance × take_profit_rr`
- [ ] Implement daily loss circuit breaker:
  - [ ] Track `daily_loss_usdc`
  - [ ] Halt trading when `daily_loss >= balance × daily_loss_limit_pct`
  - [ ] Reset at midnight UTC or manual reset
- [ ] Pre-trade checks:
  - [ ] `direction != "none"`
  - [ ] `confidence >= min_confidence`
  - [ ] No existing position on this coin
  - [ ] Circuit breaker not active

---

## PHASE 9: Executor (CCXT / Hyperliquid)

> **Requires: `HL_WALLET_ADDRESS`, `HL_API_PRIVATE_KEY`**

- [ ] Create `agent/executor.py`
- [ ] Initialize CCXT Hyperliquid client (`ccxt.hyperliquid`)
- [ ] Pair format: `BTC/USDC:USDC` (verify via `exchange.markets`)
- [ ] Implement order flow:
  - [ ] `set_leverage(leverage, symbol, isolated)` — must be called before orders
  - [ ] `create_market_order()` — entry
  - [ ] `create_stop_order()` — stop loss (`reduceOnly`, `triggerType: 'mark'`)
  - [ ] `create_limit_order()` — take profit (`reduceOnly`)
- [ ] **Non-negotiable**: no entry order without simultaneous stop loss
- [ ] Implement paper trading mode:
  - [ ] In-memory `paper_positions` dict
  - [ ] Simulate P&L using live `allMids` prices
  - [ ] Log paper trades to DB with `is_paper=true`
- [ ] Test: verify connectivity with `fetch_balance()` (paper mode)

---

## PHASE 10: Position Monitor

- [ ] Create `agent/position_monitor.py`
- [ ] Run as separate `asyncio.Task`, check every 30 seconds
- [ ] For live positions: compare `fetch_positions()` against `open_positions` dict
- [ ] For paper positions: compare entry to current mid price
- [ ] On position close:
  - [ ] Fetch fills for actual exit price + P&L
  - [ ] Update trade record in DB (`exit_price`, `pnl_usdc`, `status`, `closed_at`)
  - [ ] Update daily P&L for circuit breaker
  - [ ] Trigger Burt notification
  - [ ] Trigger memory formation

---

## PHASE 11: Memory Engine (Burt's Brain)

> **Requires: `OPENROUTER_API_KEY` (for embeddings), `DATABASE_URL` (pgvector)**

- [ ] Create `agent/memory_engine.py`
- [ ] Implement embedding generation:
  - [ ] Model: `openai/text-embedding-3-small` via OpenRouter
  - [ ] 1536 dimensions
  - [ ] Batch embedding support
- [ ] Register pgvector type on asyncpg connection pool
- [ ] Implement memory CRUD:
  - [ ] `store_memory(content, type, source, symbol?, strategy?)` — embed + insert
  - [ ] `search_memories(query, limit=5)` — embed query + cosine similarity search
  - [ ] `update_importance(memory_id, new_importance)`
- [ ] Implement memory formation from trade outcomes (automatic)
- [ ] Implement memory formation from user feedback (parsed from Discord)
- [ ] Implement nightly consolidation:
  - [ ] Schedule at 10:30 PM ET via apscheduler
  - [ ] Query day's trades + signals
  - [ ] Send consolidation prompt to Kimi K2.6
  - [ ] Store lessons + observations as memories
  - [ ] Store summary in `daily_consolidations`

---

## PHASE 12: Burt — Personality & Discord Bot

> **Requires: `DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID`, `DISCORD_USER_ID`**

- [ ] Create `agent/burt.py`
- [ ] Implement `Burt` class:
  - [ ] Discord client setup (use `discord.Client`, NOT `commands.Bot`)
  - [ ] Run in same asyncio event loop as trading agent (`asyncio.create_task`)
- [ ] Implement `on_message` handler:
  - [ ] Only respond in designated channel
  - [ ] Only respond to designated user
  - [ ] Store messages in `discord_messages` table
  - [ ] Generate response via Kimi K2.6 with full context (state + memories + working memory)
- [ ] Implement Burt's system prompt (BURT.md Section 5.4)
- [ ] Implement proactive messaging:
  - [ ] Morning brief at 6 AM ET
  - [ ] Trade opened/closed notifications in Burt's voice
  - [ ] Circuit breaker alerts
  - [ ] End of day summary at 10 PM ET
  - [ ] Max 1 proactive message per hour (except urgent)
  - [ ] Active hours check (6 AM - 10 PM ET)
- [ ] Implement natural language command handling (BURT.md Section 6):
  - [ ] "what are you looking at" → screener results
  - [ ] "what's open" → open positions
  - [ ] "how'd we do today" → daily summary
  - [ ] "go live" → double confirmation safety gate
  - [ ] "stop trading" / "resume" → pause/unpause
  - [ ] "close [coin]" → close with confirmation
  - [ ] "remember that" / "forget about X" → memory operations
- [ ] Implement `notify_trade_opened()`, `notify_trade_closed()`, `notify_circuit_breaker()`
- [ ] Burt goes quiet after 10 PM ET (one emergency exception allowed)

---

## PHASE 13: Notifier (Fallback Webhook)

- [ ] Create `agent/notifier.py`
- [ ] If Burt is running (Discord bot active), delegate to `burt.notify_*()` methods
- [ ] If Burt is not running (no bot token), fall back to Discord webhook:
  - [ ] Trade opened embed (green for long, red for short)
  - [ ] Trade closed embed (green for profit, red for loss)
  - [ ] Circuit breaker embed (red, 0xff0000)
  - [ ] Daily summary embed
- [ ] Embed colors: `0x00ff88` long/profit, `0xff4455` short/loss, `0xffaa00` warning

---

## PHASE 14: FastAPI Backend

- [ ] Create `agent/api.py`
- [ ] Run at `localhost:8000` via uvicorn in background thread
- [ ] CORS: allow `http://localhost:5173` only
- [ ] Implement endpoints:
  - [ ] `GET /api/status` — config, agent state, circuit breaker
  - [ ] `PATCH /api/config` — update config (write to DB + in-memory)
  - [ ] `GET /api/trades` — recent trades (limit 50)
  - [ ] `GET /api/signals` — recent signals (limit 100)
  - [ ] `GET /api/stats` — today's P&L, win rate, trade count
  - [ ] `GET /api/watchlist` — current watchlist with scores
  - [ ] `POST /api/screener/run` — trigger immediate re-run
  - [ ] `GET /api/positions` — current open positions
  - [ ] `POST /api/positions/{id}/close` — manually close position
  - [ ] `POST /api/circuit-breaker/reset` — manual reset

---

## PHASE 15: Main Agent Loop

- [ ] Create `agent/main.py` — entry point
- [ ] Implement startup sequence (BLUEPRINT.md Section 15):
  1. Load and validate config (fail fast on missing required keys)
  2. Connect to Neon DB (asyncpg pool)
  3. Run DB migrations (CREATE TABLE IF NOT EXISTS)
  4. Connect CCXT Hyperliquid (`load_markets`)
  5. Verify API connectivity (`fetch_balance`)
  6. Run initial screener → set active watchlist
  7. Start FastAPI server in background thread
  8. Start position monitor background task
  9. Start Burt (Discord bot) as asyncio task
  10. Log startup to Discord
  11. Enter main signal loop
- [ ] Main signal loop (every `signal_interval` seconds):
  - [ ] `await db.sync_config()` at top of each iteration
  - [ ] Re-run screener every 4 hours
  - [ ] For each coin in watchlist: fetch data → compute indicators → evaluate signal → risk check → execute/skip
- [ ] Graceful shutdown (close positions? notification? cleanup)
- [ ] Test: run agent, verify startup sequence completes and loop executes

---

## PHASE 16: SvelteKit Dashboard

- [ ] Initialize SvelteKit 2 project in `ui/` directory
- [ ] Install dependencies: `npm install`
- [ ] Create `src/lib/api.ts` — fetch wrappers for FastAPI backend at `localhost:8000`
- [ ] Create `src/lib/types.ts` — shared TypeScript types
- [ ] Use **Svelte 5 runes** (`$state`, `$derived`, `$effect`) — NOT Svelte 4 stores
- [ ] Create `src/routes/+layout.svelte` — app shell, nav, dark theme
- [ ] Create `src/routes/+page.svelte` — main dashboard with:
  - [ ] **Header bar**: agent status dot, Paper/Live badge, today's P&L, strategy name, circuit breaker status
  - [ ] **Left sidebar — Controls**:
    - [ ] Paper/Live toggle (typing "CONFIRM" to go live)
    - [ ] Strategy selector dropdown
    - [ ] Leverage slider (1-20x)
    - [ ] Risk per trade slider (0.5-5%)
    - [ ] Daily loss limit slider (1-20%)
    - [ ] Min confidence slider (0.5-0.9)
    - [ ] Stop loss method toggle (ATR / Fixed %)
    - [ ] ATR multiplier / Fixed % inputs
    - [ ] Take profit R:R slider (1-5)
    - [ ] Max watchlist size (1-10)
    - [ ] Signal interval selector
    - [ ] "Re-run screener" button
    - [ ] Current watchlist chips with scores
  - [ ] **Main area — Tabs**:
    - [ ] Positions tab (polls every 10s, manual close button)
    - [ ] Trades tab (color-coded P&L)
    - [ ] Signals tab (confidence bar, reasoning)
    - [ ] Screener tab (full score breakdown)
- [ ] Auto-save config changes (debounced 500ms, PATCH to `/api/config`)
- [ ] Polling: status 5s, positions 10s, trades/signals 30s

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
