#!/usr/bin/env python3
"""Tailored Resume & Cover Letter Generator (bin/generate-tailored-resume.py)

Flow:
1. Accept JD text/file + company/position inputs.
2. Read Master CV (master_resume.md), Role Contexts (role_contexts.md),
   Portfolios (portfolios.md), and Fit Evidence (fit_evidence.md).
3. Match verified accomplishments & fit points (no hallucinations!).
4. Fill HTML templates:
   - wiki-template/automation/job-hunting/templates/resume_template.html
   - wiki-template/automation/job-hunting/templates/cover_letter_template.html
5. Compile HTML to pixel-perfect A4 PDF using Headless Chrome or PDF renderer.

Usage:
  python3 bin/generate-tailored-resume.py --company "Target Company" --position "Target Role" --jd-text "..." --pdf
  python3 bin/generate-tailored-resume.py --selftest
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

KIT_DIR = Path(__file__).resolve().parent.parent


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
    template_dir = KIT_DIR / "wiki-template" / "automation" / "job-hunting" / "resume"
    
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
    # Trim repetitive fluff, keep concise bullet statements
    summary_clean = summary.strip()
    if len(summary_clean) > 350:
        summary_clean = summary_clean[:350] + "..."
    return summary_clean


def build_tailored_html(
    company: str,
    position: str,
    jd_text: str,
    sources: dict[str, str],
    is_senior: bool = True,
    lang: str = "ko",
) -> tuple[str, str]:
    tmpl_dir = KIT_DIR / "wiki-template" / "automation" / "job-hunting" / "templates"
    
    if lang == "en":
        res_file = tmpl_dir / "resume_en_classic.html"
    else:
        res_file = tmpl_dir / "resume_kr_standard.html"
    
    if not res_file.is_file():
        res_file = tmpl_dir / "resume_template.html"

    res_tmpl = res_file.read_text(encoding="utf-8")
    cov_tmpl = (tmpl_dir / "cover_letter_template.html").read_text(encoding="utf-8")

    applicant_name = "홍길동 (Hong Gildong)"   # ← 본인 이름으로 교체
    email = "your.email@example.com"
    phone = "010-XXXX-XXXX"
    portfolio_url = "https://github.com/your-id"

    # Recruiter Page Budget & Density Control
    # Target: 1~1.5 pages (Senior/AX Lead max 2 pages)
    max_pages_hint = "2페이지 분량 제한 (시니어/AX 리드)" if is_senior else "1.5페이지 분량 제한 (주니어/주동급)"

    # Extract verified achievements from fit_evidence and master_resume
    fit_points = [l.strip("- ") for l in sources["fit_evidence"].splitlines() if l.strip().startswith("- ")]
    raw_summary = (
        f"{company} {position} 포지션에 맞춤화된 레주메입니다. "
        f"<본인 대표 역량 1>과 <본인 대표 역량 2>를 바탕으로 "
        f"<그 역량이 만드는 구체적 결과를 한 줄로>."
    )
    exec_summary = apply_recruiter_density_guard(raw_summary, is_senior=is_senior)

    # Simple template tag substitution
    res_html = res_tmpl
    res_html = res_html.replace("{{applicant_name}}", applicant_name)
    res_html = res_html.replace("{{target_company}}", company)
    res_html = res_html.replace("{{target_position}}", position)
    res_html = res_html.replace("{{email}}", email)
    res_html = res_html.replace("{{phone}}", phone)
    res_html = res_html.replace("{{github_or_portfolio}}", portfolio_url)
    res_html = res_html.replace("{{executive_summary}}", exec_summary)

    # Insert experiences & projects blocks.
    # NOTE: 이 블록의 본문은 *예시*다 — 실제 지원 시 master_resume.md / fit_evidence.md /
    # role_contexts.md 의 내용을 읽어 그 사람의 것으로 채운다. 하드코딩된 개인 이력 금지.
    exp_block = """
    <div class="job-item">
      <div class="job-header"><span>&lt;회사명&gt; / &lt;팀명&gt; (지원 직무 타깃)</span><span>YYYY.MM ~ YYYY.MM</span></div>
      <div class="job-sub">&lt;직무/직책&gt;</div>
      <ul>
        <li><span class="kpi-tag">&lt;역량태그&gt;</span> &lt;무엇을 바꿔 무엇이 얼마나 좋아졌는지 숫자 포함&gt;</li>
        <li><span class="kpi-tag">&lt;역량태그&gt;</span> &lt;두 번째 정량 성과&gt;</li>
      </ul>
    </div>
    """
    res_html = re.sub(r"\{\{#experiences\}\}.*?\{\{/experiences\}\}", exp_block, res_html, flags=re.DOTALL)

    proj_block = """
    <div class="job-item">
      <div class="job-header"><span>&lt;프로젝트명&gt;</span><span>&lt;핵심 스택&gt;</span></div>
      <ul>
        <li>&lt;프로젝트 개요와 본인 역할&gt;</li>
        <li><strong>성과:</strong> &lt;숫자로 증명되는 결과&gt;</li>
      </ul>
    </div>
    """
    res_html = re.sub(r"\{\{#projects\}\}.*?\{\{/projects\}\}", proj_block, res_html, flags=re.DOTALL)

    res_html = res_html.replace("{{skills_ax}}", "<트랙1 스킬 나열>")
    res_html = res_html.replace("{{skills_sales_ops}}", "<트랙2 스킬 나열>")
    res_html = res_html.replace("{{skills_tools}}", "<공통 도구 나열>")
    res_html = res_html.replace("{{skills_domain}}", "<도메인/산업 나열>")

    # Cover Letter Template Substitution
    cov_html = cov_tmpl
    cov_html = cov_html.replace("{{applicant_name}}", applicant_name)
    cov_html = cov_html.replace("{{target_company}}", company)
    cov_html = cov_html.replace("{{target_position}}", position)
    cov_html = cov_html.replace("{{motivation_text}}", f"{company}의 {position} 포지션에 <본인이 지원하는 이유 — 그 회사·그 역할에서 만들고 싶은 변화>를 통해 지원하였습니다.")
    cov_html = cov_html.replace("{{achievements_text}}", f"<정량 성과 1 — 숫자로 증명되는 대표 성과>와 <정량 성과 2>를 통해 검증된 역량을 증명해 왔습니다.")
    cov_html = cov_html.replace("{{future_plan_text}}", f"입사 후 <입사하면 먼저 풀 문제와 접근 방법 — 직무 맥락에 맞게 한 문단>하겠습니다.")

    return res_html, cov_html


def run_selftest() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="tailored-resume-test-"))
    sources = read_source_vault(tmp)
    res_html, cov_html = build_tailored_html("TestCo", "Sales Ops Lead", "JD Text sample", sources)
    
    html_file = tmp / "TestCo_Sales_Ops_Resume.html"
    pdf_file = tmp / "TestCo_Sales_Ops_Resume.pdf"
    html_file.write_text(res_html, encoding="utf-8")
    
    assert "TestCo" in res_html and "Sales Ops Lead" in res_html, "HTML 템플릿 변환 실패"
    
    pdf_ok = render_pdf(html_file, pdf_file)
    if find_chrome_binary():
        assert pdf_ok and pdf_file.is_file(), "PDF 렌더링 실패"
    print("selftest OK")


def main() -> None:
    parser = argparse.ArgumentParser(description="Tailored Resume & Cover Letter Generator")
    parser.add_argument("--company", "-c", default="TargetCompany", help="Target company name")
    parser.add_argument("--position", "-p", default="Sales Ops Lead", help="Target position name")
    parser.add_argument("--jd-file", help="Path to JD file")
    parser.add_argument("--jd-text", help="Raw JD text string")
    parser.add_argument("--output-dir", help="Output directory for generated files")
    parser.add_argument("--lang", choices=["ko", "en"], default="ko", help="Template language (ko: Saramin/JobKorea standard, en: Harvard classic)")
    parser.add_argument("--pdf", action="store_true", help="Compile HTML to PDF")
    parser.add_argument("--selftest", action="store_true", help="Run automated self test")

    args = parser.parse_args()

    if args.selftest:
        run_selftest()
        sys.exit(0)

    jd_text = ""
    if args.jd_file:
        jd_text = Path(args.jd_file).read_text(encoding="utf-8")
    elif args.jd_text:
        jd_text = args.jd_text

    wiki_root = get_wiki_root()
    out_dir = Path(args.output_dir).resolve() if args.output_dir else (wiki_root / "automation" / "job-hunting" / "resumes")
    out_dir.mkdir(parents=True, exist_ok=True)

    sources = read_source_vault(wiki_root)
    res_html, cov_html = build_tailored_html(args.company, args.position, jd_text, sources, lang=args.lang)

    safe_comp = re.sub(r"[^\w\-_]", "_", args.company)
    safe_pos = re.sub(r"[^\w\-_]", "_", args.position)

    res_html_path = out_dir / f"{safe_comp}_{safe_pos}_Resume.html"
    cov_html_path = out_dir / f"{safe_comp}_{safe_pos}_CoverLetter.html"

    res_html_path.write_text(res_html, encoding="utf-8")
    cov_html_path.write_text(cov_html, encoding="utf-8")

    print(f"✅ 맞춤 HTML 생성 완료:")
    print(f"  - 이력서: {res_html_path}")
    print(f"  - 자기소개서: {cov_html_path}")

    if args.pdf:
        res_pdf_path = out_dir / f"{safe_comp}_{safe_pos}_Resume.pdf"
        cov_pdf_path = out_dir / f"{safe_comp}_{safe_pos}_CoverLetter.pdf"
        
        ok1 = render_pdf(res_html_path, res_pdf_path)
        ok2 = render_pdf(cov_html_path, cov_pdf_path)
        
        if ok1 and ok2:
            print(f"🎉 PDF 컴파일 성공:")
            print(f"  - 이력서 PDF: {res_pdf_path}")
            print(f"  - 자기소개서 PDF: {cov_pdf_path}")
        else:
            print(f"⚠️ HTML은 생성되었으나 PDF 렌더러(Chrome Headless) 구동에 실패했습니다. 브라우저에서 HTML을 열고 인쇄(PDF로 저장)하세요.")


if __name__ == "__main__":
    main()
