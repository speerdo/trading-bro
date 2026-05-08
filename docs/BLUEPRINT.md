# TradeBrain — Full System Blueprint

> **Purpose**: This document is the authoritative specification for building TradeBrain.
> Claude Code should read this entirely before writing any code. Every architectural decision,
> data flow, file structure, and implementation detail is here. Do not deviate without good reason.

---

## 1. What TradeBrain Is

TradeBrain is a personal AI-powered crypto trading agent that:

- **Screens** all Hyperliquid perpetual markets every session to find the best candidates (Option B screener)
- **Evaluates** signals using Kimi K2.6 via OpenRouter against a chosen strategy
- **Executes** leveraged long/short positions on Hyperliquid via CCXT
- **Manages risk** with mandatory stop losses, position sizing, and a daily loss circuit breaker
- **Notifies** via Discord webhook on every trade, skip, and daily summary
- **Logs** everything to Neon Postgres
- **Exposes** a local SvelteKit 5 dashboard at `localhost:5173` for real-time control

It runs entirely on your local Fedora machine. No cloud compute. No third-party holding your keys.
Paper trading is ON by default. One toggle in the UI switches to live.

---

## 2. Tech Stack

| Layer | Technology | Notes |
|---|---|---|
| Signal brain | Kimi K2.6 via OpenRouter | `openai/kimi-k2.6` model ID, OpenAI-compatible API |
| Exchange | Hyperliquid perps | No KYC, USDC collateral, up to 50x leverage |
| Market data (primary) | Hyperliquid REST + WebSocket | `api.hyperliquid.xyz/info` — free, no key required |
| Market data (supplementary) | MoonDev Hyperliquid Data API | Rate-limit-free OHLCV, funding, liquidation data |
| Indicator computation | `pandas-ta` | Compute RSI, MACD, BB, EMA, ATR in Python from OHLCV |
| Execution | CCXT (`ccxt.async_support`) | `ccxt.hyperliquid`, ECDSA via Coincurve |
| UI | SvelteKit 2, Svelte 5 runes | Local only, `localhost:5173` |
| API bridge | FastAPI + uvicorn | `localhost:8000`, connects UI to agent state |
| Database | Neon (Postgres) | `asyncpg`, trade log + signal log + config store |
| Notifications | Discord webhook | No bot token needed |
| Language | Python 3.11+ (agent), TypeScript (UI) |  |
| OS | Fedora Linux |  |

---

## 3. Data Sources — Detailed

### 3.1 Hyperliquid REST API (Primary, Free)

Base URL: `https://api.hyperliquid.xyz/info`
All requests are POST with JSON body. No authentication required for read-only data.

**Get all markets + metadata (for screener):**
```json
POST /info
{ "type": "metaAndAssetCtxs" }
```
Returns array of all perp markets with: `name`, `maxLeverage`, `openInterest`, `funding`, `markPx`, `prevDayPx`, `dayNtlVlm` (24h volume in notional).

**Get OHLCV candles:**
```json
POST /info
{
  "type": "candleSnapshot",
  "req": {
    "coin": "BTC",
    "interval": "15m",
    "startTime": <unix_ms>,
    "endTime": <unix_ms>
  }
}
```
Returns array of candles: `{ T, c, h, i, l, n, o, s, t, v }` (close, high, interval, low, trades, open, symbol, time, volume).
**Limit: 5000 candles per request.** For 15m candles, 5000 candles = ~52 days of history. Sufficient for all indicators.

**Supported intervals:** `1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 1w`

**Get funding rates for all assets:**
Included in `metaAndAssetCtxs` response. Field: `funding` per asset context.

### 3.2 Hyperliquid WebSocket (Real-time)

URL: `wss://api.hyperliquid.xyz/ws`

Subscribe to live candles:
```json
{ "method": "subscribe", "subscription": { "type": "candle", "coin": "BTC", "interval": "15m" } }
```

Subscribe to all mid prices (for screener updates):
```json
{ "method": "subscribe", "subscription": { "type": "allMids" } }
```

Use WebSocket for real-time price monitoring of open positions and live candle updates.
Use REST for initial data load and screener runs.

### 3.3 MoonDev Hyperliquid Data API (Supplementary)

Base URL: `https://api.moondev.com`
Requires `X-API-Key` header. Sign up at `moondev.com`.

