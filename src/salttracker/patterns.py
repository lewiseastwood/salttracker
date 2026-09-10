"""Pattern registry: document types this tracker already knows how to read.

Anything that does not match an entry is unfamiliar by definition. The
classifier does not invent a meaning for a new shape.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REGISTRY_PATH = os.path.join(ROOT, "data", "patterns", "document_types.json")


@lru_cache(maxsize=4)
def load_registry(path: str | None = None) -> dict:
    with open(path or REGISTRY_PATH) as fh:
        return json.load(fh)


def types(registry: dict | None = None) -> list[dict]:
    return list((registry or load_registry()).get("types") or [])


def match_type(name: str, url: str, text: str, state: str | None = None,
               registry: dict | None = None) -> dict | None:
    """Return the best registry type, or None if nothing matches."""
    blob = f"{name or ''}\n{url or ''}\n{text or ''}".lower()
    best = None
    best_score = 0
    for entry in types(registry):
        if state and entry.get("state") and entry["state"] != state:
            continue
        rules = entry.get("match") or {}
        score = 0
        for key, needles in rules.items():
            field = (name or "") if key.startswith("filename") else (
                (url or "") if key.startswith("url") else (text or "")
            )
            hay = field.lower()
            hits = sum(1 for n in needles if n.lower() in hay)
            if hits:
                score += hits
            elif key.endswith("_any") and needles:
                # filename/url misses are cheap; text_any is corroboration
                if key.startswith("text"):
                    continue
        if score > best_score:
            best, best_score = entry, score
    if best_score <= 0:
        return None
    return best
