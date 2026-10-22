# ============================================================
#  bot.py — UltimateBot Light entry point
#
#  Usage:
#      python bot.py
#
#  Requires a .env file (or environment variable) with:
#      DISCORD_TOKEN=your_bot_token_here
# ============================================================

import asyncio
import os
import logging

import discord
from discord.ext import commands
from dotenv import load_dotenv

import config
import database as db

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("UltimateBot")


async def get_prefix(bot: commands.Bot, message: discord.Message) -> list[str]:
    """Per-guild prefix from server.db. DMs use the default prefix."""
    if not message.guild:
        return commands.when_mentioned_or(config.DEFAULT_PREFIX)(bot, message)
    prefix = await db.get_prefix(message.guild.id)
    return commands.when_mentioned_or(prefix)(bot, message)


intents = discord.Intents.default()
intents.message_content = True   # prefix commands + in-game messages
intents.members = True           # member lookups, game recovery


class UltimateBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=get_prefix,
            intents=intents,
            help_command=None,
            case_insensitive=True,
        )

    async def setup_hook(self) -> None:
        log.info("Initialising databases…")
        await db.init_all_databases()
        log.info("Databases ready.")

        cogs = [
            "cogs.sessions",
            "cogs.game_starters",
            "cogs.score",
            "cogs.referee",
            "cogs.admin",
            "cogs.help",
        ]
        for cog in cogs:
            try:
                await self.load_extension(cog)
                log.info(f"Loaded cog: {cog}")
            except Exception as e:
                log.error(f"Failed to load cog {cog}: {e}", exc_info=True)

    async def on_ready(self) -> None:
        log.info(f"Logged in as {self.user} (ID: {self.user.id})")
        for guild in self.guilds:
            await db.ensure_guild(guild.id, guild.name)   # creates <server name>.db
        await self.change_presence(activity=discord.Game(name="Type .help for commands"))

    async def on_guild_join(self, guild: discord.Guild) -> None:
        await db.ensure_guild(guild.id, guild.name)

    async def on_guild_update(self, before: discord.Guild, after: discord.Guild) -> None:
        """Keep the score database file named after the server."""
        if before.name != after.name:
            try:
                await db.rename_results_db(after.id, after.name)
            except Exception:
                log.exception("Could not rename results DB for guild %s", after.id)

    async def on_command_error(self, ctx: commands.Context, error: Exception) -> None:
        """Global error handler."""
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.send("❌ This command can only be used in a server.")
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"❌ Missing argument: `{error.param.name}`. Use `.help {ctx.command}` for usage.")
            return
        if isinstance(error, commands.BadArgument):
            await ctx.send(f"❌ Bad argument. Use `.help {ctx.command}` for usage.")
            return
        if isinstance(error, commands.CheckFailure):
            await ctx.send(str(error) or "❌ You don't have permission to use this command.")
            return
        log.error(f"Unhandled error in command {ctx.command}: {error}", exc_info=True)
        await ctx.send("❌ An unexpected error occurred. Please try again later.")


async def main() -> None:
    async with UltimateBot() as bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