**Why use this in addition to native Hyperliquid API:**
- No rate limits (Hyperliquid native has volume-based limits)
- Pre-computed cross-exchange liquidation data (Binance + Bybit + OKX + Hyperliquid combined)
- Position snapshot data — who is near liquidation (powerful for squeeze detection)
- Serves as a fallback if Hyperliquid API is slow

**Key endpoints:**
```
GET /api/prices          — all 224 asset prices at once
GET /api/candles/{coin}?interval=15m  — OHLCV candles
GET /api/all_liquidations/           — cross-exchange liquidation events
GET /api/position_snapshots/{coin}   — positions near liquidation
```

MoonDev data is optional/supplementary. The agent must work without it if the key is not set.

### 3.4 What We Do NOT Use

- **TradingView MCP**: Dropped. Too fragile for unattended operation (requires GUI app running).
- **TradingView public API**: Does not exist. `tradingview.com` has no public data API.
- **`tradingviewapi.com`**: Third-party reseller, not official. Unnecessary given Hyperliquid's free native API.
- **`yfinance` / Alpha Vantage**: Not relevant for crypto perps on Hyperliquid.

---

## 4. Asset Selection — The Screener (Option B)

The screener runs once at agent startup and then every 4 hours during a session.
It scans all Hyperliquid perp markets and scores each one, returning the top N candidates for signal evaluation.

### 4.1 Screener Scoring Algorithm

For each market, compute a composite score from these factors:

**Factor 1: 24h Volume Score (weight: 30%)**
- Raw: `dayNtlVlm` from `metaAndAssetCtxs`
- Normalize to 0-1 across all markets
- Higher volume = better liquidity = lower slippage

**Factor 2: Volatility Score (weight: 25%)**
- Compute ATR(14) on 1H candles
- Express as ATR/price (normalized volatility %)
- Target range: 1-5% ATR. Too low = no movement, too high = liquidation risk
- Score peaks at ~2-3% and falls off at extremes

**Factor 3: Funding Rate Score (weight: 20%)**
- Raw: `funding` from `metaAndAssetCtxs` (hourly rate)
- Extreme positive funding (>0.05%) = crowded longs = short opportunity
- Extreme negative funding (<-0.05%) = crowded shorts = long opportunity
- Neutral funding (near 0) = balanced = good for either direction
- Score: abs(funding) * direction_alignment_bonus

**Factor 4: Trend Clarity Score (weight: 15%)**
- Compute EMA(20) and EMA(50) on 1H candles
- Score higher when EMAs are clearly separated (trending)
- Score lower when EMAs are tangled (choppy/ranging, bad for RSI/MACD)

**Factor 5: Open Interest Score (weight: 10%)**
- Higher OI = more institutional interest = more reliable signals
- Normalize across all markets

**Minimum thresholds (disqualify before scoring):**
- 24h volume < $5M: skip (too illiquid)
- Max leverage < 10x: skip
- Mark price < $0.0001: skip (micro-cap, unreliable data)

**Output:** Top 5-10 assets by composite score. These become the active watchlist for signal evaluation.

### 4.2 Screener Implementation

```python
# agent/screener.py pseudocode

async def run_screener() -> list[str]:
    """Returns list of coin names e.g. ['BTC', 'ETH', 'SOL']"""
    
    # 1. Fetch all market data
    meta, ctxs = await hl_client.get_meta_and_asset_ctxs()
    
    # 2. Filter minimum thresholds
    candidates = [
        (asset, ctx) for asset, ctx in zip(meta['universe'], ctxs)
        if float(ctx['dayNtlVlm']) > 5_000_000
        and asset['maxLeverage'] >= 10
    ]
    
    # 3. Fetch 1H candles for each candidate (last 100 bars)
    # Use asyncio.gather for concurrent fetching
    
    # 4. Compute indicators with pandas-ta
    # df.ta.atr(length=14), df.ta.ema(length=20), df.ta.ema(length=50)
    
    # 5. Score each candidate
    scores = []
    for coin, ctx, df in candidate_data:
        score = compute_score(ctx, df)
        scores.append((coin, score))
    
    # 6. Return top N by score
    scores.sort(key=lambda x: x[1], reverse=True)
    return [coin for coin, _ in scores[:cfg.max_watchlist_size]]
```

---

## 5. Signal Evaluation — Kimi K2.6

### 5.1 How It Works

For each asset in the screened watchlist, on each signal loop iteration:

