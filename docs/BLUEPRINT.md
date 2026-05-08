# TradeBrain — Full System Blueprint

> **Purpose**: This document is the authoritative specification for building TradeBrain.
> Claude Code should read this entirely before writing any code. Every architectural decision,
> data flow, file structure, and implementation detail is here. Do not deviate without good reason.

---

## 1. What TradeBrain Is

TradeBrain is a personal AI-powered crypto trading agent that:

- **Screens** the ~20 perp-style futures listed on Coinbase Financial Markets (FCM) every session and selects the top N candidates for signal evaluation
- **Evaluates** signals using Kimi K2.6 via OpenRouter against a chosen strategy
- **Executes** leveraged long/short positions on Coinbase Financial Markets (FCM, the CFTC-regulated US DCM) via a thin native JWT-signed HTTP client
- **Manages risk** with mandatory stop losses, position sizing, and a daily loss circuit breaker
- **Notifies** via Discord webhook on every trade, skip, and daily summary
- **Logs** everything to Neon Postgres (including Section 1256 tax-treatment flagging on every trade)
- **Exposes** a local SvelteKit 5 dashboard at `localhost:5173` for real-time control

It runs entirely on your local Fedora machine. No cloud compute. No third-party holding your keys.
Paper trading is ON by default. One toggle in the UI switches to live.

### 1.1 Venue: Coinbase Financial Markets (FCM)

We trade on **Coinbase Financial Markets**, the CFTC-regulated US derivatives exchange (DCM). We do NOT trade on **Coinbase International Exchange (INTX)**, which is geo-blocked for US residents.

This venue choice is load-bearing for three reasons:

1. **Different product universe.** FCM perp-style futures use obfuscated 2–3 letter product codes with a `-20DEC30-CDE` suffix (a 2030-12-20 expiry so far out that the products trade like perpetuals). For example, "BTC PERP" is actually `BIP-20DEC30-CDE`, not `BTC-PERP`. The literal name `BTC-PERP` only exists on INTX, which we cannot use. Always resolve product IDs at runtime by listing products and filtering on `display_name LIKE '% PERP'` — never hardcode the obfuscated codes.
2. **Different API surface.** CCXT's `ccxt.coinbase` integration only covers INTX-style symbols and the spot brokerage. It does not expose FCM-specific endpoints (`/cfm/balance_summary`, `/cfm/positions`, `/cfm/sweeps/...`). We bypass CCXT entirely and call `/api/v3/brokerage/...` directly using JWT auth (CDP API keys with ES256 / EC private key).
3. **Different rules.** FCM contracts are CFTC-regulated futures, so Section 1256 tax treatment applies (60/40 long-term/short-term capital gains regardless of holding period). Every trade record stores a `tax_treatment` field defaulted to `'1256'` so end-of-year reporting is straightforward.

### 1.2 Account split (spot ↔ futures)

The Coinbase Default portfolio holds two logical balances: **spot** and **CFM (futures)**. They live on the same portfolio UUID but have separate USD balances. Funds must be moved between them via `/api/v3/brokerage/cfm/sweeps/schedule` before perp trading is possible. `cfm/balance_summary.futures_buying_power` is the authoritative number for trade capacity.

### 1.3 Maintenance windows

- **Weekly**: every Friday 5:00–6:00 PM ET — exchange is closed.
- **Quarterly**: a 3-hour scheduled maintenance window — exchange is closed.

Burt checks for these windows at the top of each signal loop iteration and skips the iteration entirely if within one. He also sends a proactive Discord message at Friday 5PM ET reminding you to monitor any open positions manually.

---

## 2. Tech Stack

| Layer | Technology | Notes |
|---|---|---|
| Signal brain | Kimi K2.6 via OpenRouter | `openai/kimi-k2.6` model ID, OpenAI-compatible API |
| Exchange | Coinbase Financial Markets (FCM) | CFTC-regulated US DCM. Universe: ~20 perp-style futures (BTC, ETH, SOL, XRP, DOGE, LINK, AVAX, ADA, LTC, DOT, BCH, HBAR, NEAR, XLM, SUI, AAVE, ZCASH, PAXG, 1000SHIB, 1000PEPE, ONDO, ENA) plus monthly expiring contracts |
| Market data + execution | Native JWT-signed Coinbase Brokerage client | CDP API key (ES256 over EC private key). Hits `/api/v3/brokerage/...` and `/cfm/...` directly. CCXT is **not** used. |
| Indicator computation | `pandas-ta` | Compute RSI, MACD, BB, EMA, ATR in Python from OHLCV |
| UI | SvelteKit 2, Svelte 5 runes | Local only, `localhost:5173` |
| API bridge | FastAPI + uvicorn | `localhost:8000`, connects UI to agent state |
| Database | Neon (Postgres) | `asyncpg`, trade log + signal log + config store |
| Notifications | Discord webhook | No bot token needed |
| Language | Python 3.11+ (agent), TypeScript (UI) |  |
| OS | Fedora Linux |  |

