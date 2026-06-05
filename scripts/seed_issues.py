"""Seed a curated set of remediable security issues into your Superset fork.

These guarantee the pipeline has concrete security work to remediate for the
demo (Part 1 of the challenge), alongside whatever the OSV scanner discovers on
its own. Run once after forking apache/superset:

    .venv/bin/python -m scripts.seed_issues

Requires GITHUB_TOKEN + GITHUB_REPO in .env. The pipeline remediates these
autonomously; you can also dispatch any one immediately with
`POST /remediate/<issue_number>`.
"""
from __future__ import annotations

import asyncio

from app.config import settings
from app.github_client import GitHubClient

# Curated, concrete, independently-verifiable security remediations for
# apache/superset. Every issue is scoped, security-flavored, and has crisp
# acceptance criteria so Devin's PR can be objectively reviewed.
ISSUES = [
    {
        "title": "[security] Audit and upgrade a known-vulnerable Python dependency",
        "labels": ["security", "dependencies"],
        "body": """\
### Problem
A pinned dependency in `requirements/` has a known advisory and should be
upgraded to a patched release.

### Scope
Identify one outdated/vulnerable pin in the `requirements/*.txt` files, bump it
to the lowest non-vulnerable version that remains compatible, and update any
constraints files accordingly.

### Acceptance criteria
- The pin is updated to a patched version.
- The change is minimal and the dependency graph still resolves.
- PR description names the advisory (CVE/GHSA) being addressed.

> Note: the pipeline's scanner also files these automatically from OSV.dev;
> this seed issue guarantees one exists for the demo.""",
    },
    {
        "title": "[security] Replace insecure MD5/SHA1 hashing for security-sensitive values",
        "labels": ["security"],
        "body": """\
### Problem
Some code paths use weak hash functions (`hashlib.md5` / `hashlib.sha1`) for
values that have security relevance. These algorithms are collision-prone and
should not be relied on for integrity or token derivation.

### Scope
Find security-sensitive uses of `md5`/`sha1` in the `superset/` package and move
them to a strong algorithm (e.g. SHA-256). Where a hash is used purely as a
non-security cache key, leave it but mark it with `usedforsecurity=False` to make
the intent explicit. Do not change any persisted/wire formats without a note.

### Acceptance criteria
- No weak hash is used for a security-sensitive value in the touched files.
- Non-security uses are explicitly annotated `usedforsecurity=False`.
- Behavior is preserved; relevant tests still pass.""",
    },
    {
        "title": "[security] Remove hardcoded default SECRET_KEY / credentials from config",
        "labels": ["security"],
        "body": """\
### Problem
Configuration ships with a predictable default `SECRET_KEY` (and/or default
credentials). A deployment that forgets to override it is trivially exploitable
(session forgery), and the default has been published for years.

### Scope
Make the security-sensitive secret(s) load from the environment with no usable
hardcoded fallback. If a value is missing in a production config, fail fast with
a clear error rather than booting with a known-insecure default. Update the
example config and docs to show the env-based pattern.

### Acceptance criteria
- No usable hardcoded secret/credential remains in the touched config.
- Missing required secrets raise a clear, early error.
- Docs/example config show how to supply the value via environment.""",
    },
]

LABELS = [
    ("security", "d73a4a", "Security issue"),
    ("dependencies", "0366d6", "Dependency management"),
]


async def main() -> None:
    if not settings.configured:
        raise SystemExit(
            "Set GITHUB_TOKEN and GITHUB_REPO (a real fork) in .env first."
        )
    gh = GitHubClient(settings.github_token, settings.github_repo)
    try:
        for name, color, desc in LABELS:
            await gh.ensure_label(name, color, desc)
        for spec in ISSUES:
            issue = await gh.create_issue(spec["title"], spec["body"], spec["labels"])
            print(f"  created #{issue['number']}: {spec['title']}")
        print(f"\nDone. Seeded {len(ISSUES)} issues into {settings.github_repo}.")
    finally:
        await gh.aclose()


if __name__ == "__main__":
    asyncio.run(main())
