"""Parser for Michigan DTMB / MiDEAL bulk road-salt contracts.

Michigan keeps one contract number per supplier for years at a time and appends a
new "Contract Change Notice" each season, so one PDF is a cumulative stack of
awards, newest first:

    CONTRACT CHANGE NOTICE 17            <- block cover
      Early MDOT Detroit 2025/2026 Road Salt           <- schedule
      MiDEAL and STATE AGENCY DROP POINTS: 2025/2026
      Seasonal MDOT Detroit 2025/2026 Road Salt
      MiDEAL and STATE AGENCY DROP POINTS: 2025/2026
    CONTRACT CHANGE NOTICE 16            <- previous notice, may be partial
      ...

Each schedule is keyed by (fiscal year, program, channel) where program is
"Early Fill" or "Seasonal Back-Up" and channel is MDOT or MiDEAL/State Agency.
Both programs are contracted volume and both are captured.

Three extraction hazards drive the design:

1. Numeric cells are glyph-split by the PDF producer ("2 00" for 200, "1 ,000"
   for 1000, "$ 6 5.92" for $65.92). Spaces are therefore stripped from numeric
   cells, and tonnage is recomputed as ``extended / price`` because the price and
   extended-total columns survive extraction more cleanly than tonnage.
2. Continuation pages repeat a table body without repeating its header, and
   column positions differ between the MDOT and MiDEAL layouts and between
   contract eras. A header-derived column map is therefore always validated
   against cell content, and re-inferred from content when it does not hold.
3. A change notice may amend only part of a season (e.g. CN16 restates only the
   Early Fill schedule). Superseding is therefore resolved per
   (fiscal year, program, channel) rather than per season.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import pdfplumber

from ..util import canonical_vendor, clean_county, season_to_fy, to_number, vendor_from_text


def detect_vendor(path: str) -> str | None:
    """Identify the contracted supplier from the document's cover page.

    Older MiDEAL schedules omit the per-row bidder column, so the contract's own
    cover page is the reliable source of the vendor for those rows.
    """
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages[:3]:
            found = vendor_from_text(page.extract_text() or "")
            if found:
                return found
    return None

BLOCK_START_RE = re.compile(r"CONTRACT\s+CHANGE\s+NOTICE|NOTICE\s+OF\s+CONTRACT", re.I)
CN_NUM_RE = re.compile(r"CHANGE\s+NOTICE\s+(?:NO\.?|NUMBER)\s*(\d+)", re.I)
CONTRACT_NO_RE = re.compile(r"CONTRACT\s+(?:NO\.?|NUMBER)\s*:?\s*(?:MA)?(\d{9,12})", re.I)

# A schedule title carries program and (usually) season on one line, e.g.
# "Early MDOT Detroit 2025/2026 Road Salt" or
# "MiDEAL and STATE AGENCY DROP POINTS: 2021/2022 SALT ORDER Seasonal Backup".
EARLY_RE = re.compile(r"\bearly\b", re.I)
SEASONAL_RE = re.compile(r"\bseasonal\b|back[\s-]?up|backfill", re.I)

# Plausibility bounds used to validate content-inferred columns.
PRICE_MIN, PRICE_MAX = 15.0, 500.0
TONS_MAX = 500_000.0


@dataclass
class Row:
    state: str
    fy: int | None
    vendor: str | None
    county: str | None
    program: str | None
    channel: str | None
    tons: float
    price: float
    extended: float
    entity: str | None
    page: int
    block: int
    change_notice: str | None
    contract_no: str | None
    source_doc: str
    tons_source: str


def _norm(cell) -> str:
    return re.sub(r"\s+", " ", str(cell or "")).strip()


# --------------------------------------------------------------------------
# Column detection
# --------------------------------------------------------------------------
def _header_map(header: list[str]) -> dict[str, int | None] | None:
    """Derive a column map from a header row, or None if it isn't a header."""
    cells = [_norm(c).lower() for c in header]
    joined = " | ".join(cells)
    if "price" not in joined and "$/ton" not in joined:
        return None
    if not any(k in joined for k in ("ton", "extended", "total", "delivery")):
        return None

    cols: dict[str, int | None] = dict.fromkeys(
        ("price", "extended", "tons", "vendor", "county", "entity"), None
    )
    for j, h in enumerate(cells):
        is_price = ("price" in h and "extended" not in h and "total" not in h) or "$/ton" in h
        if is_price and cols["price"] is None:
            cols["price"] = j
        if ("extended" in h or "total price" in h) and cols["extended"] is None:
            cols["extended"] = j
        if ("ton" in h or ("delivery" in h and "inside" not in h and "hour" not in h)) \
                and "extended" not in h and "$/ton" not in h and cols["tons"] is None:
            cols["tons"] = j
        if ("bidder" in h or "awarded vendor" in h or h == "vendor") and cols["vendor"] is None:
            cols["vendor"] = j
        if h == "county" and cols["county"] is None:
            cols["county"] = j
        if h in ("name", "drop point name", "org. name", "organization") and cols["entity"] is None:
            cols["entity"] = j
    if cols["price"] is None:
        return None
    return cols


