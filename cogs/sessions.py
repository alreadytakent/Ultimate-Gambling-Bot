# ============================================================
#  cogs/sessions.py — Game session manager
#
#  Responsibilities:
#    - Track all active game sessions in memory
#    - Route DM and channel messages to the correct game
#    - Inactivity timeout: after GAME_TIMEOUT_SECONDS with no move,
#      post an embed in the game channel with two buttons:
#        • "Claim Victory"  — waiting player wins immediately
#        • "Wait X minutes" — resets the inactivity timer (repeats)
#      (King of Diamonds: players who still owe a number are eliminated)
#    - Absolute kill timer (1v1 games): if GAME_ABSOLUTE_TIMEOUT_SECONDS
#      pass since the start, the player who acted last wins. If nobody
#      acted, the game is cancelled.
#    - Record results to the server's score database
#    - Enforce one-game-per-player rule
#    - .cancel  — cancel your game (not available in King of Diamonds)
#    - .resign  — forfeit your game
#                 1v1: opponent wins immediately
#                 KOD: you lose and leave; the game continues without you
# ============================================================

import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Optional

import discord
from discord.ext import commands

import config
import database as db
from games.base_game import BaseGame, GameResult
from games.registry import GAME_CLASSES

log = logging.getLogger("UltimateBot.sessions")

# Total time (seconds) after which a 1v1 game is auto-resolved.
GAME_ABSOLUTE_TIMEOUT_SECONDS = 36000


class GameSession:
    """In-memory representation of one active game."""

    def __init__(self, session_id: str, game: BaseGame, guild_id: int,
                 channel_id: int, player_ids: list[int]):
        self.session_id = session_id
        self.game       = game
        self.guild_id   = guild_id
        self.channel_id = channel_id
        self.player_ids = player_ids
        self.started_at = datetime.utcnow()

        # Last user to make a move that changed the game state (None = nobody yet)
        self.last_actor: Optional[int] = None
        self.last_action_at: datetime = datetime.utcnow()
        self.last_state: str = ""

        self.lock = asyncio.Lock()
        self._inactivity_task: Optional[asyncio.Task] = None
        self._absolute_task:   Optional[asyncio.Task] = None


