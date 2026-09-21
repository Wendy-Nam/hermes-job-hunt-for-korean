"""Runtime capability detection for authenticated acquisition.

Only ever reports *whether* a credential is configured and whether it still
works. Cookie values, domains, tokens and proxy URLs never appear in the
returned structure, and therefore never reach trace.jsonl, Obsidian or the LLM
context — the whole point of this module is that the rest of the pipeline can
branch on capability without any component handling the secret.

Discovery, not invention: this looks for jars Hermes *already has* before any
part of the system asks the user to export a new one. The audit that produced
this file found exactly one pre-existing jar
(`/opt/data/cookies.txt`, 58 youtube.com entries, written 2026-06-27), which is
neither mounted into the agent container nor still valid — it answers
`bot_check` on the same videos as an unauthenticated request. So the honest
status for this deployment is AUTH_CAPABILITY_MISSING, and that is a fact to
surface, not to paper over.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

STATUS_MISSING = "AUTH_CAPABILITY_MISSING"
STATUS_CONFIGURED = "configured"
STATUS_UNREADABLE = "unreadable"
STATUS_EMPTY = "empty"
STATUS_USABLE = "usable"
STATUS_INVALID = "invalid"
STATUS_UNKNOWN = "unknown"

#: Jars this deployment may already hold, most-specific first. The dedicated
#: shared-music path wins when present; the others are pre-existing Hermes
#: credential material discovered by the acquisition audit.
def _candidate_jars() -> list[Path]:
    data_root = Path(os.environ.get("HERMES_DATA") or "/opt/data")
    return [
        data_root / ".youtube-cookies.txt",
        data_root / "cookies.txt",
    ]


def _looks_like_youtube_jar(path: Path) -> bool:
    """Cheap structural check. Reads the file to count matching lines and
    returns a bool — no line, domain or value is retained or returned."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if "youtube.com" in line and not line.startswith("#"):
                    return True
    except OSError:
        return False
    return False


def youtube_cookie_status() -> dict:
    """Capability descriptor. Safe to log verbatim.

    `usable` is deliberately left `unknown` here rather than guessed: proving a
    jar works costs a real network round trip, so the acquisition ladder
    reports it from an actual attempt (see `note_cookie_result`) instead of
    this function pretending to know.
    """
    now = time.time()
    for path in _candidate_jars():
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            return {"status": STATUS_UNREADABLE, "configured": True, "usable": STATUS_UNKNOWN,
                    "last_checked": now}
        if size == 0:
            return {"status": STATUS_EMPTY, "configured": True, "usable": STATUS_INVALID,
                    "last_checked": now}
        if not _looks_like_youtube_jar(path):
            return {"status": STATUS_CONFIGURED, "configured": True, "usable": STATUS_UNKNOWN,
                    "has_youtube_entries": False, "last_checked": now}
        return {
            "status": STATUS_CONFIGURED,
            "configured": True,
            "usable": _remembered_usable(),
            "has_youtube_entries": True,
            # Age matters and is not a secret: an expired jar is the failure
            # mode this deployment actually hit.
            "age_days": round((now - path.stat().st_mtime) / 86400, 1),
            "last_checked": now,
        }
    return {"status": STATUS_MISSING, "configured": False, "usable": STATUS_INVALID,
            "last_checked": now}


_usable_memo: str = STATUS_UNKNOWN


def note_cookie_result(*, succeeded: bool) -> None:
    """Record what an authenticated attempt actually proved. Called by the
    acquisition path so `usable` reflects reality rather than file existence."""
    global _usable_memo
    _usable_memo = STATUS_USABLE if succeeded else STATUS_INVALID


def _remembered_usable() -> str:
    return _usable_memo


def reset() -> None:
    """Test seam."""
    global _usable_memo
    _usable_memo = STATUS_UNKNOWN


def acquisition_capabilities() -> dict:
    """Everything the acquisition ladder can call on, for diagnostics.

    Deliberately reports *counts and booleans only*. `proxy_candidates` is a
    number, never the list — proxy URLs in this deployment embed credentials.
    """
    import music_youtube_audio as yta

    cookie = youtube_cookie_status()
    try:
        proxies = yta.resolved_proxies(n=3)
    except Exception:
        proxies = []
    return {
        "cookie": cookie,
        "auth_capability": cookie["status"],
        "proxy_candidates": len(proxies),
        "proxy_available": bool(proxies),
    }
