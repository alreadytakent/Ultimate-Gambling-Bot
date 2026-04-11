# ============================================================
#  games/contradiction.py — Contradiction
#
#  1v1, no draws.
#
#  Structure:
#    - Each ROUND has 3 DRAWS
#    - Each draw: one player chooses a SPEAR (gun/katana/taser) via DM,
#                 the other chooses a SHIELD (rubber/wooden/iron) via DM
#    - Then both bet Bios via DM: .bet [amount|all]
#    - Ties on bets → rebets immediately
#    - Higher better = attacker; their spear deals damage to the other's HP
#    - Roles (spear/shield) swap each draw
#    - Draw 3 of each round: both tools are auto-assigned (last remaining)
#    - Game ends when: HP <= 0 or Bios = 0
#
#  Damage matrix:
#    Gun:    rubber=5, wooden=3, iron=0
#    Katana: rubber=3, wooden=2, iron=0
#    Taser:  rubber=0, wooden=0, iron=3
# ============================================================

from typing import Optional
import discord
import random

from games.base_game import BaseGame, GameResult

DAMAGE_MATRIX = {
    "gun":    {"rubber": 5, "wooden": 3, "iron": 0},
    "katana": {"rubber": 3, "wooden": 2, "iron": 0},
    "taser":  {"rubber": 0, "wooden": 0, "iron": 3},
}

DAMAGE_TABLE = (
    "```\n"
    "Spear\\Shield   Rubber   Wood    Iron\n"
    "      \n"
    "Gun              5       3       0\n"
    "Katana           3       2       0\n"
    "Taser            0       0       3\n"
    "```"
)


