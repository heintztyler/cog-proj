"""Prompt + structured-output schema used to drive a Devin remediation session.

This is where Devin stops being "a chatbot" and becomes a managed worker: we
hand it a precise objective, the repo, guardrails, and a machine-readable output
contract so the orchestrator can act on the result without scraping prose.
"""
from __future__ import annotations

# Devin validates its final structured_output against this schema, so the
# orchestrator gets a reliable PR url + summary instead of free text.
STRUCTURED_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "pr_url": {"type": "string", "description": "URL of the pull request opened"},
        "summary": {"type": "string", "description": "1-2 sentence summary of the fix"},
        "files_changed": {"type": "array", "items": {"type": "string"}},
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
            "description": "Devin's confidence the change is correct and complete",
        },
        "tests_run": {"type": "boolean", "description": "Whether the test suite was exercised"},
    },
    "required": ["pr_url", "summary", "confidence"],
}


def build_prompt(*, repo: str, issue_number: int, issue_title: str, issue_body: str) -> str:
    return f"""\
You are remediating a tracked engineering issue in the repository `{repo}`
(a fork of apache/superset). Work autonomously and open a pull request.

## Issue #{issue_number}: {issue_title}

{issue_body}

## Your task
1. Clone `{repo}`, create a new branch named `devin/issue-{issue_number}`.
2. Implement the smallest correct change that fully resolves the issue above.
3. Follow the repository's existing conventions, linting, and style.
4. If the change touches Python, run the relevant unit tests / linters you can
   reasonably run for the affected module and make sure they pass.
5. Open a pull request against the default branch of `{repo}`. The PR
   description MUST start with "Fixes #{issue_number}" so the issue auto-links,
   and explain what changed and how you verified it.
6. Keep the change tightly scoped — do not refactor unrelated code.

## Guardrails
- Do not modify CI/CD secrets, delete tests, or disable security checks.
- If the issue is ambiguous or you cannot safely fix it, open a PR in draft and
  explain what's blocking in the summary rather than guessing destructively.

When finished, populate the structured output with the PR url, a short summary,
the files you changed, and your confidence level.
"""
