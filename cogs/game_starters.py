# ============================================================
#  cogs/game_starters.py — Game launch commands
#
#  Commands:
#    .dth @opponent       — Drop the Handkerchief
#    .mdth @opponent      — Manga Accurate DTH
#    .gops @opponent      — Game of Pure Strategy
#    .dotty @opponent     — Bloody Dotty
#    .comb @opponent      — Combination
#    .airpoker @opponent  — Air Poker
#    .contr @opponent     — Contradiction
#    .kb @opponent        — Knucklebones
#    .lab @opponent       — Labyrinth
#    .kod                 — King of Diamonds (lobby)
# ============================================================

import discord
from discord.ext import commands
from typing import Optional, Dict, Set

import config
from cogs.utils import channel_only

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
from games.labyrinth import LabyrinthGame

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
    """

    def __init__(
            self,
            challenger: discord.Member,
            opponent: discord.Member,
            game_class,
            ctx: commands.Context,
            message_id: int,
    ):
        super().__init__(timeout=CHALLENGE_TIMEOUT)
        self.challenger = challenger
        self.opponent = opponent
        self.game_class = game_class
        self.ctx = ctx
        self.message: Optional[discord.Message] = None
        self.message_id = message_id

    async def _cleanup_challenge(self):
        if self.challenger.id in _active_challenges:
            _active_challenges[self.challenger.id].discard(self.message_id)
            if not _active_challenges[self.challenger.id]:
                del _active_challenges[self.challenger.id]

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message("❌ This challenge isn't for you.", ephemeral=True)
            return

        # Opponent has their own pending challenge
        if self.opponent.id in _active_challenges:
            await interaction.response.send_message(
                "❌ You cannot accept a challenge while you have an active challenge pending.\n"
                "Please wait for your existing challenge to expire or be resolved.",
                ephemeral=True
            )
            return

        sessions_cog = self.ctx.bot.get_cog("Sessions")
        if sessions_cog and sessions_cog.get_session_for_player(self.challenger.id):
            await interaction.response.send_message(
                f"❌ {self.challenger.mention} is already in an active game.", ephemeral=True
            )
            return

        self.stop()
        await self._cleanup_challenge()
        for item in self.children:
            item.disabled = True

        embed = discord.Embed(
            title="✅ Challenge Accepted!",
            description=f"{self.opponent.mention} accepted the challenge from {self.challenger.mention}!",
            color=discord.Color.green()
        )
        embed.set_footer(text=self.game_class.game_name)
        await interaction.response.edit_message(embed=embed, view=self)

        await _start_game(
            ctx=self.ctx,
            challenger=self.challenger,
            opponent=self.opponent,
            game_class=self.game_class,
        )

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message("❌ This challenge isn't for you.", ephemeral=True)
            return

        self.stop()
        await self._cleanup_challenge()
        for item in self.children:
            item.disabled = True

        embed = discord.Embed(
            title="❌ Challenge Rejected",
            description=f"{self.opponent.mention} rejected the challenge from {self.challenger.mention}.",
            color=discord.Color.red()
        )
        embed.set_footer(text=self.game_class.game_name)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        await self._cleanup_challenge()
        if self.message:
            try:
                for item in self.children:
                    item.disabled = True
                embed = discord.Embed(
                    title="🕒 Challenge Expired",
                    description=f"{self.opponent.mention} didn't respond to {self.challenger.mention}'s challenge in time.",
                    color=discord.Color.dark_orange()
                )
                embed.set_footer(text=self.game_class.game_name)
                await self.message.edit(embed=embed, view=self)
            except Exception:
                pass


# ════════════════════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════════════════════

async def _send_challenge(ctx: commands.Context, opponent: discord.Member, game_class) -> None:
    """Validate the challenge, then post the Accept/Reject embed."""
    if opponent.bot:
        await ctx.send("❌ You can't challenge a bot.")
        return
    if opponent == ctx.author:
        await ctx.send("❌ You can't challenge yourself.")
        return

    if ctx.author.id in _active_challenges:
        await ctx.send(
            "❌ You already have an active challenge pending.\n"
            "Please wait for it to expire or be resolved before sending a new one."
        )
        return
    if opponent.id in _active_challenges:
        await ctx.send(
            f"❌ {opponent.mention} already has an active challenge pending.\n"
            "They cannot receive new challenges until it's resolved."
        )
        return

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

    game_name = game_class.game_name
    embed = discord.Embed(
        title=game_name,
        description=f"{ctx.author.mention} has challenged you to a game of **{game_name}**",
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=f"Challenge expires in {CHALLENGE_TIMEOUT} seconds.")

    view = ChallengeView(
        challenger=ctx.author,
        opponent=opponent,
        game_class=game_class,
        ctx=ctx,
        message_id=0,
    )
    msg = await ctx.send(content=opponent.mention, embed=embed, view=view)
    view.message = msg
    view.message_id = msg.id

    _active_challenges.setdefault(ctx.author.id, set()).add(msg.id)


async def _start_game(
        ctx: commands.Context,
        challenger: discord.Member,
        opponent: discord.Member,
        game_class,
) -> None:
    """Instantiate the game and hand it to the session manager."""
    players = [challenger, opponent]
    game = game_class(
        bot=ctx.bot,
        channel=ctx.channel,
        players=players,
        guild_id=ctx.guild.id,
    )

    sessions_cog = ctx.bot.get_cog("Sessions")
    if sessions_cog is None:
        await ctx.channel.send("❌ Session manager is not loaded. Contact an admin.")
        return

    try:
        await sessions_cog.start_session(
            game=game,
            guild_id=ctx.guild.id,
            channel=ctx.channel,
            players=players,
        )
    except discord.Forbidden:
        await ctx.channel.send(
            "❌ I couldn't DM one of the players. Enable DMs from server members and try again."
        )


# ════════════════════════════════════════════════════════════
#  GAMES COG
# ════════════════════════════════════════════════════════════

class Games(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="dth")
    @channel_only("games")
    async def dth(self, ctx: commands.Context, opponent: discord.Member):
        """Challenge someone to a game of Drop the Handkerchief."""
        await _send_challenge(ctx, opponent, DTHGame)

    @commands.command(name="mdth")
    @channel_only("games")
    async def mdth(self, ctx: commands.Context, opponent: discord.Member):
        """Challenge someone to a game of Manga Accurate DTH."""
        await _send_challenge(ctx, opponent, MDTHGame)

    @commands.command(name="gops")
    @channel_only("games")
    async def gops(self, ctx: commands.Context, opponent: discord.Member):
        """Challenge someone to a Game of Pure Strategy."""
        await _send_challenge(ctx, opponent, GOPSGame)

    @commands.command(name="dotty")
    @channel_only("games")
    async def dotty(self, ctx: commands.Context, opponent: discord.Member):
        """Challenge someone to a game of Bloody Dotty."""
        await _send_challenge(ctx, opponent, DottyGame)

    @commands.command(name="comb")
    @channel_only("games")
    async def comb(self, ctx: commands.Context, opponent: discord.Member):
        """Challenge someone to a game of Combination."""
        await _send_challenge(ctx, opponent, CombinationGame)

    @commands.command(name="airpoker")
    @channel_only("games")
    async def airpoker(self, ctx: commands.Context, opponent: discord.Member):
        """Challenge someone to a game of Air Poker."""
        await _send_challenge(ctx, opponent, AirPokerGame)

    @commands.command(name="contr")
    @channel_only("games")
    async def contr(self, ctx: commands.Context, opponent: discord.Member):
        """Challenge someone to a game of Contradiction."""
        await _send_challenge(ctx, opponent, ContradictionGame)

    @commands.command(name="kb")
    @channel_only("games")
    async def kb(self, ctx: commands.Context, opponent: discord.Member):
        """Challenge someone to a game of Knucklebones."""
        await _send_challenge(ctx, opponent, KnuckleBonesGame)

    @commands.command(name="lab")
    @channel_only("games")
    async def lab(self, ctx: commands.Context, opponent: discord.Member):
        """Challenge someone to a game of Labyrinth. Both players build a maze in DMs."""
        await _send_challenge(ctx, opponent, LabyrinthGame)

    @commands.command(name="kod")
    @channel_only("games")
    async def kod(self, ctx: commands.Context):
        """
        Open a King of Diamonds lobby. Other players can join with the button.
        The host starts the game when ready.
        """
        sessions_cog = ctx.bot.get_cog("Sessions")
        if sessions_cog and sessions_cog.get_session_for_player(ctx.author.id):
            await ctx.send("❌ You're already in an active game.")
            return

        embed = discord.Embed(
            title="👑 King of Diamonds — Lobby",
            description=(
                f"**Host:** {ctx.author.mention}\n\n"
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
            guild_id=ctx.guild.id,
            bot=ctx.bot,
            channel=ctx.channel,
        )
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg
        view.embed = embed


# ════════════════════════════════════════════════════════════
#  KOD LOBBY VIEW
# ════════════════════════════════════════════════════════════

class KODLobbyView(discord.ui.View):
    """Interactive lobby for King of Diamonds."""

    def __init__(self, host: discord.Member, guild_id: int, bot,
                 channel: discord.TextChannel, timeout: float = 600.0):
        super().__init__(timeout=timeout)
        self.host = host
        self.guild_id = guild_id
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

        sessions_cog = self.bot.get_cog("Sessions")
        if sessions_cog and sessions_cog.get_session_for_player(member.id):
            await interaction.response.send_message("❌ You're already in an active game.", ephemeral=True)
            return

        self.players.append(member)
        await self._update_embed(interaction)

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.secondary)
    async def leave_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        if member == self.host:
            await interaction.response.send_message(
                "❌ The host can't leave. Close the lobby instead.", ephemeral=True)
            return
        if member not in self.players:
            await interaction.response.send_message("❌ You're not in this lobby.", ephemeral=True)
            return
        self.players.remove(member)
        await self._update_embed(interaction)

    @discord.ui.button(label="▶ Start", style=discord.ButtonStyle.primary)
    async def start_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.host:
            await interaction.response.send_message("❌ Only the host can start the game.", ephemeral=True)
            return
        if len(self.players) < config.KOD_MIN_PLAYERS:
            await interaction.response.send_message(
                f"❌ Need at least {config.KOD_MIN_PLAYERS} players to start.", ephemeral=True)
            return

        sessions_cog = self.bot.get_cog("Sessions")
        busy = [m for m in self.players
                if sessions_cog and sessions_cog.get_session_for_player(m.id)]
        if busy:
            await interaction.response.send_message(
                "❌ Some players are already in another game: "
                + ", ".join(m.mention for m in busy), ephemeral=True)
            return

        self.stop()
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="👑 **King of Diamonds is starting!**", embed=None, view=self)

        game = KODGame(
            bot=self.bot,
            channel=self.channel,
            players=self.players,
            guild_id=self.guild_id,
        )
        if sessions_cog:
            await sessions_cog.start_session(
                game=game,
                guild_id=self.guild_id,
                channel=self.channel,
                players=self.players,
            )

    @discord.ui.button(label="✖ Close Lobby", style=discord.ButtonStyle.danger)
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.host:
            await interaction.response.send_message("❌ Only the host can close the lobby.", ephemeral=True)
            return
        self.stop()
        await interaction.response.edit_message(content="❌ Lobby closed by the host.", embed=None, view=None)

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(content="⏱️ Lobby timed out.", embed=None, view=None)
            except Exception:
                pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Games(bot))