---

## 3. Data Sources — Detailed

All market data and trading flow through the Coinbase Brokerage v3 API at `https://api.coinbase.com/api/v3/brokerage/...`. Auth is JWT (ES256) signed with the EC private key from a Coinbase Developer Platform key.

### 3.1 Authentication (JWT / CDP keys)

CDP keys come as a `(key_name, private_key)` pair where:
- `key_name` looks like `organizations/{org_uuid}/apiKeys/{key_uuid}`
- `private_key` is a PEM-encoded EC (P-256) private key

For every request, build a short-lived JWT:
```python
import jwt, time, secrets
token = jwt.encode(
    payload={
        'sub': key_name,
        'iss': 'cdp',
        'nbf': int(time.time()),
        'exp': int(time.time()) + 120,
        'uri': f'{METHOD} api.coinbase.com{path_without_query}',
    },
    key=private_key_pem,
    algorithm='ES256',
    headers={'kid': key_name, 'nonce': secrets.token_hex()},
)
# Send with: Authorization: Bearer {token}
```
The `uri` claim must use the path without the querystring. JWTs are valid for ~120 seconds — generate per request, don't reuse.

### 3.2 Product discovery

```
GET /api/v3/brokerage/market/products?product_type=FUTURE&limit=300
```

Returns ~87 future products. Filter to perp-style products by `display_name LIKE '% PERP'` (which corresponds to `contract_expiry_type=EXPIRING` with an expiry of `2030-12-20` — Coinbase calls these "perpetual-style" because the expiry is so far out). Do NOT hardcode product IDs — codes are obfuscated and may change. The current mapping (verified live) is:

| Asset | display_name | product_id |
|---|---|---|
| BTC | "BTC PERP" | `BIP-20DEC30-CDE` |
| ETH | "ETH PERP" | `ETP-20DEC30-CDE` |
| SOL | "SOL PERP" | `SLP-20DEC30-CDE` |
| XRP | "XRP PERP" | `XPP-20DEC30-CDE` |
| DOGE | "DOGE PERP" | `DOP-20DEC30-CDE` |
| LINK | "LINK PERP" | `LNP-20DEC30-CDE` |
| AVAX | "AVAX PERP" | `AVP-20DEC30-CDE` |
| ADA | "ADA PERP" | `ADP-20DEC30-CDE` |
| LTC | "LTC PERP" | `LCP-20DEC30-CDE` |
| DOT | "DOT PERP" | `POP-20DEC30-CDE` |
| BCH | "BCH PERP" | `BCP-20DEC30-CDE` |
| HBAR | "HBAR PERP" | `HEP-20DEC30-CDE` |
| NEAR | "NEAR PERP" | `NER-20DEC30-CDE` |
| XLM | "XLM PERP" | `XLP-20DEC30-CDE` |
| SUI | "SUI PERP" | `SUP-20DEC30-CDE` |
| AAVE | "AAVE PERP" | `AVE-20DEC30-CDE` |
| ZCASH | "ZCASH PERP" | `ZEC-20DEC30-CDE` |
| PAXG | "PAXG PERP" | `PAU-20DEC30-CDE` |
| 1000SHIB | "1000SHIB PERP" | `SHP-20DEC30-CDE` |
| 1000PEPE | "1000PEPE PERP" | `PEP-20DEC30-CDE` |
| ONDO | "ONDO PERP" | `OND-20DEC30-CDE` |
| ENA | "ENA PERP" | `ENA-20DEC30-CDE` |

### 3.3 OHLCV candles

```
GET /api/v3/brokerage/market/products/{product_id}/candles?granularity={enum}&start={unix_ts}&end={unix_ts}
```
- `granularity` is an enum string: `ONE_MINUTE`, `FIVE_MINUTE`, `FIFTEEN_MINUTE`, `THIRTY_MINUTE`, `ONE_HOUR`, `TWO_HOUR`, `SIX_HOUR`, `ONE_DAY`
- `start` / `end` are unix epoch seconds
- **Max 300 candles per request.** For 15m → ~75 hours of history; for 1H → ~12 days. Sufficient for all our indicators.
- Response is ascending or descending depending on the call — always sort by `start` before passing to pandas-ta.

