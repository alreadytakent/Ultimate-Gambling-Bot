# ============================================================
#  cogs/referee.py — Referee (and Mod) commands
#
#  Commands:
#    .verify @player1 vs @player2 {result} {game_name} [bet] — record a result
#    .erase-result @player1 vs @player2 {result} {game_name} [bet] — remove most recent matching result
# ============================================================

import discord
from discord.ext import commands
from discord.ext.commands import check

import config
import database as db
from cogs.utils import has_referee_role, has_mod_role, fmt_currency, parse_amount


def has_referee_or_mod_role():
    """Allow either Referee or Mod role."""

    async def predicate(ctx: commands.Context) -> bool:
        if ctx.guild is None:
            return False
        referee_role = discord.utils.get(ctx.guild.roles, name=config.REFEREE_ROLE_NAME)
        mod_role = discord.utils.get(ctx.guild.roles, name=config.MOD_ROLE_NAME)
        if (referee_role and referee_role in ctx.author.roles) or \
                (mod_role and mod_role in ctx.author.roles):
            return True
        raise commands.CheckFailure(
            f"❌ You need the **{config.REFEREE_ROLE_NAME}** or **{config.MOD_ROLE_NAME}** "
            f"role to use this command."
        )

    return check(predicate)


def _is_referee_or_mod(member: discord.Member) -> bool:
    """Return True if member holds the Referee or Mod role."""
    role_names = {r.name for r in member.roles}
    return config.REFEREE_ROLE_NAME in role_names or config.MOD_ROLE_NAME in role_names


async def _resolve_game_name(guild_id: int, raw: str) -> tuple[str | None, bool, str]:
    """
    Resolve a raw game name string against the guild's registered game list.
    Matches on alias (exact) or full_name (case-insensitive).
    Returns (canonical_full_name, can_draw, error_message).
    canonical_full_name is None if not recognised.
    """
    games = await db.get_guild_games(guild_id)

    raw_lower = raw.lower()
    for g in games:
        if g["alias"].lower() == raw_lower or g["full_name"].lower() == raw_lower:
            return g["full_name"], bool(g["can_draw"]), ""

    alias_list = ", ".join(
        f"`{g['alias']}` ({g['full_name']})" for g in games
    )
    return None, False, (
        f"❌ **{raw}** is not a recognised game name.\n"
        f"Accepted aliases: {alias_list}"
    )


def _parse_result(result: str) -> tuple[str | None, str | None, bool, str | None]:
    """
    Parse result string and return (winner_role, loser_role, is_draw, error_message).
    Returns (None, None, False, error_message) if invalid.
    """
    if result == "1-0":
        return "first", "second", False, None
    elif result == "0-1":
        return "second", "first", False, None
    elif result == "draw":
        return None, None, True, None
    else:
        return None, None, False, "❌ Invalid result. Must be `1-0`, `0-1`, or `draw`."


# ════════════════════════════════════════════════════════════
#  MUTUAL CONFIRMATION VIEW  (for non-referee .verify)
# ════════════════════════════════════════════════════════════

