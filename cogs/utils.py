# ============================================================
#  cogs/utils.py — Shared helpers and decorators
# ============================================================

from typing import Iterable, Optional

import discord
from discord.ext import commands

import config
import database as db


# ════════════════════════════════════════════════════════════
#  ROLE CHECKS
# ════════════════════════════════════════════════════════════

def is_mod(member) -> bool:
    return discord.utils.get(getattr(member, "roles", []), name=config.MOD_ROLE_NAME) is not None


def is_referee_or_mod(member) -> bool:
    roles = {r.name for r in getattr(member, "roles", [])}
    return config.REFEREE_ROLE_NAME in roles or config.MOD_ROLE_NAME in roles


def has_mod_role():
    """Check decorator: user must have the Mod role."""
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if is_mod(ctx.author):
            return True
        raise commands.CheckFailure(
            f"❌ You need the **{config.MOD_ROLE_NAME}** role to use this command."
        )
    return commands.check(predicate)


def has_referee_or_mod_role():
    """Check decorator: user must have the Referee or Mod role."""
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if is_referee_or_mod(ctx.author):
            return True
        raise commands.CheckFailure(
            f"❌ You need the **{config.REFEREE_ROLE_NAME}** or **{config.MOD_ROLE_NAME}** "
            f"role to use this command."
        )
    return commands.check(predicate)


# ════════════════════════════════════════════════════════════
#  CHANNEL RESTRICTION
# ════════════════════════════════════════════════════════════

def channel_only(category: str):
    """
    If a channel restriction is set for this category in the guild,
    the command must be used in that channel.
    """
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.guild is None:
            return True
        restricted_channel_id = await db.get_channel_restriction(ctx.guild.id, category)
        if restricted_channel_id is None or ctx.channel.id == restricted_channel_id:
            return True
        channel = ctx.guild.get_channel(restricted_channel_id)
        mention = channel.mention if channel else f"channel {restricted_channel_id}"
        raise commands.CheckFailure(f"❌ This command can only be used in {mention}.")
    return commands.check(predicate)


# ════════════════════════════════════════════════════════════
#  CONFIRMATION VIEW (used by .scores-reset)
# ════════════════════════════════════════════════════════════

class ConfirmView(discord.ui.View):
    """Two buttons. `value` is True / False / None (timed out)."""

    def __init__(self, allowed_ids: Iterable[int], *, confirm_label: str = "Confirm",
                 cancel_label: str = "Cancel", timeout: float = 60.0):
        super().__init__(timeout=timeout)
        self.allowed_ids = set(allowed_ids)
        self.value: Optional[bool] = None
        self.message: Optional[discord.Message] = None

        yes = discord.ui.Button(label=confirm_label, style=discord.ButtonStyle.danger)
        no = discord.ui.Button(label=cancel_label, style=discord.ButtonStyle.secondary)
        yes.callback = self._yes
        no.callback = self._no
        self.add_item(yes)
        self.add_item(no)

    async def _finish(self, interaction: discord.Interaction, value: bool) -> None:
        if interaction.user.id not in self.allowed_ids:
            await interaction.response.send_message("❌ This isn't for you.", ephemeral=True)
            return
        self.value = value
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    async def _yes(self, interaction: discord.Interaction) -> None:
        await self._finish(interaction, True)

    async def _no(self, interaction: discord.Interaction) -> None:
        await self._finish(interaction, False)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
