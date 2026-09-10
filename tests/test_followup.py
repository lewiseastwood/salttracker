"""PA follow-up writes drafts and never sends unless explicitly confirmed."""
from __future__ import annotations

from email.parser import BytesParser
from email.policy import default as email_policy
import datetime as dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import pa_followup as followup  # noqa: E402

SENDER_REPLY = "lewis@example.test"
SENDER_ADDRESS = "123 Test Street, Example, PA 00000"


def _set_sender(monkeypatch):
    monkeypatch.setenv("SALTTRACKER_FOLLOWUP_REPLY_TO", SENDER_REPLY)
    monkeypatch.setenv("SALTTRACKER_FOLLOWUP_ADDRESS", SENDER_ADDRESS)
    monkeypatch.setenv("SALTTRACKER_FOLLOWUP_NAME", "Lewis Eastwood")


def _stub_dgs_form(monkeypatch):
    monkeypatch.setattr(
        followup,
        "build_dgs_form_attachment",
        lambda *a, **k: (b"%PDF-1.4\ntrailer\n%%EOF\n", "filled"),
    )


def test_contacts_are_five_pa_counties_with_aoro():
    rows = followup.load_contacts()
    county_rows = [r for r in rows if not followup.is_dgs_row(r)]
    assert len(county_rows) == 5
    counties = [r["county"] for r in county_rows]
    assert counties == ["Allegheny", "Westmoreland", "Luzerne", "Washington", "Erie"]
    dgs = next(r for r in rows if followup.is_dgs_row(r))
    assert dgs["aoro_email"] == "DGS-RTK@pa.gov"
    assert dgs["aoro_officer"] == "L. Paul Vezzetti"
    assert dgs["received_on"] == ""
    assert dgs["responded_on"] == ""
    by_county = {r["county"]: r for r in county_rows}
    assert by_county["Allegheny"]["aoro_email"] == "openrecords@alleghenycounty.us"
    assert by_county["Westmoreland"]["aoro_email"] == "records@westmorelandcountypa.gov"
    assert by_county["Luzerne"]["aoro_email"] == "Admin-RTK@luzernecounty.org"
    assert by_county["Erie"]["aoro_email"] == "DHeasley@eriecountypa.gov"
    assert by_county["Washington"]["aoro_status"] == "UNVERIFIED"
    assert by_county["Washington"]["aoro_email"] == "UNVERIFIED"
    assert by_county["Washington"]["submission_method"].startswith("mail")
    assert by_county["Washington"]["rtk_web_form_url"].startswith("https://apps.docusign.com/webforms/")
    assert "UNCONFIRMED" in by_county["Washington"]["notes"]
    for row in county_rows:
        assert row["aoro_source_url"].startswith("http")
        assert row["purchasing_email"]
        assert row["salt_holder_has_own_oro"] == "no"
        assert row["salt_routing_cite"].startswith("http")
        assert row["submission_method"]
        assert "purchasing@" not in (row["aoro_email"] or "").lower() or row["aoro_status"] == "UNVERIFIED"
        assert "received_on" in row
        assert "responded_on" in row
        assert row["received_on"] == ""
        assert row["responded_on"] == ""


def test_refuses_drafts_without_reply_to_or_address(tmp_path, monkeypatch):
    monkeypatch.setattr(followup, "OUT", tmp_path)
    monkeypatch.setattr(followup, "DRAFTS", tmp_path / "drafts")
    monkeypatch.setattr(followup, "load_local_env", lambda *a, **k: None)
    monkeypatch.delenv("SALTTRACKER_FOLLOWUP_CONFIRM", raising=False)
    monkeypatch.delenv("SALTTRACKER_FOLLOWUP_REPLY_TO", raising=False)
    monkeypatch.delenv("SALTTRACKER_FOLLOWUP_ADDRESS", raising=False)
    rc = followup.main([])
    assert rc == 1
    drafts_dir = tmp_path / "drafts"
    assert not drafts_dir.exists() or not list(drafts_dir.glob("*.eml"))


