# ============================================================
#  cogs/season.py — Season management
#
#  Commands:
#    .join           — join the current season
# ============================================================

import discord
from discord.ext import commands

import config
import database as db
from cogs.utils import fmt_currency, channel_only
from cogs.classes import Classes


class Season(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="join")
    @channel_only("general")
    async def join(self, ctx: commands.Context):
        """Join the current season and receive a random class."""
        existing = await db.get_player(ctx.author.id, ctx.guild.id)
        if existing:
            await ctx.send(
                f"❌ You've already joined the season as a "
                f"**{existing['class']}** (Level {existing['class_level']})!"
            )
            return

        settings = await db.get_guild_settings(ctx.guild.id)
        season = settings["current_season"]
        starting_balance = config.DEFAULT_STARTING_BALANCE

        assigned_class = Classes.assign_random_class()
        player = await db.create_player(
            ctx.author.id, ctx.guild.id,
            player_class=assigned_class,
            season_number=season,
            starting_balance=starting_balance,
        )

        emoji = settings["currency_emoji"]
        name_s = settings["currency_name"]
        name_p = settings["currency_name_plural"]

        embed = discord.Embed(
            title="🎉 Welcome to the Season!",
            description=(
                f"{ctx.author.mention} has joined **Season {season}**!\n\n"
                f"You have been assigned the **{assigned_class}** class.\n"
                f"Starting balance: {fmt_currency(starting_balance, emoji)}\n\n"
                f"Use `.classes` to learn about your class, and `.help` to see all commands."
            ),
            color=discord.Color.green(),
        )
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Season(bot))
