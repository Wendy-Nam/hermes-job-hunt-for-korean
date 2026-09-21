"""Turn router — one pre_llm_call hook for skills + rules.

Merges the former eagle-eye (skill retrieval) and conditional-rules (tiered
rule injection) plugins, which did the same job twice: read the user message,
run regexes, inject context. One pass, one context block, one log line.

Behavior is unchanged from the two originals — only the plumbing merged:
  skills: L1 hard-trigger full inject (2k cap, session dedup, bare-URL demote)
          / L2-5 lightweight hints / none
  rules:  delegation-lite/full + secondbrain + job-rules-lite + routing-full
          stacked by tier, cron/subagent turns skipped

Module layout (single responsibility, no behavior change except the one
documented 2026-09-21 fix in skills.skip_skill_retrieval):
- skills.py: skill retrieval (L1 full-inject / L2-5 hints / casual-turn skip)
- rules.py: dynamic-loads rules_engine.py + per-session rule-set dedup
- rules_engine.py: 3-tier rule table + module file reads (unchanged, sibling
  file already single-responsibility)
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_PLUGIN_DIR = Path(__file__).resolve().parent
_DATA_HOME = Path(os.environ.get("HERMES_DATA", "/opt/data"))
_VENDOR = Path(os.environ.get("HERMES_PLUGIN_VENDOR", "/opt/data/python-site"))
for _p in (_PLUGIN_DIR, _VENDOR):
    if _p.exists() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

_DISABLE_ENV = "HERMES_DISABLE_SKILL_RETRIEVAL"

import rules  # noqa: E402
import skills  # noqa: E402


def _on_pre_llm_call(
    *,
    user_message: str = "",
    session_id: str = "",
    turn_id: str = "",
    platform: str = "",
    profile: str = "",
    **_kwargs,
) -> dict | None:
    if os.environ.get(_DISABLE_ENV, "").lower() in ("1", "true", "yes"):
        return None
    if not user_message or not user_message.strip():
        return None
    platform = platform or _kwargs.get("platform", "")
    profile = profile or _kwargs.get("profile", "") or _kwargs.get("profile_name", "")
    if platform in ("cron", "subagent"):
        return None
    rule_ctx, rule_tag = rules.rule_part(user_message, session_id)

    _msg = user_message.strip()
    if skills.skip_skill_retrieval(_msg):
        skill_ctx, skill_tag = None, "skills:skip-casual"
    else:
        skill_ctx, skill_tag = skills.skill_part(
            user_message,
            session_id,
            platform=platform,
            profile=profile,
        )
    if not rule_ctx and not skill_ctx:
        return None
    blocks = [b for b in (rule_ctx, skill_ctx) if b]
    logger.info("turn-router %s %s", rule_tag, skill_tag)
    return {"context": "\n\n".join(blocks)}


def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    # Warm the retriever now so the first real turn doesn't pay embedding
    # load inside the hook timeout (background thread, non-blocking).
    try:
        from skill_retriever import get_skill_retriever
        get_skill_retriever()
    except Exception as e:
        logger.debug("turn-router warmup failed (non-fatal): %s", e)
    logger.info("turn-router plugin registered (pre_llm_call hook)")


if __name__ == "__main__":
    for q in ["셀카 보내줘", "https://youtu.be/abc", "구직 공고 찾아줘", "잡담 재밌었어", "위임해서 단계별로 처리해"]:
        r = _on_pre_llm_call(user_message=q, session_id="t", platform="discord")
        ru = rules.rule_part(q)
        sk = skills.skill_part(q, "t")
        print(f"{q[:28]!r:32} -> {ru[1]} | {sk[1]}")