### 3.4 Funding rate, open interest, mark price

Per-product real-time data lives at:
```
GET /api/v3/brokerage/market/products/{product_id}
```
Look at the `perpetual_details` block: `funding_rate`, `funding_time`, `open_interest`, `max_leverage`. Note: in the bulk products-list response these fields are often empty strings — always fetch the per-product detail endpoint to get current funding.

### 3.5 Account / position / balance

| Purpose | Endpoint |
|---|---|
| Spot balances | `GET /api/v3/brokerage/accounts` |
| Futures balance summary (the source of truth for trade capacity) | `GET /api/v3/brokerage/cfm/balance_summary` |
| Open futures positions | `GET /api/v3/brokerage/cfm/positions` |
| Detail of one position | `GET /api/v3/brokerage/cfm/positions/{product_id}` |
| Sweep funds spot → futures (or back) | `POST /api/v3/brokerage/cfm/sweeps/schedule` |
| Pending sweeps | `GET /api/v3/brokerage/cfm/sweeps` |
| Cancel scheduled sweep | `DELETE /api/v3/brokerage/cfm/sweeps` |

The most important field for trading is `cfm/balance_summary.futures_buying_power`. If it's zero, no orders will fill — schedule a sweep first.

### 3.6 Orders

| Purpose | Endpoint |
|---|---|
| Place order | `POST /api/v3/brokerage/orders` (set `product_type=FUTURE`) |
| Cancel orders | `POST /api/v3/brokerage/orders/batch_cancel` |
| List open orders | `GET /api/v3/brokerage/orders/historical/batch?order_status=OPEN` |
| Order detail | `GET /api/v3/brokerage/orders/historical/{order_id}` |
| Fill history | `GET /api/v3/brokerage/orders/historical/fills` |

Stop loss and take profit are placed as separate `STOP_LIMIT_GTC` / `LIMIT_GTC` orders with `reduce_only=true` immediately after entry.

### 3.7 WebSocket (real-time)

URL: `wss://advanced-trade-ws.coinbase.com`

Subscribe to channels with a JWT token in the `jwt` field:
- `ticker` — real-time bid/ask/last for a product list
- `candles` — live candle formation (1m candles, aggregate locally for higher TFs if needed)
- `user` — fills, order updates for the authenticated account

Use WebSocket for live position monitoring + real-time mid for paper trading P&L. Use REST for the per-iteration data fetch in the signal loop.

### 3.8 Rate limits

- Public endpoints: 15 req/s
- Private (authenticated) endpoints: 10 req/s

Generous for our use. With ~20 perps and two timeframes each per screener pass, that's ~40 requests in maybe 4–5 seconds with conservative concurrency — no issues.

### 3.9 What We Do NOT Use

- **Hyperliquid** — operator is a US resident; no path.
- **Coinbase International Exchange (INTX)** — geo-blocked for US residents. CCXT's `ccxt.coinbase` resolves `BTC/USDC:USDC` to INTX symbols (`BTC-PERP-INTX`); ignore those.
- **CCXT** — does not cover FCM products or the `/cfm/...` endpoints. We use a thin native JWT client instead.
- **MoonDev API** — Coinbase's own API has ample rate budget for our universe; no need.
- **TradingView MCP / public API / `tradingviewapi.com`** — fragile or non-existent.
- **`yfinance` / Alpha Vantage** — not relevant for crypto perps.

---

## 4. Asset Selection — The Screener

The screener runs once at agent startup and then every 4 hours during a session. It scans all perp-style products on Coinbase Financial Markets and scores each one, returning the top N candidates for signal evaluation.

### 4.1 Universe construction

At each screener pass:

1. `GET /api/v3/brokerage/market/products?product_type=FUTURE&limit=300`
2. Filter: `display_name LIKE '% PERP'` AND `status` is tradeable AND venue is `FCM`
3. For each surviving product, follow up with `GET /api/v3/brokerage/market/products/{id}` to populate live `perpetual_details` (funding rate, open interest, max leverage), since the bulk-list values are usually empty.

This dynamically discovers the available universe — codes are not hardcoded. Currently this resolves to ~20 perps (BTC, ETH, SOL, XRP, DOGE, LINK, AVAX, ADA, LTC, DOT, BCH, HBAR, NEAR, XLM, SUI, AAVE, ZCASH, PAXG, 1000SHIB, 1000PEPE, ONDO, ENA — see §3.2 table).

