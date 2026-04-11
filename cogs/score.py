# ============================================================
#  cogs/score.py — Score tracking (paginated, per-season)
#
#  Commands:
#    .score                    — your overall + per-season score
#    .score @p1 vs @p2         — head-to-head between two players
# ============================================================

import discord
from discord.ext import commands
from typing import Optional

import database as db
from cogs.utils import require_player, PaginatedView


async def _build_score_table(rows: list[dict], guild_id: int) -> str:
    """Format a list of {game_name, wins, draws, losses} into a table string.
    Uses the guild's registered game list to determine drawability per game.
    """
    if not rows:
        return "*No games played.*"

    # Build a lookup: full_name (lower) → can_draw from the guild's game list
    guild_games = await db.get_guild_games(guild_id)
    can_draw_map: dict[str, bool] = {
        g["full_name"].lower(): bool(g["can_draw"]) for g in guild_games
    }

    # Sort by total games descending
    rows = sorted(rows, key=lambda r: r["wins"] + r["draws"] + r["losses"], reverse=True)

    name_col = 22
    lines = [f"{'Game':<{name_col}} {'W':>4} {'D':>4} {'L':>4}"]
    lines.append("─" * (name_col + 15))
    for r in rows:
        name = r["game_name"]
        if len(name) > name_col:
            name = name[:name_col - 1] + "…"
        # Show "-" for draws in games that cannot draw.
        # Unknown games (not in guild list) default to showing the number.
        can_draw = can_draw_map.get(r["game_name"].lower(), True)
        draws_str = f"{r['draws']:>4}" if can_draw else f"{'-':>4}"
        lines.append(
            f"{name:<{name_col}} {r['wins']:>4} {draws_str} {r['losses']:>4}"
        )

    total_w = sum(r["wins"] for r in rows)
    total_d = sum(r["draws"] for r in rows)
    total_l = sum(r["losses"] for r in rows)
    lines.append("─" * (name_col + 15))
    lines.append(f"{'TOTAL':<{name_col}} {total_w:>4} {total_d:>4} {total_l:>4}")

    return "```\n" + "\n".join(lines) + "\n```"


async def _build_h2h_table(rows: list[dict], guild_id: int) -> str:
    """Format head-to-head rows, respecting per-guild drawability."""
    guild_games = await db.get_guild_games(guild_id)
    can_draw_map: dict[str, bool] = {
        g["full_name"].lower(): bool(g["can_draw"]) for g in guild_games
    }

    name_col = 22
    lines = [f"{'Game':<{name_col}} {'W':>4} {'D':>4} {'L':>4}"]
    lines.append("─" * (name_col + 15))
    for r in rows:
        name = r["game_name"]
        if len(name) > name_col:
            name = name[:name_col - 1] + "…"
        can_draw = can_draw_map.get(r["game_name"].lower(), True)
        draws_str = f"{r['draws']:>4}" if can_draw else f"{'-':>4}"
        lines.append(
            f"{name:<{name_col}} {r['wins']:>4} {draws_str} {r['losses']:>4}"
        )
    total_w = sum(r["wins"] for r in rows)
    total_d = sum(r["draws"] for r in rows)
    total_l = sum(r["losses"] for r in rows)
    lines.append("─" * (name_col + 15))
    lines.append(f"{'TOTAL':<{name_col}} {total_w:>4} {total_d:>4} {total_l:>4}")
    return "```\n" + "\n".join(lines) + "\n```"


class Score(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="score")
    async def score(self, ctx: commands.Context, *, args: Optional[str] = None):
        """
        Show your score or a head-to-head comparison.

        Usage:
          .score                — your full score (all seasons, paginated)
          .score @player        — another player's score
          .score @p1 vs @p2    — head-to-head between two players
        """
        # ── Parse args ──────────────────────────────────────
        mentions = ctx.message.mentions

        # Head-to-head: .score @p1 vs @p2
        if args and " vs " in args.lower() and len(mentions) == 2:
            await self._head_to_head(ctx, mentions[0], mentions[1])
            return

        # Single player (or self)
        target = mentions[0] if mentions else ctx.author
        await self._player_score(ctx, target)

    # ── Per-player paginated score ───────────────────────────

    async def _player_score(self, ctx: commands.Context, member: discord.Member):
        player = await db.get_player(member.id, ctx.guild.id)
        if player is None:
            who = "You haven't" if member == ctx.author else f"{member.display_name} hasn't"
            await ctx.send(f"❌ {who} joined the season yet.")
            return

        # Most-recent season first
        seasons_with_data = list(reversed(await db.get_seasons_played(ctx.guild.id)))
        total_pages = len(seasons_with_data) + 1

        pages: list[discord.Embed] = []

        # Page 1: Overall
        overall = await db.get_score_overall(member.id, ctx.guild.id)
        page1 = discord.Embed(
            title=f"📊 {member.display_name}'s Score — Overall",
            description=await _build_score_table(overall, ctx.guild.id),
            color=discord.Color.blurple(),
        )
        page1.set_footer(text=f"Page 1 / {total_pages} — use ◀ ▶ to navigate seasons")
        pages.append(page1)

        # One page per season, most recent first
        for i, season_num in enumerate(seasons_with_data, start=2):
            season_rows = await db.get_score_by_season(member.id, ctx.guild.id, season_num)
            page = discord.Embed(
                title=f"📊 {member.display_name}'s Score — Season {season_num}",
                description=await _build_score_table(season_rows, ctx.guild.id),
                color=discord.Color.blurple(),
            )
            page.set_footer(text=f"Page {i} / {total_pages} — use ◀ ▶ to navigate seasons")
            pages.append(page)

        if len(pages) == 1:
            await ctx.send(embed=pages[0])
        else:
            view = PaginatedView(pages, author_id=ctx.author.id)
            await ctx.send(embed=pages[0], view=view)

    # ── Head-to-head ─────────────────────────────────────────

    async def _head_to_head(self, ctx: commands.Context,
                             p1: discord.Member, p2: discord.Member):
        rows = await db.get_head_to_head(p1.id, p2.id, ctx.guild.id)

        embed = discord.Embed(
            title=f"⚔️ Head-to-Head: {p1.display_name} vs {p2.display_name}",
            color=discord.Color.red(),
        )

        if not rows:
            embed.description = "*These two players have never played each other.*"
        else:
            embed.description = await _build_h2h_table(rows, ctx.guild.id)
            embed.set_footer(
                text=f"W/D/L from {p1.display_name}'s perspective | All seasons"
            )

        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Score(bot))