"""Populate the dashboard with representative sample rows.

For previewing the observability dashboard before (or without) live Devin runs.
This writes ONLY to the local SQLite store — it does not touch Devin or GitHub.

    .venv/bin/python -m scripts.demo_data

Clear it again by deleting the DB file (default ./data/pipeline.db).
"""
from __future__ import annotations

import time

from app.config import settings
from app.store import Store

REPO = settings.github_repo

# (issue#, title, source, severity, status, pr, acu, summary, age_min, dur_min)
SAMPLE = [
    (101, "[security] jinja2 2.11.0 is vulnerable (GHSA-h5c8-rqwp-cp95)", "scanner",
     "high", "completed", "/pull/4101", 3.4,
     "Bumped jinja2 2.11.0 -> 3.1.6 in requirements/base.txt; resolves 6 advisories.", 95, 11),
    (102, "[security] Remove hardcoded default SECRET_KEY from config", "manual",
     "high", "completed", "/pull/4102", 2.1,
     "SECRET_KEY now loads from env; production config fails fast if unset.", 70, 8),
    (103, "[security] sqlparse 0.4.1 is vulnerable (GHSA-p5w8-wqhj-9hhf)", "scanner",
     "medium", "completed", "/pull/4103", 2.8,
     "Upgraded sqlparse to 0.5.0; tests for sql_parse pass.", 55, 9),
    (104, "[security] Replace insecure MD5 hashing for security-sensitive values", "manual",
     "medium", "working", None, 1.6, None, 6, None),
    (105, "[security] urllib3 1.26.4 is vulnerable (GHSA-q2q7-5pp4-w6pg)", "scanner",
     "high", "working", None, 0.7, None, 3, None),
    (106, "[security] cryptography 3.3 vulnerable (GHSA-x4qr-2fvf-3mr5)", "scanner",
     "critical", "blocked", None, 1.9,
     "Major bump needs a maintainer decision on minimum Python.", 40, None),
    (107, "[security] Pillow 8.1.0 is vulnerable (GHSA-j7hp-h8jx-5ppr)", "scanner",
     "high", "failed", None, 0.9, None, 30, 4),
]


def main() -> None:
    store = Store(settings.database_path)
    now = time.time()
    for (num, title, source, sev, status, pr, acu, summary, age, dur) in SAMPLE:
        rid = store.create_remediation(
            issue_number=num, issue_title=title, repo=REPO,
            source=source, severity=sev,
        )
        if rid is None:
            continue
        created = now - age * 60
        fields = {
            "devin_session_id": f"devin-{num}",
            "devin_session_url": f"https://app.devin.ai/sessions/{num}",
            "status": status,
            "acu_used": acu,
            "created_at": created,
        }
        if summary:
            fields["summary"] = summary
        if pr:
            fields["pr_url"] = f"https://github.com/{REPO}{pr}"
        if dur is not None:
            fields["completed_at"] = created + dur * 60
        if status == "failed":
            fields["error"] = "session expired before opening a PR"
        store.update(rid, **fields)
    m = store.metrics()
    print(f"Seeded {m['total']} sample remediations into {settings.database_path}")
    print(f"  completed={m['completed']} active={m['active']} "
          f"success_rate={m['success_rate']}% eng_hours_saved={m['eng_hours_saved']}")


if __name__ == "__main__":
    main()
