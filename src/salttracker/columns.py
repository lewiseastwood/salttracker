"""Column signatures: a type name is not enough if the columns drifted.

Each registry type lists the exact header sets this tracker already knows how
to read. A new file is routine only when an extracted header set equals one of
those, with no extras and nothing missing.
"""
from __future__ import annotations

import os
import re

SEASON_RE = re.compile(r"20\d{2}\s*-?\s*20\d{2,4}|20\d{2}")
PROGRAM_RE = re.compile(
    r"\b(early fill(?: up)?|seasonal (?:back up|fill)|seasonal)\b", re.I,
)
DATA_HINT = re.compile(r"\b(county|cumulative|price|ton|bidder|estimate)\b", re.I)


def canon_header(cell: str) -> str:
    s = re.sub(r"\s+", " ", str(cell or "")).strip().lower()
    s = SEASON_RE.sub("", s)
    s = PROGRAM_RE.sub("", s)
    s = s.replace("#", " ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.replace("total qty", "total quantity")
    s = s.replace("costar ", "costars ")
    if s == "costar":
        s = "costars"
    return s


def header_set(cells: list[str]) -> frozenset[str]:
    return frozenset(c for c in (canon_header(c) for c in cells) if c)


def _is_header_row(cells: list[str]) -> bool:
    nonempty = [c for c in cells if str(c or "").strip()]
    if len(nonempty) < 4:
        return False
    first = canon_header(nonempty[0])
    if not first or first[0].isdigit():
        return False
    if first in ("business days", "total", "total tonnage"):
        return False
    # Data rows often leak into table[0]; a true header has no bare numbers.
    if any(re.fullmatch(r"\d+", canon_header(c) or "") for c in nonempty):
        return False
    joined = " ".join(canon_header(c) for c in nonempty)
    return bool(DATA_HINT.search(joined))


def extract_header_sets(path: str, pages: int = 40) -> list[frozenset[str]]:
    """Distinct data-table header sets from the first pages of a PDF."""
    if not path or not os.path.isfile(path):
        return []
    try:
        import pdfplumber
    except ImportError:
        return []
    found: list[frozenset[str]] = []
    seen: set[frozenset[str]] = set()
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages[:pages]:
                for table in page.extract_tables() or []:
                    if not table:
                        continue
                    raw = [str(c or "") for c in table[0]]
                    if not _is_header_row(raw):
                        continue
                    s = header_set(raw)
                    if s and s not in seen:
                        seen.add(s)
                        found.append(s)
    except Exception:
        return found
    return found


def allowed_sets(entry: dict) -> list[frozenset[str]]:
    out = []
    for sig in entry.get("column_signatures") or []:
        headers = sig.get("headers") or []
        if headers:
            out.append(header_set(headers))
    return out


def match_columns(observed: list[frozenset[str]], entry: dict | None) -> dict:
    """A registered header set must appear exactly. Type name is not enough.

    Extra tables in the same PDF (rosters, agency notes) are ignored. A
    primary table whose columns drifted — FY2026 estimates vs the FY2027
    nine-column layout — matches none of the signatures and is not routine.
    """
    allowed = allowed_sets(entry or {})
    if not allowed:
        return {
            "columns_match": False,
            "columns_observed": [sorted(s) for s in observed],
            "matched_signature": None,
            "drift": "registry type has no column_signatures",
        }
    if not observed:
        return {
            "columns_match": False,
            "columns_observed": [],
            "matched_signature": None,
            "drift": "no data-table header extracted",
        }
    signatures = entry.get("column_signatures") or []
    matched = None
    for obs in observed:
        hit = next((i for i, a in enumerate(allowed) if obs == a), None)
        if hit is not None:
            matched = signatures[hit].get("id")
            break
    if matched is None:
        return {
            "columns_match": False,
            "columns_observed": [sorted(s) for s in observed],
            "matched_signature": None,
            "drift": (
                "no extracted header set is an exact match for this document type "
                "(saw: " + "; ".join(", ".join(sorted(s)) for s in observed) + ")"
            ),
        }
    return {
        "columns_match": True,
        "columns_observed": [sorted(s) for s in observed],
        "matched_signature": matched,
        "drift": None,
    }
