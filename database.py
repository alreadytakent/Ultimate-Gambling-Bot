# ============================================================
#  database.py — UltimateBot database layer
#
#  Five separate SQLite databases:
#    server.db       — guild settings, channel restrictions
#    players.db      — player profiles, class state, work cooldowns
#    economy.db      — shop items, inventory
#    game_results.db — historical game results (all seasons)
#    sessions.db     — active game sessions (crash recovery)
#
#  All public functions are async (use aiosqlite).
#  Call init_all_databases() once on bot startup.
# ============================================================

import aiosqlite
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ── Paths ────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

SERVER_DB   = DATA_DIR / "server.db"
PLAYERS_DB  = DATA_DIR / "players.db"
ECONOMY_DB  = DATA_DIR / "economy.db"
RESULTS_DB  = DATA_DIR / "game_results.db"
SESSIONS_DB = DATA_DIR / "sessions.db"


# ════════════════════════════════════════════════════════════
#  INITIALISATION
# ════════════════════════════════════════════════════════════

async def init_all_databases(default_shop_items: list[dict]) -> None:
    """Create all tables if they don't exist and seed default shop items."""
    await _init_server_db()
    await _init_players_db()
    await _init_economy_db(default_shop_items)
    await _init_results_db()
    await _init_sessions_db()


async def _init_server_db() -> None:
    import config as _cfg
    async with aiosqlite.connect(SERVER_DB) as db:
        await db.execute(f"""
            CREATE TABLE IF NOT EXISTS guilds (
                guild_id              INTEGER PRIMARY KEY,
                prefix                TEXT    NOT NULL DEFAULT '{_cfg.DEFAULT_PREFIX}',
                currency_emoji        TEXT    NOT NULL DEFAULT '{_cfg.DEFAULT_CURRENCY_EMOJI}',
                currency_name         TEXT    NOT NULL DEFAULT '{_cfg.DEFAULT_CURRENCY_NAME}',
                currency_name_plural  TEXT    NOT NULL DEFAULT '{_cfg.DEFAULT_CURRENCY_NAME_PLURAL}',
                work_amount           INTEGER NOT NULL DEFAULT {_cfg.DEFAULT_WORK_AMOUNT},
                work_cooldown_minutes INTEGER NOT NULL DEFAULT {_cfg.DEFAULT_WORK_COOLDOWN_MINUTES},
                current_season        INTEGER NOT NULL DEFAULT 1,
                required_votes        TEXT    NOT NULL DEFAULT '[]',
                guild_games           TEXT    NOT NULL DEFAULT '[]'
            )
        """)
        # Migrations: add columns to existing tables that pre-date them
        for col_sql in [
            "ALTER TABLE guilds ADD COLUMN required_votes TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE guilds ADD COLUMN guild_games TEXT NOT NULL DEFAULT '[]'",
        ]:
            try:
                await db.execute(col_sql)
                await db.commit()
            except Exception:
                pass  # column already exists
        await db.execute("""
            CREATE TABLE IF NOT EXISTS channel_restrictions (
                guild_id         INTEGER NOT NULL,
                command_category TEXT    NOT NULL,
                channel_id       INTEGER NOT NULL,
                PRIMARY KEY (guild_id, command_category)
            )
        """)
        await db.commit()


