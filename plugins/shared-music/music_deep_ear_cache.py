"""Deep Ear result cache (task item 8) — one JSON file per (canonical_id,
window), separate from the Fast Ear track cache (music_cache.py) since the
two have independent lifecycles/versions: a Deep Ear analyzer upgrade
shouldn't force Fast Ear's compact perception to re-run, and vice versa.

Stores only the analysis result, never the separated stem audio itself
(task item 8's disk-cost note — stems are written to a tmp path by the
worker and deleted immediately after use, never kept)."""
from __future__ import annotations

import json
import os
from pathlib import Path


def _deep_dir(wiki_root: Path, canonical_id: str) -> Path:
    d = Path(wiki_root) / ".shared-music" / "deep" / canonical_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _window_key(start_s: float, end_s: float) -> str:
    return f"{int(round(start_s))}_{int(round(end_s))}"


def load(wiki_root: Path, canonical_id: str, start_s: float, end_s: float) -> dict | None:
    path = _deep_dir(wiki_root, canonical_id) / f"{_window_key(start_s, end_s)}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save(wiki_root: Path, canonical_id: str, start_s: float, end_s: float, result: dict) -> None:
    path = _deep_dir(wiki_root, canonical_id) / f"{_window_key(start_s, end_s)}.json"
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def is_current(result: dict, expected_analyzer_version: str) -> bool:
    return result.get("analyzer_version") == expected_analyzer_version
