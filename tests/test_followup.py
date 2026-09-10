"""PA follow-up writes drafts and never sends unless explicitly confirmed."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import pa_followup as followup  # noqa: E402


def test_contacts_are_five_pa_counties():
    rows = followup.load_contacts()
    assert len(rows) == 5
    counties = [r["county"] for r in rows]
    assert counties == ["Allegheny", "Westmoreland", "Luzerne", "Washington", "Erie"]
    for row in rows:
        assert "@" in row["email"]
        assert row["source_url"].startswith("http")


def test_draft_only_does_not_send(tmp_path, monkeypatch):
    monkeypatch.setattr(followup, "OUT", tmp_path)
    monkeypatch.setattr(followup, "DRAFTS", tmp_path / "drafts")
    monkeypatch.delenv("SALTTRACKER_FOLLOWUP_CONFIRM", raising=False)
    rc = followup.main([])
    assert rc == 0
    drafts = list((tmp_path / "drafts").glob("*.eml"))
    assert len(drafts) == 5
    xlsx = tmp_path / "PA_procurement_contacts.xlsx"
    assert xlsx.exists()
    body = drafts[0].read_text(errors="replace")
    assert "Right-to-Know" in body
    assert "COSTARS" in body


def test_send_without_confirm_raises(monkeypatch):
    monkeypatch.delenv("SALTTRACKER_FOLLOWUP_CONFIRM", raising=False)
    rows = followup.load_contacts()
    try:
        followup.send_drafts(rows, "2026-09-10T12:00:00")
        assert False, "should have refused"
    except RuntimeError as exc:
        assert "CONFIRM" in str(exc)
