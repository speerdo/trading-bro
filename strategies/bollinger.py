"""
Strategy 2: Bollinger Band Mean Reversion

Use case: Ranging markets. Fades overextension back to the mean.
"""

from strategies.base import BaseStrategy, SignalResult, coerce_confidence


class BollingerStrategy(BaseStrategy):
    name = "bollinger"
    description = "Bollinger Band mean reversion with RSI confirmation"

    def build_prompt(self, indicators: dict, symbol: str) -> str:
        i15 = indicators.get("15m", {})
        return f"""
STRATEGY: Bollinger Band Mean Reversion
Asset: {symbol}

**LONG CONDITIONS (ALL required):**
1. Candle wick touches or crosses below BB lower band
2. RSI(14) < 35
3. Candle closes back above the lower band (rejection wick preferred)
4. BB width > 1% of price (avoids flat consolidation)

**SHORT CONDITIONS (ALL required):**
1. Candle wick touches or crosses above BB upper band
2. RSI(14) > 65
3. Candle closes back below the upper band
4. BB width > 1% of price

Target: Middle band (BB basis / 20 EMA).
Stop: Beyond the wick that touched the band.

CURRENT DATA:
15m Price: {i15.get('price')} (high: {i15.get('high')}, low: {i15.get('low')})
15m BB: lower={i15.get('bb_lower')} middle={i15.get('bb_middle')} upper={i15.get('bb_upper')}
15m RSI: {i15.get('rsi')}
BB width: {i15.get('bb_width')}
15m ATR: {i15.get('atr')}

Return ONLY valid JSON with keys: direction, confidence, reasoning, entry_price, invalidation.
"""

    def parse_response(self, response: dict, indicators: dict) -> SignalResult:
        i15 = indicators.get("15m", {})
        direction = response.get("direction", "none")
        confidence = coerce_confidence(response.get("confidence"))

        price = i15.get("price")
        low = i15.get("low")
        high = i15.get("high")
        bb_lower = i15.get("bb_lower")
        bb_upper = i15.get("bb_upper")
        bb_width = i15.get("bb_width")
        rsi = i15.get("rsi")

        # Hard gate: require BB width > 1%
        if bb_width is not None and bb_width <= 0.01:
            return self.fallback_signal()

        if direction == "long":
            conditions = [
                low is not None and bb_lower is not None and low <= bb_lower,
                rsi is not None and rsi < 35,
            ]
            if not all(conditions):
                confidence = min(confidence, 0.5)
                direction = "none"

        elif direction == "short":
            conditions = [
                high is not None and bb_upper is not None and high >= bb_upper,
                rsi is not None and rsi > 65,
            ]
            if not all(conditions):
                confidence = min(confidence, 0.5)
                direction = "none"

        return SignalResult(
            direction=direction,
            confidence=confidence,
            reasoning=response.get("reasoning", ""),
            entry_price=price,
            invalidation=response.get("invalidation", ""),
        )
