# ============================================================
#  cogs/help.py — Custom help command
# ============================================================

import discord
from discord.ext import commands

import config
from cogs.utils import is_mod, is_referee_or_mod


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="help")
    async def help(self, ctx: commands.Context, *, topic: str = None):
        """Show help. Use .help {command} for details on a specific command."""
        prefix = ctx.prefix or config.DEFAULT_PREFIX

        if topic:
            cmd = self.bot.get_command(topic)
            if cmd is None:
                await ctx.send(f"❌ Unknown command: `{topic}`")
                return
            embed = discord.Embed(
                title=f"Help: {prefix}{cmd.name}",
                description=cmd.help or "*No description.*",
                color=discord.Color.blurple(),
            )
            if cmd.aliases:
                embed.add_field(name="Aliases", value=", ".join(f"`{a}`" for a in cmd.aliases))
            await ctx.send(embed=embed)
            return

        member_is_mod = member_is_ref = False
        if ctx.guild:
            member_is_mod = is_mod(ctx.author)
            member_is_ref = is_referee_or_mod(ctx.author)

        embed = discord.Embed(
            title="📖 UltimateBot Commands",
            description=f"Use `{prefix}help <command>` for details on any command.",
            color=discord.Color.blurple(),
        )

        embed.add_field(name="🎮 Games", value=(
            f"`{prefix}dth` `{prefix}mdth` `{prefix}gops` `{prefix}dotty` "
            f"`{prefix}comb` `{prefix}airpoker` `{prefix}contr` `{prefix}kb` `{prefix}kod` `{prefix}lab` "
            f"`{prefix}cancel` `{prefix}resign` `{prefix}verify`"
        ), inline=False)

        embed.add_field(name="📊 Stats", value=(
            f"`{prefix}score` `{prefix}score @p1 vs @p2`"
        ), inline=False)

        if member_is_ref:
            embed.add_field(name="🛡️ Referee / Mod", value=f"`{prefix}erase-result`", inline=False)

        if member_is_mod:
            embed.add_field(name="⚙️ Mod Only", value=(
                f"`{prefix}prefix` `{prefix}set-channel` `{prefix}scores-reset` "
                f"`{prefix}add-game` `{prefix}remove-game` `{prefix}list-games`"
            ), inline=False)

        embed.set_footer(text="UltimateBot Light • Play hard 🎲")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
