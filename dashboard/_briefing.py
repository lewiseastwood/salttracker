"""Watch-strip copy and the coverage grid — shared by Streamlit and the HTML pack."""
from __future__ import annotations

import html
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import pandas as pd

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from _theme import STATE_NAMES

# Public GitHub repo for change-report links. Streamlit Cloud has no usable
# `git` binary, so the dashboard must not call git at request time.
GITHUB_REPO = "lewiseastwood/salttracker"

PA_VOLUME_NOTE = (
    "Pennsylvania tons are estimated requirements committed before the season, "
    "not purchased or delivered."
)
PA_TONS_BASIS_NOTE = (
    "Pennsylvania FY2024 tons are the COSTARS packet's Cumulative Estimate column "
    "(tons_basis=printed). FY2022, FY2023, FY2026 and FY2027 tons are from the "
    "eMarketplace estimates attachment (tons_basis=committed_estimate). Those are "
    "different documents. FY2025 has no published tons."
)
UNLIKE_SHARE_NOTE = (
    "Michigan and Pennsylvania are not combined into a share: those are unlike "
    "quantities. Pennsylvania is estimated lot requirements; Michigan is contracted "
    "drop-point awards."
)
MI_CAPTURE_NOTE = (
    "Michigan prices are whichever change notice Wayback captured for that "
    "contract, not a complete CN history. FY2025 comes from a mid-season "
    "amendment with corrected pricing. Other seasons come from option-year "
    "exercises that may predate any correction, so Michigan price accuracy "
    "varies by year with crawl timing."
)
MI_CAPTURE_NOTE_SHORT = (
    "FY2025 prices are post-amendment. FY2022–FY2024, FY2026 and FY2027 are "
    "award-time figures that may predate later corrections."
)

STATE_VOLUME_MEASURE = {
    "MI": "contracted drop-point tons",
    "PA": "estimated lot requirements",
}


def volume_label(df: pd.DataFrame) -> str:
    """What the tons column is, given which states are in view."""
    if df is None or df.empty or "state" not in df.columns:
        return "Published tons"
    states = {str(s) for s in df["state"].dropna().unique()}
    if states == {"PA"}:
        return "Estimated requirements"
    if states == {"MI"}:
        return "Contracted tons"
    return "Published tons"


def pa_basis_note(df: pd.DataFrame) -> str | None:
    """On-chart copy when a PA view mixes packet-printed tons with estimates."""
    if df is None or df.empty or "state" not in df.columns:
        return None
    if "PA" not in set(df["state"].astype(str)):
        return None
    return PA_TONS_BASIS_NOTE


