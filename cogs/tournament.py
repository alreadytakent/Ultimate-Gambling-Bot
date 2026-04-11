# ============================================================
#  cogs/tournament.py — Tournament system
#
#  Triggered when 8 Tournament Tickets are purchased.
#  Single-elimination bracket. Winner wins the season.
#  Tournament starts 24h after the 8th ticket is purchased.
# ============================================================

import asyncio
import random
from datetime import datetime, timedelta
from typing import Optional

import discord
from discord.ext import commands

import config
import database as db
from cogs.utils import fmt_currency


class Tournament(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # In-memory tournament state (could be persisted later)
        # Maps guild_id → TournamentState
        self._tournaments: dict[int, "TournamentState"] = {}

    async def on_ticket_purchased(self, buyer: discord.Member, guild: discord.Guild) -> str:
        """
        Called by shop.py when a Tournament Ticket is bought.
        Returns a status message string.
        """
        state = self._tournaments.get(guild.id)
        if state is None:
            state = TournamentState()
            self._tournaments[guild.id] = state

        if buyer.id in state.ticket_holders:
            return "*(You already hold a ticket!)*"

        state.ticket_holders.append(buyer.id)
        tickets_sold = len(state.ticket_holders)
        remaining = config.TOURNAMENT_TICKET_COUNT - tickets_sold

        if remaining > 0:
            return f"🎫 Ticket purchased! **{tickets_sold}/{config.TOURNAMENT_TICKET_COUNT}** tickets sold. {remaining} remaining."

        # All tickets sold — schedule tournament start
        if not state.start_scheduled:
            state.start_scheduled = True
            state.start_time = datetime.utcnow() + timedelta(hours=config.TOURNAMENT_START_DELAY_HOURS)
            asyncio.create_task(self._schedule_tournament(guild, state))

            hours = config.TOURNAMENT_START_DELAY_HOURS
            return (
                f"🏆 **All {config.TOURNAMENT_TICKET_COUNT} tickets sold!** "
                f"The tournament begins in **{hours} hour(s)**. "
                f"Use this time to bargain and trade!"
            )

        return ""

    async def _schedule_tournament(self, guild: discord.Guild, state: "TournamentState") -> None:
        """Wait for the delay then start the tournament."""
        delay = (state.start_time - datetime.utcnow()).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)

        # Find an appropriate channel (first text channel the bot can send to)
        channel = next(
            (c for c in guild.text_channels if c.permissions_for(guild.me).send_messages),
            None
        )
        if not channel:
            return

        await self._run_tournament(guild, channel, state)

    async def _run_tournament(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        state: "TournamentState",
    ) -> None:
        """Run the single-elimination bracket."""
        participants = [guild.get_member(uid) for uid in state.ticket_holders]
        participants = [m for m in participants if m is not None]

        if len(participants) < 2:
            await channel.send("⚠️ Tournament cancelled: not enough participants.")
            self._tournaments.pop(guild.id, None)
            return

        # Shuffle for random bracket
        random.shuffle(participants)

        embed = discord.Embed(
            title="🏆 Tournament Begins!",
            description=(
                f"**{len(participants)} players** compete in a single-elimination bracket!\n"
                "The winner claims the season! Games will be announced round by round.\n\n"
                "**Participants:**\n" +
                "\n".join(f"• {m.mention}" for m in participants)
            ),
            color=discord.Color.gold(),
        )
        await channel.send(embed=embed)

        current_round = participants[:]
        round_num = 1

        while len(current_round) > 1:
            await asyncio.sleep(2)
            round_embed = discord.Embed(
                title=f"🔔 Round {round_num}",
                color=discord.Color.orange(),
            )
            matchups = []
            # Pair up players; if odd, last player gets a bye
            random.shuffle(current_round)
            bye_player = None
            if len(current_round) % 2 == 1:
                bye_player = current_round.pop()

            pairs = [(current_round[i], current_round[i+1]) for i in range(0, len(current_round), 2)]
            matchup_lines = []
            for p1, p2 in pairs:
                matchup_lines.append(f"⚔️ {p1.mention} **vs** {p2.mention}")
            if bye_player:
                matchup_lines.append(f"🛡️ {bye_player.mention} — **BYE** (advances automatically)")

            round_embed.description = "\n".join(matchup_lines)
            round_embed.set_footer(
                text="Referees: use .verify to report each match result. "
                     "Use .tournament-advance @winner to progress a matchup."
            )
            await channel.send(embed=round_embed)

            # Store pending matchups for this round
            state.pending_matchups = {(p1.id, p2.id): None for p1, p2 in pairs}
            state.current_round_winners = [bye_player] if bye_player else []
            state.round_num = round_num

            # Wait for all matchups to be resolved (via .tournament-advance)
            while len(state.pending_matchups) > 0:
                await asyncio.sleep(5)

            current_round = state.current_round_winners[:]
            round_num += 1

        # We have a winner
        if current_round:
            champion = current_round[0]
            champion_embed = discord.Embed(
                title="🏆 Tournament Champion!",
                description=(
                    f"**{champion.mention}** has won the tournament and claims the season! 🎉"
                ),
                color=discord.Color.gold(),
            )
            await channel.send(embed=champion_embed)

            settings = await db.get_guild_settings(guild.id)
            season = settings["current_season"]
            await db.record_result(
                game_name="Tournament",
                player1_id=champion.id,
                player2_id=None,
                winner_id=champion.id,
                is_draw=False,
                guild_id=guild.id,
                season_number=season,
                bet_amount=0,
                verified_by_referee=True,
            )

        self._tournaments.pop(guild.id, None)

    @commands.command(name="start-tournament")
    async def start_tournament(self, ctx: commands.Context):
        """
        Mod: Start the tournament immediately, bypassing the usual time delay.
        Requires at least 2 players to hold a ticket.
        """
        # Mod role check inline (avoid circular import of has_mod_role decorator)
        mod_role = discord.utils.get(ctx.guild.roles, name=config.MOD_ROLE_NAME)
        if not mod_role or mod_role not in ctx.author.roles:
            await ctx.send(f"❌ You need the **{config.MOD_ROLE_NAME}** role to use this command.")
            return

        state = self._tournaments.get(ctx.guild.id)
        if state is None or len(state.ticket_holders) < 2:
            count = len(state.ticket_holders) if state else 0
            await ctx.send(
                f"❌ Not enough ticket holders to start a tournament. "
                f"Currently **{count}** player(s) hold a ticket (minimum 2)."
            )
            return

        if state.start_scheduled:
            # Already counting down — cancel the scheduled task and fire now
            await ctx.send(
                f"⚡ Overriding the scheduled start — launching the tournament immediately "
                f"with **{len(state.ticket_holders)}** player(s)!"
            )
        else:
            await ctx.send(
                f"⚡ Starting the tournament immediately with "
                f"**{len(state.ticket_holders)}** player(s)!"
            )

        # Mark as scheduled so any existing countdown task doesn't also fire
        state.start_scheduled = True
        state.start_time = datetime.utcnow()   # "now" — no more waiting

        channel = ctx.channel
        # Run tournament in the background so the command returns immediately
        asyncio.create_task(self._run_tournament(ctx.guild, channel, state))

    @commands.command(name="tournament-advance")
    async def tournament_advance(self, ctx: commands.Context, winner: discord.Member):
        """
        Referee/Mod: advance a tournament matchup by declaring the winner.
        Usage: .tournament-advance @winner
        """
        state = self._tournaments.get(ctx.guild.id)
        if state is None or not state.pending_matchups:
            await ctx.send("❌ No active tournament round waiting for results.")
            return

        # Find the matchup containing the winner
        matched_key = None
        loser_id = None
        for (p1_id, p2_id) in list(state.pending_matchups.keys()):
            if winner.id in (p1_id, p2_id):
                matched_key = (p1_id, p2_id)
                loser_id = p2_id if winner.id == p1_id else p1_id
                break

        if matched_key is None:
            await ctx.send(
                f"❌ {winner.mention} is not in any pending matchup this round."
            )
            return

        del state.pending_matchups[matched_key]
        state.current_round_winners.append(winner)

        loser = ctx.guild.get_member(loser_id)
        loser_name = loser.mention if loser else f"<@{loser_id}>"
        await ctx.send(
            f"✅ **{winner.mention}** advances! {loser_name} is eliminated. "
            f"({len(state.pending_matchups)} matchup(s) remaining this round)"
        )

    @commands.command(name="tournament-status")
    async def tournament_status(self, ctx: commands.Context):
        """Check the current tournament status."""
        state = self._tournaments.get(ctx.guild.id)
        if state is None:
            await ctx.send("📭 No tournament is currently active.")
            return

        if not state.start_scheduled:
            tickets = len(state.ticket_holders)
            remaining = config.TOURNAMENT_TICKET_COUNT - tickets
            await ctx.send(
                f"🎫 **{tickets}/{config.TOURNAMENT_TICKET_COUNT}** tickets sold. "
                f"{remaining} more needed to start the tournament."
            )
            return

        now = datetime.utcnow()
        if state.start_time and state.start_time > now:
            remaining = state.start_time - now
            hours, rem = divmod(int(remaining.total_seconds()), 3600)
            mins, _ = divmod(rem, 60)
            await ctx.send(
                f"⏳ Tournament starts in **{hours}h {mins}m**.\n"
                f"Participants: {', '.join(f'<@{uid}>' for uid in state.ticket_holders)}"
            )
        else:
            pending = len(state.pending_matchups) if state.pending_matchups else 0
            await ctx.send(
                f"🏆 **Round {state.round_num}** in progress. "
                f"**{pending}** matchup(s) awaiting results."
            )


class TournamentState:
    def __init__(self):
        self.ticket_holders: list[int] = []
        self.start_scheduled: bool = False
        self.start_time: Optional[datetime] = None
        self.pending_matchups: dict = {}
        self.current_round_winners: list = []
        self.round_num: int = 0


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Tournament(bot))