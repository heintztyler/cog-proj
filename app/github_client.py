"""Minimal async GitHub REST client (issues + raw file fetch).

Only the handful of calls the pipeline needs: list/create/label/comment issues
and read a file's contents (for the dependency scanner).
"""
from __future__ import annotations

import base64
import logging
from typing import Optional

import httpx

log = logging.getLogger("github")


class GitHubClient:
    def __init__(self, token: str, repo: str):
        self.repo = repo  # "owner/name"
        self._client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def create_issue(
        self, title: str, body: str, labels: Optional[list[str]] = None
    ) -> dict:
        resp = await self._client.post(
            f"/repos/{self.repo}/issues",
            json={"title": title, "body": body, "labels": labels or []},
        )
        resp.raise_for_status()
        issue = resp.json()
        log.info("created issue #%s: %s", issue["number"], title)
        return issue

    async def list_issues(self, labels: Optional[str] = None, state: str = "open") -> list[dict]:
        params = {"state": state, "per_page": 100}
        if labels:
            params["labels"] = labels
        resp = await self._client.get(f"/repos/{self.repo}/issues", params=params)
        resp.raise_for_status()
        # The issues endpoint also returns PRs; filter them out.
        return [i for i in resp.json() if "pull_request" not in i]

    async def comment(self, issue_number: int, body: str) -> None:
        resp = await self._client.post(
            f"/repos/{self.repo}/issues/{issue_number}/comments", json={"body": body}
        )
        resp.raise_for_status()

    async def close_issue(self, issue_number: int) -> None:
        resp = await self._client.patch(
            f"/repos/{self.repo}/issues/{issue_number}", json={"state": "closed"}
        )
        resp.raise_for_status()

    async def delete_issue(self, node_id: str) -> None:
        """Permanently delete an issue via GraphQL. Irreversible; the token must
        belong to a user with admin/maintain rights on the repo."""
        query = (
            "mutation($id: ID!) { deleteIssue(input: {issueId: $id}) "
            "{ clientMutationId } }"
        )
        resp = await self._client.post(
            "/graphql", json={"query": query, "variables": {"id": node_id}}
        )
        resp.raise_for_status()
        errors = resp.json().get("errors")
        if errors:
            raise RuntimeError(f"deleteIssue failed: {errors}")

    async def add_labels(self, issue_number: int, labels: list[str]) -> None:
        resp = await self._client.post(
            f"/repos/{self.repo}/issues/{issue_number}/labels", json={"labels": labels}
        )
        resp.raise_for_status()

    async def ensure_label(self, name: str, color: str, description: str) -> None:
        """Create a label if it doesn't already exist (idempotent)."""
        resp = await self._client.post(
            f"/repos/{self.repo}/labels",
            json={"name": name, "color": color, "description": description},
        )
        if resp.status_code not in (201, 422):  # 422 = already exists
            resp.raise_for_status()

    async def get_file(self, path: str, ref: str = "HEAD") -> Optional[str]:
        """Return decoded text of a file in the repo, or None if missing."""
        resp = await self._client.get(
            f"/repos/{self.repo}/contents/{path}", params={"ref": ref}
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        if data.get("encoding") == "base64":
            return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return data.get("content")
