"""Skill retrieval: 5-layer architecture.


Layer 1: Hard triggers — exact keyword → direct return (100% deterministic)
Layer 2: FTS5 BM25 — clean text search (name + description only)
Layer 3: Synonym dictionary — independent bonus scoring layer
Layer 4: Dense Embedding — sentence-transformers cosine similarity
Layer 5: RRF fusion — combine layers 2-4, return top-k

Usage:
    retriever = get_skill_retriever()
    if retriever.is_ready():
        hints = retriever.retrieve("help me debug this", top_k=5)
"""

import logging
import math
import os
import re
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────
_DISABLE_ENV = "HERMES_DISABLE_SKILL_RETRIEVAL"
_TOP_K_ENV = "HERMES_SKILL_RETRIEVAL_TOP_K"

# RRF parameters
_RRF_K = 60
_RRF_W_FTS5 = 0.45      # FTS5 text matching
_RRF_W_SYN = 0.35       # Synonym dictionary
_RRF_W_EMB = 0.20       # Dense embedding
# Minimum RRF score to include (filters noise)
_MIN_RRF_SCORE = 0.003
# Confidence threshold: top-1 must score above this to be returned.
# 절대값으로 박으면 안 된다 — RRF 점수의 상한은 레이어 가중치와 k에 묶여 있어서
# (모든 레이어가 1위로 동의해야 1.0/(k+1) ≈ 0.0164), 0.015는 사실상 '전 레이어 만장일치'였다.
# 임베딩 모델이 없는 설치본(sentence-transformers 미설치가 기본)에서는 상한이 0.0131로 내려가
# **어떤 완벽한 매치도 통과 못 하고 L2~5가 통째로 죽는다.** 그래서 '이론상 최대의 몇 %'로 표현한다.
# 실측(라이브 103스킬): 무관한 잡담 쿼리는 전부 0.0000, 맞는 쿼리는 56~100% → 35%면 안전하게 갈린다.
_CONFIDENCE_FRACTION = 0.35
_MAX_TOP1 = (_RRF_W_FTS5 + _RRF_W_SYN + _RRF_W_EMB) / (_RRF_K + 1)
_CONFIDENCE_THRESHOLD = _CONFIDENCE_FRACTION * _MAX_TOP1  # ≈ 0.0057

_SINGLETON: "SkillRetriever | None" = None
_SINGLETON_LOCK = threading.Lock()

# ── Hard Triggers (Layer 1) ────────────────────────────────────
# Maps exact substring → skill name. Order matters: first match wins.
# These bypass all ranking — if query contains the trigger, return directly.
#
# CUSTOMIZATION: Replace the examples below with your own skill mappings.
# Run `python scripts/generate_config.py` to auto-generate from your skill library.
# See templates/hard_triggers.example.py for the format.
_HARD_TRIGGERS: list[tuple[str, str]] = [
    # >>> user-managed daily triggers (edit here when adding a small, high-frequency skill)
    ("일기 써", "daily-journaling"),
    ("일기 쓸", "daily-journaling"),
    ("일기 쓰자", "daily-journaling"),
    ("저널링", "daily-journaling"),
    ("오늘 하루 기록", "daily-journaling"),
        ("원티드", "wanted-search"),
    ("채용공고 찾", "wanted-search"),
    ("구직 공고", "wanted-search"),
    ("채용 검색", "wanted-search"),
    ("공고 찾아", "wanted-search"),
    ("링크드인", "linkedin-search"),
    ("linkedin", "linkedin-search"),
    ("인턴 공고", "linkedin-search"),
    ("인턴십", "linkedin-search"),
    ("신입 공고", "linkedin-search"),
    ("신입 채용", "linkedin-search"),
    ("채용 알림", "job-alert-mail"),
    ("잡 알림", "job-alert-mail"),
    ("알림 메일", "job-alert-mail"),
    ("유사공고", "job-match"),
    ("비슷한 공고", "job-match"),
    ("사람인", "kr-search"),
    ("잡코리아", "kr-search"),
    ("국내 채용", "kr-search"),
    ("적합도", "job-match"),
    ("이력서 매칭", "job-match"),
    ("공고 매칭", "job-match"),
    ("지원할만", "job-match"),
    ("apify", "apify-search"),
    ("아파이", "apify-search"),
    ("스크래퍼 폴백", "apify-search"),
    ("리멤버", "apify-search"),
    ("외국계", "ats-search"),
    ("그린하우스", "ats-search"),
    ("greenhouse", "ats-search"),
    ("ats 채용", "ats-search"),
# <<< user-managed daily triggers
    # ── Core daily assistant ──

    # Proactive openers / 선톡 (context-first)
    ("선톡 크론", "efficient-cron-jobs"),
    ("선톡 게이트", "efficient-cron-jobs"),
    ("선톡 스크립트", "efficient-cron-jobs"),
    ("선톡 random", "efficient-cron-jobs"),
    ("선톡", "contextual-proactive-messaging"),
    ("프로액티브 오프너", "contextual-proactive-messaging"),
    ("proactive opener", "contextual-proactive-messaging"),

    # ── Trigger-only: daily/news/creative ──

    # ── Trigger-only: transit / fun ──
    # Broad transport nouns catch casual subway wording. Avoid generic
    # "도착/언제/몇 분" alone because they over-route normal chat.

    # ── Trigger-only: media/docs/research/tools ──

    # ── Engineering ──
]


