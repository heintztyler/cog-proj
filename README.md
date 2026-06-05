# Superset Security Auto-Remediation Pipeline

**An event-driven system that autonomously detects security vulnerabilities in a codebase and turns each one into a reviewed pull request — using [Devin](https://docs.devin.ai/) as the core worker.**

Built against [`apache/superset`](https://github.com/apache/superset). A scheduled scanner watches the fork's dependencies for known CVEs; the moment one appears, the pipeline spins up a managed Devin session, drives it to a pull request that patches the vulnerability, reports the result back onto the issue, and tracks every step on a live dashboard.

---

## The problem this solves

Every large repo like Superset is quietly accumulating **security debt**: dependency CVEs land upstream daily, weak crypto and hardcoded defaults linger for years, and each finding sits in a scanner report waiting for a human. The danger isn't *knowing* about the vulnerability — scanners already tell us. It's the **mean-time-to-remediate**: the human in the middle who has to read the advisory, branch, write the upgrade, run tests, and open a PR. That gap between *detected* and *fixed* is exactly the window an attacker exploits.

This pipeline closes that window by making **"vulnerability detected → fix → PR" a fully automated event flow**, with Devin doing the engineering and humans staying on the *review* side of the loop. Security teams stop triaging and start approving.

---

## Architecture

```
   EVENT SOURCES                  ORCHESTRATION                     OUTPUTS
 ┌───────────────────┐        ┌──────────────────────┐        ┌──────────────────┐
 │ Scheduled scanner │        │  Orchestrator        │        │ GitHub PR        │
 │ (OSV.dev CVE scan │──────► │  • build task prompt │──────► │ "Fixes CVE-…"    │
 │  → files issues)  │        │  • POST /v1/sessions │        │                  │
 ├───────────────────┤        │  • track in SQLite   │        ├──────────────────┤
 │ Manual dispatch   │        │                      │        │ Issue comment    │
 │ POST /remediate/n │──────► │  Poller (every 30s)  │        │ (PR + session)   │
 │ (demo / backfill) │        │  • GET /v1/sessions  │        ├──────────────────┤
 └───────────────────┘        │  • detect PR/failure │        │ Live dashboard   │
            │                 │  • comment back      │──────► │ + /api/metrics   │
            │                 └──────────────────────┘        │ (success, ACU…)  │
            │                          ▲   │                          ▲
            └── converge on ───────────┘   └──── Devin v1 REST API ───┘
               remediate_issue()                 (api.devin.ai)
```

**Devin is the primitive, not a helper.** The orchestrator never writes code. It translates a vulnerability into a precise objective + guardrails + a machine-readable output contract (`structured_output_schema`), hands that to Devin, and reacts to the result. Add another detector (Snyk, Trivy, CodeQL) and the same machinery remediates its findings too.

### Components (`app/`)
| File | Responsibility |
|------|----------------|
| `main.py` | FastAPI app: scan/dispatch endpoints, REST API, dashboard, APScheduler wiring |
| `orchestrator.py` | `issue → Devin session → poll → PR comment`; the control loop |
| `devin_client.py` | Async wrapper over Devin v1 (`create` / `get` / `message`) |
| `github_client.py` | Issues: create, label, comment, list; raw file fetch |
| `scanner.py` | Reads `requirements/*.txt` from the fork, queries **OSV.dev**, files CVE issues |
| `prompts.py` | The remediation prompt + structured-output JSON schema |
| `store.py` | SQLite system-of-record + KPI aggregation |
| `templates/dashboard.html` | Auto-refreshing observability dashboard |

---

## Quickstart

> Requires Python 3.12+. Everything installs into a local **virtualenv** (`.venv`).

### 1. Setup
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # then edit .env (see below)
```

### 2. Preview the dashboard immediately (no credentials needed)
```bash
.venv/bin/python -m scripts.demo_data        # inject sample remediations
.venv/bin/uvicorn app.main:app --reload
# open http://localhost:8000
```
This shows the observability layer populated with representative data so you can
see exactly what a security lead would watch. Delete `./data/pipeline.db`
to clear it.

### 3. Go live against the real APIs
Fill in `.env`:
```ini
DEVIN_API_KEY=apk_...            # Devin personal or service key
GITHUB_TOKEN=ghp_...             # PAT with `repo` scope on your fork
GITHUB_REPO=<your-org>/superset  # your fork of apache/superset
```
Verify connectivity, then seed the issues and run:
```bash
.venv/bin/python -m scripts.smoke_test       # checks both APIs are reachable
.venv/bin/python -m scripts.seed_issues      # files the curated security issues (Part 1)
.venv/bin/uvicorn app.main:app
```

### 4. Trigger a remediation
- **Scan event (the real path):** `curl -X POST localhost:8000/scan` — finds CVEs → files issues → dispatches Devin. This is also what the scheduler runs automatically every `SCANNER_INTERVAL_SECONDS`.
- **Manual / demo:** `curl -X POST localhost:8000/remediate/<issue_number>` to dispatch Devin at a specific seeded issue.

---

## Run with Docker

```bash
cp .env.example .env   # fill in credentials
docker compose up --build
# dashboard at http://localhost:8000 ; SQLite persists in the `pipeline-data` volume
```

---

## Forking Superset & the seeded issues (Part 1)

1. Fork `https://github.com/apache/superset` into your org (or `gh repo fork apache/superset`).
2. Set `GITHUB_REPO=<your-org>/superset` in `.env`.
3. `.venv/bin/python -m scripts.seed_issues` creates these curated, remediable **security** issues:
   - **[security]** upgrade a known-vulnerable dependency (names the CVE/GHSA)
   - **[security]** replace insecure MD5/SHA1 hashing for security-sensitive values
   - **[security]** remove a hardcoded default `SECRET_KEY` / credential from config

The **scanner also discovers real CVEs on its own** — pointed at Superset's
`requirements/base.txt` it queries OSV.dev live and files issues for anything
vulnerable (verified working: e.g. it flags `jinja2 2.11.0` → 6 advisories, fix `3.1.6`).

---

## Observability — "how would I know this is working?"

Three layers, aimed at three audiences:

1. **Live dashboard** (`/`) — for a VP/security lead at a glance: vulnerabilities
   detected, active sessions, PRs opened, **success rate**, average detect→PR
   cycle time (your real MTTR), ACU spend, and an estimate of **engineering-hours
   saved**. Auto-refreshes every 10s.
2. **JSON APIs** — `/api/metrics` (KPIs) and `/api/sessions` (every tracked
   remediation) for piping into Datadog/Grafana or a weekly security report.
3. **In-context signals** — every issue gets a comment when Devin starts (with a
   live session link) and when it finishes (with the PR), plus structured logs.

Every Devin session the pipeline starts is a row in SQLite with status, timing,
ACU cost, and outcome — so success/failure, throughput, and cost are all answerable.

---

## Why Devin is uniquely suited here

A scanner can *find* a vulnerable pin; a Dependabot-style bot can *bump a version*
when the upgrade is trivial. Neither handles the **non-trivial** security fixes —
a major-version bump that breaks an import, swapping a weak hash without changing
a persisted format, removing a hardcoded secret and making the config fail fast —
which require reading code, making judgment calls, running tests, and iterating.
That's an autonomous engineer's job. Devin's API lets us treat that engineer as a
**programmable primitive**: start one per vulnerability, cap its budget
(`max_acu_limit`), constrain it with guardrails, and get a typed result back — at
a concurrency and speed no on-call human matches when CVEs land in bulk.

---

## Extending this in a real engagement (next steps)

- **More detectors:** Snyk/Trivy/CodeQL findings, GitHub security advisories,
  Dependabot alerts, secret-scanning hits — each just calls `remediate_issue()`.
- **Quality gates before review:** require Devin's `confidence: high` + green CI,
  auto-request the security team as reviewers, auto-merge low-risk patch bumps
  behind a policy.
- **Playbooks & knowledge:** attach Devin playbooks/`knowledge_ids` so fixes follow
  the team's exact conventions (commit style, test commands, ownership).
- **Concurrency & cost controls:** per-repo budgets, rate limits, and an approval
  queue for `critical` severity (already toggleable via `SCANNER_REQUIRES_APPROVAL`).
- **Enterprise reporting:** swap SQLite for Postgres and use Devin's enterprise
  insights endpoints for org-wide MTTR and ROI dashboards.

---

## Tests
```bash
.venv/bin/python -m pytest tests/ -q      # pure-logic unit tests, no network
```

## Project layout
```
app/            FastAPI service (event ingress, orchestration, dashboard)
scripts/        seed_issues · smoke_test · demo_data
Dockerfile      container image
docker-compose.yml
.env.example    configuration template
```
