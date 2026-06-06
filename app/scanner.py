"""Scheduled vulnerability scanner — the pipeline's primary event source.

Reads dependency manifests straight from the fork via the GitHub API, queries
the free OSV.dev advisory database for known CVEs, and files a GitHub issue for
each newly-discovered vulnerable package, then hands it to the orchestrator for
autonomous remediation.

No clone, no local toolchain required — it works against any fork on GitHub.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

import httpx

from .config import settings
from .github_client import GitHubClient
from .orchestrator import Orchestrator

log = logging.getLogger("scanner")

OSV_URL = "https://api.osv.dev/v1/query"
# pkg==1.2.3  /  pkg>=1.2.3  — capture name + first pinned version.
_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*==\s*([0-9][0-9A-Za-z.\-]*)")
_SEVERITY_ORDER = {"CRITICAL": 3, "HIGH": 2, "MODERATE": 1, "LOW": 0}


@dataclass
class Finding:
    package: str
    version: str
    vuln_ids: list[str]
    summary: str
    severity: str
    fixed_versions: list[str]
    source_file: str

    @property
    def fingerprint(self) -> str:
        return f"{self.package}@{self.version}:{','.join(sorted(self.vuln_ids))}"

    @property
    def issue_title(self) -> str:
        ids = ", ".join(self.vuln_ids[:3])
        return f"[security] {self.package} {self.version} is vulnerable ({ids})"

    def issue_body(self) -> str:
        fix = (
            f"Upgrade `{self.package}` to **{self.fixed_versions[0]}** or later."
            if self.fixed_versions
            else f"Upgrade `{self.package}` to a non-vulnerable release."
        )
        advisories = "\n".join(
            f"- https://osv.dev/vulnerability/{vid}" for vid in self.vuln_ids
        )
        return f"""\
### Automated dependency vulnerability finding

The scanner detected a known-vulnerable dependency in `{self.source_file}`.

| Field | Value |
|------|-------|
| Package | `{self.package}` |
| Installed | `{self.version}` |
| Severity | **{self.severity}** |
| Advisories | {", ".join(self.vuln_ids)} |

**Summary:** {self.summary}

**Recommended remediation:** {fix} Update the pin in `{self.source_file}`
(and any constraints files), then make sure nothing else breaks.

#### References
{advisories}

_Filed automatically by the Superset Auto-Remediation Pipeline._
"""


async def _query_osv(name: str, version: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            OSV_URL, json={"version": version, "package": {"name": name, "ecosystem": "PyPI"}}
        )
        resp.raise_for_status()
        return resp.json().get("vulns", []) or []


def _summarize(vuln: dict) -> tuple[str, list[str]]:
    """Extract a one-line summary + fixed versions from an OSV record."""
    summary = vuln.get("summary") or vuln.get("details", "")[:160] or "Known vulnerability"
    fixed: list[str] = []
    for affected in vuln.get("affected", []):
        for rng in affected.get("ranges", []):
            for ev in rng.get("events", []):
                if ev.get("fixed"):
                    fixed.append(ev["fixed"])
    return summary, fixed


def _severity(vulns: list[dict]) -> str:
    best = "LOW"
    for v in vulns:
        for sev in v.get("severity", []) or []:
            # OSV gives CVSS vectors; fall back to DB-specific labels.
            label = (v.get("database_specific", {}) or {}).get("severity", "").upper()
            if _SEVERITY_ORDER.get(label, -1) > _SEVERITY_ORDER.get(best, -1):
                best = label
    return best.capitalize()


def parse_requirements(text: str) -> list[tuple[str, str]]:
    pins: list[tuple[str, str]] = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = _REQ_LINE.match(line)
        if m:
            pins.append((m.group(1), m.group(2)))
    return pins


class Scanner:
    def __init__(self, github: GitHubClient, orchestrator: Orchestrator):
        self.github = github
        self.orchestrator = orchestrator
        self._seen: set[str] = set()  # fingerprints filed this process
        self.last_run_at: float | None = None   # epoch of the last completed scan
        self.last_result: dict | None = None     # summary of the last scan

    async def scan(self) -> list[Finding]:
        """Inspect configured requirements files and return findings."""
        findings: list[Finding] = []
        for path in settings.requirements_paths:
            text = await self.github.get_file(path)
            if not text:
                log.warning("requirements file not found in fork: %s", path)
                continue
            for name, version in parse_requirements(text):
                try:
                    vulns = await _query_osv(name, version)
                except Exception:  # noqa: BLE001
                    log.exception("OSV query failed for %s==%s", name, version)
                    continue
                if not vulns:
                    continue
                summary, fixed = _summarize(vulns[0])
                findings.append(
                    Finding(
                        package=name,
                        version=version,
                        vuln_ids=[v["id"] for v in vulns],
                        summary=summary,
                        severity=_severity(vulns),
                        fixed_versions=fixed,
                        source_file=path,
                    )
                )
        return findings

    async def run(self) -> dict:
        """Full cycle: scan -> file issues -> (optionally) dispatch to Devin."""
        findings = await self.scan()
        filed = 0
        dispatched = 0
        for f in findings:
            if f.fingerprint in self._seen:
                continue
            self._seen.add(f.fingerprint)
            try:
                issue = await self.github.create_issue(
                    f.issue_title, f.issue_body(),
                    labels=["security", "dependencies"],
                )
            except Exception:  # noqa: BLE001
                log.exception("failed to file issue for %s", f.fingerprint)
                continue
            filed += 1
            # Human-in-the-loop vs. fully autonomous, controlled by config.
            if not settings.scanner_requires_approval:
                started = await self.orchestrator.remediate_issue(
                    issue_number=issue["number"],
                    issue_title=issue["title"],
                    issue_body=issue["body"] or "",
                    source="scanner",
                    severity=f.severity.lower(),
                )
                if started:
                    dispatched += 1

        result = {"findings": len(findings), "issues_filed": filed, "dispatched": dispatched}
        self.last_run_at = time.time()
        self.last_result = result
        log.info("scan complete: %s", result)
        return result
