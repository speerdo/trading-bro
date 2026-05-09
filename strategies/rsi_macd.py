"""
Strategy 1: RSI + MACD Momentum (Default)

Use case: Trending markets. Catches momentum reversals on 15m chart.
"""

from strategies.base import BaseStrategy, SignalResult, coerce_confidence


class RsiMacdStrategy(BaseStrategy):
    name = "rsi_macd"
    description = "RSI(14) + MACD momentum with 1h EMA trend filter"

    def build_prompt(self, indicators: dict, symbol: str) -> str:
        i15 = indicators.get("15m", {})
        i1h = indicators.get("1h", {})
        return f"""
STRATEGY: RSI + MACD Momentum
Asset: {symbol}

**LONG CONDITIONS (ALL required):**
1. RSI(14) on 15m crossed above 30 from below OR RSI is 30-45 with bullish MACD cross
2. MACD line crossed above signal line
3. MACD histogram is increasing (turning positive or growing)
4. 1H price is ABOVE the 50 EMA (uptrend filter)

**SHORT CONDITIONS (ALL required):**
1. RSI(14) on 15m crossed below 70 from above OR RSI is 55-70 with bearish MACD cross
2. MACD line crossed below signal line
3. MACD histogram is decreasing (turning negative or falling)
4. 1H price is BELOW the 50 EMA (downtrend filter)

**AVOID:** RSI in middle (40-60) with no clear MACD direction.

CURRENT DATA:
15m RSI: {i15.get('rsi')} (prev: {i15.get('rsi_prev')})
15m MACD line: {i15.get('macd_line')} | signal: {i15.get('macd_signal')} | hist: {i15.get('macd_hist')} (prev: {i15.get('macd_hist_prev')})
1H Price vs EMA50: {i1h.get('price_vs_ema50')} (price={i1h.get('price')}, ema50={i1h.get('ema50')})
15m ATR: {i15.get('atr')} (for stop calculation)
BB width: {i15.get('bb_width')}
Vol ratio: {i15.get('vol_ratio')}

Return ONLY valid JSON with keys: direction, confidence, reasoning, entry_price, invalidation.
"""

    def parse_response(self, response: dict, indicators: dict) -> SignalResult:
        i15 = indicators.get("15m", {})
        i1h = indicators.get("1h", {})

        # Attempt LLM-chosen values first, fallback to programmatic check
        direction = response.get("direction", "none")
        confidence = coerce_confidence(response.get("confidence"))

        # ---- Hard gate: long requires ALL conditions ----
        if direction == "long":
            rsi = i15.get("rsi")
            rsi_prev = i15.get("rsi_prev")
            macd_hist = i15.get("macd_hist")
            macd_hist_prev = i15.get("macd_hist_prev")
            macd_line = i15.get("macd_line")
            macd_signal = i15.get("macd_signal")
            ema_filter = i1h.get("price_vs_ema50") == "above"

            # RSI condition: crossed above 30 or 30-45 with bullish MACD
            rsi_ok = False
            if rsi is not None and rsi_prev is not None:
                if rsi_prev < 30 < rsi:
                    rsi_ok = True
                elif 30 <= rsi <= 45 and macd_line is not None and macd_signal is not None:
                    # bullish MACD cross (simple: macd > signal)
                    if macd_line > macd_signal:
                        rsi_ok = True

            # MACD increasing
            macd_ok = False
            if macd_hist is not None and macd_hist_prev is not None:
                if macd_hist > macd_hist_prev:
                    macd_ok = True

            if not all([rsi_ok, macd_ok, ema_filter]):
                confidence = min(confidence, 0.5)
                direction = "none"

        # ---- Hard gate: short requires ALL conditions ----
        elif direction == "short":
            rsi = i15.get("rsi")
            rsi_prev = i15.get("rsi_prev")
            macd_hist = i15.get("macd_hist")
            macd_hist_prev = i15.get("macd_hist_prev")
            macd_line = i15.get("macd_line")
            macd_signal = i15.get("macd_signal")
            ema_filter = i1h.get("price_vs_ema50") == "below"

            rsi_ok = False
            if rsi is not None and rsi_prev is not None:
                if rsi_prev > 70 > rsi:
                    rsi_ok = True
                elif 55 <= rsi <= 70 and macd_line is not None and macd_signal is not None:
                    if macd_line < macd_signal:
                        rsi_ok = True

            macd_ok = False
            if macd_hist is not None and macd_hist_prev is not None:
                if macd_hist < macd_hist_prev:
                    macd_ok = True

            if not all([rsi_ok, macd_ok, ema_filter]):
                confidence = min(confidence, 0.5)
                direction = "none"

        return SignalResult(
            direction=direction,
            confidence=confidence,
            reasoning=response.get("reasoning", ""),
            entry_price=i15.get("price"),
            invalidation=response.get("invalidation", ""),
        )
