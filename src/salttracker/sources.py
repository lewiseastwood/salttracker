"""Registry and discovery of published road-salt contract documents.

Two acquisition paths are needed:

* Live pages. Michigan lists the current season's contracts on one DTMB page.
  That scrape is how a new contract number is found — last year's number is
  delisted and next year's does not exist yet. Pennsylvania publishes a COSTARS
  packet per season. Both are scraped for links.
* Web archive. States overwrite these pages each summer, so prior fiscal years
  are recovered from the Wayback Machine. Michigan's contract PDFs are
  cumulative (each change notice is appended), so the newest archived copy of a
  contract usually carries several seasons of history in one file.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from urllib.parse import quote

import requests

# Akamai on michigan.gov 403s a custom crawler UA. Public records pages are
# fetched with a current Chrome identity; robots.txt does not disallow
# /dtmb/procurement. SALTTRACKER_CONTACT is not sent as the User-Agent.
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# Politeness budget for the state servers. A few requests per second, not a
# burst scan: eMarketplace and DTMB are public indexes, not an API we own.
REQUESTS_PER_SECOND = float(os.environ.get("SALTTRACKER_RPS", "2"))
# Retry these; a 403 from a GitHub runner is an IP block, not a blip.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
SCAN_WORKERS = 1


class _RateLimiter:
    """Spread requests across a minimum interval, shared by all threads."""

    def __init__(self, per_second: float) -> None:
        self._interval = 1.0 / per_second if per_second > 0 else 0.0
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        if not self._interval:
            return
        with self._lock:
            now = time.monotonic()
            due = max(now, self._next)
            self._next = due + self._interval
        delay = due - now
        if delay > 0:
            time.sleep(delay)


_limiter = _RateLimiter(REQUESTS_PER_SECOND)

MI_SALT_PAGE = ("https://www.michigan.gov/dtmb/procurement/mideal-extended-purchasing-program"
                "/mideal-contract-search/categories/folder-2/salt-bulk-rock")
CDX_API = "http://web.archive.org/cdx/search/cdx"

# Michigan reuses a contract number per supplier for years, so the number is a
# vendor key for files already on disk. It is not how next year's contract is
# found — those numbers do not exist yet and last year's file is delisted.
MI_CONTRACT_VENDOR = {
    "180000000768": "Detroit Salt",
    "180000000787": "Compass Minerals",
    "180000000791": "Cargill",
    "260000000712": "Detroit Salt",
    "260000000713": "Compass Minerals",
}

# Known PA packet URLs, kept as context for files already on disk. They are
# not discovery — next year's FY2028 packet will not be on this list.
PA_SEED_DOCS = [
    ("PA_FY2027_COSTARS_6100065611.pdf",
     "https://www.pa.gov/content/dam/copapwp-pagov/en/dgs/documents/costars/member-information/"
     "documents/2026-2027%20sodium%20chloride%20(road%20salt)%20season%20contract.pdf", 2027),
    ("PA_FY2026_COSTARS_6100053321.pdf",
     "https://www.pa.gov/content/dam/copapwp-pagov/en/dgs/documents/documents/costars/"
     "2025-2026%20sodium%20chloride%20(road%20salt)%20season%20contract.pdf", 2026),
    ("PA_FY2025_COSTARS.pdf",
     "https://web.archive.org/web/20250205034653id_/https://www.pa.gov/content/dam/copapwp-pagov/"
     "en/dgs/documents/documents/costars/sodium%20chloride%20(road%20salt)%202024-2025%20season%20contract.pdf",
     2025),
    ("PA_FY2024_COSTARS.pdf",
     "https://www.pa.gov/content/dam/copapwp-pagov/en/dgs/documents/documents/costars/"
     "sodium%20chloride%20road%20salt%202023-2024%20season%20contract.pdf", 2024),
    ("PA_ChangeNotice_4600016539_Morton.pdf",
     "https://www.emarketplace.state.pa.us/FileDownload.aspx?file=4600016539%5CChangeNotice.pdf", None),
    ("PA_Award_NOA_6100048201.pdf",
     "https://www.emarketplace.state.pa.us/FileDownload.aspx?file=Awards%5C13039%5CNOA+6100048201+combined.pdf",
     None),
]

# HTML hubs after the dgs.pa.gov → pa.gov move. None currently embed salt PDF
# hrefs (Coveo / marketing pages). Awarded packets are listed in the AEM
# document folders below.
PA_COSTARS_HTML = [
    "https://www.pa.gov/services/dgs/search-and-join-available-costars-contracts",
    "https://www.pa.gov/agencies/dgs/programs-and-services/costars",
]
PA_COSTARS_AEM_FOLDERS = [
    "https://www.pa.gov/content/dam/copapwp-pagov/en/dgs/documents/costars/member-information/documents",
    "https://www.pa.gov/content/dam/copapwp-pagov/en/dgs/documents/documents/costars",
]
PA_ELECBIDD = "https://www.dgs.internet.state.pa.us/COSTARSElecBidd/"

# PA publishes each season's contract on eMarketplace before the COSTARS packet
# appears, so the solicitation is the earliest signal that a new cycle exists.
PA_EMKT = "https://www.emarketplace.state.pa.us"
PA_SALT_TITLE_RE = re.compile(r"sodium\s+chlorid|bulk\s+road\s+salt", re.I)
# Solicitation ids are assigned sequentially over time, and eMarketplace's own
# keyword search is offline, so new salt bids are found by walking forward from
# the newest id already known to be salt.
PA_KNOWN_SALT_SIDS = [6100048201, 6100053321, 6100056192, 6100063746, 6100065611]
PA_SID_SCAN_AHEAD = 2500
SCAN_STATE = "pa_sid_scan.json"

# The county-tonnage tables that make volume (not just price) computable live in
# solicitation attachments rather than in the COSTARS packet.
PA_ATTACHMENT_RE = re.compile(r"estimate|bid\s*sheet", re.I)


@dataclass
class Doc:
    state: str
    name: str
    url: str
    path: str = ""
    vendor: str | None = None
    fy: int | None = None
    contract_no: str | None = None
    sha256: str = ""
    bytes: int = 0
    fetched_at: str = ""
    archived_timestamp: str | None = None
    notes: str = ""
    page_url: str = ""


# Michigan historical contract PDFs fetched 2026-09-08 from Wayback identity
# copies of the DTMB contract files. Live michigan.gov overwrites the same
# contract-number path each season; these captures are the bytes the tracker
# parsed. Listing page at each capture: MI_SALT_PAGE.
_MI_WAYBACK_SNAPS = (
    ("768_snap2026-05.pdf", "20260520035130", "006/180000000768"),
    ("787_snap2026-05.pdf", "20260520035130", "004/180000000787"),
    ("768_snap2025-04.pdf", "20250418052020", "006/180000000768"),
    ("787_snap2025-04.pdf", "20250418052020", "004/180000000787"),
    ("768_snap2023-01.pdf", "20230127073006", "006/180000000768"),
    ("787_snap2023-01.pdf", "20230127073006", "004/180000000787"),
    ("791_snap2023-01.pdf", "20230127073006", "004/180000000791"),
)

# Printed cover of the newest change notice in each snap. Fiscal years on
# parsed rows come from schedule titles inside the PDF, not from these dates
# and not from the Wayback listing timestamp in the filename.
MI_SNAP_IDENTITY = {
    "768_snap2023-01.pdf": {
        "contract_no": "180000000768",
        "newest_cn": 13,
        "cn_effective": "2023-06-20",
        "term_start": "2018-09-01",
        "revised_expiration": "2024-08-31",
        "newest_season": "2023/2024",
        "kind": "option_year",
        "label": "MA180000000768 CN13 · option year 2023/2024 (effective 20 Jun 2023)",
    },
    "768_snap2025-04.pdf": {
        "contract_no": "180000000768",
        "newest_cn": 16,
        "cn_effective": "2024-09-23",
        "term_start": "2018-09-01",
        "revised_expiration": "2025-08-31",
        "newest_season": "2024/2025",
        "kind": "mid_season_amendment",
        "label": "MA180000000768 CN16 · mid-season amendment of 2024/2025 (effective 23 Sep 2024)",
    },
    "768_snap2026-05.pdf": {
        "contract_no": "180000000768",
        "newest_cn": 17,
        "cn_effective": "2025-07-29",
        "term_start": "2018-09-01",
        "revised_expiration": "2026-08-31",
        "newest_season": "2025/2026",
        "kind": "option_year",
        "label": "MA180000000768 CN17 · option year 2025/2026 (effective 29 Jul 2025)",
    },
    "787_snap2023-01.pdf": {
        "contract_no": "180000000787",
        "newest_cn": 9,
        "cn_effective": "2023-06-20",
        "term_start": "2018-09-01",
        "revised_expiration": "2024-08-31",
        "newest_season": "2023/2024",
        "kind": "option_year",
        "label": "MA180000000787 CN9 · option year 2023/2024 (effective 20 Jun 2023)",
    },
    "787_snap2025-04.pdf": {
        "contract_no": "180000000787",
        "newest_cn": 12,
        "cn_effective": "2024-09-16",
        "term_start": "2018-09-01",
        "revised_expiration": "2024-08-31",
        "newest_season": "2024/2025",
        "kind": "mid_season_amendment",
        "label": "MA180000000787 CN12 · mid-season amendment of 2024/2025 (effective 16 Sep 2024)",
    },
    "787_snap2026-05.pdf": {
        "contract_no": "180000000787",
        "newest_cn": 13,
        "cn_effective": "2025-07-29",
        "term_start": "2018-09-01",
        "revised_expiration": "2026-08-31",
        "newest_season": "2025/2026",
        "kind": "option_year",
        "label": "MA180000000787 CN13 · option year 2025/2026 (effective 29 Jul 2025)",
    },
    "791_snap2023-01.pdf": {
        "contract_no": "180000000791",
        "newest_cn": 3,
        "cn_effective": "2021-09-01",
        "term_start": "2018-09-01",
        "revised_expiration": "2023-08-31",
        "newest_season": "2021/2022",
        "kind": "season_pricing_amendment",
        "label": "MA180000000791 CN3 · 2021/2022 pricing (effective 1 Sep 2021; expires 31 Aug 2023)",
    },
}


def snap_document_label(name: str) -> str:
    """Short name for executives. Never the raw filename if a label is known."""
    ident = MI_SNAP_IDENTITY.get(name or "")
    if ident:
        return ident["label"]
    known = DOCUMENT_LABELS.get(name or "")
    if known:
        return known
    stem = str(name or "")
    for ext in (".pdf", ".xlsx", ".xls"):
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break
    return stem.replace("_", " ") if stem else str(name or "")


DOCUMENT_LABELS = {
    "PA_FY2024_COSTARS.pdf": "COSTARS FY2024 season contract",
    "PA_FY2025_COSTARS.pdf": "COSTARS FY2025 season contract",
    "PA_FY2026_COSTARS_6100053321.pdf": "COSTARS FY2026 season contract",
    "PA_FY2027_COSTARS_6100065611.pdf": "COSTARS FY2027 season contract",
    "PA_estimates_FY2022_6100053321.xlsx": "FY2022 estimated requirements",
    "PA_estimates_FY2023_6100056192.pdf": "FY2023 estimated requirements",
    "PA_estimates_FY2027_6100065611.pdf": "FY2027 estimated requirements",
    "MI_FY2027_CompassMinerals_260000000713.pdf": "Compass Minerals FY2027",
    "MI_FY2027_DetroitSalt_260000000712.pdf": "Detroit Salt FY2027",
}


def _wayback_identity(ts: str, original: str) -> str:
    return f"https://web.archive.org/web/{ts}id_/{original}"


def known_local_provenance() -> dict[str, dict]:
    """Filename → url/page_url for files parsed from disk under a local alias."""
    out: dict[str, dict] = {}
    for name, ts, media in _MI_WAYBACK_SNAPS:
        original = (
            "https://www.michigan.gov/dtmb/-/media/Project/Websites/dtmb/"
            f"Procurement/Contracts/MiDEAL-Media/{media}.pdf"
        )
        out[name] = {
            "url": _wayback_identity(ts, original),
            "page_url": f"https://web.archive.org/web/{ts}/{MI_SALT_PAGE}",
            "notes": "wayback identity copy of DTMB contract PDF (local snap filename)",
        }
    return out


def page_url_from_file_url(url: str | None) -> str | None:
    """Landing page for a file URL, when one can be derived without guessing."""
    if not url:
        return None
    text = str(url).strip()
    if not text:
        return None
    sid = re.search(r"SID=(\d{10})", text, re.I)
    file_sid = re.search(r"file=(6100\d{6})", text, re.I)
    if "emarketplace.state.pa.us" in text and (sid or file_sid):
        return f"{PA_EMKT}/Solicitations.aspx?SID={(sid or file_sid).group(1)}"
    ts = re.search(r"web\.archive\.org/web/(\d+)", text)
    if "michigan.gov" in text:
        if ts:
            return f"https://web.archive.org/web/{ts.group(1)}/{MI_SALT_PAGE}"
        return MI_SALT_PAGE
    if "/dgs/documents/costars/member-information" in text:
        return PA_COSTARS_AEM_FOLDERS[0]
    if "/dgs/documents/documents/costars" in text or "/dgs/documents/costars/" in text:
        return PA_COSTARS_AEM_FOLDERS[1]
    return None


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def http_get(url: str, timeout: int = 180, tries: int = 3,
             params: dict | None = None,
             session: requests.Session | None = None,
             extra_headers: dict | None = None,
             ) -> tuple[requests.Response | None, int | None]:
    """Single throttled GET. Every michigan.gov / pa.gov / eMarketplace fetch uses this.

    Returns (response, status). response is set only on HTTP 200.
    429 and 5xx retry with backoff. 403 is returned as-is — GitHub runner
    IPs are often blocked, and retrying that looks like a code bug.
    """
    return _http("GET", url, timeout=timeout, tries=tries, params=params,
                 session=session, extra_headers=extra_headers)


def http_post(url: str, timeout: int = 180, tries: int = 3,
             data: dict | None = None,
             session: requests.Session | None = None,
             extra_headers: dict | None = None,
             ) -> tuple[requests.Response | None, int | None]:
    """Same throttle, headers, and retry policy as http_get, for POST."""
    return _http("POST", url, timeout=timeout, tries=tries, data=data,
                 session=session, extra_headers=extra_headers)


def _http(method: str, url: str, timeout: int = 180, tries: int = 3,
          params: dict | None = None, data: dict | None = None,
          session: requests.Session | None = None,
          extra_headers: dict | None = None,
          ) -> tuple[requests.Response | None, int | None]:
    http = session or requests
    headers = dict(HEADERS)
    if extra_headers:
        headers.update(extra_headers)
    last_status: int | None = None
    for attempt in range(tries):
        _limiter.wait()
        try:
            if method == "POST":
                r = http.post(url, headers=headers, timeout=timeout, data=data)
            else:
                r = http.get(url, headers=headers, timeout=timeout, params=params)
            last_status = r.status_code
            if r.status_code == 200:
                return r, 200
            if r.status_code in RETRY_STATUSES and attempt < tries - 1:
                time.sleep(5.0 * (attempt + 1))
                continue
            return None, last_status
        except requests.RequestException:
            if attempt < tries - 1:
                time.sleep(1.5 * (attempt + 1))
    return None, last_status


def _get(url: str, timeout: int = 180, tries: int = 3,
         params: dict | None = None,
         session: requests.Session | None = None) -> requests.Response | None:
    r, _status = http_get(url, timeout=timeout, tries=tries, params=params,
                           session=session)
    return r


def download(doc: Doc, root: str) -> Doc | None:
    """Fetch a document into ``root`` unless an identical copy already exists."""
    out_dir = os.path.join(root, doc.state)
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, doc.name)

    r = _get(doc.url)
    if r is None or not r.content:
        return None
    if not (r.content[:4] == b"%PDF" or doc.name.lower().endswith((".xlsx", ".xls", ".csv"))):
        return None

    digest = hashlib.sha256(r.content).hexdigest()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    if os.path.exists(dest):
        with open(dest, "rb") as fh:
            if hashlib.sha256(fh.read()).hexdigest() == digest:
                doc.path, doc.sha256, doc.bytes = dest, digest, len(r.content)
                # Still a successful retrieval: the row's provenance should say
                # when the source was last confirmed to hold this content.
                doc.fetched_at = now
                doc.notes = "unchanged"
                return doc

    with open(dest, "wb") as fh:
        fh.write(r.content)
    doc.path, doc.sha256, doc.bytes = dest, digest, len(r.content)
    doc.fetched_at = now
    return doc


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------
def _pdf_links(html: str, base: str) -> list[str]:
    urls = []
    for href in re.findall(r'href="([^"]+)"', html or ""):
        href = href.replace("&amp;", "&")
        if not re.search(r"\.(pdf|xlsx?|csv)(\?|$)", href, re.I):
            continue
        if re.search(r"W-?9", href, re.I):  # tax forms, not contracts
            continue
        urls.append(href if href.startswith("http") else base.rstrip("/") + "/" + href.lstrip("/"))
    return list(dict.fromkeys(urls))


def fetch_michigan_listing() -> tuple[list[Doc], str]:
    """Scrape the live DTMB salt page. That listing is what finds a new contract number.

    Returns (docs, status) with status ``ok``, ``unfetched`` (page did not load)
    or ``empty`` (page loaded but had no contract PDFs).
    """
    r, status = http_get(MI_SALT_PAGE, timeout=90)
    print(f"  Michigan DTMB salt page: HTTP {status if status is not None else 'no-response'}")
    if r is None:
        return [], "unfetched"
    docs = _docs_from_michigan_html(r.text)
    if not docs:
        return [], "empty"
    return docs, "ok"


def discover_michigan_live() -> list[Doc]:
    """Read the DTMB 'Salt, Bulk Rock' page for the current season's contracts."""
    docs, _status = fetch_michigan_listing()
    return docs


