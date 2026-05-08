"""
TradeBrain Market Screener (Option B)

Scores all Hyperliquid perp markets and returns top N candidates.
Runs every 4 hours or on demand.
"""

import asyncio
import math
from dataclasses import dataclass
from typing import Any

import pandas as pd
from loguru import logger

from agent.data_client import HyperliquidDataClient
from agent.indicator_engine import compute_screener_indicators
from agent.database import get_db
import config


# ------------------------------------------------------------------
# Data structures
# ------------------------------------------------------------------

@dataclass
class ScreenerScore:
    coin: str
    total_score: float
    volume_score: float
    volatility_score: float
    funding_score: float
    trend_score: float
    oi_score: float
    raw_metrics: dict = None


# ------------------------------------------------------------------
# Screener
# ------------------------------------------------------------------

class Screener:
    """Screens all HL perp markets and returns best candidates."""

    VOLUME_WEIGHT = 0.30
    VOLATILITY_WEIGHT = 0.25
    FUNDING_WEIGHT = 0.20
    TREND_WEIGHT = 0.15
    OI_WEIGHT = 0.10

    MIN_VOLUME = 5_000_000    # $5M 24h volume
    MIN_LEVERAGE = 10
    MIN_PRICE = 0.0001

    def __init__(self, data_client: HyperliquidDataClient | None = None):
        self.client = data_client or HyperliquidDataClient()
        self.cfg = config.get_config()

    async def run(self, max_watchlist: int | None = None) -> list[str]:
        """
        Full screener run.
        Returns list of coin names (e.g. ['BTC', 'ETH']).
        Summary is logged to DB.
        """
        max_watchlist = max_watchlist or self.cfg.default_max_watchlist
        logger.info("Starting screener run...")

        # 1. Fetch all market metadata
        universe, ctxs = await self.client.get_meta_and_asset_ctxs()
        if not universe or not ctxs:
            logger.error("Failed to fetch market metadata")
            return []

        # 2. Filter minimum thresholds
        candidates: list[tuple[str, dict]] = []
        for asset, ctx in zip(universe, ctxs):
            name = asset.get("name", "")
            max_lev = int(asset.get("maxLeverage", 0))
            day_vlm = float(ctx.get("dayNtlVlm", 0))
            mark_px = float(ctx.get("markPx", 0))

            if day_vlm < self.MIN_VOLUME:
                continue
            if max_lev < self.MIN_LEVERAGE:
                continue
            if mark_px < self.MIN_PRICE:
                continue

            candidates.append((name, ctx))

        logger.info(f"Screener: {len(candidates)} candidates after filtering")
        if not candidates:
            return []

        # 3. Fetch 1H candles for each candidate (last 100 bars)
        coins = [c[0] for c in candidates]
        candle_maps = await self.client.get_candles_multi(coins, interval="1h")

        # 4. Score each candidate
        scores: list[ScreenerScore] = []
        for coin, ctx in candidates:
            df = candle_maps.get(coin, pd.DataFrame())
            if df.empty or len(df) < 30:
                logger.debug(f"Not enough candle data for {coin}, skipping")
                continue

            score = self._score(coin, ctx, df)
            scores.append(score)

        # 5. Normalize and sort
        scores = self.normalize_scores(scores)
        scores.sort(key=lambda s: s.total_score, reverse=True)
        top = scores[:max_watchlist]
        result = [s.coin for s in top]

        # 6. Log to DB with full score breakdown
        await self._log_screener_run(top)

        logger.info(
            f"Screener complete. Top {len(result)}: {result}"
        )
        for s in top:
            logger.info(
                f"  {s.coin}: total={s.total_score:.3f} "
                f"vol={s.volume_score:.3f} vola={s.volatility_score:.3f} "
                f"fund={s.funding_score:.3f} trend={s.trend_score:.3f} oi={s.oi_score:.3f}"
            )

        return result

    def _score(self, coin: str, ctx: dict, df_1h: pd.DataFrame) -> ScreenerScore:
        """Compute the composite score for a single market."""
        # --- Raw data ---
        day_vlm = float(ctx.get("dayNtlVlm", 0))
        mark_px = float(ctx.get("markPx", 0))
        funding = float(ctx.get("funding", 0))
        open_interest = float(ctx.get("openInterest", 0))

        # --- Indicators ---
        inds = compute_screener_indicators(df_1h)
        atr_pct = inds["atr_pct"]
        ema_sep_pct = inds["ema_sep_pct"]

        # --- Factor 1: Volume Score (30%) ---
        # Normalize log-volumes across candidates — done at batch level in run()
        volume_score = day_vlm  # placeholder, normalized later

        # --- Factor 3: Volatility Score (25%) ---
        # Target ATR% ~2-3%. Score peaks there, falls off.
        volatility_score = self._volatility_score(atr_pct)

        # --- Factor 3: Funding Score (20%) ---
        # Extreme funding = interesting (crowded longs or shorts)
        funding_score = abs(funding)

        # --- Factor 4: Trend Clarity Score (15%) ---
        trend_score = min(ema_sep_pct / 2.0, 1.0)  # 2% EMA sep = max score

        # --- Factor 5: Open Interest Score (10%) ---
        oi_score = open_interest  # placeholder, normalized later

        raw = {
            "day_vlm": day_vlm,
            "mark_px": mark_px,
            "funding": funding,
            "open_interest": open_interest,
            "atr_pct": atr_pct,
            "ema_sep_pct": ema_sep_pct,
        }

        return ScreenerScore(
            coin=coin,
            total_score=0.0,  # filled after normalization
            volume_score=volume_score,
            volatility_score=volatility_score,
            funding_score=funding_score,
            trend_score=trend_score,
            oi_score=oi_score,
            raw_metrics=raw,
        )

    @staticmethod
    def _volatility_score(atr_pct: float) -> float:
        """
        Score peaks at ~2-3% ATR and falls off at extremes.
        Simple Gaussian-ish bell curve centered at 2.5%.
        """
        target = 2.5
        sigma = 2.0
        # Gaussian: exp(-((x-target)^2)/(2*sigma^2))
        return float(max(0.0, math.exp(-((atr_pct - target) ** 2) / (2 * sigma ** 2))))

    # ------------------------------------------------------------------
    # Post-processing: normalization
    # ------------------------------------------------------------------

    @staticmethod
    def normalize_scores(scores: list[ScreenerScore]) -> list[ScreenerScore]:
        """Normalize volume/oi to 0-1 and compute total_score."""
        if not scores:
            return scores

        def _norm(values: list[float]) -> list[float]:
            min_v, max_v = min(values), max(values)
            if max_v == min_v:
                return [0.5] * len(values)
            return [(v - min_v) / (max_v - min_v) for v in values]

        vol_scores = _norm([s.volume_score for s in scores])
        oi_scores = _norm([s.oi_score for s in scores])
        fund_scores = _norm([s.funding_score for s in scores])

        for s, vs, os, fs in zip(scores, vol_scores, oi_scores, fund_scores):
            s.volume_score = vs
            s.oi_score = os
            s.funding_score = fs
            s.total_score = (
                s.volume_score * Screener.VOLUME_WEIGHT +
                s.volatility_score * Screener.VOLATILITY_WEIGHT +
                s.funding_score * Screener.FUNDING_WEIGHT +
                s.trend_score * Screener.TREND_WEIGHT +
                s.oi_score * Screener.OI_WEIGHT
            )

        return scores

    async def _log_screener_run(self, scores: list[ScreenerScore]) -> None:
        """Persist to DB."""
        try:
            db = await get_db()
            await db.log_screener_run(
                selected_coins=[s.coin for s in scores],
                scores={
                    s.coin: {
                        "total": s.total_score,
                        "volume": s.volume_score,
                        "volatility": s.volatility_score,
                        "funding": s.funding_score,
                        "trend": s.trend_score,
                        "oi": s.oi_score,
                        "raw": s.raw_metrics,
                    }
                    for s in scores
                },
            )
        except Exception as e:
            logger.warning(f"Failed to log screener run: {e}")

    # ------------------------------------------------------------------
    # Convenience entry point
    # ------------------------------------------------------------------

    @classmethod
    async def run_screener(cls) -> list[str]:
        """One-shot screener (creates its own client)."""
        s = cls()
        try:
            return await s.run()
        finally:
            await s.client.close()
