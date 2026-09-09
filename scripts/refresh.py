#!/usr/bin/env python3
"""Refresh the road-salt contract dataset.

    python scripts/refresh.py                # discover, download, parse, export
    python scripts/refresh.py --no-download  # rebuild from documents already on disk
    python scripts/refresh.py --no-archive   # skip the Wayback Machine sweep

New contracts are published in the July-August window each year, so during that
window this should run frequently (see scripts/install_schedule.sh). Documents
are content-hashed, so re-running is cheap and only changed files are rewritten.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from salttracker import notify, sources  # noqa: E402
from salttracker.pipeline import build, export  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_ROOT = os.path.join(ROOT, "data", "raw")
MANIFEST = os.path.join(ROOT, "data", "manifest.json")
LOG = os.path.join(ROOT, "data", "refresh_log.jsonl")
STATE = os.path.join(ROOT, "data", "watch_state.json")
ALERTS = os.path.join(ROOT, "data", "alerts.jsonl")
INTERIM = os.path.join(ROOT, "data", "interim")

PEAK_MONTHS = (6, 7, 8)  # contracts are finalized July-August for the next winter
FAIL_KINDS = frozenset({"parse-failed", "listing-empty", "listing-unfetched",
                        "download-failed"})


def log(event: dict) -> None:
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    event["ts"] = dt.datetime.now().isoformat(timespec="seconds")
    with open(LOG, "a") as fh:
        fh.write(json.dumps(event) + "\n")


def load_state() -> dict:
    try:
        with open(STATE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)


def raise_alerts(alerts: list[dict], stamp: str) -> None:
    """Record and print anything that a person should look at.

    A scheduled run is only useful if it says something when the picture
    changes or when a check failed. Newly published seasons, suppliers and
    documents are surfaced rather than being buried in the diff of an output
    file. A listing or parse failure is louder than a quiet week. The
    dashboard strip reads alerts whose ts matches watch_state last_run, so
    this stamp must be the same value written there.
    """
    if not alerts:
        return
    os.makedirs(os.path.dirname(ALERTS), exist_ok=True)
    with open(ALERTS, "a") as fh:
        for a in alerts:
            fh.write(json.dumps({"ts": stamp, **a}) + "\n")

    failed = any(a.get("kind") in FAIL_KINDS for a in alerts)
    print("\n" + "=" * 66)
    print("REFRESH FAILED" if failed else "NEW CONTRACT DATA DETECTED")
    print("=" * 66)
    for a in alerts:
        print(f"  [{a['kind']}] {a['detail']}")
    print("=" * 66)

    for note in notify.dispatch(alerts, stamp):
        print(f"  {note}")


def stamp_run(stamp: str, status: str, snapshot: dict | None = None) -> None:
    """Every attempt writes last_run so a quiet strip is not 'we didn't check'."""
    state = load_state()
    if snapshot:
        state.update(snapshot)
    state["last_run"] = stamp
    state["last_status"] = status
    save_state(state)


def detect_changes(result, docs: list) -> tuple[list[dict], dict]:
    """Compare this run's coverage against the last run's. Does not stamp last_run."""
    previous = load_state()
    raw = result.raw
    coverage = {
        f"{state}": sorted(int(fy) for fy in grp["fiscal_year"].unique())
        for state, grp in raw.groupby("state")
    }
    vendors = {
        f"{state}": sorted(str(v) for v in grp["vendor"].dropna().unique())
        for state, grp in raw.groupby("state")
    }
    doc_names = sorted({d.name for d in docs}) if docs else previous.get("documents", [])

    alerts: list[dict] = []
    for state, years in coverage.items():
        seen = set(previous.get("coverage", {}).get(state, []))
        for fy in years:
            if seen and fy not in seen:
                alerts.append({"kind": "new-season", "state": state, "fiscal_year": fy,
                               "detail": f"{state} FY{fy} appeared in the data for the first time"})
    for state, names in vendors.items():
        seen = set(previous.get("vendors", {}).get(state, []))
        for name in names:
            if seen and name not in seen:
                alerts.append({"kind": "new-supplier", "state": state, "vendor": name,
                               "detail": f"{state}: {name} not seen in earlier runs"})
    for name in doc_names:
        if previous.get("documents") and name not in set(previous["documents"]):
            alerts.append({"kind": "new-document",
                           "detail": f"newly published document: {name}"})

    snapshot = {"coverage": coverage, "vendors": vendors, "documents": doc_names}
    return alerts, snapshot


