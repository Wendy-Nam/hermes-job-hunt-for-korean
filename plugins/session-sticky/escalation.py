"""Difficulty-aware auto-escalation: regex heuristics + Jev System One classifier.

Decides whether a request should be promoted from the base model to the
premium (commandcode) model, and ratchets that decision for a session so it
doesn't flap mid-conversation.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# Ensure _shared is in sys.path (jev_client lives there)
_SHARED_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_shared"))
if _SHARED_DIR not in sys.path:
    sys.path.insert(0, _SHARED_DIR)

# ── Escalation Configuration ─────────────────────────────────────────
ESCALATION_MODEL = "deepseek/deepseek-v4.1-flash"
ESCALATION_PROVIDER = "commandcode"
ESCALATION_BASE_URL = "https://api.commandcode.ai/provider/v1"

# Default (free) model that gets escalated FROM
BASE_MODEL = "solar-pro4"

# Session ratchet TTL: once escalated, stay escalated for this long (seconds)
_RATCHET_TTL = 600  # 10 minutes

# Active escalated sessions: {session_id: expiry_timestamp}
_escalated_sessions: dict[str, float] = {}
_escalated_lock = threading.Lock()

# ── Heuristic regex patterns ──────────────────────────────────────────
# Strong technical/code indicators: instant escalation without remote API call
_CODE_PATTERN = re.compile(
    r"(?:"
    r"```"                                    # Code blocks
    r"|\b(?:def|class|async\s+def|lambda)\b"  # Python constructs
    r"|\b(?:import|from\s+\w+\s+import)\b"   # Imports
    r"|\b(?:const|let|var|function|=>)\b"     # JS/TS
    r"|\b(?:SELECT|INSERT|UPDATE|DELETE)\b"   # SQL
    r"|\b(?:git\s+(?:status|commit|push|diff|log|branch))\b"
    r"|\b(?:docker|kubectl|ssh|curl|bash|zsh|pytest)\b"
    r"|(?:코드|함수|리팩토링|버그|에러|오류|트레이스|스택트레이스|아키텍처|파이프라인|스키마|쿼리|스크립트|컴포넌트|엔드포인트)"
    r")",
    re.IGNORECASE,
)

# Obvious casual chat: skip escalation immediately (0ms)
_CASUAL_PATTERN = re.compile(
    r"^(?:안녕|하이|ㅎ[ㅎ]*|ㅋ[ㅋ]*|고마워|수고|잘자|좋은\s*아침|오늘\s*뭐해|점심|날씨|노래\s*추천|심심해|응|그래|오키|ㅇㅇ)[!?.~\s]*$",
    re.IGNORECASE,
)


def _is_session_escalated(session_id: str) -> bool:
    """Check if this session has been ratchet-escalated and is still valid."""
    if not session_id:
        return False
    with _escalated_lock:
        expiry = _escalated_sessions.get(session_id)
        if expiry is None:
            return False
        if time.time() > expiry:
            del _escalated_sessions[session_id]
            return False
        return True


def _mark_session_escalated(session_id: str) -> None:
    """Ratchet: mark this session as escalated for _RATCHET_TTL seconds."""
    if not session_id:
        return
    with _escalated_lock:
        _escalated_sessions[session_id] = time.time() + _RATCHET_TTL
        now = time.time()
        expired = [k for k in list(_escalated_sessions.keys())[:50] if _escalated_sessions[k] < now]
        for k in expired:
            del _escalated_sessions[k]


def should_escalate(text: str, session_id: str) -> bool:
    """Decide whether to escalate this request to the premium model."""
    if _is_session_escalated(session_id):
        return True

    text_clean = text.strip()
    if len(text_clean) < 5:
        return False

    # 1. Fast path: obvious casual chat
    if _CASUAL_PATTERN.match(text_clean):
        return False

    # 2. Fast path: strong code/technical regex match
    if _CODE_PATTERN.search(text_clean):
        logger.info("Auto-escalation triggered by regex match: session=%s", session_id[:8] if session_id else "none")
        _mark_session_escalated(session_id)
        return True

    # 3. Ambiguous queries: evaluate with Jev System One
    try:
        from jev_client import score_complexity
        score = score_complexity(text_clean, timeout=2.5)
        if score is not None and score >= 0.70:
            logger.info(
                "Auto-escalation triggered by Jev System One: score=%.2f >= 0.70 (session=%s)",
                score, session_id[:8] if session_id else "none",
            )
            _mark_session_escalated(session_id)
            return True
    except Exception as exc:
        logger.debug("Jev escalation check failed (non-fatal): %s", exc)

    return False


def extract_user_text(request: dict[str, Any]) -> str:
    """Extract the last user message text from the request's messages array."""
    messages = request.get("messages") or []
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        parts.append(part.get("text", ""))
                return " ".join(parts)
    return ""
