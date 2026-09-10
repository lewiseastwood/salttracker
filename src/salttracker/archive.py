"""Preserve a Michigan contract file before DTMB overwrites the path.

DTMB reuses the same contract-number URL each season. A new sha256 on that
path means the previous bytes are about to become unreachable. This module
copies the prior file next to its digest and submits the live URL to the
Wayback Save Page Now API so a citable snapshot exists.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from urllib.parse import quote

from .sources import http_get

SPN_BASE = "https://web.archive.org/save/"
WAYBACK_SNAP_RE = re.compile(
    r"https?://web\.archive\.org/web/(\d{14})(?:id_)?/https?://[^\s\"'<>]+",
    re.I,
)


def sha256_file(path: str) -> str | None:
    if not path or not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def archive_prior_bytes(path: str, dest_dir: str, digest: str | None = None) -> str | None:
    """Copy ``path`` to dest_dir/{digest}_{basename}. Returns the archive path."""
    if not path or not os.path.isfile(path):
        return None
    digest = digest or sha256_file(path)
    if not digest:
        return None
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"{digest}_{os.path.basename(path)}")
    if not os.path.isfile(dest):
        shutil.copy2(path, dest)
    return dest


def _extract_snapshot(text: str, headers: dict | None = None) -> str | None:
    headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    for key in ("content-location", "location"):
        val = headers.get(key, "")
        if "web.archive.org/web/" in val:
            if not val.startswith("http"):
                val = "https://web.archive.org" + val
            return val
    m = WAYBACK_SNAP_RE.search(text or "")
    if m:
        return m.group(0) if m.group(0).startswith("http") else "https://" + m.group(0)
    return None


def save_page_now(url: str, http_get_fn=None) -> dict:
    """Submit a live URL to Wayback Save Page Now. Returns snapshot metadata."""
    if not url:
        return {"ok": False, "error": "no url", "wayback_url": None, "timestamp": None}
    getter = http_get_fn or http_get
    target = SPN_BASE + quote(url, safe="")
    resp, status = getter(target, timeout=180, tries=2)
    body = ""
    headers = {}
    if resp is not None:
        body = getattr(resp, "text", "") or ""
        headers = dict(getattr(resp, "headers", {}) or {})
    snap = _extract_snapshot(body, headers)
    if not snap and status == 200 and "web.archive.org/web/" in (getattr(resp, "url", "") or ""):
        snap = getattr(resp, "url", None)
    if not snap:
        return {
            "ok": False,
            "error": f"Save Page Now did not return a snapshot (HTTP {status})",
            "wayback_url": None,
            "timestamp": None,
            "status": status,
        }
    ts = None
    m = re.search(r"/web/(\d{14})", snap)
    if m:
        ts = m.group(1)
        # Prefer the identity copy for byte-stable source_url.
        if "id_/" not in snap:
            snap = re.sub(r"/web/(\d{14})/", r"/web/\1id_/", snap, count=1)
    return {
        "ok": True,
        "error": None,
        "wayback_url": snap,
        "timestamp": ts,
        "status": status,
    }


def preserve_michigan_overwrite(
    live_url: str,
    prior_path: str,
    archive_dir: str,
    index_path: str,
    http_get_fn=None,
) -> dict:
    """Archive prior bytes and save the new live URL to Wayback.

    ``source_url`` is the Wayback snapshot of the new live URL only when Save
    Page Now actually returned one. A failed save still copies prior bytes
    into ``archive_dir`` and leaves ``source_url`` unset.
    """
    prior_sha = sha256_file(prior_path)
    archived = archive_prior_bytes(prior_path, archive_dir, prior_sha)
    spn = save_page_now(live_url, http_get_fn=http_get_fn)
    record = {
        "live_url": live_url,
        "prior_path": prior_path,
        "prior_sha256": prior_sha,
        "archived_path": archived,
        "wayback_url": spn.get("wayback_url") if spn.get("ok") else None,
        "wayback_timestamp": spn.get("timestamp") if spn.get("ok") else None,
        "save_ok": bool(spn.get("ok") and spn.get("wayback_url")),
        "save_error": None if (spn.get("ok") and spn.get("wayback_url")) else (
            spn.get("error") or "Save Page Now returned no snapshot URL"
        ),
        # Never record a Wayback source_url we did not get.
        "source_url": spn.get("wayback_url") if (spn.get("ok") and spn.get("wayback_url")) else None,
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    os.makedirs(os.path.dirname(index_path) or ".", exist_ok=True)
    existing = []
    if os.path.isfile(index_path):
        try:
            with open(index_path) as fh:
                existing = json.load(fh)
        except (OSError, ValueError):
            existing = []
    existing.append(record)
    with open(index_path, "w") as fh:
        json.dump(existing, fh, indent=2)
    return record
