"""SQLite persistence + metrics.

One row per remediation attempt. This is the system of record the observability
dashboard reads from — every Devin session the pipeline starts is tracked here
with timing, status, ACU cost, and the resulting PR.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

# Terminal lifecycle states. `pr_open` = Devin opened a PR and it's awaiting
# human review (a success outcome); `completed` = Devin reported itself finished.
# `blocked` is intentionally NOT terminal: a session blocked on (e.g.) missing
# push access can still resolve to a PR once a human unblocks it, so we keep
# polling it.
TERMINAL_STATES = {"pr_open", "completed", "failed", "expired"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS remediations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_number      INTEGER NOT NULL,
    issue_title       TEXT NOT NULL,
    repo              TEXT NOT NULL,
    source            TEXT NOT NULL,            -- scanner | manual
    severity          TEXT,                     -- low | medium | high | critical
    devin_session_id  TEXT,
    devin_session_url TEXT,
    status            TEXT NOT NULL,            -- queued|working|pr_open|completed|failed|blocked|expired
    pr_url            TEXT,
    acu_used          REAL,
    summary           TEXT,
    error             TEXT,
    created_at        REAL NOT NULL,
    updated_at        REAL NOT NULL,
    completed_at      REAL,
    UNIQUE(repo, issue_number)
);
"""


class Store:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with self._lock:
                yield conn
                conn.commit()
        finally:
            conn.close()

    # ---- writes -----------------------------------------------------------
    def create_remediation(
        self,
        *,
        issue_number: int,
        issue_title: str,
        repo: str,
        source: str,
        severity: Optional[str] = None,
    ) -> Optional[int]:
        """Insert a queued remediation. Returns row id, or None if this issue
        is already being handled (dedup on repo+issue_number)."""
        now = time.time()
        with self._conn() as c:
            cur = c.execute(
                """INSERT OR IGNORE INTO remediations
                   (issue_number, issue_title, repo, source, severity, status,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,'queued',?,?)""",
                (issue_number, issue_title, repo, source, severity, now, now),
            )
            return cur.lastrowid if cur.rowcount else None

    def update(self, rid: int, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = time.time()
        if fields.get("status") in TERMINAL_STATES and "completed_at" not in fields:
            fields["completed_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._conn() as c:
            c.execute(f"UPDATE remediations SET {cols} WHERE id=?", (*fields.values(), rid))

    # ---- reads ------------------------------------------------------------
    def get(self, rid: int) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM remediations WHERE id=?", (rid,)).fetchone()
            return dict(row) if row else None

    def in_flight(self) -> list[dict]:
        """Rows that still need polling. Includes `blocked`: a blocked session
        can still reach a PR once a human unblocks it (e.g. grants push access)."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM remediations WHERE status IN ('queued','working','blocked') "
                "AND devin_session_id IS NOT NULL"
            ).fetchall()
            return [dict(r) for r in rows]

    def list_all(self, limit: int = 200) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM remediations ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def metrics(self) -> dict:
        """Aggregate KPIs for the dashboard / VP view."""
        rows = self.list_all(limit=10_000)
        total = len(rows)
        by_status: dict[str, int] = {}
        durations: list[float] = []
        acus: list[float] = []
        prs = 0
        for r in rows:
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1
            if r["pr_url"]:
                prs += 1
            if r["acu_used"]:
                acus.append(r["acu_used"])
            if r["completed_at"] and r["created_at"]:
                durations.append(r["completed_at"] - r["created_at"])

        completed = by_status.get("completed", 0)
        awaiting_review = by_status.get("pr_open", 0)
        # Both a finished session and a PR-up-awaiting-review are successes.
        succeeded = completed + awaiting_review
        failed = by_status.get("failed", 0) + by_status.get("expired", 0)
        resolved = succeeded + failed
        success_rate = round(100 * succeeded / resolved, 1) if resolved else None

        return {
            "total": total,
            "by_status": by_status,
            "active": by_status.get("working", 0) + by_status.get("queued", 0),
            "completed": completed,
            "awaiting_review": awaiting_review,
            "succeeded": succeeded,
            "failed": failed,
            "blocked": by_status.get("blocked", 0),
            "prs_opened": prs,
            "success_rate": success_rate,
            "avg_minutes": round(sum(durations) / len(durations) / 60, 1) if durations else None,
            "total_acus": round(sum(acus), 2) if acus else 0,
            # Rough engineering-hours saved: ~2.5h/issue of senior time, a
            # conservative figure for triage+fix+PR on a security issue.
            "eng_hours_saved": round(succeeded * 2.5, 1),
        }
