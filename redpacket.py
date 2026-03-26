"""
TonPal — Red Packet (Hongbao) for TON.

SQLite-backed packets/claims. MCP sends TON/jettons on claim.
Chain comment format: rp:<packet_id>:<claim_idx> for tracing.
"""
import json
import logging
import random
import sqlite3
import string
import time
from pathlib import Path
from typing import Any, List, Optional

from config import DATA_DIR, USDT_JETTON

log = logging.getLogger("tonpal.redpacket")

DB_PATH = DATA_DIR / "redpacket.sqlite3"

# Limits (per plan)
MAX_PACKET_TON = 100.0  # single packet max
MAX_PACKET_USDT = 10000.0  # USDT jetton max
MAX_PACKET_COUNT = 50  # max shares per packet
MAX_DAILY_CREATE = 100  # per creator
DEFAULT_EXPIRE_HOURS = 24
MIN_TON_PER_SHARE = 0.001  # avoid dust
MIN_USDT_PER_SHARE = 0.01  # avoid dust
USDT_DECIMALS = 6

# Modes
MODE_LUCKY = "lucky"  # random amounts, last gets remainder
MODE_FIXED = "fixed"  # equal amounts


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create packets and claims tables."""
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS packets (
                packet_id TEXT PRIMARY KEY,
                creator_id INTEGER NOT NULL,
                mode TEXT NOT NULL,
                total_amount_real TEXT NOT NULL,
                share_count INTEGER NOT NULL,
                shares_json TEXT NOT NULL,
                asset_type TEXT NOT NULL DEFAULT 'TON',
                jetton_addr TEXT,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS claims (
                packet_id TEXT NOT NULL,
                claim_idx INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                to_address TEXT NOT NULL,
                amount_real TEXT NOT NULL,
                tx_result TEXT,
                claimed_at REAL NOT NULL,
                PRIMARY KEY (packet_id, claim_idx),
                FOREIGN KEY (packet_id) REFERENCES packets(packet_id),
                UNIQUE (packet_id, user_id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_packets_expires ON packets(expires_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_claims_packet ON claims(packet_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_packets_creator ON packets(creator_id, created_at)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS packet_group_messages (
                packet_id TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                inline_message_id TEXT,
                PRIMARY KEY (packet_id, chat_id, message_id),
                FOREIGN KEY (packet_id) REFERENCES packets(packet_id)
            )
        """)
        try:
            conn.execute("ALTER TABLE packet_group_messages ADD COLUMN inline_message_id TEXT")
        except sqlite3.OperationalError:
            pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_addresses (
                user_id INTEGER PRIMARY KEY,
                ton_address TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                first_seen REAL NOT NULL,
                last_seen REAL NOT NULL,
                touch_count INTEGER NOT NULL DEFAULT 1
            )
        """)
        conn.commit()
    log.info("redpacket DB initialized at %s", DB_PATH)


def record_bot_user(
    user_id: int,
    username: Optional[str],
    first_name: Optional[str],
    last_name: Optional[str],
) -> None:
    """Upsert anyone who interacted with the bot (for /admin visitor list)."""
    now = time.time()
    un = (username or "").strip()
    fn = (first_name or "").strip()
    ln = (last_name or "").strip()
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO bot_users (user_id, username, first_name, last_name, first_seen, last_seen, touch_count)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name,
                last_name=excluded.last_name,
                last_seen=excluded.last_seen,
                touch_count=bot_users.touch_count + 1
            """,
            (user_id, un, fn, ln, now, now),
        )
        conn.commit()


