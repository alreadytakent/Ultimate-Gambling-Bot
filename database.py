# ============================================================
#  database.py — UltimateBot Light database layer
#
#    data/server.db            — per-guild settings (prefix, game list,
#                                results-file name), channel restrictions
#    data/sessions.db          — active games (crash recovery)
#    data/scores/<server>.db   — ONE FILE PER SERVER, named after the
#                                server; stores only game results
#
#  Results tables (inside each server's file):
#    results      — 1v1 games. Decisive: player1 = winner, player2 = loser.
#                   Draw: the two players (any order).
#    ffa_results  — free-for-all games (King of Diamonds): one row per
#                   participant with outcome 'W' / 'D' / 'L'.
# ============================================================

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiosqlite

import config

DATA_DIR   = Path(__file__).parent / "data"
SCORES_DIR = DATA_DIR / "scores"
DATA_DIR.mkdir(exist_ok=True)
SCORES_DIR.mkdir(parents=True, exist_ok=True)

SERVER_DB   = DATA_DIR / "server.db"
SESSIONS_DB = DATA_DIR / "sessions.db"

_guild_cache: dict[int, dict] = {}
_results_ready: set[Path] = set()
_ALLOWED_SETTINGS = {"prefix", "guild_games", "results_db"}


# ════════════════════════════════════════════════════════════
#  INITIALISATION
# ════════════════════════════════════════════════════════════

async def init_all_databases() -> None:
    await _init_server_db()
    await _init_sessions_db()
    await _migrate_guild_games()
    _guild_cache.clear()


async def _init_server_db() -> None:
    async with aiosqlite.connect(SERVER_DB) as db:
        await db.execute(f"""
            CREATE TABLE IF NOT EXISTS guilds (
                guild_id    INTEGER PRIMARY KEY,
                prefix      TEXT NOT NULL DEFAULT '{config.DEFAULT_PREFIX}',
                guild_games TEXT NOT NULL DEFAULT '[]',
                results_db  TEXT NOT NULL DEFAULT ''
            )
        """)
        # Migrations for a server.db that pre-dates these columns
        for col_sql in [
            "ALTER TABLE guilds ADD COLUMN guild_games TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE guilds ADD COLUMN results_db TEXT NOT NULL DEFAULT ''",
        ]:
            try:
                await db.execute(col_sql)
                await db.commit()
            except Exception:
                pass
        await db.execute("""
            CREATE TABLE IF NOT EXISTS channel_restrictions (
                guild_id         INTEGER NOT NULL,
                command_category TEXT    NOT NULL,
                channel_id       INTEGER NOT NULL,
                PRIMARY KEY (guild_id, command_category)
            )
        """)
        await db.commit()


