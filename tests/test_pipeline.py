"""Fast unit tests for the pure logic — no network, no credentials."""
import os
import tempfile

os.environ.setdefault("DATABASE_PATH", os.path.join(tempfile.gettempdir(), "t.db"))

from app.devin_client import DevinClient
from app.scanner import Finding, parse_requirements
from app.store import Store


def test_requirements_parsing_only_pins():
    text = "# c\njinja2==2.11.0\nrequests>=2.0\nflask==1.0  # inline\n-e .\n"
    assert parse_requirements(text) == [("jinja2", "2.11.0"), ("flask", "1.0")]


def test_devin_status_mapping_and_pr_extraction():
    norm = DevinClient.normalize(
        {"status_enum": "finished", "pull_request": {"url": "http://x/pr/1"},
         "structured_output": {"summary": "done", "confidence": "high"}}
    )
    assert norm["status"] == "completed"
    assert norm["pr_url"] == "http://x/pr/1"
    assert norm["confidence"] == "high"
    assert DevinClient.normalize({"status_enum": "blocked"})["status"] == "blocked"
    assert DevinClient.normalize({"status_enum": "working"})["status"] == "working"


def test_store_dedup_and_metrics(tmp_path):
    s = Store(str(tmp_path / "p.db"))
    rid = s.create_remediation(issue_number=1, issue_title="x", repo="o/r", source="manual")
    assert rid is not None
    # same issue again -> deduped
    assert s.create_remediation(issue_number=1, issue_title="x", repo="o/r", source="manual") is None
    s.update(rid, status="completed", pr_url="http://pr", acu_used=2.0)
    m = s.metrics()
    assert m["total"] == 1 and m["completed"] == 1 and m["prs_opened"] == 1
    assert m["success_rate"] == 100.0


def test_finding_issue_rendering():
    f = Finding("jinja2", "2.11.0", ["GHSA-x"], "XSS", "High", ["3.1.6"], "requirements/base.txt")
    assert "jinja2" in f.issue_title and "GHSA-x" in f.issue_title
    body = f.issue_body()
    assert "3.1.6" in body and "requirements/base.txt" in body
    assert f.fingerprint == "jinja2@2.11.0:GHSA-x"
