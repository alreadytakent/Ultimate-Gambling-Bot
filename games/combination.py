# ============================================================
#  games/combination.py — Combination
#
#  1v1, no draws.
#
#  Rules:
#    - Each round has a random target number (10-60)
#    - Maker: submit a 5-card hand summing to target via .combo in channel
#    - Guesser: guess the maker's hand via .guess in channel
#    - Cards: A/1, 2-10, J/11, Q/12, K/13 (max 4 of each)
#    - Damage = number of cards in common between maker and guesser hands
#    - Maker loses that many HP; roles switch each round
#    - First to reach 0 HP loses (starts at 7 HP)
#
#  Both .combo and .guess are channel commands (not DM).
# ============================================================

from typing import Optional
import discord
import random

from games.base_game import BaseGame, GameResult


class CombinationGame(BaseGame):
    game_name = "Combination"
    can_draw = False
    is_ffa = False

    # ── Card helpers ──────────────────────────────────────────

    @staticmethod
    def _card_to_value(card_str: str) -> Optional[int]:
        s = card_str.upper().strip()
        if s in ('A', '1'):   return 1
        if s in ('J', '11'):  return 11
        if s in ('Q', '12'):  return 12
        if s in ('K', '13'):  return 13
        try:
            v = int(s)
            return v if 2 <= v <= 10 else None
        except ValueError:
            return None

    @staticmethod
    def _value_to_card(value: int) -> str:
        return {1: 'A', 11: 'J', 12: 'Q', 13: 'K'}.get(value, str(value))

    def _validate_hand(self, cards: list[str]) -> tuple[bool, Optional[str], Optional[list[int]]]:
        if len(cards) != 5:
            return False, f"❌ Please provide exactly 5 cards that sum up to {self.current_number}!", None

        values = []
        for c in cards:
            v = self._card_to_value(c)
            if v is None:
                return False, f"❌ Invalid card: {c}. Use A/1, 2-10, J/11, Q/12, K/13.", None
            values.append(v)

        counts: dict[int, int] = {}
        for v in values:
            counts[v] = counts.get(v, 0) + 1
            if counts[v] > 4:
                return False, f"❌ You used {self._value_to_card(v)} {counts[v]} times! Maximum is 4 of each card.", None

        total = sum(values)
        if total != self.current_number:
            return False, f"❌ Your hand sums to {total}, but the target is {self.current_number}.", None

        return True, None, values

    # ── __init__ ──────────────────────────────────────────────

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        p1, p2 = self.players[0], self.players[1]

        if random.choice([True, False]):
            self.maker   = p1
            self.guesser = p2
        else:
            self.maker   = p2
            self.guesser = p1

        self.hp: dict[int, int] = {p1.id: 7, p2.id: 7}
        self.current_number: int = random.randint(10, 60)
        self.maker_hand:   Optional[list[int]] = None
        self.guesser_hand: Optional[list[int]] = None
        self.awaiting_maker   = True
        self.awaiting_guesser = True
        self.round = 1

    # ── start ─────────────────────────────────────────────────

    async def start(self) -> None:
        p1, p2 = self.players[0], self.players[1]
        bet_str = f" | Bet: **{self.bet_amount:,}**" if self.bet_amount > 0 else ""

        await self.send(
            f"🃏 **Combination** started between {p1.mention} and {p2.mention}!{bet_str}\n"
            f"**Round {self.round}** - Target number: **{self.current_number}**\n"
            f"{self.maker.mention} is the maker! Create a combination that sums to {self.current_number}. "
            f"Send `.combo [cards]` in my **DMs**.\n"
            f"{self.guesser.mention} is the guesser! Send `.guess [cards]` in my **DMs** once the maker is ready."
        )

        instructions = (
            "**Combination Game Started!**\n\n"
            "**How to play:**\n"
            "- Each round has a target number (10-60)\n"
            "- Maker: Create a hand that sums to the target using `.combo [cards]`\n"
            "- Guesser: Guess the maker's hand using `.guess [cards]`\n"
            "- Cards: A/1, 2-10, J/11, Q/12, K/13\n"
            "- Maximum 4 of each card (4 suits)\n"
            "- For each correct card in guess, maker loses 1 HP\n"
            "- First to 0 HP loses!\n\n"
        )
        for player in self.players:
            opponent = p2 if player == p1 else p1
            await self.dm_player(
                player,
                instructions +
                f"You're playing against {opponent.mention}\n"
                f"{self.maker.mention} is the maker first!"
            )

    # ── handle_message ────────────────────────────────────────

    async def handle_message(
        self,
        user_id: int,
        content: str,
        is_dm: bool,
        message: Optional[discord.Message] = None,
    ) -> Optional[GameResult]:
        if not is_dm:
            return None   # all commands are DM-based

        content = content.strip()
        lower   = content.lower()

        if lower.startswith(".combo "):
            return await self._handle_combo(user_id, content, message)
        if lower.startswith(".guess "):
            return await self._handle_guess(user_id, content, message)

        return None

    # ── .combo ────────────────────────────────────────────────

    async def _handle_combo(
        self, user_id: int, content: str, message: Optional[discord.Message]
    ) -> Optional[GameResult]:
        member = self.get_member(user_id)

        if member != self.maker:
            await self.dm_player(member, "❌ You're not the maker this round!")
            return None
        if not self.awaiting_maker:
            await self.dm_player(member, "❌ You've already submitted your combination!")
            return None

        cards = content.split()[1:]
        ok, err, values = self._validate_hand(cards)
        if not ok:
            await self.dm_player(member, err)
            return None

        self.maker_hand    = values
        self.awaiting_maker = False

        if message:
            try: await message.add_reaction('✅')
            except Exception: pass

        if not self.awaiting_guesser:
            return await self._resolve_round()

        return None

    # ── .guess ────────────────────────────────────────────────

    async def _handle_guess(
        self, user_id: int, content: str, message: Optional[discord.Message]
    ) -> Optional[GameResult]:
        member = self.get_member(user_id)

        if member != self.guesser:
            await self.dm_player(member, "❌ You're not the guesser this round!")
            return None
        if not self.awaiting_guesser:
            await self.dm_player(member, "❌ You've already submitted your guess!")
            return None

        cards = content.split()[1:]
        ok, err, values = self._validate_hand(cards)
        if not ok:
            await self.dm_player(member, err)
            return None

        self.guesser_hand    = values
        self.awaiting_guesser = False

        if message:
            try: await message.add_reaction('✅')
            except Exception: pass

        if not self.awaiting_maker:
            return await self._resolve_round()

        return None

    # ── Round resolution ──────────────────────────────────────

    async def _resolve_round(self) -> Optional[GameResult]:
        p1, p2 = self.players[0], self.players[1]

        maker_display   = " ".join(self._value_to_card(c) for c in self.maker_hand)
        guesser_display = " ".join(self._value_to_card(c) for c in self.guesser_hand)

        # Count matching cards (each card consumed once)
        remaining = self.maker_hand.copy()
        damage = 0
        for card in self.guesser_hand:
            if card in remaining:
                damage += 1
                remaining.remove(card)

        self.hp[self.maker.id] = max(0, self.hp[self.maker.id] - damage)

        def hp_bar(hp: int) -> str:
            return "🟩 " * hp + "🟥 " * (7 - hp)

        result_msg = (
            f"***===== Combination - Round {self.round} =====***\n"
            f"Number: **{self.current_number}**\n"
            f"{self.maker.mention} combo `{maker_display}`  \n"
            f"{self.guesser.mention} guess `{guesser_display}` \n"
            f"~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~\n"
            f"HP lost: **{damage}**\n"
            f"~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~\n"
            f"{hp_bar(self.hp[p1.id])} {p1.mention}\n\n"
            f"{hp_bar(self.hp[p2.id])} {p2.mention}\n"
            f"~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~"
        )

        # Game over
        if self.hp[self.maker.id] <= 0:
            winner = self.guesser
            loser  = self.maker
            result_msg += f"\n\n🎉 **GAME OVER!** {winner.mention} wins! {loser.mention} reaches 0 HP!"
            await self.send(result_msg)
            return GameResult(winner_id=winner.id, loser_id=loser.id, is_draw=False)

        # Next round
        self.round += 1
        self.maker, self.guesser = self.guesser, self.maker
        self.current_number  = random.randint(10, 60)
        self.maker_hand      = None
        self.guesser_hand    = None
        self.awaiting_maker   = True
        self.awaiting_guesser = True

        result_msg += f"\nNext number : **{self.current_number}**"
        await self.send(result_msg)
        return None

    # ── State persistence ─────────────────────────────────────

    def get_state(self) -> dict:
        return {
            "round":            self.round,
            "maker_id":         self.maker.id,
            "guesser_id":       self.guesser.id,
            "hp":               {str(k): v for k, v in self.hp.items()},
            "current_number":   self.current_number,
            "maker_hand":       self.maker_hand,
            "guesser_hand":     self.guesser_hand,
            "awaiting_maker":   self.awaiting_maker,
            "awaiting_guesser": self.awaiting_guesser,
        }

    def load_state(self, state: dict) -> None:
        self.round          = state.get("round", 1)
        self.maker          = self.get_member(state["maker_id"])
        self.guesser        = self.get_member(state["guesser_id"])
        self.hp             = {int(k): v for k, v in state.get("hp", {}).items()}
        self.current_number = state.get("current_number", 10)
        self.maker_hand     = state.get("maker_hand")
        self.guesser_hand   = state.get("guesser_hand")
        self.awaiting_maker   = state.get("awaiting_maker", True)
        self.awaiting_guesser = state.get("awaiting_guesser", True)