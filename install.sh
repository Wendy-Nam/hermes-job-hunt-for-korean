#!/usr/bin/env bash
# hermes-agent-kit 구직 번들 설치기
# 사용: ./install.sh [DATA_DIR] [--upgrade] [--dry-run]
#   --upgrade: 번들이 관리하는 코드만 교체하고, 기존 파일은 .backups/upgrade-*/에 보존.
#   --dry-run: 파일을 쓰지 않고 설치 작업과 검증 대상만 출력.
# 기본 DATA_DIR: /opt/data. 기존 파일은 덮어쓰지 않습니다.
set -euo pipefail
UPGRADE=0; DRY_RUN=0; DATA=""
for arg in "$@"; do
  case "$arg" in
    --upgrade) UPGRADE=1 ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help)
      sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) DATA="$arg" ;;
  esac
done
DATA="${DATA:-/opt/data}"
BUNDLE="$(cd "$(dirname "$0")/bundle" && pwd)"
FAIL=0; WARN=0
UPTS="$(date +%Y%m%d_%H%M%S)"

say()  { echo "  $*"; }
warn() { echo "  ⚠️  $*"; WARN=$((WARN+1)); }
fail() { echo "  ❌ $*"; FAIL=$((FAIL+1)); }

DRY_LABEL=""; [ "$DRY_RUN" = "1" ] && DRY_LABEL=" (dry-run)"
echo "== hermes-agent-kit 설치 → $DATA$DRY_LABEL"
# 코어는 HERMES_DATA를 따르지만 크론 프롬프트에는 /opt/data가 문자열로 박혀 있다.
[ "$DATA" = "/opt/data" ] || echo "  ⚠️  /opt/data가 아니다 — 크론 프롬프트의 '/opt/data' 문자열은 자동으로 안 바뀐다. 설치 후 직접 치환할 것."
[ -d "$DATA" ] || { echo "❌ $DATA 없음 — Hermes 데이터 볼륨 경로를 인자로 지정하라"; exit 1; }

# 대상이 디렉터리가 아닌데 존재하면 즉시 실패 (조용한 무설치 방지)
for d in skills scripts bin; do
  if [ -e "$DATA/$d" ] && [ ! -d "$DATA/$d" ]; then
    echo "❌ $DATA/$d 가 디렉터리가 아님 — 정리 후 재시도"; exit 1
  fi
done

# 설치 루트 안의 주요 디렉터리가 심볼릭 링크면 파일이 볼트 밖에 써진다 → 중단
for d in skills scripts bin tmp wiki; do
  if [ -L "$DATA/$d" ]; then
    echo "❌ $DATA/$d 가 심볼릭 링크다 — 설치 파일이 $DATA 밖에 써질 수 있어 중단한다."
    echo "   (링크를 풀고 실제 디렉터리로 만든 뒤 재시도)"; exit 1
  fi
done

DATA_REAL="$(cd "$DATA" && pwd -P)"   # 심볼릭 링크를 푼 진짜 설치 루트

if [ "$DRY_RUN" = "1" ]; then
  N=$(find "$BUNDLE" -type f -not -path '*__pycache__*' -not -name '*.pyc' | wc -l | tr -d ' ')
  say "DRY-RUN: $N개 번들 파일 확인 (대상은 $DATA)"
  say "DRY-RUN: 코드/스킬 → $DATA/{skills,scripts,bin}"
  say "DRY-RUN: 볼트 템플릿 → $DATA/wiki"
  say "DRY-RUN: config/cron은 자동 병합하지 않고 번들 경로에 보존"
  exit 0
fi

