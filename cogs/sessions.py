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
#    - Absolute kill timer: if the game has been inactive for
#      GAME_ABSOLUTE_TIMEOUT_SECONDS (1 hour), the player who acted
#      last wins automatically. If neither player has acted since the
#      game started, the game is cancelled with full refunds.
#    - Pay out bet escrow on game end
#    - Trigger class bonuses via cogs/classes.py
#    - Record results to game_results.db
#    - Enforce one-game-per-player rule
#    - .resign  — forfeit your current game immediately
# ============================================================

import asyncio
import uuid
from datetime import datetime
from typing import Optional, TYPE_CHECKING

import discord
from discord.ext import commands

import config
import database as db
from games.base_game import BaseGame, GameResult

if TYPE_CHECKING:
    pass

# Total time (seconds) of absolute inactivity before the game is auto-resolved.
GAME_ABSOLUTE_TIMEOUT_SECONDS = 36000   # 1 hour


class GameSession:
    """In-memory representation of one active game."""

    def __init__(
        self,
        session_id: str,
        game: BaseGame,
        guild_id: int,
        channel_id: int,
        player_ids: list[int],
        bet_amount: int,
    ):
        self.session_id   = session_id
        self.game         = game
        self.guild_id     = guild_id
        self.channel_id   = channel_id
        self.player_ids   = player_ids
        self.bet_amount   = bet_amount
        self.started_at   = datetime.utcnow()

        # Tracks the last user_id to make a valid move (None = nobody yet)
        self.last_actor: Optional[int] = None
        # Timestamp of the last valid move
        self.last_action_at: datetime = datetime.utcnow()

        # Single inactivity task for the whole session
        self._inactivity_task: Optional[asyncio.Task] = None
        # Absolute kill task — started once, never reset
        self._absolute_task:   Optional[asyncio.Task] = None


