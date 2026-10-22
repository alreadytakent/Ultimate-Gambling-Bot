# ============================================================
#  cogs/admin.py — Moderator commands
#
#  All commands require the Mod role (config.MOD_ROLE_NAME).
#
#  Commands:
#    .prefix {symbol}
#    .set-channel {category} #channel   (or "none" to remove)
#    .scores-reset                      — double confirmation
#    .add-game "{full name}" {alias} {drawable}
#    .remove-game "{full name}" {alias} {drawable}
#    .list-games
# ============================================================

import discord
from discord.ext import commands

import database as db
from cogs.utils import has_mod_role, ConfirmView


class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ════════════════════════════════════════════════════════
    #  SERVER SETTINGS
    # ════════════════════════════════════════════════════════

    @commands.command(name="prefix")
    @has_mod_role()
    async def prefix(self, ctx: commands.Context, new_prefix: str):
        """Change the bot's command prefix for this server."""
        if len(new_prefix) > 5:
            await ctx.send("❌ Prefix must be 5 characters or fewer.")
            return
        await db.set_guild_setting(ctx.guild.id, "prefix", new_prefix)
        await ctx.send(f"✅ Prefix changed to `{new_prefix}`")

    @commands.command(name="set-channel")
    @has_mod_role()
    async def set_channel(self, ctx: commands.Context, category: str, channel: str):
        """
        Restrict a command category to a specific channel.
        Categories: games (game commands), general (.score)
        Use 'none' as the channel to remove a restriction.
        """
        valid = {"games", "general"}
        category = category.lower()
        if category not in valid:
            await ctx.send(f"❌ Unknown category. Valid categories: `{', '.join(sorted(valid))}`")
            return

        if channel.lower() == "none":
            await db.clear_channel_restriction(ctx.guild.id, category)
            await ctx.send(f"✅ `{category}` commands are no longer restricted to a channel.")
            return

        try:
            text_channel = await commands.TextChannelConverter().convert(ctx, channel)
        except commands.BadArgument:
            await ctx.send("❌ Channel not found. Mention a channel or use `none`.")
            return

        await db.set_channel_restriction(ctx.guild.id, category, text_channel.id)
        await ctx.send(f"✅ `{category}` commands restricted to {text_channel.mention}.")

    # ════════════════════════════════════════════════════════
    #  SCORES RESET
    # ════════════════════════════════════════════════════════

    @commands.command(name="scores-reset")
    @has_mod_role()
    async def scores_reset(self, ctx: commands.Context):
        """Erase ALL scores in this server. Requires double confirmation from the Mod who used the command."""
        view1 = ConfirmView({ctx.author.id}, confirm_label="Yes, continue",
                            cancel_label="Cancel", timeout=30)
        view1.message = await ctx.send(
            "⚠️ This will erase **all scores** in this server. Do you want to continue? (1/2)",
            view=view1,
        )
        await view1.wait()
        if view1.value is not True:
            await ctx.send("❎ Scores reset cancelled.")
            return

        view2 = ConfirmView({ctx.author.id}, confirm_label="ERASE ALL SCORES",
                            cancel_label="Cancel", timeout=30)
        view2.message = await ctx.send(
            "🛑 **Final confirmation (2/2).** This cannot be undone. Erase every score?",
            view=view2,
        )
        await view2.wait()
        if view2.value is not True:
            await ctx.send("❎ Scores reset cancelled.")
            return

        deleted = await db.reset_scores(ctx.guild.id)
        await ctx.send(f"🧹 All scores have been erased ({deleted} record(s) deleted).")

    # ════════════════════════════════════════════════════════
    #  GAME LIST
    # ════════════════════════════════════════════════════════

    @commands.command(name="add-game")
    @has_mod_role()
    async def add_game(self, ctx: commands.Context, full_name: str, alias: str, drawable: str):
        """
        Add a game to this server's accepted game list (used by .verify / .erase-result).
        Usage: .add-game "{full game name}" {alias} {drawable}
        drawable must be 1 (draws possible) or 0 (no draws).
        Wrap multi-word names in quotes: .add-game "My Custom Game" mycg 0
        """
        if drawable not in ("0", "1"):
            await ctx.send("❌ `drawable` must be `1` (draws possible) or `0` (no draws).")
            return

        can_draw = drawable == "1"
        added = await db.add_guild_game(ctx.guild.id, full_name, alias.lower(), can_draw)
        if not added:
            await ctx.send(
                f"❌ A game with alias `{alias.lower()}` already exists in this server's game list."
            )
            return

        draw_str = "draws possible" if can_draw else "no draws"
        await ctx.send(
            f"✅ **{full_name}** (`{alias.lower()}`, {draw_str}) added to the accepted game list."
        )

    @commands.command(name="remove-game")
    @has_mod_role()
    async def remove_game(self, ctx: commands.Context, full_name: str, alias: str, drawable: str):
        """
        Remove a game from this server's accepted game list.
        Usage: .remove-game "{full game name}" {alias} {drawable}
        All three parameters must match an existing entry exactly.
        """
        if drawable not in ("0", "1"):
            await ctx.send("❌ `drawable` must be `1` (draws possible) or `0` (no draws).")
            return

        can_draw = drawable == "1"
        removed = await db.remove_guild_game(ctx.guild.id, full_name, alias.lower(), can_draw)
        if not removed:
            await ctx.send(
                f"❌ No game matching full name **{full_name}**, alias `{alias.lower()}`, "
                f"drawable={drawable} was found in this server's game list."
            )
            return

        await ctx.send(f"✅ **{full_name}** (`{alias.lower()}`) removed from the accepted game list.")

    @commands.command(name="list-games")
    @has_mod_role()
    async def list_games(self, ctx: commands.Context):
        """Show all games currently accepted by .verify / .erase-result for this server."""
        games = await db.get_guild_games(ctx.guild.id)
        if not games:
            await ctx.send("📭 No games are registered for this server.")
            return

        lines = []
        for g in games:
            draw_str = "draws ✅" if g["can_draw"] else "no draws ❌"
            lines.append(f"• **{g['full_name']}** — alias `{g['alias']}` — {draw_str}")

        embed = discord.Embed(
            title="🎮 Accepted Games",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Use .add-game / .remove-game to modify this list.")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Admin(bot))
