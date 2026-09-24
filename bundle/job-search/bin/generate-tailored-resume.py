#!/usr/bin/env python3
"""Tailored Resume & Cover Letter Generator (bin/generate-tailored-resume.py)

Material-warehouse driven renderer (소재 창고 → JD 스코어링 → 렌더러):
1. Accept JD text/file + company/position inputs.
2. Read the material warehouse from the wiki vault (fallback: kit templates):
   - master_resume.md  : identity, career KPIs, labelled skill rows, episode pool, cover blocks
   - fit_evidence.md   : verified fit proof points
   - role_contexts.md  : role key messages
   - portfolios.md     : projects
3. Score every fact against JD keywords — top-N only, no hallucinated content.
4. Render HTML templates with a mini-mustache renderer: templates own ALL
   markup/CSS classes, this script only supplies escaped data. (No class
   injection → no class mismatch with template styles.)
5. Optionally render a portfolio document from the same project material pool.
6. Optionally compile rendered HTML to A4 PDF using Headless Chrome.

Usage:
  python3 bin/generate-tailored-resume.py --company "Target" --position "Role" --jd-text "..." --pdf
  python3 bin/generate-tailored-resume.py --selftest
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# The generator lives at bundle/job-search/bin; the vault is a sibling directory.
BUNDLE_DIR = Path(__file__).resolve().parents[1]
REPO_BUNDLE_DIR = BUNDLE_DIR.parent
VAULT_DIR = REPO_BUNDLE_DIR / "vault"

# Mini-mustache: section blocks first ({{#name}}...{{/name}}), then plain vars.
SECTION_RE = re.compile(r"\{\{#(\w+)\}\}(.*?)\{\{/\1\}\}", re.DOTALL)
VAR_RE = re.compile(r"\{\{(\w+)\}\}")

# JD 토큰 중 사실 판별에 무의미한 일반어 (한·영 공통 저빈도 제외어)
JD_STOP = {
    "the", "and", "for", "with", "you", "your", "our", "will", "are", "this",
    "that", "from", "have", "has", "years", "year", "experience", "etc",
    "plus", "must", "should", "able", "work", "team", "who", "what", "when",
    "where", "how", "including", "across", "using", "well", "new", "least",
    "경험", "우대", "우대사항", "필수", "자격", "지원", "업무", "담당", "채용",
    "면접", "및", "등", "이상", "이하", "관련", "활용", "능숙", "기본", "모든",
    "가능", "실제", "우선", "년차", "명", "개", "가지", "이상의",
}


def get_wiki_root() -> Path:
    data_dir = os.environ.get("HERMES_DATA") or "/opt/data"
    wiki_path = os.environ.get("WIKI_PATH") or os.environ.get("OBSIDIAN_VAULT_PATH") or f"{data_dir}/wiki"
    return Path(wiki_path).resolve()


def read_source_vault(wiki_root: Path) -> dict[str, str]:
    sources = {
        "master_resume": "",
        "fit_evidence": "",
        "role_contexts": "",
        "portfolios": "",
    }

    resume_dir = wiki_root / "automation" / "job-hunting" / "resume"
    template_dir = VAULT_DIR / "automation" / "job-hunting" / "resume"

    for key in sources.keys():
        f = resume_dir / f"{key}.md"
        if not f.is_file():
            f = template_dir / f"{key}.md"
        if f.is_file():
            sources[key] = f.read_text(encoding="utf-8")

    return sources


def find_chrome_binary() -> str | None:
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def render_pdf(html_path: Path, pdf_path: Path) -> bool:
    chrome = find_chrome_binary()
    if chrome:
        try:
            cmd = [
                chrome,
                "--headless",
                "--disable-gpu",
                "--no-sandbox",
                f"--print-to-pdf={pdf_path}",
                str(html_path),
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if r.returncode == 0 and pdf_path.is_file():
                return True
        except Exception as e:
            sys.stderr.write(f"Chrome PDF render warning: {e}\n")

    return False


def apply_recruiter_density_guard(summary: str, is_senior: bool = True) -> str:
    """Enforces recruiter readability rules: 1~1.5 pages (max 2 pages for senior), high KPI density, no fluff."""
    summary_clean = summary.strip()
    if len(summary_clean) > 350:
        summary_clean = summary_clean[:350] + "..."
    return summary_clean


# ---------------------------------------------------------------------------
# Renderer — templates own markup/classes; this only fills escaped data.
# ---------------------------------------------------------------------------

def render_template(tmpl: str, ctx: dict) -> str:
    """Expand {{#section}} loops (value = list[dict] → repeat, falsy → drop),
    then substitute {{var}} with HTML-escaped values."""
    while True:
        m = SECTION_RE.search(tmpl)
        if not m:
            break
        name, body = m.group(1), m.group(2)
        val = ctx.get(name)
        if isinstance(val, list):
            rep = "".join(render_template(body, {**ctx, **item}) for item in val)
        elif val:
            rep = render_template(body, ctx)
        else:
            rep = ""
        tmpl = tmpl[: m.start()] + rep + tmpl[m.end():]
    return VAR_RE.sub(lambda vm: html.escape(str(ctx[vm.group(1)])) if vm.group(1) in ctx else "", tmpl)
# ---------------------------------------------------------------------------
# Source parsers — tolerant of blank placeholder values.
# ---------------------------------------------------------------------------

def _strip_md(val: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"\1", val).strip()


def _is_placeholder(value: str) -> bool:
    """Template guidance such as ``<프로젝트명>`` is not a real material fact."""
    value = _strip_md(value or "")
    return (not value or value.startswith("<") or value.startswith("your.")
            or "placeholder" in value.lower())


def _usable(values: list[str]) -> list[str]:
    return [v for v in (_strip_md(x) for x in values) if v and not _is_placeholder(v)]


# 보유 기술: 스킬 섹션 안의 `- **국문 라벨 / English Label**: 값` 한 줄 = 표 한 행.
SKILLS_HEADING_RE = re.compile(r"skills|보유 기술|핵심 역량 및 도구", re.IGNORECASE)
# 구버전 고정 슬롯 키 → (국문, 영문) 라벨. 기존 master_resume.md 호환용.
LEGACY_SKILL_LABELS = {
    "ax": ("AI · 자동화", "AI & Automation"),
    "sales_ops": ("영업 운영", "Sales Operations"),
    "tools": ("개발 · 도구", "Tools"),
    "domain": ("도메인 · 언어", "Domains & Languages"),
}


def parse_skill_row(key: str, val: str) -> dict | None:
    """'데이터 분석 / Data Analysis' + 'SQL, Python' → {label_ko, label_en, value, key}.
    Placeholder labels or values yield None so untouched template rows stay hidden."""
    value = _strip_md(val)
    if _is_placeholder(key) or _is_placeholder(value):
        return None
    if key in LEGACY_SKILL_LABELS:
        ko, en = LEGACY_SKILL_LABELS[key]
    else:
        ko, _, en = (part.strip() for part in key.partition(" / "))
        en = en or ko
    return {"key": key, "label_ko": ko, "label_en": en, "value": value}


def parse_master_resume(text: str) -> dict:
    """Parse identity, experiences (with KPI bullets), and skill slots from master_resume.md."""
    ident_keys = {"이름": "name", "영문 이름": "name_en", "한 줄 소개": "headline",
                  "생년월일": "birth", "이메일": "email", "전화": "phone", "연락처": "phone",
                  "거주지": "address", "포트폴리오": "portfolio", "LinkedIn": "linkedin",
                  "블로그": "blog", "사진": "photo", "희망 연봉": "salary",
                  "입사 가능일": "available", "희망 근무지": "work_location",
                  "주요 트랙": "tracks", "숨길 항목": "hide"}
    ident: dict = {v: "" for v in ident_keys.values()}
    ident["competencies"] = []
    exp_keys = {"직급/직책": "role", "담당 업무": "department", "회사 소개": "company_desc",
                "고용 형태": "employment_type", "퇴사 사유": "leave_reason",
                "사용 도구 & 기술": "tools"}
    experiences: list = []
    skills: list = []
    current_exp = None
    in_kpi = False
    in_competency = False
    in_skills = False

    for raw in text.splitlines():
        line = raw.rstrip()
        s = line.strip()
        if not s or s == "---":
            in_kpi = False
            in_competency = False
            if s.startswith("## "):
                current_exp = None
            continue
        if s.startswith("### "):
            in_kpi = False
            in_competency = False
            m = re.match(r"^###\s+(.+?)\s*\(근무 기간[:：]\s*(.+?)\)\s*$", s)
            if m:
                current_exp = {"company": m.group(1).strip(), "period": m.group(2).strip(),
                               "kpis": [], **{v: "" for v in exp_keys.values()}}
                experiences.append(current_exp)
            continue
        if s.startswith("#"):
            in_kpi = False
            in_competency = False
            if s.startswith("## "):
                in_skills = bool(SKILLS_HEADING_RE.search(s))
            continue
        m = re.match(r"^-\s*\*\*(.+?)\*\*\s*[:：]\s*(.*)$", s)
        if m:
            key, val = m.group(1).strip(), m.group(2).strip()
            in_kpi = False
            in_competency = False
            if key in LEGACY_SKILL_LABELS or in_skills:
                row = parse_skill_row(key, val)
                if row:
                    skills.append(row)
            elif key in ident_keys:
                ident[ident_keys[key]] = _strip_md(val)
            elif key == "커리어 핵심 경쟁력":
                in_competency = True
            elif key.startswith("주요 정량"):
                in_kpi = True
                if current_exp is not None:
                    current_exp["_kpi_mode"] = True
            elif key in exp_keys and current_exp is not None:
                current_exp[exp_keys[key]] = _strip_md(val)
            continue
        if s.startswith("- ") and (line.startswith("  ") or line.startswith("\t")):
            item = _strip_md(s[2:].strip())
            if not item:
                continue
            if in_competency:
                if not _is_placeholder(item):
                    ident["competencies"].append(item)
            elif current_exp is not None and (in_kpi or current_exp.get("_kpi_mode")):
                if not _is_placeholder(item):
                    current_exp["kpis"].append(item)

    for exp in experiences:
        exp.pop("_kpi_mode", None)
    return {"ident": ident, "experiences": experiences, "skills": skills}


def parse_episodes(text: str) -> list:
    """Parse the episode pool (상황/행동/정량결과/역량태그) from master_resume.md."""
    episodes: list = []
    cur = None
    field_map = {"상황": "situation", "행동": "action", "정량결과": "result", "역량태그": "tags"}
    for raw in text.splitlines():
        s = raw.strip()
        m = re.match(r"^###\s*에피소드\s*\d+\s*[:：]\s*(.+)$", s)
        if m:
            cur = {"title": m.group(1).strip(), "situation": "", "action": "",
                   "result": "", "tags": []}
            episodes.append(cur)
            continue
        if cur is None:
            continue
        fm = re.match(r"^-\s*\*\*(.+?)\*\*\s*[:：]\s*(.*)$", s)
        if fm:
            key = fm.group(1).strip()
            if key in field_map:
                val = fm.group(2).strip()
                if field_map[key] == "tags":
                    cur["tags"] = [t.strip() for t in val.split(",") if t.strip()]
                else:
                    cur[field_map[key]] = _strip_md(val)
    return [e for e in episodes if e["result"] or e["action"]]


def parse_cover_blocks(text: str) -> dict:
    """Parse 지원동기/강점근거/포부/도메인관심사 bullet pools from master_resume.md."""
    blocks: dict = {"motivation": [], "strength": [], "vision": [], "domain": []}
    heading_map = {"지원동기": "motivation", "강점근거": "strength",
                   "포부": "vision", "도메인관심사": "domain"}
    current = None
    for raw in text.splitlines():
        s = raw.strip()
        mh = re.match(r"^###\s*(지원동기|강점근거|포부|도메인관심사)\b", s)
        if mh:
            current = heading_map[mh.group(1)]
            continue
        if s.startswith("## "):
            current = None
            continue
        if current and (s.startswith("- ") or s.startswith("* ")):
            item = _strip_md(s[2:].strip())
            if item:
                blocks[current].append(item)
    return blocks


def parse_projects(text: str) -> list:
    """Parse project entries (개요/역할/기술 스택/성과/링크) from portfolios.md.
    Placeholder-only rows are dropped so generated documents never expose template guidance.
    """
    projects: list = []
    cur = None
    for raw in text.splitlines():
        s = raw.strip()
        mh = re.match(r"^###\s*\d+\.\s*(.+?)\s*(?:\*\(필요하면 추가\)\*)?\s*$", s)
        if mh:
            name = mh.group(1).strip()
            if name.startswith("<"):
                cur = None
            else:
                cur = {"project_name": name, "period": "", "description": "", "role": "",
                       "tech_stack": "", "impact": "", "link": ""}
                projects.append(cur)
            continue
        if cur is None:
            continue
        fm = re.match(r"^-\s*\*\*(.+?)\*\*\s*[:：]\s*(.*)$", s)
        if fm:
            key, val = fm.group(1).strip(), _strip_md(fm.group(2).strip())
            if _is_placeholder(val):
                continue
            if key in ("개요", "역할"):
                cur["description" if key == "개요" else "role"] = val
            elif key == "기간":
                cur["period"] = val
            elif key == "기술 스택":
                cur["tech_stack"] = val
            elif key == "성과":
                cur["impact"] = val
            elif key in ("링크", "포트폴리오", "GitHub"):
                cur["link"] = val
    return [p for p in projects if p["description"] or p["impact"]]


# "### 학력" 등 표형 섹션: 한 줄 = `- 칸1 | 칸2 | 칸3 | 칸4`, 칸 이름은 섹션별 고정.
RESUME_SECTIONS = {
    "학력": ("education", ("period", "school", "major", "status")),
    "자격증": ("certifications", ("date", "name", "issuer")),
    "어학": ("languages", ("test", "score", "date")),
    "교육 이수": ("training", ("period", "course", "institution")),
    "수상": ("awards", ("date", "name", "issuer")),
    "대외활동": ("activities", ("period", "name", "detail")),
    "해외 경험": ("overseas", ("period", "country", "purpose")),
    "논문·특허·출판": ("publications", ("date", "title", "venue")),
    "발표·강연": ("talks", ("date", "title", "event")),
    "병역": ("military", ("status", "detail", "period")),
    "취업 우대": ("preferences", ("category", "detail")),
}

# 선택 항목 라벨 → 숨길 때 비울 컨텍스트 키 (master_resume.md `숨길 항목`에서 사용)
OPTIONAL_FIELDS = {
    "영문 이름": "applicant_name_en", "한 줄 소개": "headline", "생년월일": "birth",
    "거주지": "address", "LinkedIn": "linkedin", "블로그": "blog", "사진": "photo",
    "희망 연봉": "salary", "입사 가능일": "available", "희망 근무지": "work_location",
    "총 경력": "total_career", "확인 문구": "show_attest",
}
EXP_OPTIONAL_FIELDS = {"회사 소개": "company_desc", "고용 형태": "employment_type",
                       "재직 기간": "duration", "퇴사 사유": "leave_reason", "사용 기술": "tools"}


def parse_resume_sections(text: str) -> dict:
    """Parse pipe-delimited rows under ### 학력/자격증/어학/교육 이수/수상/대외활동/병역.
    Rows whose cells are all placeholders are dropped, so an untouched template yields []."""
    out: dict = {key: [] for key, _ in RESUME_SECTIONS.values()}
    current = None
    for raw in text.splitlines():
        s = raw.strip()
        mh = re.match(r"^###\s*(.+?)\s*$", s)
        if mh:
            current = RESUME_SECTIONS.get(mh.group(1))
            continue
        if s.startswith("## "):
            current = None
            continue
        if not current or not (s.startswith("- ") or s.startswith("* ")):
            continue
        key, fields = current
        cells = [_strip_md(c) for c in s[2:].split("|")]
        cells = ["" if _is_placeholder(c) else c for c in cells]
        if not any(cells):
            continue
        cells += [""] * (len(fields) - len(cells))
        head = cells[: len(fields) - 1] + [" · ".join(c for c in cells[len(fields) - 1:] if c)]
        out[key].append(dict(zip(fields, head)))
    return out


PERIOD_RE = re.compile(r"(\d{4})\s*[.\-/년]\s*(\d{1,2})")
PRESENT_RE = re.compile(r"현재|재직|present|now|current", re.IGNORECASE)


def period_span(period: str, today: dt.date | None = None) -> tuple | None:
    """'2021.03 ~ 2024.05' / '2021.03 – 현재' → (start, end) as absolute month indexes."""
    points = PERIOD_RE.findall(period or "")
    if not points:
        return None
    start = int(points[0][0]) * 12 + int(points[0][1]) - 1
    if len(points) >= 2:
        end = int(points[1][0]) * 12 + int(points[1][1]) - 1
    elif PRESENT_RE.search(period):
        today = today or dt.date.today()
        end = today.year * 12 + today.month - 1
    else:
        return None
    return (start, end) if 0 <= end - start < 12 * 60 else None


def period_months(period: str, today: dt.date | None = None) -> int:
    """Inclusive month count of one period (0 if unparsable)."""
    span = period_span(period, today)
    return span[1] - span[0] + 1 if span else 0


def total_months(periods: list, today: dt.date | None = None) -> int:
    """Union of all periods in months — overlapping jobs are not double-counted."""
    months: set = set()
    for p in periods:
        span = period_span(p, today)
        if span:
            months.update(range(span[0], span[1] + 1))
    return len(months)


def format_months(months: int, lang: str = "ko") -> str:
    if months <= 0:
        return ""
    y, m = divmod(months, 12)
    if lang == "en":
        parts = ([f"{y} yr" + ("s" if y > 1 else "")] if y else []) + \
                ([f"{m} mo" + ("s" if m > 1 else "")] if m else [])
    else:
        parts = ([f"{y}년"] if y else []) + ([f"{m}개월"] if m else [])
    return " ".join(parts)


def parse_fit_points(text: str) -> list:
    """Pull '- **key**: value' / '- value' proof lines from fit_evidence.md."""
    points: list = []
    for raw in text.splitlines():
        s = raw.strip()
        if not (s.startswith("- ") or s.startswith("* ")):
            continue
        body = s[2:].strip()
        m = re.match(r"^\*\*(.+?)\*\*\s*[:：]\s*(.*)$", body)
        val = _strip_md((m.group(2) if m else body).strip())
        if val:
            points.append(val)
    return points


def parse_role_messages(text: str) -> list:
    """Pull 핵심 메시지 values and plain bullets from role_contexts.md."""
    msgs: list = []
    for raw in text.splitlines():
        s = raw.strip()
        m = re.match(r"^-\s*\*\*(.+?)\*\*\s*[:：]\s*(.*)$", s)
        if m:
            if m.group(1).strip() == "핵심 메시지":
                val = _strip_md(m.group(2).strip())
                if val:
                    msgs.append(val)
        elif s.startswith("- ") or s.startswith("* "):
            val = _strip_md(s[2:].strip())
            if val and not val.startswith("<사례"):
                msgs.append(val)
    return msgs


# ---------------------------------------------------------------------------
# JD keyword scoring — rank facts by JD overlap (top-N only, stable fallback).
# ---------------------------------------------------------------------------

def jd_keywords(jd_text: str) -> list:
    """Extract distinctive tokens from JD: English words (len>=2) + Korean eojeol (len>=2),
    minus stopword list. Order-preserving unique."""
    words = re.findall(r"[A-Za-z][A-Za-z0-9+#.]*|[가-힣]{2,}", jd_text or "")
    seen: dict = {}
    for w in words:
        lw = w.lower()
        if lw in JD_STOP or lw in seen:
            continue
        seen[lw] = True
    return list(seen)


def score_text(text: str, keywords: list) -> int:
    """Count keyword hits in text (case-insensitive substring match — works for KR/EN)."""
    if not text or not keywords:
        return 0
    lt = text.lower()
    return sum(lt.count(k) for k in keywords)


def rank_by_jd(items: list, keywords: list, text_of, limit: int = 0) -> list:
    """Stable sort desc by JD score; ties keep source order. limit=0 → all."""
    scored = sorted(items, key=lambda it: -score_text(text_of(it), keywords))
    return scored[:limit] if limit else scored


def _first_filled(values: list, fallback: str) -> str:
    for v in values:
        if v and v.strip():
            return v.strip()
    return fallback


# ---------------------------------------------------------------------------
# Context builder — consume all four sources into template slots.
# ---------------------------------------------------------------------------

def build_context(company: str, position: str, jd_text: str, sources: dict,
                  is_senior: bool = True, lang: str = "ko") -> dict:
    keywords = jd_keywords(f"{jd_text}\n{position}")
    master = parse_master_resume(sources.get("master_resume", ""))
    sections = parse_resume_sections(sources.get("master_resume", ""))
    episodes = parse_episodes(sources.get("master_resume", ""))
    cover = parse_cover_blocks(sources.get("master_resume", ""))
    projects = parse_projects(sources.get("portfolios", ""))
    fit_points = parse_fit_points(sources.get("fit_evidence", ""))
    role_msgs = parse_role_messages(sources.get("role_contexts", ""))

    ident = master["ident"]

    # --- experiences: KPI bullets ranked by JD fit, top 4; exps ranked too ---
    experiences = []
    for exp in master["experiences"]:
        if _is_placeholder(exp["company"]):
            continue
        kpis = _usable(rank_by_jd(exp["kpis"], keywords, lambda t: t, limit=4) or exp["kpis"])
        if not kpis:
            continue
        experiences.append({
            "company": exp["company"],
            "period": exp["period"],
            "duration": format_months(period_months(exp["period"]), lang),
            "role": _first_filled([exp["role"]], "직무"),
            "department": _first_filled([exp["department"]], "담당 업무"),
            **{k: "" if _is_placeholder(exp[k]) else exp[k]
               for k in ("company_desc", "employment_type", "leave_reason", "tools")},
            "accomplishments": [
                {"tag": (t.split("·")[0].strip()[:14] if t else "KPI"), "description": t}
                for t in kpis
            ],
        })
    experiences = rank_by_jd(
        experiences, keywords,
        lambda e: e["company"] + " " + " ".join(a["description"] for a in e["accomplishments"]))
    if not experiences:
        experiences = []

    # --- projects ranked by JD fit, top 3; placeholder-only rows already dropped ---
    projects = rank_by_jd(
        projects, keywords,
        lambda p: " ".join([p["project_name"], p["description"], p["tech_stack"], p["impact"]]),
        limit=3)
    if not projects:
        projects = []

    # --- skills: 라벨·순서 모두 master_resume.md가 결정 (언어별 라벨 선택) ---
    skills = [{"label": row["label_en" if lang == "en" else "label_ko"], "value": row["value"]}
              for row in master["skills"]] or [
        {"label": "보유 기술", "value": "보유 기술을 master_resume.md에 `- **분야**: 기술` 형식으로 등록해 주세요."}]

    # --- episodes: JD-ranked pool feeds summary + cover achievements ---
    episodes = rank_by_jd(
        episodes, keywords,
        lambda e: " ".join([e["title"], e["situation"], e["action"], e["result"], " ".join(e["tags"])]))

    # --- executive summary: competencies + top episode results (density-guarded) ---
    comps = rank_by_jd(ident["competencies"], keywords, lambda t: t, limit=2) or ident["competencies"][:2]
    top_results = [e["result"] for e in episodes[:2] if e["result"]]
    fit_top = rank_by_jd(fit_points, keywords, lambda t: t, limit=2)
    comp_line = " · ".join(_usable(comps)) or "경력·역량 자료를 master_resume.md에 등록해 주세요."
    result_line = " / ".join(_usable(top_results)) or (fit_top[0] if fit_top else "정량 성과를 master_resume.md에 등록해 주세요.")
    raw_summary = f"{company} {position} 지원 요약 — {comp_line}. 대표 성과: {result_line}."
    executive_summary = apply_recruiter_density_guard(raw_summary, is_senior=is_senior)
    # 이력서 요약 불릿: JD 상위 역량 3 + 대표 성과 2 (중복 제거, 최대 4)
    summary_points: list = []
    for line in _usable(rank_by_jd(ident["competencies"], keywords, lambda t: t, limit=3)) + _usable(top_results):
        if line not in summary_points:
            summary_points.append(line)
    summary_points = [{"text": t} for t in summary_points[:4]] or [
        {"text": "경력·역량 자료를 master_resume.md에 등록해 주세요."}]
    total_career = format_months(total_months([e["period"] for e in experiences]), lang)

    # --- cover letter: JD-ranked sentences from cover blocks + episodes + fit/role pools ---
    def top_lines(pool: list, n: int) -> list:
        return _usable(rank_by_jd([x for x in pool if x], keywords, lambda t: t, limit=n))

    motivation_pool = cover["motivation"] + role_msgs
    motivation_lines = top_lines(motivation_pool, 2) or [
        f"{company}의 {position}에서 해결하고 싶은 문제를 master_resume.md에 구체적으로 기록해 지원드립니다."
    ]
    motivation_text = "\n".join(motivation_lines)

    strength_pool = cover["strength"] + [e["result"] for e in episodes if e["result"]] + fit_top
    strength_lines = top_lines(strength_pool, 3) or [
        "정량 성과와 문제 해결 사례를 master_resume.md에 등록하면 공고에 맞게 선별해 제시합니다."
    ]
    achievements_text = "\n".join(strength_lines)

    vision_pool = cover["vision"] + cover["domain"]
    vision_lines = top_lines(vision_pool, 2) or [
        "입사 후의 기여 계획과 관심 도메인을 master_resume.md에 등록하면 공고에 맞게 선별해 구성합니다."
    ]
    future_plan_text = "\n".join(vision_lines)

    opt = lambda key: "" if _is_placeholder(ident[key]) else ident[key]
    ctx = {
        "applicant_name": _first_filled([ident["name"]], "지원자 이름"),
        "target_company": company,
        "target_position": position,
        "email": _first_filled([ident["email"]], "이메일 입력"),
        "phone": _first_filled([ident["phone"]], "연락처 입력"),
        "github_or_portfolio": _first_filled([ident["portfolio"]], "포트폴리오 URL"),
        "applicant_name_en": opt("name_en"),
        "headline": opt("headline"),
        **{k: opt(k) for k in ("birth", "address", "linkedin", "blog", "photo",
                               "salary", "available", "work_location")},
        "total_career": total_career,
        "show_attest": True,
        "executive_summary": executive_summary,
        "summary_points": summary_points,
        "experiences": experiences,
        "projects": projects,
        "skills": skills,
        "motivation_text": motivation_text,
        "achievements_text": achievements_text,
        "future_plan_text": future_plan_text,
        "generated_date": dt.date.today().isoformat(),
        **sections,
    }
    return apply_output_options(ctx, ident["hide"], lang)


def apply_output_options(ctx: dict, hide: str, lang: str = "ko") -> dict:
    """Blank out optional fields/sections listed in `숨길 항목`, then derive the
    has_* flags and the profile/contact lists the templates iterate over."""
    hidden = {h.strip() for h in re.split(r"[,、/]", hide or "") if h.strip() and not _is_placeholder(h)}
    for label in hidden:
        if label in OPTIONAL_FIELDS:
            ctx[OPTIONAL_FIELDS[label]] = "" if label != "확인 문구" else False
        elif label in EXP_OPTIONAL_FIELDS:
            for exp in ctx["experiences"]:
                exp[EXP_OPTIONAL_FIELDS[label]] = ""
        elif label in RESUME_SECTIONS:
            ctx[RESUME_SECTIONS[label][0]] = []
    for key, _ in RESUME_SECTIONS.values():
        ctx[f"has_{key}"] = bool(ctx[key])
    ctx["has_quals"] = bool(ctx["certifications"] or ctx["languages"])

    # 인적사항 표(국문): 채워진 항목만 2열 격자로
    profile = [("성명", ctx["applicant_name"] + (f" ({ctx['applicant_name_en']})" if ctx["applicant_name_en"] else "")),
               ("생년월일", ctx["birth"]), ("연락처", ctx["phone"]), ("이메일", ctx["email"]),
               ("거주지", ctx["address"]), ("포트폴리오", ctx["github_or_portfolio"]),
               ("LinkedIn", ctx["linkedin"]), ("블로그", ctx["blog"]),
               ("희망 연봉", ctx["salary"]), ("입사 가능일", ctx["available"]),
               ("희망 근무지", ctx["work_location"])]
    ctx["profile"] = [{"label": k, "value": v} for k, v in profile if v]
    # 연락처 한 줄(영문·기본): 글로벌 표준은 생년월일·희망 연봉을 싣지 않는다
    contact = [ctx["address"], ctx["email"], ctx["phone"], ctx["github_or_portfolio"],
               ctx["linkedin"], ctx["blog"]]
    ctx["contact_items"] = [{"value": v} for v in contact if v]
    return ctx


# ---------------------------------------------------------------------------
# HTML assembly
# ---------------------------------------------------------------------------

def build_tailored_html(company: str, position: str, jd_text: str, sources: dict,
                        is_senior: bool = True, lang: str = "ko") -> tuple:
    tmpl_dir = VAULT_DIR / "automation" / "job-hunting" / "templates"

    if lang == "en":
        res_file = tmpl_dir / "resume_en_classic.html"
    else:
        res_file = tmpl_dir / "resume_kr_standard.html"
    if not res_file.is_file():
        res_file = tmpl_dir / "resume_template.html"

    res_tmpl = res_file.read_text(encoding="utf-8")
    cov_tmpl = (tmpl_dir / "cover_letter_template.html").read_text(encoding="utf-8")

    ctx = build_context(company, position, jd_text, sources, is_senior=is_senior, lang=lang)
    return render_template(res_tmpl, ctx), render_template(cov_tmpl, ctx)


def build_portfolio_html(company: str, position: str, jd_text: str, sources: dict,
                         is_senior: bool = True) -> str:
    """Render the dedicated portfolio document from the same ranked project pool."""
    tmpl = VAULT_DIR / "automation" / "job-hunting" / "templates" / "portfolio.html"
    ctx = build_context(company, position, jd_text, sources, is_senior=is_senior)
    return render_template(tmpl.read_text(encoding="utf-8"), ctx)


# ---------------------------------------------------------------------------
# Selftest — renderer contract, source→slot flow, JD ranking, HTML hygiene.
# ---------------------------------------------------------------------------

def _assert_balanced(html_doc: str, label: str) -> None:
    for tag in ("div", "ul", "li", "span", "table", "section", "header", "html", "body"):
        opens = len(re.findall(rf"<{tag}(?:\s|>)", html_doc))
        closes = len(re.findall(rf"</{tag}>", html_doc))
        assert opens == closes, f"{label}: <{tag}> 불균형 ({opens} open / {closes} close)"


def run_selftest() -> None:
    # 1) 렌더러 계약: 섹션 반복 + 이스케이프 + 미채움 토큰 소거
    tpl = ("{{#experiences}}[{{company}}|{{#accomplishments}}{{tag}}:{{description}};{{/accomplishments}}]"
           "{{/experiences}}<{{executive_summary}}>")
    ctx = {"experiences": [{"company": "A & B", "accomplishments": [
        {"tag": "T1", "description": "<b>x</b>"}]}],
        "executive_summary": "sum <script>"}
    out = render_template(tpl, ctx)
    assert "A &amp; B" in out and "&lt;b&gt;x&lt;/b&gt;" in out, f"이스케이프 실패: {out}"
    assert "&lt;script&gt;" in out, f"이스케이프 실패(summary): {out}"
    assert "{{" not in out, f"미처리 토큰 잔존: {out}"
    dropped = render_template("X{{#missing}}never{{/missing}}Y", {})
    assert dropped == "Y" or dropped == "XY", f"빈 섹션 처리 실패: {dropped!r}"

    # 2) 파서 계약: 소재 창고 → 구조화 데이터 (kit 템플릿 사용)
    tmp = Path(tempfile.mkdtemp(prefix="tailored-resume-test-"))
    sources = read_source_vault(tmp)
    assert sources["master_resume"], "master_resume 소스 로드 실패"
    master = parse_master_resume(sources["master_resume"])
    assert master["ident"]["name"] == "<지원자 이름>", f"식별자 파싱 실패: {master['ident']}"
    assert len(master["experiences"]) >= 1, "경력 블록 파싱 실패"
    assert master["experiences"][0]["kpis"], "KPI 불릿 파싱 실패"
    assert not master["skills"], f"플레이스홀더 스킬 행이 파싱됨: {master['skills']}"
    legacy = parse_master_resume("## 3. Skills\n- **ax**: Python\n- **데이터 분석 / Data Analysis**: SQL\n"
                                 "- **<분야>**: <기술>\n\n## 4. 기타\n- **메모**: 무시")["skills"]
    assert [(r["label_ko"], r["label_en"], r["value"]) for r in legacy] == [
        ("AI · 자동화", "AI & Automation", "Python"), ("데이터 분석", "Data Analysis", "SQL")], \
        f"스킬 라벨 파싱 실패: {legacy}"
    episodes = parse_episodes(sources["master_resume"])
    assert len(episodes) >= 4, f"에피소드 풀 파싱 실패: {len(episodes)}"
    assert all(e["result"] and e["tags"] for e in episodes), "에피소드 4요소 파싱 실패"
    cover = parse_cover_blocks(sources["master_resume"])
    assert all(cover[k] for k in ("motivation", "strength", "vision", "domain")), f"자소 블록 파싱 실패: { {k: len(v) for k, v in cover.items()} }"
    assert parse_fit_points(sources["fit_evidence"]), "fit_evidence 파싱 실패"
    assert parse_role_messages(sources["role_contexts"]), "role_contexts 파싱 실패"

    # 3) JD 스코어링: 키워드 일치 항목이 상위로 올라옴
    kw = jd_keywords("Salesforce 파이프라인 CRM 분석")
    assert "salesforce" in kw and "파이프라인" in kw and "the" not in kw, f"JD 키워드 추출 실패: {kw}"
    pool = ["재고 회전율 개선", "Salesforce 기반 파이프라인 자동화"]
    ranked = rank_by_jd(pool, kw, lambda t: t)
    assert ranked[0].startswith("Salesforce"), f"JD 랭킹 실패: {ranked}"

    # 4) 전체 파이프라인: 소스 → HTML (ko/en) + 커버레터 소비 + 토큰 소거 + 클래스 일치
    tmpl_dir = VAULT_DIR / "automation" / "job-hunting" / "templates"
    res_html, cov_html = build_tailored_html("TestCo", "Sales Ops Lead",
                                             "Salesforce 파이프라인 CRM 분석", sources)
    for label, doc in (("resume", res_html), ("cover", cov_html)):
        assert "{{" not in doc, f"{label}: 미처리 토큰 잔존"
        _assert_balanced(doc, label)
    assert "TestCo" in res_html and "Sales Ops Lead" in res_html, "회사/포지션 미기입"
    assert "경력·역량 자료를 master_resume.md에 등록해 주세요." in res_html, "빈 소재 안내 문구 미주입"
    assert "<무엇을 바꿔" not in res_html, "플레이스홀더 KPI가 렌더링됨"
    # 템플릿 파일의 예시 문구만 있는 경우 빈 문단을 생성하지 않는다.
    assert "Future Contribution" in cov_html and "section-body" in cov_html and "포부 블록 미주입" not in cov_html
    # 템플릿 CSS 클래스와 생성기 마크업 불일치 완화: 생성기는 템플릿 마크업을 그대로 렌더
    assert "exp-card" in (tmpl_dir / "resume_kr_standard.html").read_text(encoding="utf-8"), "KR 템플릿 마크업 미수용"
    assert "job-item" not in res_html, "불일치 클래스(job-item)가 KR 출력에 잔존"
    res_en, cov_en = build_tailored_html("TestCo", "Sales Ops Lead", "", sources, lang="en")
    assert "entry-header" in (tmpl_dir / "resume_en_classic.html").read_text(encoding="utf-8"), "EN 템플릿 마크업 미수용"
    assert "{{" not in res_en and "{{" not in cov_en, "EN 출력 미처리 토큰 잔존"
    _assert_balanced(res_en, "resume_en")

    portfolio_html = build_portfolio_html("TestCo", "Sales Ops Lead",
                                         "Salesforce 파이프라인 CRM 분석", sources)
    assert "{{" not in portfolio_html, "portfolio: 미처리 토큰 잔존"
    assert "주요 프로젝트" in portfolio_html, "portfolio: 프로젝트 섹션 없음"
    _assert_balanced(portfolio_html, "portfolio")
    portfolio_doc = portfolio_html.lower()
    assert "<!doctype html" in portfolio_doc and "name=\"viewport\"" in portfolio_doc, "portfolio: 반응형 메타 누락"

    # 5) 빈 볼트 폴백: 하드코딩 없이 플레이스홀더만
    empty = {"master_resume": "", "fit_evidence": "", "role_contexts": "", "portfolios": ""}
    r3, c3 = build_tailored_html("FB Co", "Ops", "", empty)
    assert "&lt;회사명&gt;" not in r3 and "경력·역량 자료를 master_resume.md에 등록해 주세요." in r3, f"빈 볼트 폴백 실패: {r3[:300]}"
    assert "정량 성과와 문제 해결 사례를 master_resume.md에 등록" in c3, f"빈 볼트 커버 폴백 실패: {c3[:300]}"
    assert "FB Co" in r3, "회사명 미기입(폴백)"

    # 5-1) 플레이스홀더뿐인 선택 섹션은 출력에서 숨김
    assert not any(parse_resume_sections(sources["master_resume"]).values()), "플레이스홀더 선택 섹션이 파싱됨"
    assert '<table class="list-table">' not in res_html, "빈 선택 섹션(학력 등)이 렌더링됨"
    assert "생년월일" not in res_html, "빈 선택 항목(생년월일)이 렌더링됨"

    # 5-2) 최대 표준 서식: 선택 항목·섹션 채움 → 출력, 숨길 항목 → 제외, 총 경력 합산(중복 기간 1회)
    filled = (
        "- **숨길 항목**: 희망 연봉, 퇴사 사유, 수상\n"
        "- **이름**: 홍길동\n- **생년월일**: 1990.01.01\n- **희망 연봉**: 내규\n"
        "- **LinkedIn**: linkedin.com/in/hong\n"
        "- **커리어 핵심 경쟁력**:\n  - CRM 파이프라인 재설계로 예측 오차 절반\n\n"
        "### 알파 (근무 기간: 2020.01 ~ 2021.12)\n- **직급/직책**: 매니저\n- **고용 형태**: 정규직\n"
        "- **퇴사 사유**: 이직\n- **주요 정량 성과 (KPI)**:\n  - 전환율 10% 개선\n\n"
        "### 베타 (근무 기간: 2021.07 ~ 2022.06)\n- **직급/직책**: 겸직\n- **주요 정량 성과 (KPI)**:\n  - 비용 5% 절감\n\n"
        "## 3. 보유 기술 (Skills)\n- **데이터 분석 / Data Analysis**: Tableau\n- **협업**: Jira\n\n"
        "## 4. 학력 · 자격 · 활동\n### 학력\n- 2010.03 ~ 2014.02 | 한국대 | 경영학 학사 | 졸업\n"
        "### 자격증\n- 2020.05 | SQLD | 한국데이터산업진흥원\n### 수상\n- 2021.12 | 혁신상 | 알파\n"
        "### 병역\n- 군필 | 육군 병장 | 2014.03 ~ 2015.12\n### 취업 우대\n- <보훈 대상> | <내용>\n"
    )
    fsrc = {**empty, "master_resume": filled}
    sec = parse_resume_sections(filled)
    assert sec["education"][0] == {"period": "2010.03 ~ 2014.02", "school": "한국대",
                                   "major": "경영학 학사", "status": "졸업"}, f"학력 행 파싱 실패: {sec['education']}"
    assert not sec["preferences"], "플레이스홀더 행이 파싱됨"
    assert total_months(["2020.01 ~ 2021.12", "2021.07 ~ 2022.06"]) == 30, "총 경력 중복 합산"
    assert period_months("2021.03 ~ 현재", dt.date(2021, 5, 1)) == 3, "재직 중 기간 계산 실패"
    ko_doc, _ = build_tailored_html("T", "P", "CRM", fsrc)
    en_doc, _ = build_tailored_html("T", "P", "CRM", fsrc, lang="en")
    for label, doc in (("ko", ko_doc), ("en", en_doc)):
        assert "{{" not in doc, f"{label}: 미처리 토큰 잔존"
        _assert_balanced(doc, f"filled-{label}")
        assert "한국대" in doc and "SQLD" in doc, f"{label}: 학력·자격 섹션 누락"
        assert "혁신상" not in doc and "내규" not in doc and "이직" not in doc, f"{label}: 숨길 항목 노출"
    assert "1990.01.01" in ko_doc and "군필" in ko_doc and "2년 6개월" in ko_doc, "국문 선택 항목 누락"
    assert "1990.01.01" not in en_doc and "군필" not in en_doc, "영문에 국내 전용 항목 노출"
    assert "linkedin.com/in/hong" in en_doc and "정규직" in ko_doc, "연락처·고용 형태 누락"
    assert "<th scope=\"row\">데이터 분석</th>" in ko_doc and "<dt>Data Analysis</dt>" in en_doc, "스킬 라벨(국/영) 미반영"
    assert "<dt>협업</dt>" in en_doc and "Tableau" in ko_doc and "Jira" in en_doc, "스킬 값·라벨 폴백 누락"
    assert "보유 기술을 master_resume.md에" in r3, "빈 스킬 안내 문구 미주입"

    # 6) PDF 렌더링(Chrome 있는 환경에서만 강제)
    html_file = tmp / "TestCo_Sales_Ops_Resume.html"
    pdf_file = tmp / "TestCo_Sales_Ops_Resume.pdf"
    html_file.write_text(res_html, encoding="utf-8")
    pdf_ok = render_pdf(html_file, pdf_file)
    if find_chrome_binary():
        assert pdf_ok and pdf_file.is_file(), "PDF 렌더링 실패"

    print("selftest OK")








def main() -> None:
    parser = argparse.ArgumentParser(description="Master-material-driven tailored resume and cover letter generator")
    parser.add_argument("--company", "-c", default="TargetCompany")
    parser.add_argument("--position", "-p", default="Role")
    parser.add_argument("--jd-file")
    parser.add_argument("--jd-text")
    parser.add_argument("--output-dir", "--out", dest="output_dir")
    parser.add_argument("--lang", choices=("ko", "en"), default="ko")
    parser.add_argument("--portfolio", action="store_true",
                        help="포함: 맞춤 포트폴리오 HTML도 생성")
    parser.add_argument("--pdf", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        run_selftest()
        return
    jd = Path(args.jd_file).read_text(encoding="utf-8") if args.jd_file else (args.jd_text or "")
    out = Path(args.output_dir).expanduser().resolve() if args.output_dir else get_wiki_root() / "automation" / "job-hunting" / "resumes"
    out.mkdir(parents=True, exist_ok=True)
    sources = read_source_vault(get_wiki_root())
    resume, cover = build_tailored_html(args.company, args.position, jd, sources, lang=args.lang)
    safe = lambda s: re.sub(r"[^\w\-_]", "_", s, flags=re.UNICODE)
    paths = []
    documents = [("Resume", resume), ("CoverLetter", cover)]
    if args.portfolio:
        documents.append(("Portfolio", build_portfolio_html(
            args.company, args.position, jd, sources, is_senior=True)))
    for suffix, body in documents:
        path = out / f"{safe(args.company)}_{safe(args.position)}_{suffix}.html"
        path.write_text(body, encoding="utf-8")
        paths.append(path)
        if args.pdf:
            pdf = path.with_suffix(".pdf")
            if not render_pdf(path, pdf):
                raise SystemExit(f"PDF rendering failed: {pdf}")
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