1. Fetch fresh 15m OHLCV candles from Hyperliquid (last 100 bars)
2. Fetch 1H OHLCV candles (last 50 bars) for trend filter
3. Compute indicators using `pandas-ta` on the raw OHLCV DataFrames
4. Build a structured prompt with all indicator values
5. Send to Kimi K2.6 via OpenRouter
6. Parse the JSON response into a `SignalResult`

### 5.2 Indicator Computation

All indicators computed locally in Python using `pandas-ta`. No external API needed.

```python
import pandas as pd
import pandas_ta as ta

def compute_indicators(df_15m: pd.DataFrame, df_1h: pd.DataFrame) -> dict:
    # RSI
    df_15m['rsi'] = ta.rsi(df_15m['close'], length=14)
    
    # MACD
    macd = ta.macd(df_15m['close'], fast=12, slow=26, signal=9)
    df_15m = df_15m.join(macd)
    
    # Bollinger Bands
    bb = ta.bbands(df_15m['close'], length=20, std=2)
    df_15m = df_15m.join(bb)
    
    # ATR (for stop loss calculation)
    df_15m['atr'] = ta.atr(df_15m['high'], df_15m['low'], df_15m['close'], length=14)
    
    # Volume ratio (current vs 20-bar SMA)
    df_15m['vol_sma'] = df_15m['volume'].rolling(20).mean()
    df_15m['vol_ratio'] = df_15m['volume'] / df_15m['vol_sma']
    
    # 1H trend filter
    df_1h['ema50'] = ta.ema(df_1h['close'], length=50)
    df_1h['ema20'] = ta.ema(df_1h['close'], length=20)
    df_1h['rsi'] = ta.rsi(df_1h['close'], length=14)
    
    # Return last-bar values for prompt
    last = df_15m.iloc[-1]
    prev = df_15m.iloc[-2]
    last_1h = df_1h.iloc[-1]
    
    return {
        '15m': {
            'price': last['close'],
            'high': last['high'],
            'low': last['low'],
            'rsi': last['rsi'],
            'rsi_prev': prev['rsi'],
            'macd_line': last['MACD_12_26_9'],
            'macd_signal': last['MACDs_12_26_9'],
            'macd_hist': last['MACDh_12_26_9'],
            'macd_hist_prev': prev['MACDh_12_26_9'],
            'bb_upper': last['BBU_20_2.0'],
            'bb_middle': last['BBM_20_2.0'],
            'bb_lower': last['BBL_20_2.0'],
            'bb_width': (last['BBU_20_2.0'] - last['BBL_20_2.0']) / last['BBM_20_2.0'],
            'atr': last['ATRr_14'],
            'vol_ratio': last['vol_ratio'],
        },
        '1h': {
            'price': last_1h['close'],
            'ema20': last_1h['ema20'],
            'ema50': last_1h['ema50'],
            'price_vs_ema50': 'above' if last_1h['close'] > last_1h['ema50'] else 'below',
            'rsi': last_1h['rsi'],
        }
    }
```

### 5.3 OpenRouter API Call

Model: `openai/kimi-k2.6`
Endpoint: `https://openrouter.ai/api/v1/chat/completions`
Auth: `Authorization: Bearer {OPENROUTER_API_KEY}`

Headers required by OpenRouter:
```
HTTP-Referer: https://github.com/tradebrain
X-Title: TradeBrain
```

Settings:
- `temperature: 0.1` (low for deterministic structured output)
- `max_tokens: 400`
- `response_format: { "type": "json_object" }` (enforces JSON output)

Expected cost per call: ~$0.002 (2000 input tokens × $0.75/1M + 300 output tokens × $3.50/1M)
Expected monthly cost at 1-minute intervals on 5 pairs: ~$20. At 5-minute intervals: ~$4.

### 5.4 System Prompt

```
You are TradeBrain, an expert crypto futures trading signal evaluator.
You analyze live technical indicator data and determine whether a trading 
signal meets the defined strategy criteria for a leveraged position on Hyperliquid.

Rules:
- Only signal "long" or "short" when ALL required conditions are clearly met
- When in doubt, return "none" — missing a trade is better than a bad trade  
- Be concise in reasoning — max 2 sentences
- ALWAYS return valid JSON only, no markdown, no preamble
- Never recommend a trade without clear technical justification from the data provided
- Consider funding rate when provided — extreme positive funding favors shorts, extreme negative favors longs
```

### 5.5 Signal Response Schema

