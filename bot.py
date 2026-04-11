# ============================================================
#  bot.py — UltimateBot entry point
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

# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("UltimateBot")


# ── Dynamic prefix ───────────────────────────────────────────
async def get_prefix(bot: commands.Bot, message: discord.Message) -> list[str]:
    """
    Fetch the per-guild prefix from server.db.
    Falls back to DEFAULT_PREFIX if the guild isn't registered yet.
    DMs always use the default prefix.
    """
    if not message.guild:
        return commands.when_mentioned_or(config.DEFAULT_PREFIX)(bot, message)
    prefix = await db.get_prefix(message.guild.id)
    return commands.when_mentioned_or(prefix)(bot, message)


# ── Intents ──────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True   # needed to read message text (prefix commands)
intents.members = True           # needed for member lookups, Assassin targets, mutes


# ── Bot ──────────────────────────────────────────────────────
class UltimateBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=get_prefix,
            intents=intents,
            help_command=None,       # we define our own .help
            case_insensitive=True,   # .DTH == .dth
        )

    async def setup_hook(self) -> None:
        """Called once before the bot connects. Load cogs and init DB."""
        log.info("Initialising databases…")
        await db.init_all_databases(config.DEFAULT_SHOP_ITEMS)
        log.info("Databases ready.")

        cogs = [
            "cogs.economy",
            "cogs.shop",
            "cogs.classes",
            "cogs.season",
            "cogs.score",
            "cogs.sessions",
            "cogs.game_starters",
            "cogs.referee",
            "cogs.tournament",
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
        await self.change_presence(
            activity=discord.Game(name="Type .help for commands")
        )

    async def on_message(self, message: discord.Message) -> None:
        """
        Override to handle DM routing for secret game commands.
        DMs are passed to the active session for the sender.
        Guild messages go through normal command processing.
        """
        if message.author.bot:
            return

        # ── DM routing ──────────────────────────────────────
        if isinstance(message.channel, discord.DMChannel):
            session = await db.get_player_session_by_dm(message.author.id)
            if session:
                # Let the sessions cog handle it
                self.dispatch("dm_game_message", message, session)
            # Also process as a normal command in case user is calling .help in DM etc.
            await self.process_commands(message)
            return

        # ── Channel restriction check ────────────────────────
        # (individual cogs enforce this; handled here as a fallback)
        await self.process_commands(message)

    async def on_command_error(self, ctx: commands.Context, error: Exception) -> None:
        """Global error handler."""
        if isinstance(error, commands.CommandNotFound):
            return   # silently ignore unknown commands
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"❌ Missing argument: `{error.param.name}`. Use `.help {ctx.command}` for usage.")
            return
        if isinstance(error, commands.BadArgument):
            await ctx.send(f"❌ Bad argument. Use `.help {ctx.command}` for usage.")
            return
        if isinstance(error, commands.CheckFailure):
            await ctx.send("❌ You don't have permission to use this command.")
            return
        # Unexpected error — log it
        log.error(f"Unhandled error in command {ctx.command}: {error}", exc_info=True)
        await ctx.send("❌ An unexpected error occurred. Please try again later.")


# ── Entry point ──────────────────────────────────────────────
async def main() -> None:
    async with UltimateBot() as bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
