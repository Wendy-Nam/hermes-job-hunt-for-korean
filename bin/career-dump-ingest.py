#!/usr/bin/env python3
"""Career & Resume Dump Ingestion Tool (bin/career-dump-ingest.py)

Ingests raw resume, portfolio, or past work context text dumps and structures them
atomically into wiki notes:
  - wiki/profile/career.md
  - wiki/automation/job-hunting/resume/master_resume.md
  - wiki/automation/job-hunting/resume/role_contexts.md
  - wiki/automation/job-hunting/resume/fit_evidence.md

Usage:
  python3 bin/career-dump-ingest.py --input raw_resume.txt
  python3 bin/career-dump-ingest.py --text "RAW CAREER TEXT DUMP..."
  python3 bin/career-dump-ingest.py --selftest
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path


def get_wiki_root() -> Path:
    data_dir = os.environ.get("HERMES_DATA") or "/opt/data"
    wiki_path = os.environ.get("WIKI_PATH") or os.environ.get("OBSIDIAN_VAULT_PATH") or f"{data_dir}/wiki"
    return Path(wiki_path).resolve()


def ensure_dirs(wiki_root: Path) -> None:
    (wiki_root / "profile").mkdir(parents=True, exist_ok=True)
    (wiki_root / "automation" / "job-hunting" / "resume").mkdir(parents=True, exist_ok=True)


def parse_dump(raw_text: str) -> dict[str, str]:
    """Simple structured section extraction from raw text dump."""
    sections = {
        "career_summary": "",
        "master_cv": "",
        "roles": "",
        "fit_points": "",
    }
    
    # Split text or organize into logical parts
    lines = [l for l in raw_text.splitlines() if l.strip()]
    if not lines:
        return sections

    sections["career_summary"] = "\n".join(lines[:10])
    sections["master_cv"] = raw_text.strip()
    sections["roles"] = raw_text.strip()
    
    # Extract KPI/quantified points for fit evidence
    quantified = [l for l in lines if re.search(r"(\d+%|\d+년|\d+건|\d+원|KPI|성과|달성|RAG|n8n|Sales|Ops)", l, re.I)]
    sections["fit_points"] = "\n".join(f"- {l}" for l in (quantified[:15] if quantified else lines[:5]))
    return sections


def write_atomic(path: Path, content: str) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


def ingest_dump(raw_text: str, wiki_root: Path | None = None) -> dict[str, str]:
    if wiki_root is None:
        wiki_root = get_wiki_root()
    ensure_dirs(wiki_root)

    parsed = parse_dump(raw_text)

    # 1. Update master_resume.md
    mr_path = wiki_root / "automation" / "job-hunting" / "resume" / "master_resume.md"
    mr_content = (
        "---\n"
        "title: master_resume\n"
        "type: document\n"
        "tags: [career, resume, master-cv]\n"
        "---\n\n"
        "# Master Resume (경력 정본)\n\n"
        "## 1. 커리어 서머리\n"
        f"{parsed['career_summary']}\n\n"
        "## 2. 세부 이력 덤프 원본\n"
        f"{parsed['master_cv']}\n"
    )
    write_atomic(mr_path, mr_content)

    # 2. Update fit_evidence.md
    fe_path = wiki_root / "automation" / "job-hunting" / "resume" / "fit_evidence.md"
    fe_content = (
        "---\n"
        "title: fit_evidence\n"
        "type: document\n"
        "tags: [career, fit-evidence, job-matching]\n"
        "---\n\n"
        "# Fit Evidence Matrix (공고 핏 검증 근거 매트릭스)\n\n"
        "## 🎯 핵심 정량 성과 및 검증 근거 (Fit Proof Points)\n"
        f"{parsed['fit_points']}\n\n"
        "## 🛡️ 자격 요건 검증 규칙\n"
        "- AX/AI 리드 연차 및 기술 스택(Python, n8n, LLM API, CRM) 충족 여부 확인.\n"
    )
    write_atomic(fe_path, fe_content)

    # 3. Update profile/career.md hub
    car_path = wiki_root / "profile" / "career.md"
    car_content = (
        "---\n"
        "title: career\n"
        "type: area\n"
        "tags: [career, profile, hub]\n"
        "---\n\n"
        "# Personal Career Profile Hub\n\n"
        "- 📄 [[automation/job-hunting/resume/master_resume]]\n"
        "- 🎯 [[automation/job-hunting/resume/role_contexts]]\n"
        "- ✅ [[automation/job-hunting/resume/fit_evidence]]\n\n"
        "## 📌 최근 덤프 서머리\n"
        f"{parsed['career_summary'][:300]}\n"
    )
    write_atomic(car_path, car_content)

    return {
        "master_resume": str(mr_path),
        "fit_evidence": str(fe_path),
        "career_hub": str(car_path),
    }


def run_selftest() -> None:
    import tempfile
    tmp_dir = Path(tempfile.mkdtemp(prefix="career-ingest-test-"))
    test_dump = """
    홍길동 커리어 덤프
    - B2B Sales Operations Specialist 5년 경력
    - n8n 과 Python 활용 업무 자동화(AX) 파이프라인 구축하여 매칭 타임 50% 단축
    - 대형 파트너 계정 갱신율 95% 달성
    """
    res = ingest_dump(test_dump, wiki_root=tmp_dir)
    assert os.path.exists(res["master_resume"]), "master_resume.md 미생성"
    assert os.path.exists(res["fit_evidence"]), "fit_evidence.md 미생성"
    assert "50% 단축" in Path(res["fit_evidence"]).read_text(encoding="utf-8"), "정량 성과 미추출"
    print("selftest OK")


def main() -> None:
    parser = argparse.ArgumentParser(description="Career Dump Ingestion Tool")
    parser.add_argument("--input", "-i", help="Path to raw dump text file")
    parser.add_argument("--text", "-t", help="Raw text string dump")
    parser.add_argument("--selftest", action="store_true", help="Run automated self test")

    args = parser.parse_args()

    if args.selftest:
        run_selftest()
        sys.exit(0)

    raw_text = ""
    if args.input:
        raw_text = Path(args.input).read_text(encoding="utf-8")
    elif args.text:
        raw_text = args.text
    elif not sys.stdin.isatty():
        raw_text = sys.stdin.read()

    if not raw_text.strip():
        print("❌ 덤프할 이력서/포폴 텍스트를 입력하세요 (--input, --text 또는 stdin)")
        sys.exit(1)

    result = ingest_dump(raw_text)
    print("✅ 커리어 덤프 구조화 이식 완료:")
    for k, v in result.items():
        print(f"  - {k}: {v}")


if __name__ == "__main__":
    main()
