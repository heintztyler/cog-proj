"""FastAPI application: event ingress + orchestration + observability.

The pipeline's job is narrow and autonomous: continuously detect security
vulnerabilities in the fork's dependencies and drive each one to a fix via
Devin. The scheduled scanner is the primary event source; the manual endpoints
exist for demos and backfill.

Endpoints
  POST /scan              Trigger a vulnerability scan cycle now
  POST /remediate/{n}     Manually dispatch Devin at issue #n (demo / backfill)
  GET  /                  Live observability dashboard (HTML)
  GET  /api/metrics       Aggregate KPIs (JSON)
  GET  /api/sessions      Every tracked remediation (JSON)
  GET  /healthz           Liveness
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Template

from .config import settings
from .devin_client import DevinClient
from .github_client import GitHubClient
from .orchestrator import Orchestrator
from .scanner import Scanner
from .store import Store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("app")

# Shared singletons, wired in lifespan.
state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = Store(settings.database_path)
    devin = DevinClient(settings.devin_api_key, settings.devin_api_base)
    github = GitHubClient(settings.github_token, settings.github_repo)
    orchestrator = Orchestrator(store, devin, github)
    scanner = Scanner(github, orchestrator)

    state.update(
        store=store, devin=devin, github=github,
        orchestrator=orchestrator, scanner=scanner,
    )

    scheduler = AsyncIOScheduler()
    # Poller: advance in-flight Devin sessions on a fixed cadence.
    scheduler.add_job(
        orchestrator.poll_once, "interval",
        seconds=settings.poll_interval_seconds, id="poller", max_instances=1,
    )
    # Scanner: periodic scan-results event source.
    if settings.scanner_interval_seconds > 0:
        scheduler.add_job(
            scanner.run, "interval",
            seconds=settings.scanner_interval_seconds, id="scanner", max_instances=1,
        )
    scheduler.start()
    state["scheduler"] = scheduler
    log.info(
        "pipeline up | repo=%s | configured=%s | poll=%ss | scan=%ss",
        settings.github_repo, settings.configured,
        settings.poll_interval_seconds, settings.scanner_interval_seconds,
    )
    if not settings.configured:
        log.warning("Credentials look like placeholders — set DEVIN_API_KEY, "
                    "GITHUB_TOKEN and GITHUB_REPO in .env before going live.")
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        await devin.aclose()
        await github.aclose()


app = FastAPI(title="Superset Auto-Remediation Pipeline", lifespan=lifespan)


# ── Event source: scheduled scan (also manually triggerable) ───────────────
@app.post("/scan")
async def scan_now():
    result = await state["scanner"].run()
    return JSONResponse(result)


# ── Manual dispatch (demo / backfill an existing issue) ────────────────────
@app.post("/remediate/{issue_number}")
async def remediate(issue_number: int):
    github: GitHubClient = state["github"]
    issues = await github.list_issues(state="open")
    match = next((i for i in issues if i["number"] == issue_number), None)
    if not match:
        raise HTTPException(404, f"open issue #{issue_number} not found in {github.repo}")
    rid = await state["orchestrator"].remediate_issue(
        issue_number=match["number"],
        issue_title=match["title"],
        issue_body=match.get("body") or "",
        source="manual",
    )
    if rid is None:
        return {"status": "already_in_progress", "issue": issue_number}
    return {"remediation_id": rid, "issue": issue_number}


# ── Observability ──────────────────────────────────────────────────────────
@app.get("/api/metrics")
async def api_metrics():
    return state["store"].metrics()


@app.get("/api/sessions")
async def api_sessions():
    return state["store"].list_all()


@app.get("/healthz")
async def healthz():
    return {"ok": True, "configured": settings.configured}


_TEMPLATE = Template(
    (Path(__file__).parent / "templates" / "dashboard.html").read_text()
)


def _humanize(seconds: float) -> str:
    """A compact 'ago'/'in' magnitude like 5s, 12m, 1h 3m, 2d 4h."""
    s = int(abs(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        h, m = s // 3600, (s % 3600) // 60
        return f"{h}h {m}m" if m else f"{h}h"
    d, h = s // 86400, (s % 86400) // 3600
    return f"{d}d {h}h" if h else f"{d}d"


def _scan_status() -> dict:
    """When the scanner last ran and when it runs next, for the dashboard."""
    now = time.time()
    scanner = state["scanner"]
    enabled = settings.scanner_interval_seconds > 0

    last = scanner.last_run_at
    last_str = f"{_humanize(now - last)} ago" if last else None
    last_result = scanner.last_result

    next_str = None
    if enabled:
        job = state["scheduler"].get_job("scanner")
        if job and job.next_run_time:
            next_str = f"in {_humanize(job.next_run_time.timestamp() - now)}"

    return {
        "enabled": enabled,
        "last": last_str,
        "next": next_str,
        "interval": _humanize(settings.scanner_interval_seconds) if enabled else None,
        "findings": last_result.get("findings") if last_result else None,
        "filed": last_result.get("issues_filed") if last_result else None,
    }


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    store: Store = state["store"]
    return _TEMPLATE.render(
        metrics=store.metrics(),
        sessions=store.list_all(limit=100),
        repo=settings.github_repo,
        configured=settings.configured,
        scan=_scan_status(),
    )
