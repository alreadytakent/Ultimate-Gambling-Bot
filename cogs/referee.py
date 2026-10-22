# ============================================================
#  cogs/referee.py — Referee (and Mod) commands
#
#  Commands:
#    .verify @player1 vs @player2 {result} {game_name}       — record a result
#    .erase-result @player1 vs @player2 {result} {game_name} — remove most recent matching result
#
#  Result: 1-0, 0-1 or draw.  game_name: alias or full name.
# ============================================================

import uuid

import discord
from discord.ext import commands

import database as db
from cogs.utils import has_referee_or_mod_role, is_referee_or_mod


async def _resolve_game_name(guild_id: int, raw: str) -> tuple[str | None, bool, str]:
    """Returns (canonical_full_name, can_draw, error_message)."""
    games = await db.get_guild_games(guild_id)
    raw_lower = raw.lower()
    for g in games:
        if g["alias"].lower() == raw_lower or g["full_name"].lower() == raw_lower:
            return g["full_name"], bool(g["can_draw"]), ""

    alias_list = ", ".join(f"`{g['alias']}` ({g['full_name']})" for g in games)
    return None, False, (
        f"❌ **{raw}** is not a recognised game name.\n"
        f"Accepted aliases: {alias_list}"
    )


def _parse_result(result: str) -> tuple[str | None, str | None, bool, str | None]:
    """Returns (winner_role, loser_role, is_draw, error_message)."""
    if result == "1-0":
        return "first", "second", False, None
    elif result == "0-1":
        return "second", "first", False, None
    elif result.lower() == "draw":
        return None, None, True, None
    return None, None, False, "❌ Invalid result. Must be `1-0`, `0-1`, or `draw`."


# ════════════════════════════════════════════════════════════
#  MUTUAL CONFIRMATION VIEW  (for non-referee .verify)
# ════════════════════════════════════════════════════════════

class MutualVerifyView(discord.ui.View):
    """Both named players must click Confirm. Either can click Deny."""

    def __init__(
        self,
        player1: discord.Member,
        player2: discord.Member,
        *,
        guild_id: int,
        winner: discord.Member | None,
        loser: discord.Member | None,
        is_draw: bool,
        game_name: str,
        timeout: float = 120.0,
    ):
        super().__init__(timeout=timeout)
        self.player1 = player1
        self.player2 = player2
        self.guild_id = guild_id
        self.winner = winner
        self.loser = loser
        self.is_draw = is_draw
        self.game_name = game_name
        self.confirmed: set[int] = set()
        self.message: discord.Message | None = None

    def _required_ids(self) -> set[int]:
        return {self.player1.id, self.player2.id}

    def _status(self) -> str:
        return "\n".join(
            f"{'✅' if p.id in self.confirmed else '⬜'} {p.mention}"
            for p in (self.player1, self.player2)
        )

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in self._required_ids():
            await interaction.response.send_message(
                "❌ Only the two players involved can confirm this result.", ephemeral=True)
            return
        if interaction.user.id in self.confirmed:
            await interaction.response.send_message("You've already confirmed.", ephemeral=True)
            return

        self.confirmed.add(interaction.user.id)

        if self.confirmed >= self._required_ids():
            self.stop()
            await _record_verified_result(
                guild_id=self.guild_id, winner=self.winner, loser=self.loser,
                is_draw=self.is_draw, game_name=self.game_name,
                player1=self.player1, player2=self.player2,
            )
            embed = _build_result_embed(
                self.winner, self.loser, self.is_draw, self.game_name,
                self.player1, self.player2, footer="Verified by both players",
            )
            await interaction.response.edit_message(embed=embed, view=None)
        else:
            embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
            embed.set_field_at(0, name="Confirmations", value=self._status(), inline=False)
            embed.set_footer(text="Waiting for the other player to confirm…")
            await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in self._required_ids():
            await interaction.response.send_message(
                "❌ Only the two players involved can deny this result.", ephemeral=True)
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
    winner, loser, is_draw: bool, game_name: str,
    player1: discord.Member, player2: discord.Member, *, footer: str = "",
) -> discord.Embed:
    if is_draw:
        embed = discord.Embed(title=f"Draw Recorded — {game_name}", color=discord.Color.light_grey())
        embed.add_field(name="Players", value=f"{player1.mention} vs {player2.mention}", inline=False)
    else:
        embed = discord.Embed(title=f"Result Verified — {game_name}", color=discord.Color.green())
        embed.add_field(name="Winner", value=winner.mention, inline=True)
        embed.add_field(name="Loser", value=loser.mention, inline=True)
    if footer:
        embed.set_footer(text=footer)
    return embed


async def _record_verified_result(
    *, guild_id: int, winner, loser, is_draw: bool, game_name: str,
    player1: discord.Member, player2: discord.Member,
) -> None:
    """Write the result to the server's score database. Sends no messages."""
    match_id = uuid.uuid4().hex
    if is_draw:
        await db.record_result(guild_id, game_name, player1.id, player2.id, None, True, match_id)
    else:
        await db.record_result(guild_id, game_name, winner.id, loser.id, winner.id, False, match_id)


