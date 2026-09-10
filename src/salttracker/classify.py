"""Classify a newly fetched contract file against the pattern registry.

Known shapes are matched locally. New files also go through the classify
API (when a key is set) so printed dates and undetermined fields are explicit.
A miss on the registry is unfamiliar — the API cannot promote it to a known
type.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime

from . import columns, patterns
from .util import season_to_fy

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

DATE_TOKEN = (
    r"(?P<month>January|February|March|April|May|June|July|August|September|"
    r"October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)"
    r"\.?\s+(?P<day>\d{1,2}),?\s+(?P<year>20\d{2})"
    r"|(?P<iso>20\d{2}-\d{2}-\d{2})"
    r"|(?P<mdy>\d{1,2}/\d{1,2}/20\d{2})"
)
EFFECTIVE_RE = re.compile(
    r"(?:effective\s+date|effective)\s*:?\s*(" + DATE_TOKEN + r")", re.I,
)
EXPIRATION_RE = re.compile(
    r"(?:revised\s+)?expir(?:ation|es)\s*(?:date)?\s*:?\s*(" + DATE_TOKEN + r")",
    re.I,
)
COVER_PAGES = 3
COVER_CHARS = 12_000


def extract_cover_text(path: str, pages: int = COVER_PAGES) -> str:
    if not path or not os.path.isfile(path):
        return ""
    try:
        import pdfplumber
    except ImportError:
        return ""
    bits = []
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages[:pages]:
                bits.append(page.extract_text() or "")
    except Exception:
        return ""
    return "\n".join(bits)[:COVER_CHARS]


def parse_printed_date(text: str) -> str | None:
    if not text:
        return None
    m = re.search(DATE_TOKEN, text, re.I)
    if not m:
        return None
    if m.group("iso"):
        return m.group("iso")
    if m.group("mdy"):
        a, b, y = m.group("mdy").split("/")
        return f"{int(y):04d}-{int(a):02d}-{int(b):02d}"
    month = MONTHS.get((m.group("month") or "").lower().rstrip("."))
    if not month:
        return None
    return f"{int(m.group('year')):04d}-{month:02d}-{int(m.group('day')):02d}"


def _first_date(regex: re.Pattern, text: str) -> str | None:
    m = regex.search(text or "")
    if not m:
        return None
    return parse_printed_date(m.group(0))


def fy_window(fy: int) -> tuple[date, date]:
    """Oct 1 of (fy-1) through Sep 30 of fy."""
    return date(fy - 1, 10, 1), date(fy, 9, 30)


def date_in_fy(value: str | None, fy: int | None) -> bool | None:
    if not value or not fy:
        return None
    try:
        d = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
    start, end = fy_window(int(fy))
    return start <= d <= end


_FIELD_LABELS = {
    "document_type": "document type",
    "tons_basis": "tons_basis",
    "effective_date": "printed effective date",
    "expiration_date": "printed expiration date",
    "fiscal_year": "fiscal year",
    "volume_entity": "volume column entity",
}


def _undetermined(result: dict, entry: dict | None) -> list[str]:
    required = list((entry or {}).get("required_fields") or (
        "document_type", "tons_basis", "fiscal_year", "volume_entity",
    ))
    missing = []
    for key in required:
        if result.get(key) in (None, "", "unfamiliar"):
            missing.append(_FIELD_LABELS.get(key, key))
    if not result.get("columns_match"):
        missing.append("column signature")
    return missing


def _from_registry(entry: dict | None, name: str, url: str, state: str | None,
                   text: str) -> dict:
    fy = season_to_fy(text) or season_to_fy(name or "") or season_to_fy(url or "")
    effective = _first_date(EFFECTIVE_RE, text)
    expiration = _first_date(EXPIRATION_RE, text)
    if entry:
        return {
            "filename": name,
            "url": url,
            "state": entry.get("state") or state,
            "registry_id": entry.get("id"),
            "document_type": entry.get("document_type"),
            "tons_basis": entry.get("tons_basis"),
            "volume_entity": entry.get("volume_entity"),
            "effective_date": effective,
            "expiration_date": expiration,
            "fiscal_year": fy,
            "familiar": True,
            "confidence": 0.9 if (effective or fy) else 0.7,
            "source": "registry",
            "notes": entry.get("notes") or "",
        }
    return {
        "filename": name,
        "url": url,
        "state": state,
        "registry_id": None,
        "document_type": "unfamiliar",
        "tons_basis": None,
        "volume_entity": None,
        "effective_date": effective,
        "expiration_date": expiration,
        "fiscal_year": fy,
        "familiar": False,
        "confidence": 0.2 if (effective or fy) else 0.0,
        "source": "registry",
        "notes": "Not in the pattern registry.",
    }


def classify_via_api(cover: str, name: str, url: str, state: str | None,
                     registry: dict, http_post=None) -> dict | None:
    """Ask the classify API for printed fields. Cannot mark a miss as familiar."""
    key = os.environ.get("SALTTRACKER_CLASSIFY_API_KEY")
    if not key:
        return None
    endpoint = os.environ.get(
        "SALTTRACKER_CLASSIFY_API_URL",
        "https://api.openai.com/v1/chat/completions",
    )
    model = os.environ.get("SALTTRACKER_CLASSIFY_MODEL", "gpt-4o-mini")
    catalog = [
        {
            "id": t.get("id"),
            "document_type": t.get("document_type"),
            "state": t.get("state"),
            "tons_basis": t.get("tons_basis"),
            "volume_entity": t.get("volume_entity"),
            "notes": t.get("notes"),
        }
        for t in patterns.types(registry)
    ]
    payload = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "Classify a road-salt procurement PDF. Match only against the "
                    "given registry ids. If none fit, document_type is unfamiliar "
                    "and familiar is false. Return JSON with keys: registry_id, "
                    "document_type, tons_basis, effective_date, expiration_date, "
                    "fiscal_year, volume_entity, confidence (0-1), undetermined "
                    "(list of strings), familiar (bool)."
                ),
            },
            {
                "role": "user",
                "content": json.dumps({
                    "filename": name,
                    "url": url,
                    "state": state,
                    "registry": catalog,
                    "cover_text": cover[:8000],
                }),
            },
        ],
    }
    poster = http_post
    if poster is None:
        import requests
        def poster(url, **kw):
            return requests.post(url, timeout=60, **kw)

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    try:
        resp = poster(endpoint, headers=headers, json=payload)
        if getattr(resp, "status_code", 500) != 200:
            return None
        body = resp.json()
        content = body["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def classify_document(path: str, *, name: str = "", url: str = "",
                      state: str | None = None, registry: dict | None = None,
                      http_post=None) -> dict:
    registry = registry or patterns.load_registry()
    name = name or os.path.basename(path or "")
    text = extract_cover_text(path)
    entry = patterns.match_type(name, url, text, state=state, registry=registry)
    result = _from_registry(entry, name, url, state, text)

    api = classify_via_api(text, name, url, state, registry, http_post=http_post)
    if api:
        # API may fill dates/FY. It cannot override an unfamiliar miss into a
        # known type that is not in the registry.
        api_id = api.get("registry_id")
        known_ids = {t.get("id") for t in patterns.types(registry)}
        if result["familiar"]:
            for key in ("effective_date", "expiration_date", "fiscal_year",
                        "confidence"):
                if api.get(key) not in (None, "", []):
                    result[key] = api[key]
            result["source"] = "registry+api"
        elif api_id in known_ids:
            # API named a registry id the local matcher missed — still familiar
            # only because the id exists in the file.
            hit = next(t for t in patterns.types(registry) if t.get("id") == api_id)
            filled = _from_registry(hit, name, url, state, text)
            for key in ("effective_date", "expiration_date", "fiscal_year",
                        "confidence"):
                if api.get(key) not in (None, "", []):
                    filled[key] = api[key]
            filled["source"] = "api"
            result = filled
        else:
            result["source"] = "api"
            result["confidence"] = min(float(api.get("confidence") or 0), 0.4)
            for key in ("effective_date", "expiration_date", "fiscal_year"):
                if api.get(key) not in (None, "", []):
                    result[key] = api[key]

    if isinstance(result.get("fiscal_year"), str) and str(result["fiscal_year"]).isdigit():
        result["fiscal_year"] = int(result["fiscal_year"])

    # Column set is checked against the registry entry actually used, not the
    # filename guess. The API cannot mark columns as matching.
    type_id = result.get("registry_id")
    used = next((t for t in patterns.types(registry) if t.get("id") == type_id), None)
    observed = columns.extract_header_sets(path)
    col = columns.match_columns(observed, used)
    result["columns_match"] = bool(col.get("columns_match"))
    result["columns_observed"] = col.get("columns_observed") or []
    result["matched_signature"] = col.get("matched_signature")
    result["column_drift"] = col.get("drift")
    result["undetermined"] = _undetermined(result, used if result.get("familiar") else None)
    if result.get("undetermined"):
        result["confidence"] = min(float(result.get("confidence") or 0), 0.6)
    return result
