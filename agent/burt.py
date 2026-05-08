"""
Burt — Personality, Memory & Discord Bot

Runs as a background asyncio task alongside the main trading agent.
Requires DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID, DISCORD_USER_ID.
"""

import asyncio
from datetime import datetime
from typing import Any

from loguru import logger

import config
from agent.database import get_db
from agent.executor import Executor
from agent.risk_manager import RiskManager

# discord.py is optional — graceful fallback if not installed
try:
    import discord
    _has_discord = True
except ImportError:
    _has_discord = False
    discord = None  # type: ignore

try:
    import pytz
    _has_pytz = True
except ImportError:
    _has_pytz = False
    pytz = None  # type: ignore


class Burt:
    """
    Burt's personality, memory, and Discord interface.
    Runs as a background asyncio task alongside the main trading agent.
    """

    def __init__(self, db: Any, executor: Executor, risk_manager: RiskManager):
        self.cfg = config.get_config()
        self.db = db
        self.executor = executor
        self.risk = risk_manager
        self._client: Any = None
        self._task: asyncio.Task | None = None
        self._last_proactive_time: datetime | None = None

        if not _has_discord:
            logger.warning("discord.py not installed — Burt disabled")
            return

        intents = discord.Intents.default()
        intents.message_content = True
        self._client = discord.Client(intents=intents)
        self._setup_events()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the Discord bot."""
        if self._client is None:
            logger.warning("Burt client not available")
            return
        if not self.cfg.discord_bot_token:
            logger.warning("DISCORD_BOT_TOKEN missing — Burt not starting")
            return
        try:
            await self._client.start(self.cfg.discord_bot_token)
        except Exception as exc:
            logger.error(f"Burt failed to start: {exc}")

    def stop(self) -> None:
        if self._client:
            asyncio.create_task(self._client.close())

    # ------------------------------------------------------------------
    # Discord events
    # ------------------------------------------------------------------

    def _setup_events(self) -> None:
        @self._client.event
        async def on_ready():
            logger.info(f"Burt connected as {self._client.user}")

        @self._client.event
        async def on_message(message):
            await self._on_message(message)

    async def _on_message(self, message: Any) -> None:
        """Handle incoming Discord messages."""
        # Ignore bot's own messages
        if message.author == self._client.user:
            return

        # Only respond in designated channel
        if str(message.channel.id) != self.cfg.discord_channel_id:
            return

        # Only respond to designated user
        if str(message.author.id) != self.cfg.discord_user_id:
            return

        # Store message
        try:
            await self.db.add_discord_message(
                role="user",
                content=message.content,
                discord_user=str(message.author),
                message_id=str(message.id),
            )
        except Exception as exc:
            logger.warning(f"Failed to store Discord message: {exc}")

        # Generate response
        try:
            response = await self._generate_response(message.content)
            await message.channel.send(response)
            await self.db.add_discord_message(
                role="assistant",
                content=response,
                discord_user="Burt",
                message_id="",
            )
        except Exception as exc:
            logger.error(f"Burt response failed: {exc}")

    # ------------------------------------------------------------------
    # Response generation
    # ------------------------------------------------------------------

    async def _generate_response(self, user_message: str) -> str:
        """Generate Burt's response using Kimi K2.6."""
        # For now, return simple responses without LLM (skeleton)
        # Full implementation would query memories, stats, etc.

        msg_lower = user_message.lower()

        if "what are you looking at" in msg_lower:
            watchlist = self.executor.get_open_positions()
            if watchlist:
                return f"Watching {len(watchlist)} coins: {', '.join(p.symbol for p in watchlist)}"
            return "Nothing open right now. Screener's running in the background."

        if "what's open" in msg_lower or "any positions" in msg_lower:
            positions = self.executor.get_open_positions()
            if not positions:
                return "Nothing open. Market's quiet or I'm being picky."
            lines = []
            for p in positions:
                lines.append(f"{p.symbol} {p.direction.upper()} @ {p.entry_price:.2f}")
            return "Open positions:\n" + "\n".join(lines)

        if "how'd we do today" in msg_lower:
            try:
                stats = await self.db.get_today_stats()
                return (
                    f"Today: {stats['wins']}W / {stats['losses']}L, "
                    f"P&L=${stats['pnl_today']:+.2f}"
                )
            except Exception:
                return "No trades today yet."

        if "go live" in msg_lower:
            return "You sure? We're in paper mode. Say 'yes go live' to confirm."

        if "stop trading" in msg_lower or "pause" in msg_lower:
            self.risk.state.manual_pause = True
            return "Trading paused. Say 'resume' when you're ready."

        if "resume" in msg_lower or "start trading" in msg_lower:
            self.risk.state.manual_pause = False
            return "Back in action."

        if "how much have you made" in msg_lower or "what's the p&l" in msg_lower:
            try:
                stats = await self.db.get_today_stats()
                return f"Today: ${stats['pnl_today']:+.2f} | Total closed: {stats['closed_count']}"
            except Exception:
                return "No data yet."

        return "I'm here. Ask me about positions, the screener, or tell me to pause/resume."

    # ------------------------------------------------------------------
    # Proactive messaging
    # ------------------------------------------------------------------

    async def proactive_loop(self) -> None:
        """Background task for proactive messages."""
        # Skeleton — full implementation would schedule morning briefs, trade notifications, etc.
        pass

    # ------------------------------------------------------------------
    # Notifications (called by main agent)
    # ------------------------------------------------------------------

    async def notify_trade_opened(self, symbol: str, order: dict, signal: Any) -> None:
        """Send trade notification in Burt's voice."""
        if self._client is None:
            return
        channel = self._client.get_channel(int(self.cfg.discord_channel_id))
        if channel:
            await channel.send(f"Opened {signal.direction} {symbol}. Let's see how this goes.")

    async def notify_trade_closed(self, trade: Any) -> None:
        """Send close notification in Burt's voice."""
        if self._client is None:
            return
        channel = self._client.get_channel(int(self.cfg.discord_channel_id))
        if channel:
            pnl_text = f"P&L: ${trade.pnl_usdc:+.2f}" if hasattr(trade, 'pnl_usdc') else ""
            await channel.send(f"Closed {trade.symbol}. {pnl_text}")

    async def notify_circuit_breaker(self) -> None:
        """Send circuit breaker alert in Burt's voice."""
        if self._client is None:
            return
        channel = self._client.get_channel(int(self.cfg.discord_channel_id))
        if channel:
            await channel.send("Circuit breaker triggered. I'm done for the day.")

    async def morning_brief(self) -> None:
        """Send morning brief."""
        if self._client is None:
            return
        channel = self._client.get_channel(int(self.cfg.discord_channel_id))
        if channel:
            await channel.send("Morning. Screener's running. Will report back if anything looks good.")

    # ------------------------------------------------------------------
    # Active hours check
    # ------------------------------------------------------------------

    @staticmethod
    def is_active_hours() -> bool:
        if not _has_pytz:
            return True  # fallback: always active
        et = pytz.timezone("America/New_York")
        now = datetime.now(et)
        cfg = config.get_config()
        return cfg.burt_active_hours_start <= now.hour < cfg.burt_active_hours_end
