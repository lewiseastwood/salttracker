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

    monkeypatch.setattr(sources, "_get", lambda *_a, **_k: Resp())
    docs, status = sources.fetch_michigan_listing()
    assert status == "empty"
    assert docs == []
    assert all("260000000713" not in (d.url or "") for d in docs)


def test_fetch_listing_unfetched_is_not_ok(monkeypatch):
    monkeypatch.setattr(sources, "_get", lambda *_a, **_k: None)
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
