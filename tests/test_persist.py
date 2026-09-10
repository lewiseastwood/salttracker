"""A failed scrape must still commit watch_state.json — that commit is the keepalive."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "persist_refresh.sh")


def _git_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({
        "GIT_AUTHOR_NAME": "salttracker-bot",
        "GIT_AUTHOR_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "salttracker-bot",
        "GIT_COMMITTER_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
        "PERSIST_PUSH": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
    })
    return env


def _git(repo, *args):
    result = subprocess.run(
        ["git", *args], cwd=repo, env=_git_env(),
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed ({result.returncode}): {result.stderr}"
        )
    return result


def _seed_repo(tmp_path):
    # Keep the throwaway repo inside the workspace so a sandbox that cannot
    # git-init in /tmp still exercises the persist script.
    repo = Path(ROOT) / "data" / "interim" / "parse_cache" / f"persist_git_{tmp_path.name}"
    shutil.rmtree(repo, ignore_errors=True)
    (repo / "data").mkdir(parents=True)
    _git(repo, "init", "--initial-branch=main")
    (repo / "data" / "watch_state.json").write_text(json.dumps({
        "last_run": "2026-09-01T07:15:00",
        "last_status": "ok",
    }))
    _git(repo, "add", "data/watch_state.json")
    _git(repo, "commit", "-m", "initial")
    return repo


def test_persist_on_parse_failed_commits_watch_state(tmp_path):
    repo = _seed_repo(tmp_path)
    try:
        (repo / "data" / "watch_state.json").write_text(json.dumps({
            "last_run": "2026-10-06T07:15:00",
            "last_status": "parse-failed",
        }))
        (repo / "data" / "alerts.jsonl").write_text(
            json.dumps({"ts": "2026-10-06T07:15:00", "kind": "parse-failed",
                        "detail": "MI: FY2028.pdf produced no contract rows"}) + "\n"
        )

        result = subprocess.run(
            ["bash", SCRIPT], cwd=repo, env=_git_env(),
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr

        log = _git(repo, "log", "-1", "--format=%s").stdout.strip()
        assert log.startswith("Refresh")
        assert "parse-failed" in log
        committed = json.loads(_git(repo, "show", "HEAD:data/watch_state.json").stdout)
        assert committed["last_status"] == "parse-failed"
        assert committed["last_run"] == "2026-10-06T07:15:00"
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_persist_force_adds_archived_michigan_pdf(tmp_path):
    repo = _seed_repo(tmp_path)
    try:
        (repo / ".gitignore").write_text("*.pdf\n")
        arch = repo / "data" / "archive" / "mi"
        arch.mkdir(parents=True)
        (arch / "abc123_180000000768.pdf").write_bytes(b"%PDF-prior")
        (repo / "data" / "watch_state.json").write_text(json.dumps({
            "last_run": "2026-10-06T07:15:00",
            "last_status": "ok",
        }))
        result = subprocess.run(
            ["bash", SCRIPT], cwd=repo, env=_git_env(),
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        listed = _git(repo, "ls-tree", "-r", "--name-only", "HEAD").stdout
        assert "data/archive/mi/abc123_180000000768.pdf" in listed
    finally:
        shutil.rmtree(repo, ignore_errors=True)
    repo = _seed_repo(tmp_path)
    try:
        result = subprocess.run(
            ["bash", SCRIPT], cwd=repo, env=_git_env(),
            capture_output=True, text=True,
        )
        assert result.returncode == 1
        assert "no commit" in result.stderr
    finally:
        shutil.rmtree(repo, ignore_errors=True)