def list_recent_bot_users(limit: int = 25) -> List[dict[str, Any]]:
    """Recent visitors by last_seen."""
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT user_id, username, first_name, last_name, last_seen, touch_count
            FROM bot_users ORDER BY last_seen DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_recent_claims(limit: int = 30) -> List[dict[str, Any]]:
    """Recent red-packet claims for admin (user_id, payout address, amounts)."""
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT c.packet_id, c.claim_idx, c.user_id, c.to_address, c.amount_real,
                   c.tx_result, c.claimed_at,
                   b.username AS visitor_username, b.first_name AS visitor_first_name,
                   p.asset_type AS packet_asset_type
            FROM claims c
            LEFT JOIN bot_users b ON c.user_id = b.user_id
            LEFT JOIN packets p ON c.packet_id = p.packet_id
            ORDER BY c.claimed_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_admin_dashboard_stats() -> dict:
    """Read-only stats for /admin (Telegram owner dashboard)."""
    now = time.time()
    day_ago = now - 86400
    with _get_conn() as conn:
        bot_users_n = conn.execute("SELECT COUNT(*) FROM bot_users").fetchone()[0]
        total_packets = conn.execute("SELECT COUNT(*) FROM packets").fetchone()[0]
        active_packets = conn.execute(
            "SELECT COUNT(*) FROM packets WHERE expires_at > ?", (now,)
        ).fetchone()[0]
        total_claims = conn.execute(
            """
            SELECT COUNT(*) FROM claims
            WHERE tx_result IS NOT NULL AND TRIM(tx_result) != ''
            """
        ).fetchone()[0]
        claims_24h = conn.execute(
            """
            SELECT COUNT(*) FROM claims
            WHERE claimed_at > ?
              AND tx_result IS NOT NULL AND TRIM(tx_result) != ''
            """,
            (day_ago,),
        ).fetchone()[0]
        saved_addrs = conn.execute("SELECT COUNT(*) FROM user_addresses").fetchone()[0]
        packets_24h = conn.execute(
            "SELECT COUNT(*) FROM packets WHERE created_at > ?", (day_ago,)
        ).fetchone()[0]
    return {
        "bot_users_n": bot_users_n,
        "total_packets": total_packets,
        "active_packets": active_packets,
        "total_claims": total_claims,
        "claims_24h": claims_24h,
        "saved_addresses": saved_addrs,
        "packets_24h": packets_24h,
    }


def get_saved_address(user_id: int) -> Optional[str]:
    """Get user's saved TON address."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT ton_address FROM user_addresses WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return row[0] if row else None


def save_address(user_id: int, address: str) -> None:
    """Save or update user's TON address."""
    with _get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO user_addresses (user_id, ton_address, updated_at) VALUES (?, ?, ?)",
            (user_id, address.strip(), time.time()),
        )
        conn.commit()


def register_group_message(packet_id: str, chat_id: int, message_id: int, inline_message_id: Optional[str] = None) -> None:
    """Track group/inline message for post-claim updates."""
    with _get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO packet_group_messages (packet_id, chat_id, message_id, inline_message_id) VALUES (?, ?, ?, ?)",
            (packet_id, chat_id, message_id, inline_message_id),
        )
        conn.commit()


