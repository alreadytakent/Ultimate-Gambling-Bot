# ============================================================
#  cogs/admin.py — Moderator commands
#
#  All commands require the Mod role (config.MOD_ROLE_NAME).
#
#  Commands:
#    .add-money @player {amount}
#    .remove-item @player {item}
#    .remove-shop-item {item}
#    .add-shop-item {item_name}          — restore a removed default item
#    .set-work-amount {amount}
#    .set-work-cooldown {minutes}
#    .set-currency {emoji} {singular} {plural}
#    .prefix {symbol}
#    .change-target @assassin to @new_target
#    .set-channel {category} #channel
#    .required-votes [user_id ...] — set who must unanimously confirm season commands
#    .season-reset
#    .season-winner @player
#    .set-class @player {class}
#    .give-item @player {item}
#    .add-game "{full name}" {alias} {drawable}
#    .remove-game "{full name}" {alias} {drawable}
#    .list-games
# ============================================================

import discord
from discord.ext import commands
from typing import Optional
import random

import config
import database as db
from cogs.utils import fmt_currency, has_mod_role, UnanimousConfirmView, parse_amount
from cogs.classes import Classes


class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ════════════════════════════════════════════════════════
    #  ECONOMY MOD COMMANDS
    # ════════════════════════════════════════════════════════

    @commands.command(name="add-money")
    @has_mod_role()
    async def add_money(self, ctx: commands.Context, member: discord.Member, raw_amount: str):
        """Add (or remove if negative) carats. Accepts scientific notation: 1e6, 2.5e3, -1e4."""
        amount = parse_amount(raw_amount)
        if amount is None:
            await ctx.send("❌ Invalid amount. Examples: `5000`, `1e6`, `-2.5e3`")
            return
        player = await db.get_player(member.id, ctx.guild.id)
        if player is None:
            await ctx.send(f"❌ {member.mention} hasn't joined the season.")
            return

        settings = await db.get_guild_settings(ctx.guild.id)
        emoji = settings["currency_emoji"]
        name_s = settings["currency_name"]
        name_p = settings["currency_name_plural"]

        new_balance = await db.update_balance(member.id, ctx.guild.id, amount)
        action = "added to" if amount >= 0 else "removed from"
        await ctx.send(
            f"💰 {fmt_currency(abs(amount), emoji)} {action} "
            f"{member.mention}'s balance. New balance: "
            f"{fmt_currency(new_balance, emoji)}"
        )

    @commands.command(name="set-work-amount")
    @has_mod_role()
    async def set_work_amount(self, ctx: commands.Context, amount: int):
        """Set the amount earned per .work command."""
        if amount < 0:
            await ctx.send("❌ Amount must be positive.")
            return
        await db.set_guild_setting(ctx.guild.id, "work_amount", amount)
        settings = await db.get_guild_settings(ctx.guild.id)
        emoji = settings["currency_emoji"]
        name_s = settings["currency_name"]
        name_p = settings["currency_name_plural"]
        await ctx.send(
            f"✅ Work reward set to {fmt_currency(amount, emoji)}."
        )

    @commands.command(name="set-work-cooldown")
    @has_mod_role()
    async def set_work_cooldown(self, ctx: commands.Context, minutes: int):
        """Set the cooldown for .work (in minutes)."""
        if minutes < 1:
            await ctx.send("❌ Cooldown must be at least 1 minute.")
            return
        await db.set_guild_setting(ctx.guild.id, "work_cooldown_minutes", minutes)
        hours = minutes // 60
        mins = minutes % 60
        time_str = f"{hours}h {mins}m" if hours else f"{mins}m"
        await ctx.send(f"✅ Work cooldown set to **{time_str}**.")

    @commands.command(name="set-currency")
    @has_mod_role()
    async def set_currency(self, ctx: commands.Context, emoji: str, singular: str, plural: str):
        """
        Set the server currency symbol and name.
        Usage: .set-currency {emoji} {singular} {plural}
        Example: .set-currency 💎 carat carats
        """
        await db.set_guild_setting(ctx.guild.id, "currency_emoji", emoji)
        await db.set_guild_setting(ctx.guild.id, "currency_name", singular)
        await db.set_guild_setting(ctx.guild.id, "currency_name_plural", plural)
        await ctx.send(
            f"✅ Currency updated: {emoji} — **{singular}** (singular) / **{plural}** (plural)\n"
            f"Example: {fmt_currency(1000, emoji)}"
        )

    @commands.command(name="return-deposit")
    @has_mod_role()
    async def return_deposit(self, ctx: commands.Context, member: discord.Member):
        """
        Return a player's deposit immediately with no interest.
        Usage: .return-deposit @player
        """
        dep = await db.get_deposit(member.id, ctx.guild.id)
        if dep is None:
            await ctx.send(f"❌ {member.mention} has no active deposit.")
            return

        settings = await db.get_guild_settings(ctx.guild.id)
        emoji = settings["currency_emoji"]

        await db.update_balance(member.id, ctx.guild.id, dep["amount"])
        await db.delete_deposit(member.id, ctx.guild.id)

        await ctx.send(
            f"🏦 {member.mention}'s deposit of {fmt_currency(dep['amount'], emoji)} "
            f"has been returned with no interest by {ctx.author.mention}."
        )

    # ════════════════════════════════════════════════════════
    # ════════════════════════════════════════════════════════

    @commands.command(name="remove-item")
    @has_mod_role()
    async def remove_item(self, ctx: commands.Context, member: discord.Member, *, item_name: str):
        """Remove an item from a player's inventory."""
        removed = await db.remove_inventory_item(member.id, ctx.guild.id, item_name)
        if removed:
            await ctx.send(f"✅ Removed **{item_name}** from {member.mention}'s inventory.")
        else:
            await ctx.send(
                f"❌ {member.mention} doesn't have **{item_name}** in their inventory."
            )

    @commands.command(name="give-item")
    @has_mod_role()
    async def give_item(self, ctx: commands.Context, member: discord.Member, *, item_name: str):
        """Add an item directly to a player's inventory (bypassing shop)."""
        player = await db.get_player(member.id, ctx.guild.id)
        if player is None:
            await ctx.send(f"❌ {member.mention} hasn't joined the season.")
            return
        await db.add_inventory_item(member.id, ctx.guild.id, item_name)
        await ctx.send(f"✅ Gave **{item_name}** to {member.mention}.")

    @commands.command(name="remove-shop-item")
    @has_mod_role()
    async def remove_shop_item(self, ctx: commands.Context, *, item_name: str):
        """Remove an item from the shop."""
        removed = await db.remove_shop_item(item_name)
        if removed:
            await ctx.send(f"✅ Removed **{item_name}** from the shop.")
        else:
            await ctx.send(f"❌ **{item_name}** wasn't found in the shop.")

    @commands.command(name="add-shop-item")
    @has_mod_role()
    async def add_shop_item(self, ctx: commands.Context, *, item_name: str):
        """
        Restore a previously removed default shop item.
        Usage: .add-shop-item {item_name}
        Only works for items that are part of the default shop list.
        """
        # Find this item in the default list (case-insensitive)
        default_item = next(
            (i for i in config.DEFAULT_SHOP_ITEMS
             if i["item_name"].lower() == item_name.strip().lower()),
            None,
        )
        if default_item is None:
            default_names = ", ".join(f"**{i['item_name']}**" for i in config.DEFAULT_SHOP_ITEMS)
            await ctx.send(
                f"❌ **{item_name}** is not a default shop item and cannot be added this way.\n"
                f"Default items: {default_names}"
            )
            return

        # Check it isn't already in the shop
        existing = await db.get_shop_item(default_item["item_name"])
        if existing is not None:
            await ctx.send(
                f"❌ **{default_item['item_name']}** is already in the shop."
            )
            return

        await db.add_shop_item(
            item_name=default_item["item_name"],
            price=default_item["price"],
            stock=default_item["stock"],
            max_per_person=default_item["max_per_person"],
            description=default_item["description"],
            is_passive=bool(default_item["is_passive"]),
        )
        await ctx.send(f"✅ **{default_item['item_name']}** has been restored to the shop.")

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
    async def set_channel(
        self, ctx: commands.Context,
        category: str,
        channel: discord.TextChannel,
    ):
        """
        Restrict a command category to a specific channel.
        Categories: economy, games, shop, general
        Use 'none' as the channel to remove a restriction.
        """
        valid = {"economy", "games", "shop", "general"}
        if category.lower() not in valid:
            await ctx.send(
                f"❌ Unknown category. Valid categories: `{', '.join(sorted(valid))}`"
            )
            return
        await db.set_channel_restriction(ctx.guild.id, category.lower(), channel.id)
        await ctx.send(
            f"✅ `{category}` commands restricted to {channel.mention}."
        )

    # ════════════════════════════════════════════════════════
    #  CLASS MOD COMMANDS
    # ════════════════════════════════════════════════════════

    @commands.command(name="set-class")
    @has_mod_role()
    async def set_class(self, ctx: commands.Context, member: discord.Member, *, player_class: str):
        """Manually set a player's class."""
        player_class = player_class.strip().title()
        if player_class not in config.CLASS_NAMES:
            await ctx.send(
                f"❌ Unknown class. Valid classes: `{', '.join(config.CLASS_NAMES)}`"
            )
            return
        player = await db.get_player(member.id, ctx.guild.id)
        if player is None:
            await ctx.send(f"❌ {member.mention} hasn't joined the season.")
            return
        await db.set_player_class(member.id, ctx.guild.id, player_class, level=1)
        await ctx.send(
            f"✅ {member.mention}'s class set to **{player_class}** (Level 1)."
        )

    @commands.command(name="change-target")
    @has_mod_role()
    async def change_target(
        self, ctx: commands.Context,
        assassin: discord.Member,
        to_keyword: str,
        new_target: discord.Member,
    ):
        """
        Change an Assassin's target.
        Usage: .change-target @assassin to @new_target
        """
        if to_keyword.lower() != "to":
            await ctx.send("❌ Usage: `.change-target @assassin to @new_target`")
            return

        player = await db.get_player(assassin.id, ctx.guild.id)
        if player is None:
            await ctx.send(f"❌ {assassin.mention} hasn't joined the season.")
            return
        if player["class"] != "Assassin":
            await ctx.send(f"❌ {assassin.mention} is not an **Assassin**.")
            return

        new_target_player = await db.get_player(new_target.id, ctx.guild.id)
        if new_target_player is None:
            await ctx.send(f"❌ {new_target.mention} hasn't joined the season.")
            return
        if new_target == assassin:
            await ctx.send("❌ An Assassin can't target themselves.")
            return

        from datetime import datetime
        await db.update_class_state(
            assassin.id, ctx.guild.id,
            assassin_target_id=new_target.id,
            assassin_last_assigned=datetime.utcnow().isoformat(),
        )
        await ctx.send(
            f"🎯 {assassin.mention}'s target changed to **{new_target.display_name}**."
        )
        try:
            await assassin.send(
                f"🎯 **Target Updated!**\n"
                f"A moderator changed your Assassin target to **{new_target.display_name}**."
            )
        except discord.Forbidden:
            pass

    # ════════════════════════════════════════════════════════
    #  GAME MOD COMMANDS
    # ════════════════════════════════════════════════════════

    @commands.command(name="change-objective")
    @has_mod_role()
    async def change_objective(self, ctx: commands.Context, member: discord.Member, *, game_name: str):
        """
        Change a Specialist's objective to a specific game.
        Usage: .change-objective @player {game_name}
        Accepted aliases are those registered via .add-game / .list-games.
        """
        player = await db.get_player(member.id, ctx.guild.id)
        if player is None:
            await ctx.send(f"❌ {member.mention} hasn't joined the season.")
            return
        if player["class"] != "Specialist":
            await ctx.send(f"❌ {member.mention} is not a **Specialist**.")
            return

        # Resolve against guild game list
        games = await db.get_guild_games(ctx.guild.id)
        raw_lower = game_name.lower()
        resolved = next(
            (g["full_name"] for g in games
             if g["alias"].lower() == raw_lower or g["full_name"].lower() == raw_lower),
            None,
        )
        if not resolved:
            alias_list = ", ".join(
                f"`{g['alias']}` ({g['full_name']})" for g in games
            )
            await ctx.send(
                f"❌ **{game_name}** is not a recognised game name.\n"
                f"Accepted aliases: {alias_list}"
            )
            return

        await db.update_class_state(
            member.id, ctx.guild.id,
            specialist_objective_game=resolved,
        )
        await ctx.send(
            f"📋 {member.mention}'s Specialist objective changed to **{resolved}**."
        )
        try:
            await member.send(
                f"📋 **Objective Updated!**\n"
                f"A moderator changed your Specialist objective to **{resolved}**."
            )
        except discord.Forbidden:
            pass

    @commands.command(name="cancel-game")
    @has_mod_role()
    async def cancel_game(self, ctx: commands.Context, member: discord.Member):
        """Cancel the active game a player is in. Bets are refunded."""
        sessions_cog = self.bot.get_cog("Sessions")
        if sessions_cog is None:
            await ctx.send("❌ Session manager not loaded.")
            return

        session = sessions_cog.get_session_for_player(member.id)
        if session is None:
            await ctx.send(f"❌ {member.mention} is not in an active game.")
            return

        # Refund all players
        if session.bet_amount > 0:
            for uid in session.player_ids:
                await db.update_balance(uid, ctx.guild.id, session.bet_amount)

        # Remove session without recording result
        sessions_cog._sessions.pop(session.session_id, None)
        for uid in session.player_ids:
            sessions_cog._player_session.pop(uid, None)
        if session._inactivity_task:
            session._inactivity_task.cancel()
        if session._absolute_task:
            session._absolute_task.cancel()
        await db.delete_session(session.session_id)

        await ctx.send(
            f"🛑 Game of **{session.game.game_name}** cancelled by mod. "
            f"Bets refunded (if any)."
        )

    # ── .add-game ─────────────────────────────────────────────

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

    # ── .remove-game ──────────────────────────────────────────

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

        await ctx.send(
            f"✅ **{full_name}** (`{alias.lower()}`) removed from the accepted game list."
        )

    # ── .list-games ───────────────────────────────────────────

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

    # ════════════════════════════════════════════════════════
    #  SEASON COMMANDS
    # ════════════════════════════════════════════════════════

    @commands.command(name="required-votes")
    @has_mod_role()
    async def required_votes(self, ctx: commands.Context, *raw_ids: str):
        """
        Set the list of user IDs that must unanimously confirm .season-reset
        and .season-winner.  Requires between 2 and 10 IDs.

        Usage: .required-votes 123456789 987654321 [...]
        """
        if len(raw_ids) < 2:
            await ctx.send("❌ You must provide at least **2** user IDs.")
            return
        if len(raw_ids) > 10:
            await ctx.send("❌ You can provide at most **10** user IDs.")
            return

        parsed: list[int] = []
        for raw in raw_ids:
            try:
                parsed.append(int(raw))
            except ValueError:
                await ctx.send(f"❌ `{raw}` is not a valid user ID.")
                return

        # Deduplicate while preserving order
        seen: set[int] = set()
        unique: list[int] = []
        for uid in parsed:
            if uid not in seen:
                seen.add(uid)
                unique.append(uid)

        if len(unique) < 2:
            await ctx.send("❌ After removing duplicates you must still have at least **2** unique IDs.")
            return

        await db.set_required_votes(ctx.guild.id, unique)

        lines = []
        for uid in unique:
            member = ctx.guild.get_member(uid)
            lines.append(f"• {member.mention if member else f'`{uid}` (not in server)'}")

        embed = discord.Embed(
            title="✅ Required Votes Updated",
            description="\n".join(lines),
            color=discord.Color.green(),
        )
        embed.set_footer(text=f"{len(unique)} user(s) must unanimously confirm season commands.")
        await ctx.send(embed=embed)

    async def _get_voters(self, ctx: commands.Context) -> list[int] | None:
        """
        Fetch and validate the required-votes list for the guild.
        Sends an error and returns None if the list is not properly configured.
        """
        voter_ids = await db.get_required_votes(ctx.guild.id)
        if len(voter_ids) < 2:
            await ctx.send(
                "❌ **Required votes not configured.**\n"
                "Use `.required-votes [user_id1] [user_id2] ...` (min 2, max 10) "
                "to set who must approve season commands."
            )
            return None
        return voter_ids

    @commands.command(name="season-reset")
    @has_mod_role()
    async def season_reset(self, ctx: commands.Context):
        """
        Reset the season. Requires unanimous confirmation from all users in
        the required-votes list. All player data is wiped; players must .join again.
        """
        voter_ids = await self._get_voters(ctx)
        if voter_ids is None:
            return

        voter_mentions = []
        for uid in voter_ids:
            m = ctx.guild.get_member(uid)
            voter_mentions.append(m.mention if m else f"`{uid}`")

        embed = discord.Embed(
            title="⚠️ Season Reset",
            description=(
                "This will **wipe ALL player data** (balances, inventories, classes, stats).\n"
                "Players will need to use `.join` to re-enter the new season.\n\n"
                "All of the following users must confirm:"
            ),
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="Required votes",
            value="\n".join(f"⬜ {m}" for m in voter_mentions),
            inline=False,
        )

        view = UnanimousConfirmView(
            required_user_ids=voter_ids,
            guild=ctx.guild,
            action_label="Season Reset",
        )
        await ctx.send(embed=embed, view=view)
        await view.wait()

        if view.approved:
            new_season = await db.reset_season(ctx.guild.id)
            await ctx.send(
                f"🔄 Season reset complete! **Season {new_season}** has begun.\n"
                f"All players must use `.join` to enter the new season."
            )

    @commands.command(name="season-winner")
    @has_mod_role()
    async def season_winner(self, ctx: commands.Context, winner: discord.Member):
        """
        Declare a season winner (roadmap completion).
        Requires unanimous confirmation from all users in the required-votes list.
        """
        voter_ids = await self._get_voters(ctx)
        if voter_ids is None:
            return

        voter_mentions = []
        for uid in voter_ids:
            m = ctx.guild.get_member(uid)
            voter_mentions.append(m.mention if m else f"`{uid}`")

        embed = discord.Embed(
            title="🏆 Season Winner Declaration",
            description=(
                f"Declaring **{winner.mention}** as the season winner!\n\n"
                "All of the following users must confirm:"
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="Required votes",
            value="\n".join(f"⬜ {m}" for m in voter_mentions),
            inline=False,
        )

        view = UnanimousConfirmView(
            required_user_ids=voter_ids,
            guild=ctx.guild,
            action_label="Season Winner Declaration",
        )
        await ctx.send(embed=embed, view=view)
        await view.wait()

        if view.approved:
            await ctx.send(
                f"🎉 **{winner.mention}** has won the season by completing the roadmap!\n"
                f"A new season will begin after `.season-reset`."
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Admin(bot))