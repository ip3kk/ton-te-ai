"""
TON TE AI — SQLite persistence layer.

Stores users, messages (user ↔ bot), and analytics.
"""
import aiosqlite
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "tonpal.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_db: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        _db = await aiosqlite.connect(str(DB_PATH))
        _db.row_factory = aiosqlite.Row
        await _db.executescript(_SCHEMA)
    return _db


async def close_db():
    global _db
    if _db:
        await _db.close()
        _db = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id       INTEGER PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    last_name   TEXT,
    lang        TEXT DEFAULT 'en',
    first_seen  REAL,
    last_seen   REAL
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id       INTEGER NOT NULL,
    direction   TEXT NOT NULL CHECK(direction IN ('in','out')),
    msg_type    TEXT DEFAULT 'text',
    content     TEXT,
    ts          REAL,
    FOREIGN KEY (tg_id) REFERENCES users(tg_id)
);

CREATE INDEX IF NOT EXISTS idx_msg_tg_id ON messages(tg_id);
CREATE INDEX IF NOT EXISTS idx_msg_ts    ON messages(ts DESC);

CREATE TABLE IF NOT EXISTS watchlist (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id       INTEGER NOT NULL,
    address     TEXT NOT NULL,
    label       TEXT,
    last_lt     TEXT DEFAULT '0',
    created     REAL,
    UNIQUE(tg_id, address)
);

CREATE INDEX IF NOT EXISTS idx_watch_addr ON watchlist(address);
"""


async def upsert_user(
    tg_id: int,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    lang: str | None = None,
):
    db = await get_db()
    now = time.time()
    await db.execute(
        """INSERT INTO users (tg_id, username, first_name, last_name, lang, first_seen, last_seen)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(tg_id) DO UPDATE SET
               username   = COALESCE(excluded.username, users.username),
               first_name = COALESCE(excluded.first_name, users.first_name),
               last_name  = COALESCE(excluded.last_name, users.last_name),
               lang       = COALESCE(excluded.lang, users.lang),
               last_seen  = excluded.last_seen
        """,
        (tg_id, username, first_name, last_name, lang or "en", now, now),
    )
    await db.commit()


async def log_message(
    tg_id: int,
    direction: str,
    content: str,
    msg_type: str = "text",
):
    db = await get_db()
    await db.execute(
        "INSERT INTO messages (tg_id, direction, msg_type, content, ts) VALUES (?, ?, ?, ?, ?)",
        (tg_id, direction, msg_type, content, time.time()),
    )
    await db.commit()


# ── Query helpers for admin panel ──

async def get_stats() -> dict:
    db = await get_db()
    now = time.time()
    day_ago = now - 86400

    total_users = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
    active_today = (await (await db.execute(
        "SELECT COUNT(DISTINCT tg_id) FROM messages WHERE ts > ?", (day_ago,)
    )).fetchone())[0]
    total_msgs = (await (await db.execute("SELECT COUNT(*) FROM messages")).fetchone())[0]
    msgs_today = (await (await db.execute(
        "SELECT COUNT(*) FROM messages WHERE ts > ?", (day_ago,)
    )).fetchone())[0]

    return {
        "total_users": total_users,
        "active_today": active_today,
        "total_messages": total_msgs,
        "messages_today": msgs_today,
    }


async def get_all_users(limit: int = 200, offset: int = 0) -> list[dict]:
    db = await get_db()
    rows = await (await db.execute(
        """SELECT u.tg_id, u.username, u.first_name, u.last_name, u.lang,
                  u.first_seen, u.last_seen,
                  COUNT(m.id) AS msg_count
           FROM users u LEFT JOIN messages m ON u.tg_id = m.tg_id
           GROUP BY u.tg_id
           ORDER BY u.last_seen DESC
           LIMIT ? OFFSET ?""",
        (limit, offset),
    )).fetchall()
    return [dict(r) for r in rows]


async def get_user_messages(tg_id: int, limit: int = 200) -> list[dict]:
    db = await get_db()
    rows = await (await db.execute(
        """SELECT direction, msg_type, content, ts
           FROM messages WHERE tg_id = ?
           ORDER BY ts ASC LIMIT ?""",
        (tg_id, limit),
    )).fetchall()
    return [dict(r) for r in rows]


# ── Watchlist helpers ──

async def add_watch(tg_id: int, address: str, label: str = "") -> bool:
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR IGNORE INTO watchlist (tg_id, address, label, last_lt, created) VALUES (?,?,?,?,?)",
            (tg_id, address, label, "0", time.time()),
        )
        await db.commit()
        return True
    except Exception:
        return False


async def remove_watch(tg_id: int, address: str) -> bool:
    db = await get_db()
    cur = await db.execute(
        "DELETE FROM watchlist WHERE tg_id = ? AND address = ?", (tg_id, address)
    )
    await db.commit()
    return cur.rowcount > 0


async def get_watches(tg_id: int) -> list[dict]:
    db = await get_db()
    rows = await (await db.execute(
        "SELECT address, label, last_lt, created FROM watchlist WHERE tg_id = ? ORDER BY created DESC",
        (tg_id,),
    )).fetchall()
    return [dict(r) for r in rows]


async def get_all_watches() -> list[dict]:
    db = await get_db()
    rows = await (await db.execute(
        "SELECT id, tg_id, address, label, last_lt FROM watchlist"
    )).fetchall()
    return [dict(r) for r in rows]


async def update_watch_lt(tg_id: int, address: str, lt: str):
    db = await get_db()
    await db.execute(
        "UPDATE watchlist SET last_lt = ? WHERE tg_id = ? AND address = ?",
        (lt, tg_id, address),
    )
    await db.commit()


async def count_watches(tg_id: int) -> int:
    db = await get_db()
    return (await (await db.execute(
        "SELECT COUNT(*) FROM watchlist WHERE tg_id = ?", (tg_id,)
    )).fetchone())[0]


async def search_users(query: str) -> list[dict]:
    db = await get_db()
    like = f"%{query}%"
    rows = await (await db.execute(
        """SELECT u.tg_id, u.username, u.first_name, u.last_name, u.lang,
                  u.first_seen, u.last_seen,
                  COUNT(m.id) AS msg_count
           FROM users u LEFT JOIN messages m ON u.tg_id = m.tg_id
           WHERE u.username LIKE ? OR u.first_name LIKE ? OR CAST(u.tg_id AS TEXT) LIKE ?
           GROUP BY u.tg_id
           ORDER BY u.last_seen DESC
           LIMIT 50""",
        (like, like, like),
    )).fetchall()
    return [dict(r) for r in rows]