def get_group_messages(packet_id: str) -> list[dict]:
    """Get tracked messages for this packet. Returns list of dicts with chat_id, message_id, inline_message_id."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT chat_id, message_id, inline_message_id FROM packet_group_messages WHERE packet_id = ?",
            (packet_id,),
        ).fetchall()
        return [{"chat_id": r[0], "message_id": r[1], "inline_message_id": r[2]} for r in rows]


def clear_group_messages(packet_id: str) -> None:
    """Remove tracked messages (after update)."""
    with _get_conn() as conn:
        conn.execute("DELETE FROM packet_group_messages WHERE packet_id = ?", (packet_id,))
        conn.commit()


def _gen_packet_id() -> str:
    chars = string.ascii_lowercase + string.digits
    return "rp_" + "".join(random.choices(chars, k=12))


def _lucky_split(total_nanoton: int, n: int) -> list[int]:
    """Remaining double-mean algorithm. Last share gets remainder. Avoid zeros."""
    if n <= 0:
        return []
    if n == 1:
        return [total_nanoton]
    shares = []
    remain = total_nanoton
    for i in range(n - 1):
        # mean of remaining; take [0.01*mean, 2*mean] to avoid zero
        mean = remain // (n - i)
        if mean < 1:
            mean = 1
        low = max(1, mean // 100)
        high = min(remain - (n - i - 1), 2 * mean)
        if high < low:
            high = low
        amt = random.randint(low, high) if low <= high else low
        shares.append(amt)
        remain -= amt
    shares.append(max(1, remain))  # last gets remainder
    return shares


def create_packet(
    creator_id: int,
    total_ton: str,
    share_count: int,
    mode: str = MODE_LUCKY,
    expire_hours: float = DEFAULT_EXPIRE_HOURS,
    asset_type: str = "TON",
    jetton_addr: Optional[str] = None,
) -> tuple[Optional[str], str]:
    """
    Create a red packet. Returns (packet_id, error_message).
    On success error_message is empty.
    For TON: total_ton is in TON. For USDT: total_ton is in USDT.
    """
    try:
        total = float(total_ton)
    except (ValueError, TypeError):
        return None, "Invalid amount"

    if share_count < 1 or share_count > MAX_PACKET_COUNT:
        return None, f"Share count must be 1–{MAX_PACKET_COUNT}"

    # Rate limit: daily create count
    with _get_conn() as conn:
        day_start = time.time() - 86400
        row = conn.execute(
            "SELECT COUNT(*) FROM packets WHERE creator_id = ? AND created_at > ?",
            (creator_id, day_start),
        ).fetchone()
        if row and row[0] >= MAX_DAILY_CREATE:
            return None, f"Daily limit: max {MAX_DAILY_CREATE} packets per day"

    if asset_type == "USDT" and jetton_addr:
        # USDT: 6 decimals, amount in human units
        if total < MIN_USDT_PER_SHARE or total > MAX_PACKET_USDT:
            return None, f"USDT amount must be between {MIN_USDT_PER_SHARE} and {MAX_PACKET_USDT}"
        total_raw = int(round(total * (10**USDT_DECIMALS)))
        min_per_share = int(MIN_USDT_PER_SHARE * (10**USDT_DECIMALS))
        if total_raw < share_count * min_per_share:
            return None, f"Minimum {MIN_USDT_PER_SHARE} USDT per share"
    else:
        asset_type = "TON"
        jetton_addr = ""
        if total < MIN_TON_PER_SHARE or total > MAX_PACKET_TON:
            return None, f"Amount must be between {MIN_TON_PER_SHARE} and {MAX_PACKET_TON} TON"
        total_raw = int(round(total * 1e9))
        min_per_share = int(MIN_TON_PER_SHARE * 1e9)
        if total_raw < share_count * min_per_share:
            return None, f"Minimum {MIN_TON_PER_SHARE} TON per share"

    if mode == MODE_FIXED:
        per_share = total_raw // share_count
        shares = [per_share] * (share_count - 1) + [total_raw - per_share * (share_count - 1)]
    else:
        shares = _lucky_split(total_raw, share_count)

    packet_id = _gen_packet_id()
    created_at = time.time()
    expires_at = created_at + expire_hours * 3600

    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO packets
               (packet_id, creator_id, mode, total_amount_real, share_count, shares_json,
                asset_type, jetton_addr, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                packet_id,
                creator_id,
                mode,
                total_ton,
                share_count,
                json.dumps([str(s) for s in shares]),
                asset_type,
                jetton_addr or "",
                created_at,
                expires_at,
            ),
        )
        conn.commit()

    log.info("Created packet %s creator=%s shares=%d asset=%s", packet_id, creator_id, share_count, asset_type)
    return packet_id, ""


def get_packet(packet_id: str) -> Optional[dict]:
    """Get packet by id. Returns None if not found or expired."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM packets WHERE packet_id = ? AND expires_at > ?",
            (packet_id, time.time()),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["shares"] = json.loads(d["shares_json"] or "[]")
        return d


