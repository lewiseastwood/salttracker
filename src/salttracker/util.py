"""Shared helpers: numeric coercion, fiscal-year mapping, vendor canonicalization."""
from __future__ import annotations

import re
import unicodedata

# --------------------------------------------------------------------------
# Fiscal year
# --------------------------------------------------------------------------
# Road-salt fiscal years run Oct 1 - Sep 30 and are named for the calendar year
# in which they END. The 2025/2026 winter season is therefore FY2026
# (Oct 1 2025 - Sep 30 2026).

# Season labels use a variety of dashes across documents: "2025/2026",
# "2025-26", and the U+2010/U+2011 hyphens PA's renewal tables emit.
SEASON_RE = re.compile(r"(20\d{2})\s*[/\u2010\u2011\u2012\u2013\u2014-]\s*(20\d{2}|\d{2})\b")


def season_to_fy(text: str) -> int | None:
    """Return the fiscal year for a season label such as '2025/2026' or 'FY 2025-26'."""
    if not text:
        return None
    m = SEASON_RE.search(str(text))
    if not m:
        return None
    start, end = m.group(1), m.group(2)
    if len(end) == 2:
        end = start[:2] + end
    start_i, end_i = int(start), int(end)
    if end_i != start_i + 1:
        return None
    return end_i


def fy_label(fy: int | None) -> str:
    return f"FY{fy}" if fy else ""


def fy_season_label(fy: int | None) -> str:
    return f"{fy - 1}/{fy}" if fy else ""


# --------------------------------------------------------------------------
# Numeric coercion
# --------------------------------------------------------------------------
_NULLS = {"", "-", "--", "n/a", "na", "none", "tbd"}


def _clean_numeric_text(value) -> str:
    if value is None:
        return ""
    s = unicodedata.normalize("NFKD", str(value))
    s = s.replace("\n", " ").replace("\u2013", "-").replace("\u2014", "-")
    # State PDFs frequently emit stray spaces *inside* numbers ("2 00", "1 ,000",
    # "5 0"). Spaces are never meaningful inside a numeric cell here, so drop them.
    s = s.replace(" ", "").replace(",", "").replace("$", "").replace("*", "")
    return s.strip()


def to_number(value) -> float | None:
    """Parse a possibly glyph-mangled numeric/currency cell."""
    s = _clean_numeric_text(value)
    if s.lower() in _NULLS:
        return None
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    m = re.fullmatch(r"-?\d*\.?\d+", s)
    if not m:
        return None
    try:
        n = float(s)
    except ValueError:
        return None
    return -n if neg else n


def is_blank(value) -> bool:
    return _clean_numeric_text(value).lower() in _NULLS


# --------------------------------------------------------------------------
# Vendor canonicalization
# --------------------------------------------------------------------------
# Suppliers appear under many legal/among-year variants. Collapse them so a
# vendor's history is continuous across fiscal years and states.
# Patterns are prefix-tolerant because PDF column widths truncate supplier
# names mid-word ("American Ro", "Compass Miner", "Eastern Sal").
VENDOR_PATTERNS: list[tuple[str, str]] = [
    (r"american\s*ro", "American Rock Salt"),
    (r"\bars\b", "American Rock Salt"),
    (r"cargill", "Cargill"),
    (r"compass|north\s*american\s*salt|nasc\b", "Compass Minerals"),
    # Bidder columns abbreviate to just "Detroit"/"Compass"/"Morton".
    (r"detroit", "Detroit Salt"),
    (r"eastern\s*sal", "Eastern Salt"),
    (r"morton", "Morton Salt"),
    (r"riverside", "Riverside Construction Materials"),
    (r"kissner|k\+s|ksm", "Kissner Group"),
    (r"\bsifto\b", "Compass Minerals"),
    (r"atlantic\s*salt", "Atlantic Salt"),
    (r"innovative\s*municipal", "Innovative Municipal Products"),
]


def vendor_from_text(text: str | None, limit: int = 1500) -> str | None:
    """Find the first known supplier named in a block of text.

    Used on a contract's cover page, where the awarded contractor is named near
    the top, so that a document's vendor can be established without relying on
    the filename or a per-row bidder column.
    """
    if not text:
        return None
    low = str(text)[:limit].lower()
    best: tuple[int, str] | None = None
    for pattern, canon in VENDOR_PATTERNS:
        m = re.search(pattern, low)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), canon)
    return best[1] if best else None


def canonical_vendor(name: str | None) -> str | None:
    """Map a raw supplier string to a canonical vendor name."""
    if not name:
        return None
    s = re.sub(r"\s+", " ", str(name)).strip()
    if not s or s.lower() in _NULLS:
        return None
    low = s.lower()
    for pattern, canon in VENDOR_PATTERNS:
        if re.search(pattern, low):
            return canon
    # Unknown vendor: title-case it but keep it in the data rather than dropping.
    cleaned = re.sub(r"[,.]?\s*(inc|llc|lc|co|corp|company|incorporated)\.?$", "", s, flags=re.I)
    return cleaned.strip().title() or None


# --------------------------------------------------------------------------
# County normalization
# --------------------------------------------------------------------------
def clean_county(value: str | None) -> str | None:
    if not value:
        return None
    s = re.sub(r"\s+", " ", str(value)).strip()
    s = re.sub(r"^\d+[-\s]*", "", s)  # strip leading region/item numbers
    s = re.sub(r"\bcounty\b", "", s, flags=re.I).strip(" ,-")
    if not s or s.lower() in _NULLS:
        return None
    if not re.search(r"[A-Za-z]", s):
        return None
    return s.title()
