# ============================================================
#  cogs/info.py — .info and .rules
#
#  Commands:
#    .info                          — what the bot does
#    .rules {game or game alias}    — DMs you the rules of a game
# ============================================================

from typing import Optional

import discord
from discord.ext import commands

import config
import database as db


CONTACT = "already_takent"

# ════════════════════════════════════════════════════════════
#  RULES  (key = canonical game name from config.GAME_ALIASES)
# ════════════════════════════════════════════════════════════

RULES: dict[str, str] = {

"Drop the Handkerchief": """\
**🎮 Drop the Handkerchief (DTH)** — 1v1, no draws

**Setup:** One player is the **Dropper**, the other is the **Checker** (assigned randomly). Roles switch after every round.

**Each round (both players act in DMs):**
• Dropper: `.drop [1-60]` — the second (in seconds) at which the handkerchief is dropped.
• Checker: `.check [1-60]` — the second at which the Checker looks back.

**Result:**
• Check ≥ drop → **successful check**. The Checker gets a penalty of `check − drop` seconds.
• Check < drop → **failed check**. The Checker gets a penalty of **60** seconds.
• In **round 18** (leap second) numbers from 1 to **61** are allowed.

**Winning:** Penalties are added up for the Checker only. The first player to reach **300 seconds** of penalty **loses**.""",

"Manga Accurate DTH": """\
**🎮 Manga Accurate DTH (MDTH)** — 1v1, no draws

Same as Drop the Handkerchief (`.drop [1-60]` / `.check [1-60]` in DMs, roles switch every round, leap second in round 18), but each player has **two vessels**: **Main** and **Accumulated**.

**Penalties:**
• **Successful check** (check ≥ drop): the penalty `check − drop` goes to the Checker's **Accumulated** vessel only.
• **Failed check** (check < drop): the Checker gets **60** in the Accumulated vessel, then **everything** in the Accumulated vessel is transferred to the **Main** vessel (Accumulated becomes 0).

**Winning:** If **either** of your vessels reaches **300**, you **lose**.""",

"Game of Pure Strategy": """\
**🎮 Game of Pure Strategy (GOPS)** — 1v1, draws possible

**Setup:** Each player has the cards **1-13**. The prize cards **1-13** are shuffled into a deck.

**Each round:**
• The top prize card is revealed.
• Both players secretly choose one card from their hand in DMs: `.bid [1-13]`. Every card can be used **once**.
• The **higher bid** wins the prize — its value is added to that player's score.
• **Tie:** nobody scores. The prize carries over and the next prize card is added to the pool, so the next winner takes all of them. If no cards are left to add, the pool is discarded.

**Winning:**
• The first player to reach **46 or more points** wins immediately.
• Otherwise, after all 13 cards are played, the player with more points wins. Equal points = **draw**.""",

"Bloody Dotty": """\
**🎮 Bloody Dotty** — 1v1, no draws

**Setup:** Both players secretly grab **1-10 marbles** in DMs: `.grab [1-10]`. One player is randomly **Player X** (guesses first), the other is **Player Y**.

**Guessing:** Players take turns guessing the **total** number of marbles (2-20) in the channel: `.guess [2-20]`.
• **Correct guess** → you win.
• **Incorrect guess** → the table of possible sums is updated and the turn passes.
• **Illegal guess** → you lose instantly. A guess is illegal if it's impossible given your own number: with `n` marbles you may only guess between `n+1` and `n+10`.

The bot shows a table of the sums that are still possible after every guess — use it to narrow down your opponent's number.""",

"Combination": """\
**🎮 Combination** — 1v1, no draws

**Setup:** Each player starts with **7 HP**. Every round has a random **target number** (10-60). One player is the **Maker**, the other the **Guesser** (roles switch every round).

**Each round (in DMs):**
• Maker: `.combo [5 cards]` — a hand of exactly 5 cards that **sums to the target**.
• Guesser: `.guess [5 cards]` — a guess of the Maker's hand, also summing to the target.
• Cards: `A`/`1`, `2`-`10`, `J`/`11`, `Q`/`12`, `K`/`13`. You can use each value at most **4 times** (4 suits).
• Example: `.combo 10 J 5 A 2`

**Damage:** The Maker loses **1 HP for every card** the Guesser got right (cards are matched one-to-one, so duplicates only count as many times as they appear in both hands).

**Winning:** The first player to reach **0 HP** loses.""",

"Air Poker": """\
**🎮 Air Poker** — 1v1, draws possible

**Setup:** Each player has **25 Bios** and 5 personal numbers (sent in DMs). The game lasts **5 rounds** (or until a player can't afford the next ante). Each round, a standard 52-card deck is used — cards used in a round are **removed from the deck** for the rest of the game.

**Each round:**
1. **Choose a number** in DMs: `.play [number]`. Every number can be used once.
2. **Build a poker hand** in DMs: `.hand [5 cards]` — e.g. `.hand As 10h 3d Kc 2s`. Values: A=1, J=11, Q=12, K=13; suits: `s h d c`. The hand must **sum to the number you chose**. Cards already removed from the deck make the hand **illegal** (you get one retry if both hands are illegal; if it happens twice, the round is a draw; if only one hand is illegal, the other player wins the round).
3. **Betting** in the channel. Each round both players pay an **ante** equal to the round number. The first to act is the player with the lower number in round 1, then it alternates. Actions: `.check`, `.fold`, `.call`, `.raise [amount|max]`. A raise can't exceed your opponent's total bet or the Bios they have left. A call ends the betting and goes to the showdown; two checks in a row also end it.
4. **Showdown:** the better poker hand wins the pot (standard poker rankings, ace high; A-2-3-4-5 is a straight). Equal hands split the pot.

**💥 Calamity:** If both hands contain the **same card**, the loser of the round also loses Bios equal to their total bet.

**Winning:** After the last round, the player with more Bios wins. Equal Bios = **draw**.""",

"Contradiction": """\
**🎮 Contradiction** — 1v1, no draws

**Setup:** Each player has **10 HP** and **35,000 Bios**. A round consists of **3 draws**.

**Each draw (in DMs):**
1. One player picks a **spear** (`.spear [taser/katana/gun]`), the other picks a **shield** (`.shield [rubber/wooden/iron]`). Each tool can be used once per round; in the 3rd draw the last remaining tools are assigned automatically. Roles swap after every draw.
2. Both players secretly bet Bios: `.bet [amount]` or `.bet all`. If the bets are equal, you must bet again.
3. The player who bet **more** attacks: the other player takes damage from the spear-vs-shield matchup. **Both bets are spent**, win or lose.

**Damage:**
```
Spear\\Shield   Rubber   Wood    Iron
Gun              5       3       0
Katana           3       2       0
Taser            0       0       3
```

**Winning:** You lose when your **HP reaches 0** or you run out of **Bios**.""",

"Knucklebones": """\
**🎮 Knucklebones** — 1v1, tie = draw

**Setup:** Each player has a **3×3 board** (three columns of three slots). A random player goes first.

**On your turn:** A die (1-6) is rolled for you. Place it in a column with a free slot: `.col [1-3]` (or just send `1`, `2` or `3`).

**Attack:** When you place a number, **all dice with the same number** in your opponent's **same column** are destroyed.

**Scoring (per column):**
• Three of a kind → **9 ×** the value
• A pair → **4 ×** the pair's value **+** the third die
• All different → the sum
Your score is the sum of your three columns.

**Winning:** The game ends as soon as one player fills all 9 slots. The player with the higher score wins; equal scores = **draw**.""",

"King of Diamonds": """\
**👑 King of Diamonds (KOD)** — free-for-all, one winner (or a draw)

**Start:** Use `.kod` to open a lobby. Others click **Join**, the host clicks **Start**. Everyone starts with **5 HP**.

**Each round:** Everyone secretly picks a number from **0 to 100** in DMs: `.num [0-100]`.
• **Target = 0.8 × the average** of all chosen numbers.
• The player **closest to the target** wins the round; everyone else loses **1 HP**.

**Special rules (based on how many players are still alive):**
• **4 or fewer:** players who chose the **same number** can't win the round.
• **3 or fewer:** if someone hits the **rounded target exactly** and is the only one to do so, they win and everyone else loses **2 HP**. If several players hit it, those players can't win.
• **2 players:** if one plays **0** and the other **100**, the **100** wins.

**Winning:** Last player standing wins. If all remaining players die in the **same round**, they **draw**; everyone who died before them lost.

**Other:**
• `.resign` — you lose and leave, the game goes on without you.
• `.cancel` doesn't work in KOD.
• Players who don't submit a number for too long are eliminated.""",

"Labyrinth": """\
**🎮 Labyrinth** — 1v1, draws possible

**Grid:** 6×6. Columns are `1-6`, rows are `A-F` (A1 = top-left, F6 = bottom-right).

**Setup (in DMs) — you build a maze for your opponent:**
1. Choose a **start** and an **end** square: `B2, F5`
2. Place exactly **20 walls**. A wall sits between two adjacent squares: `A2-B2, B2-B3`. You can send them in several messages. The maze must always stay **solvable**.
3. Type `yes` to confirm. `reset` (or `no` at the end) starts over.

**Play (in the channel):** You navigate **your opponent's** maze, starting at its start square, without seeing the walls. A random player goes first.
• Move with `U` (up), `D` (down), `L` (left), `R` (right).
• You keep moving until you hit a **wall** or the **outer boundary** — then your turn ends. Walls you hit are revealed on your map.

**Winning:**
• If the player who moves **second** escapes first, they win immediately.
• If the **first mover** escapes, the other player gets **one final turn**. If they escape during it, the game is a **draw**; otherwise the first mover wins.""",
}


