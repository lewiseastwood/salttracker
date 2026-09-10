"""PA follow-up writes drafts and never sends unless explicitly confirmed."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import pa_followup as followup  # noqa: E402


def test_contacts_are_five_pa_counties_with_aoro():
    rows = followup.load_contacts()
    assert len(rows) == 5
    counties = [r["county"] for r in rows]
    assert counties == ["Allegheny", "Westmoreland", "Luzerne", "Washington", "Erie"]
    by_county = {r["county"]: r for r in rows}
    assert by_county["Allegheny"]["aoro_email"] == "openrecords@alleghenycounty.us"
    assert by_county["Westmoreland"]["aoro_email"] == "records@westmorelandcountypa.gov"
    assert by_county["Luzerne"]["aoro_email"] == "Admin-RTK@luzernecounty.org"
    assert by_county["Erie"]["aoro_email"] == "DHeasley@eriecountypa.gov"
    assert by_county["Washington"]["aoro_status"] == "UNVERIFIED"
    assert by_county["Washington"]["aoro_email"] == "UNVERIFIED"
    assert by_county["Washington"]["submission_method"].startswith("mail")
    assert by_county["Washington"]["rtk_web_form_url"].startswith("https://apps.docusign.com/webforms/")
    assert "UNCONFIRMED" in by_county["Washington"]["notes"]
    for row in rows:
        assert row["aoro_source_url"].startswith("http")
        assert row["purchasing_email"]
        assert row["salt_holder_has_own_oro"] == "no"
        assert row["salt_routing_cite"].startswith("http")
        assert row["submission_method"]
        assert "purchasing@" not in (row["aoro_email"] or "").lower() or row["aoro_status"] == "UNVERIFIED"


def test_draft_only_does_not_send(tmp_path, monkeypatch):
    monkeypatch.setattr(followup, "OUT", tmp_path)
    monkeypatch.setattr(followup, "DRAFTS", tmp_path / "drafts")
    monkeypatch.delenv("SALTTRACKER_FOLLOWUP_CONFIRM", raising=False)
    rc = followup.main([])
    assert rc == 0
    drafts = list((tmp_path / "drafts").glob("*.eml"))
    assert len(drafts) == 4
    assert not any("washington" in p.name for p in drafts)
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


def test_washington_aoro_email_is_not_sendable():
    rows = {r["county"]: r for r in followup.load_contacts()}
    assert followup.aoro_email(rows["Washington"]) is None
    assert followup.aoro_email(rows["Allegheny"]) == "openrecords@alleghenycounty.us"
