# ============================================================
#  games/base_game.py — Abstract base class for all games
#
#  Every game in UltimateBot inherits from BaseGame.
#  The session engine (cogs/sessions.py) talks to games
#  exclusively through this interface.
# ============================================================

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    import discord
    from discord.ext import commands


@dataclass
class GameResult:
    """Returned by BaseGame when a game ends."""
    winner_id: Optional[int]  # None if draw
    loser_id: Optional[int]  # None if draw or FFA with many losers
    is_draw: bool
    loser_ids: list[int] = field(default_factory=list)  # for FFA games


class BaseGame(abc.ABC):
    """
    Abstract base class for all UltimateBot games.

    Subclasses must implement:
      - game_name (class attribute)
      - can_draw   (class attribute)
      - is_ffa     (class attribute — True for King of Diamonds)
      - start()
      - handle_message()
      - get_state()
      - load_state()

    The session engine will:
      1. Instantiate the game with (bot, channel, players, bet_amount)
      2. Call start() to send the initial game message
      3. Route messages to handle_message()
      4. Call end() when the game produces a result
      5. Call on_timeout(user_id) when a player times out
    """

    # ── Subclass must define these ────────────────────────────
    game_name: str = "Unknown Game"
    can_draw: bool = False
    is_ffa: bool = False

    def __init__(
            self,
            bot,
            channel: "discord.TextChannel",
            players: list["discord.Member"],
            bet_amount: int = 0,
            guild_id: int = 0,
            season_number: int = 1,
    ):
        self.bot = bot
        self.channel = channel
        self.players = players  # list of discord.Member
        self.bet_amount = bet_amount
        self.guild_id = guild_id
        self.season_number = season_number
        self.is_active = True
        self._result: Optional[GameResult] = None

    # ── Abstract methods ─────────────────────────────────────

    @abc.abstractmethod
    async def start(self) -> None:
        """
        Send the initial game message(s) and set up game state.
        Called once by the session engine after instantiation.
        """

    @abc.abstractmethod
    async def handle_message(
            self,
            user_id: int,
            content: str,
            is_dm: bool,
            message: Optional["discord.Message"] = None,
    ) -> Optional[GameResult]:
        """
        Process a player's message (channel or DM).
        Return a GameResult if the game has ended, else None.

        The session engine calls this for every message from a player
        who is in this game session.

        `message` is the raw discord.Message — use it to add reactions.
        """

    @abc.abstractmethod
    def get_state(self) -> dict:
        """
        Return a JSON-serialisable dict of the current game state.
        Used by the session engine for crash recovery (persisted to sessions.db).
        """

    @abc.abstractmethod
    def load_state(self, state: dict) -> None:
        """
        Restore game state from a dict (crash recovery).
        """

    # ── Timeout handling ─────────────────────────────────────

    async def on_timeout(self, timed_out_user_id: int) -> Optional[GameResult]:
        """
        Called when a player hasn't acted for GAME_TIMEOUT_SECONDS.
        Default behaviour: the OTHER player wins by forfeit.
        FFA games should override this.
        """
        if len(self.players) == 2:
            winner = next(
                (p for p in self.players if p.id != timed_out_user_id), None
            )
            loser = next(
                (p for p in self.players if p.id == timed_out_user_id), None
            )
            if winner and loser:
                await self.channel.send(
                    f"⏱️ **{loser.display_name}** took too long to act. "
                    f"**{winner.display_name}** wins by forfeit!"
                )
                return GameResult(
                    winner_id=winner.id,
                    loser_id=loser.id,
                    is_draw=False,
                )
        return None

    # ── Utility helpers available to all subclasses ───────────

    async def send(self, *args, **kwargs) -> "discord.Message":
        """Shorthand for self.channel.send(...)."""
        return await self.channel.send(*args, **kwargs)

    async def dm_player(self, member: "discord.Member", content: str, **kwargs) -> None:
        """Send a DM to a player, silently failing if DMs are closed."""
        try:
            await member.send(content, **kwargs)
        except Exception:
            pass

    def get_member(self, user_id: int) -> Optional["discord.Member"]:
        """Find a player by user_id."""
        return next((p for p in self.players if p.id == user_id), None)