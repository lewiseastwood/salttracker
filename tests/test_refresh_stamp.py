"""Crash path must stamp last_run so a throw is not a never-run."""
from __future__ import annotations

import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_refresh():
    path = os.path.join(ROOT, "scripts", "refresh.py")
    spec = importlib.util.spec_from_file_location("salttracker_refresh", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_forced_throw_mid_scrape_writes_last_run(tmp_path, monkeypatch):
    refresh = _load_refresh()
    state_file = tmp_path / "watch_state.json"
    monkeypatch.setattr(refresh, "STATE", str(state_file))
    monkeypatch.setattr(refresh, "LOG", str(tmp_path / "refresh_log.jsonl"))
    monkeypatch.setattr(sys, "argv", ["refresh.py", "--no-download"])

    def boom(*_a, **_k):
        raise RuntimeError("forced mid-scrape throw")

    monkeypatch.setattr(refresh, "build", boom)

    with pytest.raises(RuntimeError, match="forced mid-scrape throw"):
        refresh.main()

    written = json.loads(state_file.read_text())
    assert written["last_status"] == "error"
    assert written.get("last_run")
    assert os.path.exists(state_file)