class Sessions(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._sessions: dict[str, GameSession] = {}
        self._player_session: dict[int, str] = {}
        self._restore_task: Optional[asyncio.Task] = None

    async def cog_load(self) -> None:
        self._restore_task = asyncio.create_task(self._restore_sessions())

    async def cog_unload(self) -> None:
        if self._restore_task:
            self._restore_task.cancel()
        for s in list(self._sessions.values()):
            self._cancel_tasks(s)

    # ════════════════════════════════════════════════════════
    #  PUBLIC API
    # ════════════════════════════════════════════════════════

    async def start_session(
        self,
        game: BaseGame,
        guild_id: int,
        channel: discord.TextChannel,
        players: list[discord.Member],
    ) -> Optional[str]:
        """Create and start a session. Returns session_id, or None if a player is busy."""
        for member in players:
            if member.id in self._player_session:
                existing = self._sessions.get(self._player_session[member.id])
                game_name = existing.game.game_name if existing else "a game"
                await channel.send(
                    f"❌ {member.mention} is already in a game of **{game_name}**. "
                    f"Finish or forfeit that game first."
                )
                return None

        session_id = str(uuid.uuid4())
        player_ids = [m.id for m in players]
        session = GameSession(session_id, game, guild_id, channel.id, player_ids)

        self._sessions[session_id] = session
        for uid in player_ids:
            self._player_session[uid] = session_id
        self._start_timers(session)

        try:
            await game.start()
            dumped, state = self._snapshot(game)
            session.last_state = dumped
            await db.save_session(session_id, game.game_name, guild_id,
                                  channel.id, player_ids, state)
        except Exception:
            self._discard(session)
            await db.delete_session(session_id)
            raise
        return session_id

    def get_session_for_player(self, user_id: int) -> Optional[GameSession]:
        sid = self._player_session.get(user_id)
        return self._sessions.get(sid) if sid else None

    def get_session(self, session_id: str) -> Optional[GameSession]:
        return self._sessions.get(session_id)

    def record_action(self, session_id: str, user_id: Optional[int]) -> None:
        """A player made a real move: remember them and restart the inactivity timer."""
        session = self._sessions.get(session_id)
        if not session:
            return
        session.last_actor = user_id
        session.last_action_at = datetime.utcnow()
        self._restart_inactivity(session)

    # ════════════════════════════════════════════════════════
    #  INTERNALS
    # ════════════════════════════════════════════════════════

    @staticmethod
    def _snapshot(game: BaseGame) -> tuple[str, dict]:
        state = game.get_state()
        return json.dumps(state, sort_keys=True, default=str), state

    def _start_timers(self, session: GameSession) -> None:
        session._inactivity_task = asyncio.create_task(
            self._inactivity_watcher(session.session_id))
        if not session.game.is_ffa:
            session._absolute_task = asyncio.create_task(
                self._absolute_timeout_watcher(session.session_id))

    def _restart_inactivity(self, session: GameSession) -> None:
        t = session._inactivity_task
        if t and t is not asyncio.current_task() and not t.done():
            t.cancel()
        session._inactivity_task = asyncio.create_task(
            self._inactivity_watcher(session.session_id))

    @staticmethod
    def _cancel_tasks(session: GameSession) -> None:
        current = asyncio.current_task()
        for t in (session._inactivity_task, session._absolute_task):
            if t and t is not current and not t.done():
                t.cancel()

    def _discard(self, session: GameSession) -> None:
        """Remove a session from memory (no DB work, no result)."""
        self._cancel_tasks(session)
        self._sessions.pop(session.session_id, None)
        for uid in session.player_ids:
            if self._player_session.get(uid) == session.session_id:
                del self._player_session[uid]

    async def _after_message(self, session: GameSession, user_id: int) -> None:
        """Persist state; a changed state counts as activity of that player."""
        try:
            dumped, state = self._snapshot(session.game)
        except Exception:
            log.exception("get_state failed")
            return
        if dumped != session.last_state:
            session.last_state = dumped
            await db.update_session_state(session.session_id, state)
            self.record_action(session.session_id, user_id)

    async def end_session(self, session_id: str, result: GameResult,
                          guild: Optional[discord.Guild] = None) -> None:
        """Finalise a game: clean up and record the result."""
        session = self._sessions.get(session_id)
        if not session:
            return
        self._discard(session)
        await db.delete_session(session_id)
        try:
            await self._record(session, result)
        except Exception:
            log.exception("Failed to record result for session %s", session_id)

    async def _record(self, session: GameSession, result: GameResult) -> None:
        game = session.game
        pids = session.player_ids

        if game.is_ffa:
            winner = {result.winner_id} if result.winner_id is not None else set()
            draws = set(result.draw_ids)
            if result.is_draw and not draws and not winner:
                draws = set(pids)
            outcomes = []
            for pid in pids:
                if pid in winner:
                    outcomes.append((pid, "W"))
                elif pid in draws:
                    outcomes.append((pid, "D"))
                else:
                    outcomes.append((pid, "L"))
            await db.record_ffa_result(session.guild_id, game.game_name,
                                       session.session_id, outcomes)
            return

        if len(pids) < 2:
            return
        if result.is_draw:
            await db.record_result(session.guild_id, game.game_name, pids[0], pids[1],
                                   None, True, session.session_id)
        elif result.winner_id is not None:
            loser = result.loser_id
            if loser is None:
                loser = next((p for p in pids if p != result.winner_id), None)
            if loser is not None:
                await db.record_result(session.guild_id, game.game_name,
                                       result.winner_id, loser, result.winner_id,
                                       False, session.session_id)

    # ════════════════════════════════════════════════════════
    #  .cancel / .resign
    # ════════════════════════════════════════════════════════

    @commands.command(name="cancel")
    async def cancel(self, ctx: commands.Context):
        """Cancel your current game (no result is recorded). Not available in King of Diamonds."""
        if ctx.guild is None:
            await ctx.send("❌ This command can only be used in a server.")
            return

        session = self.get_session_for_player(ctx.author.id)
        if session is None:
            await ctx.send("❌ You're not in an active game.")
            return

        if session.game.is_ffa:
            await ctx.send(
                f"❌ **{session.game.game_name}** can't be cancelled. "
                f"Use `{ctx.clean_prefix}resign` to leave the game."
            )
            return

        game_name = session.game.game_name
        self._discard(session)
        await db.delete_session(session.session_id)
        await ctx.send(f"🛑 {ctx.author.mention} cancelled the game of **{game_name}**.")

    @commands.command(name="resign")
    async def resign(self, ctx: commands.Context):
        """Forfeit your current game. 1v1: the opponent wins. King of Diamonds: you leave and the game continues."""
        if ctx.guild is None:
            await ctx.send("❌ This command can only be used in a server.")
            return

        session = self.get_session_for_player(ctx.author.id)
        if session is None:
            await ctx.send("❌ You're not in an active game.")
            return

        game = session.game

        # ── King of Diamonds: leave, the game goes on ────────
        if game.is_ffa:
            async with session.lock:
                if session.session_id not in self._sessions:
                    return
                if ctx.author.id in game.eliminated:
                    await ctx.send("❌ You've already been eliminated from this game.")
                    return
                result = await game.resign(ctx.author.id)
                # Free the player so they can join other games right away
                if self._player_session.get(ctx.author.id) == session.session_id:
                    del self._player_session[ctx.author.id]
                if result is not None:
                    await self.end_session(session.session_id, result, ctx.guild)
                else:
                    await self._after_message(session, ctx.author.id)
            return

        # ── 1v1: opponent wins ───────────────────────────────
        others = [uid for uid in session.player_ids if uid != ctx.author.id]
        if not others:
            await ctx.send("❌ Cannot resign — no opponent found.")
            return

        winner_id      = others[0]
        winner         = ctx.guild.get_member(winner_id)
        winner_mention = winner.mention if winner else f"<@{winner_id}>"

        await ctx.send(
            f"🏳️ {ctx.author.mention} resigns from **{game.game_name}**! "
            f"{winner_mention} wins!"
        )
        result = GameResult(winner_id=winner_id, loser_id=ctx.author.id, is_draw=False)
        await self.end_session(session.session_id, result, ctx.guild)

    # ════════════════════════════════════════════════════════
    #  INACTIVITY WATCHER  (restarts on every real move)
    # ════════════════════════════════════════════════════════

    async def _inactivity_watcher(self, session_id: str) -> None:
        try:
            await asyncio.sleep(config.GAME_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            return

        session = self._sessions.get(session_id)
        if not session:
            return

        guild   = self.bot.get_guild(session.guild_id)
        channel = guild.get_channel(session.channel_id) if guild else None
        if not channel:
            return

        if session.game.is_ffa:
            await self._ffa_inactivity(session, channel)
            return

        # Nobody has moved yet → the absolute timer will cancel the game
        if session.last_actor is None or len(session.player_ids) != 2:
            return

        p0, p1 = session.player_ids
        waiting_id  = session.last_actor
        inactive_id = p1 if waiting_id == p0 else p0

        inactive_member = guild.get_member(inactive_id)
        waiting_member  = guild.get_member(waiting_id)
        inactive_name   = inactive_member.display_name if inactive_member else f"<@{inactive_id}>"
        waiting_mention = waiting_member.mention if waiting_member else f"<@{waiting_id}>"
        all_mentions = " ".join(f"<@{uid}>" for uid in session.player_ids)
        minutes = config.GAME_TIMEOUT_SECONDS // 60

        embed = discord.Embed(
            title="⏱️ Player Inactive",
            description=(
                f"**{inactive_name}** hasn't made a move in **{minutes} minutes**.\n\n"
                f"{waiting_mention}, what would you like to do?"
            ),
            color=discord.Color.orange(),
        )
        embed.set_footer(text=f"Game: {session.game.game_name}")

        view = _InactivityView(
            session_id=session_id,
            waiting_player_id=waiting_id,
            inactive_player_id=inactive_id,
            sessions_cog=self,
            guild=guild,
            minutes=minutes,
        )
        await channel.send(content=all_mentions, embed=embed, view=view)

    async def _ffa_inactivity(self, session: GameSession, channel: discord.TextChannel) -> None:
        """King of Diamonds: eliminate players who still owe a number."""
        async with session.lock:
            if session.session_id not in self._sessions:
                return
            game = session.game
            alive = [p.id for p in game.players if p.id not in game.eliminated]
            awaiting = [uid for uid in list(game.awaiting) if uid in alive]

            if not awaiting:
                self._restart_inactivity(session)
                return

            if len(awaiting) == len(alive):
                # Nobody submitted anything this round → cancel
                all_mentions = " ".join(f"<@{uid}>" for uid in session.player_ids)
                embed = discord.Embed(
                    title="⏰ Game Cancelled",
                    description="Nobody made a move in time. The game has been cancelled.",
                    color=discord.Color.red(),
                )
                await channel.send(content=all_mentions, embed=embed)
                self._discard(session)
                await db.delete_session(session.session_id)
                return

            for uid in awaiting:
                result = await game.on_timeout(uid)
                if result is not None:
                    await self.end_session(session.session_id, result)
                    return

            await self._after_message(session, awaiting[0])
            self._restart_inactivity(session)

    # ════════════════════════════════════════════════════════
    #  ABSOLUTE KILL TIMER  (1v1 only, never resets)
    # ════════════════════════════════════════════════════════

    async def _absolute_timeout_watcher(self, session_id: str) -> None:
        try:
            await asyncio.sleep(GAME_ABSOLUTE_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            return

        session = self._sessions.get(session_id)
        if not session:
            return

        guild   = self.bot.get_guild(session.guild_id)
        channel = guild.get_channel(session.channel_id) if guild else None

        if session.last_actor is not None:
            winner_id = session.last_actor
            loser_ids = [uid for uid in session.player_ids if uid != winner_id]
            loser_id  = loser_ids[0] if loser_ids else None

            winner         = guild.get_member(winner_id) if guild else None
            winner_mention = winner.mention if winner else f"<@{winner_id}>"

            if channel:
                await channel.send(embed=discord.Embed(
                    title="⏰ Game Auto-Ended",
                    description=(
                        f"This game has been running too long and was automatically ended.\n"
                        f"{winner_mention} wins by being the last to act!"
                    ),
                    color=discord.Color.red(),
                ))
            await self.end_session(
                session_id, GameResult(winner_id=winner_id, loser_id=loser_id, is_draw=False), guild)
        else:
            all_mentions = " ".join(f"<@{uid}>" for uid in session.player_ids)
            if channel:
                await channel.send(content=all_mentions, embed=discord.Embed(
                    title="⏰ Game Cancelled",
                    description="This game has been inactive with no moves made. It has been cancelled.",
                    color=discord.Color.red(),
                ))
            self._discard(session)
            await db.delete_session(session_id)

    # ════════════════════════════════════════════════════════
    #  MESSAGE ROUTING  (DMs and game channel)
    # ════════════════════════════════════════════════════════

    @commands.Cog.listener("on_message")
    async def on_game_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        session = self.get_session_for_player(message.author.id)
        if not session:
            return

        is_dm   = isinstance(message.channel, discord.DMChannel)
        content = message.content

        if not is_dm:
            if message.channel.id != session.channel_id:
                return
            prefix = await db.get_prefix(message.guild.id)
            if content.startswith(prefix):
                rest = content[len(prefix):].split()
                # Skip real bot commands (.resign, .score, ...)
                if rest and self.bot.get_command(rest[0].lower()) is not None:
                    return
                # Games hard-code "." — translate a custom prefix
                if prefix != ".":
                    content = "." + content[len(prefix):]

        async with session.lock:
            if session.session_id not in self._sessions:
                return
            game = session.game
            try:
                result = await game.handle_message(
                    user_id=message.author.id,
                    content=content,
                    is_dm=is_dm,
                    message=message,
                )
            except Exception:
                log.exception("Error in %s while handling a message", game.game_name)
                return

            # Some games end by clearing is_active without returning a result
            if result is None and not game.is_active and hasattr(game, "_build_game_result"):
                result = game._build_game_result()

            if result is not None:
                await self.end_session(session.session_id, result,
                                       self.bot.get_guild(session.guild_id))
            else:
                await self._after_message(session, message.author.id)

    # ════════════════════════════════════════════════════════
    #  CRASH RECOVERY
    # ════════════════════════════════════════════════════════

    async def _restore_sessions(self) -> None:
        await self.bot.wait_until_ready()
        for row in await db.get_all_sessions():
            try:
                cls = GAME_CLASSES.get(row["game_name"])
                guild = self.bot.get_guild(row["guild_id"])
                channel = guild.get_channel(row["channel_id"]) if guild else None
                players = [guild.get_member(pid) for pid in row["player_ids"]] if guild else []
                if not (cls and guild and channel and players and all(players)):
                    raise ValueError("missing class/guild/channel/member")

                game = cls(bot=self.bot, channel=channel, players=players, guild_id=guild.id)
                game.load_state(row["state"])

                session = GameSession(row["session_id"], game, guild.id,
                                      channel.id, row["player_ids"])
                session.last_state, _ = self._snapshot(game)
                self._sessions[session.session_id] = session
                released = getattr(game, "resigned", set())
                for uid in row["player_ids"]:
                    if uid not in released:
                        self._player_session[uid] = session.session_id
                self._start_timers(session)
                log.info("Restored session %s (%s)", row["session_id"], row["game_name"])
            except Exception:
                log.warning("Could not restore session %s — discarding",
                            row["session_id"], exc_info=True)
                await db.delete_session(row["session_id"])


# ════════════════════════════════════════════════════════════
#  INACTIVITY PROMPT VIEW
# ════════════════════════════════════════════════════════════

class _InactivityView(discord.ui.View):
    """Buttons posted when a player goes idle. Only the waiting player can use them."""

    def __init__(self, session_id: str, waiting_player_id: int, inactive_player_id: int,
                 sessions_cog: Sessions, guild: discord.Guild, minutes: int):
        super().__init__(timeout=None)
        self.session_id         = session_id
        self.waiting_player_id  = waiting_player_id
        self.inactive_player_id = inactive_player_id
        self.sessions_cog       = sessions_cog
        self.guild              = guild
        self.wait_button.label  = f"Wait another {minutes} minutes"

    @discord.ui.button(label="🏆 Claim Victory", style=discord.ButtonStyle.success)
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.waiting_player_id:
            await interaction.response.send_message(
                "❌ Only the waiting player can claim victory.", ephemeral=True)
            return

        session = self.sessions_cog.get_session(self.session_id)
        if not session:
            await interaction.response.edit_message(
                content="⚠️ This game has already ended.", embed=None, view=None)
            return

        self.stop()
        winner_id, inactive_id = interaction.user.id, self.inactive_player_id
        inactive = self.guild.get_member(inactive_id)
        inactive_mention = inactive.mention if inactive else f"<@{inactive_id}>"

        await interaction.response.edit_message(
            embed=discord.Embed(
                title="🏆 Victory Claimed",
                description=(f"{interaction.user.mention} claims victory!\n"
                             f"{inactive_mention} was inactive too long."),
                color=discord.Color.gold(),
            ),
            view=None,
        )
        await self.sessions_cog.end_session(
            self.session_id,
            GameResult(winner_id=winner_id, loser_id=inactive_id, is_draw=False),
            self.guild,
        )

    @discord.ui.button(label="Wait another X minutes", style=discord.ButtonStyle.secondary)
    async def wait_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.waiting_player_id:
            await interaction.response.send_message(
                "❌ Only the waiting player can reset the timer.", ephemeral=True)
            return

        session = self.sessions_cog.get_session(self.session_id)
        if not session:
            await interaction.response.edit_message(
                content="⚠️ This game has already ended.", embed=None, view=None)
            return

        self.stop()
        minutes = config.GAME_TIMEOUT_SECONDS // 60
        await interaction.response.edit_message(
            embed=discord.Embed(
                description=f"⏳ Timer reset. Waiting another **{minutes} minutes**…",
                color=discord.Color.blurple(),
            ),
            view=None,
        )
        # Restart the inactivity watcher without touching last_actor
        self.sessions_cog._restart_inactivity(session)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Sessions(bot))