### 4.2 Screener Scoring Algorithm

For each market, compute a composite score from these factors:

**Factor 1: 24h Volume Score (weight: 30%)**
- Compute from 1H candles: sum of last 24 bars' volume × close price (notional volume)
- Normalize to 0–1 across the surviving universe
- Higher volume = better liquidity = lower slippage

**Factor 2: Volatility Score (weight: 25%)**
- Compute ATR(14) on 1H candles
- Express as ATR / price (normalized volatility %)
- Target range: 1–5% ATR. Too low = no movement, too high = liquidation risk
- Score peaks at ~2–3% and falls off at extremes

**Factor 3: Funding Rate Score (weight: 20%)**
- Source: `perpetual_details.funding_rate` from `/products/{id}` (per-product detail endpoint, not the bulk list)
- Extreme positive funding (> 0.05% / hr) = crowded longs = short opportunity
- Extreme negative funding (< -0.05% / hr) = crowded shorts = long opportunity
- Neutral funding (near 0) = balanced = good for either direction
- Score: `abs(funding) * direction_alignment_bonus`
- If `funding_rate` comes back empty (rare, but it happened in our test runs), substitute neutral score (0.5) and log a warning rather than dropping the asset

**Factor 4: Trend Clarity Score (weight: 15%)**
- Compute EMA(20) and EMA(50) on 1H candles
- Score higher when EMAs are clearly separated (trending)
- Score lower when EMAs are tangled (choppy/ranging — bad for RSI/MACD)

**Factor 5: Open Interest Score (weight: 10%)**
- Source: `perpetual_details.open_interest` from `/products/{id}`
- Higher OI = more institutional interest = more reliable signals
- Normalize across the surviving universe

**Minimum thresholds (disqualify before scoring):**
- 24h notional volume < $5M: skip (too illiquid)
- `max_leverage` < 5x: skip (Coinbase caps these lower than offshore venues — adjust threshold based on actual venue norms)
- Mark price < $0.0001: skip (data integrity)
- Status not tradeable: skip

**Output:** Top `max_watchlist_size` (default 5) assets by composite score. These become the active watchlist for signal evaluation.

### 4.3 Screener Implementation

```python
# agent/screener.py pseudocode

async def run_screener() -> list[dict]:
    """Returns list of {product_id, display_name, score, factor_breakdown}"""

    # 1. Discover universe
    products = await cb.list_perp_products()  # filter to '% PERP'

    # 2. Hydrate perpetual_details per product (concurrent, semaphore=8)
    products = await cb.hydrate_details(products)

    # 3. Filter minimum thresholds
    candidates = [
        p for p in products
        if p.notional_24h > 5_000_000
        and p.max_leverage >= 5
        and p.status_tradeable
    ]

    # 4. Fetch 1H candles for each candidate (last 100 bars)
    # Use asyncio.gather with semaphore=8

    # 5. Compute indicators with pandas-ta and the score factors above

    # 6. Return top N by composite score
    scores.sort(key=lambda x: x['score'], reverse=True)
    return scores[:cfg.max_watchlist_size]
```

---

## 5. Signal Evaluation — Kimi K2.6

### 5.1 How It Works

For each product in the screener-selected watchlist, on each signal loop iteration:

1. Fetch fresh 15m OHLCV candles from `/api/v3/brokerage/market/products/{id}/candles?granularity=FIFTEEN_MINUTE` (last 100 bars; up to 300 per request)
2. Fetch 1H OHLCV candles (`granularity=ONE_HOUR`, last 50 bars) for trend filter
3. Re-fetch `perpetual_details.funding_rate` for current funding (the screener pass may be up to a few minutes stale)
4. Compute indicators using `pandas-ta` on the raw OHLCV DataFrames
5. Build a structured prompt with all indicator values + funding rate + the market-regime context block
6. Send to Kimi K2.6 via OpenRouter
7. Parse the JSON response into a `SignalResult`

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
signal meets the defined strategy criteria for a leveraged position on 
Coinbase Financial Markets perp-style futures.

Rules:
- Only signal "long" or "short" when ALL required conditions are clearly met
- When in doubt, return "none" — missing a trade is better than a bad trade  
- Be concise in reasoning — max 2 sentences
- ALWAYS return valid JSON only, no markdown, no preamble
- Never recommend a trade without clear technical justification from the data provided
- Consider funding rate when provided — extreme positive funding favors shorts, 
  extreme negative favors longs
