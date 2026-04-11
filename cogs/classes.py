# ============================================================
#  cogs/classes.py — Class system
#
#  Handles:
#    - Class assignment (called from season.py on .join)
#    - All class effect triggers (called from sessions.py on game end)
#    - Level-up (called from shop.py on "Class Level Up" purchase)
#    - Background tasks: Assassin target + Specialist objective (daily 00:00 UTC)
#
#  Sentinel values (stored in DB to mean "completed today, no new task yet"):
#    assassin_target_id      = -1   → target was defeated today
#    specialist_objective_game = "completed" → objective was completed today
#
#  None means "never been assigned" (e.g. just joined / just changed class).
#  Both are reset and a real value assigned each day at 00:00 UTC.
#
#  Commands:
#    .classes        — show info on all classes
#    .collect        — Collector daily collect
#    .target         — show/assign Assassin target
#    .objective      — show/assign Specialist objective
# ============================================================

import random
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord.ext import commands, tasks

import config
import database as db
from cogs.utils import fmt_currency, require_player, channel_only

# Sentinel constants
_ASSASSIN_DONE   = -1
_SPECIALIST_DONE = "completed"


# ── Helpers ───────────────────────────────────────────────────

def _next_midnight_utc() -> datetime:
    """Return the next 00:00 UTC as a timezone-aware datetime."""
    now = datetime.now(timezone.utc)
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def _seconds_until_midnight() -> float:
    return (_next_midnight_utc() - datetime.now(timezone.utc)).total_seconds()


def _fmt_time_until(dt: datetime) -> str:
    """Format time until a future datetime as 'Xh Ym'."""
    remaining = dt - datetime.now(timezone.utc)
    total_secs = max(int(remaining.total_seconds()), 0)
    hours, rem = divmod(total_secs, 3600)
    mins, _ = divmod(rem, 60)
    return f"{hours}h {mins}m" if hours else f"{mins}m"


