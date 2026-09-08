"""Parser for Pennsylvania DGS statewide sodium chloride (bulk road salt) contracts.

PennDOT buys road salt through a single DGS statewide contract that is awarded
county-by-county to the lowest bidder, so each season's COSTARS contract packet
contains one pricing block per supplier:

    4600016537 - American Rock Salt
    County        Cumulative Estimate   County Bid Price   [Initial Fill Discounted Price]
    Allegheny     99,740                $91.44             $86.87
    ...

"Cumulative Estimate" is PennDOT's contracted tonnage estimate for that county
and "County Bid Price" is the awarded delivered price per ton, which together
give contracted volume and price at county grain.

Packets also carry an "Attachment A" renewal table that restates the prior
season's price beside the new one:

    County      Current Pricing 2024-25    New Pricing 2025-26

Those rows have no tonnage, so they are emitted as price-only observations used
to backfill and cross-check earlier fiscal years rather than as volume.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import pdfplumber

from ..util import canonical_vendor, clean_county, season_to_fy, to_number

# "4600016537 - American Rock Salt" (supplier names are often truncated by the
# PDF's column width, so the longest observed spelling per contract number wins).
VENDOR_HDR_RE = re.compile(r"(4600\d{6})\s*[-\u2010\u2013]\s*([A-Za-z][A-Za-z ,.&'\-]*)")
PRICE_COL_RE = re.compile(r"(current|new)\s+pricing", re.I)
MEMBER_SECTION_RE = re.compile(r"Participating\s+Members", re.I)

PRICE_MIN, PRICE_MAX = 20.0, 400.0
TONS_MAX = 1_000_000.0


@dataclass
class Row:
    state: str
    fy: int | None
    vendor: str | None
    county: str | None
    tons: float | None
    price: float | None
    price_costars: float | None
    contract_no: str | None
    record_type: str  # "award" (volume + price) or "price_only"
    page: int
    source_doc: str


def _norm(cell) -> str:
    return re.sub(r"\s+", " ", str(cell or "")).strip()


def _compact(row: list) -> list[str]:
    """Drop empty cells so interleaved blank columns don't shift positions."""
    return [_norm(c) for c in row if _norm(c)]


ATTACHMENT_BANNER_RE = re.compile(r"^\s*attachment\s+a\b", re.I)


def _attachment_supplier(text: str) -> tuple[str, str] | None:
    """Read the supplier named in an 'Attachment A' page banner.

    Returns the SAP contract number and the supplier name printed beneath the
    heading, or None when the page is not an Attachment A pricing page.
    """
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines or not ATTACHMENT_BANNER_RE.match(lines[0]):
        return None
    name: str | None = None
    for line in lines[1:6]:
        cno = re.fullmatch(r"(4600\d{6})", line)
        if cno and name:
            return cno.group(1), name
        if canonical_vendor(line) and not to_number(line):
            name = line
    return None


def _vendor_map(pdf) -> dict[str, str]:
    """Map contract number -> longest supplier name seen anywhere in the packet."""
    best: dict[str, str] = {}
    for page in pdf.pages:
        text = page.extract_text() or ""
        # Join wrapped supplier names so "American Rock\nSalt" is seen intact.
        flat = re.sub(r"\s*\n\s*", " ", text)
        for cno, name in VENDOR_HDR_RE.findall(flat):
            name = _norm(name)
            if len(name) > len(best.get(cno, "")):
                best[cno] = name
    return best


def _is_county_pricing_header(cells: list[str]) -> bool:
    joined = " | ".join(c.lower() for c in cells)
    return "county" in joined and ("estimate" in joined or "bid price" in joined or "pricing" in joined)