- Use the provided market-regime context (BTC dominance, broader crypto trend) 
  to weight setups: align with the regime when possible, be more selective when 
  the regime contradicts the setup
```

### 5.6 Market-Regime Context Block

Computed once per signal loop iteration and shared across all watchlist evaluations. Complements (does not replace) the per-asset funding rate.

Contents (rendered into the prompt as a short paragraph):
- **BTC dominance**: current value and 24h change — how BTC is moving relative to the rest of the crypto market
- **Broader market regime**: a one-line label (`risk-on` | `risk-off` | `mixed` | `chop`) derived from BTC and ETH 1H trend posture and volatility
- **Context tickers**: brief notes on the major alts in the watchlist (SOL, AVAX, LINK, etc.) so Burt's commentary can reference correlations and rotation between them

Kimi K2.6 has unusually strong training context on the major crypto assets in our universe — leverage that. The model can apply learned priors (correlation patterns, macro reactions, narrative context) on top of pure technical signals.

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

Stop loss is placed as a separate `STOP_LIMIT_GTC` reduce-only order on FCM immediately after the entry market order fills.
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

1. MAINTENANCE WINDOW CHECK (top of every iteration)
   └── If current time is Friday 5:00–6:00 PM ET → skip iteration entirely
   └── If within a known quarterly 3-hour maintenance window → skip iteration
   └── Otherwise continue

2. SCREENER (every 4 hours or on startup)
   └── List perp-style products from FCM
   └── Hydrate per-product perpetual_details (funding, OI, max_leverage)
   └── Score all and set active watchlist (top max_watchlist_size)

3. For each product in active watchlist:
   
   a. DATA FETCH
      └── GET /products/{id}/candles?granularity=FIFTEEN_MINUTE (last 100 bars)
      └── GET /products/{id}/candles?granularity=ONE_HOUR (last 50 bars)
      └── GET /products/{id} for fresh perpetual_details (funding rate)
   
   b. INDICATOR COMPUTATION
      └── pandas-ta computes RSI, MACD, BB, EMA, ATR on local DataFrames
      └── Produces indicator dict for prompt
   
   c. MARKET-REGIME CONTEXT (computed once per iteration, shared across watchlist)
      └── Compute BTC dominance proxy + regime label
      └── Build context block for prompt
   
   d. SIGNAL EVALUATION
      └── Build prompt from active strategy + indicator dict + funding rate + regime context
      └── POST to OpenRouter (Kimi K2.6)
      └── Parse JSON response → SignalResult
      └── Log signal to Neon DB (signals table)
   
   e. PRE-TRADE CHECKS
      └── direction == "none"? → skip
      └── confidence < min_confidence? → skip + log skip_reason
      └── Already in a position on this product? → skip
      └── Circuit breaker active? → skip + alert
      └── futures_buying_power insufficient? → skip + alert (suggest sweep)
   
   f. RISK CALCULATION
      └── Calculate stop loss (ATR or fixed %)
      └── Calculate position size from risk % and stop distance
      └── Calculate take profit from R:R ratio
      └── Check margin < 20% of futures_buying_power cap
   
   g. ORDER PLACEMENT (or paper log)
      └── POST /orders with product_type=FUTURE, market entry
      └── POST /orders with STOP_LIMIT_GTC, reduce_only=true (stop loss)
      └── POST /orders with LIMIT_GTC, reduce_only=true (take profit)
      └── Log trade to Neon DB — include tax_treatment='1256', product_type='perp', product_id (e.g. 'BIP-20DEC30-CDE'), display_name
      └── Track in open_positions dict
   
   h. NOTIFICATION
      └── Discord webhook: trade opened embed
   
4. POSITION MONITORING (parallel task, every 30s)
   └── GET /cfm/positions — compare against open_positions dict
   └── Detect if stop or TP was hit (position disappeared)
   └── Pull fills via /orders/historical/fills to get actual exit price + P&L
   └── Update trade record in DB
   └── Update daily P&L for circuit breaker
   └── Send Discord notification on close

5. DAILY SUMMARY (at 17:00 local time)
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
│   ├── coinbase_client.py          ← native JWT-signed Coinbase Brokerage v3 client (REST + WebSocket)
│   ├── screener.py                 ← FCM perp-universe discovery + scoring
│   ├── indicator_engine.py         ← pandas-ta indicator computation
│   ├── regime.py                   ← BTC dominance / market-regime context block
│   ├── signal_engine.py            ← OpenRouter / Kimi K2.6 client
│   ├── risk_manager.py             ← position sizing, stops, circuit breaker
│   ├── executor.py                 ← order placement on /api/v3/brokerage/orders
│   ├── position_monitor.py         ← tracks open positions, detects closes
│   ├── sweeper.py                  ← spot ↔ CFM fund sweeps (/cfm/sweeps/*)
│   ├── maintenance.py              ← Friday 5–6PM ET + quarterly window check
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
    signal_id       INT REFERENCES signals(id),
    -- FCM product identity
    product_id      TEXT,                 -- e.g. 'BIP-20DEC30-CDE'
    display_name    TEXT,                 -- e.g. 'BTC PERP'
    -- Tax + product classification
    tax_treatment   TEXT DEFAULT '1256',  -- Section 1256 (60/40) for FCM perp-style futures
    product_type    TEXT DEFAULT 'perp'   -- 'perp' | 'expiring_future' (for the monthly contracts, if we ever trade them)
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
    selected         JSONB,               -- array of {product_id, display_name, score, factor_breakdown}
    universe_size   INT                   -- how many perps were in the universe at scoring time
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

GET  /api/watchlist       — current screener-selected watchlist with scores
POST /api/screener/run    — trigger immediate screener re-run

GET  /api/positions       — current open futures positions from /cfm/positions
POST /api/positions/{product_id}/close — manually close a position (market reduce_only)

GET  /api/balance         — spot balance + CFM balance_summary (futures_buying_power, margin, etc.)
POST /api/sweep           — schedule a sweep between spot and CFM

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
- Max watchlist size slider (1–10)
- Signal interval (1, 5, 15, 30, 60 minutes)
- "Re-run screener now" button
- Current watchlist chips (display name + score)
- Maintenance window indicator (greys out and shows countdown during Friday 5–6 PM ET)

*Account section:*
- Spot USD balance
- Futures buying power (`cfm/balance_summary.futures_buying_power`)
- Sweep button — opens a small modal to move USD between spot and CFM

**Main area — Tabs:**

*Positions tab:*
- Live open positions table (polls every 10s) — uses `/cfm/positions`
- Columns: Display name, Product ID, Direction, Entry, Current, P&L $, P&L %, Stop, TP, Time open
- Manual close button per position

*Trades tab:*
- Closed trades table
- Columns: Time, Display name, Dir, Strategy, Entry, Exit, P&L $, R multiple, Status, Tax (1256)
- Color coded: green = profit, red = loss, yellow = open

*Signals tab:*
- All evaluated signals
- Columns: Time, Display name, Dir, Confidence (bar), Strategy, Acted, Skip reason, Reasoning
- Helps you understand what the AI is seeing

*Screener tab:*
- Full screener results with score breakdown per product
- Columns: Display name, Product ID, Composite score, Volume score, Volatility score, Funding score, Trend score, OI score, Status (selected/excluded/disqualified)
- Shows why each asset was included or excluded

**All config changes auto-save** (debounced 500ms). No save button needed.
**Polling intervals:** Status every 5s, positions every 10s, trades/signals every 30s.

---

## 13. Environment Variables

```bash
# Required
OPENROUTER_API_KEY=sk-or-...
COINBASE_API_KEY=organizations/{org_uuid}/apiKeys/{key_uuid}   # CDP key name
COINBASE_API_SECRET="-----BEGIN EC PRIVATE KEY-----\n...\n-----END EC PRIVATE KEY-----\n"   # CDP key PEM (escape \n in .env)
DATABASE_URL=postgresql://...    # Neon connection string