async def _init_players_db() -> None:
    async with aiosqlite.connect(PLAYERS_DB) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS players (
                user_id       INTEGER NOT NULL,
                guild_id      INTEGER NOT NULL,
                balance       INTEGER NOT NULL DEFAULT 0,
                class         TEXT,
                class_level   INTEGER NOT NULL DEFAULT 1,
                season_number INTEGER NOT NULL DEFAULT 1,
                joined_at     TEXT    NOT NULL,
                PRIMARY KEY (user_id, guild_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS class_state (
                user_id                    INTEGER NOT NULL,
                guild_id                   INTEGER NOT NULL,
                assassin_target_id         INTEGER,
                assassin_last_assigned     TEXT,
                collector_last_used        TEXT,
                berserker_streak           INTEGER NOT NULL DEFAULT 0,
                specialist_last_assigned   TEXT,
                specialist_objective_game  TEXT,
                specialist_completed_today INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, guild_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS work_cooldowns (
                user_id      INTEGER NOT NULL,
                guild_id     INTEGER NOT NULL,
                last_work    TEXT    NOT NULL,
                PRIMARY KEY (user_id, guild_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS mute_usage (
                user_id   INTEGER NOT NULL,
                guild_id  INTEGER NOT NULL,
                use_date  TEXT    NOT NULL,
                uses_today INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, guild_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS deposits (
                user_id     INTEGER NOT NULL,
                guild_id    INTEGER NOT NULL,
                amount      INTEGER NOT NULL,
                days        INTEGER NOT NULL,
                interest_rate REAL  NOT NULL,
                created_at  TEXT    NOT NULL,
                matures_at  TEXT    NOT NULL,
                PRIMARY KEY (user_id, guild_id)
            )
        """)
        await db.commit()


async def _init_economy_db(default_shop_items: list[dict]) -> None:
    async with aiosqlite.connect(ECONOMY_DB) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS shop_items (
                item_name       TEXT    PRIMARY KEY,
                price           INTEGER NOT NULL DEFAULT 0,
                stock           INTEGER NOT NULL DEFAULT -1,
                max_per_person  INTEGER NOT NULL DEFAULT -1,
                description     TEXT    NOT NULL DEFAULT '',
                is_passive      INTEGER NOT NULL DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                user_id   INTEGER NOT NULL,
                guild_id  INTEGER NOT NULL,
                item_name TEXT    NOT NULL,
                quantity  INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (user_id, guild_id, item_name)
            )
        """)
        # Seed default shop items (skip if already present)
        for item in default_shop_items:
            await db.execute("""
                INSERT OR IGNORE INTO shop_items
                    (item_name, price, stock, max_per_person, description, is_passive)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                item["item_name"],
                item["price"],
                item["stock"],
                item["max_per_person"],
                item["description"],
                int(item["is_passive"]),
            ))
        await db.commit()


async def _init_results_db() -> None:
    async with aiosqlite.connect(RESULTS_DB) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS results (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                game_name             TEXT    NOT NULL,
                player1_id            INTEGER NOT NULL,
                player2_id            INTEGER,
                winner_id             INTEGER,
                is_draw               INTEGER NOT NULL DEFAULT 0,
                guild_id              INTEGER NOT NULL,
                season_number         INTEGER NOT NULL DEFAULT 1,
                played_at             TEXT    NOT NULL,
                verified_by_referee   INTEGER NOT NULL DEFAULT 0,
                bet_amount            INTEGER NOT NULL DEFAULT 0
            )
        """)
        await db.commit()


async def _init_sessions_db() -> None:
    async with aiosqlite.connect(SESSIONS_DB) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS active_sessions (
                session_id    TEXT    PRIMARY KEY,
                game_name     TEXT    NOT NULL,
                guild_id      INTEGER NOT NULL,
                channel_id    INTEGER NOT NULL,
                player_ids    TEXT    NOT NULL,
                state         TEXT    NOT NULL DEFAULT '{}',
                started_at    TEXT    NOT NULL,
                bet_amount    INTEGER NOT NULL DEFAULT 0
            )
        """)
        await db.commit()


# ════════════════════════════════════════════════════════════
#  SERVER / GUILD HELPERS
# ════════════════════════════════════════════════════════════

async def get_guild_settings(guild_id: int) -> dict:
    """Return guild settings row, creating defaults if missing."""
    async with aiosqlite.connect(SERVER_DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guilds WHERE guild_id = ?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            import config as _cfg
            default_games_json = json.dumps(_cfg.DEFAULT_GUILD_GAMES)
            await db.execute("""
                INSERT INTO guilds
                    (guild_id, prefix, currency_emoji, currency_name, currency_name_plural,
                     work_amount, work_cooldown_minutes, current_season, required_votes,
                     guild_games)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, '[]', ?)
            """, (
                guild_id,
                _cfg.DEFAULT_PREFIX,
                _cfg.DEFAULT_CURRENCY_EMOJI,
                _cfg.DEFAULT_CURRENCY_NAME,
                _cfg.DEFAULT_CURRENCY_NAME_PLURAL,
                _cfg.DEFAULT_WORK_AMOUNT,
                _cfg.DEFAULT_WORK_COOLDOWN_MINUTES,
                default_games_json,
            ))
            await db.commit()
            async with db.execute(
                "SELECT * FROM guilds WHERE guild_id = ?", (guild_id,)
            ) as cur:
                row = await cur.fetchone()
        return dict(row)


async def set_guild_setting(guild_id: int, key: str, value) -> None:
    """Update a single column in the guilds table."""
    await get_guild_settings(guild_id)   # ensure row exists
    async with aiosqlite.connect(SERVER_DB) as db:
        await db.execute(
            f"UPDATE guilds SET {key} = ? WHERE guild_id = ?", (value, guild_id)
        )
        await db.commit()


async def get_prefix(guild_id: int) -> str:
    settings = await get_guild_settings(guild_id)
    return settings["prefix"]


async def get_required_votes(guild_id: int) -> list[int]:
    """Return the list of user IDs required to unanimously confirm season commands."""
    settings = await get_guild_settings(guild_id)
    raw = settings.get("required_votes", "[]")
    try:
        return json.loads(raw)
    except Exception:
        return []


async def set_required_votes(guild_id: int, user_ids: list[int]) -> None:
    """Persist the required-votes list for a guild."""
    await set_guild_setting(guild_id, "required_votes", json.dumps(user_ids))


async def get_guild_games(guild_id: int) -> list[dict]:
    """
    Return the per-guild game list.
    Each entry: {"full_name": str, "alias": str, "can_draw": bool}
    Falls back to DEFAULT_GUILD_GAMES if the column is empty / missing.
    """
    import config as _cfg
    settings = await get_guild_settings(guild_id)
    raw = settings.get("guild_games", "[]")
    try:
        games = json.loads(raw)
        if not games:
            return list(_cfg.DEFAULT_GUILD_GAMES)
        return games
    except Exception:
        return list(_cfg.DEFAULT_GUILD_GAMES)


async def set_guild_games(guild_id: int, games: list[dict]) -> None:
    """Persist the per-guild game list."""
    await set_guild_setting(guild_id, "guild_games", json.dumps(games))


async def add_guild_game(guild_id: int, full_name: str, alias: str, can_draw: bool) -> bool:
    """
    Add a game entry to the guild's game list.
    Returns False if an entry with the same alias already exists.
    """
    games = await get_guild_games(guild_id)
    if any(g["alias"].lower() == alias.lower() for g in games):
        return False
    games.append({"full_name": full_name, "alias": alias.lower(), "can_draw": can_draw})
    await set_guild_games(guild_id, games)
    return True


async def remove_guild_game(guild_id: int, full_name: str, alias: str, can_draw: bool) -> bool:
    """
    Remove a game entry that matches all three parameters.
    Returns False if no matching entry was found.
    """
    games = await get_guild_games(guild_id)
    new_games = [
        g for g in games
        if not (
            g["full_name"].lower() == full_name.lower()
            and g["alias"].lower() == alias.lower()
            and bool(g["can_draw"]) == bool(can_draw)
        )
    ]
    if len(new_games) == len(games):
        return False
    await set_guild_games(guild_id, new_games)
    return True


async def set_channel_restriction(guild_id: int, category: str, channel_id: int) -> None:
    async with aiosqlite.connect(SERVER_DB) as db:
        await db.execute("""
            INSERT INTO channel_restrictions (guild_id, command_category, channel_id)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, command_category) DO UPDATE SET channel_id = excluded.channel_id
        """, (guild_id, category, channel_id))
        await db.commit()


async def get_channel_restriction(guild_id: int, category: str) -> Optional[int]:
    async with aiosqlite.connect(SERVER_DB) as db:
        async with db.execute("""
            SELECT channel_id FROM channel_restrictions
            WHERE guild_id = ? AND command_category = ?
        """, (guild_id, category)) as cur:
            row = await cur.fetchone()
    return row[0] if row else None


# ════════════════════════════════════════════════════════════
#  PLAYER HELPERS
# ════════════════════════════════════════════════════════════

async def get_player(user_id: int, guild_id: int) -> Optional[dict]:
    async with aiosqlite.connect(PLAYERS_DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM players WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def create_player(user_id: int, guild_id: int, player_class: str,
                        season_number: int, starting_balance: int = 0) -> dict:
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(PLAYERS_DB) as db:
        await db.execute("""
            INSERT INTO players (user_id, guild_id, balance, class, class_level, season_number, joined_at)
            VALUES (?, ?, ?, ?, 1, ?, ?)
        """, (user_id, guild_id, starting_balance, player_class, season_number, now))
        await db.execute("""
            INSERT OR IGNORE INTO class_state (user_id, guild_id) VALUES (?, ?)
        """, (user_id, guild_id))
        await db.commit()
    return await get_player(user_id, guild_id)


async def update_balance(user_id: int, guild_id: int, delta: int) -> int:
    """Add delta (positive or negative) to balance. Returns new balance."""
    async with aiosqlite.connect(PLAYERS_DB) as db:
        await db.execute("""
            UPDATE players SET balance = balance + ?
            WHERE user_id = ? AND guild_id = ?
        """, (delta, user_id, guild_id))
        await db.commit()
        async with db.execute(
            "SELECT balance FROM players WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id)
        ) as cur:
            row = await cur.fetchone()
    return row[0]


async def set_balance(user_id: int, guild_id: int, amount: int) -> None:
    async with aiosqlite.connect(PLAYERS_DB) as db:
        await db.execute(
            "UPDATE players SET balance = ? WHERE user_id = ? AND guild_id = ?",
            (amount, user_id, guild_id)
        )
        await db.commit()


async def get_leaderboard(guild_id: int, limit: int = 10) -> list[dict]:
    async with aiosqlite.connect(PLAYERS_DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT user_id, balance, class, class_level
            FROM players
            WHERE guild_id = ?
            ORDER BY balance DESC
            LIMIT ?
        """, (guild_id, limit)) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_class_state(user_id: int, guild_id: int) -> Optional[dict]:
    async with aiosqlite.connect(PLAYERS_DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM class_state WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def update_class_state(user_id: int, guild_id: int, **kwargs) -> None:
    """Update one or more columns in class_state."""
    if not kwargs:
        return
    cols = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [user_id, guild_id]
    async with aiosqlite.connect(PLAYERS_DB) as db:
        await db.execute(
            f"UPDATE class_state SET {cols} WHERE user_id = ? AND guild_id = ?",
            vals
        )
        await db.commit()


async def set_player_class(user_id: int, guild_id: int, player_class: str, level: int = 1) -> None:
    async with aiosqlite.connect(PLAYERS_DB) as db:
        await db.execute("""
            UPDATE players SET class = ?, class_level = ?
            WHERE user_id = ? AND guild_id = ?
        """, (player_class, level, user_id, guild_id))
        await db.commit()


async def set_class_level(user_id: int, guild_id: int, level: int) -> None:
    async with aiosqlite.connect(PLAYERS_DB) as db:
        await db.execute(
            "UPDATE players SET class_level = ? WHERE user_id = ? AND guild_id = ?",
            (level, user_id, guild_id)
        )
        await db.commit()


async def get_all_players(guild_id: int) -> list[dict]:
    async with aiosqlite.connect(PLAYERS_DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM players WHERE guild_id = ?", (guild_id,)
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ── Work cooldowns ───────────────────────────────────────────

async def get_last_work(user_id: int, guild_id: int) -> Optional[datetime]:
    async with aiosqlite.connect(PLAYERS_DB) as db:
        async with db.execute(
            "SELECT last_work FROM work_cooldowns WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id)
        ) as cur:
            row = await cur.fetchone()
    return datetime.fromisoformat(row[0]) if row else None


async def set_last_work(user_id: int, guild_id: int) -> None:
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(PLAYERS_DB) as db:
        await db.execute("""
            INSERT INTO work_cooldowns (user_id, guild_id, last_work)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, guild_id) DO UPDATE SET last_work = excluded.last_work
        """, (user_id, guild_id, now))
        await db.commit()


# ── Mute usage ───────────────────────────────────────────────

async def get_mute_uses_today(user_id: int, guild_id: int) -> int:
    today = datetime.utcnow().date().isoformat()
    async with aiosqlite.connect(PLAYERS_DB) as db:
        async with db.execute("""
            SELECT uses_today FROM mute_usage
            WHERE user_id = ? AND guild_id = ? AND use_date = ?
        """, (user_id, guild_id, today)) as cur:
            row = await cur.fetchone()
    return row[0] if row else 0


async def increment_mute_uses(user_id: int, guild_id: int) -> None:
    today = datetime.utcnow().date().isoformat()
    async with aiosqlite.connect(PLAYERS_DB) as db:
        await db.execute("""
            INSERT INTO mute_usage (user_id, guild_id, use_date, uses_today)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(user_id, guild_id) DO UPDATE
            SET uses_today = CASE
                WHEN use_date = excluded.use_date THEN uses_today + 1
                ELSE 1
            END,
            use_date = excluded.use_date
        """, (user_id, guild_id, today))
        await db.commit()


# ════════════════════════════════════════════════════════════
#  ECONOMY HELPERS
# ════════════════════════════════════════════════════════════

async def get_shop_items() -> list[dict]:
    async with aiosqlite.connect(ECONOMY_DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM shop_items ORDER BY price ASC") as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_shop_item(item_name: str) -> Optional[dict]:
    async with aiosqlite.connect(ECONOMY_DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM shop_items WHERE LOWER(item_name) = LOWER(?)", (item_name,)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def add_shop_item(item_name: str, price: int, stock: int,
                        max_per_person: int, description: str, is_passive: bool) -> None:
    async with aiosqlite.connect(ECONOMY_DB) as db:
        await db.execute("""
            INSERT INTO shop_items (item_name, price, stock, max_per_person, description, is_passive)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(item_name) DO UPDATE SET
                price = excluded.price,
                stock = excluded.stock,
                max_per_person = excluded.max_per_person,
                description = excluded.description,
                is_passive = excluded.is_passive
        """, (item_name, price, stock, max_per_person, description, int(is_passive)))
        await db.commit()


async def remove_shop_item(item_name: str) -> bool:
    async with aiosqlite.connect(ECONOMY_DB) as db:
        cur = await db.execute(
            "DELETE FROM shop_items WHERE LOWER(item_name) = LOWER(?)", (item_name,)
        )
        await db.commit()
    return cur.rowcount > 0


async def decrement_shop_stock(item_name: str) -> None:
    """Decrease stock by 1 (only if stock is not unlimited/-1)."""
    async with aiosqlite.connect(ECONOMY_DB) as db:
        await db.execute("""
            UPDATE shop_items SET stock = stock - 1
            WHERE LOWER(item_name) = LOWER(?) AND stock > 0
        """, (item_name,))
        await db.commit()


async def get_inventory(user_id: int, guild_id: int) -> list[dict]:
    async with aiosqlite.connect(ECONOMY_DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT item_name, quantity FROM inventory
            WHERE user_id = ? AND guild_id = ? AND quantity > 0
        """, (user_id, guild_id)) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_inventory_item(user_id: int, guild_id: int, item_name: str) -> int:
    """Returns quantity of item in player's inventory (0 if not present)."""
    async with aiosqlite.connect(ECONOMY_DB) as db:
        async with db.execute("""
            SELECT quantity FROM inventory
            WHERE user_id = ? AND guild_id = ? AND LOWER(item_name) = LOWER(?)
        """, (user_id, guild_id, item_name)) as cur:
            row = await cur.fetchone()
    return row[0] if row else 0


async def add_inventory_item(user_id: int, guild_id: int, item_name: str, quantity: int = 1) -> None:
    async with aiosqlite.connect(ECONOMY_DB) as db:
        await db.execute("""
            INSERT INTO inventory (user_id, guild_id, item_name, quantity)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, guild_id, item_name) DO UPDATE SET quantity = quantity + excluded.quantity
        """, (user_id, guild_id, item_name, quantity))
        await db.commit()


async def remove_inventory_item(user_id: int, guild_id: int, item_name: str, quantity: int = 1) -> bool:
    """Remove quantity from inventory. Returns False if not enough."""
    current = await get_inventory_item(user_id, guild_id, item_name)
    if current < quantity:
        return False
    async with aiosqlite.connect(ECONOMY_DB) as db:
        await db.execute("""
            UPDATE inventory SET quantity = quantity - ?
            WHERE user_id = ? AND guild_id = ? AND LOWER(item_name) = LOWER(?)
        """, (quantity, user_id, guild_id, item_name))
        await db.commit()
    return True


async def get_player_item_count(user_id: int, guild_id: int, item_name: str) -> int:
    return await get_inventory_item(user_id, guild_id, item_name)


# ════════════════════════════════════════════════════════════
#  GAME RESULTS HELPERS
# ════════════════════════════════════════════════════════════

async def record_result(
    game_name: str,
    player1_id: int,
    player2_id: Optional[int],
    winner_id: Optional[int],
    is_draw: bool,
    guild_id: int,
    season_number: int,
    bet_amount: int = 0,
    verified_by_referee: bool = False,
) -> int:
    """Insert a game result. Returns the new row id."""
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(RESULTS_DB) as db:
        cur = await db.execute("""
            INSERT INTO results
                (game_name, player1_id, player2_id, winner_id, is_draw,
                 guild_id, season_number, played_at, verified_by_referee, bet_amount)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            game_name, player1_id, player2_id, winner_id, int(is_draw),
            guild_id, season_number, now, int(verified_by_referee), bet_amount,
        ))
        await db.commit()
    return cur.lastrowid


async def get_score_overall(user_id: int, guild_id: int) -> list[dict]:
    """
    Returns per-game win/draw/loss counts across all seasons for a player.
    """
    async with aiosqlite.connect(RESULTS_DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT
                game_name,
                SUM(CASE WHEN winner_id = ? THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN is_draw = 1 AND (player1_id = ? OR player2_id = ?) THEN 1 ELSE 0 END) AS draws,
                SUM(CASE
                    WHEN is_draw = 0 AND winner_id != ? AND (player1_id = ? OR player2_id = ?)
                    THEN 1 ELSE 0 END) AS losses
            FROM results
            WHERE guild_id = ? AND (player1_id = ? OR player2_id = ?)
            GROUP BY game_name
        """, (user_id, user_id, user_id, user_id, user_id, user_id, guild_id, user_id, user_id)) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_score_by_season(user_id: int, guild_id: int, season_number: int) -> list[dict]:
    async with aiosqlite.connect(RESULTS_DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT
                game_name,
                SUM(CASE WHEN winner_id = ? THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN is_draw = 1 AND (player1_id = ? OR player2_id = ?) THEN 1 ELSE 0 END) AS draws,
                SUM(CASE
                    WHEN is_draw = 0 AND winner_id != ? AND (player1_id = ? OR player2_id = ?)
                    THEN 1 ELSE 0 END) AS losses
            FROM results
            WHERE guild_id = ? AND season_number = ? AND (player1_id = ? OR player2_id = ?)
            GROUP BY game_name
        """, (user_id, user_id, user_id, user_id, user_id, user_id,
              guild_id, season_number, user_id, user_id)) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_head_to_head(player1_id: int, player2_id: int, guild_id: int) -> list[dict]:
    """Win/draw/loss for player1 vs player2, per game, all seasons."""
    async with aiosqlite.connect(RESULTS_DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT
                game_name,
                SUM(CASE WHEN winner_id = ? THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN is_draw = 1 THEN 1 ELSE 0 END) AS draws,
                SUM(CASE WHEN winner_id = ? THEN 1 ELSE 0 END) AS losses
            FROM results
            WHERE guild_id = ?
              AND ((player1_id = ? AND player2_id = ?)
                OR (player1_id = ? AND player2_id = ?))
            GROUP BY game_name
        """, (player1_id, player2_id, guild_id,
              player1_id, player2_id, player2_id, player1_id)) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_seasons_played(guild_id: int) -> list[int]:
    """Returns sorted list of season numbers that have results."""
    async with aiosqlite.connect(RESULTS_DB) as db:
        async with db.execute("""
            SELECT DISTINCT season_number FROM results
            WHERE guild_id = ? ORDER BY season_number ASC
        """, (guild_id,)) as cur:
            rows = await cur.fetchall()
    return [r[0] for r in rows]


# ════════════════════════════════════════════════════════════
#  SESSION HELPERS
# ════════════════════════════════════════════════════════════

async def save_session(session_id: str, game_name: str, guild_id: int,
                       channel_id: int, player_ids: list[int],
                       state: dict, bet_amount: int) -> None:
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(SESSIONS_DB) as db:
        await db.execute("""
            INSERT INTO active_sessions
                (session_id, game_name, guild_id, channel_id, player_ids, state, started_at, bet_amount)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET state = excluded.state
        """, (
            session_id, game_name, guild_id, channel_id,
            json.dumps(player_ids), json.dumps(state), now, bet_amount,
        ))
        await db.commit()


async def update_session_state(session_id: str, state: dict) -> None:
    async with aiosqlite.connect(SESSIONS_DB) as db:
        await db.execute(
            "UPDATE active_sessions SET state = ? WHERE session_id = ?",
            (json.dumps(state), session_id)
        )
        await db.commit()


async def delete_session(session_id: str) -> None:
    async with aiosqlite.connect(SESSIONS_DB) as db:
        await db.execute(
            "DELETE FROM active_sessions WHERE session_id = ?", (session_id,)
        )
        await db.commit()


async def get_session(session_id: str) -> Optional[dict]:
    async with aiosqlite.connect(SESSIONS_DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM active_sessions WHERE session_id = ?", (session_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    d = dict(row)
    d["player_ids"] = json.loads(d["player_ids"])
    d["state"] = json.loads(d["state"])
    return d


async def get_all_sessions(guild_id: int) -> list[dict]:
    async with aiosqlite.connect(SESSIONS_DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM active_sessions WHERE guild_id = ?", (guild_id,)
        ) as cur:
            rows = await cur.fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["player_ids"] = json.loads(d["player_ids"])
        d["state"] = json.loads(d["state"])
        result.append(d)
    return result


async def get_player_session(user_id: int, guild_id: int) -> Optional[dict]:
    """Find the active session for a user in a guild (max 1)."""
    sessions = await get_all_sessions(guild_id)
    for s in sessions:
        if user_id in s["player_ids"]:
            return s
    return None


async def get_player_session_by_dm(user_id: int) -> Optional[dict]:
    """Find any active session for a user across ALL guilds (for DM routing)."""
    async with aiosqlite.connect(SESSIONS_DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM active_sessions"
        ) as cur:
            rows = await cur.fetchall()
    for row in rows:
        d = dict(row)
        d["player_ids"] = json.loads(d["player_ids"])
        d["state"] = json.loads(d["state"])
        if user_id in d["player_ids"]:
            return d
    return None


async def erase_result(
    winner_id: int,
    loser_id: int,
    game_name: str,
    guild_id: int,
    bet_amount: int = 0,
) -> bool:
    """
    Delete the most recent result matching winner, loser, game and guild.
    If bet_amount > 0, also matches on bet_amount.
    Returns True if a row was deleted, False if nothing matched.
    """
    async with aiosqlite.connect(RESULTS_DB) as db:
        if bet_amount > 0:
            async with db.execute("""
                SELECT id FROM results
                WHERE guild_id = ? AND game_name = ? AND winner_id = ?
                  AND player2_id = ? AND bet_amount = ?
                ORDER BY played_at DESC
                LIMIT 1
            """, (guild_id, game_name, winner_id, loser_id, bet_amount)) as cur:
                row = await cur.fetchone()
        else:
            async with db.execute("""
                SELECT id FROM results
                WHERE guild_id = ? AND game_name = ? AND winner_id = ?
                  AND player2_id = ?
                ORDER BY played_at DESC
                LIMIT 1
            """, (guild_id, game_name, winner_id, loser_id)) as cur:
                row = await cur.fetchone()

        if row is None:
            return False

        await db.execute("DELETE FROM results WHERE id = ?", (row[0],))
        await db.commit()
    return True


async def erase_draw_result(
    player1_id: int,
    player2_id: int,
    game_name: str,
    guild_id: int,
    bet_amount: int = 0,
) -> bool:
    """
    Delete the most recent draw result involving these two players.
    Player order doesn't matter — checks both orderings.
    If bet_amount > 0, also matches on bet_amount.
    Returns True if a row was deleted, False if nothing matched.
    """
    async with aiosqlite.connect(RESULTS_DB) as db:
        if bet_amount > 0:
            async with db.execute("""
                SELECT id FROM results
                WHERE guild_id = ? AND game_name = ? AND is_draw = 1
                  AND bet_amount = ?
                  AND ((player1_id = ? AND player2_id = ?)
                    OR (player1_id = ? AND player2_id = ?))
                ORDER BY played_at DESC
                LIMIT 1
            """, (guild_id, game_name, bet_amount,
                  player1_id, player2_id, player2_id, player1_id)) as cur:
                row = await cur.fetchone()
        else:
            async with db.execute("""
                SELECT id FROM results
                WHERE guild_id = ? AND game_name = ? AND is_draw = 1
                  AND ((player1_id = ? AND player2_id = ?)
                    OR (player1_id = ? AND player2_id = ?))
                ORDER BY played_at DESC
                LIMIT 1
            """, (guild_id, game_name,
                  player1_id, player2_id, player2_id, player1_id)) as cur:
                row = await cur.fetchone()

        if row is None:
            return False

        await db.execute("DELETE FROM results WHERE id = ?", (row[0],))
        await db.commit()
    return True


async def get_deposit(user_id: int, guild_id: int) -> Optional[dict]:
    """Return a player's active deposit, or None if they have none."""
    async with aiosqlite.connect(PLAYERS_DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM deposits WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def create_deposit(user_id: int, guild_id: int, amount: int,
                          days: int, interest_rate: float) -> None:
    """Create a new deposit for a player."""
    now      = datetime.utcnow()
    matures  = (now + timedelta(days=days)).isoformat()
    async with aiosqlite.connect(PLAYERS_DB) as db:
        await db.execute("""
            INSERT INTO deposits (user_id, guild_id, amount, days, interest_rate, created_at, matures_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, guild_id, amount, days, interest_rate, now.isoformat(), matures))
        await db.commit()


async def delete_deposit(user_id: int, guild_id: int) -> None:
    """Remove a deposit (after maturity or cancellation)."""
    async with aiosqlite.connect(PLAYERS_DB) as db:
        await db.execute(
            "DELETE FROM deposits WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id)
        )
        await db.commit()


async def get_all_mature_deposits(guild_id: int) -> list[dict]:
    """Return all deposits that have matured (matures_at <= now)."""
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(PLAYERS_DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM deposits WHERE guild_id = ? AND matures_at <= ?
        """, (guild_id, now)) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ════════════════════════════════════════════════════════════
#  SEASON RESET
# ════════════════════════════════════════════════════════════

async def reset_season(guild_id: int) -> int:
    """
    Wipe all player balances, inventories, class states, and work cooldowns
    for a guild. Increment the season counter. Returns the new season number.
    """
    settings = await get_guild_settings(guild_id)
    new_season = settings["current_season"] + 1

    async with aiosqlite.connect(PLAYERS_DB) as db:
        # Reset balances, re-assign classes at level 1 will be handled by season.py
        await db.execute(
            "DELETE FROM players WHERE guild_id = ?", (guild_id,)
        )
        await db.execute(
            "DELETE FROM class_state WHERE guild_id = ?", (guild_id,)
        )
        await db.execute(
            "DELETE FROM work_cooldowns WHERE guild_id = ?", (guild_id,)
        )
        await db.execute(
            "DELETE FROM deposits WHERE guild_id = ?", (guild_id,)
        )
        await db.commit()

    async with aiosqlite.connect(ECONOMY_DB) as db:
        await db.execute(
            "DELETE FROM inventory WHERE guild_id = ?", (guild_id,)
        )
        await db.commit()

    await set_guild_setting(guild_id, "current_season", new_season)
    return new_season