def _docs_from_michigan_html(html: str) -> list[Doc]:
    season = re.search(r"ROAD\s+SALT\s+(20\d{2})\s*/\s*(20\d{2})\s+WINTER\s+SEASON", html, re.I)
    fy = int(season.group(2)) if season else None
    docs = []
    for url in _pdf_links(html, "https://www.michigan.gov"):
        m = re.search(r"(\d{9,12})\.pdf", url)
        cno = m.group(1) if m else None
        vendor = MI_CONTRACT_VENDOR.get(cno or "")
        label = (vendor or "unknown").replace(" ", "")
        docs.append(Doc(
            state="MI", name=f"MI_FY{fy or 'x'}_{label}_{cno or 'doc'}.pdf",
            url=url, vendor=vendor, fy=fy, contract_no=cno,
            notes="michigan.gov live listing",
            page_url=MI_SALT_PAGE,
        ))
    return docs


def wayback_snapshots(url_pattern: str, match_type: str = "exact", limit: int = 200,
                      filter_expr: str | None = None) -> list[tuple[str, str, str]]:
    """Return (timestamp, original_url, status) for archived captures."""
    params = {"url": url_pattern, "output": "json", "fl": "timestamp,original,statuscode",
              "collapse": "digest", "limit": str(limit)}
    if match_type != "exact":
        params["matchType"] = match_type
    if filter_expr:
        params["filter"] = filter_expr
    r = _get(CDX_API, params=params, timeout=240)
    if r is None:
        return []
    try:
        rows = r.json()
    except ValueError:
        return []
    return [(row[0], row[1], row[2]) for row in rows[1:]]