# Optional but recommended
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

# Agent defaults (all overridable in UI)
PAPER_TRADING=true
DEFAULT_LEVERAGE=3
DEFAULT_RISK_PER_TRADE=0.01      # 1%
DEFAULT_DAILY_LOSS_LIMIT=0.05    # 5%
DEFAULT_STRATEGY=rsi_macd
DEFAULT_SIGNAL_INTERVAL=300      # 5 minutes in seconds
DEFAULT_MAX_WATCHLIST=5
```

**On the Coinbase sandbox (api-sandbox.coinbase.com):** the sandbox returns mocked, static responses for the Accounts and Orders endpoints only — it cannot drive a real signal loop. Our paper trading mode (simulate trades in-memory against real live prices from the production API, never fire actual orders) is strictly superior for strategy validation. Use the sandbox only to verify API key authentication and order payload formatting before going live, not for strategy testing.

---

## 14. Python Dependencies (requirements.txt)

```
# Exchange + data (native Coinbase Brokerage v3 client — no CCXT)
pyjwt>=2.8.0                 # ES256 JWT signing for CDP keys
cryptography>=42.0.0         # EC private key handling for ES256
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
    1. Load and validate config (fail fast on missing required keys; verify EC private key parses)
    2. Connect to Neon DB (asyncpg pool)
    3. Run DB migrations (CREATE TABLE IF NOT EXISTS)
    4. Coinbase client: probe /api/v3/brokerage/accounts (auth + spot)
    5. Coinbase client: probe /cfm/balance_summary (futures account is provisioned + funded)
       └── If futures_buying_power == 0, log a warning suggesting a sweep
    6. Run initial screener → set active watchlist
    7. Start FastAPI server in background thread (uvicorn)
    8. Start position monitor background task
    9. Log startup summary to Discord
    10. Enter main signal loop (which always begins with maintenance-window check)
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