Kimi K2.6 must return exactly this JSON:
```json
{
  "direction": "long" | "short" | "none",
  "confidence": 0.0-1.0,
  "reasoning": "string max 150 chars",
  "entry_price": number | null,
  "invalidation": "string — what price action would cancel this setup"
}
```

Minimum confidence to act: **0.65**. Below this, log the signal but skip the trade.

---

## 6. Risk Management

### 6.1 Position Sizing

```
risk_dollars = account_balance_usdc × risk_per_trade_pct
stop_distance_pct = abs(entry_price - stop_loss_price) / entry_price
notional_size = risk_dollars / stop_distance_pct
margin_required = notional_size / leverage

Safety cap: margin_required must not exceed 20% of account balance.
If it does, scale down notional_size to fit within cap.
```

### 6.2 Stop Loss Calculation

**ATR method (default):**
```
stop_distance = ATR(14) × atr_multiplier (default: 1.5)
long stop = entry_price - stop_distance
short stop = entry_price + stop_distance
```

**Fixed % method (fallback):**
```
stop_distance = entry_price × fixed_stop_pct (default: 2%)
```

Stop loss is placed as a separate `stop` order on Hyperliquid immediately after entry.
**No trade is placed without a simultaneous stop loss order. This is non-negotiable.**

### 6.3 Take Profit

```
risk_distance = abs(entry_price - stop_loss)
take_profit_distance = risk_distance × take_profit_rr (default: 2.0)
long tp = entry_price + take_profit_distance
short tp = entry_price - take_profit_distance
```

Take profit is placed as a `limit` reduceOnly order.

### 6.4 Daily Loss Circuit Breaker

At the start of each session, daily_loss_usdc = 0.
Every time a position closes at a loss, add abs(pnl) to daily_loss_usdc.

```
if daily_loss_usdc >= account_balance × daily_loss_limit_pct:
    circuit_breaker = True
    halt all new trades
    send Discord alert
    log to DB
```

The circuit breaker resets at midnight UTC or when the user manually resets via the UI.

### 6.5 Default Parameters (all adjustable in UI)

| Parameter | Default | Min | Max |
|---|---|---|---|
| Leverage | 3x | 1x | 20x |
| Risk per trade | 1% | 0.5% | 5% |
| Daily loss limit | 5% | 1% | 20% |
| ATR multiplier | 1.5 | 0.5 | 4.0 |
| Take profit R:R | 2.0 | 1.0 | 5.0 |
| Signal interval | 5 min | 1 min | 60 min |
| Max watchlist | 5 | 1 | 10 |
| Min confidence | 0.65 | 0.5 | 0.9 |

---

## 7. Strategies

Each strategy is a Python class that inherits from `BaseStrategy`.
A strategy defines:
- Which indicators it needs (for the indicator computation step)
- The prompt fragment it injects into the Kimi K2.6 evaluation
- How to parse the response

### 7.1 Strategy 1: RSI + MACD Momentum (Default)

**Use case:** Trending markets. Catches momentum reversals on the 15m chart.

**Long conditions (ALL required):**
1. RSI(14) crossed above 30 from below (was <30, now >30) OR RSI between 30-45 with bullish MACD cross
2. MACD line crossed above signal line
3. MACD histogram increasing (turning positive or growing)
4. 1H price is ABOVE the 50 EMA (uptrend filter)

**Short conditions (ALL required):**
1. RSI(14) crossed below 70 from above (was >70, now <70) OR RSI between 55-70 with bearish MACD cross
2. MACD line crossed below signal line
3. MACD histogram decreasing (turning negative or falling)
4. 1H price is BELOW the 50 EMA (downtrend filter)

**Avoid:** When RSI is in the middle (40-60) with no clear MACD direction.

### 7.2 Strategy 2: Bollinger Band Mean Reversion

**Use case:** Ranging markets. Fades overextension back to the mean.

**Long conditions (ALL required):**
1. Candle wick touches or crosses below BB lower band
2. RSI(14) < 35
3. Candle closes back above the lower band (rejection wick preferred)
4. BB width is not extremely narrow (> 1% of price — avoids flat consolidation)

**Short conditions (ALL required):**
1. Candle wick touches or crosses above BB upper band
2. RSI(14) > 65
3. Candle closes back below the upper band
4. BB width is not extremely narrow

**Target:** Middle band (BB basis / 20 EMA).
**Stop:** Beyond the wick that touched the band.

### 7.3 Strategy 3: EMA Trend + Pullback

