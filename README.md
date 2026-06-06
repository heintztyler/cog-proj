# Security Auto-Remediation Pipeline

Autonomously detects security vulnerabilities in a GitHub repo and uses [Devin](https://devin.ai) to open pull requests that fix them.

Vulnerabilities are detected with [OSV.dev](https://osv.dev), Google's open-source vulnerability database. The pipeline reads your repo's pinned Python dependencies (`requirements/*.txt`) and queries OSV for known advisories (CVE/GHSA) against each one, then creates Github issues for each at-risk dependency. Devin then processes these issues and creates PR to resolve them.

The dashboard at `http://localhost:8000` shows what Devin is working on live, along with performance and system health metrics.

## What you need

- **Git** and **Python 3.12+** — or just **Docker**.
- A **Devin API key** and a **GitHub token** (a PAT with `repo` access to your fork).

## 1. Download

```bash
git clone https://github.com/heintztyler/cog-proj.git
cd cog-proj
```

## 2. Configure

```bash
cp .env.example .env
```

Open `.env` and fill in three values:

```ini
DEVIN_API_KEY=...                 # your Devin API key
GITHUB_TOKEN=...                  # GitHub PAT with repo access
GITHUB_REPO=your-username/your-repo
```

## 3. Run

**With Python:**

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app
```

**Or with Docker (simplest):**

```bash
docker compose up --build
```

Then open **http://localhost:8000**.

To kick off a scan immediately (instead of waiting for the 1x hour schedule):

```bash
curl -X POST http://localhost:8000/scan
```
