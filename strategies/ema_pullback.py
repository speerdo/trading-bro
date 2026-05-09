"""
Strategy 3: EMA Trend + Pullback

Use case: Strong trending markets. Buys/sells pullbacks to the 20 EMA.
"""

from strategies.base import BaseStrategy, SignalResult, coerce_confidence


class EmaPullbackStrategy(BaseStrategy):
    name = "ema_pullback"
    description = "EMA trend with pullback entry on 15m"

    def build_prompt(self, indicators: dict, symbol: str) -> str:
        i15 = indicators.get("15m", {})
        i1h = indicators.get("1h", {})
        return f"""
STRATEGY: EMA Trend + Pullback
Asset: {symbol}

**LONG CONDITIONS (ALL required):**
1. 1H: Price above 20 EMA AND 20 EMA above 50 EMA (clear uptrend)
2. 15m: Price pulls back to within 0.5% of 20 EMA
3. 15m: Bullish candle forming (close > open, close > prev close)
4. RSI(14) between 40-60 (pulled back from overbought, not yet oversold)

**SHORT CONDITIONS:** Inverse of above.

CURRENT DATA:
1H: price={i1h.get('price')} ema20={i1h.get('ema20')} ema50={i1h.get('ema50')} (above 50 EMA: {i1h.get('price_vs_ema50')})
15m: price={i15.get('price')} high={i15.get('high')} low={i15.get('low')} close={i15.get('price')}
15m RSI: {i15.get('rsi')}
15m ATR: {i15.get('atr')}

Return ONLY valid JSON with keys: direction, confidence, reasoning, entry_price, invalidation.
"""

    def parse_response(self, response: dict, indicators: dict) -> SignalResult:
        i15 = indicators.get("15m", {})
        i1h = indicators.get("1h", {})
        direction = response.get("direction", "none")
        confidence = coerce_confidence(response.get("confidence"))

        price_1h = i1h.get("price")
        ema20_1h = i1h.get("ema20")
        ema50_1h = i1h.get("ema50")
        price_15m = i15.get("price")
        rsi_15m = i15.get("rsi")

        # 1. Trend clarity
        if ema20_1h is None or ema50_1h is None or price_1h is None:
            return self.fallback_signal()

        uptrend = price_1h > ema20_1h and ema20_1h > ema50_1h
        downtrend = price_1h < ema20_1h and ema20_1h < ema50_1h

        if direction == "long":
            if not uptrend:
                return self.fallback_signal()
            # Pullback within 0.5% of 20 EMA
            if ema20_1h and price_15m:
                away_pct = abs(price_15m - ema20_1h) / ema20_1h
                if away_pct > 0.005:
                    confidence = min(confidence, 0.5)
                    direction = "none"
            # RSI 40-60
            if rsi_15m is not None and not (40 <= rsi_15m <= 60):
                confidence = min(confidence, 0.5)
                direction = "none"

        elif direction == "short":
            if not downtrend:
                return self.fallback_signal()
            if ema20_1h and price_15m:
                away_pct = abs(price_15m - ema20_1h) / ema20_1h
                if away_pct > 0.005:
                    confidence = min(confidence, 0.5)
                    direction = "none"
            if rsi_15m is not None and not (40 <= rsi_15m <= 60):
                confidence = min(confidence, 0.5)
                direction = "none"

        return SignalResult(
            direction=direction,
            confidence=confidence,
            reasoning=response.get("reasoning", ""),
            entry_price=price_15m,
            invalidation=response.get("invalidation", ""),
        )
