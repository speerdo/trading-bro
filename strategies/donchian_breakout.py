"""
Strategy 4: Donchian Channel Breakout (Trend-Following)

Use case: volatility-expansion / trending markets. Catches confirmed breakouts
of the prior 20-bar range. Best fit for leveraged trading because asymmetric
R:R amplifies favorably and the strategy naturally sidesteps chop (the worst
regime for leverage). Donchian — not Bollinger — is the right tool here:
Bollinger uses standard deviation (mean-reversion-flavoured); Donchian uses
raw highs/lows (the actual definition of a breakout).
"""

from strategies.base import BaseStrategy, SignalResult, coerce_confidence


class DonchianBreakoutStrategy(BaseStrategy):
    name = "donchian_breakout"
    description = "Donchian 20-bar breakout with volume + volatility-expansion confirmation"

    def build_prompt(self, indicators: dict, symbol: str) -> str:
        i15 = indicators.get("15m", {})
        i1h = indicators.get("1h", {})
        return f"""
STRATEGY: Donchian Channel Breakout (Trend-Following)
Asset: {symbol}

**LONG CONDITIONS (ALL required):**
1. 15m close strictly ABOVE the prior 20-bar high (dc_upper_prev) — clean breakout
2. Volume on the breakout bar is elevated (vol_ratio >= 1.2)
3. BB width is EXPANDING vs its 20-bar average (volatility breaking out, not contracting)
4. 1H trend is bullish (price above 50 EMA)
5. RSI 50-75 (room to run, not exhausted)

**SHORT CONDITIONS (ALL required):**
1. 15m close strictly BELOW the prior 20-bar low (dc_lower_prev)
2. vol_ratio >= 1.2
3. BB width expanding vs its 20-bar average
4. 1H trend bearish (price below 50 EMA)
5. RSI 25-50

**AVOID (these are the textbook fakeout setups):**
- Marginal break on low volume (vol_ratio < 1.0)
- Break during contracting BB width (range was already dying)
- Counter-trend break (long while 1H below EMA50, or short while above)
- RSI already extreme (>78 or <22) — likely the exhaustion bar, not the start of a move

CURRENT DATA:
15m close: {i15.get('price')}
15m donchian: upper={i15.get('dc_upper')} lower={i15.get('dc_lower')} middle={i15.get('dc_middle')}
15m PRIOR 20-bar range (the breakout test): upper_prev={i15.get('dc_upper_prev')} lower_prev={i15.get('dc_lower_prev')}
15m volume ratio: {i15.get('vol_ratio')} (1.0 = average)
15m BB width: {i15.get('bb_width')} | 20-bar avg: {i15.get('bb_width_avg20')}
15m RSI: {i15.get('rsi')}
15m ATR: {i15.get('atr')}
1H price vs EMA50: {i1h.get('price_vs_ema50')}
1H RSI: {i1h.get('rsi')}

For invalidation, the natural stop is the OPPOSITE Donchian boundary
(longs: dc_lower; shorts: dc_upper) or 1.5 ATR — whichever is closer.

Return ONLY valid JSON with keys: direction, confidence, reasoning, entry_price, invalidation.
"""

    def parse_response(self, response: dict, indicators: dict) -> SignalResult:
        i15 = indicators.get("15m", {})
        i1h = indicators.get("1h", {})

        direction = response.get("direction", "none")
        confidence = coerce_confidence(response.get("confidence"))

        price = i15.get("price")
        dc_upper_prev = i15.get("dc_upper_prev")
        dc_lower_prev = i15.get("dc_lower_prev")
        vol_ratio = i15.get("vol_ratio")
        bb_width = i15.get("bb_width")
        bb_width_avg = i15.get("bb_width_avg20")
        rsi_15 = i15.get("rsi")
        ema_filter_long = i1h.get("price_vs_ema50") == "above"
        ema_filter_short = i1h.get("price_vs_ema50") == "below"

        # Hard gate: actual breakout must have happened. The LLM can hallucinate;
        # we re-check the math here so a "long" signal without a real upper-band
        # break gets killed.
        if direction == "long":
            if price is None or dc_upper_prev is None or price <= dc_upper_prev:
                direction = "none"
            else:
                # Soft penalties — fold weak setups into "skip" via min_confidence
                if vol_ratio is not None and vol_ratio < 1.0:
                    confidence = min(confidence, 0.4)
                if bb_width is not None and bb_width_avg is not None and bb_width <= bb_width_avg:
                    confidence = min(confidence, 0.45)
                if not ema_filter_long:
                    confidence = min(confidence, 0.5)
                if rsi_15 is not None and rsi_15 > 78:
                    confidence = min(confidence, 0.5)

        elif direction == "short":
            if price is None or dc_lower_prev is None or price >= dc_lower_prev:
                direction = "none"
            else:
                if vol_ratio is not None and vol_ratio < 1.0:
                    confidence = min(confidence, 0.4)
                if bb_width is not None and bb_width_avg is not None and bb_width <= bb_width_avg:
                    confidence = min(confidence, 0.45)
                if not ema_filter_short:
                    confidence = min(confidence, 0.5)
                if rsi_15 is not None and rsi_15 < 22:
                    confidence = min(confidence, 0.5)

        return SignalResult(
            direction=direction,
            confidence=confidence,
            reasoning=response.get("reasoning", ""),
            entry_price=response.get("entry_price"),
            invalidation=response.get("invalidation", ""),
        )