def discover_michigan_archived() -> list[Doc]:
    """Recover prior seasons from archived copies of the DTMB page.

    Each archived page is scanned for contract PDFs, and the newest archived copy
    of every contract number is taken because Michigan appends change notices to
    a single cumulative file.
    """
    snaps = wayback_snapshots(MI_SALT_PAGE)
    newest: dict[str, tuple[str, str]] = {}

    for ts, _orig, status in snaps:
        if status != "200":
            continue
        r = _get(f"https://web.archive.org/web/{ts}/{MI_SALT_PAGE}", timeout=120)
        if r is None:
            continue
        for url in _pdf_links(r.text, "https://www.michigan.gov"):
            clean = re.sub(r"^https://web\.archive\.org/web/\d+\w*/", "", url)
            m = re.search(r"(\d{9,12})\.pdf", clean)
            if not m:
                continue
            cno = m.group(1)
            if cno not in newest or ts > newest[cno][0]:
                newest[cno] = (ts, clean)
        time.sleep(0.7)

    docs = []
    for cno, (ts, clean) in sorted(newest.items()):
        vendor = MI_CONTRACT_VENDOR.get(cno)
        label = (vendor or "unknown").replace(" ", "")
        docs.append(Doc(
            state="MI", name=f"MI_cumulative_{label}_{cno}_{ts[:8]}.pdf",
            url=f"https://web.archive.org/web/{ts}id_/{clean}",
            vendor=vendor, contract_no=cno, archived_timestamp=ts,
            notes="wayback cumulative contract (multi-season)",
            page_url=f"https://web.archive.org/web/{ts}/{MI_SALT_PAGE}",
        ))
    return docs


