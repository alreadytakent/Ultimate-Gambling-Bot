# ============================================================
#  games/airpoker.py — Air Poker
#
#  1v1, draws possible.
#
#  Flow per round:
#    1. Both players choose a number via DM:        .play [number]
#    2. Both players build a poker hand via DM:     .hand [5 cards]
#       (hand must sum to their chosen number)
#    3. Betting phase in channel:
#       .fold  .check  .call  .raise [amount|max]
#    4. Round ends: hands compared, pot awarded, calamity checked
#
#  Game ends after 5 rounds OR when a player can't afford the next ante.
#  Most Bios at end wins; equal = draw.
#
#  poker_utils logic merged into this file (no external import).
# ============================================================

from typing import Optional
import discord
import random

from games.base_game import BaseGame, GameResult


# ════════════════════════════════════════════════════════════
#  POKER UTILITIES  (merged from poker_utils.py)
# ════════════════════════════════════════════════════════════

def _parse_card(card: str) -> tuple:
    card = card.upper()
    suit = card[-1]
    value_str = card[:-1]
    if value_str in ('A', '1'):
        value = 14
    elif value_str in ('K', '13'):
        value = 13
    elif value_str in ('Q', '12'):
        value = 12
    elif value_str in ('J', '11'):
        value = 11
    else:
        value = int(value_str)
    return (value, suit)


def _get_hand_rank(hand: list) -> tuple:
    values = [c[0] for c in hand]
    suits  = [c[1] for c in hand]
    values.sort(reverse=True)

    value_counts: dict = {}
    for v in values:
        value_counts[v] = value_counts.get(v, 0) + 1

    is_flush    = len(set(suits)) == 1
    is_straight = len(set(values)) == 5 and max(values) - min(values) == 4

    # Ace-low straight
    if set(values) == {14, 2, 3, 4, 5}:
        is_straight = True
        values = [5, 4, 3, 2, 1]

    if is_straight and is_flush and max(values) == 14:
        return (10, max(values), 0, values)
    if is_straight and is_flush:
        return (9, max(values), 0, values)
    if 4 in value_counts.values():
        four = [v for v, c in value_counts.items() if c == 4][0]
        kicker = [v for v in values if v != four][0]
        return (8, four, kicker, values)
    if 3 in value_counts.values() and 2 in value_counts.values():
        three = [v for v, c in value_counts.items() if c == 3][0]
        pair  = [v for v, c in value_counts.items() if c == 2][0]
        return (7, three, pair, values)
    if is_flush:
        return (6, max(values), 0, values)
    if is_straight:
        return (5, max(values), 0, values)
    if 3 in value_counts.values():
        three = [v for v, c in value_counts.items() if c == 3][0]
        kickers = sorted([v for v in values if v != three], reverse=True)
        return (4, three, kickers[0], values)
    pairs = [v for v, c in value_counts.items() if c == 2]
    if len(pairs) == 2:
        pairs.sort(reverse=True)
        kicker = [v for v in values if v not in pairs][0]
        return (3, pairs[0], pairs[1], values)
    if 2 in value_counts.values():
        pair = [v for v, c in value_counts.items() if c == 2][0]
        kickers = sorted([v for v in values if v != pair], reverse=True)
        return (2, pair, kickers[0], values)
    return (1, max(values), 0, values)


def _compare_hands(hand1: list, hand2: list) -> int:
    """Return 1 if hand1 wins, 2 if hand2 wins, 0 if tie."""
    try:
        p1 = [_parse_card(c) for c in hand1]
        p2 = [_parse_card(c) for c in hand2]
        r1, pr1, sr1, k1 = _get_hand_rank(p1)
        r2, pr2, sr2, k2 = _get_hand_rank(p2)
        if r1 != r2:   return 1 if r1 > r2 else 2
        if pr1 != pr2: return 1 if pr1 > pr2 else 2
        if sr1 != sr2: return 1 if sr1 > sr2 else 2
        for a, b in zip(k1, k2):
            if a != b: return 1 if a > b else 2
        return 0
    except Exception:
        return 0


_RANK_NAMES = {
    10: "Royal Flush", 9: "Straight Flush", 8: "Four of a Kind",
    7: "Full House", 6: "Flush", 5: "Straight", 4: "Three of a Kind",
    3: "Two Pair", 2: "One Pair", 1: "High Card",
}