class Sessions(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Maps session_id → GameSession
        self._sessions: dict[str, GameSession] = {}
        # Maps user_id → session_id (for quick lookup)
        self._player_session: dict[int, str] = {}

    # ════════════════════════════════════════════════════════
    #  PUBLIC API
    # ════════════════════════════════════════════════════════

    async def start_session(
        self,
        game: BaseGame,
        guild_id: int,
        channel: discord.TextChannel,
        players: list[discord.Member],
        bet_amount: int = 0,
        season_number: int = 1,
    ) -> Optional[str]:
        """
        Create and start a new game session.
        Returns session_id on success, None if any player is already in a game.
        """
        # One game per player check
        for member in players:
            if member.id in self._player_session:
                existing_sid = self._player_session[member.id]
                existing = self._sessions.get(existing_sid)
                game_name = existing.game.game_name if existing else "a game"
                await channel.send(
                    f"❌ {member.mention} is already in a game of **{game_name}**. "
                    f"Finish or forfeit that game first."
                )
                return None

        # Lock bet escrow from all players
        if bet_amount > 0:
            for member in players:
                player = await db.get_player(member.id, guild_id)
                if player is None or player["balance"] < bet_amount:
                    await channel.send(
                        f"❌ {member.mention} doesn't have enough carats to cover the "
                        f"**{bet_amount:,}** bet."
                    )
                    return None
            for member in players:
                await db.update_balance(member.id, guild_id, -bet_amount)

        session_id = str(uuid.uuid4())
        player_ids = [m.id for m in players]

        session = GameSession(
            session_id=session_id,
            game=game,
            guild_id=guild_id,
            channel_id=channel.id,
            player_ids=player_ids,
            bet_amount=bet_amount,
        )

        self._sessions[session_id] = session
        for uid in player_ids:
            self._player_session[uid] = session_id

        # Persist to sessions.db for crash recovery
        await db.save_session(
            session_id, game.game_name, guild_id, channel.id,
            player_ids, game.get_state(), bet_amount,
        )

        # Start the inactivity timer and the absolute kill timer
        session._inactivity_task = asyncio.create_task(
            self._inactivity_watcher(session_id)
        )
        session._absolute_task = asyncio.create_task(
            self._absolute_timeout_watcher(session_id)
        )

        await game.start()
        return session_id

    def get_session_for_player(self, user_id: int) -> Optional[GameSession]:
        sid = self._player_session.get(user_id)
        return self._sessions.get(sid) if sid else None

    def get_session(self, session_id: str) -> Optional[GameSession]:
        return self._sessions.get(session_id)

    def record_action(self, session_id: str, user_id: int) -> None:
        """
        Call whenever a player makes a valid move.
        Resets the inactivity timer and updates last_actor.
        """
        session = self._sessions.get(session_id)
        if not session:
            return

        session.last_actor     = user_id
        session.last_action_at = datetime.utcnow()

        # Restart inactivity timer
        if session._inactivity_task:
            session._inactivity_task.cancel()
        session._inactivity_task = asyncio.create_task(
            self._inactivity_watcher(session_id)
        )

    # Keep backward-compat name used elsewhere in the codebase
    def reset_timeout(self, session_id: str, user_id: int) -> None:
        self.record_action(session_id, user_id)

    async def end_session(
        self,
        session_id: str,
        result: GameResult,
        guild: discord.Guild,
    ) -> None:
        """
        Finalise a game: pay out escrow, record result, trigger class bonuses.
        """
        session = self._sessions.pop(session_id, None)
        if not session:
            return

        # Cancel all timer tasks
        if session._inactivity_task:
            session._inactivity_task.cancel()
        if session._absolute_task:
            session._absolute_task.cancel()

        # Remove player→session mappings
        for uid in session.player_ids:
            self._player_session.pop(uid, None)

        await db.delete_session(session_id)

        settings  = await db.get_guild_settings(guild.id)
        season    = settings["current_season"]
        emoji     = settings["currency_emoji"]
        name_s    = settings["currency_name"]
        name_p    = settings["currency_name_plural"]
        bet       = session.bet_amount

        # ── Payout ───────────────────────────────────────────
        if result.is_draw:
            if bet > 0:
                for uid in session.player_ids:
                    await db.update_balance(uid, guild.id, bet)
        elif result.winner_id:
            total_pot = bet * len(session.player_ids)
            if total_pot > 0:
                await db.update_balance(result.winner_id, guild.id, total_pot)

            # ── Totem of Undying: refund the loser's bet ──────
            if bet > 0 and result.loser_id:
                totem_active: set = getattr(self.bot, "_totem_active", set())
                if result.loser_id in totem_active:
                    totem_active.discard(result.loser_id)
                    await db.update_balance(result.loser_id, guild.id, bet)
                    channel = guild.get_channel(session.channel_id)
                    loser_member = guild.get_member(result.loser_id)
                    loser_mention = loser_member.mention if loser_member else f"<@{result.loser_id}>"
                    if channel:
                        await channel.send(
                            f"🏺 {loser_mention}'s **Totem of Undying** activates! "
                            f"Their bet of {emoji} **{bet:,}** has been refunded."
                        )

        # ── Record result ────────────────────────────────────
        loser_id = result.loser_id or (
            session.player_ids[1]
            if len(session.player_ids) == 2 and result.winner_id == session.player_ids[0]
            else (session.player_ids[0] if len(session.player_ids) == 2 else None)
        )
        player2_id = session.player_ids[1] if len(session.player_ids) >= 2 else None

        await db.record_result(
            game_name=session.game.game_name,
            player1_id=session.player_ids[0],
            player2_id=player2_id,
            winner_id=result.winner_id,
            is_draw=result.is_draw,
            guild_id=guild.id,
            season_number=season,
            bet_amount=bet,
        )

        # ── Class bonuses ────────────────────────────────────
        classes_cog = self.bot.get_cog("Classes")
        bonuses: dict[int, int] = {}
        if classes_cog:
            bonuses = await classes_cog.on_game_end(
                guild=guild,
                winner_id=result.winner_id,
                loser_id=result.loser_id,
                game_name=session.game.game_name,
                bet_amount=bet,
                is_draw=result.is_draw,
            )

        # ── Announce bonuses ─────────────────────────────────
        channel = guild.get_channel(session.channel_id)
        if channel and bonuses:
            bonus_lines = []
            for uid, amount in bonuses.items():
                member = guild.get_member(uid)
                name   = member.mention if member else f"<@{uid}>"
                bonus_lines.append(
                    f"✨ {name} earned a class bonus of "
                    f"{emoji} **{amount:,}** {name_p if amount != 1 else name_s}!"
                )
            if bonus_lines:
                await channel.send("\n".join(bonus_lines))

    # ════════════════════════════════════════════════════════
    #  .cancel COMMAND
    # ════════════════════════════════════════════════════════

    @commands.command(name="cancel")
    async def cancel(self, ctx: commands.Context):
        """Cancel your current game. Bets are returned to all players."""
        if ctx.guild is None:
            await ctx.send("❌ This command can only be used in a server.")
            return

        session = self.get_session_for_player(ctx.author.id)
        if session is None:
            await ctx.send("❌ You're not in an active game.")
            return

        game_name = session.game.game_name
        bet = session.bet_amount

        if bet > 0:
            for uid in session.player_ids:
                await db.update_balance(uid, ctx.guild.id, bet)

        if session._inactivity_task:
            session._inactivity_task.cancel()
        if session._absolute_task:
            session._absolute_task.cancel()

        self._sessions.pop(session.session_id, None)
        for uid in session.player_ids:
            self._player_session.pop(uid, None)
        await db.delete_session(session.session_id)

        settings = await db.get_guild_settings(ctx.guild.id)
        emoji  = settings["currency_emoji"]
        name_p = settings["currency_name_plural"]

        refund_msg = (
            f" {emoji} **{bet:,}** {name_p} refunded to all players."
            if bet > 0 else ""
        )
        await ctx.send(
            f"🛑 {ctx.author.mention} cancelled the game of **{game_name}**.{refund_msg}"
        )

    # ════════════════════════════════════════════════════════
    #  .resign COMMAND
    # ════════════════════════════════════════════════════════

    @commands.command(name="resign")
    async def resign(self, ctx: commands.Context):
        """Forfeit your current game. The opponent wins and collects the pot."""
        if ctx.guild is None:
            await ctx.send("❌ This command can only be used in a server.")
            return

        session = self.get_session_for_player(ctx.author.id)
        if session is None:
            await ctx.send("❌ You're not in an active game.")
            return

        others = [uid for uid in session.player_ids if uid != ctx.author.id]
        if not others:
            await ctx.send("❌ Cannot resign — no opponent found.")
            return

        winner_id      = others[0]
        winner         = ctx.guild.get_member(winner_id)
        winner_mention = winner.mention if winner else f"<@{winner_id}>"

        await ctx.send(
            f"🏳️ {ctx.author.mention} resigns from **{session.game.game_name}**! "
            f"{winner_mention} wins!"
        )

        result = GameResult(
            winner_id=winner_id,
            loser_id=ctx.author.id,
            is_draw=False,
        )
        await self.end_session(session.session_id, result, ctx.guild)

    # ════════════════════════════════════════════════════════
    #  INACTIVITY WATCHER  (resets on every valid move)
    # ════════════════════════════════════════════════════════

    async def _inactivity_watcher(self, session_id: str) -> None:
        """
        Sleep for GAME_TIMEOUT_SECONDS.  If nobody acts, post an embed with
        two buttons: "Claim Victory" and "Wait X minutes".
        Only the waiting player (last actor) can interact.
        This cycle repeats until a button is pressed or the absolute timer fires.
        """
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

        # Determine who went quiet and who is waiting.
        if len(session.player_ids) == 2:
            p0, p1 = session.player_ids
            if session.last_actor == p0:
                inactive_id = p1
                waiting_id  = p0
            elif session.last_actor == p1:
                inactive_id = p0
                waiting_id  = p1
            else:
                # Neither player has acted yet
                inactive_id = None
                waiting_id  = None
        else:
            inactive_id = None
            waiting_id  = None

        inactive_member = guild.get_member(inactive_id) if guild and inactive_id else None
        waiting_member  = guild.get_member(waiting_id)  if guild and waiting_id  else None

        inactive_name   = (
            inactive_member.display_name if inactive_member
            else (f"<@{inactive_id}>" if inactive_id else "A player")
        )
        waiting_mention = (
            waiting_member.mention if waiting_member
            else (f"<@{waiting_id}>" if waiting_id else "The other player")
        )
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

    # ════════════════════════════════════════════════════════
    #  ABSOLUTE KILL TIMER  (never resets)
    # ════════════════════════════════════════════════════════

    async def _absolute_timeout_watcher(self, session_id: str) -> None:
        """
        If the game has been running for GAME_ABSOLUTE_TIMEOUT_SECONDS with
        no action at all, resolve it:
          - last_actor wins (if someone has acted)
          - otherwise cancel with full refund
        """
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

            embed = discord.Embed(
                title="⏰ Game Auto-Ended",
                description=(
                    f"This game has been inactive for **1 hour** and has been automatically ended.\n"
                    f"{winner_mention} wins by being the last to act!"
                ),
                color=discord.Color.red(),
            )
            if channel:
                await channel.send(embed=embed)

            result = GameResult(
                winner_id=winner_id,
                loser_id=loser_id,
                is_draw=False,
            )
        else:
            all_mentions = " ".join(f"<@{uid}>" for uid in session.player_ids)
            embed = discord.Embed(
                title="⏰ Game Cancelled",
                description=(
                    "This game has been inactive for **1 hour** with no moves made.\n"
                    "It has been cancelled and all bets refunded."
                ),
                color=discord.Color.red(),
            )
            if channel:
                await channel.send(content=all_mentions, embed=embed)

            # Refund manually before cleaning up (no result to record)
            if session.bet_amount > 0 and guild:
                for uid in session.player_ids:
                    await db.update_balance(uid, guild.id, session.bet_amount)

            if session._inactivity_task:
                session._inactivity_task.cancel()
            self._sessions.pop(session_id, None)
            for uid in session.player_ids:
                self._player_session.pop(uid, None)
            await db.delete_session(session_id)
            return

        if guild:
            await self.end_session(session_id, result, guild)

    # ════════════════════════════════════════════════════════
    #  DM ROUTING
    # ════════════════════════════════════════════════════════

    @commands.Cog.listener("on_dm_game_message")
    async def on_dm_game_message(
        self, message: discord.Message, session_data: dict
    ) -> None:
        """
        Called by bot.py when a DM arrives for an active game player.
        Routes the message to the correct game's handle_message().
        """
        session = self.get_session_for_player(message.author.id)
        if not session:
            return

        result = await session.game.handle_message(
            user_id=message.author.id,
            content=message.content,
            is_dm=True,
            message=message,
        )

        self.record_action(session.session_id, message.author.id)

        if result is not None:
            guild = self.bot.get_guild(session.guild_id)
            if guild:
                await self.end_session(session.session_id, result, guild)

    # ════════════════════════════════════════════════════════
    #  CHANNEL MESSAGE ROUTING
    # ════════════════════════════════════════════════════════

    @commands.Cog.listener("on_message")
    async def on_channel_game_message(self, message: discord.Message) -> None:
        """
        Listen for in-channel game commands from active players.
        (Timeout responses are handled via buttons on the embed, not text.)
        """
        if message.author.bot or isinstance(message.channel, discord.DMChannel):
            return

        session = self.get_session_for_player(message.author.id)
        if not session:
            return

        if message.channel.id != session.channel_id:
            return

        # Only skip messages that are actual registered bot commands.
        prefix = await db.get_prefix(message.guild.id)
        if message.content.startswith(prefix):
            possible_cmd = (
                message.content[len(prefix):].split()[0].lower()
                if message.content[len(prefix):].split() else ""
            )
            if self.bot.get_command(possible_cmd) is not None:
                return

        result = await session.game.handle_message(
            user_id=message.author.id,
            content=message.content,
            is_dm=False,
            message=message,
        )

        self.record_action(session.session_id, message.author.id)

        if result is not None:
            await self.end_session(session.session_id, result, message.guild)


# ════════════════════════════════════════════════════════════
#  INACTIVITY PROMPT VIEW
# ════════════════════════════════════════════════════════════

class _InactivityView(discord.ui.View):
    """
    Embed buttons posted when a player goes idle.
    Only the waiting player (last actor) can interact.
    timeout=None so the buttons persist until explicitly stopped —
    the absolute kill timer will tear down the session if needed.
    """

    def __init__(
        self,
        session_id: str,
        waiting_player_id: Optional[int],
        inactive_player_id: Optional[int],
        sessions_cog: "Sessions",
        guild: discord.Guild,
        minutes: int,
    ):
        super().__init__(timeout=None)
        self.session_id         = session_id
        self.waiting_player_id  = waiting_player_id
        self.inactive_player_id = inactive_player_id
        self.sessions_cog       = sessions_cog
        self.guild              = guild

        # Update the "wait" button label with the actual minute count
        self.wait_button.label = f"Wait another {minutes} minutes"

    def _is_eligible(self, interaction: discord.Interaction) -> bool:
        """True if this interaction is from the eligible (waiting) player."""
        if self.waiting_player_id is None:
            # No clear waiting player — allow any participant
            session = self.sessions_cog.get_session(self.session_id)
            return session is not None and interaction.user.id in session.player_ids
        return interaction.user.id == self.waiting_player_id

    @discord.ui.button(label="🏆 Claim Victory", style=discord.ButtonStyle.success)
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._is_eligible(interaction):
            await interaction.response.send_message(
                "❌ Only the waiting player can claim victory.", ephemeral=True
            )
            return

        session = self.sessions_cog.get_session(self.session_id)
        if not session:
            await interaction.response.edit_message(
                content="⚠️ This game has already ended.", embed=None, view=None
            )
            return

        self.stop()

        winner_id   = interaction.user.id
        inactive_id = self.inactive_player_id

        winner   = self.guild.get_member(winner_id)
        inactive = self.guild.get_member(inactive_id) if inactive_id else None

        winner_mention   = winner.mention   if winner   else f"<@{winner_id}>"
        inactive_mention = (
            inactive.mention if inactive
            else (f"<@{inactive_id}>" if inactive_id else "the opponent")
        )

        result_embed = discord.Embed(
            title="🏆 Victory Claimed",
            description=(
                f"{winner_mention} claims victory!\n"
                f"{inactive_mention} was inactive too long."
            ),
            color=discord.Color.gold(),
        )
        await interaction.response.edit_message(embed=result_embed, view=None)

        result = GameResult(
            winner_id=winner_id,
            loser_id=inactive_id,
            is_draw=False,
        )
        await self.sessions_cog.end_session(self.session_id, result, self.guild)

    @discord.ui.button(label="Wait another X minutes", style=discord.ButtonStyle.secondary)
    async def wait_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._is_eligible(interaction):
            await interaction.response.send_message(
                "❌ Only the waiting player can reset the timer.", ephemeral=True
            )
            return

        session = self.sessions_cog.get_session(self.session_id)
        if not session:
            await interaction.response.edit_message(
                content="⚠️ This game has already ended.", embed=None, view=None
            )
            return

        self.stop()

        minutes = config.GAME_TIMEOUT_SECONDS // 60
        wait_embed = discord.Embed(
            description=f"⏳ Timer reset. Waiting another **{minutes} minutes**…",
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=wait_embed, view=None)

        # Restart inactivity watcher without touching last_actor
        if session._inactivity_task:
            session._inactivity_task.cancel()
        session._inactivity_task = asyncio.create_task(
            self.sessions_cog._inactivity_watcher(self.session_id)
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Sessions(bot))