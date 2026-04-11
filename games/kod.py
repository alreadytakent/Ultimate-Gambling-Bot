# ============================================================
#  games/kod.py — King of Diamonds (KOD)
#
#  FFA — unlimited players, 1 winner (or draw).
#  Players join via lobby (see cogs/game_starters.py KODLobbyView).
#
#  Rules:
#    - Each round all players choose a number 0-100 via DM: .num [number]
#    - Target = 0.8 × average of all submitted numbers
#    - Closest to target wins the round; all others lose 1 HP
#    - Last player standing wins
#
#  Special rules (activate at ≤4 / ≤3 / ≤2 players):
#    ≤4 players: duplicate numbers → those players are disqualified from winning
#    ≤3 players: exact match of rounded target → winner gets double penalty applied
#                to all other players (they lose 2 HP instead of 1)
#    =2 players: if one plays 0 and the other plays 100, the 100 player wins
# ============================================================

from typing import Optional
import discord

from games.base_game import BaseGame, GameResult


class KODGame(BaseGame):
    game_name = "King of Diamonds"
    can_draw = False
    is_ffa = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.round = 1
        self.hp: dict[int, int] = {p.id: 5 for p in self.players}
        self.eliminated: set[int] = set()

        # Current round state
        self.current_numbers: dict[int, Optional[int]] = {p.id: None for p in self.players}
        self.awaiting: list[int] = [p.id for p in self.players]

    # ── start ────────────────────────────────────────────────

    async def start(self) -> None:
        bet_str = f" | Bet: **{self.bet_amount:,}** per player" if self.bet_amount > 0 else ""
        player_mentions = " ".join(p.mention for p in self.players)

        await self.send(
            f"👑 **King of Diamonds** game started with {len(self.players)} players!\n"
            f"Players: {player_mentions}{bet_str}\n"
            f"Check your DMs for instructions. All players should choose a number now!"
        )

        for player in self.players:
            await self.dm_player(
                player,
                f"**King of Diamonds Game Started!**\n\n"
                f"Use `.num [0-100]` each round to choose your number.\n"
                f"Starting HP: 5\n"
                f"Good luck!"
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
        if user_id in self.eliminated:
            return None

        content = content.strip()
        if not content.lower().startswith(".num "):
            return None

        return await self._handle_num(user_id, content, message)

    # ── .num ─────────────────────────────────────────────────

    async def _handle_num(
        self, user_id: int, content: str, message: Optional[discord.Message]
    ) -> Optional[GameResult]:
        member = self.get_member(user_id)

        if user_id not in self.awaiting:
            await self.dm_player(member, "❌ You've already chosen a number for this round!")
            return None

        parts = content.split()
        if len(parts) < 2:
            await self.dm_player(member, "❌ Usage: `.num [0-100]`")
            return None

        try:
            number = int(parts[1])
        except ValueError:
            await self.dm_player(member, "❌ Please provide a valid number between 0 and 100!")
            return None

        if not (0 <= number <= 100):
            await self.dm_player(member, "❌ Number must be between 0 and 100!")
            return None

        self.current_numbers[user_id] = number
        self.awaiting.remove(user_id)

        if message:
            try:
                await message.add_reaction('✅')
            except Exception:
                pass

        # All active players have submitted — resolve
        if len(self.awaiting) == 0:
            return await self._resolve_round()

        return None

    # ── Round resolution ──────────────────────────────────────

    async def _resolve_round(self) -> Optional[GameResult]:
        active_players = [p for p in self.players if p.id not in self.eliminated]
        active_numbers = {
            p.id: self.current_numbers[p.id]
            for p in active_players
            if self.current_numbers[p.id] is not None
        }
        # If everyone was timed out and has no number, nothing to resolve
        if not active_numbers:
            return None
        remaining = len(active_players)

        numbers_list = list(active_numbers.values())
        average = sum(numbers_list) / len(numbers_list)
        target = 0.8 * average

        winners = []
        double_penalty = False
        exact_match = False

        # ── Step 1: ≤4-player duplicate rule ─────────────────
        if remaining <= 4:
            duplicates = self._find_duplicates(active_numbers)
            if duplicates:
                valid = {pid: n for pid, n in active_numbers.items() if pid not in duplicates}
                if valid:
                    winner_ids = self._find_closest(valid, target)
                    winners = [p for p in active_players if p.id in winner_ids]
                # else: all duplicates → winners stays []

        # ── Step 2: 2-player special (0 vs 100) ──────────────
        if not winners and remaining == 2:
            p1, p2 = active_players
            n1, n2 = active_numbers[p1.id], active_numbers[p2.id]
            if n1 == 0 and n2 == 100:
                winners = [p2]
            elif n1 == 100 and n2 == 0:
                winners = [p1]

        # ── Step 3: ≤3-player exact-match rule ───────────────
        if not winners and remaining <= 3:
            rounded_target = round(target)
            exact_ids = [pid for pid, n in active_numbers.items() if n == rounded_target]
            if len(exact_ids) == 1:
                winners = [p for p in active_players if p.id == exact_ids[0]]
                double_penalty = True
                exact_match = True
            elif len(exact_ids) > 1:
                # Multiple exact matches — those players lose too
                valid = {pid: n for pid, n in active_numbers.items() if pid not in exact_ids}
                if valid:
                    winner_ids = self._find_closest(valid, target)
                    winners = [p for p in active_players if p.id in winner_ids]

        # ── Step 4: Default — closest to target ──────────────
        if not winners:
            eligible = dict(active_numbers)
            if remaining <= 4:
                duplicates = self._find_duplicates(active_numbers)
                eligible = {pid: n for pid, n in eligible.items() if pid not in duplicates}
            if remaining <= 3:
                rounded_target = round(target)
                exact_ids = [pid for pid, n in active_numbers.items() if n == rounded_target]
                if len(exact_ids) > 1:
                    eligible = {pid: n for pid, n in eligible.items() if pid not in exact_ids}
            if eligible:
                winner_ids = self._find_closest(eligible, target)
                winners = [p for p in active_players if p.id in winner_ids]

        # ── Apply HP penalties ────────────────────────────────
        self._apply_penalties(active_players, winners, double_penalty)

        # ── Build result message ──────────────────────────────
        msg = await self._build_message(
            active_players, active_numbers, target, winners, exact_match
        )

        # ── Check game over ───────────────────────────────────
        remaining_players = [p for p in self.players if p.id not in self.eliminated]

        if len(remaining_players) <= 1:
            await self.send(msg)
            if len(remaining_players) == 1:
                winner = remaining_players[0]
                loser_ids = [p.id for p in self.players if p.id != winner.id]
                return GameResult(
                    winner_id=winner.id,
                    loser_id=loser_ids[0] if len(loser_ids) == 1 else None,
                    is_draw=False,
                    loser_ids=loser_ids,
                )
            else:
                # Everyone eliminated simultaneously — draw
                return GameResult(winner_id=None, loser_id=None, is_draw=True)

        # ── Prepare next round ────────────────────────────────
        self.round += 1
        self.current_numbers = {p.id: None for p in self.players}
        self.awaiting = [p.id for p in self.players if p.id not in self.eliminated]
        msg += f"\n**Round {self.round} begins!** Choose your numbers."
        await self.send(msg)
        return None

    # ── Helpers ───────────────────────────────────────────────

    def _find_duplicates(self, numbers_dict: dict) -> set:
        count: dict[int, int] = {}
        for n in numbers_dict.values():
            count[n] = count.get(n, 0) + 1
        return {pid for pid, n in numbers_dict.items() if count[n] > 1}

    def _find_closest(self, numbers_dict: dict, target: float) -> list:
        closest: list = []
        best = float('inf')
        for pid, n in numbers_dict.items():
            diff = abs(n - target)
            if diff < best:
                best = diff
                closest = [pid]
            elif diff == best:
                closest.append(pid)
        return closest

    def _apply_penalties(self, active_players, winners, double_penalty: bool):
        penalty = 2 if double_penalty else 1
        for player in active_players:
            if player not in winners:
                self.hp[player.id] -= penalty
                if self.hp[player.id] <= 0:
                    self.hp[player.id] = 0
                    self.eliminated.add(player.id)

    async def _build_message(
        self,
        active_players,
        active_numbers: dict,
        target: float,
        winners: list,
        exact_match: bool,
    ) -> str:
        # Numbers display with HP hearts
        lines = []
        for p in active_players:
            if p.id not in active_numbers:
                continue   # timed out this round — already eliminated, skip
            hp = self.hp[p.id]
            hearts = "❤️" * hp + "💔" * (5 - hp)
            lines.append(f"{hearts} {p.mention}: {active_numbers[p.id]}")

        msg = (
            f"***===== 👑 King of Diamonds - Round {self.round} 👑 =====***\n\n"
            f"Numbers chosen:\n" + "\n".join(lines) + "\n\n"
            f"Target: {target:.2f}\n"
        )

        # Duplicate note
        if len(active_players) <= 4:
            duplicates = self._find_duplicates(active_numbers)
            dup_players = [p for p in active_players if p.id in duplicates]
            if dup_players and len(dup_players) < len(active_players):
                mentions = " ".join(p.mention for p in dup_players)
                s = "s" if len(dup_players) > 1 else ""
                msg += f"🚫 {mentions} lose{s} the round due to choosing the same number.\n\n"

        # Winner line
        if winners:
            mentions = " ".join(w.mention for w in winners)
            s = "s" if len(winners) == 1 else ""
            if exact_match:
                msg += f"🎯 **EXACT MATCH!** {mentions} win{s} with perfect guess!\n"
                msg += f"💔 All other players lose **2 HP**!\n"
            else:
                msg += f"🏆 {mentions} win{s} the round!\n"
                msg += f"💔 All other players lose 1 HP!\n"
        else:
            if len(active_players) <= 4:
                duplicates = self._find_duplicates(active_numbers)
                if len(duplicates) == len(active_players):
                    msg += f"🚫 All players chose duplicate numbers!\n"
            msg += f"🤝 **No winners! Everyone loses 1 HP!**\n"

        # Eliminations
        eliminated_now = [p for p in active_players if self.hp[p.id] <= 0 and p not in winners]
        if eliminated_now:
            msg += f"\n💀 **Eliminated:** {', '.join(p.mention for p in eliminated_now)}\n"

        # Game over check
        remaining = [p for p in self.players if p.id not in self.eliminated]
        if len(remaining) <= 1:
            if len(remaining) == 1:
                msg += f"\n🎊 **GAME OVER!** {remaining[0].mention} is the King of Diamonds! 👑"
            else:
                msg += f"\n🎊 **GAME OVER!** It's a tie! No king today."

        return msg

    # ── Timeout override ──────────────────────────────────────

    async def on_timeout(self, timed_out_user_id: int) -> Optional[GameResult]:
        """Eliminate the timed-out player and continue the game."""
        member = self.get_member(timed_out_user_id)
        name = member.display_name if member else f"<@{timed_out_user_id}>"

        # Remove from awaiting and set a default number (treated as not playing this round)
        if timed_out_user_id in self.awaiting:
            self.awaiting.remove(timed_out_user_id)
        self.eliminated.add(timed_out_user_id)
        self.hp[timed_out_user_id] = 0

        await self.send(f"⏱️ **{name}** was eliminated for inactivity!")

        # If everyone else already submitted, resolve now
        if len(self.awaiting) == 0:
            # Remove eliminated player from active_numbers for this round
            self.current_numbers[timed_out_user_id] = None
            return await self._resolve_round()

        remaining = [p for p in self.players if p.id not in self.eliminated]
        if len(remaining) <= 1:
            if len(remaining) == 1:
                winner = remaining[0]
                loser_ids = [p.id for p in self.players if p.id != winner.id]
                return GameResult(
                    winner_id=winner.id,
                    loser_id=loser_ids[0] if len(loser_ids) == 1 else None,
                    is_draw=False,
                    loser_ids=loser_ids,
                )
            else:
                return GameResult(winner_id=None, loser_id=None, is_draw=True)

        return None

    # ── State persistence ─────────────────────────────────────

    def get_state(self) -> dict:
        return {
            "round":            self.round,
            "hp":               {str(k): v for k, v in self.hp.items()},
            "eliminated":       list(self.eliminated),
            "current_numbers":  {str(k): v for k, v in self.current_numbers.items()},
            "awaiting":         self.awaiting,
        }

    def load_state(self, state: dict) -> None:
        self.round          = state.get("round", 1)
        self.hp             = {int(k): v for k, v in state.get("hp", {}).items()}
        self.eliminated     = set(state.get("eliminated", []))
        self.current_numbers = {int(k): v for k, v in state.get("current_numbers", {}).items()}
        self.awaiting       = state.get("awaiting", [p.id for p in self.players])