def _as_url(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    text = str(value).strip()
    if not text or text.lower() in ("nan", "none"):
        return None
    return text


def file_markdown_link(name: str | None, url: str | None) -> str:
    """Filename (or label) as a markdown link when a published URL exists."""
    text = str(name or "").strip()
    if not text:
        return ""
    href = _as_url(url)
    if not href:
        return text
    safe = text.replace("[", "\\[").replace("]", "\\]")
    return f"[{safe}]({href})"


def clickable_url(url: str | None) -> str | None:
    """Make a Wayback href that Streamlit's HTML sanitizer will keep.

    Identity URLs are ``…/web/{timestamp}id_/https://…`` (no slash before
    ``id_``). Streamlit drops nested-scheme hrefs, which is why Michigan
    snaps rendered as "—".
    """
    href = _as_url(url)
    if not href:
        return None
    href = re.sub(r"(https://web\.archive\.org/web/\d+)id_/", r"\1/", href)
    m = re.match(r"^(https://web\.archive\.org/web/\d+/)(https://.+)$", href)
    if m and "%3A" not in m.group(2):
        return m.group(1) + quote(m.group(2), safe="")
    return href


# Viewer URLs (no id_) plus labels so the table does not depend on salttracker.sources.
_MI_SALT_LISTING = (
    "https://www.michigan.gov/dtmb/procurement/mideal-extended-purchasing-program"
    "/mideal-contract-search/categories/folder-2/salt-bulk-rock"
)
_MI_SNAPS = {
    "768_snap2023-01.pdf": (
        "20230127073006", "006/180000000768",
        "MA180000000768 CN13 · option year 2023/2024 (effective 20 Jun 2023)",
    ),
    "787_snap2023-01.pdf": (
        "20230127073006", "004/180000000787",
        "MA180000000787 CN9 · option year 2023/2024 (effective 20 Jun 2023)",
    ),
    "791_snap2023-01.pdf": (
        "20230127073006", "004/180000000791",
        "MA180000000791 CN3 · 2021/2022 pricing (effective 1 Sep 2021; expires 31 Aug 2023)",
    ),
    "768_snap2025-04.pdf": (
        "20250418052020", "006/180000000768",
        "MA180000000768 CN16 · mid-season amendment of 2024/2025 (effective 23 Sep 2024)",
    ),
    "787_snap2025-04.pdf": (
        "20250418052020", "004/180000000787",
        "MA180000000787 CN12 · mid-season amendment of 2024/2025 (effective 16 Sep 2024)",
    ),
    "768_snap2026-05.pdf": (
        "20260520035130", "006/180000000768",
        "MA180000000768 CN17 · option year 2025/2026 (effective 29 Jul 2025)",
    ),
    "787_snap2026-05.pdf": (
        "20260520035130", "004/180000000787",
        "MA180000000787 CN13 · option year 2025/2026 (effective 29 Jul 2025)",
    ),
}


def _snap_pdf_page(name: str) -> tuple[str | None, str | None, str | None]:
    rec = _MI_SNAPS.get(str(name) or "")
    if not rec:
        return None, None, None
    ts, media, label = rec
    original = (
        "https://www.michigan.gov/dtmb/-/media/Project/Websites/dtmb/"
        f"Procurement/Contracts/MiDEAL-Media/{media}.pdf"
    )
    pdf = f"https://web.archive.org/web/{ts}/{original}"
    page = f"https://web.archive.org/web/{ts}/{_MI_SALT_LISTING}"
    return pdf, page, label


def provenance_urls(name: str) -> tuple[str | None, str | None]:
    """Wayback file + listing page for a Michigan snap alias."""
    pdf, page, _ = _snap_pdf_page(name)
    if pdf:
        return pdf, page
    try:
        from salttracker.sources import known_local_provenance
    except ImportError:
        return None, None
    rec = known_local_provenance().get(str(name) or "") or {}
    return clickable_url(rec.get("url")), _as_url(rec.get("page_url"))


_DOC_LABELS = {
    "PA_FY2024_COSTARS.pdf": "COSTARS FY2024 season contract",
    "PA_FY2025_COSTARS.pdf": "COSTARS FY2025 season contract",
    "PA_FY2026_COSTARS_6100053321.pdf": "COSTARS FY2026 season contract",
    "PA_FY2027_COSTARS_6100065611.pdf": "COSTARS FY2027 season contract",
    "PA_estimates_FY2022_6100053321.xlsx": "FY2022 estimated requirements",
    "PA_estimates_FY2023_6100056192.pdf": "FY2023 estimated requirements",
    "PA_estimates_FY2027_6100065611.pdf": "FY2027 estimated requirements",
    "MI_FY2027_CompassMinerals_260000000713.pdf": "Compass Minerals FY2027",
    "MI_FY2027_DetroitSalt_260000000712.pdf": "Detroit Salt FY2027",
}


def document_label(name: str) -> str:
    _, _, label = _snap_pdf_page(name)
    if label:
        return label
    known = _DOC_LABELS.get(str(name) or "")
    if known:
        return known
    try:
        from salttracker.sources import snap_document_label
        return snap_document_label(name)
    except ImportError:
        stem = str(name or "")
        for ext in (".pdf", ".xlsx", ".xls"):
            if stem.lower().endswith(ext):
                stem = stem[: -len(ext)]
                break
        return stem.replace("_", " ") if stem else str(name or "")


def _state_display(code: str) -> str:
    text = str(code or "").strip()
    if not text:
        return ""
    if ", " in text:
        return ", ".join(STATE_NAMES.get(p.strip(), p.strip()) for p in text.split(","))
    return STATE_NAMES.get(text, text) if len(text) == 2 else text


def years_label(fy_from, fy_to) -> str:
    try:
        a = int(fy_from)
    except (TypeError, ValueError):
        a = None
    try:
        b = int(fy_to)
    except (TypeError, ValueError):
        b = None
    if a and b and a != b:
        return f"FY{a}–FY{b}"
    if a:
        return f"FY{a}"
    if b:
        return f"FY{b}"
    return "—"


def source_table_for_export(catalog: pd.DataFrame) -> pd.DataFrame:
    """Plain columns for CSV/Excel — no markdown."""
    rows = []
    for _, row in catalog.iterrows():
        fname = str(row.get("source_doc") or "").strip()
        url = clickable_url(_as_url(row.get("source_url")))
        page = clickable_url(_as_url(row.get("source_page_url")))
        snap_pdf, snap_page, _ = _snap_pdf_page(fname)
        rows.append({
            "State": _state_display(row.get("state")),
            "Contract": document_label(fname) if fname else (row.get("source_label") or ""),
            "Filename": fname,
            "Years": years_label(row.get("fiscal_year_from"), row.get("fiscal_year_to")),
            "Supplier": row.get("suppliers") or "",
            "PDF URL": url or snap_pdf or "",
            "Listing URL": page or snap_page or "",
        })
    return pd.DataFrame(rows)


def _html_link(text: str, url: str | None, hover: str | None = None) -> str:
    """Anchor whose tooltip is the clean name, not the URL."""
    label = html.escape(text)
    if not url:
        return label
    tip = html.escape(hover or text)
    href = html.escape(url, quote=True)
    return (
        f'<a href="{href}" title="{tip}" target="_blank" rel="noopener">{label}</a>'
    )


def executive_source_html(catalog: pd.DataFrame) -> str:
    """HTML table: hover shows the contract name, not the file URL."""
    body = []
    for _, row in catalog.iterrows():
        fname = str(row.get("source_doc") or "").strip()
        snap_pdf, snap_page, _ = _snap_pdf_page(fname)
        url = clickable_url(_as_url(row.get("source_url"))) or clickable_url(snap_pdf)
        page = clickable_url(_as_url(row.get("source_page_url"))) or clickable_url(snap_page)
        label = document_label(fname) if fname else str(row.get("source_label") or "").strip()
        years = years_label(row.get("fiscal_year_from"), row.get("fiscal_year_to"))
        supplier = html.escape(row.get("suppliers") or "—")
        body.append(
            "<tr>"
            f"<td>{html.escape(_state_display(row.get('state')))}</td>"
            f"<td>{_html_link(label, url, hover=label)}</td>"
            f"<td>{html.escape(years)}</td>"
            f"<td>{supplier}</td>"
            f"<td>{_html_link('Open', url, hover=label) if url else '—'}</td>"
            f"<td>{_html_link('Open', page, hover=label) if page else '—'}</td>"
            "</tr>"
        )
    return (
        '<table class="src"><thead><tr>'
        "<th>State</th><th>Contract</th><th>Years</th><th>Supplier</th>"
        "<th>PDF</th><th>Listing</th>"
        "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )

# Same window as the weekly Action: a missed Monday is an outage after 10 days.
STALE_AFTER = timedelta(days=10)

# Detector kinds shown on the watch strip. Failures are included so a
# zero-row parse or empty DTMB listing cannot read as a quiet week.
DETECTOR_KINDS = (
    "new-season", "new-supplier", "new-document",
    "parse-failed", "listing-empty", "listing-unfetched", "download-failed",
)
FAILED_STATUSES = frozenset({"empty", "error", "parse-failed", "discovery-failed"})


def load_jsonl(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    rows = []
    with p.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def load_watch(state_path: str | Path) -> dict:
    p = Path(state_path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def git_head(root: str | Path | None = None) -> str | None:
    """Local helper. Returns None when git is missing (Streamlit Cloud)."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root) if root else None,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip() or None
    except Exception:
        return None


def change_report_href(watch: dict | None = None, root: str | Path | None = None) -> str:
    """HTTPS blob URL. Does not call git — Cloud has no repo remote config."""
    del root  # kept so existing callers can still pass ROOT
    path = (watch or {}).get("change_report_path") or "data/output/CHANGE_REPORT.txt"
    return f"https://github.com/{GITHUB_REPO}/blob/main/{path}"


def revision_caption(watch: dict | None = None, root: str | Path | None = None) -> tuple[str | None, str]:
    """Commit id and change-report URL. Never raises; never calls git."""
    del root
    state = watch or {}
    return state.get("data_commit"), change_report_href(state)


def revision_line(watch: dict, root: str | Path | None = None) -> str:
    sha, href = revision_caption(watch, root)
    bits = []
    if sha:
        bits.append(f"Data commit {sha[:7]}")
    if href:
        bits.append(f"Change report {href}")
    return " · ".join(bits)


def alerts_for_run(alerts: list[dict], last_run: str | None) -> list[dict]:
    """Only the refresh that last_run names. Older jsonl lines stay in the file."""
    if not last_run:
        return []
    return [
        a for a in alerts
        if a.get("ts") == last_run and a.get("kind") in DETECTOR_KINDS
    ]


def alert_phrase(alert: dict) -> str:
    """One clause per detector event. Same facts as alerts.jsonl, shorter."""
    kind = alert.get("kind")
    if kind == "new-season":
        state = alert.get("state")
        fy = alert.get("fiscal_year")
        if state and fy:
            return f"{state} FY{fy} appeared"
        return alert.get("detail") or "new-season"
    if kind == "new-supplier":
        state = alert.get("state")
        vendor = alert.get("vendor")
        if state and vendor:
            return f"{state}: {vendor} not seen in earlier runs"
        return alert.get("detail") or "new-supplier"
    if kind == "new-document":
        return alert.get("detail") or "new-document"
    if kind == "parse-failed":
        return alert.get("detail") or "a contract PDF produced no rows"
    if kind == "listing-empty":
        return alert.get("detail") or "Michigan DTMB listing had no contract PDFs"
    if kind == "listing-unfetched":
        return alert.get("detail") or "Michigan DTMB salt page could not be fetched"
    if kind == "download-failed":
        return alert.get("detail") or "a listed Michigan contract PDF did not download"
    return alert.get("detail") or kind or "change"


def format_checked(when: datetime) -> str:
    return f"{when.day} {when.strftime('%b %Y, %H:%M')}"


def stale_after(when: datetime) -> timedelta:
    """Weekly cadence: 10 days of silence is an outage, peak season or not."""
    return STALE_AFTER


def watch_strip(
    watch: dict,
    alerts: list[dict],
    now: datetime | None = None,
) -> dict:
    """Headline + checked line. Silence is a signal; missing/stale/failed are not."""
    now = now or datetime.now()
    last_run = watch.get("last_run")
    status = watch.get("last_status") or ("ok" if last_run else None)
    checked = parse_ts(last_run)
    current = alerts_for_run(alerts, last_run)
    phrases = [alert_phrase(a) for a in current]

    if not last_run or checked is None:
        return {
            "tone": "missing",
            "headline": "No refresh recorded.",
            "checked": "The scraper has not written a last-checked time.",
            "revision": revision_line(watch),
            "phrases": [],
        }

    checked_label = format_checked(checked)
    age = now - checked
    stale = age >= stale_after(now)

    if status in FAILED_STATUSES:
        if phrases:
            n = len(phrases)
            noun = "change" if n == 1 else "changes"
            headline = f"{n} {noun} since last refresh: " + "; ".join(phrases) + "."
        elif status == "parse-failed":
            headline = "A contract PDF did not parse."
        elif status == "discovery-failed":
            headline = "Michigan DTMB listing returned no contract PDFs."
        elif status == "empty":
            headline = "Last refresh parsed no rows."
        else:
            headline = "Last refresh failed."
        return {
            "tone": "failed",
            "headline": headline,
            "checked": f"Last checked {checked_label}.",
            "revision": revision_line(watch),
            "phrases": phrases,
        }

    if phrases:
        n = len(phrases)
        noun = "change" if n == 1 else "changes"
        headline = f"{n} {noun} since last refresh: " + "; ".join(phrases) + "."
    else:
        headline = "No new seasons, suppliers, or documents."

    if stale:
        days = int(age.total_seconds() // 86400)
        checked_line = (
            f"Last checked {checked_label} — scraper has not run in {days} days."
        )
        tone = "stale"
    else:
        checked_line = f"Last checked {checked_label}."
        tone = "news" if phrases else "quiet"

    if watch.get("auto_updated_unreviewed") and tone not in ("failed",):
        headline = "Auto-updated, not yet reviewed. " + headline
        if tone != "stale":
            tone = "unreviewed"

    return {
        "tone": tone,
        "headline": headline,
        "checked": checked_line,
        "revision": revision_line(watch),
        "phrases": phrases,
    }


def _has_volume(row: pd.Series) -> bool:
    tons = row.get("contracted_tons")
    return pd.notna(tons) and float(tons) > 0


def _has_price(row: pd.Series) -> bool:
    for col in ("weighted_avg_price", "simple_avg_price"):
        if col in row.index and pd.notna(row[col]):
            return True
    return False


def cell_fill(row: pd.Series | None) -> str:
    """both | partial | empty — not a volume scale."""
    if row is None:
        return "empty"
    vol, price = _has_volume(row), _has_price(row)
    if vol and price:
        return "both"
    if vol or price:
        return "partial"
    return "empty"


def cell_title(row: pd.Series | None) -> str:
    fill = cell_fill(row)
    if fill == "both":
        return "Price and volume"
    if fill == "empty":
        return "Nothing in the documents"
    if row is not None and _has_volume(row) and not _has_price(row):
        return "Volume only"
    if row is not None and _has_price(row) and not _has_volume(row):
        return "Price only"
    return "One of price or volume"


def supplier_label(vendor: str) -> str:
    if vendor == "Unattributed":
        return "Volume, no award"
    return vendor


def coverage_grid(vendor_df: pd.DataFrame, state_code: str) -> dict:
    """Rows = suppliers (+ volume-no-award); columns = fiscal years in the frame."""
    src = vendor_df[vendor_df["state"] == state_code].copy()
    years = sorted(int(y) for y in src["fiscal_year"].dropna().unique())
    named = [v for v in src["vendor"].dropna().unique() if v != "Unattributed"]
    has_unattr = (src["vendor"] == "Unattributed").any()

    latest = int(src["fiscal_year"].max()) if not src.empty else None
    vol = (
        src[src["fiscal_year"] == latest]
        .groupby("vendor")["contracted_tons"]
        .sum()
        if latest is not None else pd.Series(dtype=float)
    )

    def key(name: str) -> float:
        x = vol.get(name, 0)
        return float(x) if pd.notna(x) else 0.0

    vendors = sorted(named, key=key, reverse=True)
    if has_unattr:
        vendors.append("Unattributed")

    lookup: dict[tuple[str, int], pd.Series] = {}
    for _, row in src.iterrows():
        lookup[(str(row["vendor"]), int(row["fiscal_year"]))] = row

    rows = []
    for vendor in vendors:
        cells = []
        for fy in years:
            hit = lookup.get((vendor, fy))
            cells.append({
                "fy": fy,
                "fill": cell_fill(hit),
                "title": cell_title(hit),
            })
        rows.append({"vendor": vendor, "label": supplier_label(vendor), "cells": cells})

    return {
        "state": state_code,
        "title": STATE_NAMES.get(state_code, state_code),
        "years": years,
        "rows": rows,
    }


def _first_url(series: pd.Series) -> str | None:
    for value in series:
        href = _as_url(value)
        if href:
            return href
    return None


def source_documents(raw: pd.DataFrame) -> pd.DataFrame:
    """One row per source PDF/xlsx used in the current filter."""
    cols = [
        "state", "source_doc", "source_label", "fiscal_year_from", "fiscal_year_to",
        "suppliers", "source_url", "source_page_url",
    ]
    if raw.empty or "source_doc" not in raw.columns:
        return pd.DataFrame(columns=cols)
    work = raw[raw["source_doc"].notna() & raw["source_doc"].astype(str).str.strip().ne("")].copy()
    if work.empty:
        return pd.DataFrame(columns=cols)

    rows = []
    for name, grp in work.groupby("source_doc", dropna=False):
        fys = pd.to_numeric(grp["fiscal_year"], errors="coerce").dropna()
        states = [str(s) for s in grp["state"].dropna().unique()]
        vendors = []
        if "vendor" in grp.columns:
            vendors = sorted({
                str(v) for v in grp["vendor"].dropna()
                if str(v) not in ("", "Unattributed", "nan")
            })
        url = _first_url(grp["source_url"]) if "source_url" in grp.columns else None
        page = (_first_url(grp["source_page_url"])
                if "source_page_url" in grp.columns else None)
        snap_url, snap_page = provenance_urls(str(name))
        url = clickable_url(url) or clickable_url(snap_url)
        page = clickable_url(page) or clickable_url(snap_page)
        rows.append({
            "state": states[0] if len(states) == 1 else ", ".join(states),
            "source_doc": str(name),
            "source_label": document_label(str(name)),
            "fiscal_year_from": int(fys.min()) if len(fys) else None,
            "fiscal_year_to": int(fys.max()) if len(fys) else None,
            "suppliers": ", ".join(vendors),
            "source_url": url,
            "source_page_url": page,
        })
    return pd.DataFrame(rows, columns=cols).sort_values(
        ["state", "fiscal_year_from", "source_doc"],
        na_position="last",
    ).reset_index(drop=True)


def coverage_table_html(grid: dict) -> str:
    if not grid["years"]:
        return f'<p class="cov-empty">{grid["title"]}: no years in view.</p>'
    head = "".join(f"<th>FY{fy}</th>" for fy in grid["years"])
    body = []
    for row in grid["rows"]:
        tds = "".join(
            f'<td class="cov-{c["fill"]}" title="{html.escape(c["title"])}"><span></span></td>'
            for c in row["cells"]
        )
        body.append(
            f'<tr><th>{html.escape(row["label"])}</th>{tds}</tr>'
        )
    return (
        f'<div class="cov-wrap"><div class="cov-title">{html.escape(grid["title"])}</div>'
        f'<table class="cov"><thead><tr><th></th>{head}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div>'
    )