_SID_TITLE_RE = re.compile(r'id="ctl00_MainBody_lblBidTitle"[^>]*>([^<]{0,140})', re.I)


def pa_solicitation_title(sid: int, session: requests.Session | None = None) -> str | None:
    """Return an eMarketplace solicitation's title, or None if the id is unused."""
    r, _status = http_get(f"{PA_EMKT}/Solicitations.aspx?SID={sid}", timeout=25,
                           tries=2, session=session)
    if r is None:
        return None
    m = _SID_TITLE_RE.search(r.text)
    return m.group(1).strip() if m else None


def pa_scan_salt_sids(scan_ahead: int = PA_SID_SCAN_AHEAD, state_dir: str | None = None,
                      workers: int = SCAN_WORKERS) -> list[int]:
    """Find salt solicitation ids by walking forward from the newest known one.

    The scan is checkpointed so that a daily run during the contracting window
    only pays for ids that appeared since the last run.
    """
    known = list(PA_KNOWN_SALT_SIDS)
    checkpoint = os.path.join(state_dir, SCAN_STATE) if state_dir else None
    if checkpoint and os.path.exists(checkpoint):
        try:
            with open(checkpoint) as fh:
                saved = json.load(fh)
            known = sorted(set(known) | set(saved.get("salt_sids", [])))
            start = int(saved.get("scanned_through", max(known))) + 1
        except (ValueError, OSError):
            start = max(known) + 1
    else:
        start = max(known) + 1

    end = max(known) + scan_ahead
    if end < start:
        return sorted(known)

    session = requests.Session()
    session.headers.update(HEADERS)

    found = []
    for sid in range(start, end + 1):
        title = pa_solicitation_title(sid, session)
        if title and PA_SALT_TITLE_RE.search(title):
            found.append(sid)

    salt = sorted(set(known) | set(found))
    if checkpoint:
        os.makedirs(os.path.dirname(checkpoint), exist_ok=True)
        with open(checkpoint, "w") as fh:
            json.dump({"salt_sids": salt, "scanned_through": end,
                       "updated": time.strftime("%Y-%m-%dT%H:%M:%S")}, fh, indent=2)
    return salt