def parse(path: str, source_doc: str | None = None) -> list[Row]:
    """Extract county-level award and price-only rows from a PA salt contract packet."""
    doc = source_doc or path.split("/")[-1]
    out: list[Row] = []

    with pdfplumber.open(path) as pdf:
        vendors = _vendor_map(pdf)
        first_text = (pdf.pages[0].extract_text() or "") + " " + (pdf.pages[min(4, len(pdf.pages) - 1)].extract_text() or "")
        doc_fy = season_to_fy(first_text) or season_to_fy(doc)

        current_cno: str | None = None
        # A supplier's county list often spills onto the next page as a table
        # with no repeated header, so once the pricing section has started the
        # header requirement is satisfied for the rest of the section.
        pricing_started = False
        pending_name: str | None = None

        for pno, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            page_fy = season_to_fy(text) or doc_fy

            # The packet ends with a roster of participating COSTARS members whose
            # per-entity tonnage columns look like pricing rows. Everything from
            # that heading onward is not contract pricing.
            if MEMBER_SECTION_RE.search(text):
                break

            # Each Attachment A page is a single supplier's county list, and it
            # names that supplier in a banner above the table rather than in a
            # table row. Without re-reading the banner the previous page's
            # supplier would be carried over and counties would be credited to
            # the wrong company.
            banner = _attachment_supplier(text)
            if banner:
                current_cno, banner_name = banner
                if len(vendors.get(current_cno, "")) < len(banner_name):
                    vendors[current_cno] = banner_name

            for table in page.extract_tables():
                if not table:
                    continue

                table_cno = current_cno
                attach_fys: list[int | None] = []
                header_seen = False

                for raw in table:
                    cells = _compact(raw)
                    if not cells:
                        continue
                    joined = " | ".join(cells)

                    # Supplier section heading, either "4600016537 - Name" or a
                    # bare contract number directly under the supplier's name.
                    m = VENDOR_HDR_RE.search(joined)
                    if m and len(cells) <= 3:
                        table_cno = m.group(1)
                        current_cno = table_cno
                        if len(vendors.get(table_cno, "")) < len(_norm(m.group(2))):
                            vendors[table_cno] = _norm(m.group(2))
                        continue
                    bare = re.fullmatch(r"(4600\d{6})", cells[0])
                    if bare and len(cells) <= 2:
                        table_cno = bare.group(1)
                        current_cno = table_cno
                        if pending_name and len(vendors.get(table_cno, "")) < len(pending_name):
                            vendors[table_cno] = pending_name
                        continue
                    if len(cells) <= 2 and canonical_vendor(cells[0]) and not to_number(cells[0]):
                        pending_name = cells[0]
                        continue

                    # Attachment A header carries an explicit fiscal year per column.
                    if PRICE_COL_RE.search(joined) and "county" in joined.lower():
                        attach_fys = [season_to_fy(c) for c in cells[1:]]
                        continue

                    if _is_county_pricing_header(cells):
                        attach_fys = []
                        header_seen = True
                        pricing_started = True
                        continue

                    county = clean_county(cells[0])
                    if not county:
                        continue

                    nums = [to_number(c) for c in cells[1:]]
                    prices = [n for n in nums if n is not None and PRICE_MIN <= n <= PRICE_MAX]
                    vendor = canonical_vendor(vendors.get(table_cno or "", "")) if table_cno else None

                    # Attachment A: price per fiscal year, no tonnage.
                    if attach_fys and len(prices) >= 1:
                        for fy, val in zip(attach_fys, nums):
                            if fy and val is not None and PRICE_MIN <= val <= PRICE_MAX:
                                out.append(Row(
                                    state="PA", fy=fy, vendor=vendor, county=county,
                                    tons=None, price=val, price_costars=None,
                                    contract_no=table_cno, record_type="price_only",
                                    page=pno, source_doc=doc,
                                ))
                        continue

                    # Only trust award rows once the pricing section has begun.
                    if not (header_seen or pricing_started):
                        continue

                    # County award row: tonnage then bid price (then COSTARS price).
                    tons = None
                    for n in nums:
                        if n is not None and n > PRICE_MAX and n <= TONS_MAX:
                            tons = n
                            break
                    if tons is None or not prices:
                        continue

                    out.append(Row(
                        state="PA", fy=page_fy, vendor=vendor, county=county,
                        tons=tons, price=prices[0],
                        price_costars=prices[1] if len(prices) > 1 else None,
                        contract_no=table_cno, record_type="award",
                        page=pno, source_doc=doc,
                    ))
    return out