def parse_failure_alerts(yields: list[dict]) -> list[dict]:
    """Award PDFs that produced no rows. A layout change must not look quiet."""
    alerts: list[dict] = []
    for y in yields:
        if y.get("kind") != "award":
            continue
        if int(y.get("n_rows") or 0) > 0:
            continue
        name = y.get("name") or os.path.basename(y.get("path") or "document")
        extra = f" ({y['error']})" if y.get("error") else ""
        alerts.append({
            "kind": "parse-failed",
            "state": y.get("state"),
            "detail": (f"{y.get('state')}: {name} produced no contract rows{extra}"
                       " — parser may not match this layout"),
        })
    return alerts


def listing_failure_alerts(status: str) -> list[dict]:
    if status == "unfetched":
        return [{
            "kind": "listing-unfetched",
            "state": "MI",
            "detail": ("Michigan DTMB salt page could not be fetched — next year's "
                       "contract number is discovered from that listing, not from "
                       "a hardcoded file"),
        }]
    if status == "empty":
        return [{
            "kind": "listing-empty",
            "state": "MI",
            "detail": ("Michigan DTMB listing had no contract PDFs — last year's "
                       "number may have been delisted and the new one was not found"),
        }]
    return []


def acquire(include_archive: bool, scan_emarketplace: bool = True
            ) -> tuple[list[sources.Doc], str, list[sources.Doc]]:
    print("Discovering published contract documents...")
    mi_listed, listing = sources.fetch_michigan_listing()
    print(f"  Michigan DTMB listing: {listing} ({len(mi_listed)} PDF link(s))")
    pa = sources.discover_pennsylvania(state_dir=INTERIM, scan_emarketplace=scan_emarketplace)
    docs = list(mi_listed) + pa
    # Wayback recovers overwritten history. It is not a stand-in for today's listing.
    if include_archive and listing == "ok":
        docs += sources.discover_michigan_archived()
    docs = sources.unique_docs(docs)
    print(f"  {len(docs)} candidate documents")

    fetched, new, unchanged = [], 0, 0
    for d in docs:
        got = sources.download(d, RAW_ROOT)
        if got is None:
            print(f"  [skip] {d.name}")
            continue
        fetched.append(got)
        if got.notes == "unchanged":
            unchanged += 1
        else:
            new += 1
            print(f"  [new]  {got.name}  ({got.bytes:,} bytes)")
    print(f"  downloaded: {new} new / {unchanged} unchanged")
    sources.write_manifest(fetched, MANIFEST)
    log({"event": "acquire", "candidates": len(docs), "new": new, "unchanged": unchanged,
         "michigan_listing": listing, "michigan_listed": len(mi_listed)})
    return fetched, listing, mi_listed


def local_docs() -> tuple[dict[str, str | None], list[str], list[str]]:
    """Michigan paths mapped to a default vendor, plus PA packets and estimates."""
    mi: dict[str, str | None] = {}
    for path in sorted(glob.glob(os.path.join(RAW_ROOT, "MI", "*.pdf"))):
        vendor = None
        base = os.path.basename(path)
        for cno, v in sources.MI_CONTRACT_VENDOR.items():
            if cno in base:
                vendor = v
                break
        if vendor is None:  # fall back to a vendor token in the filename
            for v in ("Detroit Salt", "Compass Minerals", "Cargill"):
                if v.replace(" ", "") in base.replace(" ", "") or v.split()[0] in base:
                    vendor = v
                    break
        mi[path] = vendor

    # Estimates carry county tonnage and are parsed by a different reader than
    # the contract packets, which carry county pricing.
    pa_all = _unique_by_content(sorted(glob.glob(os.path.join(RAW_ROOT, "PA", "*.pdf"))
                                       + glob.glob(os.path.join(RAW_ROOT, "PA", "*.xlsx"))))
    estimates = [p for p in pa_all if "estimates" in os.path.basename(p).lower()]
    packets = [p for p in pa_all if p not in set(estimates) and p.lower().endswith(".pdf")]
    return mi, packets, estimates


