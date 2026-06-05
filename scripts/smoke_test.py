"""Live smoke test against the real Devin + GitHub APIs.

Verifies credentials and the end-to-end primitive without the full server:
  1. GitHub: confirm we can read the fork.
  2. Devin: create a tiny throwaway session and poll it once.

    .venv/bin/python -m scripts.smoke_test
"""
from __future__ import annotations

import asyncio

from app.config import settings
from app.devin_client import DevinClient
from app.github_client import GitHubClient


async def main() -> None:
    print(f"repo            : {settings.github_repo}")
    print(f"devin base      : {settings.devin_api_base}")
    print(f"configured      : {settings.configured}\n")
    if not settings.configured:
        raise SystemExit("Fill in DEVIN_API_KEY, GITHUB_TOKEN, GITHUB_REPO in .env.")

    gh = GitHubClient(settings.github_token, settings.github_repo)
    devin = DevinClient(settings.devin_api_key, settings.devin_api_base)
    try:
        issues = await gh.list_issues()
        print(f"[github] OK — {len(issues)} open issues visible in {settings.github_repo}")

        session = await devin.create_session(
            "Smoke test from the Superset auto-remediation pipeline. "
            "Reply with the string 'pong' and finish.",
            title="pipeline smoke test",
            tags=["smoke-test"],
            max_acu_limit=1,
        )
        print(f"[devin]  OK — session {session['session_id']}")
        print(f"         {session.get('url')}")
        detail = await devin.get_session(session["session_id"])
        print(f"[devin]  status = {devin.normalize(detail)['raw_status']}")
        print("\nSmoke test passed. Credentials and both APIs are reachable.")
    finally:
        await gh.aclose()
        await devin.aclose()


if __name__ == "__main__":
    asyncio.run(main())
