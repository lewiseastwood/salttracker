"""Detect-and-propose a contract refresh. Never publishes to the live tracker.

Writes a staging tree and a change report. The GitHub Action opens a PR;
routine PRs auto-merge so the dashboard updates, needs-review PRs wait.
"""
from __future__ import annotations

import datetime as dt
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys

import pandas as pd

from . import archive, assertions, classify, patterns, sources
from .pipeline import build, export, refuse_unknown_provenance
from .sources import Doc

STAGING_DEFAULT = os.path.join("data", "staging")


def _load_manifest(path: str) -> list[dict]:
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return []


def _write_json(path: str, payload) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2, default=str)


def _digest(path: str) -> str | None:
    if not path or not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _unique_by_content(paths: list[str]) -> list[str]:
    seen: dict[str, str] = {}
    for path in paths:
        digest = _digest(path)
        if digest:
            seen.setdefault(digest, path)
    return sorted(seen.values())


def inventory(raw_root: str) -> tuple[dict[str, str | None], list[str], list[str]]:
    mi: dict[str, str | None] = {}
    for path in sorted(glob.glob(os.path.join(raw_root, "MI", "*.pdf"))):
        vendor = None
        base = os.path.basename(path)
        for cno, v in sources.MI_CONTRACT_VENDOR.items():
            if cno in base:
                vendor = v
                break
        mi[path] = vendor
    pa_all = _unique_by_content(
        sorted(glob.glob(os.path.join(raw_root, "PA", "*.pdf"))
               + glob.glob(os.path.join(raw_root, "PA", "*.xlsx")))
    )
    estimates = [p for p in pa_all if "estimates" in os.path.basename(p).lower()]
    packets = [
        p for p in pa_all
        if p not in set(estimates)
        and p.lower().endswith(".pdf")
        and "NOA" not in os.path.basename(p).upper()
    ]
    return mi, packets, estimates


def _totals(df: pd.DataFrame) -> dict[tuple[str, int], float]:
    if df is None or df.empty:
        return {}
    out: dict[tuple[str, int], float] = {}
    for _, row in df.iterrows():
        tons = row.get("contracted_tons")
        try:
            n = float(tons)
        except (TypeError, ValueError):
            continue
        if n != n or n <= 0:
            continue
        out[(str(row["state"]), int(row["fiscal_year"]))] = n
    return out


