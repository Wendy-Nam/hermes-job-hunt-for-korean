#!/usr/bin/env bash
# =====================================================================
# 💼 Hermes Agent Kit - Job Search Kit 모듈화 관리 및 커스터마이징 도구
# =====================================================================
# 구직/채용 정보 수집 파이프라인 (jobfilter, search-profile.yaml, job-collect, jsc)을
# 손쉽게 초기화, 커스터마이징 및 자가검증할 수 있는 모듈화 CLI 스크립트입니다.
#
# 사용법:
#   $ bash bin/job-setup.sh --init             (search-profile.yaml 기본 템플릿 생성)
#   $ bash bin/job-setup.sh --verify           (구직 파이프라인 의존성 & 필터 자가검증)
#   $ bash bin/job-setup.sh --test "채용공고제목" (지정한 공고 제목의 제외/포함 테스트)
#   $ bash bin/job-setup.sh --dry-run          (스테이징 변경 없이 수집기 시뮬레이션)
# =====================================================================
set -euo pipefail

KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 색상 정의
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

PROFILE_YAML="$KIT_DIR/search-profile.yaml"

show_help() {
  echo -e "${CYAN}${BOLD}Job Search Kit - 관리 및 커스터마이징 도구${NC}"
  echo "사용법: bash bin/job-setup.sh [옵션]"
  echo ""
  echo "옵션:"
  echo "  --init, -i          search-profile.yaml 설정 파일이 없으면 기본 템플릿 생성"
  echo "  --verify, -v        구직 키트 파이프라인 무결성 및 YAML 파싱 검증"
  echo "  --test, -t <TITLE>  공고 제목을 테스트하여 jobfilter 필터링(제외 여부/트랙) 결과 출력"
  echo "  --dry-run, -d       상태 파일 오염 없이 채용 수집기(job-collect.py) DRY_RUN 테스트"
  echo "  --help, -h          도움말 출력"
}

init_profile() {
  if [ -f "$PROFILE_YAML" ]; then
    echo -e "${GREEN}✓ search-profile.yaml 이 이미 존재합니다: $PROFILE_YAML${NC}"
  else
    echo -e "${BLUE}▶ search-profile.yaml 템플릿을 생성합니다...${NC}"
    cat > "$PROFILE_YAML" <<'YAML'
# =====================================================================
# 🎯 Hermes Job Search Kit - 구직 프로필 및 필터 설정 (search-profile.yaml)
# =====================================================================
filters:
  # 타겟 포지션 트랙 (우선순위 가중치)
  target_tracks:
    - name: "AI Automation"
      keywords: ["AX", "AI 자동화", "Agent", "n8n", "RAG"]
    - name: "Sales Operations"
      keywords: ["Sales Ops", "세일즈 오퍼레이션", "영업기획", "Sales Engineer"]
    - name: "Key Account Management"
      keywords: ["KAM", "Key Account", "해외영업", "B2B 영업"]

  # 경험 연차 최소 기준 (AX/AI 리드 직군)
  ax_exp_min_years: 6

  # 제외 키워드 (정규표현식 지원)
  exclude_keywords:
    - "아르바이트"
    - "사무보조"
    - "인턴"
    - "채권관리"

# 법인명 및 브랜드 별칭 사전 (동일 회사 중복 수집 방지)
company_aliases:
  당근: "당근마켓"
  카카오: "주식회사 카카오"
  네이버: "NAVER"

# 트랙 가중치 (priority-recalc.py 계산 기준)
track_bonus:
  - ["AI.*자동화|AX", 2.0]
  - ["Sales.*Ops|영업기획", 1.5]
  - ["KAM|Key Account", 1.0]
YAML
    echo -e "${GREEN}✅ search-profile.yaml 이 성공적으로 생성되었습니다.${NC}"
  fi
}

verify_pipeline() {
  echo -e "${CYAN}🔍 Job Search Kit 자가 진증 중...${NC}"
  local err=0

  # 1. 파일 존재 여부
  if [ -f "$PROFILE_YAML" ]; then
    echo -e "${GREEN}✓ search-profile.yaml 존재함${NC}"
  else
    echo -e "${RED}❌ search-profile.yaml 이 없습니다 (bash bin/job-setup.sh --init 로 생성하세요)${NC}"
    ((err++))
  fi

  if [ -f "$KIT_DIR/skills/job-search/jobfilter.py" ]; then
    echo -e "${GREEN}✓ jobfilter.py 모듈 존재함${NC}"
  else
    echo -e "${RED}❌ skills/job-search/jobfilter.py 모듈 누락${NC}"
    ((err++))
  fi

  # 2. Python 테스트 파이프라인
  python3 -c "
import sys, os
sys.path.insert(0, '$KIT_DIR/skills/job-search')
try:
    import yaml
    import jobfilter as jf
    prof = yaml.safe_load(open('$PROFILE_YAML', encoding='utf-8'))
    assert prof and 'filters' in prof, 'YAML 루트에 filters 항목이 필요합니다'
    print('  ✓ Python YAML 및 jobfilter 파싱 정상')
except Exception as e:
    print(f'  ❌ 파이프라인 오류: {e}')
    sys.exit(1)
" || ((err++))

  if [ "$err" -eq 0 ]; then
    echo -e "${GREEN}🎉 Job Search Kit 무결성 검증 통과!${NC}"
    return 0
  else
    echo -e "${RED}❌ $err 개의 검증 실패가 발생했습니다.${NC}"
    return 1
  fi
}

test_title() {
  local title="${1:-}"
  if [ -z "$title" ]; then
    echo -e "${RED}공고 제목을 입력하세요 (예: bash bin/job-setup.sh --test 'Sales Operations Manager')${NC}"
    exit 1
  fi

  echo -e "${CYAN}🧪 테스트 공고 제목: '$title'${NC}"
  python3 -c "
import sys
sys.path.insert(0, '$KIT_DIR/skills/job-search')
import jobfilter as jf
ex = jf.is_excluded('$title')
tr = jf.matches_track('$title')
print(f'  - 제외(Exclude) 여부: {\"❌ 제외 대상\" if ex else \"✅ 통과(포함)\"}')
print(f'  - 매칭(Track) 트랙: {tr}')
"
}

run_dry_run() {
  echo -e "${BLUE}▶ job-collect.py DRY_RUN 시뮬레이션 구동...${NC}"
  HERMES_KIT_DRY_RUN=1 python3 "$KIT_DIR/scripts/job-collect.py" || true
}

# 인자 처리
if [ $# -eq 0 ]; then
  verify_pipeline
  exit 0
fi

case "$1" in
  --init|-i)
    init_profile
    ;;
  --verify|-v)
    verify_pipeline
    ;;
  --test|-t)
    shift
    test_title "${1:-}"
    ;;
  --dry-run|-d)
    run_dry_run
    ;;
  --help|-h)
    show_help
    ;;
  *)
    echo -e "${RED}알 수 없는 옵션: $1${NC}"
    show_help
    exit 1
    ;;
esac
