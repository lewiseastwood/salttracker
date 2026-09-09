"""Michigan next-year contracts come from the DTMB listing, not a seed list."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from salttracker import sources  # noqa: E402


def test_seed_helpers_are_gone():
    assert not hasattr(sources, "discover_michigan_seed")
    assert not hasattr(sources, "MI_SEED_DOCS")


def test_listing_discovers_a_contract_number_that_is_not_in_the_vendor_map():
    html = """
    <p>ROAD SALT 2027 / 2028 WINTER SEASON</p>
    <a href="https://www.michigan.gov/dtmb/-/media/Project/270000000801.pdf">Contract</a>
    """
    docs = sources._docs_from_michigan_html(html)
    assert len(docs) == 1
    assert docs[0].contract_no == "270000000801"
    assert docs[0].fy == 2028
    assert docs[0].vendor is None
    assert "270000000801" not in sources.MI_CONTRACT_VENDOR


def test_fetch_listing_empty_page_is_empty_not_seeded(monkeypatch):
    class Resp:
        text = "<html><body>Salt, Bulk Rock — no files</body></html>"

    monkeypatch.setattr(sources, "http_get", lambda *_a, **_k: (Resp(), 200))
    docs, status = sources.fetch_michigan_listing()
    assert status == "empty"
    assert docs == []
    assert all("260000000713" not in (d.url or "") for d in docs)


def test_fetch_listing_unfetched_is_not_ok(monkeypatch):
    monkeypatch.setattr(sources, "http_get", lambda *_a, **_k: (None, 403))
    docs, status = sources.fetch_michigan_listing()
    assert status == "unfetched"
    assert docs == []


def test_discover_all_does_not_invent_last_years_contract(monkeypatch):
    monkeypatch.setattr(sources, "fetch_michigan_listing", lambda: ([], "empty"))
    monkeypatch.setattr(sources, "discover_pennsylvania", lambda **_k: [])
    monkeypatch.setattr(sources, "discover_michigan_archived", lambda: [])
    docs = sources.discover_all(include_archive=True, scan_emarketplace=False)
    assert docs == []
    assert not any(getattr(d, "contract_no", None) in {"260000000712", "260000000713"} for d in docs)


class _JsonResp:
    def __init__(self, payload, text=""):
        self.text = text
        self._payload = payload

    def json(self):
        return self._payload


def test_pa_seeds_do_not_satisfy_discovery(monkeypatch):
    monkeypatch.setattr(sources, "http_get", lambda *_a, **_k: (None, 404))
    monkeypatch.setattr(sources, "http_post", lambda *_a, **_k: (None, 404))
    monkeypatch.setattr(sources, "pa_scan_salt_sids", lambda **_k: list(sources.PA_KNOWN_SALT_SIDS))
    docs, status = sources.fetch_pennsylvania_live(scan_emarketplace=True)
    assert status == "unfetched"
    assert docs == []
    seed_urls = {u for _n, u, _fy in sources.PA_SEED_DOCS}
    assert not any(d.url in seed_urls for d in docs)


def test_pa_aem_listing_finds_salt_pdfs_not_tracking_sheets(monkeypatch):
    html_ok = type("R", (), {"text": "<html></html>"})()
    aem = {
        "jcr:content": {},
        "2026-2027 sodium chloride (road salt) season contract.pdf": {},
        "sodium chloride ordering tracking sheet template.xlsx": {},
        "W-9.pdf": {},
    }

    def fake_http_get(url, **_k):
        if url.endswith(".1.json") and "member-information" in url:
            return _JsonResp(aem), 200
        if url.endswith(".1.json"):
            return _JsonResp({"jcr:content": {}}), 200
        if "COSTARSElecBidd" in url:
            return type("R", (), {"text": "<html></html>"})(), 200
        return html_ok, 200

    monkeypatch.setattr(sources, "http_get", fake_http_get)
    monkeypatch.setattr(sources, "http_post", lambda *_a, **_k: (None, 404))
    docs, status, http = sources.fetch_pa_costars_listing()
    assert status == "ok"
    assert http == 200
    assert len(docs) == 1
    assert "2026-2027" in docs[0].url
    assert "tracking" not in docs[0].url.lower()
    seed_urls = {u for _n, u, _fy in sources.PA_SEED_DOCS}
    # Live AEM URL may equal a seed URL; that is listing, not the seed list.
    assert docs[0].notes == "costars aem listing"