class MutualVerifyView(discord.ui.View):
    """
    Shown when a regular player uses .verify.
    Both named players must click Confirm for the result to be recorded.
    Either player can click Deny to cancel.
    """

    def __init__(
        self,
        player1: discord.Member,
        player2: discord.Member,
        *,
        ctx: commands.Context,
        winner: discord.Member | None,
        loser: discord.Member | None,
        is_draw: bool,
        game_name: str,
        can_draw: bool,
        bet: int,
        timeout: float = 120.0,
    ):
        super().__init__(timeout=timeout)
        self.player1    = player1
        self.player2    = player2
        self.ctx        = ctx
        self.winner     = winner
        self.loser      = loser
        self.is_draw    = is_draw
        self.game_name  = game_name
        self.can_draw   = can_draw
        self.bet        = bet
        self.confirmed: set[int] = set()
        self.message: discord.Message | None = None

    def _required_ids(self) -> set[int]:
        return {self.player1.id, self.player2.id}

    def _status(self) -> str:
        lines = []
        for p in (self.player1, self.player2):
            tick = "✅" if p.id in self.confirmed else "⬜"
            lines.append(f"{tick} {p.mention}")
        return "\n".join(lines)

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in self._required_ids():
            await interaction.response.send_message(
                "❌ Only the two players involved can confirm this result.", ephemeral=True
            )
            return
        if interaction.user.id in self.confirmed:
            await interaction.response.send_message(
                "You've already confirmed.", ephemeral=True
            )
            return

        self.confirmed.add(interaction.user.id)

        if self.confirmed >= self._required_ids():
            # Both confirmed — record the result
            self.stop()
            await _record_verified_result(
                ctx=self.ctx,
                winner=self.winner,
                loser=self.loser,
                is_draw=self.is_draw,
                game_name=self.game_name,
                can_draw=self.can_draw,
                bet=self.bet,
                player1=self.player1,
                player2=self.player2,
                verified_by_referee=False,
            )
            settings = await db.get_guild_settings(self.ctx.guild.id)
            emoji = settings["currency_emoji"]
            embed = _build_result_embed(
                self.winner, self.loser, self.is_draw, self.game_name, self.bet, emoji,
                footer="Verified by both players"
            )
            await interaction.response.edit_message(embed=embed, view=None)
        else:
            # One confirmed, waiting for the other
            embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
            embed.set_field_at(
                0,
                name="Confirmations",
                value=self._status(),
                inline=False,
            )
            embed.set_footer(text="Waiting for the other player to confirm…")
            await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in self._required_ids():
            await interaction.response.send_message(
                "❌ Only the two players involved can deny this result.", ephemeral=True
            )
            return
        self.stop()
        embed = discord.Embed(
            title="❌ Result Denied",
            description=f"{interaction.user.mention} denied the result. Nothing was recorded.",
            color=discord.Color.red(),
        )
        await interaction.response.edit_message(embed=embed, view=None)

    async def on_timeout(self):
        if self.message:
            try:
                embed = discord.Embed(
                    title="⏰ Verification Timed Out",
                    description="Both players did not confirm in time. Nothing was recorded.",
                    color=discord.Color.dark_orange(),
                )
                await self.message.edit(embed=embed, view=None)
            except Exception:
                pass


# ════════════════════════════════════════════════════════════
#  SHARED RESULT HELPERS
# ════════════════════════════════════════════════════════════

def _build_result_embed(
    winner: discord.Member | None,
    loser: discord.Member | None,
    is_draw: bool,
    game_name: str,
    bet: int,
    emoji: str,
    *,
    footer: str = "",
) -> discord.Embed:
    if is_draw:
        embed = discord.Embed(
            title=f"Draw Recorded — {game_name}",
            color=discord.Color.light_grey(),
        )
        if winner is None and loser is None:
            pass  # players shown via field set by caller
    else:
        embed = discord.Embed(
            title=f"Result Verified — {game_name}",
            color=discord.Color.green(),
        )
        if winner:
            embed.add_field(name="Winner", value=winner.mention, inline=True)
        if loser:
            embed.add_field(name="Loser",  value=loser.mention,  inline=True)
    if bet > 0:
        label = "Bet (refunded)" if is_draw else "Bet"
        embed.add_field(name=label, value=fmt_currency(bet, emoji), inline=False)
    if footer:
        embed.set_footer(text=footer)
    return embed


async def _record_verified_result(
    *,
    ctx: commands.Context,
    winner: discord.Member | None,
    loser: discord.Member | None,
    is_draw: bool,
    game_name: str,
    can_draw: bool,
    bet: int,
    player1: discord.Member,
    player2: discord.Member,
    verified_by_referee: bool,
) -> None:
    """
    Core logic: deduct/pay bets, write DB row, fire class bonuses.
    Does NOT send any Discord messages — callers handle that.
    """
    settings = await db.get_guild_settings(ctx.guild.id)
    season   = settings["current_season"]

    if is_draw:
        await db.record_result(
            game_name=game_name,
            player1_id=player1.id,
            player2_id=player2.id,
            winner_id=None,
            is_draw=True,
            guild_id=ctx.guild.id,
            season_number=season,
            bet_amount=bet,
            verified_by_referee=verified_by_referee,
        )
        return

    # Win/loss
    if bet > 0 and winner and loser:
        await db.update_balance(loser.id,   ctx.guild.id, -bet)
        await db.update_balance(winner.id,  ctx.guild.id,  bet)

    await db.record_result(
        game_name=game_name,
        player1_id=winner.id if winner else player1.id,
        player2_id=loser.id  if loser  else player2.id,
        winner_id=winner.id if winner else None,
        is_draw=False,
        guild_id=ctx.guild.id,
        season_number=season,
        bet_amount=bet,
        verified_by_referee=verified_by_referee,
    )

    classes_cog = ctx.bot.get_cog("Classes")
    bonuses: dict[int, int] = {}
    if classes_cog and winner and loser:
        bonuses = await classes_cog.on_game_end(
            guild=ctx.guild,
            winner_id=winner.id,
            loser_id=loser.id,
            game_name=game_name,
            bet_amount=bet,
            is_draw=False,
        )

    if bonuses:
        emoji    = settings["currency_emoji"]
        name_p   = settings["currency_name_plural"]
        name_s   = settings["currency_name"]
        lines = []
        for uid, amount in bonuses.items():
            member = ctx.guild.get_member(uid)
            name   = member.mention if member else f"<@{uid}>"
            lines.append(
                f"✨ {name} earned a class bonus of "
                f"{fmt_currency(amount, emoji)}!"
            )
        await ctx.send("\n".join(lines))