def _chunks(text: str, limit: int = 1900) -> list[str]:
    """Split text into Discord-sized messages on line boundaries."""
    out, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit and cur:
            out.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        out.append(cur)
    return out


async def _resolve_game(guild: Optional[discord.Guild], token: str) -> Optional[str]:
    """Alias or full name → canonical full name (server list first, then defaults)."""
    token = token.strip().lower()
    games = await db.get_guild_games(guild.id) if guild else []
    for g in list(games) + config.DEFAULT_GUILD_GAMES:
        if g["alias"].lower() == token or g["full_name"].lower() == token:
            return g["full_name"]
    return None


class Info(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="info")
    async def info(self, ctx: commands.Context):
        """Show what this bot does."""
        p = ctx.clean_prefix
        await ctx.send(
            "**Referee-bot**\n"
            "A bot for playing mind games and strategy duels against other members of the server: "
            "Drop the Handkerchief (and its manga-accurate version), Game of Pure Strategy, Bloody Dotty, "
            "Combination, Air Poker, Contradiction, Knucklebones, Labyrinth and King of Diamonds.\n\n"
            "• Challenge someone with e.g. `" + p + "dth @opponent` — most moves are made in DMs with the bot.\n"
            "• Every finished game is recorded. Check your stats with `" + p + "score` or compare two players "
            "with `" + p + "score @p1 vs @p2`. Each server has its own separate scoreboard.\n"
            "• Results of games played elsewhere can be recorded with `" + p + "verify` "
            "(both players confirm, or a Referee/Mod does it).\n"
            "• Learn how a game works with `" + p + "rules <game>`, see all commands with `" + p + "help`.\n\n"
            f"For more information on the bot contact **{CONTACT}** on Discord."
        )

    @commands.command(name="rules")
    async def rules(self, ctx: commands.Context, *, game: Optional[str] = None):
        """
        Send the rules of a game to your DMs.
        Usage: .rules {game or game alias}   e.g. .rules dth  or  .rules "Air Poker"
        """
        p = ctx.clean_prefix
        if not game:
            names = ", ".join(f"`{a}` ({n})" for a, n in config.GAME_ALIASES.items())
            await ctx.send(f"❌ Usage: `{p}rules <game>`\nAvailable games: {names}")
            return

        name = await _resolve_game(ctx.guild, game.strip().strip('"'))
        if name is None:
            await ctx.send(f"❌ Unknown game: `{game}`. Use `{p}rules` to see the list of games.")
            return

        text = RULES.get(name)
        if text is None:
            await ctx.send(f"❌ There are no written rules for **{name}**.")
            return

        try:
            for part in _chunks(text):
                await ctx.author.send(part)
        except discord.Forbidden:
            await ctx.send("❌ I couldn't DM you. Enable DMs from server members and try again.")
            return

        if ctx.guild is not None:
            await ctx.send(f"📬 {ctx.author.mention}, I sent you the rules of **{name}** in DMs.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Info(bot))