class ContradictionGame(BaseGame):
    game_name = "Contradiction"
    can_draw = False
    is_ffa = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        p1, p2 = self.players[0], self.players[1]

        self.bios: dict[int, int] = {p1.id: 35000, p2.id: 35000}
        self.hp:   dict[int, int] = {p1.id: 10,    p2.id: 10}

        self.current_round = 1
        self.current_draw  = 1

        # Randomly assign first spear/shield roles
        if random.choice([True, False]):
            self.spear_chooser  = p1
            self.shield_chooser = p2
        else:
            self.spear_chooser  = p2
            self.shield_chooser = p1

        # Per-draw state
        self.available_spears  = ["taser", "katana", "gun"]
        self.available_shields = ["rubber", "wooden", "iron"]
        self.spear_choices:  dict[int, str] = {}
        self.shield_choices: dict[int, str] = {}
        self.bets:           dict[int, int] = {}
        self.awaiting_bets = False

    # ── HP bar ────────────────────────────────────────────────

    def _hp_bar(self, player) -> str:
        hp = self.hp[player.id]
        return ":green_square: " * hp + ":red_square: " * (10 - hp)

    # ── start ─────────────────────────────────────────────────

    async def start(self) -> None:
        p1, p2 = self.players[0], self.players[1]
        bet_str = f" | Bet: **{self.bet_amount:,}**" if self.bet_amount > 0 else ""

        await self.send(
            f":crossed_swords: :shield: **CONTRADICTION** :shield: :crossed_swords: started between "
            f"{p1.mention} and {p2.mention}!{bet_str}\n\n"
            f"**Round {self.current_round} - Draw {self.current_draw}/3**\n"
            f"• {self.spear_chooser.mention} will choose a SPEAR\n"
            f"• {self.shield_chooser.mention} will choose a SHIELD\n\n"
            f"Damage Matrix:\n{DAMAGE_TABLE}\n"
            f"Check your DMs for instructions."
        )

        await self._send_choice_dms(initial=True)

    async def _send_choice_dms(self, initial: bool = False) -> None:
        intro = (
            f":crossed_swords: :shield: **CONTRADICTION** :shield: :crossed_swords: game started!\n\n"
            if initial else ""
        )
        spear_opp  = self.shield_chooser
        shield_opp = self.spear_chooser

        await self.dm_player(
            self.spear_chooser,
            f"{intro}You're playing against {spear_opp.mention}\n"
            f"Choose a SPEAR to use: `.spear [taser/katana/gun]`\n\n"
            f"Damage Matrix:\n{DAMAGE_TABLE}"
        )
        await self.dm_player(
            self.shield_chooser,
            f"{intro}You're playing against {shield_opp.mention}\n"
            f"Choose a SHIELD to use: `.shield [rubber/wooden/iron]`\n\n"
            f"Damage Matrix:\n{DAMAGE_TABLE}"
        )

    async def _send_bet_dms(self) -> None:
        for player in self.players:
            await self.dm_player(
                player,
                f"💰 **Place your bet!** Use: `.bet [amount]` or `.bet all`\n"
                f"Your Bios: **{self.bios[player.id]:,}**"
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
            return None

        content = content.strip()
        lower   = content.lower()

        if lower.startswith(".spear "):
            return await self._handle_spear(user_id, content, message)
        if lower.startswith(".shield "):
            return await self._handle_shield(user_id, content, message)
        if lower.startswith(".bet "):
            return await self._handle_bet(user_id, content, message)

        return None

    # ── .spear ────────────────────────────────────────────────

    async def _handle_spear(
        self, user_id: int, content: str, message: Optional[discord.Message]
    ) -> Optional[GameResult]:
        member = self.get_member(user_id)

        if member != self.spear_chooser:
            await self.dm_player(member, "❌ It's not your turn to choose a spear!")
            return None
        if user_id in self.spear_choices:
            await self.dm_player(member, "❌ You've already chosen a spear this draw!")
            return None

        choice = content.split()[1].lower() if len(content.split()) > 1 else ""
        if choice not in self.available_spears:
            await self.dm_player(member, f"❌ Invalid spear! Available: {', '.join(self.available_spears)}")
            return None

        self.spear_choices[user_id] = choice

        if message:
            try: await message.add_reaction('✅')
            except Exception: pass

        return await self._check_choices_complete()

    # ── .shield ───────────────────────────────────────────────

    async def _handle_shield(
        self, user_id: int, content: str, message: Optional[discord.Message]
    ) -> Optional[GameResult]:
        member = self.get_member(user_id)

        if member != self.shield_chooser:
            await self.dm_player(member, "❌ It's not your turn to choose a shield!")
            return None
        if user_id in self.shield_choices:
            await self.dm_player(member, "❌ You've already chosen a shield this draw!")
            return None

        choice = content.split()[1].lower() if len(content.split()) > 1 else ""
        if choice not in self.available_shields:
            await self.dm_player(member, f"❌ Invalid shield! Available: {', '.join(self.available_shields)}")
            return None

        self.shield_choices[user_id] = choice

        if message:
            try: await message.add_reaction('✅')
            except Exception: pass

        return await self._check_choices_complete()

    async def _check_choices_complete(self) -> Optional[GameResult]:
        if len(self.spear_choices) == 1 and len(self.shield_choices) == 1:
            self.awaiting_bets = True
            await self._send_bet_dms()
        return None

    # ── .bet ──────────────────────────────────────────────────

    async def _handle_bet(
        self, user_id: int, content: str, message: Optional[discord.Message]
    ) -> Optional[GameResult]:
        member = self.get_member(user_id)

        if not self.awaiting_bets:
            await self.dm_player(member, "❌ No betting phase active right now!")
            return None
        if user_id in self.bets:
            await self.dm_player(member, "❌ You've already placed your bet!")
            return None

        raw = content.split()[1] if len(content.split()) > 1 else ""
        if raw.lower() == 'all':
            amount = self.bios[user_id]
        else:
            try:
                amount = int(raw)
            except ValueError:
                await self.dm_player(member, "❌ Please provide a valid number or 'all'!")
                return None

        if amount <= 0:
            await self.dm_player(member, "❌ Bet must be positive!")
            return None
        if amount > self.bios[user_id]:
            await self.dm_player(member, f"❌ You only have {self.bios[user_id]:,} Bios!")
            return None

        self.bets[user_id] = amount

        if message:
            try: await message.add_reaction('✅')
            except Exception: pass

        if len(self.bets) == 2:
            return await self._resolve_bets()

        return None

    # ── Bet resolution ─────────────────────────────────────────

    async def _resolve_bets(self) -> Optional[GameResult]:
        p1, p2 = self.players[0], self.players[1]
        b1, b2 = self.bets[p1.id], self.bets[p2.id]

        # Tied bets — reset and re-request
        if b1 == b2:
            self.bets = {}
            self.awaiting_bets = True
            await self.send(
                f"⚖️ **Bet Tie!**\n"
                f"Both players bet {b1:,} Bios.\n"
                f"Please bet again with different amounts!"
            )
            await self._send_bet_dms()
            return None

        attacker = p1 if b1 > b2 else p2
        defender = p2 if b1 > b2 else p1

        spear  = self.spear_choices[self.spear_chooser.id]
        shield = self.shield_choices[self.shield_chooser.id]
        damage = DAMAGE_MATRIX[spear][shield]

        # Apply damage and deduct bets
        self.hp[defender.id]     = max(0, self.hp[defender.id] - damage)
        self.bios[attacker.id]  -= self.bets[attacker.id]
        self.bios[defender.id]  -= self.bets[defender.id]
        self.awaiting_bets       = False

        return await self._resolve_draw(spear, shield, damage)

    # ── Draw resolution ────────────────────────────────────────

    async def _resolve_draw(
        self, spear: str, shield: str, damage: int
    ) -> Optional[GameResult]:
        p1, p2 = self.players[0], self.players[1]
        sc_bet = self.bets[self.spear_chooser.id]
        sh_bet = self.bets[self.shield_chooser.id]
        dmg_emoji = "💥" if damage > 0 else "❌"

        result_msg = (
            f"***===== :crossed_swords: :shield: CONTRADICTION - "
            f"Round {self.current_round} - Draw {self.current_draw}/3 "
            f":shield: :crossed_swords: =====***\n\n"
            f"{self.spear_chooser.mention} chose **{spear.upper()}** and bet **{sc_bet:,} Bios**\n"
            f"{self.shield_chooser.mention} chose **{shield.upper()} SHIELD** and bet **{sh_bet:,} Bios**\n\n"
            f"**{spear.upper()}** vs **{shield.upper()} SHIELD** = {damage} damage! {dmg_emoji}\n\n"
            f"{self._hp_bar(p1)}{p1.mention} {self.bios[p1.id]:,} Bios\n\n"
            f"{self._hp_bar(p2)}{p2.mention} {self.bios[p2.id]:,} Bios"
        )

        # Remove used tools
        self.available_spears.remove(spear)
        self.available_shields.remove(shield)

        # Reset draw state
        self.spear_choices  = {}
        self.shield_choices = {}
        self.bets           = {}
        self.spear_chooser, self.shield_chooser = self.shield_chooser, self.spear_chooser
        self.current_draw  += 1

        # ── Check game-ending conditions ──────────────────────
        winner = None
        for player in self.players:
            other = p2 if player == p1 else p1
            if self.hp[player.id] <= 0 or self.bios[player.id] == 0:
                winner = other
                break

        if winner:
            loser = p2 if winner == p1 else p1
            result_msg += f"\n\n**🏆 GAME OVER! {winner.mention} wins!**"
            await self.send(result_msg)
            return GameResult(winner_id=winner.id, loser_id=loser.id, is_draw=False)

        # ── Continue: draw 3 is auto-assigned ────────────────
        if self.current_draw == 3:
            remaining_spear  = self.available_spears[0]
            remaining_shield = self.available_shields[0]
            self.spear_choices[self.spear_chooser.id]   = remaining_spear
            self.shield_choices[self.shield_chooser.id] = remaining_shield

            result_msg += (
                f"\n\n**Round {self.current_round} - Draw 3/3**\n"
                f"It's the last draw of the round, so:\n"
                f"• {self.spear_chooser.mention}'s SPEAR is **{remaining_spear.upper()}**\n"
                f"• {self.shield_chooser.mention}'s SHIELD is **{remaining_shield.upper()}**\n"
                f"Now place your bets!"
            )
            await self.send(result_msg)
            self.awaiting_bets = True
            await self._send_bet_dms()
            return None

        # ── New draw within same round ────────────────────────
        if self.current_draw <= 3:
            result_msg += (
                f"\n\n**Round {self.current_round} - Draw {self.current_draw}/3**\n"
                f"• {self.spear_chooser.mention} will choose a SPEAR\n"
                f"• {self.shield_chooser.mention} will choose a SHIELD"
            )
            await self.send(result_msg)
            await self._send_choice_dms()
            return None

        # ── Round completed — start new round ─────────────────
        self.current_round    += 1
        self.current_draw      = 1
        self.available_spears  = ["taser", "katana", "gun"]
        self.available_shields = ["rubber", "wooden", "iron"]

        result_msg += (
            f"\n\n**Round {self.current_round} - Draw 1/3**\n"
            f"• {self.spear_chooser.mention} will choose a SPEAR\n"
            f"• {self.shield_chooser.mention} will choose a SHIELD"
        )
        await self.send(result_msg)
        await self._send_choice_dms()
        return None

    # ── State persistence ─────────────────────────────────────

    def get_state(self) -> dict:
        return {
            "bios":               {str(k): v for k, v in self.bios.items()},
            "hp":                 {str(k): v for k, v in self.hp.items()},
            "current_round":      self.current_round,
            "current_draw":       self.current_draw,
            "spear_chooser_id":   self.spear_chooser.id,
            "shield_chooser_id":  self.shield_chooser.id,
            "available_spears":   self.available_spears,
            "available_shields":  self.available_shields,
            "spear_choices":      {str(k): v for k, v in self.spear_choices.items()},
            "shield_choices":     {str(k): v for k, v in self.shield_choices.items()},
            "bets":               {str(k): v for k, v in self.bets.items()},
            "awaiting_bets":      self.awaiting_bets,
        }

    def load_state(self, state: dict) -> None:
        self.bios              = {int(k): v for k, v in state.get("bios", {}).items()}
        self.hp                = {int(k): v for k, v in state.get("hp", {}).items()}
        self.current_round     = state.get("current_round", 1)
        self.current_draw      = state.get("current_draw", 1)
        self.spear_chooser     = self.get_member(state["spear_chooser_id"])
        self.shield_chooser    = self.get_member(state["shield_chooser_id"])
        self.available_spears  = state.get("available_spears", ["taser", "katana", "gun"])
        self.available_shields = state.get("available_shields", ["rubber", "wooden", "iron"])
        self.spear_choices     = {int(k): v for k, v in state.get("spear_choices", {}).items()}
        self.shield_choices    = {int(k): v for k, v in state.get("shield_choices", {}).items()}
        self.bets              = {int(k): v for k, v in state.get("bets", {}).items()}
        self.awaiting_bets     = state.get("awaiting_bets", False)