def _channel_from_header(header: list[str]) -> str | None:
    joined = " | ".join(_norm(c).lower() for c in header)
    if "email" in joined or ("zip" in joined and "city" in joined):
        return "MiDEAL / State Agency"
    if "billing" in joined or "mdot region" in joined:
        return "MDOT"
    return None


def _program_from_header(header: list[str]) -> str | None:
    joined = " | ".join(_norm(c).lower() for c in header)
    early = bool(re.search(r"early", joined))
    seasonal = bool(re.search(r"seasonal", joined))
    if early and not seasonal:
        return "Early Fill"
    if seasonal and not early:
        return "Seasonal Back-Up"
    return None


def _score_map(rows: list[list], cols: dict[str, int | None]) -> int:
    """Count rows for which a column map yields a coherent price/extended pair."""
    ip, ie = cols.get("price"), cols.get("extended")
    if ip is None:
        return 0
    good = 0
    for r in rows:
        if ip >= len(r):
            continue
        price = to_number(r[ip])
        if price is None or not (PRICE_MIN <= price <= PRICE_MAX):
            continue
        if ie is not None and ie < len(r):
            ext = to_number(r[ie])
            if ext is None or ext < 0:
                continue
            if ext > 0 and not (0 < ext / price <= TONS_MAX):
                continue
        good += 1
    return good


def _infer_map(rows: list[list], width: int) -> dict[str, int | None] | None:
    """Infer price/extended/tons columns purely from cell content."""
    sample = [r for r in rows if len(r) == width][:40]
    if not sample:
        return None

    def frac_price(j: int) -> float:
        vals = [to_number(r[j]) for r in sample if j < len(r)]
        vals = [v for v in vals if v is not None]
        if not vals:
            return 0.0
        return sum(1 for v in vals if PRICE_MIN <= v <= PRICE_MAX) / len(sample)

    price_cands = sorted(range(width), key=lambda j: -frac_price(j))
    best, best_score = None, 0
    for ip in price_cands[:4]:
        if frac_price(ip) < 0.5:
            continue
        for ie in range(ip + 1, min(ip + 4, width)):
            cols = {"price": ip, "extended": ie, "tons": ip - 1 if ip else None,
                    "vendor": None, "county": None, "entity": None}
            s = _score_map(sample, cols)
            if s > best_score:
                best, best_score = cols, s
    if best and best_score >= max(3, 0.5 * len(sample)):
        return best
    return None