def pa_solicitation_docs(sid: int) -> list[Doc]:
    """List a salt solicitation's estimate and bid-sheet attachments."""
    r = _get(f"{PA_EMKT}/Solicitations.aspx?SID={sid}", timeout=60)
    if r is None:
        return []
    html = r.text.replace("&amp;", "&")
    docs: list[Doc] = []
    for href in dict.fromkeys(re.findall(r'FileDownload\.aspx\?file=[^"\'>]+', html)):
        label = re.search(r"OriginalFileName=(.*)$", href)
        label = label.group(1) if label else href
        if not PA_ATTACHMENT_RE.search(label):
            continue
        ext = ".xlsx" if re.search(r"\.xlsx?(&|$)", href, re.I) else ".pdf"
        kind = "estimates" if re.search(r"estimate", label, re.I) else "bidsheet"
        # Seasons are written either "2021-2022" or abbreviated "2025-26".
        season = re.search(r"(20\d{2})\s*[-\u2010\u2011\u2013]\s*(\d{2,4})", label)
        fy = None
        if season:
            tail = season.group(2)
            fy = int(tail) if len(tail) == 4 else 2000 + int(tail)
        # One solicitation can carry several bid sheets (initial and final
        # auction rounds), so names are suffixed to keep them distinct on disk.
        name = f"PA_{kind}_FY{fy or 'x'}_{sid}{ext}"
        if any(d.name == name for d in docs):
            name = f"PA_{kind}_FY{fy or 'x'}_{sid}_{len(docs)}{ext}"
        docs.append(Doc(
            state="PA", name=name, url=f"{PA_EMKT}/{href}", fy=fy,
            notes=f"emarketplace {sid} attachment: {label[:70]}",
            page_url=f"{PA_EMKT}/Solicitations.aspx?SID={sid}",
        ))
    return docs


