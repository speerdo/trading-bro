"""
Executor — Order placement + paper trading

Handles both paper (simulated) and live (CCXT/Hyperliquid) trading.
Paper mode works without any API keys.
Live mode requires HL_WALLET_ADDRESS + HL_API_PRIVATE_KEY + coincurve.
"""

import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

import config
from agent.database import get_db

# ------------------------------------------------------------------
# Live trading imports (optional — graceful fallback)
# ------------------------------------------------------------------
try:
    import ccxt
    _has_ccxt = True
except ImportError:
    _has_ccxt = False

try:
    import coincurve
    _has_coincurve = True
except ImportError:
    _has_coincurve = False


# ------------------------------------------------------------------
# Data structures
# ------------------------------------------------------------------

@dataclass
class PaperPosition:
    """In-memory paper position."""
    symbol: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    size_usdc: float
    margin_usdc: float
    leverage: int
    risk_usdc: float
    opened_at: float = field(default_factory=time.time)
    signal_id: int | None = None
    strategy: str = ""
    confidence: float = 0.0
    reasoning: str = ""
    is_paper: bool = True
    status: str = "open"
    exit_price: float | None = None
    pnl_usdc: float = 0.0


@dataclass
class OrderResult:
    """Result of an order placement."""
    success: bool
    order_id: str | None = None
    error: str = ""
    filled_price: float | None = None
    filled_size: float | None = None


# ------------------------------------------------------------------
# Executor
# ------------------------------------------------------------------

