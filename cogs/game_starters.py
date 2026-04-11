# ============================================================
#  cogs/game_starters.py — Game launch commands
#
#  Commands:
#    .dth @opponent [bet]       — Drop the Handkerchief
#    .mdth @opponent [bet]      — Manga Accurate DTH
#    .gops @opponent [bet]      — Game of Pure Strategy
#    .dotty @opponent [bet]     — Bloody Dotty
#    .comb @opponent [bet]      — Combination
#    .airpoker @opponent [bet]  — Air Poker
#    .contr @opponent [bet]     — Contradiction
#    .kb @opponent [bet]        — Knucklebones
#    .kod [bet]                 — King of Diamonds (lobby)
# ============================================================

import discord
from discord.ext import commands
from typing import Optional, Dict, Set

import config
import database as db
from cogs.utils import require_player, channel_only, parse_amount

# Game imports
from games.dth import DTHGame
from games.mdth import MDTHGame
from games.gops import GOPSGame
from games.dotty import DottyGame
from games.combination import CombinationGame
from games.airpoker import AirPokerGame
from games.contradiction import ContradictionGame
from games.knucklebones import KnuckleBonesGame
from games.kod import KODGame

CHALLENGE_TIMEOUT = 60  # seconds before a challenge expires

# Track active challenges: user_id -> set of challenge message IDs
_active_challenges: Dict[int, Set[int]] = {}


# ════════════════════════════════════════════════════════════
#  CHALLENGE VIEW
# ════════════════════════════════════════════════════════════

class ChallengeView(discord.ui.View):
    """
    Posted after a player issues a game challenge.
    Only the challenged opponent can interact.
    Expires after CHALLENGE_TIMEOUT seconds.
    """

    def __init__(
            self,
            challenger: discord.Member,
            opponent: discord.Member,
            game_class,
            bet_amount: int,
            ctx: commands.Context,
            message_id: int,
    ):
        super().__init__(timeout=CHALLENGE_TIMEOUT)
        self.challenger = challenger
        self.opponent = opponent
        self.game_class = game_class
        self.bet_amount = bet_amount
        self.ctx = ctx
        self.message: Optional[discord.Message] = None
        self.message_id = message_id

    async def _cleanup_challenge(self):
        """Remove this challenge from active tracking."""
        if self.challenger.id in _active_challenges:
            _active_challenges[self.challenger.id].discard(self.message_id)
            if not _active_challenges[self.challenger.id]:
                del _active_challenges[self.challenger.id]

    # ── Accept ────────────────────────────────────────────────

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message(
                "❌ This challenge isn't for you.", ephemeral=True
            )
            return

        # Check if opponent has an active challenge
        if self.opponent.id in _active_challenges:
            await interaction.response.send_message(
                "❌ You cannot accept a challenge while you have an active challenge pending.\n"
                "Please wait for your existing challenge to expire or be resolved.",
                ephemeral=True
            )
            return

        # Check if challenger is still valid (they might have started another game)
        sessions_cog = self.ctx.bot.get_cog("Sessions")
        if sessions_cog and sessions_cog.get_session_for_player(self.challenger.id):
            await interaction.response.send_message(
                f"❌ {self.challenger.mention} is already in an active game.",
                ephemeral=True
            )
            return

        self.stop()
        await self._cleanup_challenge()

        for item in self.children:
            item.disabled = True

        # Create embed for accepted challenge
        embed = discord.Embed(
            title="✅ Challenge Accepted!",
            description=f"{self.opponent.mention} accepted the challenge from {self.challenger.mention}!",
            color=discord.Color.green()
        )

        # Add game and bet info
        settings = await db.get_guild_settings(self.ctx.guild.id)
        emoji = settings["currency_emoji"]
        game_name = self.game_class.game_name
        bet_str = f"Bet: {emoji}{self.bet_amount:,}" if self.bet_amount > 0 else "No bet"

        embed.set_footer(text=f"{game_name} | {bet_str}")

        await interaction.response.edit_message(
            embed=embed,
            view=self,
        )

        # Now actually start the game
        await _start_game(
            ctx=self.ctx,
            challenger=self.challenger,
            opponent=self.opponent,
            game_class=self.game_class,
            bet_amount=self.bet_amount,
        )

    # ── Reject ────────────────────────────────────────────────

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message(
                "❌ This challenge isn't for you.", ephemeral=True
            )
            return

        self.stop()
        await self._cleanup_challenge()

        for item in self.children:
            item.disabled = True

        # Create embed for rejected challenge
        embed = discord.Embed(
            title="❌ Challenge Rejected",
            description=f"{self.opponent.mention} rejected the challenge from {self.challenger.mention}.",
            color=discord.Color.red()
        )

        # Add game and bet info
        settings = await db.get_guild_settings(self.ctx.guild.id)
        emoji = settings["currency_emoji"]
        game_name = self.game_class.game_name
        bet_str = f"Bet: {emoji}{self.bet_amount:,}" if self.bet_amount > 0 else "No bet"

        embed.set_footer(text=f"{game_name} | {bet_str}")

        await interaction.response.edit_message(
            embed=embed,
            view=self,
        )

    # ── Timeout ───────────────────────────────────────────────

    async def on_timeout(self):
        await self._cleanup_challenge()

        if self.message:
            try:
                for item in self.children:
                    item.disabled = True

                # Create embed for expired challenge
                embed = discord.Embed(
                    title="🕒 Challenge Expired",
                    description=f"{self.opponent.mention} didn't respond to {self.challenger.mention}'s challenge in time.",
                    color=discord.Color.dark_orange()
                )

                # Add game and bet info
                settings = await db.get_guild_settings(self.ctx.guild.id)
                emoji = settings["currency_emoji"]
                game_name = self.game_class.game_name
                bet_str = f"Bet: {emoji}{self.bet_amount:,}" if self.bet_amount > 0 else "No bet"

                embed.set_footer(text=f"{game_name} | {bet_str}")

                await self.message.edit(
                    embed=embed,
                    view=self,
                )
            except Exception:
                pass


