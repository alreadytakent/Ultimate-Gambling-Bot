# ============================================================
#  games/dth.py — Drop the Handkerchief (DTH)
#
#  1v1, no draws.
#
#  Rules:
#    - One player is the Dropper, one is the Checker (randomly assigned)
#    - Dropper: .drop [1-60] via DM
#    - Checker: .check [1-60] via DM
#    - If check >= drop: SUCCESSFUL CHECK, penalty = check - drop
#    - If check < drop:  FAILED CHECK,      penalty = 60
#    - Round 18 allows numbers 1-61 (leap second)
#    - Penalty added to the Checker; roles switch after each round
#    - First player to reach 300 seconds penalty loses
# ============================================================

from typing import Optional
import discord
import random

from games.base_game import BaseGame, GameResult


class DTHGame(BaseGame):
    game_name = "Drop the Handkerchief"
    can_draw = False
    is_ffa = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Randomly assign starting roles
        if random.choice([True, False]):
            self.dropper = self.players[0]
            self.checker = self.players[1]
        else:
            self.dropper = self.players[1]
            self.checker = self.players[0]

        self.dropped_number: Optional[int] = None
        self.pending_check: Optional[tuple] = None   # (member, check_time)
        self.penalties: dict[int, int] = {p.id: 0 for p in self.players}
        self.max_penalty = 300
        self.round = 1

    # ── start ────────────────────────────────────────────────

    async def start(self) -> None:
        bet_str = f" | Bet: **{self.bet_amount:,}**" if self.bet_amount > 0 else ""

        embed = discord.Embed(
            title="Drop the Handkerchief",
            description=(
                f"{self.players[0].mention} **vs** {self.players[1].mention}{bet_str}\n\n"
                f"Check your DMs for instructions!\n"
                f"{self.dropper.mention} is the Dropper first."
            ),
            color=discord.Color.blue(),
        )
        await self.send(embed=embed)

        instructions = (
            "**Drop the Handkerchief Game Started!**\n\n"
            "**How to play:**\n"
            "- Dropper: `.drop [1-60]` - choose when to drop the handkerchief (seconds)\n"
            "- Checker: `.check [1-60]` - choose when to look back (seconds)\n"
            "- If check is successful (C >= D): penalty = C - D\n"
            "- If check fails (C < D): penalty = 60\n"
            "- First to reach 300 seconds penalty loses!\n\n"
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

        # React to the DM message
        if message:
            try:
                await message.add_reaction('✅')
            except Exception:
                pass

        # If checker already submitted, resolve immediately
        if self.pending_check is not None:
            _, check_time = self.pending_check
            self.pending_check = None
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

        # React to the DM message
        if message:
            try:
                await message.add_reaction('✅')
            except Exception:
                pass

        # If dropper already submitted, resolve immediately
        if self.dropped_number is not None:
            return await self._resolve_round(check_time)

        # Store pending check
        self.pending_check = (member, check_time)
        return None

    # ── Round resolution ──────────────────────────────────────

    async def _resolve_round(self, check_time: int) -> Optional[GameResult]:
        drop_time = self.dropped_number

        if check_time >= drop_time:
            penalty = check_time - drop_time
            result_type = "SUCCESSFUL CHECK"
        else:
            penalty = 60
            result_type = "FAILED CHECK"

        self.penalties[self.checker.id] += penalty

        p1, p2 = self.players[0], self.players[1]
        result_msg = (
            f"====**𝐃𝐫𝐨𝐩 𝐓𝐡𝐞 𝐇𝐚𝐧𝐝𝐤𝐞𝐫𝐜𝐡𝐢𝐞𝐟**====\n"
            f"**Round {self.round}** \n"
            f"~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~\n"
            f"{self.dropper.mention} dropped: {drop_time}\n"
            f"{self.checker.mention} checked: {check_time}\n"
            f"~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~\n"
            f"**RESULT: {result_type}**\n"
            f"{self.checker.mention} accumulated: {penalty}\n"
            f"~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~\n"
            f"{p1.mention} ({self.penalties[p1.id]}/{self.max_penalty})\n"
            f"{p2.mention} ({self.penalties[p2.id]}/{self.max_penalty})"
        )

        # Check for game over
        if self.penalties[self.checker.id] >= self.max_penalty:
            winner = self.dropper
            loser = self.checker
            result_msg += (
                f"\n\n🏆 **GAME OVER!** {winner.mention} wins!\n"
                f"{loser.mention} reached 300 seconds penalty!"
            )
            await self.send(result_msg)
            return GameResult(winner_id=winner.id, loser_id=loser.id, is_draw=False)

        # Switch roles for next round
        self.dropper, self.checker = self.checker, self.dropper
        self.dropped_number = None
        self.pending_check = None
        self.round += 1

        result_msg += f"\n\n🔄 **Round {self.round}** - {self.dropper.mention} is now the dropper!"
        await self.send(result_msg)
        return None

    # ── State persistence ─────────────────────────────────────

    def get_state(self) -> dict:
        return {
            "dropper_id": self.dropper.id,
            "checker_id": self.checker.id,
            "dropped_number": self.dropped_number,
            "pending_check_time": self.pending_check[1] if self.pending_check else None,
            "pending_check_user_id": self.pending_check[0].id if self.pending_check else None,
            "penalties": {str(k): v for k, v in self.penalties.items()},
            "round": self.round,
        }

    def load_state(self, state: dict) -> None:
        dropper_id = state.get("dropper_id")
        checker_id = state.get("checker_id")
        self.dropper = self.get_member(dropper_id)
        self.checker = self.get_member(checker_id)
        self.dropped_number = state.get("dropped_number")
        self.penalties = {int(k): v for k, v in state.get("penalties", {}).items()}
        self.round = state.get("round", 1)
        pct = state.get("pending_check_time")
        pcu = state.get("pending_check_user_id")
        if pct is not None and pcu is not None:
            self.pending_check = (self.get_member(pcu), pct)
        else:
            self.pending_check = None