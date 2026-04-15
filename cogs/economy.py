# ============================================================
#  cogs/economy.py — Economy commands
#
#  Commands:
#    .bal [player]                       — show balance
#    .give @p amt                        — transfer carats
#    .work                               — earn carats (cooldown)
#    .lb                                 — richest players leaderboard
#    .deposit {amount} for {N}d          — create a deposit with interest
#    .mydeposit                          — check your active deposit
# ============================================================

import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta, timezone
from typing import Optional

import config
import database as db
from cogs.utils import (
    fmt_currency, require_player, channel_only, has_mod_role, parse_amount
)

DEPOSIT_INTEREST_RATE_PER_DAY = 0.015   # 1.5% per day
DEPOSIT_MAX_DAYS = 20


class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._deposit_payout_task.start()

    def cog_unload(self):
        self._deposit_payout_task.cancel()

    # ── Background task: pay out matured deposits ────────────

    @tasks.loop(minutes=5)
    async def _deposit_payout_task(self):
        """Every 5 minutes: find matured deposits and pay them out."""
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            deposits = await db.get_all_mature_deposits(guild.id)
            if not deposits:
                continue

            settings = await db.get_guild_settings(guild.id)
            emoji  = settings["currency_emoji"]
            name_s = settings["currency_name"]
            name_p = settings["currency_name_plural"]

            # Find a notification channel
            notif_channel = None
            restricted_id = await db.get_channel_restriction(guild.id, "economy")
            if restricted_id:
                notif_channel = guild.get_channel(restricted_id)
            if not notif_channel:
                notif_channel = next(
                    (c for c in guild.text_channels
                     if c.permissions_for(guild.me).send_messages),
                    None
                )

            for dep in deposits:
                user_id = dep["user_id"]
                member  = guild.get_member(user_id)
                if member is None:
                    await db.delete_deposit(user_id, guild.id)
                    continue

                interest     = int(dep["amount"] * dep["interest_rate"])
                total_return = dep["amount"] + interest

                await db.update_balance(user_id, guild.id, total_return)
                await db.delete_deposit(user_id, guild.id)

                if notif_channel:
                    await notif_channel.send(
                        f"🏦 {member.mention} Your deposit has matured! "
                        f"You received {fmt_currency(total_return, emoji)} "
                        f"({fmt_currency(dep['amount'], emoji)} + "
                        f"{dep['interest_rate']*100:.1f}% interest)."
                    )

    # ── .bal ─────────────────────────────────────────────────

    @commands.command(name="bal", aliases=["balance"])
    @channel_only("economy")
    async def bal(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """Show your balance or another player's balance."""
        target = member or ctx.author
        player = await db.get_player(target.id, ctx.guild.id)

        if player is None:
            if target == ctx.author:
                await ctx.send("❌ You haven't joined the season yet! Use `.join` to start.")
            else:
                await ctx.send(f"❌ {target.display_name} hasn't joined the season.")
            return

        settings = await db.get_guild_settings(ctx.guild.id)
        emoji  = settings["currency_emoji"]
        name_s = settings["currency_name"]
        name_p = settings["currency_name_plural"]

        embed = discord.Embed(
            title=f"{target.display_name}'s Balance",
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="Balance",
            value=fmt_currency(player["balance"], emoji),
            inline=False,
        )
        embed.add_field(
            name="Class",
            value=f"{player['class']} (Level {player['class_level']})" if player["class"] else "None",
            inline=True,
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        await ctx.send(embed=embed)

    # ── .give ────────────────────────────────────────────────

    @commands.command(name="give")
    @channel_only("economy")
    @require_player()
    async def give(self, ctx: commands.Context, member: discord.Member, raw_amount: str):
        """Give carats to another player. Accepts scientific notation: 1e6, 2.5e3."""
        amount = parse_amount(raw_amount)
        if amount is None:
            await ctx.send("❌ Invalid amount. Examples: `5000`, `1e6`, `2.5e3`")
            return
        if member.bot:
            await ctx.send("❌ You can't give carats to a bot.")
            return
        if member == ctx.author:
            await ctx.send("❌ You can't give carats to yourself.")
            return
        if amount <= 0:
            await ctx.send("❌ Amount must be positive.")
            return

        sender = await db.get_player(ctx.author.id, ctx.guild.id)
        if sender["balance"] < amount:
            await ctx.send(
                f"❌ You don't have enough carats. "
                f"Your balance: **{sender['balance']:,}**"
            )
            return

        recipient = await db.get_player(member.id, ctx.guild.id)
        if recipient is None:
            await ctx.send(f"❌ {member.display_name} hasn't joined the season.")
            return

        settings = await db.get_guild_settings(ctx.guild.id)
        emoji  = settings["currency_emoji"]
        name_s = settings["currency_name"]
        name_p = settings["currency_name_plural"]

        await db.update_balance(ctx.author.id, ctx.guild.id, -amount)
        await db.update_balance(member.id, ctx.guild.id, amount)

        await ctx.send(
            f"✅ {ctx.author.mention} gave "
            f"{fmt_currency(amount, emoji)} "
            f"to {member.mention}!"
        )

    # ── .work ────────────────────────────────────────────────

    @commands.command(name="work")
    @channel_only("economy")
    @require_player()
    async def work(self, ctx: commands.Context):
        """Earn carats. Can only be used once per cooldown period."""
        settings         = await db.get_guild_settings(ctx.guild.id)
        cooldown_minutes = settings["work_cooldown_minutes"]
        work_amount      = settings["work_amount"]
        emoji  = settings["currency_emoji"]

        last_work = await db.get_last_work(ctx.author.id, ctx.guild.id)
        if last_work is not None:
            next_work = last_work + timedelta(minutes=cooldown_minutes)
            now = datetime.utcnow()
            if now < next_work:
                remaining = next_work - now
                hours, remainder = divmod(int(remaining.total_seconds()), 3600)
                minutes, seconds = divmod(remainder, 60)
                time_str = f"{hours}h {minutes}m {seconds}s" if hours else f"{minutes}m {seconds}s"
                await ctx.send(f"⏳ You already worked recently. Try again in **{time_str}**.")
                return

        await db.update_balance(ctx.author.id, ctx.guild.id, work_amount)
        await db.set_last_work(ctx.author.id, ctx.guild.id)
        await ctx.send(
            f"💼 {ctx.author.mention} worked hard and earned "
            f"{fmt_currency(work_amount, emoji)}!"
        )

    # ── .lb ──────────────────────────────────────────────────

    @commands.command(name="lb", aliases=["leaderboard"])
    @channel_only("economy")
    async def lb(self, ctx: commands.Context):
        """Show the richest players on the server (paginated, 10 per page)."""
        settings = await db.get_guild_settings(ctx.guild.id)
        emoji  = settings["currency_emoji"]

        players = await db.get_leaderboard(ctx.guild.id)
        if not players:
            await ctx.send("📭 No players have joined the season yet!")
            return

        # Find the author's rank (1-based); None if not in the list
        author_rank = next(
            (i + 1 for i, p in enumerate(players) if p["user_id"] == ctx.author.id),
            None,
        )

        medals = ["🥇", "🥈", "🥉"]
        per_page = 10
        total_pages = max(1, (len(players) + per_page - 1) // per_page)

        def _ordinal(n: int) -> str:
            if 11 <= (n % 100) <= 13:
                return f"{n}th"
            return f"{n}{['th','st','nd','rd','th'][min(n % 10, 4)]}"

        def _make_page(page_index: int) -> discord.Embed:
            start = page_index * per_page
            chunk = players[start: start + per_page]

            lines = []
            for i, p in enumerate(chunk):
                global_rank = start + i + 1
                member = ctx.guild.get_member(p["user_id"])
                name   = member.display_name if member else f"User {p['user_id']}"
                medal  = medals[global_rank - 1] if global_rank <= 3 else f"`{global_rank}.`"
                balance_str = fmt_currency(p["balance"], emoji)
                class_str   = f"*{p['class']} Lv.{p['class_level']}*" if p["class"] else ""
                lines.append(f"{medal} **{name}** — {balance_str}  {class_str}")

            rank_str = (
                f"Your rank: {_ordinal(author_rank)}"
                if author_rank is not None
                else "You haven't joined the season"
            )
            footer = f"Page {page_index + 1}/{total_pages} • {rank_str}"

            embed = discord.Embed(
                title=f"Leaderboard — Season {settings['current_season']}",
                description="\n".join(lines),
                color=discord.Color.gold(),
            )
            embed.set_footer(text=footer)
            return embed

        pages = [_make_page(i) for i in range(total_pages)]

        if len(pages) == 1:
            await ctx.send(embed=pages[0])
        else:
            from cogs.utils import PaginatedView
            view = PaginatedView(pages, author_id=ctx.author.id)
            await ctx.send(embed=pages[0], view=view)

    # ── .deposit ─────────────────────────────────────────────

    @commands.command(name="deposit")
    @channel_only("economy")
    @require_player()
    async def deposit(
        self, ctx: commands.Context,
        raw_amount: str,
        for_keyword: str,
        days_str: str,
    ):
        """
        Create a deposit. Locks carats and returns them with interest after N days.
        Usage:    .deposit {amount} for {N}d
        Example:  .deposit 1e6 for 7d
        Interest = 1.5% x number of days. Maximum 20 days. One deposit at a time.
        """

        settings = await db.get_guild_settings(ctx.guild.id)
        emoji = settings["currency_emoji"]

        if for_keyword.lower() != "for":
            await ctx.send("❌ Usage: `.deposit {amount} for {N}d`  e.g. `.deposit 1e6 for 7d`")
            return

        amount = parse_amount(raw_amount)
        if amount is None or amount <= 0:
            await ctx.send("❌ Invalid amount. Examples: `5000`, `1e6`, `2.5e3`")
            return

        if not days_str.lower().endswith("d"):
            await ctx.send("❌ Days must be written like `7d`, `14d`, etc.")
            return
        try:
            days = int(days_str[:-1])
        except ValueError:
            await ctx.send("❌ Invalid number of days. Example: `7d`")
            return

        if days < 1:
            await ctx.send("❌ Deposit must be for at least 1 day.")
            return
        if days > DEPOSIT_MAX_DAYS:
            await ctx.send(f"❌ Maximum deposit length is **{DEPOSIT_MAX_DAYS} days**.")
            return

        existing = await db.get_deposit(ctx.author.id, ctx.guild.id)
        if existing:
            matures   = datetime.fromisoformat(existing["matures_at"])
            remaining = matures - datetime.utcnow()
            hours, rem = divmod(int(max(remaining.total_seconds(), 0)), 3600)
            mins, _    = divmod(rem, 60)
            await ctx.send(
                f"❌ You already have an active deposit of {fmt_currency(existing['amount'], emoji)} "
                f"for **{existing['days']} days**. "
                f"It matures in **{hours}h {mins}m**."
            )
            return

        player = await db.get_player(ctx.author.id, ctx.guild.id)
        if player["balance"] < amount:
            await ctx.send(
                f"❌ You don't have enough carats. "
                f"Your balance: **{player['balance']:,}**"
            )
            return

        rate         = DEPOSIT_INTEREST_RATE_PER_DAY * days
        interest     = int(amount * rate)
        total_return = amount + interest
        matures_at   = datetime.now(timezone.utc) + timedelta(days=days)

        await db.update_balance(ctx.author.id, ctx.guild.id, -amount)
        await db.create_deposit(ctx.author.id, ctx.guild.id, amount, days, rate)

        embed = discord.Embed(title="🏦 Deposit Created", color=discord.Color.green())
        embed.add_field(name="Deposited", value=fmt_currency(amount,       emoji,               ), inline=True)
        embed.add_field(name="Duration",  value=f"**{days}** day(s)",                              inline=True)
        embed.add_field(name="Interest",  value=f"+{rate*100:.1f}%",                               inline=True)
        embed.add_field(name="Returns",   value=fmt_currency(total_return, emoji,               ), inline=True)
        embed.add_field(name="Matures",   value=f"<t:{int(matures_at.timestamp())}:R>",            inline=True)
        embed.set_footer(text="You'll be notified in this channel when your deposit matures.")
        await ctx.send(embed=embed)

    # ── .mydeposit ───────────────────────────────────────────

    @commands.command(name="mydeposit", aliases=["dep"])
    @require_player()
    async def mydeposit(self, ctx: commands.Context):
        """Check your current active deposit."""
        dep = await db.get_deposit(ctx.author.id, ctx.guild.id)
        if not dep:
            await ctx.send(
                "📭 You have no active deposit. "
                "Use `.deposit {amount} for {N}d` to create one."
            )
            return

        settings = await db.get_guild_settings(ctx.guild.id)
        emoji  = settings["currency_emoji"]

        interest     = int(dep["amount"] * dep["interest_rate"])
        total_return = dep["amount"] + interest
        matures      = datetime.fromisoformat(dep["matures_at"]).replace(tzinfo=timezone.utc)
        now          = datetime.now(timezone.utc)

        embed = discord.Embed(title="🏦 Your Active Deposit", color=discord.Color.blurple())
        embed.add_field(name="Deposited", value=fmt_currency(dep["amount"], emoji), inline=True)
        embed.add_field(name="Duration",  value=f"**{dep['days']}** day(s)",                        inline=True)
        embed.add_field(name="Interest",  value=f"+{dep['interest_rate']*100:.1f}%",                inline=True)
        embed.add_field(name="Returns",   value=fmt_currency(total_return, emoji),  inline=True)
        if matures > now:
            embed.add_field(name="Matures", value=f"<t:{int(matures.timestamp())}:R>", inline=True)
        else:
            embed.add_field(name="Status", value="✅ Matured — payout pending!", inline=True)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Economy(bot))