def test_draft_only_does_not_send(tmp_path, monkeypatch):
    monkeypatch.setattr(followup, "OUT", tmp_path)
    monkeypatch.setattr(followup, "DRAFTS", tmp_path / "drafts")
    monkeypatch.delenv("SALTTRACKER_FOLLOWUP_CONFIRM", raising=False)
    _set_sender(monkeypatch)
    _stub_dgs_form(monkeypatch)
    rc = followup.main([])
    assert rc == 0
    drafts = list((tmp_path / "drafts").glob("*.eml"))
    names = [p.name for p in drafts]
    assert any(n.endswith("_dgs.eml") for n in names)
    assert not any("washington" in n for n in names)
    assert len([n for n in names if n.endswith("_dgs.eml")]) == 1
    assert len(drafts) == 5  # four counties + DGS
    xlsx = tmp_path / "PA_procurement_contacts.xlsx"
    assert xlsx.exists()
    county_path = next(p for p in drafts if "allegheny" in p.name)
    county = BytesParser(policy=email_policy).parsebytes(county_path.read_bytes()).get_content()
    assert "Right-to-Know" in county
    assert "6100065611" in county
    assert "FY2027" in county
    assert SENDER_ADDRESS in county
    assert SENDER_REPLY in county
    assert "academic research" not in county.lower()
    assert "participating municipalities" not in county.lower()
    assert "current or most recently awarded" not in county.lower()
    assert "tons committed" in county
    assert "tons actually received" in county
    assert "off-contract" in county.lower() or "not made under that COSTARS contract" in county


def test_dgs_draft_goes_to_aoro_not_commodity_specialist(tmp_path, monkeypatch):
    _set_sender(monkeypatch)
    _stub_dgs_form(monkeypatch)
    monkeypatch.setattr(followup, "DRAFTS", tmp_path / "drafts")
    path = followup.write_drafts(followup.load_contacts(), "2026-09-10T12:00:00")[-1]
    assert path.name.endswith("_dgs.eml")
    raw = path.read_bytes()
    headers = raw.decode("utf-8", errors="replace")
    body = followup.message_plain_text(raw)
    assert "To: DGS-RTK@pa.gov" in headers
    assert "L. Paul Vezzetti" in body
    assert "Agency Open Records Officer" in body
    assert "Cheryl Spackman" in body
    assert "weekly" in body.lower()
    assert "6100065611" in body
    assert "awarded tons" in body.lower()
    assert "tons shipped" in body.lower()
    assert "without supplier attribution" in body.lower()
    assert "confidential proprietary" in body.lower()
    assert "commodity specialist" not in body.lower()
    assert "randmiller" not in body.lower()
    assert "academic research" not in body.lower()
    assert SENDER_ADDRESS in body
    assert followup.DGS_AORO["source_url"].startswith("https://www.pa.gov/services/dgs/")
    parsed = BytesParser(policy=email_policy).parsebytes(raw)
    filenames = [p.get_filename() for p in parsed.walk() if p.get_filename()]
    assert followup.DGS_FORM_FILENAME in filenames
    assert "X-SaltTracker-DGS-Form: filled" in headers


def test_dgs_form_paste_block_if_pdf_unavailable(tmp_path, monkeypatch):
    _set_sender(monkeypatch)
    monkeypatch.setattr(followup, "build_dgs_form_attachment", lambda *a, **k: (None, "unavailable"))
    monkeypatch.setattr(followup, "DRAFTS", tmp_path / "drafts")
    path = followup.write_drafts(followup.load_contacts(), "2026-09-10T12:00:00")[-1]
    body = followup.message_plain_text(path.read_bytes())
    assert "DGS STANDARD RTKL FORM — PASTE BLOCK" in body
    assert SENDER_REPLY in body
    assert "Yes, electronic" in body


def test_parse_mailing_address_and_form_values():
    street, city, state, zip_code = followup.parse_mailing_address(SENDER_ADDRESS)
    assert (street, city, state, zip_code) == ("123 Test Street", "Example", "PA", "00000")
    values = followup.dgs_form_values(SENDER_REPLY, SENDER_ADDRESS, "Lewis Eastwood")
    assert values["Full Name"] == "Lewis Eastwood"
    assert values["Email_3"] == SENDER_REPLY
    assert values["City"] == "Example"
    assert "6100065611" in values["Records Requested1"]
    assert "without supplier attribution" in values["Records Requested2"]
    assert values["Yes electronic"] == "/On"


