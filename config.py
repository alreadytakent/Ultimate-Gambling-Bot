# ============================================================
#  config.py — UltimateBot global defaults & constants
#  All values here are the server-wide defaults.
#  Per-guild overrides are stored in server.db.
# ============================================================

# --- Bot ---
DEFAULT_PREFIX = "."

# --- Currency ---
DEFAULT_CURRENCY_EMOJI = "💠"       # Change to your emoji when decided
DEFAULT_CURRENCY_NAME = "carat"     # singular
DEFAULT_CURRENCY_NAME_PLURAL = "carats"

def currency(amount: int, emoji: str = DEFAULT_CURRENCY_EMOJI, name_singular: str = DEFAULT_CURRENCY_NAME, name_plural: str = DEFAULT_CURRENCY_NAME_PLURAL) -> str:
    """Format an amount with the correct currency name and emoji."""
    unit = name_singular if amount == 1 else name_plural
    return f"{emoji} {amount:,} {unit}"

# --- Starting balance ---
DEFAULT_STARTING_BALANCE = 0       # given to player on .join

# --- Work command ---
DEFAULT_WORK_AMOUNT = 20_000
DEFAULT_WORK_COOLDOWN_MINUTES = 240  # 4 hours

# --- Classes ---
CLASS_NAMES = ["Assassin", "Specialist", "Collector", "Berserker"]
CLASS_WEIGHTS = [0.25, 0.25, 0.25, 0.25]   # must sum to 1.0
MAX_CLASS_LEVEL = 4

# Cost to level up from current level to next (key = current level)
CLASS_LEVELUP_COSTS = {
    1: 250_000,
    2: 3_000_000,
    3: 6_000_000,
}

# Assassin: bonus % of balance per level on defeating target
ASSASSIN_BONUS = {1: 0.02, 2: 0.04, 3: 0.06, 4: 0.08}
ASSASSIN_TARGET_INTERVAL_HOURS = 24

# Specialist: bonus % of bet per level on completing daily objective
SPECIALIST_BONUS = {1: 0.05, 2: 0.10, 3: 0.15, 4: 0.20}
SPECIALIST_INTERVAL_HOURS = 24     # how often a new objective is assigned

# Collector: % of balance earned per level on .collect
COLLECTOR_BONUS = {1: 0.01, 2: 0.02, 3: 0.03, 4: 0.04}
COLLECTOR_COOLDOWN_HOURS = 24

# Berserker: bonus % of bet per consecutive win tier, per level
# Structure: {level: [2nd_win_bonus, 3rd_win_bonus, 4th+_win_bonus]}
BERSERKER_BONUS = {
    1: [0.01, 0.02, 0.03],
    2: [0.02, 0.04, 0.06],
    3: [0.03, 0.06, 0.09],
    4: [0.04, 0.08, 0.12],
}

# --- Games ---
GAME_NAMES = [
    "Drop the Handkerchief",
    "Manga Accurate DTH",
    "Game of Pure Strategy",
    "Bloody Dotty",
    "Combination",
    "Air Poker",
    "Contradiction",
    "Knucklebones",
    "King of Diamonds",
]

# Maps command aliases → canonical game name
GAME_ALIASES = {
    "dth":      "Drop the Handkerchief",
    "mdth":     "Manga Accurate DTH",
    "gops":     "Game of Pure Strategy",
    "dotty":    "Bloody Dotty",
    "comb":     "Combination",
    "airpoker": "Air Poker",
    "contr":    "Contradiction",
    "kb":       "Knucklebones",
    "kod":      "King of Diamonds",
}

# Games that can produce a draw
GAMES_WITH_DRAWS = {"Game of Pure Strategy", "Air Poker", "King of Diamonds", "Knucklebones"}

# Default per-guild game list (used when a guild hasn't customised theirs).
# Each entry: {"full_name": str, "alias": str, "can_draw": bool}
DEFAULT_GUILD_GAMES: list[dict] = [
    {"full_name": name, "alias": alias, "can_draw": name in GAMES_WITH_DRAWS}
    for alias, name in GAME_ALIASES.items()
]

# Timeout before opponent can trigger forfeit (seconds)
GAME_TIMEOUT_SECONDS = 36000  # 10 minutes

# KOD lobby
KOD_MIN_PLAYERS = 2   # minimum to start
KOD_MAX_PLAYERS = 50  # safety cap

# --- Shop ---
# Built-in items seeded into the DB on first run.
# Admins can add/remove items at runtime via mod commands.
DEFAULT_SHOP_ITEMS = [
    {
        "item_name":        "Suspicious Stew",
        "price":            250_000,
        "stock":            -1,          # -1 = unlimited
        "max_per_person":   -1,
        "description":      "Resets your class to another random class at lvl 1 (not the same as current). Acts immediately.",
        "is_passive":       False,
    },
    {
        "item_name":        "Polyjuice Potion",
        "price":            1_500_000,
        "stock":            -1,
        "max_per_person":   -1,
        "description":      "Resets your class to a class of your choosing at lvl 1. Acts immediately.",
        "is_passive":       False,
    },
    {
        "item_name":        "Tournament Ticket",
        "price":            50_000_000,
        "stock":            8,
        "max_per_person":   1,
        "description":      "Once 8 players hold a ticket, a single-elimination tournament starts. The winner wins the season.",
        "is_passive":       False,
    },
    {
        "item_name":        "Mute Button",
        "price":            250_000,
        "stock":            -1,
        "max_per_person":   -1,
        "description":      "Mutes a player of your choosing for 10 minutes. Can be used up to 5 times per day.",
        "is_passive":       False,
    },
    {
        "item_name":        "Royal Pass",
        "price":            5_000_000,
        "stock":            -1,
        "max_per_person":   1,
        "description":      "Allows you to skip 1 level on the roadmap. Can be used only once per day.",
        "is_passive":       False,
    },
    {
        "item_name":        "Totem of Undying",
        "price":            20_000_000,
        "stock":            -1,
        "max_per_person":   1,
        "description":      "Saves you from paying after losing any bet. Must be activated before the gamble. Cannot be used in tournaments.",
        "is_passive":       False,
    },
    {
        "item_name":        "Class Level Up",
        "price":            0,           # price is dynamic based on player's current level
        "stock":            -1,
        "max_per_person":   -1,
        "description":      "Levels up your current class. Cost: lvl1→2: 250k | lvl2→3: 3M | lvl3→4: 6M. Max level is 4.",
        "is_passive":       False,
    },
]

# --- Tournament ---
TOURNAMENT_TICKET_COUNT = 8         # tickets needed to trigger tournament
TOURNAMENT_START_DELAY_HOURS = 24   # delay after all tickets bought

# --- Mute Button ---
MUTE_DURATION_MINUTES = 10
MUTE_DAILY_USE_LIMIT = 5

# --- Roles (names — must match role names in the Discord server) ---
MOD_ROLE_NAME = "Mod"
REFEREE_ROLE_NAME = "Referee"

# --- Pagination ---
SCORE_ITEMS_PER_PAGE = 10   # rows shown per page in .score