# 설치 대상이 정말 $DATA 안인지 — 상위 디렉터리가 심볼릭 링크면 파일이 볼트 밖에 써진다.
# (최종 파일만 -L로 보는 걸론 못 잡는다: $DATA/skills 자체가 링크인 경우)
inside() {
  local d; d="$(cd "$(dirname "$1")" 2>/dev/null && pwd -P)" || return 1
  case "$d/" in "$DATA_REAL"/*|"$DATA_REAL"/) return 0 ;; *) return 1 ;; esac
}

# 소유권은 '우리가 만든 것'에만 준다 — 기존 사용자 파일은 소유자도 안 건드린다.
own()   { chown 10000:10000 "$@" 2>/dev/null || OWNFAIL=1; }
mkown() {  # mkdir -p 하되, 새로 생기는 레벨만 chown (기존 상위 디렉터리는 불가침)
  local d="$1" stack=()
  while [ ! -d "$d" ] && [ "$d" != "/" ] && [ "$d" != "." ]; do stack+=("$d"); d="$(dirname "$d")"; done
  mkdir -p "$1"
  [ ${#stack[@]} -eq 0 ] || own "${stack[@]}"
}

# 파일 단위 보강 복사 — 기존 파일은 절대 덮지 않음 (cp -n은 coreutils 9.2+에서 스킵을 실패로 반환해 사용 불가)
# $3=managed: 1이면 '키트가 관리하는 코드'라서 --upgrade 시 키트 소스와 다른 기존 파일을
#             $DATA/.backups/upgrade-<ts>/ 에 보존한 뒤 교체한다. (wiki 등 사용자 데이터는 managed=0 — 업그레이드에도 불가침)
copy_missing() {  # $1=src_dir $2=dst_dir $3=managed(0/1)
  local n=0 up=0 managed="${3:-0}"
  while IFS= read -r f; do
    local dst="$2/${f#./}"
    if [ -e "$dst" ] || [ -L "$dst" ]; then
      # 업그레이드: 관리 코드가 키트 소스와 다르면 백업 후 교체(재실행해도 보안 수정이 안 올라가던 구멍)
      if [ "$UPGRADE" = "1" ] && [ "$managed" = "1" ] && [ -f "$dst" ] && [ ! -L "$dst" ] \
         && ! cmp -s "$1/${f#./}" "$dst"; then
        if ! inside "$dst"; then fail "설치 루트 밖으로 나감: $dst"; return 1; fi
        local bk="$DATA/.backups/upgrade-$UPTS/${dst#"$DATA"/}"
        mkdir -p "$(dirname "$bk")"; cp -p "$dst" "$bk"
        cp "$1/${f#./}" "$dst"; own "$dst"
        up=$((up+1))
      fi
      continue
    fi
    if [ "$DRY_RUN" = "1" ]; then say "DRY-RUN $dst"; n=$((n+1)); continue; fi
    mkown "$(dirname "$dst")"
    if ! inside "$dst"; then      # 상위 경로가 링크로 밖을 가리키는 경우(중첩 포함)
      fail "설치 루트 밖으로 나감: $dst — 경로에 심볼릭 링크가 있다(중단)"; return 1
    fi
    cp "$1/${f#./}" "$dst"
    own "$dst"
    # managed=1(코드: skills/scripts/bin)만 기록 — 볼트 템플릿(managed=0)은
    # 설치 후 사용자가 실제 노트로 채우므로 uninstall이 지우면 안 된다.
    [ "$managed" = "1" ] && echo "${dst#"$DATA"/}" >> "$DATA/.kit-manifest.txt"
    n=$((n+1))
  done < <(cd "$1" && find . -type f -not -path "*__pycache__*" -not -name "*.pyc")
  if [ "$up" -gt 0 ]; then say "$2 ← ${n}파일 보강 · ${up}파일 업그레이드(원본: .backups/upgrade-$UPTS/)"
  else say "$2 ← ${n}파일 보강"; fi
}

# 1) 코드/스킬 복사
for d in skills scripts bin tmp; do mkown "$DATA/$d"; done
copy_missing "$BUNDLE/job-search/skills"  "$DATA/skills"  1
copy_missing "$BUNDLE/job-search/scripts" "$DATA/scripts" 1
copy_missing "$BUNDLE/job-search/bin"     "$DATA/bin"     1
if [ -e "$DATA/search-profile.yaml" ]; then say "search-profile.yaml 이미 존재 — 보존"
else cp "$BUNDLE/config/search-profile.yaml" "$DATA/search-profile.yaml"; own "$DATA/search-profile.yaml"; fi

# 2-0) 구경로 마이그레이션(2026-07-28): 한때 템플릿이 wiki/job-hunting(구경로)로 깔리던
#      창이 있었다 — 코드는 wiki/automation/job-hunting 을 쓴다. 구경로가 있으면 병합한다
#      (새 경로에 없는 파일만 이동 = 기존 노트 불가침, 겹친 잔여는 백업으로 치우고 구경로 제거).
if [ -d "$DATA/wiki/job-hunting" ]; then
  say "구경로 wiki/job-hunting 발견 → wiki/automation/job-hunting 으로 병합"
  mkown "$DATA/wiki/automation/job-hunting"
  ( cd "$DATA/wiki/job-hunting" && find . -type f -print0 ) | while IFS= read -r -d '' f; do
    rel="${f#./}"; tgt="$DATA/wiki/automation/job-hunting/$rel"
    if [ ! -e "$tgt" ]; then
      mkdir -p "$(dirname "$tgt")"
      mv "$DATA/wiki/job-hunting/$rel" "$tgt"
    fi
  done
  if find "$DATA/wiki/job-hunting" -type f 2>/dev/null | grep -q .; then
    mkdir -p "$DATA/.backups/path-migrate-$UPTS"
    mv "$DATA/wiki/job-hunting" "$DATA/.backups/path-migrate-$UPTS/"
    say "겹친 파일 잔여는 .backups/path-migrate-$UPTS/ 에 보존"
  else
    rm -rf "$DATA/wiki/job-hunting"
  fi
fi

# 2) 위키 골격 — 파일 단위 보강 (기존 노트는 내용도 소유권도 안 건드림)
mkown "$DATA/wiki"
copy_missing "$BUNDLE/vault" "$DATA/wiki" 0
mkown "$DATA/wiki/automation/job-hunting/postings"

# 4) 소유권 결과 (Hermes 컨테이너 uid 10000 — 새로 만든 것만 위에서 부여했다)
# 복구 안내에 chown -R을 쓰면 이 설치기의 원칙(기존 파일 불가침)을 안내가 깨뜨린다 →
# 이번에 새로 설치된 파일만 root로 다시 돌려서 잡게 한다.
[ -z "${OWNFAIL:-}" ] || warn "일부 chown 실패(비root 실행?) — root로 이 설치기를 다시 실행하면 새로 설치된 파일만 소유권이 잡힌다(기존 파일은 그대로). 수동으로 할 거면 방금 복사된 파일만 골라서 chown 할 것 — chown -R은 기존 노트까지 바꾼다."

# 4b) Hermes(uid 10000)가 실제로 쓸 수 있는지 검사 — 파일이 있어도 못 쓰면 파이프라인이 죽는다.
#     상태파일은 $DATA 바로 아래(.job-seen.json 등), 노트는 wiki/automation/job-hunting/postings 에 쓴다.
if [ "$DATA" = "/opt/data" ] || [ -n "${HERMES_UID:-}" ]; then
  writable_by_hermes() {  # $1=dir — Hermes 컨테이너(기본 uid 10000)의 쓰기 가능 여부
    local uid="${HERMES_UID:-10000}"
    python3 - "$1" "$uid" <<'PY'
import os, stat, sys
st = os.stat(sys.argv[1]); m = st.st_mode; uid = int(sys.argv[2])
ok = (st.st_uid == uid and m & stat.S_IWUSR) or (st.st_gid == uid and m & stat.S_IWGRP) or (m & stat.S_IWOTH)
sys.exit(0 if ok else 1)
PY
  }
  for d in "$DATA" "$DATA/wiki" "$DATA/wiki/automation/job-hunting/postings" "$DATA/tmp" "$DATA/scripts" "$DATA/bin"; do
    [ -d "$d" ] || continue
    if writable_by_hermes "$d"; then say "OK  쓰기가능(uid ${HERMES_UID:-10000}) $d"
    else fail "uid ${HERMES_UID:-10000}이 못 씀: $d — chown ${HERMES_UID:-10000}:${HERMES_UID:-10000} '$d' 필요"; fi
  done
else
  say "일반 사용자 설치 — uid 10000 쓰기 검사 생략 (HERMES_UID 설정 시 검사)"
fi

# 5) 설치 검증
echo "== 설치 검증"
for f in "bin/job-stage-commit.py" "bin/note-set-field.py" "bin/priority-recalc.py" "bin/noteio.py" \
         "scripts/job-collect.py" "skills/job-search/jobfilter.py" "search-profile.yaml" \
         "wiki/automation/job-hunting/Rules.md" "wiki/automation/job-hunting/Dashboard.base" \
         "wiki/automation/job-hunting/README.md" "wiki/automation/job-hunting/resume/master_resume.md" \
         "wiki/automation/job-hunting/research/면접-리서치-템플릿.md"; do
  [ -e "$DATA/$f" ] && say "OK  $f" || fail "누락 $f"
done
[ -d "$DATA/wiki/automation/job-hunting/postings" ] && say "OK  wiki/automation/job-hunting/postings/" || fail "누락 wiki/automation/job-hunting/postings/"
N_SKILL=$(find "$DATA/skills" -name SKILL.md 2>/dev/null | wc -l | tr -d ' ')
[ "$N_SKILL" -ge 10 ] && say "OK  스킬 ${N_SKILL}종" || fail "스킬 ${N_SKILL}종뿐 — 복사 실패 의심"
python3 -c "import sys; sys.path.insert(0, '$DATA/skills/job-search'); import jobfilter" 2>/dev/null \
  && say "OK  jobfilter import" || fail "jobfilter import 실패 (python3/PyYAML 확인)"

# 6) placeholder 잔존 안내 (config·cron은 사용자가 병합하는 파일이라 여기선 안내만)
PH=$(grep -c "<YOUR_DISCORD\|<REDACTED_SET_YOUR_OWN>" "$BUNDLE/cron/jobs.json" "$BUNDLE/config/config.example.yaml" 2>/dev/null | awk -F: '{s+=$2} END{print s}')
say "참고: 템플릿 placeholder ${PH}곳 — config/cron 병합 시 전부 본인 값으로"

echo
if [ "$FAIL" -gt 0 ]; then echo "== 결과: FAIL (${FAIL} 실패, ${WARN} 경고) — 위 항목을 해결한 뒤 재실행"; exit 1
elif [ "$WARN" -gt 0 ]; then echo "== 결과: WARN (${WARN} 경고) — 동작은 하나 위 항목 확인 권장"
else echo "== 결과: PASS"; fi

cat <<'MSG'
== 다음 단계 (수동):
1. `bundle/config/config.example.yaml`의 설정을 본인 `config.yaml`에 병합하고 키를 채운다.
2. `bundle/cron/jobs.json`에서 원하는 잡만 본인 `cron/jobs.json`에 병합 — 전 잡이 enabled:false다.
   origin.chat_id를 채우고 thread_id는 null로 둔다(스레드 오배송 방지).
3. 스모크 테스트(키트 디렉터리에서 실행): cd <키트경로> && python3 tests/smoke_test.py
4. Hermes 재시작.
MSG