def test_fill_dgs_rtkl_form_when_blank_cached():
    import pytest
    from io import BytesIO
    from pypdf import PdfReader

    blank_path = "/tmp/dgs_rtk.pdf"
    if not os.path.exists(blank_path):
        pytest.skip("cached DGS form not present")
    filled = followup.fill_dgs_rtkl_form(
        open(blank_path, "rb").read(),
        SENDER_REPLY,
        SENDER_ADDRESS,
        "Lewis Eastwood",
    )
    fields = PdfReader(BytesIO(filled)).get_fields()
    assert fields["Full Name"].get("/V") == "Lewis Eastwood"
    assert "6100065611" in str(fields["Records Requested1"].get("/V"))
    assert fields["Yes electronic"].get("/V") == "/On"


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


def test_commonwealth_holidays_match_published_circulars():
    # AC 25-13 (Holidays — 2026)
    h2026 = followup.commonwealth_holidays(2026)
    assert dt.date(2026, 1, 1) in h2026
    assert dt.date(2026, 1, 19) in h2026
    assert dt.date(2026, 2, 16) in h2026
    assert dt.date(2026, 5, 25) in h2026
    assert dt.date(2026, 6, 19) in h2026
    assert dt.date(2026, 7, 3) in h2026
    assert dt.date(2026, 9, 7) in h2026
    assert dt.date(2026, 10, 12) in h2026
    assert dt.date(2026, 11, 11) in h2026
    assert dt.date(2026, 11, 26) in h2026
    assert dt.date(2026, 11, 27) in h2026
    assert dt.date(2026, 12, 25) in h2026
    # AC 24-12 (Holidays — 2025)
    h2025 = followup.commonwealth_holidays(2025)
    assert dt.date(2025, 1, 20) in h2025
    assert dt.date(2025, 7, 4) in h2025
    assert dt.date(2025, 10, 13) in h2025
    assert dt.date(2025, 11, 27) in h2025
    assert dt.date(2025, 11, 28) in h2025


def test_five_business_days_skip_receipt_day_and_labor_day():
    # Received Monday 14 Sep 2026 → due the following Monday.
    assert followup.add_business_days(dt.date(2026, 9, 14), 5) == dt.date(2026, 9, 21)
    # Received Friday 4 Sep 2026; Labor Day (7 Sep) is skipped → due 14 Sep.
    assert followup.add_business_days(dt.date(2026, 9, 4), 5) == dt.date(2026, 9, 14)


def test_clock_row_awaiting_overdue_and_responded():
    today = dt.date(2026, 9, 10)
    awaiting = followup.clock_row({"county": "DGS", "received_on": "2026-09-08"}, today)
    assert awaiting["status"] == "awaiting"
    assert awaiting["response_due"] == dt.date(2026, 9, 15)
    assert awaiting["overdue"] is False
    overdue = followup.clock_row({"county": "Erie", "received_on": "2026-08-24"}, today)
    assert overdue["overdue"] is True
    assert overdue["response_due"] == dt.date(2026, 8, 31)
    assert overdue["appeal_by"] == dt.date(2026, 9, 22)
    assert "appeal window open" in overdue["flag"]
    responded = followup.clock_row(
        {"county": "DGS", "received_on": "2026-08-01", "responded_on": "2026-08-05"},
        today,
    )
    assert responded["status"] == "responded"
    assert responded["overdue"] is False


def test_clock_does_not_write_drafts_or_need_sender(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(followup, "OUT", tmp_path)
    monkeypatch.setattr(followup, "DRAFTS", tmp_path / "drafts")
    monkeypatch.setattr(followup, "load_local_env", lambda *a, **k: None)
    monkeypatch.delenv("SALTTRACKER_FOLLOWUP_REPLY_TO", raising=False)
    monkeypatch.delenv("SALTTRACKER_FOLLOWUP_ADDRESS", raising=False)
    rc = followup.main(["--clock"])
    assert rc == 0
    assert not list(tmp_path.glob("**/*.eml"))
    out = capsys.readouterr().out
    assert "received_on" in out
    assert "not received" in out
    assert "No overdue rows." in out


def test_poll_inbox_flag_is_gone():
    try:
        followup.main(["--poll-inbox"])
        assert False, "expected argparse to reject --poll-inbox"
    except SystemExit as exc:
        assert exc.code == 2