async def _init_sessions_db() -> None:
    async with aiosqlite.connect(SESSIONS_DB) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS active_sessions (
                session_id  TEXT PRIMARY KEY,
                game_name   TEXT    NOT NULL,
                guild_id    INTEGER NOT NULL,
                channel_id  INTEGER NOT NULL,
                player_ids  TEXT    NOT NULL,
                state       TEXT    NOT NULL DEFAULT '{}',
                started_at  TEXT    NOT NULL
            )
        """)
        await db.commit()


async def _migrate_guild_games() -> None:
    """Append any new default games to every existing guild's game list."""
    async with aiosqlite.connect(SERVER_DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT guild_id, guild_games FROM guilds") as cur:
            rows = await cur.fetchall()
        for row in rows:
            try:
                games: list[dict] = json.loads(row["guild_games"] or "[]")
            except Exception:
                games = []
            existing = {g["alias"].lower() for g in games}
            added = False
            for default in config.DEFAULT_GUILD_GAMES:
                if default["alias"].lower() not in existing:
                    games.append(default)
                    added = True
            if added:
                await db.execute(
                    "UPDATE guilds SET guild_games = ? WHERE guild_id = ?",
                    (json.dumps(games), row["guild_id"]),
                )
        await db.commit()


# ════════════════════════════════════════════════════════════
#  GUILD SETTINGS
# ════════════════════════════════════════════════════════════

async def get_guild_settings(guild_id: int) -> dict:
    """Return guild settings (cached), creating defaults if missing."""
    cached = _guild_cache.get(guild_id)
    if cached is not None:
        return dict(cached)

    async with aiosqlite.connect(SERVER_DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM guilds WHERE guild_id = ?", (guild_id,)) as cur:
            row = await cur.fetchone()
        if row is None:
            await db.execute(
                "INSERT OR IGNORE INTO guilds (guild_id, prefix, guild_games, results_db) "
                "VALUES (?, ?, ?, '')",
                (guild_id, config.DEFAULT_PREFIX, json.dumps(config.DEFAULT_GUILD_GAMES)),
            )
            await db.commit()
            async with db.execute("SELECT * FROM guilds WHERE guild_id = ?", (guild_id,)) as cur:
                row = await cur.fetchone()
    settings = dict(row)
    _guild_cache[guild_id] = settings
    return dict(settings)


async def set_guild_setting(guild_id: int, key: str, value) -> None:
    if key not in _ALLOWED_SETTINGS:
        raise ValueError(f"Unknown setting: {key}")
    await get_guild_settings(guild_id)
    async with aiosqlite.connect(SERVER_DB) as db:
        await db.execute(f"UPDATE guilds SET {key} = ? WHERE guild_id = ?", (value, guild_id))
        await db.commit()
    _guild_cache[guild_id][key] = value


async def get_prefix(guild_id: int) -> str:
    return (await get_guild_settings(guild_id))["prefix"]


# ── Per-guild game list ──────────────────────────────────────

async def get_guild_games(guild_id: int) -> list[dict]:
    settings = await get_guild_settings(guild_id)
    try:
        games = json.loads(settings.get("guild_games") or "[]")
        return games if games else list(config.DEFAULT_GUILD_GAMES)
    except Exception:
        return list(config.DEFAULT_GUILD_GAMES)


async def set_guild_games(guild_id: int, games: list[dict]) -> None:
    await set_guild_setting(guild_id, "guild_games", json.dumps(games))


async def add_guild_game(guild_id: int, full_name: str, alias: str, can_draw: bool) -> bool:
    games = await get_guild_games(guild_id)
    if any(g["alias"].lower() == alias.lower() for g in games):
        return False
    games.append({"full_name": full_name, "alias": alias.lower(), "can_draw": can_draw})
    await set_guild_games(guild_id, games)
    return True


async def remove_guild_game(guild_id: int, full_name: str, alias: str, can_draw: bool) -> bool:
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


# ── Channel restrictions ─────────────────────────────────────

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
        async with db.execute(
            "SELECT channel_id FROM channel_restrictions WHERE guild_id = ? AND command_category = ?",
            (guild_id, category),
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else None


async def clear_channel_restriction(guild_id: int, category: str) -> None:
    async with aiosqlite.connect(SERVER_DB) as db:
        await db.execute(
            "DELETE FROM channel_restrictions WHERE guild_id = ? AND command_category = ?",
            (guild_id, category),
        )
        await db.commit()


# ════════════════════════════════════════════════════════════
#  PER-SERVER RESULTS DATABASE FILES
# ════════════════════════════════════════════════════════════

def _safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".")
    return (cleaned or "server")[:100]


async def _unique_filename(guild_id: int, guild_name: str) -> str:
    """'<Server Name>.db', or '<Server Name> (<id>).db' if another server owns that name."""
    base = _safe_filename(guild_name)
    candidate = f"{base}.db"
    async with aiosqlite.connect(SERVER_DB) as db:
        async with db.execute(
            "SELECT 1 FROM guilds WHERE LOWER(results_db) = LOWER(?) AND guild_id != ?",
            (candidate, guild_id),
        ) as cur:
            taken = await cur.fetchone()
    return f"{base} ({guild_id}).db" if taken else candidate


async def _init_results_db(path: Path) -> None:
    async with aiosqlite.connect(path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS results (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id    TEXT    NOT NULL DEFAULT '',
                game_name   TEXT    NOT NULL,
                player1_id  INTEGER NOT NULL,
                player2_id  INTEGER,
                winner_id   INTEGER,
                is_draw     INTEGER NOT NULL DEFAULT 0,
                played_at   TEXT    NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ffa_results (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id    TEXT    NOT NULL,
                game_name   TEXT    NOT NULL,
                player_id   INTEGER NOT NULL,
                outcome     TEXT    NOT NULL,
                played_at   TEXT    NOT NULL
            )
        """)
        await db.commit()


async def _ensure_results_ready(path: Path) -> None:
    if path not in _results_ready:
        await _init_results_db(path)
        _results_ready.add(path)


async def ensure_guild(guild_id: int, guild_name: str) -> Path:
    """Make sure the guild has settings and its own results file (named after the server)."""
    settings = await get_guild_settings(guild_id)
    filename = settings.get("results_db") or ""
    if not filename:
        filename = await _unique_filename(guild_id, guild_name)
        legacy, target = SCORES_DIR / f"guild_{guild_id}.db", SCORES_DIR / filename
        if legacy.exists() and not target.exists():
            legacy.rename(target)
        await set_guild_setting(guild_id, "results_db", filename)
    path = SCORES_DIR / filename
    await _ensure_results_ready(path)
    return path


async def rename_results_db(guild_id: int, new_guild_name: str) -> None:
    """Rename the results file after the server was renamed."""
    settings = await get_guild_settings(guild_id)
    old_name = settings.get("results_db") or ""
    if not old_name:
        await ensure_guild(guild_id, new_guild_name)
        return

    new_file = await _unique_filename(guild_id, new_guild_name)
    if new_file == old_name:
        return
    old_path, new_path = SCORES_DIR / old_name, SCORES_DIR / new_file

    if new_path.exists() and not (old_path.exists() and old_path.samefile(new_path)):
        new_file = f"{_safe_filename(new_guild_name)} ({guild_id}).db"
        new_path = SCORES_DIR / new_file

    if old_path.exists():
        old_path.rename(new_path)
    _results_ready.discard(old_path)
    await set_guild_setting(guild_id, "results_db", new_file)


async def _results_path(guild_id: int) -> Path:
    settings = await get_guild_settings(guild_id)
    filename = settings.get("results_db") or f"guild_{guild_id}.db"
    path = SCORES_DIR / filename
    await _ensure_results_ready(path)
    return path


# ════════════════════════════════════════════════════════════
#  RESULTS
# ════════════════════════════════════════════════════════════

async def record_result(
    guild_id: int,
    game_name: str,
    player1_id: int,
    player2_id: Optional[int],
    winner_id: Optional[int],
    is_draw: bool,
    match_id: str = "",
) -> int:
    """Insert a 1v1 result (decisive: player1 = winner, player2 = loser)."""
    path = await _results_path(guild_id)
    async with aiosqlite.connect(path) as db:
        cur = await db.execute("""
            INSERT INTO results (match_id, game_name, player1_id, player2_id, winner_id, is_draw, played_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (match_id, game_name, player1_id, player2_id, winner_id,
              int(is_draw), datetime.utcnow().isoformat()))
        await db.commit()
    return cur.lastrowid


async def record_ffa_result(guild_id: int, game_name: str, match_id: str,
                            outcomes: list[tuple[int, str]]) -> None:
    """Insert one row per participant. outcome is 'W', 'D' or 'L'."""
    path = await _results_path(guild_id)
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(path) as db:
        await db.executemany(
            "INSERT INTO ffa_results (match_id, game_name, player_id, outcome, played_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [(match_id, game_name, pid, outcome, now) for pid, outcome in outcomes],
        )
        await db.commit()


async def get_score_overall(user_id: int, guild_id: int) -> list[dict]:
    """Per-game wins/draws/losses for a player (1v1 + free-for-all games)."""
    path = await _results_path(guild_id)
    merged: dict[str, dict] = {}
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT
                game_name,
                SUM(CASE WHEN is_draw = 0 AND winner_id = ? THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN is_draw = 1 THEN 1 ELSE 0 END) AS draws,
                SUM(CASE WHEN is_draw = 0 AND winner_id IS NOT NULL AND winner_id != ?
                         THEN 1 ELSE 0 END) AS losses
            FROM results
            WHERE player1_id = ? OR player2_id = ?
            GROUP BY game_name
        """, (user_id, user_id, user_id, user_id)) as cur:
            for r in await cur.fetchall():
                merged[r["game_name"]] = {
                    "game_name": r["game_name"], "wins": r["wins"] or 0,
                    "draws": r["draws"] or 0, "losses": r["losses"] or 0,
                }
        async with db.execute("""
            SELECT
                game_name,
                SUM(CASE WHEN outcome = 'W' THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN outcome = 'D' THEN 1 ELSE 0 END) AS draws,
                SUM(CASE WHEN outcome = 'L' THEN 1 ELSE 0 END) AS losses
            FROM ffa_results
            WHERE player_id = ?
            GROUP BY game_name
        """, (user_id,)) as cur:
            for r in await cur.fetchall():
                e = merged.setdefault(r["game_name"], {
                    "game_name": r["game_name"], "wins": 0, "draws": 0, "losses": 0})
                e["wins"] += r["wins"] or 0
                e["draws"] += r["draws"] or 0
                e["losses"] += r["losses"] or 0
    return list(merged.values())


async def get_head_to_head(player1_id: int, player2_id: int, guild_id: int) -> list[dict]:
    """Win/draw/loss for player1 vs player2, per game (1v1 results only)."""
    path = await _results_path(guild_id)
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT
                game_name,
                SUM(CASE WHEN is_draw = 0 AND winner_id = ? THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN is_draw = 1 THEN 1 ELSE 0 END) AS draws,
                SUM(CASE WHEN is_draw = 0 AND winner_id = ? THEN 1 ELSE 0 END) AS losses
            FROM results
            WHERE (player1_id = ? AND player2_id = ?) OR (player1_id = ? AND player2_id = ?)
            GROUP BY game_name
        """, (player1_id, player2_id, player1_id, player2_id, player2_id, player1_id)) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def erase_result(guild_id: int, winner_id: int, loser_id: int, game_name: str) -> bool:
    """Delete the most recent decisive 1v1 result matching winner/loser/game."""
    path = await _results_path(guild_id)
    async with aiosqlite.connect(path) as db:
        async with db.execute("""
            SELECT id FROM results
            WHERE is_draw = 0 AND LOWER(game_name) = LOWER(?) AND winner_id = ?
              AND ((player1_id = ? AND player2_id = ?) OR (player1_id = ? AND player2_id = ?))
            ORDER BY id DESC LIMIT 1
        """, (game_name, winner_id, winner_id, loser_id, loser_id, winner_id)) as cur:
            row = await cur.fetchone()
        if row is None:
            return False
        await db.execute("DELETE FROM results WHERE id = ?", (row[0],))
        await db.commit()
    return True


async def erase_draw_result(guild_id: int, player1_id: int, player2_id: int, game_name: str) -> bool:
    """Delete the most recent draw between two players (order doesn't matter)."""
    path = await _results_path(guild_id)
    async with aiosqlite.connect(path) as db:
        async with db.execute("""
            SELECT id FROM results
            WHERE is_draw = 1 AND LOWER(game_name) = LOWER(?)
              AND ((player1_id = ? AND player2_id = ?) OR (player1_id = ? AND player2_id = ?))
            ORDER BY id DESC LIMIT 1
        """, (game_name, player1_id, player2_id, player2_id, player1_id)) as cur:
            row = await cur.fetchone()
        if row is None:
            return False
        await db.execute("DELETE FROM results WHERE id = ?", (row[0],))
        await db.commit()
    return True


async def reset_scores(guild_id: int) -> int:
    """Erase ALL results of a server. Returns the number of deleted rows."""
    path = await _results_path(guild_id)
    async with aiosqlite.connect(path) as db:
        c1 = await db.execute("DELETE FROM results")
        c2 = await db.execute("DELETE FROM ffa_results")
        await db.commit()
        deleted = (c1.rowcount or 0) + (c2.rowcount or 0)
        await db.execute("VACUUM")
    return deleted


# ════════════════════════════════════════════════════════════
#  SESSIONS (crash recovery)
# ════════════════════════════════════════════════════════════

async def save_session(session_id: str, game_name: str, guild_id: int,
                       channel_id: int, player_ids: list[int], state: dict) -> None:
    async with aiosqlite.connect(SESSIONS_DB) as db:
        await db.execute("""
            INSERT INTO active_sessions
                (session_id, game_name, guild_id, channel_id, player_ids, state, started_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET state = excluded.state
        """, (session_id, game_name, guild_id, channel_id,
              json.dumps(player_ids), json.dumps(state, default=str),
              datetime.utcnow().isoformat()))
        await db.commit()


async def update_session_state(session_id: str, state: dict) -> None:
    async with aiosqlite.connect(SESSIONS_DB) as db:
        await db.execute(
            "UPDATE active_sessions SET state = ? WHERE session_id = ?",
            (json.dumps(state, default=str), session_id),
        )
        await db.commit()


async def delete_session(session_id: str) -> None:
    async with aiosqlite.connect(SESSIONS_DB) as db:
        await db.execute("DELETE FROM active_sessions WHERE session_id = ?", (session_id,))
        await db.commit()


async def get_all_sessions() -> list[dict]:
    async with aiosqlite.connect(SESSIONS_DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM active_sessions") as cur:
            rows = await cur.fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["player_ids"] = json.loads(d["player_ids"])
        d["state"] = json.loads(d["state"])
        result.append(d)
    return result
