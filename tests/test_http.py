"""Every state-site request goes through sources.http_get."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from salttracker import sources  # noqa: E402


class _FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text
        self.content = b""


def test_http_get_retries_429_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, headers=None, timeout=None, params=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResponse(429)
        return _FakeResponse(200, "ok")

    monkeypatch.setattr(sources.requests, "get", fake_get)
    monkeypatch.setattr(sources.time, "sleep", lambda *_a: None)
    r, status = sources.http_get("https://www.michigan.gov/example")
    assert status == 200
    assert r is not None
    assert calls["n"] == 2


def test_http_get_does_not_retry_403(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, headers=None, timeout=None, params=None):
        calls["n"] += 1
        return _FakeResponse(403)

    monkeypatch.setattr(sources.requests, "get", fake_get)
    monkeypatch.setattr(sources.time, "sleep", lambda *_a: None)
    r, status = sources.http_get("https://www.michigan.gov/example", tries=3)
    assert r is None
    assert status == 403
    assert calls["n"] == 1


def test_http_get_retries_503(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, headers=None, timeout=None, params=None):
        calls["n"] += 1
        return _FakeResponse(503)

    monkeypatch.setattr(sources.requests, "get", fake_get)
    monkeypatch.setattr(sources.time, "sleep", lambda *_a: None)
    r, status = sources.http_get("https://www.pa.gov/example", tries=3)
    assert r is None
    assert status == 503
    assert calls["n"] == 3


def test_emarketplace_title_uses_http_get(monkeypatch):
    called = []

    def fake_http_get(url, **_k):
        called.append(url)
        return None, 403

    monkeypatch.setattr(sources, "http_get", fake_http_get)
    assert sources.pa_solicitation_title(6100065611) is None
    assert called
    assert "emarketplace.state.pa.us" in called[0]


def test_http_get_sends_browser_headers(monkeypatch):
    captured = {}

    def fake_get(url, headers=None, timeout=None, params=None):
        captured["headers"] = headers
        return _FakeResponse(200, "ok")

    monkeypatch.setattr(sources.requests, "get", fake_get)
    monkeypatch.setattr(sources.time, "sleep", lambda *_a: None)
    r, status = sources.http_get("https://www.michigan.gov/example")
    assert status == 200
    assert r is not None
    headers = captured["headers"]
    assert "Mozilla/5.0" in headers["User-Agent"]
    assert "Chrome/" in headers["User-Agent"]
    assert "SaltTracker/" not in headers["User-Agent"]
    assert "text/html" in headers["Accept"]
    assert headers["Accept-Language"].startswith("en")
    assert "gzip" in headers["Accept-Encoding"]
    assert "br" not in headers["Accept-Encoding"]
    assert headers["Sec-Fetch-Dest"] == "document"
    assert headers["Sec-Fetch-Mode"] == "navigate"
    assert headers["Sec-Fetch-Site"] == "none"
    assert headers["Sec-Fetch-User"] == "?1"


def test_http_post_does_not_retry_403(monkeypatch):
    calls = {"n": 0}

    def fake_post(url, headers=None, timeout=None, data=None):
        calls["n"] += 1
        return _FakeResponse(403)

    monkeypatch.setattr(sources.requests, "post", fake_post)
    monkeypatch.setattr(sources.time, "sleep", lambda *_a: None)
    r, status = sources.http_post("https://www.dgs.internet.state.pa.us/COSTARSElecBidd/Home/GetBiddingOpportunitiesList", tries=3)
    assert r is None
    assert status == 403
    assert calls["n"] == 1


def test_rate_limit_is_between_two_and_five():
    assert 2 <= sources.REQUESTS_PER_SECOND <= 5