**Use case:** Strong trending markets. Buys/sells pullbacks to the 20 EMA.

**Long conditions (ALL required):**
1. 1H: Price above 20 EMA AND 20 EMA above 50 EMA (clear uptrend)
2. 15m: Price pulls back to within 0.5% of 20 EMA
3. 15m: Bullish candle forming off the EMA (close above open, higher than prev close)
4. RSI(14) between 40-60 (pulled back from overbought, not yet oversold)

**Short conditions:** Inverse of above.

### 7.4 Adding New Strategies

To add a new strategy:
1. Create `strategies/your_strategy.py` inheriting from `BaseStrategy`
2. Implement `build_prompt(market_data) -> str` and `parse_response(response, market_data) -> SignalResult`
3. Register it in `strategies/__init__.py` STRATEGIES dict
4. It appears automatically in the UI strategy selector

---

## 8. Execution Flow (End-to-End)

```
Every {signal_interval} seconds:

1. SCREENER (every 4 hours or on startup)
   └── Fetch metaAndAssetCtxs from Hyperliquid
   └── Score all markets
   └── Set active watchlist (top N coins)

2. For each coin in active watchlist:
   
   a. DATA FETCH
      └── Fetch 15m candles (last 100 bars) from Hyperliquid candleSnapshot
      └── Fetch 1H candles (last 50 bars) from Hyperliquid candleSnapshot
      └── If MoonDev key set: fetch liquidation context (optional enrichment)
   
   b. INDICATOR COMPUTATION
      └── pandas-ta computes RSI, MACD, BB, EMA, ATR on local DataFrames
      └── Produces indicator dict for prompt
   
   c. SIGNAL EVALUATION
      └── Build prompt from active strategy + indicator dict
      └── POST to OpenRouter (Kimi K2.6)
      └── Parse JSON response → SignalResult
      └── Log signal to Neon DB (signals table)
   
   d. PRE-TRADE CHECKS
      └── direction == "none"? → skip
      └── confidence < min_confidence? → skip + log skip_reason
      └── Already in a position on this coin? → skip
      └── Circuit breaker active? → skip + alert
   
   e. RISK CALCULATION
      └── Calculate stop loss (ATR or fixed %)
      └── Calculate position size from risk % and stop distance
      └── Calculate take profit from R:R ratio
      └── Check margin < 20% of balance cap
   
   f. ORDER PLACEMENT (or paper log)
      └── CCXT: set_leverage(leverage, coin, isolated)
      └── CCXT: create_market_order (entry)
      └── CCXT: create_stop_order (stop loss, reduceOnly)
      └── CCXT: create_limit_order (take profit, reduceOnly)
      └── Log trade to Neon DB (trades table)
      └── Track in open_positions dict
   
   g. NOTIFICATION
      └── Discord webhook: trade opened embed
   
3. POSITION MONITORING (parallel task, every 30s)
   └── For each open position: fetch current P&L from CCXT
   └── Detect if stop or TP was hit (position closed)
   └── Update trade record in DB with exit price + P&L
   └── Update daily P&L for circuit breaker
   └── Send Discord notification on close

4. DAILY SUMMARY (at 17:00 local time)
   └── Query DB for day's trades
   └── Compute win rate, total P&L, best/worst trade
   └── Discord webhook: summary embed
   └── Reset circuit breaker and daily counters
```

---

## 9. File Structure

```
tradebrain/
├── README.md
├── BLUEPRINT.md                    ← this file
├── .env.example
├── .env                            ← gitignored
├── .gitignore
├── requirements.txt
│
├── config.py                       ← central config, loads .env, hot-reloadable
│
├── agent/
│   ├── __init__.py
│   ├── main.py                     ← entry point, main loop, startup/shutdown
│   ├── screener.py                 ← market screener, scores all HL assets
│   ├── data_client.py              ← Hyperliquid REST + WebSocket client
│   ├── indicator_engine.py         ← pandas-ta indicator computation
│   ├── signal_engine.py            ← OpenRouter / Kimi K2.6 client
│   ├── risk_manager.py             ← position sizing, stops, circuit breaker
│   ├── executor.py                 ← CCXT Hyperliquid order placement
│   ├── position_monitor.py         ← tracks open positions, detects closes
│   ├── database.py                 ← Neon asyncpg client + schema
│   └── notifier.py                 ← Discord webhook
│
├── strategies/
│   ├── __init__.py                 ← strategy registry
│   ├── base.py                     ← BaseStrategy ABC, SignalResult dataclass
│   ├── rsi_macd.py                 ← Strategy 1
│   ├── bollinger.py                ← Strategy 2
│   └── ema_pullback.py             ← Strategy 3
│
├── ui/                             ← SvelteKit 2 project
│   ├── package.json
│   ├── svelte.config.js
│   ├── vite.config.ts
│   ├── tsconfig.json
│   └── src/
│       ├── app.html
│       ├── lib/
│       │   ├── api.ts              ← fetch wrappers for FastAPI backend
│       │   ├── types.ts            ← shared TypeScript types
│       │   └── stores.ts           ← Svelte stores for reactive state
│       └── routes/
│           ├── +layout.svelte      ← app shell, nav, dark theme
│           └── +page.svelte        ← main dashboard
│
├── scripts/
│   ├── setup_db.py                 ← creates Neon schema (run once)
│   └── backtest.py                 ← simple backtester (Phase 2)
│
└── logs/
    └── .gitkeep
```

