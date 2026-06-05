"""Thin async wrapper over the Devin v1 REST API.

Docs: https://docs.devin.ai/api-reference
  POST   /v1/sessions               -> create a session
  GET    /v1/sessions/{id}          -> poll status / structured output / PR
  POST   /v1/sessions/{id}/message  -> nudge a running/blocked session
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

log = logging.getLogger("devin")

# Map Devin's status_enum -> our internal lifecycle vocabulary.
_STATUS_MAP = {
    "working": "working",
    "resumed": "working",
    "resume_requested": "working",
    "resume_requested_frontend": "working",
    "suspend_requested": "working",
    "suspend_requested_frontend": "working",
    "blocked": "blocked",
    "finished": "completed",
    "expired": "expired",
}


class DevinClient:
    def __init__(self, api_key: str, base_url: str = "https://api.devin.ai"):
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=60,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def create_session(
        self,
        prompt: str,
        *,
        title: Optional[str] = None,
        tags: Optional[list[str]] = None,
        max_acu_limit: Optional[int] = None,
        structured_output_schema: Optional[dict] = None,
        idempotent: bool = True,
    ) -> dict:
        """Start a Devin session. Returns {session_id, url, is_new_session}."""
        body: dict[str, Any] = {"prompt": prompt, "idempotent": idempotent}
        if title:
            body["title"] = title
        if tags:
            body["tags"] = tags
        if max_acu_limit:
            body["max_acu_limit"] = max_acu_limit
        if structured_output_schema:
            body["structured_output_schema"] = structured_output_schema

        resp = await self._client.post("/v1/sessions", json=body)
        resp.raise_for_status()
        data = resp.json()
        log.info("created Devin session %s", data.get("session_id"))
        return data

    async def get_session(self, session_id: str) -> dict:
        resp = await self._client.get(f"/v1/sessions/{session_id}")
        resp.raise_for_status()
        return resp.json()

    async def send_message(self, session_id: str, message: str) -> None:
        resp = await self._client.post(
            f"/v1/sessions/{session_id}/message", json={"message": message}
        )
        resp.raise_for_status()

    @staticmethod
    def normalize(session: dict) -> dict:
        """Pull the fields the pipeline cares about out of a raw session."""
        raw_status = session.get("status_enum") or session.get("status") or "working"
        pr = session.get("pull_request") or {}
        structured = session.get("structured_output") or {}
        return {
            "status": _STATUS_MAP.get(raw_status, "working"),
            "raw_status": raw_status,
            "pr_url": pr.get("url") or structured.get("pr_url"),
            "summary": structured.get("summary"),
            "confidence": structured.get("confidence"),
            "acu_used": session.get("acu_used") or session.get("acus_used"),
        }