class Classes(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.daily_cycle.start()

    def cog_unload(self):
        self.daily_cycle.cancel()

    # ════════════════════════════════════════════════════════
    #  CLASS ASSIGNMENT
    # ════════════════════════════════════════════════════════

    @staticmethod
    def assign_random_class() -> str:
        return random.choices(config.CLASS_NAMES, weights=config.CLASS_WEIGHTS, k=1)[0]

    # ════════════════════════════════════════════════════════
    #  BACKGROUND TASK — fires once at 00:00 UTC daily
    # ════════════════════════════════════════════════════════

    @tasks.loop(hours=24)
    async def daily_cycle(self):
        """
        Runs once per day at 00:00 UTC.
        Reassigns Assassin targets and Specialist objectives for all guilds
        and sends a single DM to each affected player.
        """
        await self.bot.wait_until_ready()

        for guild in self.bot.guilds:
            players = await db.get_all_players(guild.id)
            all_ids = [p["user_id"] for p in players]

            assassins   = [p for p in players if p["class"] == "Assassin"]
            specialists = [p for p in players if p["class"] == "Specialist"]

            # ── Reassign Assassin targets ─────────────────────
            for assassin in assassins:
                pool = [uid for uid in all_ids if uid != assassin["user_id"]]
                new_target = random.choice(pool) if pool else None

                await db.update_class_state(
                    assassin["user_id"], guild.id,
                    assassin_target_id=new_target,
                )

                assassin_member = guild.get_member(assassin["user_id"])
                if assassin_member and new_target:
                    target_member = guild.get_member(new_target)
                    target_name = target_member.display_name if target_member else f"<@{new_target}>"
                    try:
                        await assassin_member.send(
                            f"🎯 **New Assassin Target!**\n"
                            f"Your target is **{target_name}**.\n"
                            f"Defeat them in any game to earn your class bonus!\n"
                            f"Next refresh: **00:00 UTC** (in ~24h)"
                        )
                    except discord.Forbidden:
                        pass

            # ── Reassign Specialist objectives ────────────────
            for spec in specialists:
                objective_game = random.choice(config.GAME_NAMES)
                await db.update_class_state(
                    spec["user_id"], guild.id,
                    specialist_objective_game=objective_game,
                )

                member = guild.get_member(spec["user_id"])
                if member:
                    try:
                        await member.send(
                            f"📋 **New Specialist Objective!**\n"
                            f"Win a game of **{objective_game}** today to earn your class bonus!\n"
                            f"Next refresh: **00:00 UTC** (in ~24h)"
                        )
                    except discord.Forbidden:
                        pass

    @daily_cycle.before_loop
    async def before_daily_cycle(self):
        """Sleep until the next 00:00 UTC before the loop starts."""
        await self.bot.wait_until_ready()
        await asyncio.sleep(_seconds_until_midnight())

    # ════════════════════════════════════════════════════════
    #  ON-DEMAND ASSIGNMENT  (called by .target / .objective)
    # ════════════════════════════════════════════════════════

    async def _assign_assassin_target(self, user_id: int, guild: discord.Guild) -> Optional[int]:
        """
        Assign a fresh target for an Assassin whose target_id is None.
        Returns the assigned target user_id, or None if no eligible players exist.
        """
        players = await db.get_all_players(guild.id)
        pool = [p["user_id"] for p in players if p["user_id"] != user_id]
        new_target = random.choice(pool) if pool else None
        await db.update_class_state(user_id, guild.id, assassin_target_id=new_target)
        return new_target

    async def _assign_specialist_objective(self, user_id: int, guild_id: int) -> str:
        """
        Assign a fresh objective for a Specialist whose objective is None.
        Returns the assigned game name.
        """
        objective_game = random.choice(config.GAME_NAMES)
        await db.update_class_state(
            user_id, guild_id,
            specialist_objective_game=objective_game,
        )
        return objective_game

    # ════════════════════════════════════════════════════════
    #  CLASS EFFECT TRIGGERS  (called externally by sessions.py)
    # ════════════════════════════════════════════════════════

    async def on_game_end(
        self,
        guild: discord.Guild,
        winner_id: Optional[int],
        loser_id: Optional[int],
        game_name: str,
        bet_amount: int,
        is_draw: bool,
    ) -> dict[int, int]:
        """
        Compute and pay class bonuses after a game ends.
        Returns a dict of {user_id: bonus_amount} for display.
        """
        bonuses: dict[int, int] = {}

        if is_draw or winner_id is None:
            if winner_id is None and loser_id:
                await self._berserker_reset(loser_id, guild.id)
            return bonuses

        winner_player = await db.get_player(winner_id, guild.id)
        if winner_player is None:
            return bonuses

        cls = winner_player["class"]
        lvl = winner_player["class_level"]

        # ── Assassin ─────────────────────────────────────────
        if cls == "Assassin" and loser_id is not None:
            state = await db.get_class_state(winner_id, guild.id)
            target_id = state.get("assassin_target_id") if state else None
            if target_id == loser_id:  # real match (not None, not -1)
                pct = config.ASSASSIN_BONUS[lvl]
                bonus = int(winner_player["balance"] * pct)
                if bonus > 0:
                    await db.update_balance(winner_id, guild.id, bonus)
                    bonuses[winner_id] = bonus
                # Mark as completed for today with sentinel value
                await db.update_class_state(
                    winner_id, guild.id,
                    assassin_target_id=_ASSASSIN_DONE,
                )

        # ── Specialist ───────────────────────────────────────
        elif cls == "Specialist" and bet_amount > 0:
            state = await db.get_class_state(winner_id, guild.id)
            obj = state.get("specialist_objective_game") if state else None
            if obj == game_name:  # real match (not None, not "completed")
                pct = config.SPECIALIST_BONUS[lvl]
                bonus = int(bet_amount * pct)
                if bonus > 0:
                    await db.update_balance(winner_id, guild.id, bonus)
                    bonuses[winner_id] = bonus
                # Mark as completed for today with sentinel value
                await db.update_class_state(
                    winner_id, guild.id,
                    specialist_objective_game=_SPECIALIST_DONE,
                )

        # ── Berserker ─────────────────────────────────────────
        elif cls == "Berserker" and bet_amount > 0:
            state = await db.get_class_state(winner_id, guild.id)
            streak = (state["berserker_streak"] if state else 0) + 1
            await db.update_class_state(winner_id, guild.id, berserker_streak=streak)

            bonus_tiers = config.BERSERKER_BONUS[lvl]
            if streak == 2:
                pct = bonus_tiers[0]
            elif streak == 3:
                pct = bonus_tiers[1]
            elif streak >= 4:
                pct = bonus_tiers[2]
            else:
                pct = 0.0

            if pct > 0:
                bonus = int(bet_amount * pct)
                if bonus > 0:
                    await db.update_balance(winner_id, guild.id, bonus)
                    bonuses[winner_id] = bonus

        # Loser streak reset
        if loser_id:
            await self._berserker_reset(loser_id, guild.id)

        return bonuses

    async def _berserker_reset(self, user_id: int, guild_id: int) -> None:
        player = await db.get_player(user_id, guild_id)
        if player and player["class"] == "Berserker":
            await db.update_class_state(user_id, guild_id, berserker_streak=0)

    # ════════════════════════════════════════════════════════
    #  LEVEL UP  (called from shop.py)
    # ════════════════════════════════════════════════════════

    async def level_up(self, user_id: int, guild_id: int) -> tuple[bool, str]:
        """
        Attempt to level up the player's class.
        Returns (success: bool, message: str).
        """
        player = await db.get_player(user_id, guild_id)
        if not player:
            return False, "You haven't joined the season."

        current_level = player["class_level"]
        if current_level >= config.MAX_CLASS_LEVEL:
            return False, f"Your class is already at the maximum level ({config.MAX_CLASS_LEVEL})."

        cost = config.CLASS_LEVELUP_COSTS.get(current_level)
        if cost is None:
            return False, "Invalid level state."

        if player["balance"] < cost:
            settings = await db.get_guild_settings(guild_id)
            needed = fmt_currency(cost, settings["currency_emoji"])
            return False, f"You need {needed} to level up. You don't have enough."

        await db.update_balance(user_id, guild_id, -cost)
        await db.set_class_level(user_id, guild_id, current_level + 1)

        settings = await db.get_guild_settings(guild_id)
        cost_str = fmt_currency(cost, settings["currency_emoji"])
        return True, (
            f"⬆️ **{player['class']}** leveled up to **Level {current_level + 1}**! "
            f"({cost_str} deducted)"
        )

    # ════════════════════════════════════════════════════════
    #  COMMANDS
    # ════════════════════════════════════════════════════════

    @commands.command(name="classes")
    async def classes_info(self, ctx: commands.Context):
        """Show info on all available classes."""
        embed = discord.Embed(
            title="📚 Classes",
            description="When you join the season, you're assigned a random class. "
                        "Level up by purchasing **Class Level Up** in the shop.",
            color=discord.Color.blurple(),
        )

        embed.add_field(name="🗡️ Assassin",
            value=(
                "Gets a new target every day at 00:00 UTC. Defeating your target earns a bonus.\n"
                "Lv1: +2% | Lv2: +4% | Lv3: +6% | Lv4: +8% of your **balance**"
            ), inline=False)

        embed.add_field(name="🎲 Specialist",
            value=(
                "Gets a daily game objective at 00:00 UTC. Winning that game earns a bonus.\n"
                "Lv1: +5% | Lv2: +10% | Lv3: +15% | Lv4: +20% of your **bet**"
            ), inline=False)

        embed.add_field(name="💰 Collector",
            value=(
                "Use `.collect` once every 24h to earn free carats.\n"
                "Lv1: +1% | Lv2: +2% | Lv3: +3% | Lv4: +4% of your **balance**"
            ), inline=False)

        embed.add_field(name="⚔️ Berserker",
            value=(
                "Earn bonus carats for consecutive wins (resets on loss/draw).\n"
                "**2nd win:** Lv1: +1% | Lv2: +2% | Lv3: +3% | Lv4: +4%\n"
                "**3rd win:** Lv1: +2% | Lv2: +4% | Lv3: +6% | Lv4: +8%\n"
                "**4th+ win:** Lv1: +3% | Lv2: +6% | Lv3: +9% | Lv4: +12% of your **bet**"
            ), inline=False)

        embed.set_footer(text="Level up costs: Lv1→2: 250k | Lv2→3: 3M | Lv3→4: 6M")
        await ctx.send(embed=embed)

    @commands.command(name="collect")
    @channel_only("economy")
    @require_player()
    async def collect(self, ctx: commands.Context):
        """Collector class: collect carats once every 24 hours."""
        player = await db.get_player(ctx.author.id, ctx.guild.id)
        if player["class"] != "Collector":
            await ctx.send("❌ Only **Collector** class players can use `.collect`.")
            return

        state = await db.get_class_state(ctx.author.id, ctx.guild.id)
        last = state.get("collector_last_used") if state else None
        if last:
            last_dt = datetime.fromisoformat(last)
            next_collect = last_dt + timedelta(hours=config.COLLECTOR_COOLDOWN_HOURS)
            now = datetime.utcnow()
            if now < next_collect:
                remaining = next_collect - now
                hours, rem = divmod(int(remaining.total_seconds()), 3600)
                mins, _ = divmod(rem, 60)
                await ctx.send(
                    f"⏳ You already collected today. Come back in **{hours}h {mins}m**."
                )
                return

        pct = config.COLLECTOR_BONUS[player["class_level"]]
        bonus = max(int(player["balance"] * pct), 100)

        settings = await db.get_guild_settings(ctx.guild.id)
        emoji = settings["currency_emoji"]

        await db.update_balance(ctx.author.id, ctx.guild.id, bonus)
        await db.update_class_state(
            ctx.author.id, ctx.guild.id,
            collector_last_used=datetime.utcnow().isoformat()
        )

        await ctx.send(
            f"💰 {ctx.author.mention} collected "
            f"{fmt_currency(bonus, emoji)}! "
            f"*(+{pct*100:.0f}% of balance)*"
        )

    @commands.command(name="target")
    @require_player()
    async def target(self, ctx: commands.Context):
        """Assassin class: show your current target, or receive one if you don't have one yet."""
        player = await db.get_player(ctx.author.id, ctx.guild.id)
        if player["class"] != "Assassin":
            await ctx.send("❌ Only **Assassin** class players have targets.")
            return

        state = await db.get_class_state(ctx.author.id, ctx.guild.id)
        target_id = state.get("assassin_target_id") if state else None
        next_refresh = _next_midnight_utc()
        time_left = _fmt_time_until(next_refresh)

        if target_id is None:
            # First time ever (or just changed class) — auto-assign
            target_id = await self._assign_assassin_target(ctx.author.id, ctx.guild)
            if not target_id:
                await ctx.send(
                    "🎯 No target could be assigned — there are no other players in the season yet.\n"
                    f"⏰ Check again after the next reset (in {time_left})."
                )
                return
            member = ctx.guild.get_member(target_id)
            name = member.mention if member else f"<@{target_id}>"
            await ctx.send(
                f"🎯 Your Assassin target has been assigned: {name}\n"
                f"⏰ Next refresh: **00:00 UTC** (in {time_left})"
            )

        elif target_id == _ASSASSIN_DONE:
            await ctx.send(
                f"✅ You've already taken down your target for today. "
                f"Your next target will be assigned at the daily reset.\n"
                f"⏰ Next refresh: **00:00 UTC** (in {time_left})"
            )

        else:
            member = ctx.guild.get_member(target_id)
            name = member.mention if member else f"<@{target_id}>"
            await ctx.send(
                f"🎯 Your current Assassin target is: {name}\n"
                f"⏰ Next refresh: **00:00 UTC** (in {time_left})"
            )

    @commands.command(name="objective")
    @require_player()
    async def objective(self, ctx: commands.Context):
        """Specialist class: show your current objective, or receive one if you don't have one yet."""
        player = await db.get_player(ctx.author.id, ctx.guild.id)
        if player["class"] != "Specialist":
            await ctx.send("❌ Only **Specialist** class players have objectives.")
            return

        state = await db.get_class_state(ctx.author.id, ctx.guild.id)
        obj_game = state.get("specialist_objective_game") if state else None
        next_refresh = _next_midnight_utc()
        time_left = _fmt_time_until(next_refresh)

        if obj_game is None:
            # First time ever (or just changed class) — auto-assign
            obj_game = await self._assign_specialist_objective(ctx.author.id, ctx.guild.id)
            await ctx.send(
                f"📋 Your Specialist objective has been assigned: win a game of **{obj_game}**!\n"
                f"⏰ Next refresh: **00:00 UTC** (in {time_left})"
            )

        elif obj_game == _SPECIALIST_DONE:
            await ctx.send(
                f"✅ You've already completed your objective for today. "
                f"A new one will be assigned at the daily reset.\n"
                f"⏰ Next refresh: **00:00 UTC** (in {time_left})"
            )

        else:
            await ctx.send(
                f"📋 **Specialist Objective:** Win a game of **{obj_game}**\n"
                f"Status: ❌ Not yet completed\n"
                f"⏰ Next refresh: **00:00 UTC** (in {time_left})"
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Classes(bot))