---

## 10. Database Schema (Neon Postgres)

```sql
-- Signal log: every evaluation, acted on or not
CREATE TABLE signals (
    id              SERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    symbol          TEXT NOT NULL,
    direction       TEXT NOT NULL,        -- 'long' | 'short' | 'none'
    strategy        TEXT NOT NULL,
    confidence      FLOAT NOT NULL,
    reasoning       TEXT,
    acted_on        BOOLEAN DEFAULT FALSE,
    skip_reason     TEXT,                 -- why it was skipped if not acted on
    -- Indicator snapshot for analysis
    rsi_15m         FLOAT,
    macd_hist_15m   FLOAT,
    atr_15m         FLOAT,
    price           FLOAT
);

-- Trade log: every position opened (paper or live)
CREATE TABLE trades (
    id              SERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    symbol          TEXT NOT NULL,
    direction       TEXT NOT NULL,        -- 'long' | 'short'
    strategy        TEXT NOT NULL,
    confidence      FLOAT,
    entry_price     FLOAT NOT NULL,
    stop_loss       FLOAT NOT NULL,
    take_profit     FLOAT NOT NULL,
    size_usdc       FLOAT NOT NULL,       -- notional size
    margin_usdc     FLOAT NOT NULL,       -- actual margin used
    leverage        INT NOT NULL,
    risk_usdc       FLOAT NOT NULL,       -- dollars at risk
    is_paper        BOOLEAN DEFAULT TRUE,
    status          TEXT DEFAULT 'open',  -- 'open' | 'closed_tp' | 'closed_sl' | 'closed_manual'
    exit_price      FLOAT,
    pnl_usdc        FLOAT,
    closed_at       TIMESTAMPTZ,
    reasoning       TEXT,
    order_id        TEXT,
    signal_id       INT REFERENCES signals(id)
);

-- Config store: UI writes here, agent reads here for hot-reload
CREATE TABLE agent_config (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Screener history: what was selected and why
CREATE TABLE screener_runs (
    id              SERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    selected_coins  TEXT[],               -- array of coin names
    scores          JSONB                 -- full score breakdown
);

-- Indexes
CREATE INDEX idx_trades_created ON trades(created_at DESC);
CREATE INDEX idx_trades_symbol ON trades(symbol);
CREATE INDEX idx_signals_created ON signals(created_at DESC);
CREATE INDEX idx_signals_acted ON signals(acted_on);
```

---

## 11. FastAPI Backend (agent/api.py)

Runs at `localhost:8000`. Connects the SvelteKit UI to agent state.

### Endpoints

```
GET  /api/status          — current config, agent state, circuit breaker status
PATCH /api/config         — update any config field (written to DB + in-memory)

GET  /api/trades          — recent trades (default limit 50)
GET  /api/signals         — recent signals (default limit 100)
GET  /api/stats           — today's P&L, win rate, trade count

GET  /api/watchlist       — current screened watchlist with scores
POST /api/screener/run    — trigger immediate screener re-run

GET  /api/positions       — current open positions from CCXT
POST /api/positions/{id}/close — manually close a position

POST /api/circuit-breaker/reset — manually reset the circuit breaker
```

CORS: allow `http://localhost:5173` only.

---

## 12. SvelteKit Dashboard

Uses **Svelte 5 runes** (`$state`, `$derived`, `$effect`). Not Svelte 4 stores syntax.

### Dashboard Sections

