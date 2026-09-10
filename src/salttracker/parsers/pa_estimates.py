"""Parser for Pennsylvania's county-level salt demand estimates.

Pennsylvania's COSTARS packet publishes an awarded price per county but no
tonnage, so volume has to come from the "Estimates" attachment that DGS issues
with each salt solicitation. That attachment lists, per county, the tonnage
committed by PennDOT, by COSTARS members, and by non-PennDOT state agencies.

The column layout is not stable between seasons: some years break PennDOT and
COSTARS into initial-fill/balance-of-season pairs and some print only totals,
and the district and lot columns swap order. What is stable is that the first
number after a county name is the cumulative total for that county, and that
the remaining numbers end with the state-agency figure. Parsing keys off those
two invariants and then verifies that the parts sum back to the cumulative.
"""
from __future__ import annotations

import os
import re

import pandas as pd
import pdfplumber

from ..util import SEASON_RE, clean_county, season_to_fy, to_number

# Counties are matched against the known roster because the tables also contain
# district subtotals and free-text notes that otherwise look like data rows.
PA_COUNTIES = {
    "adams", "allegheny", "armstrong", "beaver", "bedford", "berks", "blair",
    "bradford", "bucks", "butler", "cambria", "cameron", "carbon", "centre",
    "chester", "clarion", "clearfield", "clinton", "columbia", "crawford",
    "cumberland", "dauphin", "delaware", "elk", "erie", "fayette", "forest",
    "franklin", "fulton", "greene", "huntingdon", "indiana", "jefferson",
    "juniata", "lackawanna", "lancaster", "lawrence", "lebanon", "lehigh",
    "luzerne", "lycoming", "mckean", "mercer", "mifflin", "monroe",
    "montgomery", "montour", "northampton", "northumberland", "perry",
    "philadelphia", "pike", "potter", "schuylkill", "snyder", "somerset",
    "sullivan", "susquehanna", "tioga", "union", "venango", "warren",
    "washington", "wayne", "westmoreland", "wyoming", "york",
}

_COUNTY_ALIASES = {"mc kean": "mckean", "mc-kean": "mckean"}


def _norm_county(text: str | None) -> str | None:
    if not text:
        return None
    key = re.sub(r"[^a-z ]+", "", str(text).strip().lower()).strip()
    key = _COUNTY_ALIASES.get(key, key)
    return key if key in PA_COUNTIES else None


def _split_parts(nums: list[float]) -> tuple[float | None, float | None, float | None]:
    """Map the numbers trailing the cumulative onto PennDOT/COSTARS/agency.

    Seasons differ in whether the initial-fill and balance-of-season columns are
    printed alongside each total, so the layout is identified by how many
    numbers follow the cumulative rather than by reading the header.
    """
    if len(nums) >= 7:      # penndot initial, balance, total; costars x3; agency
        return nums[2], nums[5], nums[6]
    if len(nums) == 6:      # same, with the agency column blank
        return nums[2], nums[5], 0.0
    if len(nums) == 5:      # penndot initial, balance, total; costars total; agency
        return nums[2], nums[3], nums[4]
    if len(nums) == 4:
        return nums[2], nums[3], 0.0
    if len(nums) == 3:      # totals only
        return nums[0], nums[1], nums[2]
    return None, None, None


def _fy_from_text(text: str) -> int | None:
    m = SEASON_RE.search(text or "")
    if m:
        return season_to_fy(m.group(0))
    m2 = re.search(r"FY\s*(20\d{2})", text or "", re.I)
    return int(m2.group(1)) if m2 else None


def _row_from_numbers(county: str, nums: list[float], fy: int, source: str,
                      page: int | None) -> dict | None:
    if not nums:
        return None
    cumulative = nums[0]
    penndot, costars, agency = _split_parts(nums[1:])
    parts = [p for p in (penndot, costars, agency) if p is not None]
    # A mis-read column would break the identity below; when that happens the
    # cumulative is still trustworthy but the split is not, so it is dropped.
    if len(parts) == 3 and abs(sum(parts) - cumulative) > max(2.0, cumulative * 0.01):
        penndot = costars = agency = None
    return {
        "state": "PA",
        "fiscal_year": fy,
        "county": clean_county(county.title()),
        "cumulative_tons": cumulative,
        "penndot_tons": penndot,
        "costars_tons": costars,
        "agency_tons": agency,
        "source_doc": os.path.basename(source),
        "source_page": page,
    }