def get_claim_count(packet_id: str) -> int:
    """Successful on-chain sends only (matches group card / user-visible progress)."""
    with _get_conn() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM claims
            WHERE packet_id = ?
              AND tx_result IS NOT NULL
              AND TRIM(tx_result) != ''
            """,
            (packet_id,),
        ).fetchone()
        return row[0] if row else 0


def get_reserved_claim_count(packet_id: str) -> int:
    """All claim rows (pending MCP or completed); caps how many users can hold a slot."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM claims WHERE packet_id = ?",
            (packet_id,),
        ).fetchone()
        return row[0] if row else 0


def has_claimed(packet_id: str, user_id: int) -> bool:
    """Check if user already claimed this packet."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM claims WHERE packet_id = ? AND user_id = ?",
            (packet_id, user_id),
        ).fetchone()
        return bool(row)


def allocate_claim(packet_id: str, user_id: int, to_address: str) -> Optional[tuple[int, str, str]]:
    """
    Atomically allocate a claim slot when user provides address.
    Uses the smallest free claim_idx so a row removed after MCP failure can reuse that share.
    Returns (claim_idx, amount_nanoton_str, asset_type) or None.
    """
    p = get_packet(packet_id)
    if not p:
        return None

    share_count = p["share_count"]
    shares = p["shares"]
    asset_type = p.get("asset_type") or "TON"
    addr = to_address.strip()

    for attempt in range(5):
        try:
            with _get_conn() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT 1 FROM claims WHERE packet_id = ? AND user_id = ?",
                    (packet_id, user_id),
                ).fetchone()
                if row:
                    conn.rollback()
                    return None

                idx_rows = conn.execute(
                    "SELECT claim_idx FROM claims WHERE packet_id = ?",
                    (packet_id,),
                ).fetchall()
                used = {int(r[0]) for r in idx_rows}
                claim_idx: Optional[int] = None
                for i in range(share_count):
                    if i not in used:
                        claim_idx = i
                        break
                if claim_idx is None:
                    conn.rollback()
                    return None

                amount_str = shares[claim_idx]
                try:
                    conn.execute(
                        """INSERT INTO claims (packet_id, claim_idx, user_id, to_address, amount_real, claimed_at)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (packet_id, claim_idx, user_id, addr, amount_str, time.time()),
                    )
                    conn.commit()
                except sqlite3.IntegrityError:
                    conn.rollback()
                    continue
            return claim_idx, amount_str, asset_type
        except sqlite3.OperationalError:
            time.sleep(0.02 * (attempt + 1))
            continue

    return None


def release_failed_claim(packet_id: str, claim_idx: int) -> None:
    """After MCP send failure: drop pending row so the share is free and the user may retry."""
    with _get_conn() as conn:
        conn.execute(
            """
            DELETE FROM claims
            WHERE packet_id = ? AND claim_idx = ?
              AND (tx_result IS NULL OR TRIM(tx_result) = '')
            """,
            (packet_id, claim_idx),
        )
        conn.commit()


def set_claim_tx_result(packet_id: str, claim_idx: int, tx_result: str) -> None:
    """Record MCP send result (idempotency: don't resend if already has result)."""
    with _get_conn() as conn:
        conn.execute(
            "UPDATE claims SET tx_result = ? WHERE packet_id = ? AND claim_idx = ?",
            (tx_result, packet_id, claim_idx),
        )
        conn.commit()


def nanoton_to_ton(nanoton_str: str) -> str:
    """Convert nanoton string to TON decimal string."""
    try:
        n = int(nanoton_str)
        return f"{n / 1e9:.9f}".rstrip("0").rstrip(".")
    except (ValueError, TypeError):
        return "0"


def raw_to_usdt(raw_str: str) -> str:
    """Convert raw jetton units (6 decimals) to USDT display string."""
    try:
        n = int(raw_str)
        return f"{n / (10**USDT_DECIMALS):.6f}".rstrip("0").rstrip(".")
    except (ValueError, TypeError):
        return "0"


def get_deep_link(packet_id: str, bot_username: str) -> str:
    """Generate t.me/bot?start= packet link."""
    return f"https://t.me/{bot_username}?start={packet_id}"