# ════════════════════════════════════════════════════════════
#  COG
# ════════════════════════════════════════════════════════════

class Referee(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="verify")
    @commands.guild_only()
    async def verify(
        self,
        ctx: commands.Context,
        player1: discord.Member,
        vs: str,
        player2: discord.Member,
        result: str,
        *,
        game_name: str,
    ):
        """
        Record a game result.
        • Referee/Mod: recorded immediately.
        • Regular player: both players must confirm via buttons.
        Usage: .verify @player1 vs @player2 {result} {game_name}
        Result: 1-0, 0-1, or draw
        """
        if vs.lower() != "vs":
            await ctx.send("❌ Invalid format. Use: `.verify @player1 vs @player2 {result} {game_name}`")
            return

        winner_role, _loser_role, is_draw, error = _parse_result(result)
        if error:
            await ctx.send(error)
            return

        resolved, can_draw, err = await _resolve_game_name(ctx.guild.id, game_name)
        if resolved is None:
            await ctx.send(err)
            return

        if is_draw and not can_draw:
            await ctx.send(
                f"❌ **{resolved}** cannot end in a draw. "
                f"Use `1-0` or `0-1` to record the result."
            )
            return

        if player1 == player2:
            await ctx.send("❌ Players can't be the same.")
            return
        if player1.bot or player2.bot:
            await ctx.send("❌ Bots can't play games.")
            return

        winner = player1 if winner_role == "first" else (player2 if winner_role == "second" else None)
        loser  = player2 if winner_role == "first" else (player1 if winner_role == "second" else None)

        # ── Referee / Mod path — immediate ───────────────────
        if is_referee_or_mod(ctx.author):
            await _record_verified_result(
                guild_id=ctx.guild.id, winner=winner, loser=loser, is_draw=is_draw,
                game_name=resolved, player1=player1, player2=player2,
            )
            await ctx.send(embed=_build_result_embed(
                winner, loser, is_draw, resolved, player1, player2,
                footer=f"Verified by {ctx.author.display_name}",
            ))
            return

        # ── Regular player path — mutual confirmation ────────
        if ctx.author != player1 and ctx.author != player2:
            await ctx.send("❌ You can't verify a game that wasn't played by you.")
            return

        result_desc = "**Draw**" if is_draw else f"**{winner.mention}** wins vs **{loser.mention}**"
        confirm_embed = discord.Embed(
            title=f"📋 Result Verification — {resolved}",
            description=(
                f"{ctx.author.mention} is requesting to record a result.\n\n"
                f"**Result:** {result_desc}\n\n"
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
            player1=player1, player2=player2, guild_id=ctx.guild.id,
            winner=winner, loser=loser, is_draw=is_draw, game_name=resolved,
        )
        msg = await ctx.send(content=f"{player1.mention} {player2.mention}",
                             embed=confirm_embed, view=view)
        view.message = msg

    @commands.command(name="erase-result")
    @commands.guild_only()
    @has_referee_or_mod_role()
    async def erase_result(
        self,
        ctx: commands.Context,
        player1: discord.Member,
        vs: str,
        player2: discord.Member,
        result: str,
        *,
        game_name: str,
    ):
        """
        Remove the most recent result matching these parameters from the database.
        Usage: .erase-result @player1 vs @player2 {result} {game_name}
        Result must be: 1-0, 0-1, or draw
        """
        if vs.lower() != "vs":
            await ctx.send("❌ Invalid format. Use: `.erase-result @player1 vs @player2 {result} {game_name}`")
            return

        winner_role, _loser_role, is_draw, error = _parse_result(result)
        if error:
            await ctx.send(error)
            return

        resolved, _can_draw, err = await _resolve_game_name(ctx.guild.id, game_name)
        if resolved is None:
            await ctx.send(err)
            return

        # ── Draw path ────────────────────────────────────────
        if is_draw:
            deleted = await db.erase_draw_result(ctx.guild.id, player1.id, player2.id, resolved)
            if not deleted:
                await ctx.send(
                    f"❌ No draw result found for {player1.mention} vs {player2.mention} "
                    f"in **{resolved}**."
                )
                return
            embed = discord.Embed(title=f"Draw Result Erased — {resolved}",
                                  color=discord.Color.light_grey())
            embed.add_field(name="Players", value=f"{player1.mention} vs {player2.mention}", inline=False)
            embed.set_footer(text=f"Erased by {ctx.author.display_name}")
            await ctx.send(embed=embed)
            return

        # ── Win/loss path ────────────────────────────────────
        winner = player1 if winner_role == "first" else player2
        loser  = player2 if winner_role == "first" else player1

        deleted = await db.erase_result(ctx.guild.id, winner.id, loser.id, resolved)
        if not deleted:
            await ctx.send(
                f"❌ No result found for {winner.mention} beating {loser.mention} "
                f"in **{resolved}**."
            )
            return

        embed = discord.Embed(title=f"Result Erased — {resolved}", color=discord.Color.red())
        embed.add_field(name="Winner (erased)", value=winner.mention, inline=True)
        embed.add_field(name="Loser (erased)",  value=loser.mention,  inline=True)
        embed.set_footer(text=f"Erased by {ctx.author.display_name}")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Referee(bot))
