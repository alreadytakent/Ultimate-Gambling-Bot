# ============================================================
#  cogs/score.py — Score tracking
#
#  Commands:
#    .score                    — your score
#    .score @player            — another player's score
#    .score @p1 vs @p2         — head-to-head between two players
# ============================================================

import discord
from discord.ext import commands
from typing import Optional

import database as db
from cogs.utils import channel_only


async def _build_score_table(rows: list[dict], guild_id: int) -> str:
    """Format {game_name, wins, draws, losses} rows into a table string.
    Uses the guild's registered game list to determine drawability per game.
    """
    if not rows:
        return "No games played."

    guild_games = await db.get_guild_games(guild_id)
    can_draw_map: dict[str, bool] = {
        g["full_name"].lower(): bool(g["can_draw"]) for g in guild_games
    }

    rows = sorted(rows, key=lambda r: r["wins"] + r["draws"] + r["losses"], reverse=True)

    name_col = 22
    lines = [f"{'Game':<{name_col}} {'W':>4} {'D':>4} {'L':>4}"]
    lines.append("─" * (name_col + 15))
    for r in rows:
        name = r["game_name"]
        if len(name) > name_col:
            name = name[:name_col - 1] + "…"
        can_draw = can_draw_map.get(r["game_name"].lower(), True)
        draws_str = f"{r['draws']:>4}" if can_draw else f"{'-':>4}"
        lines.append(f"{name:<{name_col}} {r['wins']:>4} {draws_str} {r['losses']:>4}")

    total_w = sum(r["wins"] for r in rows)
    total_d = sum(r["draws"] for r in rows)
    total_l = sum(r["losses"] for r in rows)
    lines.append("─" * (name_col + 15))
    lines.append(f"{'TOTAL':<{name_col}} {total_w:>4} {total_d:>4} {total_l:>4}")
    return "\n".join(lines)


class Score(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="score")
    @commands.guild_only()
    @channel_only("general")
    async def score(self, ctx: commands.Context, *, args: Optional[str] = None):
        """
        Show your score or a head-to-head comparison.

        Usage:
          .score                — your score
          .score @player        — another player's score
          .score @p1 vs @p2     — head-to-head between two players
        """
        mentions = ctx.message.mentions

        if args and " vs " in args.lower() and len(mentions) == 2:
            await self._head_to_head(ctx, mentions[0], mentions[1])
            return

        target = mentions[0] if mentions else ctx.author
        rows = await db.get_score_overall(target.id, ctx.guild.id)
        table = await _build_score_table(rows, ctx.guild.id)
        await ctx.send(f"📊 **{target.display_name}'s Score**\n```\n{table}\n```")

    async def _head_to_head(self, ctx: commands.Context,
                            p1: discord.Member, p2: discord.Member):
        rows = await db.get_head_to_head(p1.id, p2.id, ctx.guild.id)
        if not rows:
            await ctx.send(
                f"⚔️ **{p1.display_name} vs {p2.display_name}**\n"
                f"*These two players have never played each other.*"
            )
            return

        table = await _build_score_table(rows, ctx.guild.id)
        await ctx.send(
            f"⚔️ **{p1.display_name} vs {p2.display_name}** — W/D/L from {p1.display_name}'s perspective\n"
            f"```\n{table}\n```"
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Score(bot))
