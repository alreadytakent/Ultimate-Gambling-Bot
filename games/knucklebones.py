# ============================================================
#  games/knucklebones.py — Knucklebones
#
#  1v1, no draws (draw possible on equal final score).
#
#  Rules:
#    - Players take turns rolling a die (1-6)
#    - Current player chooses a column (1-3) to place the die: .col [1-3]
#    - Placing a number eliminates matching numbers from the SAME column
#      of the opponent's board
#    - Scoring per column:
#        All 3 same → 9× the value
#        Two same   → 4× the pair + the third
#        All diff   → sum
#    - Game ends when any player fills all 9 slots
#    - Highest total score wins
#
#  .col is a channel command (not DM).
#  Player 1 board fills bottom→top; Player 2 board fills top→bottom.
# ============================================================

from typing import Optional
import discord
import random

from games.base_game import BaseGame, GameResult


class KnuckleBonesGame(BaseGame):
    game_name = "Knucklebones"
    can_draw = False
    is_ffa = False

    # ── Emoji helpers ─────────────────────────────────────────

    _DICE = {
        1:":one:", 2:":two:", 3:":three:",
        4:":four:", 5:":five:", 6:":six:",
    }

    _EMPTY = ":small_blue_diamond:"

    def _die(self, n: int) -> str:
        return self._DICE.get(n, str(n))

    def _cell(self, n: Optional[int]) -> str:
        return self._DICE.get(n, self._EMPTY) if n is not None else self._EMPTY

    # ── __init__ ──────────────────────────────────────────────

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        p1, p2 = self.players[0], self.players[1]

        # 3×3 matrices: matrices[pid][row][col]
        self.matrices: dict[int, list] = {
            p1.id: [[None]*3 for _ in range(3)],
            p2.id: [[None]*3 for _ in range(3)],
        }
        # How many slots are filled in each column per player
        self.filled: dict[int, list[int]] = {p1.id: [0,0,0], p2.id: [0,0,0]}

        self.current_player_index = random.randint(0, 1)
        self.current_roll: Optional[int] = None
        self.awaiting_column = False

    # ── start ─────────────────────────────────────────────────

    async def start(self) -> None:
        bet_str = f" | Bet: **{self.bet_amount:,}**" if self.bet_amount > 0 else ""
        p1, p2 = self.players[0], self.players[1]
        await self.send(
            f"🎲 **Knucklebones** started between {p1.mention} and {p2.mention}!{bet_str}"
        )
        await self._start_turn()

    async def _start_turn(self) -> None:
        current = self.players[self.current_player_index]
        self.current_roll    = random.randint(1, 6)
        self.awaiting_column = True

        await self.send(
            f"***===== 🎲 Knucklebones 🎲 =====***\n"
            f"{current.mention} rolls a {self._die(self.current_roll)}\n"
            f"Choose a column to place it\n\n"
            f"{self._game_state()}"
        )

    # ── handle_message ────────────────────────────────────────

    async def handle_message(
        self,
        user_id: int,
        content: str,
        is_dm: bool,
        message: Optional[discord.Message] = None,
    ) -> Optional[GameResult]:
        if is_dm:
            return None

        content = content.strip()
        lower   = content.lower()

        # Accept ".col N" or bare "1" / "2" / "3"
        if lower.startswith(".col "):
            return await self._handle_col(user_id, content, message)
        if lower in ("1", "2", "3"):
            return await self._handle_col(user_id, f".col {lower}", message)

        return None

    # ── .col ──────────────────────────────────────────────────

    async def _handle_col(
        self, user_id: int, content: str, message: Optional[discord.Message]
    ) -> Optional[GameResult]:
        current = self.players[self.current_player_index]

        if not self.awaiting_column:
            return None
        if user_id != current.id:
            return None   # silently ignore — don't spam wrong-turn messages

        parts = content.split()
        if len(parts) < 2:
            await self.send(f"❌ {current.mention} Usage: `.col [1-3]`")
            return None
        try:
            column = int(parts[1])
        except ValueError:
            await self.send(f"❌ {current.mention} Please provide a valid column number 1-3!")
            return None

        if column < 1 or column > 3:
            await self.send(f"❌ {current.mention} Column must be 1, 2, or 3!")
            return None

        col_idx = column - 1

        if self.filled[current.id][col_idx] >= 3:
            await self.send(f"❌ {current.mention} That column is already full!")
            return None

        if message:
            try: await message.add_reaction('✅')
            except Exception: pass

        self.awaiting_column = False

        # Place the die
        self._place(current.id, col_idx, self.current_roll)

        # Eliminate matching numbers from opponent's same column
        opponent = self.players[1 - self.current_player_index]
        self._eliminate(opponent.id, col_idx, self.current_roll)

        # Check game end
        result = self._check_end()
        if result is not None:
            scores = self._scores()
            p1, p2 = self.players[0], self.players[1]
            end_msg = f"🎊 **GAME OVER!** "
            if result == "tie":
                end_msg += f"It's a tie! Both players scored {scores[p1.id]}."
            else:
                end_msg += f"{result.mention} wins with {scores[result.id]} vs {scores[opponent.id if result == current else current.id]}!"

            await self.send(
                f"***===== 🎲 Knucklebones 🎲 =====***\n"
                f"{end_msg}\n\n"
                f"{self._game_state()}"
            )

            if result == "tie":
                # Knucklebones can technically end in a tie — treat as draw
                return GameResult(winner_id=None, loser_id=None, is_draw=True)
            else:
                loser = p2 if result == p1 else p1
                return GameResult(winner_id=result.id, loser_id=loser.id, is_draw=False)

        # Continue — switch player and roll
        self.current_player_index = 1 - self.current_player_index
        next_player = self.players[self.current_player_index]
        self.current_roll    = random.randint(1, 6)
        self.awaiting_column = True

        await self.send(
            f"***===== 🎲 Knucklebones 🎲 =====***\n"
            f"{next_player.mention} rolls a {self._die(self.current_roll)}\n"
            f"Choose a column to place it\n\n"
            f"{self._game_state()}"
        )
        return None

    # ── Board logic ───────────────────────────────────────────

    def _place(self, player_id: int, col: int, number: int) -> None:
        """Place a number in the next available slot of a column."""
        filled = self.filled[player_id][col]
        p1_id  = self.players[0].id

        if player_id == p1_id:
            row = 2 - filled       # P1 fills bottom→top: row 2, 1, 0
        else:
            row = filled           # P2 fills top→bottom: row 0, 1, 2

        self.matrices[player_id][row][col] = number
        self.filled[player_id][col] += 1

    def _eliminate(self, opponent_id: int, col: int, number: int) -> None:
        """Remove all occurrences of `number` in opponent's column, then compact."""
        matrix = self.matrices[opponent_id]
        p1_id  = self.players[0].id

        # Clear matching cells
        for row in range(3):
            if matrix[row][col] == number:
                matrix[row][col] = None

        # Compact: keep non-None values, pad with None in the right direction
        col_nums = [matrix[row][col] for row in range(3) if matrix[row][col] is not None]

        if opponent_id == p1_id:
            # P1: numbers fall down → None on top, numbers at bottom
            padded = [None] * (3 - len(col_nums)) + col_nums
        else:
            # P2: numbers float up → numbers on top, None at bottom
            padded = col_nums + [None] * (3 - len(col_nums))

        for row in range(3):
            matrix[row][col] = padded[row]

        self.filled[opponent_id][col] = len(col_nums)

    def _col_score(self, player_id: int, col: int) -> int:
        nums = [self.matrices[player_id][row][col] for row in range(3)
                if self.matrices[player_id][row][col] is not None]
        if not nums:
            return 0
        while len(nums) < 3:
            nums.append(0)
        a, b, c = nums
        if a == b == c:        return 9 * a
        elif a == b:           return 4 * a + c
        elif a == c:           return 4 * a + b
        elif b == c:           return 4 * b + a
        else:                  return a + b + c

    def _scores(self) -> dict[int, int]:
        return {p.id: sum(self._col_score(p.id, col) for col in range(3))
                for p in self.players}

    def _check_end(self):
        """Return winner member, 'tie', or None if game not over."""
        for player in self.players:
            if sum(self.filled[player.id]) >= 9:
                scores = self._scores()
                p1, p2 = self.players[0], self.players[1]
                if scores[p1.id] > scores[p2.id]:
                    return p1
                elif scores[p2.id] > scores[p1.id]:
                    return p2
                else:
                    return "tie"
        return None

    # ── Board display ─────────────────────────────────────────

    def _game_state(self) -> str:
        p1, p2 = self.players[0], self.players[1]
        scores = self._scores()

        lines = []

        # P1 board (rows 0-2, top to bottom visually)
        for row in range(3):
            line = " ".join(self._cell(self.matrices[p1.id][row][col]) for col in range(3))
            if row == 1:
                line += f"  {p1.mention}"
            lines.append(line)

        # P1 column scores
        col_s = [self._col_score(p1.id, c) for c in range(3)]
        lines.append(f"`{col_s[0]:2} {col_s[1]:2} {col_s[2]:2} = {scores[p1.id]:2}`")

        lines.append("")

        # P2 column scores
        col_s2 = [self._col_score(p2.id, c) for c in range(3)]
        lines.append(f"`{col_s2[0]:2} {col_s2[1]:2} {col_s2[2]:2} = {scores[p2.id]:2}`")

        # P2 board (rows 0-2)
        for row in range(3):
            line = " ".join(self._cell(self.matrices[p2.id][row][col]) for col in range(3))
            if row == 1:
                line += f"  {p2.mention}"
            lines.append(line)

        return "\n".join(lines)

    # ── State persistence ─────────────────────────────────────

    def get_state(self) -> dict:
        return {
            "matrices":             {str(k): v for k, v in self.matrices.items()},
            "filled":               {str(k): v for k, v in self.filled.items()},
            "current_player_index": self.current_player_index,
            "current_roll":         self.current_roll,
            "awaiting_column":      self.awaiting_column,
        }

    def load_state(self, state: dict) -> None:
        self.matrices             = {int(k): v for k, v in state.get("matrices", {}).items()}
        self.filled               = {int(k): v for k, v in state.get("filled", {}).items()}
        self.current_player_index = state.get("current_player_index", 0)
        self.current_roll         = state.get("current_roll")
        self.awaiting_column      = state.get("awaiting_column", False)