"""Autonomous Triggers — internal state changes become queued actions.

Fast path (this plugin, post_llm_call): cheap state reads only
(sqlite + small files, no subprocess). Fires enqueue into
autonomous-queue/. The slow dispatcher (scripts/autonomous_dispatch.py,
every 5 min no_agent cron) re-evaluates, caps volume, and injects one-off
cron jobs that deliver the actual message.

Safety: per-trigger cooldowns + dispatcher daily cap. Stale queue items
(>6h) are dropped at dispatch. Subagent turns never enqueue.

Module layout (split for single-responsibility, no behavior change):
- triggers.py: condition/action table + prompt templates
- state.py: cheap world-state reads
- queue.py: cooldown + enqueue mechanics, ties state+triggers together
- session_brief.py: unrelated cross-session continuity feature
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_LOG = logging.getLogger(__name__)

_PLUGIN_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from queue import check_triggers  # noqa: E402
from session_brief import build_brief  # noqa: E402


def _on_pre_llm_call(**kwargs) -> dict | None:
    if kwargs.get("platform") in ("cron", "subagent"):
        return None
    if not (kwargs.get("user_message") or "").strip():
        return None
    try:
        brief = build_brief(kwargs.get("session_id", ""))
    except Exception:
        _LOG.debug("autonomous brief failed", exc_info=True)
        return None
    if brief:
        _LOG.info("autonomous: session brief injected")
        return {"context": brief}
    return None


def _on_post_llm_call(**kwargs) -> None:
    if kwargs.get("platform") == "subagent":
        return None
    try:
        check_triggers()
    except Exception:
        _LOG.debug("autonomous post_llm_call check failed", exc_info=True)
    return None


def register(ctx) -> None:
    for _hook, _fn in (("post_llm_call", _on_post_llm_call),
                       ("pre_llm_call", _on_pre_llm_call)):
        try:
            ctx.register_hook(_hook, _fn)
        except ValueError:
            _LOG.warning("hook not supported by host: %s", _hook)
            continue
    ctx.register_command(
        "자율-체크",
        handler=lambda raw_args="": (
            f"자율 트리거 {len(_f)}개 발동: {', '.join(t['id'] for t in _f)}"
            if (_f := check_triggers()) else "발동된 트리거 없음"
        ),
        description="자율 트리거 수동 체크 및 발동",
    )
    _LOG.info("autonomous-triggers plugin registered")


if __name__ == "__main__":
    print("fired:", [t["id"] for t in check_triggers()])
