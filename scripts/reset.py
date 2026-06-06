"""Reset the demo to a clean slate.

Closes (or deletes) every open issue in the fork and wipes the local
remediation DB, so you can run a fresh scan-to-PR cycle.

    .venv/bin/python -m scripts.reset                # close issues + wipe DB (asks first)
    .venv/bin/python -m scripts.reset --yes          # skip the confirmation prompt
    .venv/bin/python -m scripts.reset --delete       # permanently DELETE issues (irreversible)
    .venv/bin/python -m scripts.reset --db-only      # only wipe the local DB
    .venv/bin/python -m scripts.reset --issues-only  # only touch GitHub issues

Notes:
- "Close" is the default and is reversible. "--delete" is permanent and needs a
  token with admin/maintain rights on the repo.
- If the server is running, restart it after a reset so the scanner re-files
  issues it already saw this session.
"""
from __future__ import annotations

import argparse
import asyncio

from app.config import settings
from app.github_client import GitHubClient
from app.store import Store


async def clear_issues(delete: bool) -> int:
    gh = GitHubClient(settings.github_token, settings.github_repo)
    count = 0
    try:
        issues = await gh.list_issues(state="open")
        for issue in issues:
            num = issue["number"]
            if delete:
                await gh.delete_issue(issue["node_id"])
                print(f"  deleted #{num}: {issue['title']}")
            else:
                await gh.close_issue(num)
                print(f"  closed  #{num}: {issue['title']}")
            count += 1
    finally:
        await gh.aclose()
    return count


def clear_db() -> int:
    removed = Store(settings.database_path).clear()
    print(f"  removed {removed} remediation row(s) from {settings.database_path}")
    return removed


async def main() -> None:
    ap = argparse.ArgumentParser(description="Reset the pipeline demo state.")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    ap.add_argument("--delete", action="store_true",
                    help="permanently DELETE issues via GraphQL (default: close them)")
    ap.add_argument("--db-only", action="store_true", help="only wipe the local DB")
    ap.add_argument("--issues-only", action="store_true", help="only touch GitHub issues")
    args = ap.parse_args()

    if args.db_only and args.issues_only:
        raise SystemExit("--db-only and --issues-only are mutually exclusive.")

    do_issues = not args.db_only
    do_db = not args.issues_only

    plan = []
    if do_issues:
        verb = "DELETE" if args.delete else "close"
        plan.append(f"{verb} all open issues in {settings.github_repo}")
    if do_db:
        plan.append(f"wipe the local DB ({settings.database_path})")
    print("This will: " + "; ".join(plan) + ".")

    if do_issues and not settings.configured:
        raise SystemExit("Set GITHUB_TOKEN and GITHUB_REPO in .env first.")

    if not args.yes:
        if input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return

    if do_issues:
        print("Clearing GitHub issues…")
        n = await clear_issues(args.delete)
        print(f"{'Deleted' if args.delete else 'Closed'} {n} issue(s).")
    if do_db:
        print("Clearing local DB…")
        clear_db()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
