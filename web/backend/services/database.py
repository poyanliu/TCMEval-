"""SQLite database layer for evaluation result persistence.

Schema
  evaluations          — one row per uploaded document
  primary_results      — 7-per-evaluation (one per primary indicator)
  secondary_results    — 16-per-evaluation (one per secondary indicator)
  additional_results   — 0-or-1-per-evaluation (the +/- 5 bonus item)
"""

import json
import logging
import os
import sqlite3
from datetime import datetime
from threading import Lock
from typing import Optional

from backend.config import DATABASE_PATH

logger = logging.getLogger(__name__)
_lock = Lock()

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS evaluations (
    id              TEXT PRIMARY KEY,
    ip_address      TEXT    NOT NULL DEFAULT 'unknown',
    doc_name        TEXT    NOT NULL,
    filename        TEXT    NOT NULL DEFAULT '',
    file_hash       TEXT    NOT NULL DEFAULT '',
    timestamp       TEXT    NOT NULL,
    total_score     REAL    NOT NULL,
    base_score      REAL    NOT NULL,
    overall_comment TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS primary_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_id   TEXT    NOT NULL REFERENCES evaluations(id) ON DELETE CASCADE,
    indicator_id    TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    weight          INTEGER NOT NULL,
    score           INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS secondary_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_id   TEXT    NOT NULL REFERENCES evaluations(id) ON DELETE CASCADE,
    indicator_id    TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    max_score       INTEGER NOT NULL,
    score           INTEGER NOT NULL,
    evidence        TEXT    NOT NULL DEFAULT '',
    comment         TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS additional_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_id   TEXT    NOT NULL REFERENCES evaluations(id) ON DELETE CASCADE,
    name            TEXT    NOT NULL DEFAULT '学科适配性',
    score           INTEGER NOT NULL DEFAULT 0,
    comment         TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT    NOT NULL UNIQUE,
    password_hash   TEXT    NOT NULL,
    salt            TEXT    NOT NULL,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_eval_ip        ON evaluations(ip_address);
CREATE INDEX IF NOT EXISTS idx_eval_created   ON evaluations(created_at);
CREATE INDEX IF NOT EXISTS idx_primary_eval   ON primary_results(evaluation_id);
CREATE INDEX IF NOT EXISTS idx_secondary_eval ON secondary_results(evaluation_id);
CREATE INDEX IF NOT EXISTS idx_additional_eval ON additional_results(evaluation_id);
"""


def _ensure_db() -> None:
    os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)


def get_conn() -> sqlite3.Connection:
    """Return a new connection (caller must close)."""
    _ensure_db()
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables and indexes if they do not exist, then run migrations."""
    with _lock:
        conn = get_conn()
        try:
            conn.executescript(SCHEMA_SQL)
            # Migration: add file_hash column to existing databases
            _migrate_add_column(conn, "evaluations", "file_hash", "TEXT NOT NULL DEFAULT ''")
            # Migration: add scale_factor and excluded_indicators columns
            _migrate_add_column(conn, "evaluations", "scale_factor", "REAL NOT NULL DEFAULT 1.0")
            _migrate_add_column(conn, "evaluations", "excluded_indicators", "TEXT NOT NULL DEFAULT ''")
            # Migration: add username column to evaluations
            _migrate_add_column(conn, "evaluations", "username", "TEXT NOT NULL DEFAULT ''")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_eval_username ON evaluations(username)"
            )
            # Seed default users if users table is empty
            _seed_default_users(conn)
            # Index for file_hash (created here so column exists first)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_eval_file_hash ON evaluations(file_hash)"
            )
            conn.commit()
        finally:
            conn.close()
    logger.info("Database initialized at %s", DATABASE_PATH)


def _migrate_add_column(conn: sqlite3.Connection, table: str, column: str, col_def: str) -> None:
    """Add a column if it doesn't already exist (idempotent)."""
    cur = conn.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cur.fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
        conn.commit()
        logger.info("Migration: added %s.%s (%s)", table, column, col_def)


