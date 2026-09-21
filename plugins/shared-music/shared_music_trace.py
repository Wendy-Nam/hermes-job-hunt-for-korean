"""Rotating, size-bounded observability log — same convention as
interest_graph_trace.py / memory_core_trace.py / conversation_mode_trace.py
(JSONL under HERMES_DATA/logs/shared-music/trace.jsonl, never raw message
text, best-effort and never allowed to raise)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024
MAX_KEEP_LINES = 20_000


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_path(data_root: Path) -> Path:
    return Path(data_root) / "logs" / "shared-music" / "trace.jsonl"


def _rotate_if_needed(path: Path) -> None:
    try:
        if not path.exists() or path.stat().st_size < MAX_BYTES:
            return
        lines = path.read_text(encoding="utf-8").splitlines()[-MAX_KEEP_LINES:]
        tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass


def log_event(data_root: Path, event: str, **fields) -> None:
    try:
        path = _log_path(data_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed(path)
        record = {"ts": now_iso(), "event": event, **fields}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass
