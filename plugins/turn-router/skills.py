"""Turn router — skill retrieval half: L1 hard-trigger full inject (2k cap,
session dedup, bare-URL demote) / L2-5 lightweight hints / none.

Split out of __init__.py (single responsibility, no behavior change) except
for the documented 2026-09-21 bugfix in skip_skill_retrieval() below.
"""
from __future__ import annotations

import logging
import re
import threading
import time

logger = logging.getLogger(__name__)

_MAX_CONTENT_CHARS = 2000
_L1_COOLDOWN_S = 4 * 3600
_L1_SEEN: dict[str, dict[str, float]] = {}
_L1_LOCK = threading.Lock()

# Casual message guard: skip heavy skill retrieval for trivial turns (~700ms saved)
_CASUAL_MSG_RE = re.compile(
    r'^(?:o+k*|gg+|hi|hey|lol|ugh|hmm|idk|'
    r'[ㅇㄱ][ㄱㅇ]*|응+|넹?|넵?|엉|어+|오+|오케이?|'
    r'ㅎ{2,}|ㅋ{2,}|ㅠ{2,}|ㅜ{2,}|안녕|하이|고마워|감사|수고|잘자)[!?.~\s]*$',
    re.IGNORECASE,
)

_URL_RE = re.compile(r"https?://|youtu\.be|youtube\.com|m\.youtube")
_ASK_VERB_RE = re.compile(
    r"요약|정리|설명|알려|분석|번역|추출|만들|써줘|해줘|찾아|비교|리뷰|추천|계획|뭐야|뭔지|어떻|자막|대본|"
    r"summar|explain|analyz|translat|extract|compar|review|transcri",
    re.IGNORECASE,
)


def skip_skill_retrieval(msg: str) -> bool:
    """True if the expensive skill retrieval pass (~700ms) should be skipped.

    Skips only for messages matching the casual-ack pattern (응, ㄱㄱ, 안녕, ...).

    2026-09-21 fix, part 1: this used to also skip when `not rule_ctx and
    len(msg) < 25` (any short message rules_engine didn't tag as a rule-tier
    turn). That broke short real commands rules_engine has no tier for — e.g.
    "셀카 보여줘", "노트 검색해줘". Clause removed.

    2026-09-21 fix, part 2: the original `len(msg) <= _SKILL_SKIP_LEN` check
    (added in the same commit as part 1) has the same bug on its own — Korean
    is information-dense enough that "셀카 보여줘" (6 chars) is a complete,
    specific command, not a casual turn. A standalone length threshold can't
    tell those apart. `_CASUAL_MSG_RE` already anchors on whole-message casual
    patterns (all short by construction), so it alone is sufficient — the
    length check added nothing but false positives on short real commands.
    """
    return bool(_CASUAL_MSG_RE.match(msg))


def _l1_fresh_inject(session_id: str, skill_name: str) -> bool:
    if not session_id:
        return True
    now = time.time()
    with _L1_LOCK:
        seen = _L1_SEEN.setdefault(session_id, {})
        for name, ts in [x for x in seen.items() if now - x[1] >= _L1_COOLDOWN_S]:
            del seen[name]
        if skill_name in seen:
            return False
        seen[skill_name] = now
        for sid in [s for s, m in _L1_SEEN.items() if not m]:
            del _L1_SEEN[sid]
        return True


def skill_part(
    user_message: str,
    session_id: str,
    *,
    platform: str = "",
    profile: str = "",
) -> tuple[str | None, str]:
    """Returns (context_block_or_None, short_tag_for_log)."""
    try:
        from skill_retriever import get_skill_retriever
        retriever = get_skill_retriever()
        result = retriever.retrieve_detailed(
            user_message,
            top_k=3,
            platform=platform,
            profile=profile,
        )
        skills = result.get("skills", [])
        layer = result.get("layer", "none")
        skill_name = result.get("skill_name", skills[0] if skills else "")
        if not skills:
            return None, "skills:none"
        if layer == "L1":
            if _URL_RE.search(user_message) and not _ASK_VERB_RE.search(user_message):
                return (
                    f"## Skill available (not auto-loaded): {skill_name}\n"
                    f"[System note: the message is just a link with no explicit ask. "
                    f"Mention skill_view() only if the user wants more than a glance.]"
                ), f"skills:L1-demote:{skill_name}"
            if not _l1_fresh_inject(session_id, skill_name):
                return (
                    f"## Skill already loaded: {skill_name}\n"
                    f"[System note: the full content was injected earlier this "
                    f"session — refer to it above, or skill_view() to re-read.]"
                ), f"skills:L1-pointer:{skill_name}"
            content = retriever.get_skill_content(
                skill_name,
                platform=platform,
                profile=profile,
            )
            if not content:
                return None, "skills:L1-empty"
            if len(content) > _MAX_CONTENT_CHARS:
                content = (
                    content[:_MAX_CONTENT_CHARS]
                    + f"\n\n[... truncated to {_MAX_CONTENT_CHARS} chars — "
                    + f"skill_view('{skill_name}') for full content.]"
                )
            return (
                f"## Auto-loaded Skill: {skill_name}\n"
                f"[System note: This skill was automatically matched "
                f"via hard trigger. Use its instructions directly.]\n\n"
                f"{content}"
            ), f"skills:L1:{skill_name}({len(content)}ch)"
        hint = (
            "## Skill Retrieval Hint\n"
            "[System note: The following skills may be relevant to this query. "
            "Use your judgment — load via skill_view() if useful, "
            "or ignore and answer directly if none fit.]\n\n"
            + "\n".join(f"- {name}" for name in skills)
        )
        return hint, f"skills:L2-5:{len(skills)}"
    except Exception as e:
        logger.debug("turn-router skill part failed (non-fatal): %s", e)
        return None, "skills:error"
