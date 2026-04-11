# ============================================================
#  cogs/shop.py — Shop system
#
#  Commands:
#    .itemshop          — browse the shop
#    .buy {item}        — purchase an item
#    .inv               — view your inventory
#    .use {item} [args] — use an active item
# ============================================================

import discord
from discord.ext import commands
from typing import Optional

import config
import database as db
from cogs.utils import fmt_currency, require_player, channel_only


class Shop(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── .itemshop ────────────────────────────────────────────
    @commands.command(name="itemshop", aliases=["shop"])
    @channel_only("shop")
    async def itemshop(self, ctx: commands.Context):
        """Browse the item shop."""
        items = await db.get_shop_items()
        settings = await db.get_guild_settings(ctx.guild.id)
        emoji = settings["currency_emoji"]
        name_s = settings["currency_name"]
        name_p = settings["currency_name_plural"]

        if not items:
            await ctx.send("🛒 The shop is currently empty.")
            return

        active_items = [i for i in items if not i["is_passive"]]
        passive_items = [i for i in items if i["is_passive"]]

        def build_embeds(item_list, section_label):
            embeds = []
            current = discord.Embed(
                title=f"🛒 Item Shop — {section_label}",
                color=discord.Color.purple(),
            )
            field_count = 0
            for item in item_list:
                if item["item_name"] == "Class Level Up":
                    price_str = "Price depends on your level (see `.classes`)"
                else:
                    price_str = fmt_currency(item["price"], emoji)
                stock_str = "∞" if item["stock"] == -1 else str(item["stock"])
                per_person = (
                    f" · max {item['max_per_person']} per player"
                    if item["max_per_person"] != -1 else ""
                )
                field_value = (
                    f"{price_str}\n"
                    f"Stock: **{stock_str}**{per_person}\n"
                    f"*{item['description']}*"
                )
                if field_count >= 25:
                    embeds.append(current)
                    current = discord.Embed(color=discord.Color.purple())
                    field_count = 0
                current.add_field(name=item["item_name"], value=field_value, inline=False)
                field_count += 1
            current.set_footer(text="Use .buy <item name> to purchase · .inv to see your items")
            embeds.append(current)
            return embeds

        all_embeds = []
        if active_items:
            all_embeds.extend(build_embeds(active_items, "🟢 Active Items"))
        if passive_items:
            all_embeds.extend(build_embeds(passive_items, "🔴 Passive Items"))

        for embed in all_embeds:
            await ctx.send(embed=embed)

    # ── .buy ─────────────────────────────────────────────────
    @commands.command(name="buy")
    @channel_only("shop")
    @require_player()
    async def buy(self, ctx: commands.Context, *, item_name: str):
        """Purchase an item from the shop."""
        item = await db.get_shop_item(item_name)
        if item is None:
            await ctx.send(
                f"❌ Item **{item_name}** not found in the shop. "
                f"Use `.itemshop` to see available items."
            )
            return

        player = await db.get_player(ctx.author.id, ctx.guild.id)
        settings = await db.get_guild_settings(ctx.guild.id)
        emoji = settings["currency_emoji"]
        name_s = settings["currency_name"]
        name_p = settings["currency_name_plural"]

        # ── Class Level Up — special pricing ─────────────────
        if item["item_name"] == "Class Level Up":
            classes_cog = self.bot.get_cog("Classes")
            if classes_cog:
                success, msg = await classes_cog.level_up(ctx.author.id, ctx.guild.id)
                symbol = "✅" if success else "❌"
                await ctx.send(f"{symbol} {msg}")
            return

        # ── Stock check ───────────────────────────────────────
        if item["stock"] == 0:
            await ctx.send(f"❌ **{item['item_name']}** is out of stock.")
            return

        # ── Max per person check ──────────────────────────────
        if item["max_per_person"] != -1:
            owned = await db.get_player_item_count(ctx.author.id, ctx.guild.id, item["item_name"])
            if owned >= item["max_per_person"]:
                await ctx.send(
                    f"❌ You can only hold **{item['max_per_person']}** of **{item['item_name']}** at a time."
                )
                return

        # ── Balance check ─────────────────────────────────────
        if player["balance"] < item["price"]:
            await ctx.send(
                f"❌ You can't afford **{item['item_name']}**. "
                f"You need {fmt_currency(item['price'], emoji)} "
                f"but only have {fmt_currency(player['balance'], emoji)}."
            )
            return

        # ── Deduct balance & decrement stock ──────────────────
        await db.update_balance(ctx.author.id, ctx.guild.id, -item["price"])
        if item["stock"] != -1:
            await db.decrement_shop_stock(item["item_name"])

        # ── Instant-use items (apply immediately on purchase) ──
        applied_msg = ""
        if item["item_name"] == "Suspicious Stew":
            applied_msg = await self._apply_suspicious_stew(ctx, player)
        elif item["item_name"] == "Polyjuice Potion":
            await ctx.send(
                f"🧪 {ctx.author.mention} drank the Polyjuice Potion!\n"
                f"Which class do you want? Reply with one of: "
                f"`{', '.join(config.CLASS_NAMES)}`"
            )
            def check(m):
                return (
                    m.author == ctx.author
                    and m.channel == ctx.channel
                    and m.content.strip().title() in config.CLASS_NAMES
                )
            try:
                reply = await self.bot.wait_for("message", check=check, timeout=30.0)
                chosen = reply.content.strip().title()
                await db.set_player_class(ctx.author.id, ctx.guild.id, chosen, level=1)
                await ctx.send(
                    f"✅ {ctx.author.mention} transformed into a **{chosen}** (Level 1)!"
                )
            except Exception:
                # Refund if they don't respond
                await db.update_balance(ctx.author.id, ctx.guild.id, item["price"])
                if item["stock"] != -1:
                    await db.add_shop_item(  # re-increment by just restoring stock isn't ideal; skip for now
                        item["item_name"], item["price"], item["stock"] + 1,
                        item["max_per_person"], item["description"], bool(item["is_passive"])
                    )
                await ctx.send("⏰ Timed out. Purchase refunded.")
            return
        elif item["item_name"] == "Tournament Ticket":
            applied_msg = await self._apply_tournament_ticket(ctx)

        # ── Add to inventory (for items that stay in inv) ─────
        non_inventory_items = {"Suspicious Stew", "Polyjuice Potion", "Tournament Ticket"}
        if item["item_name"] not in non_inventory_items:
            await db.add_inventory_item(ctx.author.id, ctx.guild.id, item["item_name"])

        cost_str = fmt_currency(item["price"], emoji)
        await ctx.send(
            f"✅ {ctx.author.mention} bought **{item['item_name']}** for {cost_str}! "
            + applied_msg
        )

    async def _apply_suspicious_stew(self, ctx: commands.Context, player: dict) -> str:
        current = player["class"]
        pool = [c for c in config.CLASS_NAMES if c != current]
        import random
        new_class = random.choice(pool) if pool else current
        await db.set_player_class(ctx.author.id, ctx.guild.id, new_class, level=1)
        return f"🍲 Class changed from **{current}** to **{new_class}** (Level 1)!"

    async def _apply_tournament_ticket(self, ctx: commands.Context) -> str:
        tournament_cog = self.bot.get_cog("Tournament")
        if tournament_cog:
            msg = await tournament_cog.on_ticket_purchased(ctx.author, ctx.guild)
            return msg
        return ""

    # ── .inv ─────────────────────────────────────────────────
    @commands.command(name="inv", aliases=["inventory"])
    @require_player()
    async def inv(self, ctx: commands.Context):
        """View your inventory."""
        items = await db.get_inventory(ctx.author.id, ctx.guild.id)
        if not items:
            await ctx.send(
                f"🎒 {ctx.author.mention}'s inventory is empty. "
                f"Visit the `.itemshop` to buy items!"
            )
            return

        embed = discord.Embed(
            title=f"🎒 {ctx.author.display_name}'s Inventory",
            color=discord.Color.blue(),
        )
        lines = [f"• **{i['item_name']}** ×{i['quantity']}" for i in items]
        embed.description = "\n".join(lines)
        embed.set_footer(text="Use .use <item> to use an item")
        await ctx.send(embed=embed)

    # ── .use ─────────────────────────────────────────────────
    @commands.command(name="use")
    @require_player()
    async def use(self, ctx: commands.Context, *, args: str):
        """
        Use an active item from your inventory.

        Examples:
          .use Totem of Undying
          .use Mute Button @player
        """
        # Split off possible @mention at the end
        parts = args.split()
        if ctx.message.mentions:
            # Args might be: "Mute Button @someone"
            target = ctx.message.mentions[0]
            # Remove the mention from the item name search
            item_name = args.replace(target.mention, "").replace(f"<@{target.id}>", "").strip()
        else:
            item_name = args
            target = None

        qty = await db.get_inventory_item(ctx.author.id, ctx.guild.id, item_name)
        if qty == 0:
            await ctx.send(
                f"❌ You don't have **{item_name}** in your inventory. Use `.inv` to check."
            )
            return

        item_lower = item_name.lower()

        # ── Totem of Undying ──────────────────────────────────
        if "totem" in item_lower:
            session = self.bot.get_cog("Sessions")
            if session and session.get_session_for_player(ctx.author.id):
                await ctx.send(
                    "❌ You can only activate the Totem **before** starting a game."
                )
                return
            # Mark as active in a simple in-memory set (or DB flag)
            if not hasattr(self.bot, "_totem_active"):
                self.bot._totem_active = set()
            self.bot._totem_active.add(ctx.author.id)
            await db.remove_inventory_item(ctx.author.id, ctx.guild.id, item_name)
            await ctx.send(
                f"🏺 {ctx.author.mention} activated the **Totem of Undying**! "
                f"Your next bet loss will be refunded."
            )

        # ── Mute Button ───────────────────────────────────────
        elif "mute" in item_lower:
            if target is None:
                await ctx.send("❌ You need to specify who to mute: `.use Mute Button @player`")
                return
            if target == ctx.author:
                await ctx.send("❌ You can't mute yourself.")
                return

            uses = await db.get_mute_uses_today(ctx.author.id, ctx.guild.id)
            if uses >= config.MUTE_DAILY_USE_LIMIT:
                await ctx.send(
                    f"❌ You've already used the Mute Button "
                    f"{config.MUTE_DAILY_USE_LIMIT} times today."
                )
                return

            # Apply mute (timeout in Discord)
            try:
                import datetime as dt
                await target.timeout(
                    dt.timedelta(minutes=config.MUTE_DURATION_MINUTES),
                    reason=f"Mute Button used by {ctx.author.display_name}",
                )
                await db.increment_mute_uses(ctx.author.id, ctx.guild.id)
                await ctx.send(
                    f"🔇 {target.mention} has been muted for "
                    f"**{config.MUTE_DURATION_MINUTES} minutes** "
                    f"by {ctx.author.mention}!"
                )
            except discord.Forbidden:
                await ctx.send(
                    "❌ I don't have permission to mute that player (they may have a higher role)."
                )
            except Exception as e:
                await ctx.send(f"❌ Could not mute: {e}")

        # ── Royal Pass ───────────────────────────────────────
        elif "royal" in item_lower:
            await ctx.send(
                "🎫 Royal Pass used! A mod will process your roadmap level skip."
            )
            await db.remove_inventory_item(ctx.author.id, ctx.guild.id, item_name)

        else:
            await ctx.send(
                f"❓ **{item_name}** doesn't have a use action, or it's applied automatically."
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Shop(bot))