_SUMMARY_HEADER_RE = re.compile(r"cumulative", re.I)
_TOTAL_ROW_RE = re.compile(r"^\s*TOTALS?\b", re.I)


def _numbers(text: str) -> list[float]:
    return [n for n in (to_number(x) for x in
                        re.findall(r"-?[\d,]+(?:\.\d+)?", text)) if n is not None]


def parse_pdf(path: str) -> tuple[pd.DataFrame, float | None]:
    """Read the county summary table and the total it reports.

    Only the leading summary table is wanted. The same file goes on to list every
    participating COSTARS member and state agency by county, and those rosters
    would otherwise be read as additional county rows. The summary is entered at
    its header and left at its TOTAL line, which is also returned so the caller
    can check the extraction against the document's own arithmetic.
    """
    rows: list[dict] = []
    doc_fy: int | None = None
    stated_total: float | None = None
    in_summary = False

    with pdfplumber.open(path) as pdf:
        for pageno, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            page_fy = _fy_from_text(text)
            if page_fy:
                doc_fy = page_fy
            if _SUMMARY_HEADER_RE.search(text):
                in_summary = True
            if not in_summary or not doc_fy:
                continue

            for line in text.splitlines():
                # The wrapped column headings include a bare "Total Quantity"
                # line, so the closing totals row is recognised by its figures.
                if _TOTAL_ROW_RE.match(line) and len(_numbers(line)) >= 3:
                    stated_total = _numbers(line)[0]
                    in_summary = False
                    break
                county = tail = None
                for token in re.finditer(r"[A-Za-z][A-Za-z .'-]{2,25}", line):
                    if _norm_county(token.group(0)):
                        county, tail = token.group(0), line[token.end():]
                        break
                if county is None:
                    continue
                row = _row_from_numbers(county, _numbers(tail), doc_fy, path, pageno)
                if row:
                    rows.append(row)
            if not in_summary:
                break
    return pd.DataFrame(rows), stated_total


def parse_xlsx(path: str) -> tuple[pd.DataFrame, float | None]:
    rows: list[dict] = []
    stated_total: float | None = None
    book = pd.ExcelFile(path)
    sheet = "Cumulative" if "Cumulative" in book.sheet_names else book.sheet_names[0]
    frame = book.parse(sheet, header=None)

    blob = " ".join(str(v) for v in frame.head(4).to_numpy().ravel() if pd.notna(v))
    fy = _fy_from_text(blob) or _fy_from_text(os.path.basename(path))
    if not fy:
        return pd.DataFrame(rows), None

    for _, record in frame.iterrows():
        values = list(record)
        labels = " ".join(str(v) for v in values[:3] if pd.notna(v))
        if _TOTAL_ROW_RE.match(labels.strip()):
            nums = [n for n in (to_number(v) for v in values) if n is not None]
            if nums:
                stated_total = nums[0]
            break
        idx = next((i for i, v in enumerate(values) if _norm_county(v)), None)
        if idx is None:
            continue
        nums = [n for n in (to_number(v) for v in values[idx + 1:]) if n is not None]
        row = _row_from_numbers(str(values[idx]), nums, fy, path, None)
        if row:
            rows.append(row)
    return pd.DataFrame(rows), stated_total


def parse(path: str) -> pd.DataFrame:
    """Parse one estimates attachment, deduplicating counties within a season."""
    if path.lower().endswith((".xlsx", ".xls")):
        frame, stated_total = parse_xlsx(path)
    else:
        frame, stated_total = parse_pdf(path)
    if frame.empty:
        return frame
    frame = frame.drop_duplicates(subset=["fiscal_year", "county"]).reset_index(drop=True)
    frame.attrs["stated_total"] = stated_total
    return frame