class Executor:
    """Handles order execution — paper or live."""

    def __init__(self):
        self.cfg = config.get_config()
        self.paper_positions: dict[str, PaperPosition] = {}
        self._live_ready = False
        self._exchange: Any = None

        # Attempt live setup if keys present
        if self.cfg.hl_wallet_address and self.cfg.hl_api_private_key:
            if _has_ccxt and _has_coincurve:
                self._init_live()
            else:
                logger.warning(
                    "Live trading keys present but coincurve/ccxt unavailable. "
                    "Install coincurve for live trading (currently blocked on Python 3.14)."
                )
        else:
            logger.info("Live trading keys missing — running in paper-only mode")

    # ------------------------------------------------------------------
    # Live setup
    # ------------------------------------------------------------------

    def _init_live(self) -> None:
        """Initialize CCXT Hyperliquid client."""
        try:
            self._exchange = ccxt.hyperliquid({
                "walletAddress": self.cfg.hl_wallet_address,
                "privateKey": self.cfg.hl_api_private_key,
                "options": {
                    "defaultType": "swap",
                },
            })
            if self.cfg.hl_testnet:
                self._exchange.set_sandbox_mode(True)
            self._live_ready = True
            logger.info("Live trading client initialized")
        except Exception as exc:
            logger.error(f"Failed to init live client: {exc}")
            self._live_ready = False

    async def verify_connectivity(self) -> bool:
        """Test fetch_balance to verify API works."""
        if not self._live_ready:
            return False
        try:
            await self._exchange.fetch_balance()
            logger.info("Live API connectivity verified")
            return True
        except Exception as exc:
            logger.error(f"Live API connectivity failed: {exc}")
            return False

    # ------------------------------------------------------------------
    # Entry flow
    # ------------------------------------------------------------------

    async def enter_position(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        size_usdc: float,
        margin_usdc: float,
        leverage: int,
        risk_usdc: float,
        signal_id: int | None = None,
        strategy: str = "",
        confidence: float = 0.0,
        reasoning: str = "",
    ) -> OrderResult:
        """
        Enter a position (paper or live).
        Non-negotiable: no entry without simultaneous stop loss.
        """
        if self.cfg.paper_trading or not self._live_ready:
            return await self._enter_paper(
                symbol, direction, entry_price, stop_loss, take_profit,
                size_usdc, margin_usdc, leverage, risk_usdc,
                signal_id, strategy, confidence, reasoning,
            )
        else:
            return await self._enter_live(
                symbol, direction, entry_price, stop_loss, take_profit,
                size_usdc, margin_usdc, leverage, risk_usdc,
                signal_id, strategy, confidence, reasoning,
            )

    async def _enter_paper(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        size_usdc: float,
        margin_usdc: float,
        leverage: int,
        risk_usdc: float,
        signal_id: int | None,
        strategy: str,
        confidence: float,
        reasoning: str,
    ) -> OrderResult:
        """Simulate a paper trade."""
        if symbol in self.paper_positions:
            return OrderResult(success=False, error=f"Already have open position on {symbol}")

        pos = PaperPosition(
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            size_usdc=size_usdc,
            margin_usdc=margin_usdc,
            leverage=leverage,
            risk_usdc=risk_usdc,
            signal_id=signal_id,
            strategy=strategy,
            confidence=confidence,
            reasoning=reasoning,
            is_paper=True,
        )
        self.paper_positions[symbol] = pos
        logger.info(
            f"📄 PAPER ENTRY: {direction.upper()} {symbol} @ {entry_price:.2f} "
            f"size=${size_usdc:.2f} margin=${margin_usdc:.2f} lev={leverage}x"
        )

        # Log to DB
        await self._log_trade(pos)
        return OrderResult(success=True, order_id=f"paper-{symbol}-{int(time.time())}")

    async def _enter_live(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        size_usdc: float,
        margin_usdc: float,
        leverage: int,
        risk_usdc: float,
        signal_id: int | None,
        strategy: str,
        confidence: float,
        reasoning: str,
    ) -> OrderResult:
        """Place a real order on Hyperliquid."""
        if not self._live_ready:
            return OrderResult(success=False, error="Live client not ready")

        if symbol in self.paper_positions:
            return OrderResult(success=False, error=f"Already have open position on {symbol}")

        try:
            # Hyperliquid pair format: BTC/USDC:USDC
            market_symbol = self._to_ccxt_symbol(symbol)

            # 1. Set leverage
            await self._exchange.set_leverage(leverage, market_symbol)

            # 2. Market entry order
            side = "buy" if direction == "long" else "sell"
            amount = size_usdc / entry_price  # coin amount
            entry_order = await self._exchange.create_market_buy_order(
                market_symbol, amount
            ) if side == "buy" else await self._exchange.create_market_sell_order(
                market_symbol, amount
            )

            entry_id = entry_order.get("id", "")
            filled_price = float(entry_order.get("average", entry_price) or entry_price)

            # 3. Stop loss (reduceOnly, trigger on mark price)
            sl_order = await self._exchange.create_order(
                market_symbol,
                "stop_market",
                "sell" if direction == "long" else "buy",
                amount,
                None,
                {"stopPrice": stop_loss, "reduceOnly": True},
            )

            # 4. Take profit (reduceOnly limit)
            tp_order = await self._exchange.create_order(
                market_symbol,
                "limit",
                "sell" if direction == "long" else "buy",
                amount,
                take_profit,
                {"reduceOnly": True},
            )

            logger.info(
                f"💰 LIVE ENTRY: {direction.upper()} {symbol} @ {filled_price:.2f} "
                f"entry_id={entry_id} sl_id={sl_order.get('id')} tp_id={tp_order.get('id')}"
            )

            # Log to DB
            pos = PaperPosition(
                symbol=symbol, direction=direction, entry_price=filled_price,
                stop_loss=stop_loss, take_profit=take_profit,
                size_usdc=size_usdc, margin_usdc=margin_usdc,
                leverage=leverage, risk_usdc=risk_usdc,
                signal_id=signal_id, strategy=strategy,
                confidence=confidence, reasoning=reasoning,
                is_paper=False,
            )
            await self._log_trade(pos, order_id=entry_id)

            return OrderResult(
                success=True,
                order_id=entry_id,
                filled_price=filled_price,
                filled_size=amount,
            )

        except Exception as exc:
            logger.error(f"Live entry failed for {symbol}: {exc}")
            return OrderResult(success=False, error=str(exc))

    # ------------------------------------------------------------------
    # Exit flow
    # ------------------------------------------------------------------

    async def close_position(
        self, symbol: str, exit_price: float | None = None
    ) -> OrderResult:
        """Close a position (paper or live)."""
        pos = self.paper_positions.get(symbol)
        if pos is None:
            return OrderResult(success=False, error=f"No open position on {symbol}")

        if pos.is_paper:
            return await self._close_paper(pos, exit_price)
        else:
            return await self._close_live(pos, exit_price)

    async def _close_paper(self, pos: PaperPosition, exit_price: float | None) -> OrderResult:
        """Close a paper position."""
        if exit_price is None:
            exit_price = pos.entry_price  # assume flat (caller should provide live price)

        if pos.direction == "long":
            pnl = (exit_price - pos.entry_price) / pos.entry_price * pos.size_usdc
        else:
            pnl = (pos.entry_price - exit_price) / pos.entry_price * pos.size_usdc

        pos.exit_price = exit_price
        pos.pnl_usdc = pnl
        pos.status = "closed"
        del self.paper_positions[pos.symbol]

        logger.info(
            f"📄 PAPER CLOSE: {pos.symbol} @ {exit_price:.2f} P&L=${pnl:+.2f}"
        )

        # Update DB
        await self._update_trade_close(pos)
        return OrderResult(success=True, filled_price=exit_price)

    async def _close_live(self, pos: PaperPosition, exit_price: float | None) -> OrderResult:
        """Close a live position via market order."""
        if not self._live_ready:
            return OrderResult(success=False, error="Live client not ready")

        try:
            market_symbol = self._to_ccxt_symbol(pos.symbol)
            side = "sell" if pos.direction == "long" else "buy"
            amount = pos.size_usdc / pos.entry_price

            order = await self._exchange.create_market_order(
                market_symbol, side, amount
            )
            filled_price = float(order.get("average", exit_price or pos.entry_price))

            if pos.direction == "long":
                pnl = (filled_price - pos.entry_price) / pos.entry_price * pos.size_usdc
            else:
                pnl = (pos.entry_price - filled_price) / pos.entry_price * pos.size_usdc

            pos.exit_price = filled_price
            pos.pnl_usdc = pnl
            pos.status = "closed"
            del self.paper_positions[pos.symbol]

            logger.info(
                f"💰 LIVE CLOSE: {pos.symbol} @ {filled_price:.2f} P&L=${pnl:+.2f}"
            )
            await self._update_trade_close(pos)
            return OrderResult(success=True, filled_price=filled_price)

        except Exception as exc:
            logger.error(f"Live close failed for {pos.symbol}: {exc}")
            return OrderResult(success=False, error=str(exc))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_ccxt_symbol(coin: str) -> str:
        """Convert 'BTC' to CCXT format 'BTC/USDC:USDC'."""
        return f"{coin}/USDC:USDC"

    async def _log_trade(self, pos: PaperPosition, order_id: str = "") -> None:
        try:
            db = await get_db()
            await db.log_trade({
                "symbol": pos.symbol,
                "direction": pos.direction,
                "strategy": pos.strategy,
                "confidence": pos.confidence,
                "entry_price": pos.entry_price,
                "stop_loss": pos.stop_loss,
                "take_profit": pos.take_profit,
                "size_usdc": pos.size_usdc,
                "margin_usdc": pos.margin_usdc,
                "leverage": pos.leverage,
                "risk_usdc": pos.risk_usdc,
                "is_paper": pos.is_paper,
                "status": "open",
                "reasoning": pos.reasoning,
                "order_id": order_id or "",
                "signal_id": pos.signal_id,
            })
        except Exception as exc:
            logger.warning(f"Failed to log trade: {exc}")

    async def _update_trade_close(self, pos: PaperPosition) -> None:
        try:
            db = await get_db()
            # Find the open trade record
            row = await db.fetchrow(
                "SELECT id FROM trades WHERE symbol = $1 AND status = 'open' ORDER BY created_at DESC LIMIT 1",
                pos.symbol,
            )
            if row:
                await db.close_trade(row["id"], pos.exit_price or 0, pos.pnl_usdc, "closed")
        except Exception as exc:
            logger.warning(f"Failed to update trade close: {exc}")

    # ------------------------------------------------------------------
    # Position queries
    # ------------------------------------------------------------------

    def get_open_positions(self) -> list[PaperPosition]:
        return list(self.paper_positions.values())

    def has_position(self, symbol: str) -> bool:
        return symbol in self.paper_positions

    def get_position(self, symbol: str) -> PaperPosition | None:
        return self.paper_positions.get(symbol)

    async def simulate_paper_pnl(self, symbol: str, current_price: float) -> float:
        """Return unrealized P&L for a paper position."""
        pos = self.paper_positions.get(symbol)
        if pos is None:
            return 0.0
        if pos.direction == "long":
            return (current_price - pos.entry_price) / pos.entry_price * pos.size_usdc
        else:
            return (pos.entry_price - current_price) / pos.entry_price * pos.size_usdc
