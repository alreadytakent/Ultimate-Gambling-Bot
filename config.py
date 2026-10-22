# ============================================================
#  config.py — UltimateBot Light constants
# ============================================================

DEFAULT_PREFIX = "."

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
    "lab":      "Labyrinth",
}

# Games that can produce a draw
GAMES_WITH_DRAWS = {"Game of Pure Strategy", "Air Poker", "King of Diamonds", "Knucklebones", "Labyrinth"}

# Default per-guild game list (used by .verify / .erase-result / .score).
# Each entry: {"full_name": str, "alias": str, "can_draw": bool}
DEFAULT_GUILD_GAMES: list[dict] = [
    {"full_name": name, "alias": alias, "can_draw": name in GAMES_WITH_DRAWS}
    for alias, name in GAME_ALIASES.items()
]

# Timeout before opponent can trigger forfeit (seconds)
GAME_TIMEOUT_SECONDS = 36000

# KOD lobby
KOD_MIN_PLAYERS = 2   # minimum to start
KOD_MAX_PLAYERS = 50  # safety cap

# --- Roles (names — must match role names in the Discord server) ---
MOD_ROLE_NAME = "Mod"
REFEREE_ROLE_NAME = "Referee"