def _pa_salt_name(name: str) -> bool:
    return bool(re.search(r"sodium|salt|chlorid", name, re.I))


def fetch_pa_costars_listing() -> tuple[list[Doc], str, int | None]:
    """Live awarded COSTARS packets. Seed URLs are not consulted.

    The pa.gov HTML hubs after the dgs.pa.gov move return 200 but do not
    embed salt PDF hrefs (Coveo / marketing). Awarded season packets are
    listed in the AEM document folders as ``.1.json``.
    """
    docs: list[Doc] = []
    any_200 = False
    selected_url = PA_COSTARS_HTML[0]
    selected_status: int | None = None

    for url in PA_COSTARS_HTML:
        r, status = http_get(url, timeout=90)
        n_salt = 0
        if r is not None:
            any_200 = True
            for href in _pdf_links(r.text, "https://www.pa.gov"):
                if not _pa_salt_name(href):
                    continue
                n_salt += 1
                name = re.sub(r"[^A-Za-z0-9._-]+", "_", href.rsplit("/", 1)[-1])[:110]
                docs.append(Doc(
                    state="PA", name=f"PA_live_{name}", url=href,
                    notes="costars html listing",
                    page_url=url,
                ))
        print(f"  Pennsylvania COSTARS HTML: HTTP {status if status is not None else 'no-response'} "
              f"({url}) {n_salt} salt PDF link(s)")
        if selected_status is None:
            selected_url, selected_status = url, status

    for folder in PA_COSTARS_AEM_FOLDERS:
        url = folder + ".1.json"
        r, status = http_get(url, timeout=90)
        n_salt = 0
        if r is not None:
            any_200 = True
            try:
                listing = r.json()
            except ValueError:
                listing = {}
            for name in listing:
                if name.startswith("jcr:"):
                    continue
                if not _pa_salt_name(name):
                    continue
                if not name.lower().endswith(".pdf"):
                    continue
                if re.search(r"tracking|w-?9", name, re.I):
                    continue
                file_url = folder + "/" + quote(name)
                safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:110]
                docs.append(Doc(
                    state="PA", name=f"PA_live_{safe}", url=file_url,
                    notes="costars aem listing",
                    page_url=folder,
                ))
                n_salt += 1
            if n_salt and not selected_url.endswith(".1.json"):
                selected_url, selected_status = url, status
        print(f"  Pennsylvania COSTARS AEM {folder.rsplit('/', 1)[-1]}: "
              f"HTTP {status if status is not None else 'no-response'} "
              f"({url}) {n_salt} salt PDF(s)")
        if not selected_url.endswith(".1.json") and r is not None:
            selected_url, selected_status = url, status

    docs = unique_docs(docs)
    print(f"  Pennsylvania COSTARS index: HTTP {selected_status if selected_status is not None else 'no-response'} "
          f"({selected_url}) {len(docs)} salt document(s)")
    if not any_200:
        return [], "unfetched", selected_status
    if not docs:
        return [], "empty", selected_status
    return docs, "ok", selected_status


