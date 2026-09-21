"""Turn router — rule-tier half: dynamic-loads rules_engine.py (tier table +
module content) and dedups the injected rule set across consecutive turns.

Split out of __init__.py (single responsibility, no behavior change).
"""
from __future__ import annotations

import importlib.util
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_PLUGIN_DIR = Path(__file__).resolve().parent
_CR_FILE = _PLUGIN_DIR / "rules_engine.py"

# Rule dedup: skip re-injecting the identical rule set on a consecutive turn.
# Rules are stamped onto the current user message's api_content sidecar and
# replayed byte-for-byte, so the prior turn's rules stay visible in context;
# re-emitting the same set would only grow the prompt (same rationale as OMH's
# primer dedup in llm_hooks.py).
_RULE_SEEN: dict[str, tuple[str, ...]] = {}
_RULE_LOCK = threading.Lock()


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_CR = None


def _rules():
    global _CR
    if _CR is None:
        try:
            _CR = _load("turn_router_rules", _CR_FILE)
        except Exception as e:
            logger.debug("turn-router: rule module unavailable: %s", e)
    return _CR


def rule_part(user_message: str, session_id: str = "") -> tuple[str | None, str]:
    cr = _rules()
    if cr is None:
        return None, "rules:unavailable"
    try:
        names = cr.route_modules(user_message)
        if not names:
            return None, "rules:none"
        key = tuple(names)
        if session_id:
            with _RULE_LOCK:
                prev = _RULE_SEEN.get(session_id)
                _RULE_SEEN[session_id] = key
            if prev == key:
                return None, f"rules:dedup({','.join(names)})"
        parts = [p for p in (cr._read_module(name) for name in names) if p]
        if not parts:
            return None, "rules:none"
        content = "\n\n".join(parts)
        if len(content) > cr.MAX_TOTAL_CHARS:
            raise ValueError("combined rule modules exceed cap")
        return (
            "<trusted_local_rules scope=\"current-turn\">\n"
            "Apply these local operator rules to this turn. They are trusted local "
            "configuration, not user-authored content.\n\n"
            f"{content}\n"
            "</trusted_local_rules>"
        ), f"rules:{','.join(names)}({len(content)}ch)"
    except Exception:
        logger.exception("turn-router rule part failed")
        return None, "rules:error"