### Generate Coinbase CDP API key:
1. Sign in to the Coinbase Developer Platform (`portal.cdp.coinbase.com`)
2. Create a new API key with **trade** permissions on the Default portfolio (this is the same portfolio that holds your spot and CFM/futures balances — Coinbase does not split them into separate portfolios for US users)
3. Download or copy:
   - The **key name**, format `organizations/{org_uuid}/apiKeys/{key_uuid}` → goes in `COINBASE_API_KEY`
   - The **EC private key** (PEM with `BEGIN/END EC PRIVATE KEY`) → goes in `COINBASE_API_SECRET`. In `.env`, escape newlines as `\n` (the agent un-escapes them at startup)
4. You must also be enrolled in Coinbase Financial Markets (futures) before perp trading is possible. Enrollment is in the Coinbase mobile app under *Perpetual Futures*.

> Coinbase has a sandbox at `api-sandbox.coinbase.com`, but it only returns mocked static responses for Accounts and Orders. **Do not use it for strategy testing** — use our in-process paper trading mode against live production data instead.

---

## 17. Key Implementation Notes for Claude Code

1. **Venue is FCM, not INTX.** The literal name `BTC-PERP` only exists on Coinbase International Exchange (geo-blocked for US residents). On FCM, "BTC PERP" is the obfuscated `BIP-20DEC30-CDE`. Always discover product IDs at runtime via the products list and resolve by `display_name LIKE '% PERP'` — never hardcode.

2. **JWT auth, every request.** Coinbase Brokerage v3 with CDP keys uses ES256 JWTs (NOT HMAC). Generate per-request, valid ~120s, signed with the EC private key from the CDP keypair. The JWT `uri` claim must be `"{METHOD} api.coinbase.com{path_without_querystring}"`. Token goes in the `Authorization: Bearer ...` header.

3. **No CCXT.** `ccxt.coinbase` does not cover FCM products or the `/cfm/...` endpoints, and silently maps to INTX symbols. Build a thin native client (`agent/coinbase_client.py`) that wraps the JWT auth + the endpoints in §3. CCXT is not in `requirements.txt`.

4. **Candle response shape.** Each candle is `{start, low, high, open, close, volume}` where `start` is unix epoch seconds (string). Convert to int and sort ascending before handing to pandas-ta.

5. **Granularity is an enum, not seconds.** `FIFTEEN_MINUTE`, `ONE_HOUR`, etc. — see §3.3.

6. **Funding rate fetch.** `perpetual_details.funding_rate` is often empty in the bulk products list but populated in `/products/{id}`. Fetch per-product when scoring or evaluating signals.

7. **Stop loss + take profit as separate orders.** After the entry market order fills, place `STOP_LIMIT_GTC` (stop loss) and `LIMIT_GTC` (take profit) reduce-only orders. Both must include `reduce_only=true` in `order_configuration`.

8. **Leverage and margin.** FCM uses portfolio-level margining; there's no per-symbol `set_leverage` like CCXT exposes for offshore venues. Position size and risk-cap math run against `cfm/balance_summary.futures_buying_power` directly.

9. **Sweep before trade.** If `futures_buying_power == 0`, no orders fill. Either: (a) prompt the user via Burt to schedule a sweep, or (b) auto-schedule a sweep when transitioning to live mode if user has enabled `AUTO_SWEEP=true` in config.

