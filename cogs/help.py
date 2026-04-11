# ============================================================
#  cogs/help.py — Custom help command
# ============================================================

import discord
from discord.ext import commands

import config


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="help")
    async def help(self, ctx: commands.Context, *, topic: str = None):
        """Show help. Use .help {command} for details on a specific command."""
        prefix = ctx.prefix or config.DEFAULT_PREFIX

        if topic:
            # Look up a specific command
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

        # Determine role privileges (guild only; DMs get base view)
        is_mod = False
        is_referee = False
        if ctx.guild:
            mod_role      = discord.utils.get(ctx.guild.roles, name=config.MOD_ROLE_NAME)
            referee_role  = discord.utils.get(ctx.guild.roles, name=config.REFEREE_ROLE_NAME)
            is_mod        = bool(mod_role      and mod_role      in ctx.author.roles)
            is_referee    = bool(referee_role  and referee_role  in ctx.author.roles)

        # Full help
        embed = discord.Embed(
            title="📖 UltimateBot Commands",
            description=f"Use `{prefix}help <command>` for details on any command.",
            color=discord.Color.blurple(),
        )

        embed.add_field(name="💰 Economy", value=(
            f"`{prefix}bal` `{prefix}give` `{prefix}work` `{prefix}lb` "
            f"`{prefix}deposit` `{prefix}mydeposit`"
        ), inline=False)

        embed.add_field(name="🏪 Shop", value=(
            f"`{prefix}itemshop` `{prefix}buy` `{prefix}inv` `{prefix}use`"
        ), inline=False)

        embed.add_field(name="🎮 Games", value=(
            f"`{prefix}dth` `{prefix}mdth` `{prefix}gops` `{prefix}dotty` "
            f"`{prefix}comb` `{prefix}airpoker` `{prefix}contr` `{prefix}kb` `{prefix}kod` `{prefix}cancel`"
        ), inline=False)

        embed.add_field(name="📊 Stats", value=(
            f"`{prefix}score` `{prefix}score @p1 vs @p2`"
        ), inline=False)

        embed.add_field(name="🌟 Season & Class", value=(
            f"`{prefix}join` `{prefix}classes` `{prefix}collect` "
            f"`{prefix}target` `{prefix}objective`"
        ), inline=False)

        embed.add_field(name="🏆 Tournament", value=(
            f"`{prefix}tournament-status`"
        ), inline=False)

        if is_referee or is_mod:
            embed.add_field(name="🛡️ Referee / Mod", value=(
                f"`{prefix}verify` `{prefix}erase-result` `{prefix}tournament-advance`"
            ), inline=False)

        if is_mod:
            embed.add_field(name="⚙️ Mod Only", value=(
                f"`{prefix}add-money` `{prefix}set-work-amount` `{prefix}set-work-cooldown` "
                f"`{prefix}set-currency` `{prefix}remove-item` `{prefix}give-item` "
                f"`{prefix}remove-shop-item` `{prefix}add-shop-item` `{prefix}return-deposit` `{prefix}prefix` "
                f"`{prefix}set-channel` `{prefix}set-class` `{prefix}change-target` "
                f"`{prefix}change-objective` `{prefix}cancel-game` `{prefix}required-votes` "
                f"`{prefix}season-reset` `{prefix}season-winner` `{prefix}start-tournament` "
                f"`{prefix}add-game` `{prefix}remove-game` `{prefix}list-games`"
            ), inline=False)

        embed.set_footer(text="UltimateBot • Bet smart, play hard 🎲")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))