def _hand_rank_name(hand: list, deck: list) -> str:
    if not hand: return "No hand"
    if not all(c in deck for c in hand): return "Illegal Hand"
    try:
        parsed = [_parse_card(c) for c in hand]
        rank, *_ = _get_hand_rank(parsed)
        return _RANK_NAMES.get(rank, "Unknown")
    except Exception:
        return "Unknown"


# ════════════════════════════════════════════════════════════
#  AIR POKER GAME
# ════════════════════════════════════════════════════════════

class AirPokerGame(BaseGame):
    game_name = "Air Poker"
    can_draw = True
    is_ffa = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        p1, p2 = self.players[0], self.players[1]
        self.total_rounds = 5

        # Bios (chips)
        self.bios: dict[int, int] = {p1.id: 25, p2.id: 25}

        # Generate numbers
        all_nums = self._generate_numbers()
        self.player1_numbers: list[int] = all_nums[:5]
        self.player2_numbers: list[int] = all_nums[5:]

        # Round state
        self.round = 1
        self.current_plays: dict[int, Optional[int]] = {p1.id: None, p2.id: None}
        self.awaiting_plays: list[int] = [p1.id, p2.id]
        self.used_numbers:   dict[int, list] = {p1.id: [], p2.id: []}

        # Poker phase
        self.poker_active = False
        self.player_hands: dict[int, Optional[list]] = {p1.id: None, p2.id: None}
        self.awaiting_hands: list[int] = []
        self.hand_attempts = 0

        # Deck
        suits  = ['s', 'h', 'd', 'c']
        values = ['A','2','3','4','5','6','7','8','9','10','J','Q','K']
        self.deck: list[str] = [f"{v}{s}" for s in suits for v in values]

        # Betting phase
        self.betting_active = False
        self.current_player = None
        self.pot = 0
        self.current_bets: dict[int, int] = {p1.id: 0, p2.id: 0}
        self.total_bets:   dict[int, int] = {p1.id: 0, p2.id: 0}
        self.last_raise_amount = 0
        self.betting_history: list[str] = []
        self.first_player = None
        self.round_winner = None
        self.round_tie = False

    # ── Number generation ─────────────────────────────────────

    def _generate_numbers(self) -> list[int]:
        lucky = [15, 20, 25, 30, 35, 40, 45, 50, 55, 47]
        while True:
            sel = random.sample(lucky, 2)
            pool = [i for i in range(6, 65) if i not in sel]
            rest = random.sample(pool, 8)
            nums = sel + rest
            if 338 <= sum(nums) <= 362:
                random.shuffle(rest)
                return [sel[0]] + rest[:4] + [sel[1]] + rest[4:]

    # ── Deck display ──────────────────────────────────────────

    def _fmt_deck(self) -> str:
        suits_order = [('c','Clubs    |'), ('d','Diamonds |'), ('h','Hearts   |'), ('s','Spades   |')]
        val_order   = ['A','2','3','4','5','6','7','8','9','10','J','Q','K']
        lines = ["Remaining cards:"]
        for sc, sn in suits_order:
            line = f"{sn} "
            for v in val_order:
                card = f"{v}{sc}"
                if card in self.deck:
                    line += f" {v}"
                elif v == '10':
                    line += " --"
                else:
                    line += " -"
            lines.append(line)
        return "```\n" + "\n".join(lines) + "\n```"

    # ── Hand sum ──────────────────────────────────────────────

    def _hand_sum(self, hand: list[str]) -> int:
        total = 0
        for card in hand:
            v = card[:-1]
            if v == 'A':  total += 1
            elif v == 'J': total += 11
            elif v == 'Q': total += 12
            elif v == 'K': total += 13
            else:          total += int(v)
        return total

    # ── start ─────────────────────────────────────────────────

    async def start(self) -> None:
        p1, p2 = self.players[0], self.players[1]
        bet_str = f" | Season bet: **{self.bet_amount:,}**" if self.bet_amount > 0 else ""

        await self.send(
            f":clubs: :diamonds: **Air Poker** :hearts: :spades: started between "
            f"{p1.mention} and {p2.mention}!{bet_str}\n"
            f"Check your DMs for instructions. Both players should choose a number now!"
        )

        await self.dm_player(
            p1,
            f"**:clubs: :diamonds: Air Poker :hearts: :spades: Game Started!**\n\n"
            f"Your numbers: {self.player1_numbers}\n"
            f"Choose a number to play in round 1 using `.play [number]`!"
        )
        await self.dm_player(
            p2,
            f"**:clubs: :diamonds: Air Poker :hearts: :spades: Game Started!**\n\n"
            f"Your numbers: {self.player2_numbers}\n"
            f"Choose a number to play in round 1 using `.play [number]`!"
        )

    # ── handle_message ────────────────────────────────────────

    async def handle_message(
        self,
        user_id: int,
        content: str,
        is_dm: bool,
        message: Optional[discord.Message] = None,
    ) -> Optional[GameResult]:
        content = content.strip()
        lower   = content.lower()

        # DM commands
        if is_dm:
            if lower.startswith(".play "):
                return await self._handle_play(user_id, content, message)
            if lower.startswith(".hand "):
                return await self._handle_hand(user_id, content, message)
            return None

        # Channel betting commands
        if not is_dm and self.betting_active:
            if lower == ".fold":
                return await self._handle_fold(user_id, message)
            if lower == ".check":
                return await self._handle_check(user_id, message)
            if lower == ".call":
                return await self._handle_call(user_id, message)
            if lower.startswith(".raise "):
                return await self._handle_raise(user_id, content, message)
        return None

    # ── .play ─────────────────────────────────────────────────

    async def _handle_play(
        self, user_id: int, content: str, message: Optional[discord.Message]
    ) -> Optional[GameResult]:
        p1, p2 = self.players[0], self.players[1]
        member = self.get_member(user_id)

        if user_id not in self.awaiting_plays:
            await self.dm_player(member, "❌ You've already chosen a number for this round!")
            return None

        parts = content.split()
        if len(parts) < 2:
            await self.dm_player(member, "❌ Usage: `.play [number]`")
            return None
        try:
            number = int(parts[1])
        except ValueError:
            await self.dm_player(member, "❌ Please provide a valid number.")
            return None

        player_nums = self.player1_numbers if member == p1 else self.player2_numbers
        if number not in player_nums:
            await self.dm_player(member, f"❌ You don't have {number}! Your available numbers: {player_nums}")
            return None
        if number in self.used_numbers[user_id]:
            await self.dm_player(member, f"❌ You already used {number} in a previous round!")
            return None

        self.current_plays[user_id] = number
        self.awaiting_plays.remove(user_id)

        if message:
            try: await message.add_reaction('✅')
            except Exception: pass

        if len(self.awaiting_plays) == 0:
            msg = await self._start_poker_phase()
            await self.send(msg)
        return None

    # ── Poker phase start ──────────────────────────────────────

    async def _start_poker_phase(self) -> str:
        p1, p2 = self.players[0], self.players[1]
        self.used_numbers[p1.id].append(self.current_plays[p1.id])
        self.used_numbers[p2.id].append(self.current_plays[p2.id])

        self.poker_active   = True
        self.player_hands   = {p1.id: None, p2.id: None}
        self.awaiting_hands = [p1.id, p2.id]
        self.hand_attempts  = 0

        return (
            f"***===== :clubs: :diamonds: Air Poker - Round {self.round} - Start :hearts: :spades: =====***\n\n"
            f"{p1.mention} **vs** {p2.mention}\n"
            f"Both players have chosen their numbers!\n\n"
            f"**Now make your poker hands!** Use `.hand [5 cards]` in DMs.\n"
            f"Your hand must sum to your chosen number."
        )

    # ── .hand ─────────────────────────────────────────────────

    async def _handle_hand(
        self, user_id: int, content: str, message: Optional[discord.Message]
    ) -> Optional[GameResult]:
        member = self.get_member(user_id)

        if not self.poker_active:
            await self.dm_player(member, "❌ No poker phase active right now!")
            return None
        if user_id not in self.awaiting_hands:
            await self.dm_player(member, "❌ You've already submitted your hand for this round!")
            return None

        parts = content.split()[1:]  # cards after ".hand"
        if len(parts) != 5:
            await self.dm_player(member, "❌ You must provide exactly 5 cards!")
            return None

        valid_suits  = {'s', 'h', 'd', 'c'}
        valid_values = {'A','2','3','4','5','6','7','8','9','10','J','Q','K'}
        validated = []
        for card in parts:
            suit = card[-1].lower()
            val  = card[:-1].upper()
            if suit not in valid_suits:
                await self.dm_player(member, f"❌ Invalid suit: {suit}. Use s, h, d, or c")
                return None
            if val not in valid_values:
                await self.dm_player(member, f"❌ Invalid card value: {val}. Use 2-10 or J, Q, K, A.")
                return None
            validated.append(f"{val}{suit}")

        if len(set(validated)) != 5:
            await self.dm_player(member, "❌ You can't have repeating cards!")
            return None

        hand_sum = self._hand_sum(validated)
        chosen   = self.current_plays[user_id]
        if hand_sum != chosen:
            await self.dm_player(member, f"❌ Your hand sums to {hand_sum}, but you chose {chosen}!")
            return None

        self.player_hands[user_id] = validated
        self.awaiting_hands.remove(user_id)

        if message:
            try: await message.add_reaction('✅')
            except Exception: pass

        if len(self.awaiting_hands) == 0:
            result = await self._evaluate_hands()
            if isinstance(result, str):
                await self.send(result)
                return None
            # result is None → both legal, start betting
            msg = await self._start_betting_phase()
            await self.send(msg)
        return None

    # ── Hand evaluation ────────────────────────────────────────

    async def _evaluate_hands(self):
        p1, p2 = self.players[0], self.players[1]
        h1, h2 = self.player_hands[p1.id], self.player_hands[p2.id]

        legal1 = all(c in self.deck for c in h1)
        legal2 = all(c in self.deck for c in h2)

        if not legal1 and not legal2:
            self.hand_attempts += 1
            if self.hand_attempts < 2:
                self.player_hands   = {p1.id: None, p2.id: None}
                self.awaiting_hands = [p1.id, p2.id]
                return (f"Both {p1.mention} & {p2.mention} made a mistake! "
                        f"They have one more chance to submit a legal hand!")
            else:
                return await self._end_round_tie_immediately()
        elif not legal1:
            self.round_winner = p2
            return None
        elif not legal2:
            self.round_winner = p1
            return None
        return None  # both legal → betting

    async def _end_round_tie_immediately(self) -> str:
        p1, p2 = self.players[0], self.players[1]
        h1, h2 = self.player_hands[p1.id], self.player_hands[p2.id]
        self._remove_from_deck(h1 + h2)

        result_msg = (
            f"***===== :clubs: :diamonds: Air Poker - Round {self.round} - Results :hearts: :spades: =====***\n\n"
            f"{p1.mention} chose: **{self.current_plays[p1.id]}** | Hand: `{' '.join(h1)}` - ***Illegal Hand***\n"
            f"{p2.mention} chose: **{self.current_plays[p2.id]}** | Hand: `{' '.join(h2)}` - ***Illegal Hand***\n\n"
            f"Both players submitted illegal hands twice!\n"
            f"🤝 Round is a draw! No betting occurred.\n"
            f"All used cards are removed from the deck.\n\n"
            f"{p1.mention} - {self.bios[p1.id]} Bios | {p2.mention} - {self.bios[p2.id]} Bios\n"
            f"{self._fmt_deck()}"
        )

        if self.round == self.total_rounds or self._check_bankruptcy():
            result_msg += f"\n{await self._end_game()}"
            return result_msg

        result_msg += f"\n**Round {self.round + 1} begins!** Players must choose their number."
        await self._prepare_next_round()
        return result_msg

    # ── Betting phase start ────────────────────────────────────

    async def _start_betting_phase(self) -> str:
        p1, p2 = self.players[0], self.players[1]
        n1, n2 = self.current_plays[p1.id], self.current_plays[p2.id]

        if self.round == 1:
            self.first_player = p1 if n1 < n2 else p2

        ante = self.round
        self.betting_active  = True
        self.current_player  = self.first_player
        self.pot             = ante * 2
        self.current_bets    = {p1.id: ante, p2.id: ante}
        self.total_bets      = {p1.id: ante, p2.id: ante}
        self.last_raise_amount = 0
        self.betting_history = []
        self.poker_active    = False

        self.bios[p1.id] -= ante
        self.bios[p2.id] -= ante

        return (
            f"***===== :clubs: :diamonds: Air Poker - Round {self.round} - Betting :hearts: :spades: =====***\n\n"
            f"Both players have made their hands!\n\n"
            f"{p1.mention}'s number was: **{n1}**\n"
            f"{p2.mention}'s number was: **{n2}**\n\n"
            f"**Betting begins!** {self.first_player.mention} starts the action.\n"
            f"Ante - {ante} Bios | Pot - {self.pot} Bios"
        )

    # ── Betting commands ───────────────────────────────────────

    def _pot_status(self) -> str:
        p1, p2 = self.players[0], self.players[1]
        return (
            f"\n**Pot: {self.pot} Bios**\n"
            f"{p1.mention} - {self.bios[p1.id]} Bios | "
            f"{p2.mention} - {self.bios[p2.id]} Bios"
        )

    async def _handle_fold(
        self, user_id: int, message: Optional[discord.Message]
    ) -> Optional[GameResult]:
        member = self.get_member(user_id)
        if member != self.current_player:
            await self.send("❌ It's not your turn to act!")
            return None

        self.betting_active = False
        self.betting_history.append(f"{member.mention} fold")
        other = self.players[1] if member == self.players[0] else self.players[0]
        self.round_winner = other

        pot_msg = self._pot_status()
        result  = await self._end_round()
        await self.send(f"✅ {member.mention} folds!{pot_msg}\n\n{result}")

        if not self.is_active:
            return self._build_game_result()
        return None

    async def _handle_check(
        self, user_id: int, message: Optional[discord.Message]
    ) -> Optional[GameResult]:
        member = self.get_member(user_id)
        if member != self.current_player:
            await self.send("❌ It's not your turn to act!")
            return None

        p1, p2 = self.players[0], self.players[1]
        if self.current_bets[p1.id] != self.current_bets[p2.id]:
            await self.send("❌ You cannot check when there's an outstanding bet!")
            return None

        self.betting_history.append(f"{member.mention} check")
        pot_msg = self._pot_status()

        result = await self._advance_betting(member)
        if result is None:
            await self.send(f"✅ {member.mention} checks!{pot_msg}")
        else:
            await self.send(f"✅ {member.mention} checks!{pot_msg}\n\n{result}")
            if not self.is_active:
                return self._build_game_result()
        return None

    async def _handle_call(
        self, user_id: int, message: Optional[discord.Message]
    ) -> Optional[GameResult]:
        member = self.get_member(user_id)
        if member != self.current_player:
            await self.send("❌ It's not your turn to act!")
            return None

        other = self.players[1] if member == self.players[0] else self.players[0]
        call_amount = self.current_bets[other.id] - self.current_bets[member.id]

        if call_amount <= 0:
            await self.send("❌ No bet to call!")
            return None
        if call_amount > self.bios[member.id]:
            await self.send(f"❌ Not enough Bios! You need {call_amount} but only have {self.bios[member.id]}.")
            return None

        self.bios[member.id]         -= call_amount
        self.current_bets[member.id] += call_amount
        self.total_bets[member.id]   += call_amount
        self.pot                     += call_amount

        self.betting_history.append(f"{member.mention} call {call_amount} Bios")
        pot_msg = self._pot_status()

        self.betting_active = False
        result = await self._end_round()
        await self.send(f"✅ {member.mention} calls {call_amount} Bios!{pot_msg}\n\n{result}")

        if not self.is_active:
            return self._build_game_result()
        return None

    async def _handle_raise(
        self, user_id: int, content: str, message: Optional[discord.Message]
    ) -> Optional[GameResult]:
        member = self.get_member(user_id)
        if member != self.current_player:
            await self.send("❌ It's not your turn to act!")
            return None

        other = self.players[1] if member == self.players[0] else self.players[0]
        parts = content.split()
        amount_str = parts[1] if len(parts) > 1 else ""

        max_raise = self._calc_max_raise(member, other)
        if amount_str.lower() == 'max':
            amount = max_raise
            if amount == 0:
                await self.send("❌ You cannot raise now!")
                return None
        else:
            try:
                amount = int(amount_str)
            except ValueError:
                await self.send("❌ Please provide a valid number or 'max'!")
                return None

        if amount < 1:
            await self.send("❌ Raise amount must be at least 1 Bio!")
            return None

        call_amount  = self.current_bets[other.id] - self.current_bets[member.id]
        total_needed = call_amount + amount

        if total_needed > self.bios[member.id]:
            await self.send(f"❌ Not enough Bios! Your max raise is {max_raise} Bios!")
            return None
        if amount > self.total_bets[other.id]:
            await self.send(f"❌ Raise ({amount}) cannot exceed opponent's total bet ({self.total_bets[other.id]})")
            return None
        if amount > self.bios[other.id]:
            await self.send(f"❌ Opponent can only call up to {self.bios[other.id]} Bios")
            return None

        self.bios[member.id]         -= total_needed
        self.current_bets[member.id] += total_needed
        self.total_bets[member.id]   += total_needed
        self.pot                     += total_needed
        self.last_raise_amount        = amount

        action = f"{member.mention} raises {amount} Bios"
        self.betting_history.append(action)
        pot_msg = self._pot_status()

        self.current_player = other
        await self.send(f"✅ {action}{pot_msg}")
        return None

    def _calc_max_raise(self, player, other) -> int:
        call = self.current_bets[other.id] - self.current_bets[player.id]
        m = min(
            self.bios[player.id] - call,
            self.total_bets[other.id],
            self.bios[other.id],
        )
        return max(m, 0)

    async def _advance_betting(self, player):
        other = self.players[1] if player == self.players[0] else self.players[0]
        if (self.current_bets[self.players[0].id] == self.current_bets[self.players[1].id]
                and len(self.betting_history) >= 2
                and "check" in self.betting_history[-1]
                and "check" in self.betting_history[-2]):
            self.betting_active = False
            return await self._end_round()
        self.current_player = other
        return None

    # ── Round end ─────────────────────────────────────────────

    async def _end_round(self) -> str:
        p1, p2 = self.players[0], self.players[1]
        h1 = self.player_hands[p1.id]
        h2 = self.player_hands[p2.id]

        h1_rank = _hand_rank_name(h1, self.deck) if h1 else "No valid hand"
        h2_rank = _hand_rank_name(h2, self.deck) if h2 else "No valid hand"

        calamity_msg = ""
        if not self.round_winner and not self.round_tie:
            result = _compare_hands(h1, h2)
            if result == 1:
                self.round_winner = p1
            elif result == 2:
                self.round_winner = p2

            # Calamity check
            if h1 and h2 and self.round_winner:
                shared = set(h1) & set(h2)
                if shared:
                    loser = p2 if self.round_winner == p1 else p1
                    penalty = min(self.total_bets[loser.id], self.bios[loser.id])
                    self.bios[loser.id] -= penalty
                    cards_str = ", ".join(shared)
                    calamity_msg = (
                        f"\n 💥 **Calamity!** 💥 Both hands include `{cards_str}`. "
                        f"{loser.mention} loses {penalty} Bios!"
                    )

        # Pot distribution
        if self.round_winner:
            self.bios[self.round_winner.id] += self.pot
            pot_msg = (
                f"🏆 {self.round_winner.mention} wins the round! (+{self.pot} Bios)"
                f"{calamity_msg}\nAll used cards are removed from the deck."
            )
        else:
            half = self.pot // 2
            extra = self.pot % 2
            self.bios[p1.id] += half + extra
            self.bios[p2.id] += half
            if extra:
                pot_msg = (f"🤝 Round is a draw! Pot split: {p1.mention} gets {half + extra} Bios, "
                           f"{p2.mention} gets {half} Bios.\nAll used cards are removed from the deck.")
            else:
                pot_msg = (f"🤝 Round is a draw! Pot split equally: both players get {half} Bios.\n"
                           f"All used cards are removed from the deck.")

        self._remove_from_deck((h1 or []) + (h2 or []))

        result_msg = (
            f"***===== :clubs: :diamonds: Air Poker - Round {self.round} - Results :hearts: :spades: =====***\n\n"
            f"{p1.mention} chose: **{self.current_plays[p1.id]}** | Hand: `{' '.join(h1 or [])}` - ***{h1_rank}***\n"
            f"{p2.mention} chose: **{self.current_plays[p2.id]}** | Hand: `{' '.join(h2 or [])}` - ***{h2_rank}***\n\n"
            f"{pot_msg}\n\n"
            f"{p1.mention} - {self.bios[p1.id]} Bios | {p2.mention} - {self.bios[p2.id]} Bios\n"
            f"{self._fmt_deck()}"
        )

        if self.round == self.total_rounds or self._check_bankruptcy():
            result_msg += f"\n{await self._end_game()}"
            return result_msg

        result_msg += f"\n**Round {self.round + 1} begins!** Players must choose their number."
        await self._prepare_next_round()
        return result_msg

    # ── Game end ──────────────────────────────────────────────

    async def _end_game(self) -> str:
        self.is_active = False
        p1, p2 = self.players[0], self.players[1]
        b1, b2 = self.bios[p1.id], self.bios[p2.id]
        if b1 > b2:
            return f"🎊 **Game Over!** {p1.mention} wins with {b1} Bios!"
        elif b2 > b1:
            return f"🎊 **Game Over!** {p2.mention} wins with {b2} Bios!"
        else:
            return f"🎊 **Game Over!** It's a tie! Both players have {b1} Bios."

    def _build_game_result(self) -> Optional[GameResult]:
        p1, p2 = self.players[0], self.players[1]
        b1, b2 = self.bios[p1.id], self.bios[p2.id]
        if b1 > b2:
            return GameResult(winner_id=p1.id, loser_id=p2.id, is_draw=False)
        elif b2 > b1:
            return GameResult(winner_id=p2.id, loser_id=p1.id, is_draw=False)
        else:
            return GameResult(winner_id=None, loser_id=None, is_draw=True)

    def _remove_from_deck(self, cards: list) -> None:
        for c in cards:
            if c in self.deck:
                self.deck.remove(c)

    def _check_bankruptcy(self) -> bool:
        next_ante = self.round + 1
        p1, p2 = self.players[0], self.players[1]
        return self.bios[p1.id] < next_ante or self.bios[p2.id] < next_ante

    async def _prepare_next_round(self) -> None:
        p1, p2 = self.players[0], self.players[1]
        self.round       += 1
        self.round_winner = None
        self.round_tie    = False
        self.current_plays    = {p1.id: None, p2.id: None}
        self.awaiting_plays   = [p1.id, p2.id]
        self.poker_active     = False
        self.betting_active   = False
        self.hand_attempts    = 0
        self.first_player     = p2 if self.first_player == p1 else p1

    # ── State persistence ─────────────────────────────────────

    def get_state(self) -> dict:
        p1, p2 = self.players[0], self.players[1]
        return {
            "round":              self.round,
            "bios":               {str(k): v for k, v in self.bios.items()},
            "player1_numbers":    self.player1_numbers,
            "player2_numbers":    self.player2_numbers,
            "current_plays":      {str(k): v for k, v in self.current_plays.items()},
            "awaiting_plays":     self.awaiting_plays,
            "used_numbers":       {str(k): v for k, v in self.used_numbers.items()},
            "poker_active":       self.poker_active,
            "player_hands":       {str(k): v for k, v in self.player_hands.items()},
            "awaiting_hands":     self.awaiting_hands,
            "hand_attempts":      self.hand_attempts,
            "deck":               self.deck,
            "betting_active":     self.betting_active,
            "current_player_id":  self.current_player.id if self.current_player else None,
            "pot":                self.pot,
            "current_bets":       {str(k): v for k, v in self.current_bets.items()},
            "total_bets":         {str(k): v for k, v in self.total_bets.items()},
            "betting_history":    self.betting_history,
            "first_player_id":    self.first_player.id if self.first_player else None,
            "round_winner_id":    self.round_winner.id if self.round_winner else None,
            "round_tie":          self.round_tie,
        }

    def load_state(self, state: dict) -> None:
        p1, p2 = self.players[0], self.players[1]
        self.round           = state.get("round", 1)
        self.bios            = {int(k): v for k, v in state.get("bios", {}).items()}
        self.player1_numbers = state.get("player1_numbers", [])
        self.player2_numbers = state.get("player2_numbers", [])
        self.current_plays   = {int(k): v for k, v in state.get("current_plays", {}).items()}
        self.awaiting_plays  = state.get("awaiting_plays", [])
        self.used_numbers    = {int(k): v for k, v in state.get("used_numbers", {}).items()}
        self.poker_active    = state.get("poker_active", False)
        self.player_hands    = {int(k): v for k, v in state.get("player_hands", {}).items()}
        self.awaiting_hands  = state.get("awaiting_hands", [])
        self.hand_attempts   = state.get("hand_attempts", 0)
        self.deck            = state.get("deck", [])
        self.betting_active  = state.get("betting_active", False)
        self.pot             = state.get("pot", 0)
        self.current_bets    = {int(k): v for k, v in state.get("current_bets", {}).items()}
        self.total_bets      = {int(k): v for k, v in state.get("total_bets", {}).items()}
        self.betting_history = state.get("betting_history", [])
        self.round_tie       = state.get("round_tie", False)
        cp  = state.get("current_player_id")
        fp  = state.get("first_player_id")
        rw  = state.get("round_winner_id")
        self.current_player = self.get_member(cp) if cp else None
        self.first_player   = self.get_member(fp) if fp else None
        self.round_winner   = self.get_member(rw) if rw else None