# ── Save ──────────────────────────────────────────────────────────
def save_evaluation(
    *,
    evaluation_id: str,
    ip_address: str,
    doc_name: str,
    username: str = "",
    filename: str = "",
    file_hash: str = "",
    timestamp: str = "",
    total_score: float = 0.0,
    base_score: float = 0.0,
    scale_factor: float = 1.0,
    excluded_indicators: list | None = None,
    primary_results: list | None = None,
    secondary_results_map: dict | None = None,
    additional_results: list | None = None,
    overall_comment: str = "",
) -> None:
    """Persist a full evaluation result (one transaction)."""
    ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with _lock:
        conn = get_conn()
        try:
            conn.execute(
                """INSERT INTO evaluations
                   (id, ip_address, doc_name, username, filename, file_hash,
                    timestamp, total_score, base_score, scale_factor,
                    excluded_indicators, overall_comment)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (evaluation_id, ip_address, doc_name, username,
                 filename, file_hash, ts,
                 total_score, base_score, scale_factor,
                 ",".join(excluded_indicators) if excluded_indicators else "",
                 overall_comment),
            )

            for p in (primary_results or []):
                conn.execute(
                    """INSERT INTO primary_results
                       (evaluation_id, indicator_id, name, weight, score)
                       VALUES (?, ?, ?, ?, ?)""",
                    (evaluation_id, p["id"], p["name"], p["weight"], p["score"]),
                )

            # secondary_results_map: dict[indicator_id -> SecondaryResult]
            if secondary_results_map:
                for sid, s in secondary_results_map.items():
                    conn.execute(
                        """INSERT INTO secondary_results
                           (evaluation_id, indicator_id, name, max_score, score, evidence, comment)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (evaluation_id, s["id"], s["name"], s["max_score"], s["score"],
                         s.get("evidence", ""), s.get("comment", "")),
                    )

            for a in (additional_results or []):
                conn.execute(
                    """INSERT INTO additional_results (evaluation_id, name, score, comment)
                       VALUES (?, ?, ?, ?)""",
                    (evaluation_id,
                     a.get("name", ""),
                     a.get("score", 0),
                     a.get("comment", "")),
                )

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


