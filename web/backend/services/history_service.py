"""Evaluation history persistence — SQLite-backed, backward-compatible API.

Keeps the same function signatures as the original JSON-file version so
existing callers (routers, frontend) continue to work unchanged.
"""

import logging
from typing import Optional

from shared.constants import SECONDARY_TO_PRIMARY
from backend.models.schemas import PrimaryResult, AdditionalResult, HistoryRecord
from backend.services.database import (
    save_evaluation,
    list_evaluations,
    list_evaluations_full,
    get_evaluation,
    delete_evaluation,
    list_unique_ips,
    count_evaluations,
    find_by_file_hash,
)

logger = logging.getLogger(__name__)


def load_history(username: Optional[str] = None) -> list[dict]:
    """Return recent evaluation dicts, optionally filtered by username."""
    rows = list_evaluations_full(limit=0, username=username)
    results = []
    for row in rows:
        results.append(_eval_row_to_dict(row))
    return results


def _migrate_ip(from_ip: str, to_ip: str) -> None:
    """Migrate all records from one IP to another (one-time)."""
    import sqlite3
    from backend.config import DATABASE_PATH
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cur = conn.execute(
            "UPDATE evaluations SET ip_address = ? WHERE ip_address = ?",
            (to_ip, from_ip),
        )
        if cur.rowcount > 0:
            conn.commit()
            logger.info("Migrated %d records from '%s' to '%s'", cur.rowcount, from_ip, to_ip)
        conn.close()
    except Exception:
        pass


def save_to_history(
    record_id: str,
    timestamp: str,
    doc_name: str,
    base_score: float = 0.0,
    total_score: float = 0.0,
    scale_factor: float = 1.0,
    excluded_indicators: list[str] | None = None,
    primary_results: list[PrimaryResult] | None = None,
    additional_results: list[AdditionalResult] | None = None,
    overall_comment: str = "",
    ip_address: str = "unknown",
    username: str = "",
    filename: str = "",
    file_hash: str = "",
) -> None:
    """Persist a completed evaluation (kept backward-compatible signature)."""
    primary_list = []
    secondary_map = {}

    for p in (primary_results or []):
        primary_list.append({
            "id": p.id,
            "name": p.name,
            "weight": p.weight,
            "score": p.score,
        })
        for s in p.secondary_results:
            secondary_map[s.id] = {
                "id": s.id,
                "name": s.name,
                "max_score": s.max_score,
                "score": s.score,
                "evidence": s.evidence,
                "comment": s.comment,
            }

    add_list = []
    for a in (additional_results or []):
        add_list.append({
            "name": a.name,
            "score": a.score,
            "comment": a.comment,
        })

    save_evaluation(
        evaluation_id=record_id,
        ip_address=ip_address,
        doc_name=doc_name,
        username=username,
        filename=filename or doc_name,
        file_hash=file_hash,
        timestamp=timestamp,
        total_score=total_score,
        base_score=base_score,
        scale_factor=scale_factor,
        excluded_indicators=excluded_indicators or [],
        primary_results=primary_list,
        secondary_results_map=secondary_map,
        additional_results=add_list,
        overall_comment=overall_comment,
    )


def get_record(record_id: str) -> Optional[dict]:
    """Return a single evaluation dict (backward-compatible)."""
    row = get_evaluation(record_id)
    if row is None:
        return None
    return _eval_row_to_dict(row)


def delete_record(record_id: str) -> bool:
    """Delete by ID. Returns True if the record existed."""
    return delete_evaluation(record_id)


def lookup_cached_result(file_hash: str) -> Optional[dict]:
    """Return an existing evaluation dict if this file hash was already evaluated.

    Returns None if no match found (cache miss).
    """
    row = find_by_file_hash(file_hash)
    if row is None:
        return None
    return _eval_row_to_dict(row)


def list_history(limit: int = 20, username: Optional[str] = None) -> list[dict]:
    """List recent evaluations, optionally filtered by username.

    Uses batch loading for efficiency — 4 queries total regardless of result count.
    """
    rows = list_evaluations_full(limit=limit, username=username)
    results = []
    for row in rows:
        results.append(_eval_row_to_dict(row))
    return results


def get_ip_list() -> list[dict]:
    """Return distinct IPs with eval counts."""
    return list_unique_ips()


def get_total_count(username: Optional[str] = None) -> int:
    """Return total evaluation count, optionally for a specific user."""
    return count_evaluations(username=username)


# ── Helpers ──────────────────────────────────────────────────────
def _eval_row_to_dict(row: dict) -> dict:
    """Convert a flat DB row + nested results into the legacy dict shape."""
    result: dict = {
        "id": row["id"],
        "timestamp": row["timestamp"],
        "doc_name": row["doc_name"],
        "ip_address": row.get("ip_address", "unknown"),
        "username": row.get("username", ""),
        "total_score": row["total_score"],
        "base_score": row["base_score"],
        "scale_factor": row.get("scale_factor", 1.0),
        "excluded_indicators": (
            [x for x in row.get("excluded_indicators", "").split(",") if x]
            if row.get("excluded_indicators", "")
            else []
        ),
        "overall_comment": row.get("overall_comment", ""),
        "primary_results": [],
        "additional_results": [],
    }

    # Rebuild primary_results from flat rows, grouping secondaries
    primary_rows = row.get("primary_results") or []
    secondary_rows = row.get("secondary_results") or []

    # Group secondary results by their parent primary indicator
    sec_by_primary: dict[str, list] = {}
    for sr in secondary_rows:
        pid = SECONDARY_TO_PRIMARY.get(sr["indicator_id"], "")
        sec_by_primary.setdefault(pid, []).append({
            "id": sr["indicator_id"],
            "name": sr["name"],
            "max_score": sr["max_score"],
            "score": sr["score"],
            "evidence": sr.get("evidence", ""),
            "comment": sr.get("comment", ""),
        })

    for pr in primary_rows:
        result["primary_results"].append({
            "id": pr["indicator_id"],
            "name": pr["name"],
            "weight": pr["weight"],
            "score": pr["score"],
            "secondary_results": sec_by_primary.get(pr["indicator_id"], []),
        })

    if row.get("additional_results"):
        result["additional_results"] = row["additional_results"]
    elif row.get("additional_result"):
        # Backward compat: single additional_result -> list
        ar = row["additional_result"]
        result["additional_results"] = [{
            "name": ar.get("name", "学科适配性"),
            "score": ar["score"],
            "comment": ar.get("comment", ""),
        }]
    else:
        result["additional_results"] = []

    return result