def _unique_by_content(paths: list[str]) -> list[str]:
    """Drop files whose bytes duplicate one already listed.

    eMarketplace serves the same combined change notice under every contract
    number on the contract, so the identical PDF arrives several times under
    different names and would otherwise be parsed once per copy.
    """
    seen: dict[str, str] = {}
    for path in paths:
        try:
            with open(path, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            continue
        seen.setdefault(digest, path)
    return sorted(seen.values())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-download", action="store_true", help="parse documents already on disk")
    ap.add_argument("--no-archive", action="store_true", help="skip the Wayback Machine sweep")
    ap.add_argument("--no-scan", action="store_true",
                    help="skip the eMarketplace scan for newly posted PA solicitations")
    args = ap.parse_args()

    month = dt.date.today().month
    if month in PEAK_MONTHS:
        print(f"Note: month {month} is in the June-August contracting window; "
              "new awards for the next winter are likely to appear.")

    stamp = dt.datetime.now().isoformat(timespec="seconds")
    try:
        return _run(args, stamp)
    except Exception:
        stamp_run(stamp, "error")
        raise


def _run(args: argparse.Namespace, stamp: str) -> int:
    docs: list[sources.Doc] = []
    listing = "skipped"
    listed_mi: list[sources.Doc] = []
    if not args.no_download:
        docs, listing, listed_mi = acquire(include_archive=not args.no_archive,
                                           scan_emarketplace=not args.no_scan)
        listing_alerts = listing_failure_alerts(listing)
        fetched_urls = {d.url for d in docs}
        missed = [d for d in listed_mi if d.url not in fetched_urls]
        if listing == "ok" and missed:
            listing_alerts.append({
                "kind": "download-failed",
                "state": "MI",
                "detail": ("Michigan listing named "
                           + ", ".join(d.name for d in missed)
                           + " but the file(s) did not download"),
            })
        if listing_alerts:
            print("ERROR: Michigan live listing did not yield current contract PDFs.")
            log({"event": "build", "status": "discovery-failed", "listing": listing,
                 "missed": [d.name for d in missed]})
            stamp_run(stamp, "discovery-failed")
            raise_alerts(listing_alerts, stamp)
            return 1
    else:
        print("Skipping download; using documents already on disk.")

    mi, pa, pa_est = local_docs()
    print(f"Parsing {len(mi)} Michigan, {len(pa)} Pennsylvania and "
          f"{len(pa_est)} PA estimate documents...")
    result = build(mi, pa, pa_est)

    parse_alerts = parse_failure_alerts(result.parse_yields)
    if result.raw.empty:
        print("ERROR: no rows parsed.")
        status = "parse-failed" if parse_alerts else "empty"
        log({"event": "build", "status": status})
        stamp_run(stamp, status)
        raise_alerts(parse_alerts, stamp)
        return 1

    previous_states = set(load_state().get("coverage", {}))
    now_states = set(str(s) for s in result.raw["state"].dropna().unique())
    dropped = previous_states - now_states
    if dropped:
        print(f"ERROR: refresh dropped states {sorted(dropped)}; not writing outputs.")
        log({"event": "build", "status": "dropped_states", "dropped": sorted(dropped)})
        stamp_run(stamp, "error")
        return 1

    paths = export(result)
    print(f"\n{len(result.raw):,} contract line items")
    print(result.state_fy[[
        "state", "fiscal_year", "contracted_tons", "weighted_avg_price"
    ]].round(2).to_string(index=False))
    print("\nOutputs:")
    for k, p in paths.items():
        print(f"  {k:12s} {p}")

    alerts, snapshot = detect_changes(result, docs)
    alerts = parse_alerts + alerts
    status = "parse-failed" if parse_alerts else "ok"
    stamp_run(stamp, status, snapshot)
    raise_alerts(alerts, stamp)

    log({"event": "build", "status": status, "rows": int(len(result.raw)),
         "states": sorted(result.raw["state"].unique().tolist()),
         "fiscal_years": sorted(int(x) for x in result.raw["fiscal_year"].unique())})
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
