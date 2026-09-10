"""Fetch published contract files for the Streamlit Tables & export tab.

Local PDFs (after a scrape) are preferred. Otherwise the published URL is
fetched through sources.http_get. Failures are returned as strings so they can
be shown; they are never dropped from the list of attempted files.
"""
from __future__ import annotations

import io
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from salttracker.sources import http_get  # noqa: E402

RAW_DIR = os.path.join(ROOT, "data", "raw")


def local_source_path(doc_name: str, state: str | None = None,
                      raw_dir: str = RAW_DIR) -> str | None:
    if not doc_name:
        return None
    codes = []
    if state in ("MI", "PA"):
        codes.append(state)
    elif isinstance(state, str) and ", " in state:
        codes.extend(p.strip() for p in state.split(",") if p.strip() in ("MI", "PA"))
    codes.extend(c for c in ("MI", "PA") if c not in codes)
    for code in codes:
        path = os.path.join(raw_dir, code, str(doc_name))
        if os.path.isfile(path):
            return path
    return None


def _clean_url(url: str | None) -> str | None:
    if url is None:
        return None
    if isinstance(url, float) and url != url:
        return None
    text = str(url).strip()
    if not text or text.lower() in ("nan", "none"):
        return None
    return text


def published_link(value) -> str | None:
    return _clean_url(value)


def _looks_like_file(blob: bytes) -> bool:
    if blob[:4] == b"%PDF" or blob[:2] == b"PK":
        return True
    head = blob[:200].lstrip().lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html"):
        return False
    return len(blob) > 1000


def load_source_bytes(doc_name: str, state: str | None, url: str | None,
                      raw_dir: str = RAW_DIR,
                      page_url: str | None = None) -> tuple[bytes | None, str | None]:
    """Return (bytes, None) or (None, failure message).

    Three non-success states, kept distinct because an empty file URL is not
    a fetch failure:
    * fetch failed — a file URL was tried and did not return a contract file
    * no stable public file URL — no direct file; a landing page may exist
    * provenance unknown — neither a file URL nor a source page is recorded
    """
    path = local_source_path(doc_name, state, raw_dir=raw_dir)
    if path:
        with open(path, "rb") as fh:
            return fh.read(), None
    url = _clean_url(url)
    page = _clean_url(page_url)
    if not url:
        if page:
            return None, f"{doc_name}: no stable public file URL; page {page}"
        return None, f"{doc_name}: provenance unknown"
    resp, status = http_get(url)
    if resp is None:
        return None, f"{doc_name}: {url} failed (HTTP {status})"
    blob = resp.content or b""
    if not _looks_like_file(blob):
        return None, f"{doc_name}: {url} returned HTML or an empty body, not a contract file"
    return blob, None


def zip_sources(rows: list[dict], raw_dir: str = RAW_DIR) -> tuple[bytes | None, list[str]]:
    """Zip every row. Failed URLs are listed; they are not omitted from the report."""
    failures: list[str] = []
    buf = io.BytesIO()
    n = 0
    names_used: set[str] = set()
    with zipfile.ZipFile(buf, "w") as zf:
        for row in rows:
            name = str(row.get("source_doc") or "contract")
            blob, err = load_source_bytes(
                name, row.get("state"), row.get("source_url"), raw_dir=raw_dir,
                page_url=row.get("source_page_url"),
            )
            if err:
                failures.append(err)
                continue
            arc = os.path.basename(name)
            if arc in names_used:
                stem, ext = os.path.splitext(arc)
                arc = f"{stem}_{len(names_used)}{ext}"
            names_used.add(arc)
            zf.writestr(arc, blob)
            n += 1
    if not n:
        return None, failures
    return buf.getvalue(), failures