# ── Load ──────────────────────────────────────────────────────────
def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def list_evaluations(
    limit: int = 20,
    ip_address: Optional[str] = None,
) -> list[dict]:
    """Return recent evaluations, optionally filtered by IP.

    Set limit=0 for unlimited (all records).
    """
    conn = get_conn()
    try:
        if ip_address:
            if limit > 0:
                rows = conn.execute(
                    "SELECT * FROM evaluations WHERE ip_address = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (ip_address, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM evaluations WHERE ip_address = ? "
                    "ORDER BY created_at DESC",
                    (ip_address,),
                ).fetchall()
        else:
            if limit > 0:
                rows = conn.execute(
                    "SELECT * FROM evaluations ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM evaluations ORDER BY created_at DESC"
                ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_evaluation(evaluation_id: str) -> Optional[dict]:
    """Fetch a single evaluation with all related result rows."""
    conn = get_conn()
    try:
        eval_row = conn.execute(
            "SELECT * FROM evaluations WHERE id = ?", (evaluation_id,)
        ).fetchone()
        if eval_row is None:
            return None

        result = _row_to_dict(eval_row)
        result["primary_results"] = [
            _row_to_dict(r) for r in conn.execute(
                "SELECT * FROM primary_results WHERE evaluation_id = ? ORDER BY indicator_id",
                (evaluation_id,),
            ).fetchall()
        ]
        result["secondary_results"] = [
            _row_to_dict(r) for r in conn.execute(
                "SELECT * FROM secondary_results WHERE evaluation_id = ? ORDER BY indicator_id",
                (evaluation_id,),
            ).fetchall()
        ]
        add_row = conn.execute(
            "SELECT * FROM additional_results WHERE evaluation_id = ?",
            (evaluation_id,),
        ).fetchone()
        result["additional_result"] = _row_to_dict(add_row) if add_row else None
        return result
    finally:
        conn.close()


def list_evaluations_full(
    limit: int = 20,
    ip_address: Optional[str] = None,
    username: Optional[str] = None,
) -> list[dict]:
    """Return recent evaluations with all nested results in a single batch.

    Accepts optional ip_address (legacy) or username filter.
    Set limit=0 for unlimited (all records).
    """
    conn = get_conn()
    try:
        # 1. Fetch evaluation headers
        if username:
            query = (
                "SELECT * FROM evaluations WHERE username = ? "
                "ORDER BY created_at DESC"
            )
            params: tuple = (username,)
            eval_rows = conn.execute(query, params).fetchall()
        elif ip_address:
            query = (
                "SELECT * FROM evaluations WHERE ip_address = ? "
                "ORDER BY created_at DESC"
            )
            params = (ip_address,)
            eval_rows = conn.execute(query, params).fetchall()
        else:
            eval_rows = conn.execute(
                "SELECT * FROM evaluations ORDER BY created_at DESC"
            ).fetchall()

        if limit > 0:
            eval_rows = eval_rows[:limit]

        if not eval_rows:
            return []

        # 2. Collect IDs for batch queries
        eval_dicts = [_row_to_dict(r) for r in eval_rows]
        eval_ids = [d["id"] for d in eval_dicts]
        placeholders = ",".join("?" for _ in eval_ids)

        # 3. Batch fetch all related rows
        primary_rows = conn.execute(
            f"SELECT * FROM primary_results WHERE evaluation_id IN ({placeholders}) "
            "ORDER BY evaluation_id, indicator_id",
            eval_ids,
        ).fetchall()

        secondary_rows = conn.execute(
            f"SELECT * FROM secondary_results WHERE evaluation_id IN ({placeholders}) "
            "ORDER BY evaluation_id, indicator_id",
            eval_ids,
        ).fetchall()

        add_rows = conn.execute(
            f"SELECT * FROM additional_results WHERE evaluation_id IN ({placeholders})",
            eval_ids,
        ).fetchall()

        # 4. Group by evaluation_id
        primaries_by_eval: dict[str, list] = {}
        for r in primary_rows:
            d = _row_to_dict(r)
            primaries_by_eval.setdefault(d["evaluation_id"], []).append(d)

        secondaries_by_eval: dict[str, list] = {}
        for r in secondary_rows:
            d = _row_to_dict(r)
            secondaries_by_eval.setdefault(d["evaluation_id"], []).append(d)

        adds_by_eval: dict[str, dict] = {}
        for r in add_rows:
            d = _row_to_dict(r)
            adds_by_eval[d["evaluation_id"]] = d

        # 5. Assemble results
        for d in eval_dicts:
            eid = d["id"]
            d["primary_results"] = primaries_by_eval.get(eid, [])
            d["secondary_results"] = secondaries_by_eval.get(eid, [])
            d["additional_result"] = adds_by_eval.get(eid)

        return eval_dicts
    finally:
        conn.close()


def find_by_file_hash(file_hash: str) -> Optional[dict]:
    """Return a full evaluation dict if a file with this hash was already evaluated."""
    if not file_hash:
        return None
    conn = get_conn()
    try:
        eval_row = conn.execute(
            "SELECT id FROM evaluations WHERE file_hash = ? AND file_hash != '' "
            "ORDER BY created_at DESC LIMIT 1",
            (file_hash,),
        ).fetchone()
        if eval_row is None:
            return None
        return get_evaluation(eval_row["id"])
    finally:
        conn.close()


def delete_evaluation(evaluation_id: str) -> bool:
    """Delete an evaluation and its related rows. Returns True if found."""
    with _lock:
        conn = get_conn()
        try:
            cur = conn.execute(
                "DELETE FROM evaluations WHERE id = ?", (evaluation_id,)
            )
            deleted = cur.rowcount > 0
            conn.commit()
            return deleted
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def list_unique_ips() -> list[dict]:
    """Return distinct IPs with evaluation counts."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT ip_address, COUNT(*) AS count, MAX(created_at) AS last_seen "
            "FROM evaluations GROUP BY ip_address ORDER BY last_seen DESC"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def count_evaluations(ip_address: Optional[str] = None, username: Optional[str] = None) -> int:
    """Return total evaluation count, optionally filtered by IP or username."""
    conn = get_conn()
    try:
        if username:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM evaluations WHERE username = ?",
                (username,),
            ).fetchone()
        elif ip_address:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM evaluations WHERE ip_address = ?",
                (ip_address,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM evaluations"
            ).fetchone()
        return row["cnt"] if row else 0
    finally:
        conn.close()


# ── User management ────────────────────────────────────────────────

def _seed_default_users(conn: sqlite3.Connection) -> None:
    """Insert default test accounts if the users table is empty."""
    import hashlib
    import secrets

    cur = conn.execute("SELECT COUNT(*) FROM users")
    if cur.fetchone()[0] > 0:
        return

    default_users = ["root", "ABC", "DEF", "HIJK"]
    default_password = "123456"

    for username in default_users:
        salt = secrets.token_hex(16)
        pw_hash = hashlib.pbkdf2_hmac(
            "sha256", default_password.encode("utf-8"), salt.encode("utf-8"), 100000,
        )
        conn.execute(
            "INSERT INTO users (username, password_hash, salt) VALUES (?, ?, ?)",
            (username, pw_hash.hex(), salt),
        )
    conn.commit()
    logger.info("Seeded %d default users", len(default_users))


def verify_user(username: str, password: str) -> bool:
    """Verify a username/password pair against the database."""
    import hashlib
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT password_hash, salt FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if row is None:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), row["salt"].encode("utf-8"), 100000,
        )
        return digest.hex() == row["password_hash"]
    finally:
        conn.close()


def user_exists(username: str) -> bool:
    """Check whether a username exists in the database."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM users WHERE username = ?", (username,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


# ── Init on import ────────────────────────────────────────────────
init_db()