# ════════════════════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════════════════════

async def _send_challenge(
        ctx: commands.Context,
        opponent: discord.Member,
        game_class,
        bet_amount: int,
) -> None:
    """
    Validate the challenge, then post the Accept/Reject embed.
    All validation (session check, balance, etc.) happens here.
    """
    if opponent.bot:
        await ctx.send("❌ You can't challenge a bot.")
        return
    if opponent == ctx.author:
        await ctx.send("❌ You can't challenge yourself.")
        return
    if bet_amount < 0:
        await ctx.send("❌ Bet amount can't be negative.")
        return

    # Check if challenger already has an active challenge
    if ctx.author.id in _active_challenges:
        await ctx.send(
            "❌ You already have an active challenge pending.\n"
            "Please wait for it to expire or be resolved before sending a new one."
        )
        return

    # Check if opponent has an active challenge
    if opponent.id in _active_challenges:
        await ctx.send(
            f"❌ {opponent.mention} already has an active challenge pending.\n"
            "They cannot receive new challenges until it's resolved."
        )
        return

    # Season membership checks
    challenger_player = await db.get_player(ctx.author.id, ctx.guild.id)
    if challenger_player is None:
        await ctx.send("❌ You haven't joined the season yet. Use `.join` first.")
        return

    opponent_player = await db.get_player(opponent.id, ctx.guild.id)
    if opponent_player is None:
        await ctx.send(f"❌ {opponent.mention} hasn't joined the season yet.")
        return

    # Active-game checks
    sessions_cog = ctx.bot.get_cog("Sessions")
    if sessions_cog is None:
        await ctx.send("❌ Session manager is not loaded. Contact an admin.")
        return

    if sessions_cog.get_session_for_player(ctx.author.id):
        await ctx.send("❌ You're already in an active game.")
        return
    if sessions_cog.get_session_for_player(opponent.id):
        await ctx.send(f"❌ {opponent.mention} is already in an active game.")
        return

    # Balance checks (done early so we don't surface this after acceptance)
    settings = await db.get_guild_settings(ctx.guild.id)
    emoji = settings["currency_emoji"]
    if bet_amount > 0:
        if challenger_player["balance"] < bet_amount:
            await ctx.send(
                f"❌ You don't have enough to cover the bet of "
                f"{emoji}**{bet_amount:,}**."
            )
            return
        if opponent_player["balance"] < bet_amount:
            await ctx.send(
                f"❌ {opponent.mention} doesn't have enough to cover the bet of "
                f"{emoji}**{bet_amount:,}**."
            )
            return

    # Build embed
    game_name = game_class.game_name
    bet_str = f"Bet: {emoji}**{bet_amount:,}**" if bet_amount > 0 else "No bet"

    embed = discord.Embed(
        title=f"{game_name} | {bet_str}",
        description=(
            f"{ctx.author.mention} has challenged you to a game of **{game_name}**"
        ),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=f"Challenge expires in {CHALLENGE_TIMEOUT} seconds.")

    view = ChallengeView(
        challenger=ctx.author,
        opponent=opponent,
        game_class=game_class,
        bet_amount=bet_amount,
        ctx=ctx,
        message_id=0,  # Will be updated after sending
    )

    msg = await ctx.send(content=opponent.mention, embed=embed, view=view)
    view.message = msg
    view.message_id = msg.id

    # Track this active challenge
    if ctx.author.id not in _active_challenges:
        _active_challenges[ctx.author.id] = set()
    _active_challenges[ctx.author.id].add(msg.id)