# ════════════════════════════════════════════════════════════
#  COG
# ════════════════════════════════════════════════════════════

class Referee(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── .verify ──────────────────────────────────────────────

    @commands.command(name="verify")
    async def verify(
            self,
            ctx: commands.Context,
            player1: discord.Member,
            vs: str,
            player2: discord.Member,
            result: str,
            game_name: str,
            raw_bet: str = "0",
    ):
        """
        Record a game result.
        • Referee/Mod: recorded immediately.
        • Regular player: both players must confirm via buttons.
        Usage: .verify @player1 vs @player2 {result} {game_name} [bet]
        Result: 1-0, 0-1, or draw
        """
        if vs.lower() != "vs":
            await ctx.send("❌ Invalid format. Use: `.verify @player1 vs @player2 {result} {game_name} [bet]`")
            return

        winner_role, loser_role, is_draw, error = _parse_result(result)
        if error:
            await ctx.send(error)
            return

        bet = parse_amount(raw_bet)
        if bet is None or bet < 0:
            await ctx.send("❌ Invalid bet amount.")
            return

        game_name_resolved, game_can_draw, err = await _resolve_game_name(ctx.guild.id, game_name)
        if game_name_resolved is None:
            await ctx.send(err)
            return

        settings = await db.get_guild_settings(ctx.guild.id)
        emoji    = settings["currency_emoji"]

        # ── Shared validation (all callers) ───────────────────
        if is_draw and not game_can_draw:
            await ctx.send(
                f"❌ **{game_name_resolved}** cannot end in a draw. "
                f"Use `1-0` or `0-1` to record the result."
            )
            return

        winner = player1 if winner_role == "first" else (player2 if winner_role == "second" else None)
        loser  = player2 if winner_role == "first" else (player1 if winner_role == "second" else None)

        # Player existence checks
        for p in (player1, player2):
            rec = await db.get_player(p.id, ctx.guild.id)
            if rec is None:
                await ctx.send(f"❌ {p.mention} hasn't joined the season.")
                return

        if player1 == player2:
            await ctx.send("❌ Players can't be the same.")
            return

        if not is_draw and bet > 0 and loser:
            loser_rec = await db.get_player(loser.id, ctx.guild.id)
            if loser_rec["balance"] < bet:
                await ctx.send(
                    f"❌ {loser.mention} doesn't have enough to cover the bet of "
                    f"{fmt_currency(bet, emoji)}."
                )
                return

        # ── Referee / Mod path — immediate ───────────────────
        if _is_referee_or_mod(ctx.author):
            await _record_verified_result(
                ctx=ctx,
                winner=winner,
                loser=loser,
                is_draw=is_draw,
                game_name=game_name_resolved,
                can_draw=game_can_draw,
                bet=bet,
                player1=player1,
                player2=player2,
                verified_by_referee=True,
            )
            embed = _build_result_embed(
                winner, loser, is_draw, game_name_resolved, bet, emoji,
                footer=f"Verified by {ctx.author.display_name}"
            )
            if is_draw:
                embed.add_field(
                    name="Players",
                    value=f"{player1.mention} vs {player2.mention}",
                    inline=False,
                )
            await ctx.send(embed=embed)
            return

        # ── Regular player path — mutual confirmation ─────────
        if ctx.author != player1 and ctx.author != player2:
            await ctx.send("❌ You can't verify a game that wasn't played by you.")

        if is_draw:
            result_desc = "**Draw**"
        else:
            result_desc = f"**{winner.mention}** wins vs **{loser.mention}**"

        bet_str = f"\n**Bet:** {fmt_currency(bet, emoji)}" if bet > 0 else ""

        confirm_embed = discord.Embed(
            title=f"📋 Result Verification — {game_name_resolved}",
            description=(
                f"{ctx.author.mention} is requesting to record a result.\n\n"
                f"**Result:** {result_desc}{bet_str}\n\n"
                f"Both players must confirm for this to be recorded."
            ),
            color=discord.Color.blurple(),
        )
        confirm_embed.add_field(
            name="Confirmations",
            value=f"⬜ {player1.mention}\n⬜ {player2.mention}",
            inline=False,
        )
        confirm_embed.set_footer(text="Expires in 2 minutes")

        view = MutualVerifyView(
            player1=player1,
            player2=player2,
            ctx=ctx,
            winner=winner,
            loser=loser,
            is_draw=is_draw,
            game_name=game_name_resolved,
            can_draw=game_can_draw,
            bet=bet,
        )
        msg = await ctx.send(
            content=f"{player1.mention} {player2.mention}",
            embed=confirm_embed,
            view=view,
        )
        view.message = msg

    # ── .erase-result ─────────────────────────────────────────

    @commands.command(name="erase-result")
    @has_referee_or_mod_role()
    async def erase_result(
            self,
            ctx: commands.Context,
            player1: discord.Member,
            vs: str,
            player2: discord.Member,
            result: str,
            game_name: str,
            raw_bet: str = "0",
    ):
        """
        Remove the most recent result matching these parameters from the database.
        Usage: .erase-result @player1 vs @player2 {result} {game_name} [bet]
        Result must be: 1-0, 0-1, or draw
        If no matching result is found, an error is returned.
        """
        if vs.lower() != "vs":
            await ctx.send("❌ Invalid format. Use: `.erase-result @player1 vs @player2 {result} {game_name} [bet]`")
            return

        winner_role, loser_role, is_draw, error = _parse_result(result)
        if error:
            await ctx.send(error)
            return

        bet = parse_amount(raw_bet)
        if bet is None or bet < 0:
            await ctx.send("❌ Invalid bet amount.")
            return

        game_name_resolved, _game_can_draw, err = await _resolve_game_name(ctx.guild.id, game_name)
        if game_name_resolved is None:
            await ctx.send(err)
            return

        settings = await db.get_guild_settings(ctx.guild.id)
        emoji = settings["currency_emoji"]

        # ── Draw path ─────────────────────────────────────────
        if is_draw:
            deleted = await db.erase_draw_result(
                player1_id=player1.id,
                player2_id=player2.id,
                game_name=game_name_resolved,
                guild_id=ctx.guild.id,
                bet_amount=bet,
            )

            if not deleted:
                await ctx.send(
                    f"❌ No draw result found for {player1.mention} vs {player2.mention} "
                    f"in **{game_name_resolved}**"
                    + (f" with a bet of {fmt_currency(bet, emoji)}" if bet > 0 else "")
                    + "."
                )
                return

            embed = discord.Embed(
                title=f"Draw Result Erased — {game_name_resolved}",
                color=discord.Color.light_grey(),
            )
            embed.add_field(name="Players", value=f"{player1.mention} vs {player2.mention}", inline=False)
            if bet > 0:
                embed.add_field(name="Bet", value=fmt_currency(bet, emoji), inline=False)
            embed.set_footer(text=f"Erased by {ctx.author.display_name}")
            await ctx.send(embed=embed)
            return

        # ── Win/loss path ─────────────────────────────────────
        winner = player1 if winner_role == "first" else player2
        loser  = player2 if winner_role == "first" else player1

        deleted = await db.erase_result(
            winner_id=winner.id,
            loser_id=loser.id,
            game_name=game_name_resolved,
            guild_id=ctx.guild.id,
            bet_amount=bet,
        )

        if not deleted:
            await ctx.send(
                f"❌ No result found for {winner.mention} beating {loser.mention} "
                f"in **{game_name_resolved}**"
                + (f" with a bet of {fmt_currency(bet, emoji)}" if bet > 0 else "")
                + "."
            )
            return

        embed = discord.Embed(
            title=f"Result Erased — {game_name_resolved}",
            color=discord.Color.red(),
        )
        embed.add_field(name="Winner (erased)", value=winner.mention, inline=True)
        embed.add_field(name="Loser (erased)",  value=loser.mention,  inline=True)
        if bet > 0:
            embed.add_field(name="Bet", value=fmt_currency(bet, emoji), inline=False)
        embed.set_footer(text=f"Erased by {ctx.author.display_name}")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Referee(bot))