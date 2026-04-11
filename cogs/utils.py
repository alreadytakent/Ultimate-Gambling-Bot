# ============================================================
#  cogs/utils.py — Shared helpers and decorators
# ============================================================

import functools
from typing import Optional, Callable

import discord
from discord.ext import commands

import config
import database as db



# ════════════════════════════════════════════════════════════
#  AMOUNT PARSING
# ════════════════════════════════════════════════════════════

def parse_amount(raw: str) -> int | None:
    """
    Parse a currency amount string into an integer.
    Accepts plain integers and scientific notation: 1e6, 2.5e3, 1E6, etc.
    Returns None if the string cannot be parsed or is not a positive integer.

    Examples:
      "5000"   -> 5000
      "1e6"    -> 1000000
      "2.5e3"  -> 2500
      "1E6"    -> 1000000
      "-500"   -> -500   (negatives allowed for add-money)
      "abc"    -> None
    """
    try:
        value = float(raw)
        result = int(value)
        # Guard against float precision drift (e.g. 1e20 -> still int)
        if result != value:
            return None
        return result
    except (ValueError, OverflowError):
        return None

# ════════════════════════════════════════════════════════════
#  FORMATTING
# ════════════════════════════════════════════════════════════

def fmt_currency(amount: int, emoji: str) -> str:
    return f"{emoji}**{amount:,}**"


# ════════════════════════════════════════════════════════════
#  ROLE CHECKS
# ════════════════════════════════════════════════════════════

def has_mod_role():
    """Check decorator: user must have the Mod role."""
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.guild is None:
            return False
        role = discord.utils.get(ctx.guild.roles, name=config.MOD_ROLE_NAME)
        if role and role in ctx.author.roles:
            return True
        raise commands.CheckFailure(
            f"❌ You need the **{config.MOD_ROLE_NAME}** role to use this command."
        )
    return commands.check(predicate)


def has_referee_role():
    """Check decorator: user must have the Referee role."""
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.guild is None:
            return False
        role = discord.utils.get(ctx.guild.roles, name=config.REFEREE_ROLE_NAME)
        if role and role in ctx.author.roles:
            return True
        raise commands.CheckFailure(
            f"❌ You need the **{config.REFEREE_ROLE_NAME}** role to use this command."
        )
    return commands.check(predicate)


# ════════════════════════════════════════════════════════════
#  CHANNEL RESTRICTION
# ════════════════════════════════════════════════════════════

def channel_only(category: str):
    """
    Check decorator: if a channel restriction is set for this category in the
    guild, the command must be used in that channel.
    """
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.guild is None:
            return True   # allow DM usage
        restricted_channel_id = await db.get_channel_restriction(ctx.guild.id, category)
        if restricted_channel_id is None:
            return True   # no restriction set
        if ctx.channel.id == restricted_channel_id:
            return True
        channel = ctx.guild.get_channel(restricted_channel_id)
        mention = channel.mention if channel else f"channel {restricted_channel_id}"
        raise commands.CheckFailure(
            f"❌ This command can only be used in {mention}."
        )
    return commands.check(predicate)


# ════════════════════════════════════════════════════════════
#  PLAYER CHECK
# ════════════════════════════════════════════════════════════

def require_player():
    """Check decorator: the command author must have joined the season."""
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.guild is None:
            return True
        player = await db.get_player(ctx.author.id, ctx.guild.id)
        if player is not None:
            return True
        raise commands.CheckFailure(
            "❌ You haven't joined the season yet! Use `.join` first."
        )
    return commands.check(predicate)


# ════════════════════════════════════════════════════════════
#  PAGINATED EMBED VIEWER
# ════════════════════════════════════════════════════════════