async def _start_game(
        ctx: commands.Context,
        challenger: discord.Member,
        opponent: discord.Member,
        game_class,
        bet_amount: int,
) -> None:
    """Instantiate the game and hand it to the session manager."""
    settings = await db.get_guild_settings(ctx.guild.id)
    season = settings["current_season"]

    players = [challenger, opponent]

    game = game_class(
        bot=ctx.bot,
        channel=ctx.channel,
        players=players,
        bet_amount=bet_amount,
        guild_id=ctx.guild.id,
        season_number=season,
    )

    sessions_cog = ctx.bot.get_cog("Sessions")
    if sessions_cog is None:
        await ctx.channel.send("❌ Session manager is not loaded. Contact an admin.")
        return

    await sessions_cog.start_session(
        game=game,
        guild_id=ctx.guild.id,
        channel=ctx.channel,
        players=players,
        bet_amount=bet_amount,
        season_number=season,
    )


# ════════════════════════════════════════════════════════════
#  GAMES COG
# ════════════════════════════════════════════════════════════

class Games(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Drop the Handkerchief ────────────────────────────────
    @commands.command(name="dth")
    @channel_only("games")
    @require_player()
    async def dth(self, ctx: commands.Context, opponent: discord.Member, raw_bet: str = "0"):
        """Challenge someone to a game of Drop the Handkerchief."""
        bet = parse_amount(raw_bet)
        if bet is None:
            await ctx.send("❌ Invalid bet amount. Examples: `5000`, `1e6`, `2.5e3`")
            return
        await _send_challenge(ctx, opponent, DTHGame, bet)

    # ── Manga Accurate DTH ───────────────────────────────────
    @commands.command(name="mdth")
    @channel_only("games")
    @require_player()
    async def mdth(self, ctx: commands.Context, opponent: discord.Member, raw_bet: str = "0"):
        """Challenge someone to a game of Manga Accurate DTH."""
        bet = parse_amount(raw_bet)
        if bet is None:
            await ctx.send("❌ Invalid bet amount. Examples: `5000`, `1e6`, `2.5e3`")
            return
        await _send_challenge(ctx, opponent, MDTHGame, bet)

    # ── Game of Pure Strategy ────────────────────────────────
    @commands.command(name="gops")
    @channel_only("games")
    @require_player()
    async def gops(self, ctx: commands.Context, opponent: discord.Member, raw_bet: str = "0"):
        """Challenge someone to a Game of Pure Strategy."""
        bet = parse_amount(raw_bet)
        if bet is None:
            await ctx.send("❌ Invalid bet amount. Examples: `5000`, `1e6`, `2.5e3`")
            return
        await _send_challenge(ctx, opponent, GOPSGame, bet)

    # ── Bloody Dotty ─────────────────────────────────────────
    @commands.command(name="dotty")
    @channel_only("games")
    @require_player()
    async def dotty(self, ctx: commands.Context, opponent: discord.Member, raw_bet: str = "0"):
        """Challenge someone to a game of Bloody Dotty."""
        bet = parse_amount(raw_bet)
        if bet is None:
            await ctx.send("❌ Invalid bet amount. Examples: `5000`, `1e6`, `2.5e3`")
            return
        await _send_challenge(ctx, opponent, DottyGame, bet)

    # ── Combination ──────────────────────────────────────────
    @commands.command(name="comb")
    @channel_only("games")
    @require_player()
    async def comb(self, ctx: commands.Context, opponent: discord.Member, raw_bet: str = "0"):
        """Challenge someone to a game of Combination."""
        bet = parse_amount(raw_bet)
        if bet is None:
            await ctx.send("❌ Invalid bet amount. Examples: `5000`, `1e6`, `2.5e3`")
            return
        await _send_challenge(ctx, opponent, CombinationGame, bet)

    # ── Air Poker ────────────────────────────────────────────
    @commands.command(name="airpoker")
    @channel_only("games")
    @require_player()
    async def airpoker(self, ctx: commands.Context, opponent: discord.Member, raw_bet: str = "0"):
        """Challenge someone to a game of Air Poker."""
        bet = parse_amount(raw_bet)
        if bet is None:
            await ctx.send("❌ Invalid bet amount. Examples: `5000`, `1e6`, `2.5e3`")
            return
        await _send_challenge(ctx, opponent, AirPokerGame, bet)

    # ── Contradiction ────────────────────────────────────────
    @commands.command(name="contr")
    @channel_only("games")
    @require_player()
    async def contr(self, ctx: commands.Context, opponent: discord.Member, raw_bet: str = "0"):
        """Challenge someone to a game of Contradiction."""
        bet = parse_amount(raw_bet)
        if bet is None:
            await ctx.send("❌ Invalid bet amount. Examples: `5000`, `1e6`, `2.5e3`")
            return
        await _send_challenge(ctx, opponent, ContradictionGame, bet)

    # ── Knucklebones ─────────────────────────────────────────
    @commands.command(name="kb")
    @channel_only("games")
    @require_player()
    async def kb(self, ctx: commands.Context, opponent: discord.Member, raw_bet: str = "0"):
        """Challenge someone to a game of Knucklebones."""
        bet = parse_amount(raw_bet)
        if bet is None:
            await ctx.send("❌ Invalid bet amount. Examples: `5000`, `1e6`, `2.5e3`")
            return
        await _send_challenge(ctx, opponent, KnuckleBonesGame, bet)

    # ── King of Diamonds (lobby) ─────────────────────────────
    @commands.command(name="kod")
    @channel_only("games")
    @require_player()
    async def kod(self, ctx: commands.Context, raw_bet: str = "0"):
        """
        Open a King of Diamonds lobby. Other players can join with the button.
        The host starts the game when ready.
        """
        bet = parse_amount(raw_bet)
        if bet is None:
            await ctx.send("❌ Invalid bet amount. Examples: `5000`, `1e6`, `2.5e3`")
            return
        if bet < 0:
            await ctx.send("❌ Bet amount can't be negative.")
            return

        settings = await db.get_guild_settings(ctx.guild.id)
        season = settings["current_season"]
        emoji = settings["currency_emoji"]

        bet_str = f"{emoji} **{bet:,}** per player" if bet > 0 else "**Friendly (no bet)**"

        embed = discord.Embed(
            title="👑 King of Diamonds — Lobby",
            description=(
                f"**Host:** {ctx.author.mention}\n"
                f"**Bet:** {bet_str}\n\n"
                f"Click **Join** to enter the lobby.\n"
                f"The host can click **Start** when ready "
                f"(minimum {config.KOD_MIN_PLAYERS} players)."
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(name=f"Players (1/{config.KOD_MAX_PLAYERS})",
                        value=f"• {ctx.author.mention}", inline=False)

        view = KODLobbyView(
            host=ctx.author,
            bet_amount=bet,
            guild_id=ctx.guild.id,
            season_number=season,
            bot=ctx.bot,
            channel=ctx.channel,
        )
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg
        view.embed = embed


# ════════════════════════════════════════════════════════════
#  KOD LOBBY VIEW  (unchanged)
# ════════════════════════════════════════════════════════════

class KODLobbyView(discord.ui.View):
    """Interactive lobby for King of Diamonds."""

    def __init__(
            self,
            host: discord.Member,
            bet_amount: int,
            guild_id: int,
            season_number: int,
            bot,
            channel: discord.TextChannel,
            timeout: float = 600.0,
    ):
        super().__init__(timeout=timeout)
        self.host = host
        self.bet_amount = bet_amount
        self.guild_id = guild_id
        self.season_number = season_number
        self.bot = bot
        self.channel = channel
        self.players: list[discord.Member] = [host]
        self.message: Optional[discord.Message] = None
        self.embed: Optional[discord.Embed] = None

    async def _update_embed(self, interaction: discord.Interaction):
        self.embed.set_field_at(
            0,
            name=f"Players ({len(self.players)}/{config.KOD_MAX_PLAYERS})",
            value="\n".join(f"• {m.mention}" for m in self.players),
            inline=False,
        )
        await interaction.response.edit_message(embed=self.embed, view=self)

    @discord.ui.button(label="Join", style=discord.ButtonStyle.success)
    async def join_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        if member in self.players:
            await interaction.response.send_message("❌ You're already in the lobby.", ephemeral=True)
            return
        if len(self.players) >= config.KOD_MAX_PLAYERS:
            await interaction.response.send_message("❌ Lobby is full.", ephemeral=True)
            return

        player = await db.get_player(member.id, self.guild_id)
        if player is None:
            await interaction.response.send_message(
                "❌ You need to join the season first (`.join`).", ephemeral=True
            )
            return

        sessions_cog = self.bot.get_cog("Sessions")
        if sessions_cog and sessions_cog.get_session_for_player(member.id):
            await interaction.response.send_message(
                "❌ You're already in an active game.", ephemeral=True
            )
            return

        self.players.append(member)
        await self._update_embed(interaction)

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.secondary)
    async def leave_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        if member == self.host:
            await interaction.response.send_message(
                "❌ The host can't leave. Close the lobby instead.", ephemeral=True
            )
            return
        if member not in self.players:
            await interaction.response.send_message("❌ You're not in this lobby.", ephemeral=True)
            return
        self.players.remove(member)
        await self._update_embed(interaction)

    @discord.ui.button(label="▶ Start", style=discord.ButtonStyle.primary)
    async def start_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.host:
            await interaction.response.send_message(
                "❌ Only the host can start the game.", ephemeral=True
            )
            return
        if len(self.players) < config.KOD_MIN_PLAYERS:
            await interaction.response.send_message(
                f"❌ Need at least {config.KOD_MIN_PLAYERS} players to start.", ephemeral=True
            )
            return

        self.stop()
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="👑 **King of Diamonds is starting!**", embed=None, view=self
        )

        game = KODGame(
            bot=self.bot,
            channel=self.channel,
            players=self.players,
            bet_amount=self.bet_amount,
            guild_id=self.guild_id,
            season_number=self.season_number,
        )

        sessions_cog = self.bot.get_cog("Sessions")
        if sessions_cog:
            await sessions_cog.start_session(
                game=game,
                guild_id=self.guild_id,
                channel=self.channel,
                players=self.players,
                bet_amount=self.bet_amount,
                season_number=self.season_number,
            )

    @discord.ui.button(label="✖ Close Lobby", style=discord.ButtonStyle.danger)
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.host:
            await interaction.response.send_message(
                "❌ Only the host can close the lobby.", ephemeral=True
            )
            return
        self.stop()
        await interaction.response.edit_message(
            content="❌ Lobby closed by the host.", embed=None, view=None
        )

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(
                    content="⏱️ Lobby timed out.", embed=None, view=None
                )
            except Exception:
                pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Games(bot))