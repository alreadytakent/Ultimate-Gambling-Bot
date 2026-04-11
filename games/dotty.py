# ============================================================
#  games/dotty.py — Bloody Dotty
#
#  1v1, no draws.
#
#  Rules:
#    - Both players secretly grab 1-10 marbles via DM: .grab [1-10]
#    - One player (X) is randomly chosen to guess first
#    - Players take turns guessing the total sum (2-20) in the channel
#    - A guess is illegal if it's outside the range possible given your number
#      (illegal guess = instant loss)
#    - Correct guess = win; incorrect guess = table updated, turn switches
# ============================================================

from typing import Optional
import discord
import random

from games.base_game import BaseGame, GameResult


class DottyGame(BaseGame):
    game_name = "Bloody Dotty"
    can_draw = False
    is_ffa = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        p1, p2 = self.players[0], self.players[1]

        self.numbers: dict[int, Optional[int]] = {p1.id: None, p2.id: None}
        self.awaiting_grabs: list[int] = [p1.id, p2.id]

        # Randomly assign Player X (guesses first) and Player Y
        if random.choice([True, False]):
            self.player_x = p1
            self.player_y = p2
        else:
            self.player_x = p2
            self.player_y = p1

        self.current_guesser = self.player_x
        self.other_player    = self.player_y

        # Table of possible sums: table[i][j] = (i+1) + (j+1), possible[i][j] = still valid
        self.table    = [[i + j for j in range(1, 11)] for i in range(1, 11)]
        self.possible = [[True] * 10 for _ in range(10)]

    # ── Formatting ────────────────────────────────────────────

    def _fmt_table(self) -> str:
        lines = []
        header = "Y\\X   " + "  ".join(f"{i}" for i in range(1, 10)) + " 10\n"
        lines.append(header)
        for i in range(10):
            row_num = i + 1
            row = f"{row_num:2}   "
            for j in range(10):
                if self.possible[i][j]:
                    row += f"{self.table[i][j]:2} "
                else:
                    row += " - "
            lines.append(row)
        return "```\n" + "\n".join(lines) + "\n```"

    def _update_table(self, guess: int, guesser_is_x: bool) -> None:
        for i in range(10):
            for j in range(10):
                if guesser_is_x:
                    if (self.table[i][j] == guess
                            or (j + 1) > (guess - 1)
                            or (j + 1) < (guess - 10)):
                        self.possible[i][j] = False
                else:
                    if (self.table[i][j] == guess
                            or (i + 1) > (guess - 1)
                            or (i + 1) < (guess - 10)):
                        self.possible[i][j] = False

    def _is_valid_guess(self, player_id: int, guess: int) -> bool:
        n = self.numbers[player_id]
        return (1 + n) <= guess <= (10 + n)

    # ── start ────────────────────────────────────────────────

    async def start(self) -> None:
        p1, p2 = self.players[0], self.players[1]
        bet_str = f" | Bet: **{self.bet_amount:,}**" if self.bet_amount > 0 else ""

        await self.send(
            f"🎯 **Bloody Dotty** started between {p1.mention} and {p2.mention}!{bet_str}\n"
            f"Both players should grab 1-10 marbles using `.grab [number]` in my DMs.\n"
            f"{self.current_guesser.mention} is Player X and will guess first!\n"
            f"{self.other_player.mention} is Player Y."
        )

        instructions = (
            "**Bloody Dotty Game Started!**\n\n"
            "**How to play:**\n"
            "- Both players grab 1-10 marbles using `.grab [1-10]` in DMs\n"
            "- Then take turns guessing the total sum (2-20)\n"
            "- You can't guess sums that are impossible given your number\n"
            "- First to guess the correct sum wins!\n\n"
        )
        for player in self.players:
            opponent = p2 if player == p1 else p1
            await self.dm_player(
                player,
                instructions +
                f"You're playing against {opponent.mention}\n"
                f"{self.current_guesser.mention} is Player X and will guess first!"
            )

    # ── handle_message ───────────────────────────────────────

    async def handle_message(
        self,
        user_id: int,
        content: str,
        is_dm: bool,
        message: Optional[discord.Message] = None,
    ) -> Optional[GameResult]:
        content = content.strip()
        lower = content.lower()

        # .grab is a DM command
        if is_dm and lower.startswith(".grab "):
            return await self._handle_grab(user_id, content, message)

        # .guess is a channel command — only accepted after both players have grabbed
        if not is_dm and lower.startswith(".guess "):
            if self.numbers[self.players[0].id] is None or self.numbers[self.players[1].id] is None:
                return None   # grabbing phase not done yet; ignore silently
            return await self._handle_guess(user_id, content, message)

        return None

    # ── .grab ─────────────────────────────────────────────────

    async def _handle_grab(
        self, user_id: int, content: str, message: Optional[discord.Message]
    ) -> Optional[GameResult]:
        member = self.get_member(user_id)

        if user_id not in self.awaiting_grabs:
            await self.dm_player(member, "❌ You've already grabbed your marbles!")
            return None

        parts = content.split()
        if len(parts) < 2:
            await self.dm_player(member, "❌ Usage: `.grab [1-10]`")
            return None

        try:
            number = int(parts[1])
        except ValueError:
            await self.dm_player(member, "❌ Please provide a valid number.")
            return None

        if not (1 <= number <= 10):
            await self.dm_player(member, "❌ Number must be between 1 and 10!")
            return None

        self.numbers[user_id] = number
        self.awaiting_grabs.remove(user_id)

        if message:
            try:
                await message.add_reaction('✅')
            except Exception:
                pass

        # Both grabbed — announce and show table in channel
        if len(self.awaiting_grabs) == 0:
            await self.send(
                f"🎯 Both players have grabbed their marbles!\n"
                f"{self.current_guesser.mention} (Player X) starts guessing. "
                f"Use `.guess [sum]` in this channel.\n\n"
                f"Initial Table of Possible Sums:\n{self._fmt_table()}"
            )

        return None

    # ── .guess ────────────────────────────────────────────────

    async def _handle_guess(
        self, user_id: int, content: str, message: Optional[discord.Message]
    ) -> Optional[GameResult]:
        member = self.get_member(user_id)

        if member != self.current_guesser:
            # Wrong player — silently ignore (don't spam the channel)
            return None

        parts = content.split()
        if len(parts) < 2:
            await self.send(f"❌ {member.mention} Usage: `.guess [2-20]`")
            return None

        try:
            guess = int(parts[1])
        except ValueError:
            await self.send(f"❌ {member.mention} Please provide a valid number.")
            return None

        if not (2 <= guess <= 20):
            await self.send(f"❌ {member.mention} Guess must be between 2 and 20!")
            return None

        # ── Illegal guess (outside range given player's own number) ──
        if not self._is_valid_guess(user_id, guess):
            n = self.numbers[user_id]
            winner = self.other_player
            loser  = member
            result_msg = (
                f"🎯 **Bloody Dotty - Guess Result**\n"
                f"{member.mention} guessed: **{guess}**\n"
                f"💀 **ILLEGAL GUESS!** {member.mention} loses the game!\n"
                f"With your number {n}, you can only guess between {1 + n} and {10 + n}.\n\n"
                f"Actual numbers: {self.player_x.mention} (X) had {self.numbers[self.player_x.id]}, "
                f"{self.player_y.mention} (Y) had {self.numbers[self.player_y.id]}\n\n"
                f"Final Table:\n{self._fmt_table()}"
            )
            await self.send(result_msg)
            return GameResult(winner_id=winner.id, loser_id=loser.id, is_draw=False)

        actual_sum = self.numbers[self.players[0].id] + self.numbers[self.players[1].id]

        # ── Correct guess ─────────────────────────────────────
        if guess == actual_sum:
            loser = self.other_player
            result_msg = (
                f"🎯 **Bloody Dotty - Guess Result**\n"
                f"{member.mention} guessed: **{guess}**\n"
                f"🎉 **CORRECT!** {member.mention} wins!\n"
                f"Actual numbers: {self.player_x.mention} (X) had {self.numbers[self.player_x.id]}, "
                f"{self.player_y.mention} (Y) had {self.numbers[self.player_y.id]}\n\n"
                f"Final Table:\n{self._fmt_table()}"
            )
            await self.send(result_msg)
            return GameResult(winner_id=member.id, loser_id=loser.id, is_draw=False)

        # ── Incorrect guess ───────────────────────────────────
        guesser_is_x = (member == self.player_x)
        self._update_table(guess, guesser_is_x)
        self.current_guesser, self.other_player = self.other_player, self.current_guesser

        result_msg = (
            f"🎯 **Bloody Dotty - Guess Result**\n"
            f"{member.mention} guessed: **{guess}**\n"
            f"❌ **INCORRECT!**\n\n"
            f"Updated Table of Possible Sums:\n{self._fmt_table()}\n"
            f"Next to guess: {self.current_guesser.mention}"
        )
        await self.send(result_msg)
        return None

    # ── State persistence ─────────────────────────────────────

    def get_state(self) -> dict:
        return {
            "numbers":          {str(k): v for k, v in self.numbers.items()},
            "awaiting_grabs":   self.awaiting_grabs,
            "player_x_id":      self.player_x.id,
            "player_y_id":      self.player_y.id,
            "current_guesser_id": self.current_guesser.id,
            "other_player_id":  self.other_player.id,
            "table":            self.table,
            "possible":         self.possible,
        }

    def load_state(self, state: dict) -> None:
        self.numbers        = {int(k): v for k, v in state.get("numbers", {}).items()}
        self.awaiting_grabs = state.get("awaiting_grabs", [])
        self.player_x       = self.get_member(state["player_x_id"])
        self.player_y       = self.get_member(state["player_y_id"])
        self.current_guesser = self.get_member(state["current_guesser_id"])
        self.other_player   = self.get_member(state["other_player_id"])
        self.table          = state.get("table", [[i + j for j in range(1, 11)] for i in range(1, 11)])
        self.possible       = state.get("possible", [[True] * 10 for _ in range(10)])