# --------------------------------------------------------------------------
# Page classification
# --------------------------------------------------------------------------
def _page_title_context(text: str) -> tuple[str | None, int | None]:
    """Read program and fiscal year from a page's title lines."""
    lines = [ln for ln in text.split("\n") if ln.strip()][:4]
    program = fy = None
    for ln in lines:
        if program is None:
            if SEASONAL_RE.search(ln):
                program = "Seasonal Back-Up"
            elif EARLY_RE.search(ln):
                program = "Early Fill"
        if fy is None:
            fy = season_to_fy(ln)
        if program and fy:
            break
    return program, fy


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------
def parse(
    path: str,
    default_vendor: str | None = None,
    source_doc: str | None = None,
    max_blocks: int | None = None,
) -> list[Row]:
    """Extract every drop-point pricing row from a Michigan salt contract PDF.

    ``max_blocks`` limits parsing to the first N change-notice blocks, which is
    useful for reading only the newest award without walking decades of history.
    """
    out: list[Row] = []
    doc = source_doc or path.split("/")[-1]

    block = -1
    cn: str | None = None
    contract_no: str | None = None
    block_fy: int | None = None

    carried_cols: dict[str, int | None] | None = None
    carried_prog: str | None = None
    carried_chan: str | None = None
    carried_fy: int | None = None

    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if not text.strip():
                continue

            head = text[:400]
            if BLOCK_START_RE.search(head):
                block += 1
                if max_blocks is not None and block >= max_blocks:
                    break
                m = CN_NUM_RE.search(text)
                cn = m.group(1) if m else cn
                m = CONTRACT_NO_RE.search(text)
                if m:
                    contract_no = m.group(1)
                block_fy = season_to_fy(text)
                carried_cols = carried_prog = carried_chan = None
                carried_fy = None
                continue

            # A cover page's DESCRIPTION continuation may name the season.
            if block_fy is None:
                block_fy = season_to_fy(text[:400])

            page_prog, page_fy = _page_title_context(text)
            if page_prog:
                carried_prog = page_prog
            if page_fy:
                carried_fy = page_fy

            for table in page.extract_tables():
                if not table or len(table) < 2:
                    continue
                width = max(len(r) for r in table)

                cols = None
                prog = carried_prog
                chan = carried_chan
                start = 0

                # Prefer an explicit header row when this table has one.
                for hi in range(min(3, len(table))):
                    hm = _header_map(table[hi])
                    if hm:
                        cols, start = hm, hi + 1
                        chan = _channel_from_header(table[hi]) or chan
                        prog = _program_from_header(table[hi]) or prog
                        break

                body = table[start:]
                # Validate; fall back to the carried map, then to content inference.
                if not cols or _score_map(body, cols) < max(2, 0.4 * len(body)):
                    alt = None
                    if carried_cols and _score_map(body, carried_cols) >= max(2, 0.4 * len(body)):
                        alt = carried_cols
                    else:
                        alt = _infer_map(body, width)
                    if alt:
                        cols, start = alt, start if cols else 0
                        body = table[start:]
                    elif not cols:
                        continue

                if cols.get("price") is None:
                    continue

                carried_cols = cols
                if chan:
                    carried_chan = chan
                if prog:
                    carried_prog = prog

                fy = carried_fy or block_fy
                idxs = [v for v in cols.values() if v is not None]
                max_idx = max(idxs) if idxs else 0

                for raw in body:
                    if max_idx >= len(raw):
                        continue
                    price = to_number(raw[cols["price"]])
                    if price is None or not (PRICE_MIN <= price <= PRICE_MAX):
                        continue

                    ie = cols.get("extended")
                    ext = to_number(raw[ie]) if ie is not None and ie < len(raw) else None
                    it = cols.get("tons")
                    printed = to_number(raw[it]) if it is not None and it < len(raw) else None

                    if ext is not None and ext > 0:
                        tons = ext / price
                        tsrc = "derived"
                    elif printed and 0 < printed <= TONS_MAX:
                        tons = printed
                        ext = printed * price
                        tsrc = "printed"
                    else:
                        continue  # zero-commitment drop point

                    if tons <= 0 or tons > TONS_MAX:
                        continue

                    vendor = None
                    iv = cols.get("vendor")
                    if iv is not None and iv < len(raw):
                        vendor = canonical_vendor(_norm(raw[iv]))
                    vendor = vendor or default_vendor

                    ic = cols.get("county")
                    county = clean_county(_norm(raw[ic])) if ic is not None and ic < len(raw) else None
                    ien = cols.get("entity")
                    entity = _norm(raw[ien]) if ien is not None and ien < len(raw) else None

                    out.append(Row(
                        state="MI", fy=fy, vendor=vendor, county=county,
                        program=prog, channel=chan, tons=round(tons, 4),
                        price=price, extended=round(ext, 2), entity=entity or None,
                        page=pno, block=max(block, 0), change_notice=cn,
                        contract_no=contract_no, source_doc=doc, tons_source=tsrc,
                    ))
    return out


def select_current(rows: list[Row]) -> list[Row]:
    """Drop superseded schedules.

    A change notice may restate only part of a season, so the newest notice is
    chosen independently for each (fiscal year, program, channel) schedule.
    Change-notice numbers increase over time; blocks appear newest-first.
    """
    def cn_key(r: Row) -> tuple[int, int]:
        try:
            return (1, int(r.change_notice)) if r.change_notice else (0, -r.block)
        except (TypeError, ValueError):
            return (0, -r.block)

    best: dict[tuple, tuple] = {}
    for r in rows:
        if r.fy is None:
            continue
        key = (r.fy, r.program, r.channel)
        k = cn_key(r)
        if key not in best or k > best[key]:
            best[key] = k
    return [r for r in rows if r.fy is not None and cn_key(r) == best.get((r.fy, r.program, r.channel))]
