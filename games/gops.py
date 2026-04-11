# ============================================================
#  games/gops.py — Game of Pure Strategy (GOPS)
#
#  1v1, draws possible.
#
#  Rules:
#    - Both players have a hand of cards 1-13
#    - Each round a prize card is revealed; players secretly bid via DM: .bid [1-13]
#    - Higher bid wins all current prize values as points
#    - Tie: prize carries over to next round (next card added to pool)
#    - If tie with no cards left to add: prize pool discarded
#    - First to 46+ points wins instantly
#    - After all 13 cards played: most points wins; equal = draw
# ============================================================

from typing import Optional
import discord
import random

from games.base_game import BaseGame, GameResult


class GOPSGame(BaseGame):
    game_name = "Game of Pure Strategy"
    can_draw = True
    is_ffa = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        p1, p2 = self.players[0], self.players[1]

        # Prize deck (shuffled 1-13)
        self.prize_deck = list(range(1, 14))
        random.shuffle(self.prize_deck)

        # Current prize pool (grows on ties)
        self.current_prizes: list[int] = [self.prize_deck.pop()]

        # Player hands — original_hands tracks what's available for display
        # hands tracks what's remaining for validation
        self.original_hands: dict[int, list[int]] = {
            p1.id: list(range(1, 14)),
            p2.id: list(range(1, 14)),
        }
        self.hands: dict[int, list[int]] = {
            p1.id: list(range(1, 14)),
            p2.id: list(range(1, 14)),
        }

        self.scores: dict[int, int] = {p1.id: 0, p2.id: 0}
        self.bids:   dict[int, int] = {}
        self.awaiting_bids: list[int] = [p1.id, p2.id]
        self.round = 1

    # ── Formatting helpers ────────────────────────────────────

    def _fmt_cards(self, cards: list[int]) -> str:
        """Format a hand with dashes for played cards."""
        parts = []
        for i in range(1, 14):
            if i in cards:
                parts.append(str(i))
            else:
                parts.append("-" if i < 10 else "--")
        return " ".join(parts)

    def _fmt_prize_track(self) -> str:
        """Format remaining prize cards (deck + current pool)."""
        available = self.prize_deck + self.current_prizes
        parts = []
        for i in range(1, 14):
            if i in available:
                parts.append(str(i))
            else:
                parts.append("-" if i < 10 else "--")
        return " ".join(parts)

    def _fmt_prize_display(self) -> str:
        """Format current prize pool, showing sum if multiple cards."""
        if not self.current_prizes:
            return "0"
        if len(self.current_prizes) == 1:
            return f"**{self.current_prizes[0]}**"
        total = sum(self.current_prizes)
        return f"{' + '.join(str(p) for p in self.current_prizes)} = **{total}**"

    # ── start ────────────────────────────────────────────────

    async def start(self) -> None:
        p1, p2 = self.players[0], self.players[1]
        bet_str = f" | Bet: **{self.bet_amount:,}**" if self.bet_amount > 0 else ""

        await self.send(
            f"🎴 **Game of Pure Strategy** started between {p1.mention} and {p2.mention}!{bet_str}\n"
            f"**Round 1** - Prize card: {self.current_prizes[0]}\n"
            f"Check your DMs for instructions. Both players should bid now!"
        )

        instructions = (
            "**Game of Pure Strategy started!**\n\n"
            "**How to play:**\n"
            "- Each round, prize cards are revealed\n"
            "- You bid one card from your hand (1-13)\n"
            "- Higher bid wins ALL prize cards' values as points\n"
            "- Ties: prize cards carry over to next round\n"
            "- First to reach 46+ points wins instantly!\n"
            "- Use `.bid [1-13]` to play your card\n\n"
        )
        for player in self.players:
            opponent = p2 if player == p1 else p1
            await self.dm_player(player, instructions + f"You're playing against {opponent.mention}")

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
        if not content.lower().startswith(".bid "):
            return None

        return await self._handle_bid(user_id, content, message)

    # ── .bid ─────────────────────────────────────────────────

    async def _handle_bid(
        self, user_id: int, content: str, message: Optional[discord.Message]
    ) -> Optional[GameResult]:
        member = self.get_member(user_id)

        if user_id not in self.awaiting_bids:
            await self.dm_player(member, "❌ You've already bid this round!")
            return None

        parts = content.split()
        if len(parts) < 2:
            await self.dm_player(member, "❌ Usage: `.bid [1-13]`")
            return None

        try:
            card = int(parts[1])
        except ValueError:
            await self.dm_player(member, "❌ Please provide a valid number.")
            return None

        if card not in self.original_hands[user_id]:
            available = sorted(self.original_hands[user_id])
            await self.dm_player(
                member,
                f"❌ You don't have card {card} in your hand! "
                f"Your available cards: {available}"
            )
            return None

        # Record bid
        self.bids[user_id] = card
        self.awaiting_bids.remove(user_id)
        self.hands[user_id].remove(card)

        if message:
            try:
                await message.add_reaction('✅')
            except Exception:
                pass

        if len(self.awaiting_bids) == 0:
            return await self._resolve_round()

        return None

    # ── Round resolution ──────────────────────────────────────

    async def _resolve_round(self) -> Optional[GameResult]:
        p1, p2 = self.players[0], self.players[1]
        bid1 = self.bids[p1.id]
        bid2 = self.bids[p2.id]
        total_prize = sum(self.current_prizes)

        result_msg = (
            f"***===== Game of Pure Strategy - Round {self.round} =====***\n\n"
            f"Prize cards: {self._fmt_prize_display()}\n"
            f"{p1.mention} bid: **{bid1}**\n"
            f"{p2.mention} bid: **{bid2}**\n\n"
        )

        if bid1 > bid2:
            self.scores[p1.id] += total_prize
            result_msg += f"🏆 {p1.mention} wins the round! (+{total_prize} points)"
            if self.prize_deck:
                self.current_prizes = [self.prize_deck.pop()]
            else:
                self.current_prizes = []

        elif bid2 > bid1:
            self.scores[p2.id] += total_prize
            result_msg += f"🏆 {p2.mention} wins the round! (+{total_prize} points)"
            if self.prize_deck:
                self.current_prizes = [self.prize_deck.pop()]
            else:
                self.current_prizes = []

        else:
            # Tie — carry over
            if self.prize_deck:
                new_card = self.prize_deck.pop()
                self.current_prizes.append(new_card)
                result_msg += f"🤝 **Tie!** Prize cards carry over to next round. Added card: **{new_card}**"
            else:
                result_msg += f"🤝 **Tie!** No more cards to add. Prize pool of {total_prize} points is discarded!"
                self.current_prizes = []

        # Update original hands now that the round is resolved
        self.original_hands[p1.id].remove(bid1)
        self.original_hands[p2.id].remove(bid2)

        # Score display
        p1_cards   = self._fmt_cards(sorted(self.original_hands[p1.id]))
        p2_cards   = self._fmt_cards(sorted(self.original_hands[p2.id]))
        prize_track = self._fmt_prize_track()
        result_msg += (
            f"\n\n`{p1_cards}` {p1.mention} ({self.scores[p1.id]} points)\n"
            f"`{p2_cards}` {p2.mention} ({self.scores[p2.id]} points)\n"
            f"`{prize_track}` Remaining prizes"
        )

        # ── Check instant win (46+ points) ────────────────────
        for pid, player in [(p1.id, p1), (p2.id, p2)]:
            if self.scores[pid] >= 46:
                other = p2 if player == p1 else p1
                result_msg += (
                    f"\n\n🎉 **GAME OVER!** {player.mention} reaches "
                    f"{self.scores[pid]} points and wins instantly!"
                )
                await self.send(result_msg)
                return GameResult(winner_id=player.id, loser_id=other.id, is_draw=False)

        # ── Check all cards played ─────────────────────────────
        if not self.original_hands[p1.id] and not self.original_hands[p2.id]:
            s1, s2 = self.scores[p1.id], self.scores[p2.id]
            if s1 > s2:
                result_msg += f"\n\n🎉 **GAME OVER!** {p1.mention} wins with {s1} points!"
                await self.send(result_msg)
                return GameResult(winner_id=p1.id, loser_id=p2.id, is_draw=False)
            elif s2 > s1:
                result_msg += f"\n\n🎉 **GAME OVER!** {p2.mention} wins with {s2} points!"
                await self.send(result_msg)
                return GameResult(winner_id=p2.id, loser_id=p1.id, is_draw=False)
            else:
                result_msg += f"\n\n🎉 **GAME OVER!** It's a tie! Both players scored {s1} points!"
                await self.send(result_msg)
                return GameResult(winner_id=None, loser_id=None, is_draw=True)

        # ── Prepare next round ────────────────────────────────
        self.round += 1
        self.bids = {}
        self.awaiting_bids = [p1.id, p2.id]
        self.hands = {
            p1.id: self.original_hands[p1.id][:],
            p2.id: self.original_hands[p2.id][:],
        }

        if self.current_prizes:
            result_msg += f"\n\n**Round {self.round}** - Prize cards: {self._fmt_prize_display()}"
        elif self.prize_deck:
            self.current_prizes = [self.prize_deck.pop()]
            result_msg += f"\n\n**Round {self.round}** - Prize cards: {self._fmt_prize_display()}"
        else:
            result_msg += f"\n\n**Game ended unexpectedly** - no more prize cards."
            await self.send(result_msg)
            # Fallback: decide by score
            s1, s2 = self.scores[p1.id], self.scores[p2.id]
            if s1 > s2:
                return GameResult(winner_id=p1.id, loser_id=p2.id, is_draw=False)
            elif s2 > s1:
                return GameResult(winner_id=p2.id, loser_id=p1.id, is_draw=False)
            else:
                return GameResult(winner_id=None, loser_id=None, is_draw=True)

        await self.send(result_msg)
        return None

    # ── State persistence ─────────────────────────────────────

    def get_state(self) -> dict:
        return {
            "round":            self.round,
            "prize_deck":       self.prize_deck,
            "current_prizes":   self.current_prizes,
            "original_hands":   {str(k): v for k, v in self.original_hands.items()},
            "hands":            {str(k): v for k, v in self.hands.items()},
            "scores":           {str(k): v for k, v in self.scores.items()},
            "bids":             {str(k): v for k, v in self.bids.items()},
            "awaiting_bids":    self.awaiting_bids,
        }

    def load_state(self, state: dict) -> None:
        self.round          = state.get("round", 1)
        self.prize_deck     = state.get("prize_deck", [])
        self.current_prizes = state.get("current_prizes", [])
        self.original_hands = {int(k): v for k, v in state.get("original_hands", {}).items()}
        self.hands          = {int(k): v for k, v in state.get("hands", {}).items()}
        self.scores         = {int(k): v for k, v in state.get("scores", {}).items()}
        self.bids           = {int(k): v for k, v in state.get("bids", {}).items()}
        self.awaiting_bids  = state.get("awaiting_bids", [p.id for p in self.players])