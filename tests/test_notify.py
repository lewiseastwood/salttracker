from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from salttracker.notify import alert_body, dispatch
from salttracker.sources import UA, REQUESTS_PER_SECOND


def test_user_agent_does_not_impersonate_a_browser():
    assert "SaltTracker/" in UA
    assert "Mozilla" not in UA
    assert REQUESTS_PER_SECOND <= 3


def test_alert_body_lists_each_signal():
    text = alert_body([
        {"kind": "new-season", "detail": "PA FY2028 appeared"},
        {"kind": "new-document", "detail": "newly published document: x.pdf"},
    ])
    assert "PA FY2028" in text
    assert "x.pdf" in text


def test_dispatch_is_a_noop_without_config(monkeypatch):
    monkeypatch.delenv("SALTTRACKER_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SALTTRACKER_ALERT_EMAIL", raising=False)
    monkeypatch.delenv("SALTTRACKER_SMTP_HOST", raising=False)
    assert dispatch([{"kind": "new-season", "detail": "x"}], "2026-01-01") == []
    assert dispatch([], "2026-01-01") == []