_PA_COUNTY_CELL = re.compile(r"^PA-([A-Za-z]+)$")
_MEMBER_SKIP = re.compile(
    r"^(organization name|member id|state-county|participants?:|stockpile|county)\b",
    re.I,
)
_AGENCY_SECTION_RE = re.compile(r"Non-PennDOT State Agency", re.I)
_COSTARS_MEMBER_RE = re.compile(
    r"(COSTARS\s+20\d{2}[-–]\d{2}\s+Salt Estimates|Organization Name)", re.I)


def _member_from_cells(cells: list[str], fy: int | None, source: str,
                       page: int | None) -> dict | None:
    if not fy:
        return None
    county_idx = next(
        (i for i, c in enumerate(cells) if _PA_COUNTY_CELL.fullmatch(c)), None)
    county = None
    name_idx = 0
    if county_idx is not None and county_idx > 0:
        county = clean_county(_PA_COUNTY_CELL.fullmatch(cells[county_idx]).group(1))
        name = cells[0]
        nums = [to_number(c) for c in cells[county_idx + 1:]]
        category = None
        if county_idx >= 2 and cells[1] and not re.fullmatch(r"\d+", cells[1]):
            category = cells[1]
    else:
        # LPPU-style: County | Organization Name | ... | Total Tons
        idx = next((i for i, c in enumerate(cells) if _norm_county(c)), None)
        if idx is None:
            return None
        county = clean_county(str(cells[idx]).title())
        if idx + 1 >= len(cells):
            return None
        name = cells[idx + 1]
        nums = [to_number(c) for c in cells[idx + 2:]]
        category = None
    nums = [n for n in nums if n is not None]
    if not county or not name or _MEMBER_SKIP.match(name) or not nums:
        return None
    if _norm_county(name):
        return None
    return {
        "fiscal_year": fy,
        "county": county,
        "purchasing_entity": name,
        "member_category": category,
        "contracted_tons": nums[-1],
        "source_doc": os.path.basename(source),
        "source_page": page,
    }


def parse_members_pdf(path: str) -> pd.DataFrame:
    """COSTARS member roster from an estimates PDF, when tables keep one row per member."""
    rows: list[dict] = []
    fy: int | None = None
    in_members = False
    with pdfplumber.open(path) as pdf:
        for pageno, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            page_fy = _fy_from_text(text)
            if page_fy:
                fy = page_fy
            if _AGENCY_SECTION_RE.search(text):
                break
            if _COSTARS_MEMBER_RE.search(text) and "Organization Name" in (text or ""):
                in_members = True
            if not in_members:
                continue
            for table in page.extract_tables() or []:
                for raw in table or []:
                    cells = [re.sub(r"\s+", " ", str(c or "")).strip() for c in raw]
                    row = _member_from_cells(cells, fy, path, pageno)
                    if row:
                        rows.append(row)
    return pd.DataFrame(rows)


def parse_members_xlsx(path: str) -> pd.DataFrame:
    book = pd.ExcelFile(path)
    if "LPPU" not in book.sheet_names:
        return pd.DataFrame()
    frame = book.parse("LPPU", header=None)
    blob = " ".join(str(v) for v in frame.head(3).to_numpy().ravel() if pd.notna(v))
    fy = _fy_from_text(blob) or _fy_from_text(os.path.basename(path))
    rows: list[dict] = []
    for _, record in frame.iterrows():
        cells = [re.sub(r"\s+", " ", str(v)).strip() if pd.notna(v) else ""
                 for v in record]
        row = _member_from_cells(cells, fy, path, None)
        if row:
            rows.append(row)
    return pd.DataFrame(rows)


def parse_members(path: str) -> pd.DataFrame:
    """Named COSTARS members from an estimates attachment. Empty when tables are mashed."""
    if path.lower().endswith((".xlsx", ".xls")):
        frame = parse_members_xlsx(path)
    else:
        frame = parse_members_pdf(path)
    if frame.empty:
        return frame
    return frame.drop_duplicates(
        subset=["fiscal_year", "county", "purchasing_entity"]
    ).reset_index(drop=True)