**Header bar:**
- Agent status indicator (green/yellow/red dot)
- Paper / Live badge (big, obvious)
- Today's P&L
- Active strategy name
- Circuit breaker status

**Left sidebar — Controls:**

*Mode section:*
- Paper/Live toggle (requires typing "CONFIRM" to go live — safety gate)

*Strategy selector:*
- Dropdown of all registered strategies

*Risk controls:*
- Leverage slider (1-20x, shows value)
- Risk per trade slider (0.5-5%, shows value)
- Daily loss limit slider (1-20%, shows value)
- Min confidence slider (0.5-0.9)

*Stop loss section:*
- Method toggle (ATR / Fixed %)
- ATR multiplier slider (if ATR selected)
- Fixed % input (if Fixed selected)
- Take profit R:R slider (1-5)

*Screener section:*
- Max watchlist size (1-10)
- Signal interval (1, 5, 15, 30, 60 minutes)
- "Re-run screener now" button
- Current watchlist chips with scores

**Main area — Tabs:**

*Positions tab:*
- Live open positions table (polls every 10s)
- Columns: Symbol, Direction, Entry, Current, P&L $, P&L %, Stop, TP, Time open
- Manual close button per position

*Trades tab:*
- Closed trades table
- Columns: Time, Symbol, Dir, Strategy, Entry, Exit, P&L $, R multiple, Status
- Color coded: green = profit, red = loss, yellow = open

*Signals tab:*
- All evaluated signals
- Columns: Time, Symbol, Dir, Confidence (bar), Strategy, Acted, Skip reason, Reasoning
- Helps you understand what the AI is seeing

*Screener tab:*
- Full screener results with scores breakdown per coin
- Shows why each coin was included/excluded

**All config changes auto-save** (debounced 500ms). No save button needed.
**Polling intervals:** Status every 5s, positions every 10s, trades/signals every 30s.

---

## 13. Environment Variables

```bash
# Required
OPENROUTER_API_KEY=sk-or-...
HL_WALLET_ADDRESS=0x...          # Your main Hyperliquid wallet address
HL_API_PRIVATE_KEY=0x...         # API wallet private key (NOT main wallet key)
DATABASE_URL=postgresql://...    # Neon connection string

# Optional but recommended
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
MOONDEV_API_KEY=...              # moondev.com — for supplementary data

# Agent defaults (all overridable in UI)
PAPER_TRADING=true
DEFAULT_LEVERAGE=3
DEFAULT_RISK_PER_TRADE=0.01      # 1%
DEFAULT_DAILY_LOSS_LIMIT=0.05    # 5%
DEFAULT_STRATEGY=rsi_macd
DEFAULT_SIGNAL_INTERVAL=300      # 5 minutes in seconds
DEFAULT_MAX_WATCHLIST=5

# HL testnet (optional, for testing with fake money)
HL_TESTNET=false
```

---

## 14. Python Dependencies (requirements.txt)

```
# Exchange + data
ccxt>=4.4.87
coincurve>=18.0.0            # fast ECDSA for Hyperliquid order signing
aiohttp>=3.9.0
websockets>=12.0

# Indicators
pandas>=2.2.0
pandas-ta>=0.3.14b

# Database
asyncpg>=0.29.0

# API + config
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
python-dotenv>=1.0.0
pydantic>=2.7.0
httpx>=0.27.0

# Utilities
loguru>=0.7.2
apscheduler>=3.10.4
```

---

## 15. Startup Sequence (agent/main.py)

```python
async def startup():
    1. Load and validate config (fail fast on missing required keys)
    2. Connect to Neon DB (asyncpg pool)
    3. Run DB migrations (CREATE TABLE IF NOT EXISTS)
    4. Connect CCXT Hyperliquid (load_markets)
    5. Verify Hyperliquid API connectivity (fetch balance)
    6. Run initial screener → set active watchlist
    7. Start FastAPI server in background thread (uvicorn)
    8. Start position monitor background task
    9. Log startup summary to Discord
    10. Enter main signal loop
```

---

## 16. Running the System

### Start everything (3 terminals):

```bash
# Terminal 1: Agent + API backend
cd tradebrain
source venv/bin/activate
python -m agent.main

# Terminal 2: SvelteKit UI
cd tradebrain/ui
npm run dev

# That's it. Dashboard at http://localhost:5173
```

### First-time setup:

```bash
# Install Python deps
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Create DB schema
python scripts/setup_db.py

# Copy and fill env
cp .env.example .env
# Edit .env

# Install UI deps
cd ui && npm install
```

### Generate Hyperliquid API wallet:
1. Go to `app.hyperliquid.xyz/API`
2. Paste your main wallet address
3. Click Generate → Authorize API Wallet → sign transaction
4. Copy the private key shown (store securely)
5. Set `HL_WALLET_ADDRESS` = your main wallet address (not API wallet)
6. Set `HL_API_PRIVATE_KEY` = the API wallet private key just generated

---

## 17. Key Implementation Notes for Claude Code

1. **Hyperliquid OHLCV response format**: candle fields are single letters (`o`, `h`, `l`, `c`, `v`, `t`, `T`). Always rename to full names before passing to pandas-ta.

2. **CCXT Hyperliquid pair format**: use `BTC/USDC:USDC` for perps (not `BTCUSDT`). Confirm by checking `exchange.markets` after `load_markets()`.

3. **Stop orders on Hyperliquid via CCXT**: use `type='stop'` with `params={'stopPrice': sl_price, 'reduceOnly': True, 'triggerType': 'mark'}`. Mark price triggers are more reliable than last price for crypto perps.

4. **Leverage must be set before order placement**: call `exchange.set_leverage(leverage, symbol, params={'marginMode': 'isolated'})` before `create_market_order`.

5. **pandas-ta column names**: MACD columns are `MACD_12_26_9`, `MACDh_12_26_9`, `MACDs_12_26_9`. BB columns are `BBL_20_2.0`, `BBM_20_2.0`, `BBU_20_2.0`. ATR column is `ATRr_14`.

6. **OpenRouter JSON mode**: include `"response_format": {"type": "json_object"}` in the request body. Always wrap the parse in try/except and return a `direction: "none"` signal on failure.

7. **Screener concurrency**: use `asyncio.gather()` with a semaphore (max 10 concurrent) when fetching candles for all candidate markets to avoid overwhelming Hyperliquid's rate limits.

8. **Config hot-reload**: the agent's main loop calls `await db.sync_config()` at the top of each iteration. This reads any keys in `agent_config` table and overwrites the in-memory `cfg` object. The UI writes to this table via PATCH `/api/config`.

9. **Svelte 5 runes**: use `$state()`, `$derived()`, `$effect()`. Do NOT use Svelte 4 `writable()` stores or the `$store` prefix syntax.

10. **Position monitoring**: run as a separate `asyncio.Task`, not in the main signal loop. Check every 30 seconds. Detect closed positions by comparing CCXT `fetch_positions()` against the `open_positions` dict. When a position disappears, fetch fills to get the actual exit price and P&L.

11. **Paper trading position tracking**: since paper trades don't exist on Hyperliquid, maintain a separate in-memory `paper_positions` dict. Simulate P&L by comparing entry price to current mid price from Hyperliquid `allMids`.

12. **Discord embed colors**: use `0x00ff88` for long/profit, `0xff4455` for short/loss, `0xffaa00` for warnings, `0xff0000` for circuit breaker.

13. **Minimum viable first run**: get the screener running and printing scores to console before wiring up signal evaluation. Then add signal evaluation before execution. Build in layers, not all at once.

---

## 18. What Success Looks Like (Phase 1)

Phase 1 is complete when:
- [ ] Screener runs and selects top 5 coins from all Hyperliquid markets
- [ ] Indicators compute correctly from Hyperliquid OHLCV data
- [ ] Kimi K2.6 returns valid JSON signals
- [ ] Paper trades are logged to Neon with correct stop/TP levels
- [ ] Discord notifications fire on paper trade open
- [ ] SvelteKit dashboard shows live signal feed and paper trade log
- [ ] Risk controls update correctly via the UI
- [ ] Circuit breaker triggers and halts paper trading at daily loss limit
- [ ] Agent runs for 8 hours without crashing

**Do not enable live trading until Phase 1 runs cleanly for 2 weeks of paper trading.**

---

## 19. Phase 2 (After Validation)

- Backtesting module using historical Hyperliquid candles
- Strategy performance comparison dashboard
- MoonDev liquidation heatmap integration (trade WITH squeeze setups)
- Multiple concurrent strategies on different coins
- Telegram alternative to Discord
- Strategy marketplace (export/import rules.json)
- Cloud deployment option (Railway) for always-on operation
- Multi-exchange expansion (Bitget, MEXC)
