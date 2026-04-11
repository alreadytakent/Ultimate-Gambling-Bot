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


class Referee(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── .verify ──────────────────────────────────────────────

    @commands.command(name="verify")
    @has_referee_or_mod_role()
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
        Record a manually verified game result.
        Usage: .verify @player1 vs @player2 {result} {game_name} [bet]
        Result must be: 1-0, 0-1, or draw
        Use .verify without args or check .add-game for accepted game aliases.
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
        season = settings["current_season"]
        emoji = settings["currency_emoji"]

        # ── Draw path ─────────────────────────────────────────
        if is_draw:
            # Hard block: draws are not possible in this game
            if not game_can_draw:
                await ctx.send(
                    f"❌ **{game_name_resolved}** cannot end in a draw. "
                    f"Use `1-0` or `0-1` to record the result."
                )
                return

            p1_player = await db.get_player(player1.id, ctx.guild.id)
            p2_player = await db.get_player(player2.id, ctx.guild.id)

            if p1_player is None:
                await ctx.send(f"❌ {player1.mention} hasn't joined the season.")
                return
            if p2_player is None:
                await ctx.send(f"❌ {player2.mention} hasn't joined the season.")
                return
            if player1 == player2:
                await ctx.send("❌ Players can't be the same.")
                return

            await db.record_result(
                game_name=game_name_resolved,
                player1_id=player1.id,
                player2_id=player2.id,
                winner_id=None,
                is_draw=True,
                guild_id=ctx.guild.id,
                season_number=season,
                bet_amount=bet,
                verified_by_referee=True,
            )

            embed = discord.Embed(
                title=f"Draw Recorded — {game_name_resolved}",
                color=discord.Color.light_grey(),
            )
            embed.add_field(name="Players", value=f"{player1.mention} vs {player2.mention}", inline=False)
            if bet > 0:
                embed.add_field(name="Bet (refunded)", value=fmt_currency(bet, emoji), inline=False)
            embed.set_footer(text=f"Verified by {ctx.author.display_name}")
            await ctx.send(embed=embed)
            return

        # ── Win/loss path ─────────────────────────────────────
        winner = player1 if winner_role == "first" else player2
        loser  = player2 if winner_role == "first" else player1

        winner_player = await db.get_player(winner.id, ctx.guild.id)
        loser_player  = await db.get_player(loser.id,  ctx.guild.id)

        if winner_player is None:
            await ctx.send(f"❌ {winner.mention} hasn't joined the season.")
            return
        if loser_player is None:
            await ctx.send(f"❌ {loser.mention} hasn't joined the season.")
            return
        if winner.id == loser.id:
            await ctx.send("❌ Winner and loser can't be the same player.")
            return

        if bet > 0:
            if loser_player["balance"] < bet:
                await ctx.send(
                    f"❌ {loser.mention} doesn't have enough to cover the bet of "
                    f"{fmt_currency(bet, emoji)}."
                )
                return
            await db.update_balance(loser.id,   ctx.guild.id, -bet)
            await db.update_balance(winner.id,  ctx.guild.id,  bet)

        await db.record_result(
            game_name=game_name_resolved,
            player1_id=winner.id,
            player2_id=loser.id,
            winner_id=winner.id,
            is_draw=False,
            guild_id=ctx.guild.id,
            season_number=season,
            bet_amount=bet,
            verified_by_referee=True,
        )

        classes_cog = self.bot.get_cog("Classes")
        bonuses = {}
        if classes_cog:
            bonuses = await classes_cog.on_game_end(
                guild=ctx.guild,
                winner_id=winner.id,
                loser_id=loser.id,
                game_name=game_name_resolved,
                bet_amount=bet,
                is_draw=False,
            )

        embed = discord.Embed(
            title=f"Result Verified — {game_name_resolved}",
            color=discord.Color.green(),
        )
        embed.add_field(name="Winner", value=winner.mention, inline=True)
        embed.add_field(name="Loser",  value=loser.mention,  inline=True)
        if bet > 0:
            embed.add_field(name="Bet", value=fmt_currency(bet, emoji), inline=False)
        embed.set_footer(text=f"Verified by {ctx.author.display_name}")
        await ctx.send(embed=embed)

        if bonuses:
            bonus_lines = []
            for uid, amount in bonuses.items():
                member = ctx.guild.get_member(uid)
                name = member.mention if member else f"<@{uid}>"
                bonus_lines.append(f"{name} earned a class bonus of {fmt_currency(amount, emoji)}!")
            await ctx.send("\n".join(bonus_lines))

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