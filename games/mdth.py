# ============================================================
#  games/mdth.py — Manga Accurate Drop the Handkerchief (MDTH)
#
#  1v1, no draws.
#
#  Rules (differ from DTH):
#    - Each player has TWO vessels: Main and Accumulated
#    - Successful check (C >= D): penalty goes to Accumulated vessel only
#    - Failed check (C < D):      penalty = 60 goes to Accumulated,
#                                 then ALL of Accumulated transfers to Main
#    - Player loses if EITHER vessel reaches 300
#    - Leap second on round 18 (allows 61)
# ============================================================

from typing import Optional
import discord
import random

from games.base_game import BaseGame, GameResult


class MDTHGame(BaseGame):
    game_name = "Manga Accurate DTH"
    can_draw = False
    is_ffa = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if random.choice([True, False]):
            self.dropper = self.players[0]
            self.checker = self.players[1]
        else:
            self.dropper = self.players[1]
            self.checker = self.players[0]

        self.dropped_number: Optional[int] = None
        self.pending_check: Optional[tuple] = None   # (member, check_time)
        self.main_vessel:        dict[int, int] = {p.id: 0 for p in self.players}
        self.accumulated_vessel: dict[int, int] = {p.id: 0 for p in self.players}
        self.max_penalty = 300
        self.round = 1

    # ── start ────────────────────────────────────────────────

    async def start(self) -> None:
        bet_str = f" | Bet: **{self.bet_amount:,}**" if self.bet_amount > 0 else ""

        embed = discord.Embed(
            title="Manga Accurate Drop the Handkerchief",
            description=(
                f"{self.players[0].mention} **vs** {self.players[1].mention}{bet_str}\n\n"
                f"Check your DMs for instructions!\n"
                f"{self.dropper.mention} is the Dropper first."
            ),
            color=discord.Color.dark_blue(),
        )
        await self.send(embed=embed)

        instructions = (
            "**Manga Accurate Drop the Handkerchief Game Started!**\n\n"
            "**How to play:**\n"
            "- Dropper: `.drop [1-60]` - choose when to drop the handkerchief (seconds)\n"
            "- Checker: `.check [1-60]` - choose when to look back (seconds)\n"
            "- If check is successful (C ≥ D): penalty = C - D (goes to accumulated vessel)\n"
            "- If check fails (C < D): penalty = 60 (goes to accumulated vessel, then ALL accumulated transfers to main vessel)\n"
            "- Players have 2 vessels: Main and Accumulated\n"
            "- If EITHER vessel reaches 300 seconds, player loses!\n\n"
        )

        for player in self.players:
            opponent = self.players[1] if player == self.players[0] else self.players[0]
            role_line = f"{self.dropper.mention} is the dropper first!"
            await self.dm_player(
                player,
                instructions +
                f"You're playing against {opponent.mention}\n"
                f"{role_line}"
            )

    # ── handle_message ───────────────────────────────────────

    async def handle_message(
        self,
        user_id: int,
        content: str,
        is_dm: bool,
        message: Optional[discord.Message] = None,
    ) -> Optional[GameResult]:
        if not is_dm:
            return None

        content = content.strip()
        lower = content.lower()

        if lower.startswith(".drop "):
            return await self._handle_drop(user_id, content, message)
        elif lower.startswith(".check "):
            return await self._handle_check(user_id, content, message)

        return None

    # ── .drop ─────────────────────────────────────────────────

    async def _handle_drop(
        self, user_id: int, content: str, message: Optional[discord.Message]
    ) -> Optional[GameResult]:
        member = self.get_member(user_id)

        if member != self.dropper:
            await self.dm_player(member, "❌ It's not your turn to drop! Wait for your role as checker.")
            return None

        parts = content.split()
        if len(parts) < 2:
            await self.dm_player(member, "❌ Usage: `.drop [1-60]`")
            return None

        try:
            number = int(parts[1])
        except ValueError:
            await self.dm_player(member, "❌ Please provide a valid number.")
            return None

        max_n = 61 if self.round == 18 else 60
        if not (1 <= number <= max_n):
            await self.dm_player(member, f"❌ Number must be between 1 and {max_n} seconds!")
            return None

        self.dropped_number = number

        # Snapshot and claim pending_check BEFORE yielding to the event loop.
        pending = self.pending_check
        if pending is not None:
            self.pending_check = None

        if message:
            try:
                await message.add_reaction('✅')
            except Exception:
                pass

        if pending is not None:
            _, check_time = pending
            return await self._resolve_round(check_time)

        return None

    # ── .check ────────────────────────────────────────────────

    async def _handle_check(
        self, user_id: int, content: str, message: Optional[discord.Message]
    ) -> Optional[GameResult]:
        member = self.get_member(user_id)

        if member != self.checker:
            await self.dm_player(member, "❌ It's not your turn to check! Wait for your role as dropper.")
            return None

        parts = content.split()
        if len(parts) < 2:
            await self.dm_player(member, "❌ Usage: `.check [1-60]`")
            return None

        try:
            check_time = int(parts[1])
        except ValueError:
            await self.dm_player(member, "❌ Please provide a valid number.")
            return None

        max_n = 61 if self.round == 18 else 60
        if not (1 <= check_time <= max_n):
            await self.dm_player(member, f"❌ Check time must be between 1 and {max_n} seconds!")
            return None

        # Snapshot dropped_number BEFORE yielding to the event loop.
        already_dropped = self.dropped_number

        if message:
            try:
                await message.add_reaction('✅')
            except Exception:
                pass

        if already_dropped is not None:
            return await self._resolve_round(check_time)

        self.pending_check = (member, check_time)
        return None

    # ── Round resolution ──────────────────────────────────────

    async def _resolve_round(self, check_time: int) -> Optional[GameResult]:
        drop_time = self.dropped_number

        if check_time >= drop_time:
            penalty = check_time - drop_time
            result_type = "SUCCESSFUL CHECK"
            self.accumulated_vessel[self.checker.id] += penalty
        else:
            penalty = 60
            result_type = "FAILED CHECK"
            self.accumulated_vessel[self.checker.id] += penalty
            # Transfer ALL accumulated to main vessel
            transfer = self.accumulated_vessel[self.checker.id]
            self.main_vessel[self.checker.id] += transfer
            self.accumulated_vessel[self.checker.id] = 0

        p1, p2 = self.players[0], self.players[1]
        result_msg = (
            f"**===== Manga Accurate DTH - Round {self.round} =====**\n"
            f"~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~\n"
            f"{self.dropper.mention} dropped: {drop_time}\n"
            f"{self.checker.mention} checked: {check_time}\n"
            f"~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~\n"
            f"**RESULT: {result_type}**\n"
            f"{self.checker.mention} accumulated: {penalty}\n"
            f"~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~\n"
            f"{p1.mention} ({self.main_vessel[p1.id]}/{self.max_penalty})\n"
            f"Vessel: {self.accumulated_vessel[p1.id]}/{self.max_penalty}\n"
            f"~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~\n"
            f"{p2.mention} ({self.main_vessel[p2.id]}/{self.max_penalty})\n"
            f"Vessel: {self.accumulated_vessel[p2.id]}/{self.max_penalty}"
        )

        # Check for game over — either vessel hits max
        checker_lost = (
            self.main_vessel[self.checker.id] >= self.max_penalty or
            self.accumulated_vessel[self.checker.id] >= self.max_penalty
        )
        if checker_lost:
            winner = self.dropper
            loser  = self.checker
            result_msg += f"\n\n🏆 **GAME OVER!** {winner.mention} wins!\n"
            if self.main_vessel[self.checker.id] >= self.max_penalty:
                result_msg += f"{self.checker.mention} reached 300 seconds penalty!"
            else:
                result_msg += f"{self.checker.mention}'s vessel reached 300 seconds penalty!"
            await self.send(result_msg)
            return GameResult(winner_id=winner.id, loser_id=loser.id, is_draw=False)

        # Switch roles
        self.dropper, self.checker = self.checker, self.dropper
        self.dropped_number = None
        self.pending_check  = None
        self.round += 1

        result_msg += f"\n\n🔄 **Round {self.round}** - {self.dropper.mention} is now the dropper!"
        await self.send(result_msg)
        return None

    # ── State persistence ─────────────────────────────────────

    def get_state(self) -> dict:
        return {
            "dropper_id":           self.dropper.id,
            "checker_id":           self.checker.id,
            "dropped_number":       self.dropped_number,
            "pending_check_time":   self.pending_check[1] if self.pending_check else None,
            "pending_check_uid":    self.pending_check[0].id if self.pending_check else None,
            "main_vessel":          {str(k): v for k, v in self.main_vessel.items()},
            "accumulated_vessel":   {str(k): v for k, v in self.accumulated_vessel.items()},
            "round":                self.round,
        }

    def load_state(self, state: dict) -> None:
        self.dropper  = self.get_member(state["dropper_id"])
        self.checker  = self.get_member(state["checker_id"])
        self.dropped_number       = state.get("dropped_number")
        self.main_vessel          = {int(k): v for k, v in state.get("main_vessel", {}).items()}
        self.accumulated_vessel   = {int(k): v for k, v in state.get("accumulated_vessel", {}).items()}
        self.round = state.get("round", 1)
        pct = state.get("pending_check_time")
        pcu = state.get("pending_check_uid")
        self.pending_check = (self.get_member(pcu), pct) if pct is not None and pcu is not None else None