def _suppliers_latest(df: pd.DataFrame) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Named suppliers in the latest FY vs the year before, per state."""
    prev: dict[str, set[str]] = {}
    now: dict[str, set[str]] = {}
    if df is None or df.empty:
        return prev, now
    work = df[df.get("is_attributed", True) == True] if "is_attributed" in df.columns else df
    for state, grp in work.groupby("state"):
        years = sorted(int(y) for y in grp["fiscal_year"].dropna().unique())
        if not years:
            continue
        latest = years[-1]
        now[str(state)] = {
            str(v) for v in grp.loc[grp["fiscal_year"] == latest, "vendor"].dropna()
            if str(v) not in ("", "Unattributed")
        }
        earlier = [y for y in years if y < latest]
        if earlier:
            prior = earlier[-1]
            prev[str(state)] = {
                str(v) for v in grp.loc[grp["fiscal_year"] == prior, "vendor"].dropna()
                if str(v) not in ("", "Unattributed")
            }
    return prev, now


def _series_basis(df: pd.DataFrame) -> dict[str, set[str]]:
    if df is None or df.empty or "tons_basis" not in df.columns:
        return {}
    out: dict[str, set[str]] = {}
    for state, grp in df.groupby("state"):
        out[str(state)] = {str(b) for b in grp["tons_basis"].dropna() if str(b)}
    return out


def _known_contract_nos(manifest: list[dict]) -> set[str]:
    nos: set[str] = set()
    for entry in manifest:
        nos |= assertions.contract_numbers(
            entry.get("name") or "", entry.get("url") or "",
            entry.get("contract_no") or "",
        )
    return nos


def fetch_into_staging(
    staging_raw: str,
    live_raw: str,
    live_manifest: list[dict],
    archive_dir: str,
    archive_index: str,
    scan_emarketplace: bool = True,
) -> dict:
    mi_listed, mi_listing = sources.fetch_michigan_listing()
    pa_listed, pa_listing = sources.fetch_pennsylvania_live(
        state_dir=os.path.join(os.path.dirname(staging_raw), "interim"),
        scan_emarketplace=scan_emarketplace,
    )
    listed = sources.unique_docs(list(mi_listed) + list(pa_listed))
    by_name = {e.get("name"): e for e in live_manifest if e.get("name")}
    by_hash = {e.get("sha256"): e for e in live_manifest if e.get("sha256")}
    fetched: list[Doc] = []
    new_docs: list[dict] = []
    changed: list[dict] = []
    preserved: list[dict] = []
    missed: list[str] = []

    for doc in listed:
        got = sources.download(doc, staging_raw)
        if got is None:
            missed.append(doc.name)
            continue
        fetched.append(got)
        prior_live = os.path.join(live_raw, got.state, got.name)
        prior_sha = _digest(prior_live) if os.path.isfile(prior_live) else None
        live_entry = by_name.get(got.name) or by_hash.get(got.sha256) or {}
        is_new = got.sha256 not in by_hash
        is_changed = bool(prior_sha and prior_sha != got.sha256) or (
            live_entry.get("sha256") and live_entry.get("sha256") != got.sha256
        )
        if got.state == "MI" and prior_sha and prior_sha != got.sha256:
            rec = archive.preserve_michigan_overwrite(
                got.url, prior_live, archive_dir, archive_index,
            )
            preserved.append(rec)
            if rec.get("save_ok") and rec.get("wayback_url"):
                got.url = rec["wayback_url"]
                got.archived_timestamp = rec.get("wayback_timestamp")
                got.notes = "wayback save of overwritten DTMB path"
        record = {
            "name": got.name,
            "state": got.state,
            "url": got.url,
            "page_url": got.page_url,
            "sha256": got.sha256,
            "path": got.path,
            "bytes": got.bytes,
            "source_url": got.url,
            "source_page_url": got.page_url or sources.page_url_from_file_url(got.url),
        }
        if is_new:
            new_docs.append(record)
        elif is_changed:
            changed.append(record)

    return {
        "fetched": fetched,
        "new_docs": new_docs,
        "changed": changed,
        "preserved": preserved,
        "missed": missed,
        "mi_listing": mi_listing,
        "pa_listing": pa_listing,
        "mi_listed": [d.name for d in mi_listed],
        "pa_listed": [d.name for d in pa_listed],
        "listed_count": len(listed),
    }


def classify_new(new_docs: list[dict], registry: dict, http_post=None) -> list[dict]:
    out = []
    for doc in new_docs:
        out.append(classify.classify_document(
            doc.get("path") or "",
            name=doc.get("name") or "",
            url=doc.get("url") or "",
            state=doc.get("state"),
            registry=registry,
            http_post=http_post,
        ))
    return out


def _copy_familiar_into_raw(classifications: list[dict], new_docs: list[dict],
                             live_raw: str) -> list[str]:
    """Known types may be parsed. Unfamiliar files stay in staging only."""
    familiar = {c.get("filename") for c in classifications if c.get("familiar")}
    copied = []
    for doc in new_docs:
        name = doc.get("name")
        path = doc.get("path")
        if name not in familiar or not path or not os.path.isfile(path):
            continue
        dest_dir = os.path.join(live_raw, doc.get("state") or "XX")
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, name)
        shutil.copy2(path, dest)
        copied.append(dest)
    return copied


def change_report(stamp: str, label: str, fetch: dict, classifications: list[dict],
                  fired: list[dict], dropped_rows: int, discovery_ok: bool) -> str:
    new_names = [d["name"] for d in fetch.get("new_docs") or []]
    changed_names = [d["name"] for d in fetch.get("changed") or []]
    unsure = [
        f"{c.get('filename')}: " + ", ".join(c.get("undetermined") or ["(none)"])
        for c in classifications
        if c.get("undetermined") or not c.get("familiar")
    ]
    lines = [
        f"Refresh {stamp[:10]}",
        f"Label: {label}",
        "",
        "This PR proposes tracker updates. Routine PRs auto-merge so the "
        "dashboard updates. Needs-review PRs wait for a person.",
        "",
        "WHAT'S NEW",
    ]
    if new_names:
        lines.extend(f"- {n}" for n in new_names)
    else:
        lines.append("- (none)")
    lines += ["", "WHAT CHANGED"]
    if changed_names:
        lines.extend(f"- {n}" for n in changed_names)
    else:
        lines.append("- (none)")
    for rec in fetch.get("preserved") or []:
        lines.append(
            f"- preserved MI prior sha256 {str(rec.get('prior_sha256') or '')[:12]}… "
            f"wayback={rec.get('wayback_url') or rec.get('save_error')}"
        )
    if not discovery_ok:
        lines += [
            "",
            "DISCOVERY",
            f"- Michigan listing: {fetch.get('mi_listing')}",
            f"- Pennsylvania listing: {fetch.get('pa_listing')}",
            f"- missed: {', '.join(fetch.get('missed') or []) or '(none)'}",
        ]
    lines += ["", "ASSERTIONS"]
    if fired:
        lines.extend(f"- [{a['kind']}] {a['detail']}" for a in fired)
    else:
        lines.append("- (none)")
    if dropped_rows:
        lines += ["", f"PROVENANCE: refused {dropped_rows} row(s) with unknown provenance."]
    lines += ["", "CLASSIFICATION"]
    lines.append(json.dumps(classifications, indent=2, default=str))
    lines += ["", "UNSURE"]
    if unsure:
        lines.extend(f"- {u}" for u in unsure)
    else:
        lines.append("- (none)")
    return "\n".join(lines) + "\n"


def run(root: str, staging: str | None = None, *,
        scan_emarketplace: bool = True, rebuild: bool = True,
        http_post=None) -> dict:
    staging = staging or os.path.join(root, STAGING_DEFAULT)
    live_raw = os.path.join(root, "data", "raw")
    live_manifest_path = os.path.join(root, "data", "manifest.json")
    live_output = os.path.join(root, "data", "output")
    watch_path = os.path.join(root, "data", "watch_state.json")
    stamp = dt.datetime.now().isoformat(timespec="seconds")

    os.makedirs(staging, exist_ok=True)
    staging_raw = os.path.join(staging, "raw")
    archive_dir = os.path.join(root, "data", "archive", "mi")
    archive_index = os.path.join(root, "data", "archive", "index.json")

    registry = patterns.load_registry()
    live_manifest = _load_manifest(live_manifest_path)
    fetch = fetch_into_staging(
        staging_raw, live_raw, live_manifest, archive_dir, archive_index,
        scan_emarketplace=scan_emarketplace,
    )

    discovery_ok = (
        fetch["mi_listing"] == "ok"
        and fetch["pa_listing"] == "ok"
        and not fetch["missed"]
    )
    classifications = classify_new(fetch["new_docs"], registry, http_post=http_post)
    changed_classifications = classify_new(fetch["changed"], registry, http_post=http_post)
    unfamiliar = [c for c in classifications if not c.get("familiar")]

    dropped_n = 0
    result = None
    proposed_rows: list[dict] = []
    proposed_totals: dict[tuple[str, int], float] = {}
    now_suppliers: dict[str, set[str]] = {}
    prev_suppliers: dict[str, set[str]] = {}
    live_state = pd.DataFrame()
    live_vendor = pd.DataFrame()
    live_state_path = os.path.join(live_output, "salt_contracts_by_state.csv")
    live_vendor_path = os.path.join(live_output, "salt_contracts_by_vendor.csv")
    if os.path.isfile(live_state_path):
        live_state = pd.read_csv(live_state_path)
    if os.path.isfile(live_vendor_path):
        live_vendor = pd.read_csv(live_vendor_path)

    staging_manifest = os.path.join(staging, "manifest.json")
    if os.path.isfile(live_manifest_path):
        shutil.copy2(live_manifest_path, staging_manifest)
    if fetch["fetched"]:
        sources.write_manifest(fetch["fetched"], staging_manifest)

    if discovery_ok and rebuild:
        _copy_familiar_into_raw(classifications, fetch["new_docs"], live_raw)
        for doc in fetch["changed"]:
            src = doc.get("path")
            if src and os.path.isfile(src):
                dest_dir = os.path.join(live_raw, doc.get("state") or "MI")
                os.makedirs(dest_dir, exist_ok=True)
                shutil.copy2(src, os.path.join(dest_dir, doc["name"]))
        mi, pa, est = inventory(live_raw)
        result = build(mi, pa, est, manifest_path=staging_manifest)
        kept, dropped = refuse_unknown_provenance(result.raw)
        dropped_n = len(dropped)
        result.raw = kept
        if not kept.empty:
            from .pipeline import aggregate
            result.state_fy_vendor, result.state_fy = aggregate(kept)
        proposed_rows = kept.to_dict("records") if not kept.empty else []
        proposed_totals = _totals(result.state_fy)
        prev_suppliers, now_suppliers = _suppliers_latest(result.state_fy_vendor)
    else:
        prev_suppliers, now_suppliers = _suppliers_latest(live_vendor)

    fired = assertions.evaluate(
        classifications=classifications,
        new_docs=fetch["new_docs"] + fetch["changed"],
        proposed_rows=proposed_rows,
        live_manifest=live_manifest,
        live_totals=_totals(live_state),
        proposed_totals=proposed_totals,
        prev_suppliers=prev_suppliers,
        now_suppliers=now_suppliers,
        series_basis=_series_basis(live_vendor if not live_vendor.empty else live_state),
        known_contract_nos=_known_contract_nos(live_manifest),
    )
    # Overwrites keep last season's dates; only the column set is re-checked.
    fired += assertions.column_drift(changed_classifications)
    if not discovery_ok:
        fired.append({
            "kind": "discovery_failed",
            "detail": (
                f"listing MI={fetch['mi_listing']} PA={fetch['pa_listing']}"
                + (f"; missed {fetch['missed']}" if fetch["missed"] else "")
            ),
        })
    if unfamiliar:
        fired.append({
            "kind": "unfamiliar_document",
            "detail": "Unfamiliar document type(s): "
            + ", ".join(c.get("filename") or "?" for c in unfamiliar)
            + " — not parsed onto charts",
        })
    for rec in fetch.get("preserved") or []:
        if not rec.get("save_ok") or not rec.get("wayback_url"):
            fired.append({
                "kind": "wayback_save_failed",
                "detail": (
                    f"Save Page Now failed for {rec.get('live_url')}: "
                    f"{rec.get('save_error') or 'no snapshot URL'}. "
                    "Prior bytes are in data/archive/mi/. Not recording a Wayback source_url."
                ),
            })

    label = "needs-review" if fired else "routine"
    if label == "routine":
        for c in classifications:
            if (c.get("undetermined") or not c.get("columns_match")
                    or not c.get("familiar")):
                label = "needs-review"
                break
    report = change_report(
        stamp, label, fetch, classifications, fired, dropped_n, discovery_ok,
    )
    report_path = os.path.join(staging, "CHANGE_REPORT.txt")
    output_report = os.path.join(live_output, "CHANGE_REPORT.txt")
    os.makedirs(live_output, exist_ok=True)
    with open(report_path, "w") as fh:
        fh.write(report)
    shutil.copy2(report_path, output_report)

    proposal = {
        "stamp": stamp,
        "label": label,
        "discovery_ok": discovery_ok,
        "assertions": fired,
        "classifications": classifications,
        "new_documents": [d.get("name") for d in fetch["new_docs"]],
        "changed": [d.get("name") for d in fetch["changed"]],
        "preserved": fetch.get("preserved"),
        "undetermined": [
            {"filename": c.get("filename"), "undetermined": c.get("undetermined")}
            for c in classifications if c.get("undetermined")
        ],
    }
    _write_json(os.path.join(staging, "proposal.json"), proposal)
    _write_json(os.path.join(staging, "classifications.json"), classifications)

    if result is not None and discovery_ok and not result.raw.empty:
        export(result, live_output)
        if os.path.isfile(staging_manifest):
            shutil.copy2(staging_manifest, live_manifest_path)

    watch = {}
    if os.path.isfile(watch_path):
        try:
            with open(watch_path) as fh:
                watch = json.load(fh)
        except (OSError, ValueError):
            watch = {}
    watch["last_run"] = stamp
    watch["last_status"] = "ok" if discovery_ok and label == "routine" else (
        "discovery-failed" if not discovery_ok else "needs-review"
    )
    watch["change_report_path"] = "data/output/CHANGE_REPORT.txt"
    parent = os.environ.get("GITHUB_SHA") or _git_sha(root)
    if label == "routine" and (fetch["new_docs"] or fetch["changed"]):
        watch["auto_updated_unreviewed"] = True
        if parent:
            watch["auto_merge_parent"] = parent
    if result is not None and not result.raw.empty:
        watch["coverage"] = {
            str(st): sorted(int(y) for y in grp["fiscal_year"].unique())
            for st, grp in result.raw.groupby("state")
        }
    _write_json(watch_path, watch)

    return proposal


def _git_sha(root: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root,
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def mark_reviewed(root: str) -> dict:
    """Clear the auto-updated strip. Does not push."""
    watch_path = os.path.join(root, "data", "watch_state.json")
    watch = {}
    if os.path.isfile(watch_path):
        with open(watch_path) as fh:
            watch = json.load(fh)
    watch["auto_updated_unreviewed"] = False
    _write_json(watch_path, watch)
    return watch


def revert_last_auto_merge(root: str) -> str:
    """Revert the squash commit whose parent is auto_merge_parent, then rebuild.

    Does not push. After this, commit if git revert wrote one, then push main.
    """
    watch_path = os.path.join(root, "data", "watch_state.json")
    with open(watch_path) as fh:
        watch = json.load(fh)
    parent = watch.get("auto_merge_parent")
    if not parent:
        raise RuntimeError("watch_state.json has no auto_merge_parent; nothing to revert")
    log = subprocess.check_output(
        ["git", "log", "--first-parent", "--format=%H %P", "-n", "50"],
        cwd=root, text=True,
    )
    target = None
    for line in log.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        sha, parents = parts[0], parts[1:]
        if parent in parents:
            target = sha
            break
    if not target:
        raise RuntimeError(
            f"no first-parent commit on this branch has parent {parent[:12]}. "
            "Check out main and pull before --revert-last."
        )
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "salttracker-bot")
    env.setdefault(
        "GIT_AUTHOR_EMAIL",
        "41898282+github-actions[bot]@users.noreply.github.com",
    )
    env.setdefault("GIT_COMMITTER_NAME", env["GIT_AUTHOR_NAME"])
    env.setdefault("GIT_COMMITTER_EMAIL", env["GIT_AUTHOR_EMAIL"])
    subprocess.check_call(["git", "revert", "--no-commit", target], cwd=root, env=env)
    watch["auto_updated_unreviewed"] = False
    watch["last_status"] = "reverted"
    watch["auto_merge_parent"] = None
    _write_json(watch_path, watch)
    html = os.path.join(root, "scripts", "build_exec_dashboard.py")
    if os.path.isfile(html):
        subprocess.check_call([sys.executable, html], cwd=root)
    subprocess.check_call(["git", "add", "--", "data/watch_state.json"], cwd=root, env=env)
    output_dir = os.path.join(root, "data", "output")
    if os.path.isdir(output_dir):
        subprocess.check_call(["git", "add", "--", "data/output"], cwd=root, env=env)
    subprocess.check_call(
        ["git", "commit", "-m", "Revert last routine auto-merge"],
        cwd=root, env=env,
    )
    return target