def _is_subsequence(chars: list[str], text: str, max_gap: int = 3) -> bool:
    """Check if all chars appear in order within text, with max gap between consecutive matches.

    >>> _is_subsequence(['d', 'e', 'b', 'u', 'g'], 'please debug this')
    True
    >>> _is_subsequence(['d', 'b', 'g'], 'debug')
    False  # 'b' appears before 'd' in subsequence order
    >>> _is_subsequence(['技', '能', '指', '南'], '梳理一下现有的技能，弄个说明指南')
    False  # gap of 4 between 能 and 指 exceeds max_gap=3
    """
    pos = -1  # position of last match
    for c in chars:
        idx = text.find(c, pos + 1)
        if idx == -1:
            return False
        if pos >= 0 and idx - pos - 1 > max_gap:
            return False
        pos = idx
    return True


def get_skill_retriever() -> "SkillRetriever":
    """Return the global singleton, creating it on first call."""
    global _SINGLETON
    if _SINGLETON is None:
        with _SINGLETON_LOCK:
            if _SINGLETON is None:
                _SINGLETON = SkillRetriever()
    return _SINGLETON


class SkillRetriever:
    """5-layer skill retrieval: Hard Trigger → FTS5 → Synonym → Embedding → RRF."""

    def __init__(self) -> None:
        self._ready = False
        self._loading = False
        self._error: str | None = None
        self._skill_names: list[str] = []
        self._skill_descs: list[str] = []
        self._doc_tokens: list[list[str]] = []
        self._idf: dict[str, float] = {}
        # Synonym structures
        self._synonyms: dict[str, list[str]] = {}       # skill → [syn, ...]
        self._synonym_index: dict[str, list[tuple[str, float]]] = {}  # token → [(skill, weight)]
        self._skill_paths: dict[str, Path] = {}          # skill_name → SKILL.md path
        # Embedding
        self._emb_matrix = None
        self._model = None
        self._jieba_initialized = False
        self._top_k = int(os.environ.get(_TOP_K_ENV, "5"))
        self._init_lock = threading.Lock()   # 백그라운드 스레드와 lazy 경로가 동시에 init 하면
                                            # 리스트가 두 번 쌓여 인덱스가 어긋난다(→ 전 쿼리 사망)
        # Start background initialization immediately
        threading.Thread(target=self._lazy_init, daemon=True).start()

        if os.environ.get(_DISABLE_ENV, "").lower() in ("1", "true", "yes"):
            logger.info("Skill retrieval disabled via %s", _DISABLE_ENV)
            return

    def is_ready(self) -> bool:
        return self._ready

    def is_loading(self) -> bool:
        return self._loading

    def error(self) -> str | None:
        return self._error

    def retrieve(self, query: str, top_k: int | None = None) -> list[str]:
        """Return top-k skill names matching the query.

        For detailed match info (including layer and content), use
        ``retrieve_detailed()`` instead.
        """
        result = self.retrieve_detailed(query, top_k)
        return result["skills"]

    def retrieve_detailed(self, query: str, top_k: int | None = None) -> dict:
        """Return detailed match info: skills, layer, and content.

        Returns:
            {
                "skills": ["skill-name", ...],
                "layer": "L1" | "L2-5" | "none",
                "skill_name": "skill-name"  # only for L1
            }
        """
        if not query or not query.strip():
            return {"skills": [], "layer": "none"}

        # ── Layer 1: Hard triggers (instant return, no ranking) ──
        hard_hit = self._hard_trigger(query)
        if hard_hit:
            logger.info("Skill retriever L1 hard trigger → %s", hard_hit)
            return {"skills": [hard_hit], "layer": "L1", "skill_name": hard_hit}

        # ── Layers 2-5: Full retrieval pipeline ──
        if not self._ready:
            if not self._loading:
                logger.info("Skill retriever: initializing on first call...")
                self._lazy_init()
            else:
                for _ in range(300):
                    if not self._loading:
                        break
                    time.sleep(0.1)
            if not self._ready:
                logger.warning("Skill retriever not ready after init attempt")
                return {"skills": [], "layer": "none"}

        k = top_k or self._top_k
        try:
            result = self._retrieve_inner(query, k)
            if result:
                logger.info("Skill retriever: %d skills for [%s]", len(result), query[:50])
            return {"skills": result, "layer": "L2-5" if result else "none"}
        except Exception as e:
            logger.warning("Skill retrieval failed: %s", e)
            return {"skills": [], "layer": "none"}

    def get_skill_content(self, skill_name: str) -> str | None:
        """Read and return the full SKILL.md content for a skill."""
        # Try cached path first
        path = self._skill_paths.get(skill_name)
        if path and path.exists():
            try:
                return path.read_text(encoding="utf-8")
            except Exception:
                pass

        # Fallback: search skills directories directly
        from hermes_constants import get_hermes_home

        for base_dir in [
            get_hermes_home() / "skills",
            get_hermes_home() / "hermes-agent" / "skills",
            Path("/opt/hermes/skills"),
        ]:
            if not base_dir.exists():
                continue
            # Pattern 1: skills/<skill_name>/SKILL.md (flat structure)
            direct = base_dir / skill_name / "SKILL.md"
            if direct.exists():
                try:
                    return direct.read_text(encoding="utf-8")
                except Exception:
                    pass
            # Pattern 2: skills/<category>/<skill_name>/SKILL.md (nested)
            for cat_dir in base_dir.iterdir():
                if not cat_dir.is_dir():
                    continue
                skill_md = cat_dir / skill_name / "SKILL.md"
                if skill_md.exists():
                    try:
                        return skill_md.read_text(encoding="utf-8")
                    except Exception:
                        pass
        return None

    @staticmethod
    def _hard_trigger(query: str) -> str | None:
        """Layer 1: 3-tier matching against hard trigger table.

        Tier 1: Exact substring match (fastest, 100% precise)
                 Collects ALL matches, picks longest trigger (most specific),
                 then earliest position in query (closest to intent).
        Tier 2: Subsequence match — trigger CJK chars appear in order in query
                 with max 3-char gap between consecutive matched chars.
        Tier 3: Regex fuzzy — trigger segments with optional gaps (0-3 chars)
        """
        q = query.strip()
        if not q:
            return None

        # Tier 1: Collect ALL exact substring matches, pick the best one
        tier1_matches: list[tuple[str, str, int]] = []  # (trigger, skill, position)
        for trigger, skill in _HARD_TRIGGERS:
            idx = q.find(trigger)
            if idx != -1:
                tier1_matches.append((trigger, skill, idx))

        if tier1_matches:
            # Longest trigger wins (most specific), ties broken by earliest position
            tier1_matches.sort(key=lambda x: (-len(x[0]), x[2]))
            return tier1_matches[0][1]

        # Tier 2+3: Only for triggers with 2+ CJK characters
        #           and NO ASCII letters (ASCII-containing triggers like
        #           "Python数据" are too ambiguous for fuzzy matching)
        cjk_pattern = re.compile('[\u4e00-\u9fff]')
        ascii_pattern = re.compile(r'[a-zA-Z]')
        for trigger, skill in _HARD_TRIGGERS:
            cjk_chars = cjk_pattern.findall(trigger)
            if len(cjk_chars) < 2:
                continue
            # Skip fuzzy matching for mixed CJK/ASCII triggers
            if ascii_pattern.search(trigger):
                continue

            # Tier 2: Subsequence — all CJK chars of trigger appear in order
            if _is_subsequence(cjk_chars, q, max_gap=3):
                logger.debug("L1 subsequence match: %r in %r → %s", trigger, q, skill)
                return skill

            # Tier 3: Regex fuzzy — insert .{0,3} between trigger segments
            segments = re.findall('[\u4e00-\u9fff]+', trigger)
            if len(segments) >= 2:
                pattern = r'.{0,3}'.join(re.escape(s) for s in segments)
                try:
                    if re.search(pattern, q):
                        logger.debug("L1 regex fuzzy match: %r ~ %r → %s", pattern, q, skill)
                        return skill
                except re.error:
                    pass

        return None

    # ── Initialization ──────────────────────────────────────────

    def _lazy_init(self) -> None:
        """멱등해야 한다. __init__의 백그라운드 스레드와 retrieve 경로가 동시에/연달아 부르는데,
        _load_skills·_build_fts5_index가 리스트에 append만 하던 탓에 두 번 돌면
        _doc_tokens가 _skill_names보다 길어져 _fts5_search가 IndexError를 냈다.
        그 예외는 상위 except가 삼켜서 L2~5가 통째로 빈 결과를 반환했다(하드트리거만 사는 것처럼 보임)."""
        with self._init_lock:
            if self._ready:
                return
            self._loading = True
            try:
                t0 = time.time()
                # 재시도·중복 호출에 대비해 누적 상태를 비우고 새로 만든다
                self._skill_names.clear()
                self._skill_descs.clear()
                self._doc_tokens.clear()
                self._idf.clear()
                self._synonyms.clear()
                self._synonym_index.clear()
                self._skill_paths.clear()
                self._emb_matrix = None
                self._load_skills()
                self._load_synonyms()
                self._build_fts5_index()
                self._load_embedding_model()
                t1 = time.time()
                self._ready = True
                logger.info(
                    "Skill retriever ready: %d skills, %d synonyms, %.1fs",
                    len(self._skill_names),
                    sum(len(v) for v in self._synonyms.values()),
                    t1 - t0,
                )
            except Exception as e:
                self._error = str(e)
                logger.warning("Skill retriever init failed: %s", e)
            finally:
                self._loading = False

    def _load_skills(self) -> None:
        from hermes_constants import get_hermes_home
        skills_dir = get_hermes_home() / "skills"
        hermes_agent_skills = get_hermes_home() / "hermes-agent" / "skills"
        source_skills = Path("/opt/hermes/skills")

        seen = set()
        for base_dir in [skills_dir, hermes_agent_skills, source_skills]:
            if not base_dir.exists():
                continue
            for cat_dir in sorted(base_dir.iterdir()):
                if not cat_dir.is_dir():
                    continue
                for skill_dir in sorted(cat_dir.iterdir()):
                    if not skill_dir.is_dir():
                        continue
                    skill_md = skill_dir / "SKILL.md"
                    if not skill_md.exists():
                        continue
                    name = skill_dir.name
                    if name in seen:
                        continue
                    try:
                        content = skill_md.read_text(encoding="utf-8")
                        desc = self._extract_description(content)
                        seen.add(name)
                        self._skill_names.append(name)
                        self._skill_descs.append(desc)
                        self._skill_paths[name] = skill_md
                    except Exception:
                        pass

    @staticmethod
    def _extract_description(content: str) -> str:
        if not content.startswith("---"):
            return ""
        parts = content.split("---", 2)
        if len(parts) < 3:
            return ""
        for line in parts[1].split("\n"):
            if line.strip().startswith("description:"):
                return line.strip()[12:].strip().strip("\"'")
        return ""

    def _load_synonyms(self) -> None:
        """Load synonym dictionary — Layer 3 source data."""
        import jieba

        synonym_file = Path(__file__).parent / "skill_synonyms.yaml"
        if not synonym_file.exists():
            logger.debug("No synonym dictionary found at %s", synonym_file)
            return

        try:
            content = synonym_file.read_text(encoding="utf-8")
            current_skill = None
            for line in content.split("\n"):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if stripped.startswith("- "):
                    if current_skill:
                        synonym = stripped[2:].strip().strip('"').strip("'")
                        if synonym:
                            self._synonyms.setdefault(current_skill, []).append(synonym)
                elif ":" in stripped and not stripped.startswith("-"):
                    skill_name = stripped.split(":")[0].strip()
                    if skill_name and not skill_name.startswith("_"):
                        current_skill = skill_name
                    else:
                        current_skill = None

            # Build reverse index for Layer 3
            valid_names = set(self._skill_names)
            for skill_name, syn_list in self._synonyms.items():
                if skill_name not in valid_names:
                    continue
                for syn in syn_list:
                    # Index jieba tokens of the synonym
                    tokens = [t.strip() for t in jieba.cut(syn) if len(t.strip()) > 1]
                    for token in tokens:
                        self._synonym_index.setdefault(token, []).append(
                            (skill_name, 1.0)
                        )
                    # Also index the full synonym as a single unit
                    if len(syn) > 1:
                        self._synonym_index.setdefault(syn, []).append(
                            (skill_name, 1.5)
                        )

            logger.info(
                "Loaded %d synonym entries for %d skills, %d index tokens",
                sum(len(v) for v in self._synonyms.values()),
                len(self._synonyms),
                len(self._synonym_index),
            )
        except Exception as e:
            logger.warning("Failed to load synonym dictionary: %s", e)

    def _build_fts5_index(self) -> None:
        """Layer 2: Build clean FTS5 index (name + description only)."""
        import jieba

        if not self._jieba_initialized:
            jieba.initialize()
            self._jieba_initialized = True

        all_doc_freq: Counter = Counter()
        for i, desc in enumerate(self._skill_descs):
            name = self._skill_names[i]
            text = f"{name} {desc}"
            tokens = [
                t.strip() for t in jieba.cut(text)
                if len(t.strip()) > 1 and not t.strip().isdigit()
            ]
            self._doc_tokens.append(tokens)
            for token in set(tokens):
                all_doc_freq[token] += 1

        n = len(self._skill_names)
        self._idf = {
            t: math.log((n + 1) / (df + 1)) + 1
            for t, df in all_doc_freq.items()
        }

    def _load_embedding_model(self) -> None:
        """Layer 4: Load dense embedding model."""
        try:
            from sentence_transformers import SentenceTransformer
            import numpy as np
        except ImportError:
            logger.warning("sentence-transformers not installed; FTS5+Syn only")
            self._emb_matrix = None
            return

        model_name = os.environ.get(
            "HERMES_EMBEDDING_MODEL",
            "shibing624/text2vec-base-chinese-paraphrase"
        )
        try:
            self._model = SentenceTransformer(model_name)
            texts = [f"{n}, {d}" for n, d in zip(self._skill_names, self._skill_descs)]
            self._emb_matrix = self._model.encode(
                texts, normalize_embeddings=True, show_progress_bar=False
            )
            logger.info("Embedding model loaded: %s, shape: %s", model_name, self._emb_matrix.shape)
        except Exception as e:
            logger.warning("Embedding model load failed (%s); FTS5+Syn only", e)
            self._emb_matrix = None

    # ── Retrieval Pipeline ──────────────────────────────────────

    def _retrieve_inner(self, query: str, k: int) -> list[str]:
        """Layers 2-5: FTS5 + Synonym + Embedding → RRF fusion.

        Returns empty list when confidence is too low — meaning the query
        doesn't match any skill well, and the LLM should use general knowledge.
        """
        fts5_results = self._fts5_search(query, k * 3)
        syn_results = self._syn_search(query, k * 3)
        emb_results = self._emb_search(query, k * 3)

        fused = self._rrf_fusion(fts5_results, syn_results, emb_results)

        if not fused:
            return []

        top_score = fused[0][1]

        # Confidence check: top-1 must exceed floor (filter pure noise only)
        if top_score < _CONFIDENCE_THRESHOLD:
            logger.debug(
                "Skill retriever: top-1 score %.4f < floor %.4f, no signal",
                top_score, _CONFIDENCE_THRESHOLD,
            )
            return []

        # No gap check — when multiple skills score close, return all as hints
        # and let the LLM decide which (if any) to load.

        result = []
        for idx, score in fused[:k]:
            if score >= _MIN_RRF_SCORE:
                result.append(self._skill_names[idx])
        return result

    def _fts5_search(self, query: str, k: int) -> list[tuple[int, float]]:
        """Layer 2: Clean BM25 search — name + description only, no synonym mixing."""
        import jieba

        query_tokens = [
            t.strip() for t in jieba.cut(query)
            if len(t.strip()) > 1
        ]
        scores: dict[int, float] = defaultdict(float)

        for qt in query_tokens:
            q_idf = self._idf.get(qt, 1.0)
            for i, doc_tok in enumerate(self._doc_tokens):
                tf = doc_tok.count(qt)
                if tf > 0:
                    scores[i] += q_idf * tf / (tf + 1.5)
                # Direct string match bonus in name+desc
                if qt in f"{self._skill_names[i]} {self._skill_descs[i]}":
                    scores[i] += q_idf * 0.5

        ranked = sorted(scores.items(), key=lambda x: -x[1])
        return ranked[:k]

    def _syn_search(self, query: str, k: int) -> list[tuple[int, float]]:
        """Layer 3: Synonym dictionary matching — independent scoring."""
        import jieba

        query_tokens = [
            t.strip() for t in jieba.cut(query)
            if len(t.strip()) > 1
        ]
        scores: dict[int, float] = defaultdict(float)

        # Token-level synonym matching
        for qt in query_tokens:
            if qt in self._synonym_index:
                q_idf = self._idf.get(qt, 1.0)
                for skill_name, weight in self._synonym_index[qt]:
                    try:
                        idx = self._skill_names.index(skill_name)
                        scores[idx] += weight * q_idf
                    except ValueError:
                        pass

        # Full-phrase synonym matching (multi-char synonyms in query)
        for syn_key, entries in self._synonym_index.items():
            if len(syn_key) > 2 and syn_key in query:
                for skill_name, weight in entries:
                    try:
                        idx = self._skill_names.index(skill_name)
                        scores[idx] += weight * 2.0
                    except ValueError:
                        pass

        ranked = sorted(scores.items(), key=lambda x: -x[1])
        return ranked[:k]

    def _emb_search(self, query: str, k: int) -> list[tuple[int, float]]:
        """Layer 4: Dense embedding cosine similarity."""
        if self._model is None or self._emb_matrix is None:
            return []

        import numpy as np

        query_emb = self._model.encode([query], normalize_embeddings=True)
        scores = np.dot(self._emb_matrix, query_emb.T).flatten()
        top_idx = np.argsort(-scores)[:k]
        return [(int(idx), float(scores[idx])) for idx in top_idx]

    @staticmethod
    def _rrf_fusion(
        fts5_results: list[tuple[int, float]],
        syn_results: list[tuple[int, float]],
        emb_results: list[tuple[int, float]],
        k: int = _RRF_K,
    ) -> list[tuple[int, float]]:
        """Layer 5: Reciprocal Rank Fusion of three ranked lists.

        점수는 '살아있는 레이어의 가중치 합'으로 정규화한다. 신뢰 임계값이 3레이어(FTS5+Syn+임베딩)
        기준으로 잡혀 있어서, 임베딩 모델이 없는 설치본(sentence-transformers 미설치가 기본)에서는
        최대 점수가 (0.45+0.35)/(k+1)에 묶여 **어떤 완벽한 매치도 임계값을 못 넘는다** → L2~5가
        통째로 무력화된다. 정규화하면 임계값의 의미가 레이어 구성과 무관해진다.
        """
        fused: dict[int, float] = {}

        active = 0.0
        for w, lst in ((_RRF_W_FTS5, fts5_results), (_RRF_W_SYN, syn_results), (_RRF_W_EMB, emb_results)):
            if lst:
                active += w
        if not active:
            return []
        scale = (_RRF_W_FTS5 + _RRF_W_SYN + _RRF_W_EMB) / active

        for rank, (idx, _) in enumerate(fts5_results, start=1):
            fused[idx] = fused.get(idx, 0.0) + _RRF_W_FTS5 / (k + rank)

        for rank, (idx, _) in enumerate(syn_results, start=1):
            fused[idx] = fused.get(idx, 0.0) + _RRF_W_SYN / (k + rank)

        for rank, (idx, _) in enumerate(emb_results, start=1):
            fused[idx] = fused.get(idx, 0.0) + _RRF_W_EMB / (k + rank)

        return sorted(((i, sc * scale) for i, sc in fused.items()), key=lambda x: -x[1])
