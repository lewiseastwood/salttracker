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
    assert docs[0].page_url == sources.MI_SALT_PAGE


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
    assert docs[0].page_url in sources.PA_COSTARS_AEM_FOLDERS


def test_page_url_from_file_url_is_derived_not_guessed():
    emkt = (
        "https://www.emarketplace.state.pa.us/FileDownload.aspx"
        "?file=6100065611/Solicitation_21.pdf&OriginalFileName=estimates.pdf"
    )
    assert sources.page_url_from_file_url(emkt) == (
        "https://www.emarketplace.state.pa.us/Solicitations.aspx?SID=6100065611"
    )
    change = (
        "https://www.emarketplace.state.pa.us/FileDownload.aspx"
        "?file=4600016539%5CChangeNotice.pdf"
    )
    assert sources.page_url_from_file_url(change) is None
    costars = (
        "https://www.pa.gov/content/dam/copapwp-pagov/en/dgs/documents/"
        "documents/costars/sodium%20chloride%20road%20salt%202023-2024%20season%20contract.pdf"
    )
    assert sources.page_url_from_file_url(costars) == sources.PA_COSTARS_AEM_FOLDERS[1]
    wayback = (
        "https://web.archive.org/web/20260520035130id_/"
        "https://www.michigan.gov/dtmb/-/media/Project/Websites/dtmb/"
        "Procurement/Contracts/MiDEAL-Media/006/180000000768.pdf"
    )
    assert sources.page_url_from_file_url(wayback) == (
        f"https://web.archive.org/web/20260520035130/{sources.MI_SALT_PAGE}"
    )
    assert sources.page_url_from_file_url(None) is None
    snap = sources.known_local_provenance()["768_snap2026-05.pdf"]
    assert "20260520035130id_" in snap["url"]
    assert "180000000768" in snap["url"]


def test_snap_identity_is_keyed_off_printed_change_notices_not_wayback_dates():
    ident = sources.MI_SNAP_IDENTITY
    assert ident["768_snap2023-01.pdf"]["newest_cn"] == 13
    assert ident["768_snap2023-01.pdf"]["kind"] == "option_year"
    assert ident["768_snap2023-01.pdf"]["cn_effective"] == "2023-06-20"
    assert ident["768_snap2025-04.pdf"]["kind"] == "mid_season_amendment"
    assert ident["787_snap2026-05.pdf"]["newest_season"] == "2025/2026"
    assert ident["791_snap2023-01.pdf"]["newest_season"] == "2021/2022"
    assert "CN3" in sources.snap_document_label("791_snap2023-01.pdf")
    assert sources.snap_document_label("PA_FY2024_COSTARS.pdf") == "COSTARS FY2024 season contract"
    assert sources.snap_document_label("PA_FY2025_COSTARS.pdf") == "COSTARS FY2025 season contract"

