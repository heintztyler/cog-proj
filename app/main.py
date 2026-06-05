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


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    store: Store = state["store"]
    return _TEMPLATE.render(
        metrics=store.metrics(),
        sessions=store.list_all(limit=100),
        repo=settings.github_repo,
        configured=settings.configured,
    )
