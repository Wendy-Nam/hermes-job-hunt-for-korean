"""Session-start brief (cross-session continuity).

First turn of each user-chat session injects up to 3 one-line pointers to
recent sessions (titles + last activity). In-context history covers the
current session only; this covers earlier ones. Cron/subagent turns are
excluded — they start a fresh session on every tick and must not pay this
tax. Independent feature from triggers/state/queue, hence its own module.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

_LOG = logging.getLogger(__name__)

_DATA = Path(os.environ.get("HERMES_DATA", "/opt/data"))

_SEEN_SESSIONS: dict[str, float] = {}
_SEEN_TTL_S = 24 * 3600


def build_brief(session_id: str) -> str | None:
    now = time.time()
    for sid, ts in [x for x in _SEEN_SESSIONS.items() if now - x[1] > _SEEN_TTL_S]:
        del _SEEN_SESSIONS[sid]
    if not session_id or session_id in _SEEN_SESSIONS:
        return None
    _SEEN_SESSIONS[session_id] = now
    conn = None
    try:
        import sqlite3
        db = _DATA / "state.db"
        if not db.exists():
            return None
        conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=3)
        rows = conn.execute(
            "SELECT id, title, last_activity_description, message_count "
            "FROM sessions WHERE id != ? AND id NOT LIKE 'cron_%' "
            "AND COALESCE(message_count, 0) > 2 "
            "ORDER BY started_at DESC LIMIT 3",
            (session_id,),
        ).fetchall()
    except Exception as e:
        _LOG.debug("autonomous: brief query failed: %s", e)
        return None
    lines = []
    for sid, title, desc, n in rows:
        line = (title or "").strip()[:40]
        if not line and not desc:
            try:
                fm = conn.execute(
                    "SELECT substr(content, 1, 60) FROM messages "
                    "WHERE session_id = ? AND role = 'user' "
                    "ORDER BY rowid LIMIT 1",
                    (sid,),
                ).fetchone()
                if fm and fm[0]:
                    line = "“" + str(fm[0]).strip()[:60] + "”"
            except Exception:
                pass
        line = line or "(무제)"
        if desc:
            line += " — " + str(desc).strip()[:60]
        lines.append(f"- {line} ({n}턴)")
    try:
        if conn is not None:
            conn.close()
    except Exception:
        pass
    if not lines:
        return None
    return ("[Recent sessions — 이어서 말할 때 참조. 필요하면 snow_search/session_search로 원문 확인]\n"
            + "\n".join(lines))
