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


def raise_alerts(alerts: list[dict]) -> None:
    """Record and print anything that a person should look at.

    A scheduled run is only useful if it says something when the picture
    changes, so newly published seasons, suppliers and documents are surfaced
    rather than being buried in the diff of an output file.
    """
    if not alerts:
        return
    os.makedirs(os.path.dirname(ALERTS), exist_ok=True)
    stamp = dt.datetime.now().isoformat(timespec="seconds")
    with open(ALERTS, "a") as fh:
        for a in alerts:
            fh.write(json.dumps({"ts": stamp, **a}) + "\n")

    print("\n" + "=" * 66)
    print("NEW CONTRACT DATA DETECTED")
    print("=" * 66)
    for a in alerts:
        print(f"  [{a['kind']}] {a['detail']}")
    print("=" * 66)

    for note in notify.dispatch(alerts, stamp):
        print(f"  {note}")


def detect_changes(result, docs: list) -> list[dict]:
    """Compare this run's coverage against the last run's."""
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
                alerts.append({"kind": "new-supplier", "state": state,
                               "detail": f"{state}: {name} not seen in earlier runs"})
    for name in doc_names:
        if previous.get("documents") and name not in set(previous["documents"]):
            alerts.append({"kind": "new-document",
                           "detail": f"newly published document: {name}"})

    save_state({"coverage": coverage, "vendors": vendors, "documents": doc_names,
                "last_run": dt.datetime.now().isoformat(timespec="seconds")})
    return alerts


def acquire(include_archive: bool, scan_emarketplace: bool = True) -> list[sources.Doc]:
    print("Discovering published contract documents...")
    docs = sources.discover_all(include_archive=include_archive, state_dir=INTERIM,
                                scan_emarketplace=scan_emarketplace)
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
    log({"event": "acquire", "candidates": len(docs), "new": new, "unchanged": unchanged})
    return fetched


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

    docs: list[sources.Doc] = []
    if not args.no_download:
        docs = acquire(include_archive=not args.no_archive,
                       scan_emarketplace=not args.no_scan)
    else:
        print("Skipping download; using documents already on disk.")

    mi, pa, pa_est = local_docs()
    print(f"Parsing {len(mi)} Michigan, {len(pa)} Pennsylvania and "
          f"{len(pa_est)} PA estimate documents...")
    result = build(mi, pa, pa_est)

    if result.raw.empty:
        print("ERROR: no rows parsed.")
        log({"event": "build", "status": "empty"})
        return 1

    paths = export(result)
    print(f"\n{len(result.raw):,} contract line items")
    print(result.state_fy[[
        "state", "fiscal_year", "contracted_tons", "weighted_avg_price"
    ]].round(2).to_string(index=False))
    print("\nOutputs:")
    for k, p in paths.items():
        print(f"  {k:12s} {p}")

    raise_alerts(detect_changes(result, docs))

    log({"event": "build", "status": "ok", "rows": int(len(result.raw)),
         "states": sorted(result.raw["state"].unique().tolist()),
         "fiscal_years": sorted(int(x) for x in result.raw["fiscal_year"].unique())})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
