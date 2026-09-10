"""Hard assertions that block a refresh PR from being labeled routine."""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime

from .classify import date_in_fy

LIVE_MOVE = 0.10

# Live FY2027 numbers vs the historical series. Never treat these as the
# same contract just because the vendor matches.
MI_RENUMBER = (
    {"from": "180000000768", "to": "260000000712", "vendor": "Detroit Salt"},
    {"from": "180000000787", "to": "260000000713", "vendor": "Compass Minerals"},
)
CONTRACT_NO_RE = re.compile(r"\b(\d{9,12})\b")


def _blank(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("nan", "none"):
        return None
    return text


def _ton(value) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n != n or n <= 0:
        return None
    return n


def contract_numbers(*texts: str) -> set[str]:
    found: set[str] = set()
    for text in texts:
        if not text:
            continue
        found.update(CONTRACT_NO_RE.findall(str(text)))
    return found


def _assert(kind: str, detail: str, **extra) -> dict:
    return {"kind": kind, "detail": detail, **extra}


def column_drift(classifications: list[dict]) -> list[dict]:
    out = []
    for c in classifications:
        if not c.get("familiar"):
            continue
        if c.get("columns_match"):
            continue
        out.append(_assert(
            "column_drift",
            f"{c.get('filename')}: {c.get('column_drift') or 'column set does not match the registry signature'}",
            source_doc=c.get("filename"),
        ))
    return out


def undetermined_fields(classifications: list[dict]) -> list[dict]:
    out = []
    for c in classifications:
        missing = c.get("undetermined") or []
        if not missing:
            continue
        out.append(_assert(
            "undetermined",
            f"{c.get('filename')}: classifier could not determine {', '.join(missing)}",
            source_doc=c.get("filename"),
            undetermined=missing,
        ))
    return out


def empty_provenance(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        url = _blank(row.get("source_url"))
        page = _blank(row.get("source_page_url"))
        if url or page:
            continue
        name = row.get("source_doc") or row.get("name") or "row"
        out.append(_assert(
            "empty_provenance",
            f"{name}: no source_url and no source_page_url — unknown provenance is a failure",
            source_doc=name,
        ))
    return out


def tons_basis_mismatch(classifications: list[dict], series_basis: dict[str, set[str]]
                        ) -> list[dict]:
    """A new document whose tons_basis is not one already used for that state.

    Known mixed series (PA printed vs committed_estimate) are allowed if both
    already appear in the live series. A third basis, or a new basis on MI,
    is not routine.
    """
    out = []
    for c in classifications:
        state = c.get("state")
        basis = c.get("tons_basis")
        if not state or not basis or not c.get("familiar"):
            continue
        seen = series_basis.get(str(state)) or set()
        if not seen:
            continue
        if str(basis) not in seen:
            out.append(_assert(
                "tons_basis_mismatch",
                f"{c.get('filename')}: tons_basis={basis} is not in the {state} series "
                f"(has {sorted(seen)})",
                state=state, tons_basis=basis, source_doc=c.get("filename"),
            ))
    return out


def supplier_dropped(previous: dict[str, set[str]], current: dict[str, set[str]]
                     ) -> list[dict]:
    """A named supplier present last season and absent this one (the Cargill case)."""
    out = []
    for state, prev in previous.items():
        now = current.get(state) or set()
        for vendor in sorted(prev - now):
            if vendor in ("", "Unattributed"):
                continue
            out.append(_assert(
                "supplier_dropped",
                f"{state}: {vendor} was present last season and is absent this one",
                state=state, vendor=vendor,
            ))
    return out


def dates_outside_fiscal_year(classifications: list[dict]) -> list[dict]:
    out = []
    for c in classifications:
        fy = c.get("fiscal_year")
        if not fy:
            continue
        try:
            fy_i = int(fy)
        except (TypeError, ValueError):
            continue
        for field in ("effective_date", "expiration_date"):
            value = c.get(field)
            inside = date_in_fy(value, fy_i)
            if inside is False:
                out.append(_assert(
                    "dates_outside_fy",
                    f"{c.get('filename')}: printed {field} {value} is outside FY{fy_i} "
                    f"(1 Oct {fy_i - 1} – 30 Sep {fy_i})",
                    source_doc=c.get("filename"), field=field, value=value,
                    fiscal_year=fy_i,
                ))
    return out


def sha256_alias(new_docs: list[dict], manifest: list[dict]) -> list[dict]:
    """Same bytes under a different name — the filename-join bug."""
    by_hash: dict[str, str] = {}
    for entry in manifest:
        digest = _blank(entry.get("sha256"))
        name = entry.get("name")
        if digest and name:
            by_hash[digest] = name
    out = []
    for doc in new_docs:
        digest = _blank(doc.get("sha256"))
        name = doc.get("name") or doc.get("filename")
        prior = by_hash.get(digest or "")
        if digest and name and prior and prior != name:
            out.append(_assert(
                "sha256_alias",
                f"{name} has sha256 {digest[:12]}… already stored as {prior}",
                source_doc=name, alias_of=prior, sha256=digest,
            ))
    return out


def yoy_move(live_totals: dict[tuple[str, int], float],
             proposed_totals: dict[tuple[str, int], float],
             threshold: float = LIVE_MOVE) -> list[dict]:
    """A 10% move vs the live published total, or a new FY 10% off the prior year.

    Historical YoY already on the chart is not re-litigated every week. This is
    the auto-merge gate: a quiet re-parse of the same years must not fire.
    """
    out = []
    for (state, fy), tons in proposed_totals.items():
        live = live_totals.get((state, fy))
        if live and tons and abs(tons / live - 1) > threshold:
            out.append(_assert(
                "live_move",
                f"{state} FY{fy} proposed {tons:,.0f} vs live {live:,.0f} "
                f"({(tons / live - 1) * 100:+.0f}%) — over {threshold:.0%} so not routine",
                state=state, fiscal_year=fy,
            ))
    states = {s for s, _ in proposed_totals}
    for state in states:
        years = sorted(fy for st, fy in proposed_totals if st == state)
        if len(years) < 2:
            continue
        latest = years[-1]
        if (state, latest) in live_totals:
            continue
        prior = years[-2]
        a = proposed_totals.get((state, prior))
        b = proposed_totals.get((state, latest))
        if a and b and abs(b / a - 1) > threshold:
            out.append(_assert(
                "yoy_move",
                f"{state} FY{latest} tons {b:,.0f} vs FY{prior} {a:,.0f} "
                f"({(b / a - 1) * 100:+.0f}%) — new year over {threshold:.0%}",
                state=state, fiscal_year=latest,
            ))
    return out


def contract_renumbering(new_numbers: set[str], known_numbers: set[str],
                         classifications: list[dict] | None = None) -> list[dict]:
    """New MiDEAL contract numbers are not auto-linked to the historical series."""
    out = []
    classifications = classifications or []
    by_no: dict[str, dict] = {}
    for c in classifications:
        for n in contract_numbers(c.get("filename") or "", c.get("url") or ""):
            by_no[n] = c
    for pair in MI_RENUMBER:
        src, dest = pair["from"], pair["to"]
        if dest in new_numbers and dest not in known_numbers:
            hit = by_no.get(dest) or {}
            out.append(_assert(
                "contract_renumber",
                f"MiDEAL {dest} ({pair['vendor']}) appeared; history has {src}. "
                f"Not auto-linked. Printed effective={hit.get('effective_date') or 'unknown'}, "
                f"expiration={hit.get('expiration_date') or 'unknown'}. "
                "Decide whether pricing carries forward and whether the series connects.",
                vendor=pair["vendor"], from_no=src, to_no=dest,
                effective_date=hit.get("effective_date"),
                expiration_date=hit.get("expiration_date"),
            ))
    leftover = new_numbers - known_numbers
    leftover -= {p["to"] for p in MI_RENUMBER}
    leftover -= {p["from"] for p in MI_RENUMBER}
    for n in sorted(leftover):
        if not n.startswith("180") and not n.startswith("260"):
            continue
        hit = by_no.get(n) or {}
        out.append(_assert(
            "contract_renumber",
            f"MiDEAL contract {n} is not in the known series. Not auto-linked. "
            f"Printed effective={hit.get('effective_date') or 'unknown'}, "
            f"expiration={hit.get('expiration_date') or 'unknown'}.",
            to_no=n,
            effective_date=hit.get("effective_date"),
            expiration_date=hit.get("expiration_date"),
        ))
    return out


def file_digest(path: str) -> str | None:
    if not path or not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def evaluate(classifications: list[dict], new_docs: list[dict],
             proposed_rows: list[dict],
             live_manifest: list[dict],
             live_totals: dict[tuple[str, int], float],
             proposed_totals: dict[tuple[str, int], float],
             prev_suppliers: dict[str, set[str]],
             now_suppliers: dict[str, set[str]],
             series_basis: dict[str, set[str]],
             known_contract_nos: set[str]) -> list[dict]:
    fired: list[dict] = []
    fired += empty_provenance(proposed_rows)
    fired += empty_provenance(new_docs)
    fired += tons_basis_mismatch(classifications, series_basis)
    fired += supplier_dropped(previous=prev_suppliers, current=now_suppliers)
    fired += dates_outside_fiscal_year(classifications)
    fired += sha256_alias(new_docs, live_manifest)
    fired += yoy_move(live_totals, proposed_totals)
    fired += column_drift(classifications)
    fired += undetermined_fields(classifications)
    new_nos: set[str] = set()
    for d in new_docs:
        new_nos |= contract_numbers(d.get("name") or "", d.get("url") or "")
    fired += contract_renumbering(new_nos, known_contract_nos, classifications)
    return fired
