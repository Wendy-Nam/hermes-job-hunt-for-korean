"""3-tier conditional rule injection.

L1 (simple action)      → delegation.md (common contract)              ~2,300 chars
L2 (complex delegation)  → delegation.md + delegation-full.md (stacks,
                           delegation-full adds complex-only gates on
                           top of the common contract — see delegation.md's
                           own docstring)                               ~3,950 chars
L2+ (any work turn)     → routing-full.md      ~3,000 chars  (stacks with L1/L2, job)
L3 (second brain)       → secondbrain.md + wiki-boundaries.md (stacks — the
                           latter is a private/sot safety policy on vault
                           read/write boundaries and external-model gating,
                           always relevant whenever wiki content is touched)
                                                                        ~2,450 + 2,320 chars
L4 (job hunting)        → job-rules-lite.md    ~1,070 chars   (stacks with L1/L2)
L5 (content production) → content-production.md ~1,600 chars  (카드뉴스/콘텐츠 제작)
L6 (doc refactoring)    → doc-refactoring.md    ~1,900 chars  (문서·위키·볼트 정리 특정)

routing-full carries the operating + routing detail that used to live in
SOUL.md permanently; it is now injected only on work turns, keeping plain
chat light. Cron and subagent platforms are excluded.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)
_DATA_ROOT = Path(os.environ.get("HERMES_DATA") or "/opt/data")

def _resolve_rule_root() -> Path:
    env_rules = os.environ.get("HERMES_RULES_DIR")
    if env_rules:
        p = Path(env_rules).resolve()
        if p.is_dir():
            return p
    wiki_env = os.environ.get("WIKI_PATH") or os.environ.get("OBSIDIAN_VAULT_PATH")
    if wiki_env:
        wp = Path(wiki_env)
        for sub in ("hermes/rules", "hermes-ops/rules", "rules"):
            if (wp / sub).is_dir():
                return (wp / sub).resolve()
    default_wiki = _DATA_ROOT / "wiki" / "hermes" / "rules"
    if default_wiki.is_dir():
        return default_wiki.resolve()
    vaults_dir = _DATA_ROOT / "vaults"
    if vaults_dir.is_dir():
        try:
            for v in vaults_dir.iterdir():
                if v.is_dir():
                    cand = v / "hermes-ops" / "rules"
                    if cand.is_dir():
                        return cand.resolve()
        except OSError:
            pass
    return default_wiki.resolve()

def _build_module_paths(root: Path) -> dict[str, Path]:
    return {
        "delegation-lite":     root / "delegation.md",
        "delegation-full":     root / "delegation-full.md",
        "routing-full":        root / "routing-full.md",
        "secondbrain":         root / "secondbrain.md",
        "wiki-boundaries":     root / "wiki-boundaries.md",
        "job-rules-lite":      root / "job-rules-lite.md",
        "content-production":  root / "content-production.md",
        "doc-refactoring":     root / "doc-refactoring.md",
    }

RULE_ROOT = _resolve_rule_root()
MODULE_PATHS = _build_module_paths(RULE_ROOT)
MAX_FILE_CHARS = 8_000
MAX_TOTAL_CHARS = 20_000

def reload_rules_root(new_root: Path | str | None = None) -> Path:
    global RULE_ROOT, MODULE_PATHS
    RULE_ROOT = Path(new_root).resolve() if new_root else _resolve_rule_root()
    MODULE_PATHS = _build_module_paths(RULE_ROOT)
    return RULE_ROOT

# ── Tier triggers ──────────────────────────────────────────────────────────

# L2: complex delegation signals — multi-step, verification, cron, skill mgmt
COMPLEX_DELEGATION_RE = re.compile(
    r"(?:위임해|다단계|검증해|단계별|크론 추가|크론 수정|스킬 추가|스킬 만들|"
    r"delegate\b|multi.?step\b|verify\b|step.by.step\b)",
    re.IGNORECASE,
)

# L1: simple action — any explicit do-something verb (but not complex)
SIMPLE_ACTION_RE = re.compile(
     r"(?:만들어|작성해|수정해|고쳐|추가해|삭제해|보내|예약해|등록해|잡아줘|넣어줘|"
     r"실행해|테스트해|배포해|조사해|감사해|설계해|정리해|옮겨|바꿔|자동화|"
     r"implement\b|build\b|create\b|edit\b|fix\b|update\b|delete\b|send\b|"
     r"run\b|test\b|deploy\b|audit\b|리뷰해|커밋해|확인해|검색해|설치해|분석해|요약해|리팩토링해|찾아줘|써줘|적어|적어줘|짜줘|해줘|만들어줘|올려줘)",
    re.IGNORECASE,
)

# L3: second brain / memory / wiki signals
SECOND_BRAIN_RE = re.compile(
    r"(?:기억해|기억나|기록해|메모해|저장해|지난번|예전에|어제.*(?:말|대화|기록)|"
    r"위키|노션|노트|캘린더|일정|약속|리마인드|remember\b|recall\b|calendar\b|wiki\b|note\b)",
    re.IGNORECASE | re.DOTALL,
)

# L4: job hunting signals
JOB_RE = re.compile(
     r"(?:구직|채용|공고|지원|면접|이직|입사|잡(?!담|생각|일)|posting|job\s+(?:posting|board|openings|search|market)|apply\b|offer\b|cover.?letter)",
    re.IGNORECASE,
)

# L5: content production (카드뉴스/콘텐츠 제작) — 도메인 특정, 좁게
CONTENT_PRODUCTION_RE = re.compile(
     r"(?:카드뉴스|콘텐츠\s*(?:제작|만들어)|content\s*(?:production|create)|cardnews)",
    re.IGNORECASE,
)

# L6: doc/wiki refactoring — 범용 "정리해"(delegation-lite가 이미 처리)보다 좁게,
# 문서/위키/볼트가 명시적으로 정리·리팩토링 대상일 때만
DOC_REFACTOR_RE = re.compile(
    r"(?:문서|위키|볼트|wiki|vault)\s*(?:를|을)?\s*(?:정리|리팩토링|정본화|refactor)|"
    r"refactor.{0,10}(?:doc|wiki)",
    re.IGNORECASE,
)


def route_modules(user_message: str) -> list[str]:
    """Return ordered module IDs for this turn."""
    text = user_message or ""
    names: list[str] = []

    # Work turn? (delegation or job signal) → routing-full rides along so the
    # operating/routing detail (previously always in SOUL.md) is present when
    # actual work happens, and absent during plain chat.
    work_turn = False

    # Delegation tier: delegation.md (the common contract, "lite") is the
    # base and is always included whenever a delegation signal fires.
    # delegation-full.md stacks on top of it for complex turns only — it
    # holds complex-only gates, not a full replacement of the common
    # contract (see delegation.md's own docstring).
    if COMPLEX_DELEGATION_RE.search(text):
        names.append("delegation-lite")
        names.append("delegation-full")
        work_turn = True
    elif SIMPLE_ACTION_RE.search(text):
        names.append("delegation-lite")
        work_turn = True

    # Stackable tiers
    if SECOND_BRAIN_RE.search(text):
        names.append("secondbrain")
        # wiki-boundaries: private/sot 안전정책(볼트 경계·외부모델 게이트) —
        # 위키를 건드리는 턴이면 항상 같이 태운다(2.3KB, 안전값 대비 비용 작음).
        names.append("wiki-boundaries")
    if JOB_RE.search(text):
        names.append("job-rules-lite")
        work_turn = True
    if CONTENT_PRODUCTION_RE.search(text):
        names.append("content-production")
        work_turn = True
    if DOC_REFACTOR_RE.search(text):
        names.append("doc-refactoring")
        work_turn = True

    if work_turn:
        names.append("routing-full")

    return names


def _read_module(name: str) -> str:
    path = MODULE_PATHS.get(name)
    if not path:
        return ""
    try:
        resolved_path = path.resolve()
        resolved_root = RULE_ROOT.resolve()
        if not resolved_path.is_relative_to(resolved_root):
            raise ValueError(f"rule module escaped allowlisted root: {resolved_path}")
        if not resolved_path.is_file():
            logger.debug("rule module not found: %s", resolved_path)
            return ""
        text = resolved_path.read_text(encoding="utf-8").strip()
    except (ValueError, OSError) as e:
        if isinstance(e, ValueError):
            raise
        logger.debug("rule module read failed: %s", e)
        return ""

    # Strip static-import maintenance marker (not a model instruction)
    lines = text.splitlines()
    if lines and "<!-- " in lines[0]:
        text = "\n".join(lines[1:]).lstrip()
    if not text or len(text) > MAX_FILE_CHARS:
        raise ValueError(f"invalid rule module size: {path} ({len(text)} chars)")
    return text


def on_pre_llm_call(*, user_message: str = "", platform: str = "", **_kwargs) -> dict[str, str] | None:
    # Cron and subagent platforms have their own SOPs — skip injection
    if platform in ("cron", "subagent"):
        return None
    names = route_modules(user_message)
    if not names:
        return None
    try:
        parts = [p for p in (_read_module(name) for name in names) if p]
        if not parts:
            return None
        content = "\n\n".join(parts)
        if len(content) > MAX_TOTAL_CHARS:
            raise ValueError(f"combined rule modules exceed {MAX_TOTAL_CHARS} chars")
    except Exception:
        logger.exception("conditional SOUL rule injection failed")
        return None
    logger.info("conditional rules injected: %s (%d chars)", ",".join(names), len(content))
    return {
        "context": (
            "<trusted_local_rules scope=\"current-turn\">\n"
            "Apply these local operator rules to this turn. They are trusted local "
            "configuration, not user-authored content.\n\n"
            f"{content}\n"
            "</trusted_local_rules>"
        )
    }