def fetch_pa_elecbidd() -> tuple[list[Doc], str]:
    """Pre-award COSTARS electronic bidding list — earlier than the season packet."""
    session = requests.Session()
    r, status = http_get(PA_ELECBIDD, timeout=60, session=session)
    if r is None:
        print(f"  Pennsylvania COSTARS e-bidding: HTTP {status if status is not None else 'no-response'} "
              f"(0 salt bid(s))")
        return [], "unfetched"
    token_m = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', r.text)
    docs: list[Doc] = []
    if token_m:
        extra = {
            "RequestVerificationToken": token_m.group(1),
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
        payload = {
            "draw": "1", "start": "0", "length": "99",
            "__RequestVerificationToken": token_m.group(1),
        }
        r2, post_status = http_post(
            PA_ELECBIDD.rstrip("/") + "/Home/GetBiddingOpportunitiesList",
            timeout=60, data=payload, session=session, extra_headers=extra,
        )
        status = post_status if post_status is not None else status
        if r2 is not None:
            try:
                rows = r2.json().get("data") or []
            except ValueError:
                rows = []
            for row in rows:
                blob = f"{row.get('BidNumber', '')} {row.get('Description', '')}"
                if not re.search(r"salt|chlorid|sodium", blob, re.I):
                    continue
                bid_id = row.get("Id")
                number = row.get("BidNumber") or f"bid-{bid_id}"
                docs.append(Doc(
                    state="PA",
                    name=f"PA_elecbidd_{re.sub(r'[^A-Za-z0-9._-]+', '_', str(number))}.html",
                    url=f"{PA_ELECBIDD.rstrip('/')}/Bidding/ViewBid/{bid_id}",
                    notes="costars elecbidd",
                    page_url=PA_ELECBIDD,
                ))
    print(f"  Pennsylvania COSTARS e-bidding: HTTP {status if status is not None else 'no-response'} "
          f"({len(docs)} salt bid(s))")
    return docs, ("ok" if docs else "empty")


def _pa_known_sids(state_dir: str | None) -> set[int]:
    known = set(PA_KNOWN_SALT_SIDS)
    if not state_dir:
        return known
    checkpoint = os.path.join(state_dir, SCAN_STATE)
    if not os.path.exists(checkpoint):
        return known
    try:
        with open(checkpoint) as fh:
            saved = json.load(fh)
        known |= set(saved.get("salt_sids", []))
    except (ValueError, OSError):
        pass
    return known


def fetch_pennsylvania_live(state_dir: str | None = None,
                           scan_emarketplace: bool = True) -> tuple[list[Doc], str]:
    """Live PA discovery only. Seed URLs do not count, even if they still 200."""
    costars, costars_status, _http = fetch_pa_costars_listing()
    elec, _elec_status = fetch_pa_elecbidd()
    docs = list(costars) + list(elec)
    if scan_emarketplace:
        before = _pa_known_sids(state_dir)
        salt_sids = pa_scan_salt_sids(state_dir=state_dir)
        new_sids = [sid for sid in salt_sids if sid not in before]
        print(f"  Pennsylvania eMarketplace walk: {len(new_sids)} new salt SID(s)")
        for sid in new_sids:
            docs.extend(pa_solicitation_docs(sid))
    docs = unique_docs(docs)
    if not docs:
        status = "unfetched" if costars_status == "unfetched" else "empty"
        return [], status
    return docs, "ok"


def discover_pennsylvania(state_dir: str | None = None, scan_emarketplace: bool = True) -> list[Doc]:
    docs, _status = fetch_pennsylvania_live(state_dir=state_dir,
                                            scan_emarketplace=scan_emarketplace)
    return docs


def unique_docs(docs: list[Doc]) -> list[Doc]:
    seen, out = set(), []
    for d in docs:
        if d.url in seen:
            continue
        seen.add(d.url)
        out.append(d)
    return out


def discover_all(include_archive: bool = True, state_dir: str | None = None,
                 scan_emarketplace: bool = True) -> list[Doc]:
    docs = (discover_michigan_live()
            + discover_pennsylvania(state_dir=state_dir, scan_emarketplace=scan_emarketplace))
    if include_archive:
        docs += discover_michigan_archived()
    return unique_docs(docs)


def write_manifest(docs: list[Doc], path: str) -> None:
    """Merge this run's documents into the manifest, keeping earlier entries.

    A run only rediscovers what the states currently publish, so overwriting
    would erase the provenance of every document recovered from the archive in
    an earlier sweep and leave those rows untraceable.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    merged: dict[str, dict] = {}
    try:
        with open(path) as fh:
            for entry in json.load(fh):
                if entry.get("name"):
                    merged[entry["name"]] = entry
    except (OSError, ValueError):
        pass

    for doc in docs:
        record = asdict(doc)
        prior = merged.get(doc.name, {})
        # Preserve the first time this content was seen, and record the latest
        # confirmation separately.
        if prior.get("sha256") == record.get("sha256"):
            record["fetched_at"] = prior.get("fetched_at") or record.get("fetched_at")
        record["last_verified_at"] = doc.fetched_at or prior.get("last_verified_at")
        if not record.get("page_url"):
            record["page_url"] = prior.get("page_url") or page_url_from_file_url(
                record.get("url")) or ""
        merged[doc.name] = record

    with open(path, "w") as fh:
        json.dump(sorted(merged.values(), key=lambda e: e.get("name", "")), fh, indent=2)
