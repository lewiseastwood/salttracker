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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

import requests

# Identify the crawler honestly. Override with SALTTRACKER_CONTACT (mailbox
# or a repo URL). Unattended runs fall back to this public repo so DTMB / DGS
# can still see who is fetching.
CONTACT = os.environ.get("SALTTRACKER_CONTACT", "").strip() or (
    "https://github.com/lewiseastwood/salttracker"
)
_ua = ["SaltTracker/1.0", "(academic public-records research"]
if CONTACT:
    _ua.append(f"; +{CONTACT}")
_ua.append(")")
UA = "".join(_ua)
HEADERS = {"User-Agent": UA, "Accept": "*/*"}
if "@" in CONTACT and " " not in CONTACT:
    HEADERS["From"] = CONTACT

# Politeness budget for the state servers. A few requests per second, not a
# burst scan: eMarketplace and DTMB are public indexes, not an API we own.
REQUESTS_PER_SECOND = float(os.environ.get("SALTTRACKER_RPS", "2"))
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

# Pennsylvania COSTARS season packets. Paths are not templatable across years,
# so known-good URLs are pinned and the live COSTARS page is also crawled.
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
    ("PA_ChangeNotice_4600016539_Morton.pdf",
     "https://www.emarketplace.state.pa.us/FileDownload.aspx?file=4600016539%5CChangeNotice.pdf", None),
    ("PA_Award_NOA_6100048201.pdf",
     "https://www.emarketplace.state.pa.us/FileDownload.aspx?file=Awards%5C13039%5CNOA+6100048201+combined.pdf",
     None),
]

PA_COSTARS_INDEX = "https://www.pa.gov/agencies/dgs/programs-and-services/costars/costars-contracts.html"

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


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def _get(url: str, timeout: int = 180, tries: int = 3,
         params: dict | None = None) -> requests.Response | None:
    for attempt in range(tries):
        _limiter.wait()
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout, params=params)
            if r.status_code == 200:
                return r
            # Back off rather than retry straight into a server that is
            # rate-limiting or temporarily refusing us.
            if r.status_code in (403, 429, 503):
                time.sleep(5.0 * (attempt + 1))
                continue
        except requests.RequestException:
            pass
        time.sleep(1.5 * (attempt + 1))
    return None


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
    r = _get(MI_SALT_PAGE, timeout=90)
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
        ))
    return docs


_SID_TITLE_RE = re.compile(r'id="ctl00_MainBody_lblBidTitle"[^>]*>([^<]{0,140})', re.I)


def pa_solicitation_title(sid: int, session: requests.Session | None = None) -> str | None:
    """Return an eMarketplace solicitation's title, or None if the id is unused."""
    http = session or requests
    _limiter.wait()
    try:
        r = http.get(f"{PA_EMKT}/Solicitations.aspx?SID={sid}", headers=HEADERS, timeout=25)
    except requests.RequestException:
        return None
    if r.status_code != 200:
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

    def probe(sid: int) -> tuple[int, str | None]:
        return sid, pa_solicitation_title(sid, session)

    found = []
    with ThreadPoolExecutor(workers) as pool:
        for sid, title in pool.map(probe, range(start, end + 1)):
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
        ))
    return docs


def discover_pennsylvania(state_dir: str | None = None, scan_emarketplace: bool = True) -> list[Doc]:
    """Seed known PA packets, crawl the COSTARS index, and scan eMarketplace."""
    docs = [Doc(state="PA", name=n, url=u, fy=fy, notes="seed") for n, u, fy in PA_SEED_DOCS]

    r = _get(PA_COSTARS_INDEX, timeout=90)
    if r is not None:
        for url in _pdf_links(r.text, "https://www.pa.gov"):
            if not re.search(r"sodium|salt", url, re.I):
                continue
            name = re.sub(r"[^A-Za-z0-9._-]+", "_", url.rsplit("/", 1)[-1])[:110]
            if any(d.url == url for d in docs):
                continue
            docs.append(Doc(state="PA", name=f"PA_live_{name}", url=url, notes="costars index"))

    if scan_emarketplace:
        for sid in pa_scan_salt_sids(state_dir=state_dir):
            docs.extend(pa_solicitation_docs(sid))
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
        merged[doc.name] = record

    with open(path, "w") as fh:
        json.dump(sorted(merged.values(), key=lambda e: e.get("name", "")), fh, indent=2)
