"""The brain of the pipeline.

Turns an *issue* into a *managed Devin session*, then tracks that session to a
terminal state and reports the outcome back onto the GitHub issue. A background
poller advances every in-flight session on a fixed cadence.

    issue ──> create Devin session ──> poll ──> comment PR back ──> metrics
"""
from __future__ import annotations

import logging

from .config import settings
from .devin_client import DevinClient
from .github_client import GitHubClient
from .prompts import STRUCTURED_OUTPUT_SCHEMA, build_prompt
from .store import Store

log = logging.getLogger("orchestrator")


class Orchestrator:
    def __init__(self, store: Store, devin: DevinClient, github: GitHubClient):
        self.store = store
        self.devin = devin
        self.github = github

    async def remediate_issue(
        self,
        *,
        issue_number: int,
        issue_title: str,
        issue_body: str,
        source: str,
        severity: str | None = None,
    ) -> int | None:
        """Kick off a Devin session for one issue. Idempotent per (repo, issue)."""
        repo = self.github.repo
        rid = self.store.create_remediation(
            issue_number=issue_number,
            issue_title=issue_title,
            repo=repo,
            source=source,
            severity=severity,
        )
        if rid is None:
            log.info("issue #%s already has a remediation; skipping", issue_number)
            return None

        prompt = build_prompt(
            repo=repo,
            issue_number=issue_number,
            issue_title=issue_title,
            issue_body=issue_body or "(no description provided)",
        )
        try:
            session = await self.devin.create_session(
                prompt,
                title=f"Fix #{issue_number}: {issue_title[:60]}",
                tags=["superset-auto-remediation", f"issue-{issue_number}", source],
                max_acu_limit=settings.max_acu_limit,
                structured_output_schema=STRUCTURED_OUTPUT_SCHEMA,
            )
        except Exception as e:  # noqa: BLE001 - surface any API failure on the row
            log.exception("failed to start Devin session for #%s", issue_number)
            self.store.update(rid, status="failed", error=f"create_session: {e}")
            return rid

        self.store.update(
            rid,
            devin_session_id=session["session_id"],
            devin_session_url=session.get("url"),
            status="working",
        )
        # Acknowledge on the issue so humans can watch Devin live.
        await self._safe_comment(
            issue_number,
            f"🤖 **Devin is on it.** Started an autonomous remediation session.\n\n"
            f"Track it live: {session.get('url')}",
        )
        log.info("remediation #%s -> session %s", rid, session["session_id"])
        return rid

    async def poll_once(self) -> None:
        """Advance every in-flight remediation by one polling step."""
        for row in self.store.in_flight():
            try:
                await self._poll_row(row)
            except Exception:  # noqa: BLE001 - one bad row must not stall the loop
                log.exception("poll failed for remediation #%s", row["id"])

    async def _poll_row(self, row: dict) -> None:
        session = await self.devin.get_session(row["devin_session_id"])
        norm = self.devin.normalize(session)
        rid = row["id"]

        if norm["status"] == "completed":
            self.store.update(
                rid,
                status="completed",
                pr_url=norm["pr_url"],
                summary=norm["summary"],
                acu_used=norm["acu_used"],
            )
            await self._report_success(row, norm)
        elif norm["status"] in ("expired", "failed"):
            self.store.update(
                rid, status="failed", error=f"session {norm['raw_status']}",
                acu_used=norm["acu_used"],
            )
            await self._safe_comment(
                row["issue_number"],
                f"⚠️ Devin session ended ({norm['raw_status']}) without a merged fix. "
                f"Needs a human look: {row['devin_session_url']}",
            )
        elif norm["status"] == "blocked":
            # Devin is waiting on input — flag for a human, keep the row alive.
            self.store.update(rid, status="blocked", acu_used=norm["acu_used"])
            log.warning("remediation #%s blocked — needs human input", rid)
        else:
            # still working; just refresh ACU usage so the dashboard ticks.
            if norm["acu_used"] is not None:
                self.store.update(rid, acu_used=norm["acu_used"])

    async def _report_success(self, row: dict, norm: dict) -> None:
        pr = norm["pr_url"]
        if pr:
            body = (
                f"✅ **Devin opened a pull request:** {pr}\n\n"
                f"> {norm.get('summary') or 'Remediation complete.'}\n\n"
                f"Confidence: `{norm.get('confidence') or 'n/a'}` · "
                f"Session: {row['devin_session_url']}"
            )
        else:
            body = (
                f"✅ Devin reports the remediation is complete, but no PR URL was "
                f"returned. Review the session: {row['devin_session_url']}"
            )
        await self._safe_comment(row["issue_number"], body)

    async def _safe_comment(self, issue_number: int, body: str) -> None:
        try:
            await self.github.comment(issue_number, body)
        except Exception:  # noqa: BLE001 - commenting is best-effort telemetry
            log.exception("failed to comment on issue #%s", issue_number)