10. **Maintenance windows.** At the top of every signal loop iteration, call `agent.maintenance.is_open()`. Returns `False` during Friday 5:00–6:00 PM ET and during the quarterly 3-hour scheduled maintenance window. When `False`, skip the iteration entirely. Burt also sends a proactive Discord message at Friday 5:00 PM ET reminding the user to monitor open positions manually.

11. **Tax treatment.** Every row inserted into `trades` must populate `tax_treatment='1256'`, `product_type='perp'`, plus `product_id` and `display_name` for clean reporting. FCM perp-style futures are CFTC-regulated Section 1256 contracts (60% long-term / 40% short-term capital gains regardless of holding period).

12. **pandas-ta column names.** MACD columns are `MACD_12_26_9`, `MACDh_12_26_9`, `MACDs_12_26_9`. BB columns are `BBL_20_2.0`, `BBM_20_2.0`, `BBU_20_2.0`. ATR column is `ATRr_14`.

13. **OpenRouter JSON mode.** Include `"response_format": {"type": "json_object"}` in the request body. Always wrap the parse in try/except and return a `direction: "none"` signal on failure.

14. **Rate limits.** 10 req/s private, 15 req/s public. The screener fans out across ~20 products × 2 timeframes = ~40 candle requests per pass. Use `asyncio.gather` with a `Semaphore(8)` to stay comfortably under the limit.

15. **Config hot-reload.** The agent's main loop calls `await db.sync_config()` at the top of each iteration. This reads any keys in `agent_config` table and overwrites the in-memory `cfg` object. The UI writes to this table via PATCH `/api/config`.

16. **Svelte 5 runes.** Use `$state()`, `$derived()`, `$effect()`. Do NOT use Svelte 4 `writable()` stores or the `$store` prefix syntax.

17. **Position monitoring.** Run as a separate `asyncio.Task`, not in the main signal loop. Check every 30 seconds. Detect closed positions by comparing `/cfm/positions` against the `open_positions` dict. When a position disappears, hit `/orders/historical/fills` to get the actual exit price and P&L.

18. **Paper trading position tracking.** Maintain a separate in-memory `paper_positions` dict. Simulate P&L by comparing entry price to the current mark from the WebSocket `ticker` feed (or the latest REST candle close as a fallback). Never fire an order on the exchange in paper mode.

19. **Discord embed colors.** Use `0x00ff88` for long/profit, `0xff4455` for short/loss, `0xffaa00` for warnings, `0xff0000` for circuit breaker.

20. **Build order.** First get the screener listing perp products and printing scores to console. Then add data fetch + indicator computation. Then signal evaluation. Then paper-mode execution. Then live execution with the sweep flow. Build in layers, not all at once.

---

## 18. What Success Looks Like (Phase 1)

Phase 1 is complete when:
- [ ] Native Coinbase client authenticates with JWT/ES256 against `/api/v3/brokerage/...`
- [ ] Screener discovers the FCM perp universe at runtime, hydrates `perpetual_details`, and selects top N by composite score
- [ ] Indicators compute correctly from FCM OHLCV data
- [ ] Funding rate is included in the signal prompt (per-product) and the screener score
- [ ] Market-regime context block (BTC dominance, regime label) is injected into the prompt
- [ ] Kimi K2.6 returns valid JSON signals
- [ ] Paper trades are logged to Neon with correct stop/TP levels, `product_id`, `display_name`, `tax_treatment='1256'`
- [ ] Maintenance-window check skips Friday 5–6 PM ET correctly
- [ ] `/cfm/balance_summary` and `/cfm/positions` are surfaced on the dashboard
- [ ] Sweep flow (spot ↔ futures) works from the UI
- [ ] Discord notifications fire on paper trade open and at Friday 5 PM ET maintenance start
- [ ] SvelteKit dashboard shows live signal feed, paper trade log, and screener results
- [ ] Risk controls update correctly via the UI
- [ ] Circuit breaker triggers and halts paper trading at daily loss limit
- [ ] Agent runs for 8 hours without crashing

**Do not enable live trading until Phase 1 runs cleanly for 2 weeks of paper trading.**

---

## 19. Phase 2 (After Validation)

- Backtesting module using historical FCM candles
- Strategy performance comparison dashboard
- Multiple concurrent strategies on different products in the watchlist
- Trading the FCM monthly expiring futures (`BIT-MMMYY-CDE`, etc.) alongside the perp-style products, with auto-roll
- Telegram alternative to Discord
- Strategy marketplace (export/import rules.json)
- Cloud deployment option (Railway) for always-on operation
- Auto-sweep policy: rebalance spot ↔ CFM nightly to keep target buying-power buffer