class PaginatedView(discord.ui.View):
    """
    A View with ◀ / ▶ buttons for multi-page embeds.

    Usage:
        pages = [embed1, embed2, embed3]
        view = PaginatedView(pages)
        await ctx.send(embed=pages[0], view=view)
    """

    def __init__(self, pages: list[discord.Embed], author_id: int, timeout: float = 120.0):
        super().__init__(timeout=timeout)
        self.pages = pages
        self.current = 0
        self.author_id = author_id
        self._update_buttons()

    def _update_buttons(self):
        self.prev_button.disabled = self.current == 0
        self.next_button.disabled = self.current == len(self.pages) - 1

    async def _update(self, interaction: discord.Interaction):
        self._update_buttons()
        await interaction.response.edit_message(
            embed=self.pages[self.current], view=self
        )

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ This isn't your scoreboard.", ephemeral=True)
            return
        self.current -= 1
        await self._update(interaction)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ This isn't your scoreboard.", ephemeral=True)
            return
        self.current += 1
        await self._update(interaction)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


# ════════════════════════════════════════════════════════════
#  CONFIRMATION VIEW (for dangerous mod commands)
# ════════════════════════════════════════════════════════════

# Keep old name as an alias so any external references don't break.
DoubleConfirmView = None   # replaced below; reassigned after class definition


class UnanimousConfirmView(discord.ui.View):
    """
    Unanimous confirmation: every user in `required_user_ids` must press
    ✅ Confirm before the action proceeds.  Any one of them can cancel.

    The embed/message is updated live to show who has already confirmed.
    Used for .season-reset and .season-winner.
    """

    def __init__(
        self,
        required_user_ids: list[int],
        guild: discord.Guild,
        action_label: str = "this action",
        timeout: float = 120.0,
    ):
        super().__init__(timeout=timeout)
        self.required_user_ids = list(dict.fromkeys(required_user_ids))  # deduplicate, preserve order
        self.guild        = guild
        self.action_label = action_label
        self.confirmed_by: set[int] = set()
        self.approved   = False
        self.cancelled  = False

    def _status_lines(self) -> str:
        lines = []
        for uid in self.required_user_ids:
            member = self.guild.get_member(uid)
            name   = member.display_name if member else f"<@{uid}>"
            tick   = "✅" if uid in self.confirmed_by else "⬜"
            lines.append(f"{tick} {name}")
        return "\n".join(lines)

    def _build_embed(self, title: str, color: discord.Color) -> discord.Embed:
        embed = discord.Embed(title=title, color=color)
        embed.add_field(name="Confirmations", value=self._status_lines(), inline=False)
        remaining = len(self.required_user_ids) - len(self.confirmed_by)
        embed.set_footer(text=f"{remaining} confirmation(s) still needed")
        return embed

    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in self.required_user_ids:
            await interaction.response.send_message(
                "❌ You are not in the required-votes list for this action.", ephemeral=True
            )
            return
        if interaction.user.id in self.confirmed_by:
            await interaction.response.send_message(
                "You have already confirmed.", ephemeral=True
            )
            return

        self.confirmed_by.add(interaction.user.id)
        remaining = set(self.required_user_ids) - self.confirmed_by

        if not remaining:
            self.approved = True
            self.stop()
            embed = self._build_embed(f"✅ {self.action_label} — Approved", discord.Color.green())
            embed.set_footer(text="All votes received. Proceeding…")
            await interaction.response.edit_message(embed=embed, view=None)
        else:
            embed = self._build_embed(
                f"⏳ {self.action_label} — Waiting for votes",
                discord.Color.orange(),
            )
            await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in self.required_user_ids:
            await interaction.response.send_message(
                "❌ Only required voters can cancel this action.", ephemeral=True
            )
            return
        self.cancelled = True
        self.stop()
        embed = discord.Embed(
            title=f"❌ {self.action_label} — Cancelled",
            description=f"Cancelled by {interaction.user.mention}.",
            color=discord.Color.red(),
        )
        await interaction.response.edit_message(embed=embed, view=None)


# Backward-compat alias
DoubleConfirmView = UnanimousConfirmView