"""Propose writes staging and never treats unknown provenance as a null."""
from __future__ import annotations

import json
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from salttracker.pipeline import refuse_unknown_provenance  # noqa: E402
from salttracker.propose import change_report  # noqa: E402


def test_refuse_unknown_provenance_drops_blank_rows():
    df = pd.DataFrame({
        "source_doc": ["ok.pdf", "mystery.pdf"],
        "source_url": ["https://example.com/a.pdf", None],
        "source_page_url": [None, None],
    })
    kept, dropped = refuse_unknown_provenance(df)
    assert list(kept["source_doc"]) == ["ok.pdf"]
    assert list(dropped["source_doc"]) == ["mystery.pdf"]


def test_report_needs_review_when_assertions_fire():
    fetch = {
        "new_docs": [{"name": "new.pdf"}],
        "changed": [],
        "preserved": [],
        "missed": [],
        "mi_listing": "ok",
        "pa_listing": "ok",
    }
    fired = [{"kind": "supplier_dropped", "detail": "MI: Cargill was present last season and is absent this one"}]
    body = change_report("2026-09-15T07:15:00", "needs-review", fetch, [], fired, 0, True)
    assert "Label: needs-review" in body
    assert "Cargill" in body
    assert "WHAT'S NEW" in body
    assert "new.pdf" in body


def test_persist_script_does_not_push_to_main():
    path = os.path.join(ROOT, "scripts", "persist_refresh.sh")
    text = open(path).read()
    assert "git push origin" not in text
    wf = open(os.path.join(ROOT, ".github", "workflows", "refresh.yml")).read()
    assert "git push origin main" not in wf
    assert "open_refresh_pr.sh" in wf
    assert "propose.py" in wf
    sh = open(os.path.join(ROOT, "scripts", "open_refresh_pr.sh")).read()
    assert "notify_auto_merge" not in sh
    assert "gh pr merge" in sh
    persist = open(os.path.join(ROOT, "scripts", "persist_refresh.sh")).read()
    assert "data/archive" in persist
    readme = open(os.path.join(ROOT, "README.md")).read()
    assert "--revert-last" in readme
    assert "auto-updated, not yet reviewed" in readme
    assert "column_signatures" in readme
    assert "data/archive/mi" in readme


def test_revert_last_undoes_the_child_of_auto_merge_parent(tmp_path):
    import json
    import shutil
    import subprocess
    from pathlib import Path
    from salttracker.propose import revert_last_auto_merge

    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({
        "GIT_AUTHOR_NAME": "salttracker-bot",
        "GIT_AUTHOR_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "salttracker-bot",
        "GIT_COMMITTER_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
    })

    def git(*args):
        result = subprocess.run(
            ["git", *args], cwd=repo, env=env, capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise AssertionError(f"git {args} failed: {result.stderr}")
        return result

    repo = Path(ROOT) / "data" / "interim" / "parse_cache" / f"revert_git_{tmp_path.name}"
    shutil.rmtree(repo, ignore_errors=True)
    (repo / "data").mkdir(parents=True)
    git("init", "--initial-branch=main")
    (repo / "data" / "watch_state.json").write_text("{}")
    (repo / "keep.txt").write_text("before\n")
    git("add", "-A")
    git("commit", "-m", "base")
    parent = git("rev-parse", "HEAD").stdout.strip()
    (repo / "keep.txt").write_text("after auto-merge\n")
    (repo / "data" / "watch_state.json").write_text(json.dumps({
        "auto_merge_parent": parent,
        "auto_updated_unreviewed": True,
        "last_status": "ok",
    }))
    git("add", "-A")
    git("commit", "-m", "Refresh routine")
    try:
        sha = revert_last_auto_merge(str(repo))
        assert sha
        assert (repo / "keep.txt").read_text() == "before\n"
        watch = json.loads((repo / "data" / "watch_state.json").read_text())
        assert watch["auto_updated_unreviewed"] is False
        assert watch.get("auto_merge_parent") is None
    finally:
        shutil.rmtree(repo, ignore_errors=True)
