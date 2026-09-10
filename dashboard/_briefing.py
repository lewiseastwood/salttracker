"""Watch-strip copy and the coverage grid — shared by Streamlit and the HTML pack."""
from __future__ import annotations

import html
import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from _theme import STATE_NAMES

PA_VOLUME_NOTE = (
    "Pennsylvania tons are estimated requirements committed before the season, "
    "not purchased or delivered."
)
UNLIKE_SHARE_NOTE = (
    "The two-state share compares unlike quantities: Pennsylvania is estimated "
    "requirements; Michigan is contracted drop-point awards."
)


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

# Same window as scripts/refresh.py: daily in June–August, Mondays otherwise.
PEAK_MONTHS = (6, 7, 8)
STALE_PEAK = timedelta(days=3)
STALE_OFFSEASON = timedelta(days=10)

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
    """Peak season is a daily scrape; 10 days of silence there is an outage."""
    return STALE_PEAK if when.month in PEAK_MONTHS else STALE_OFFSEASON


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

    return {
        "tone": tone,
        "headline": headline,
        "checked": checked_line,
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


def source_documents(raw: pd.DataFrame) -> pd.DataFrame:
    """One row per source PDF/xlsx used in the current filter."""
    cols = [
        "state", "source_doc", "fiscal_year_from", "fiscal_year_to",
        "suppliers", "source_url",
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
        url = None
        if "source_url" in grp.columns:
            for u in grp["source_url"].dropna():
                if str(u).strip():
                    url = str(u)
                    break
        rows.append({
            "state": states[0] if len(states) == 1 else ", ".join(states),
            "source_doc": str(name),
            "fiscal_year_from": int(fys.min()) if len(fys) else None,
            "fiscal_year_to": int(fys.max()) if len(fys) else None,
            "suppliers": ", ".join(vendors),
            "source_url": url,
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
