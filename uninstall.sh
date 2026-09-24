#!/usr/bin/env bash
# hermes-agent-kit 제거기 — 사용: ./uninstall.sh [DATA_DIR] [--dry-run]
# install.sh가 남긴 $DATA/.kit-manifest.txt(코드 파일 목록: skills/scripts/bin)만 지운다.
# 절대 건드리지 않는 것(직접 확인하고 지울 것): wiki/(실제 노트가 쌓임), SOUL.md(캐릭터),
# search-profile.yaml(구직 설정), cron/jobs.json(본인이 병합한 자동화 설정), config.yaml.
set -euo pipefail
DRY=0; DATA=""
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    *) DATA="$arg" ;;
  esac
done
DATA="${DATA:-/opt/data}"
MANIFEST="$DATA/.kit-manifest.txt"

echo "== hermes-agent-kit 제거 → $DATA"
[ -f "$MANIFEST" ] || { echo "❌ $MANIFEST 없음 — install.sh로 설치한 적이 없거나, 매니페스트 도입 이전 버전으로 설치됨(수동으로 skills/scripts/bin 확인 필요)"; exit 1; }

N=$(sort -u "$MANIFEST" | grep -c . || true)
echo "  매니페스트: 파일 ${N}개 (skills/scripts/bin 아래 코드만 — wiki·SOUL·search-profile·cron·config는 그대로 둠)"
[ "$DRY" = "1" ] && echo "  (--dry-run: 실제로 지우지 않음)"

removed=0
while IFS= read -r rel; do
  [ -n "$rel" ] || continue
  f="$DATA/$rel"
  if [ -e "$f" ] || [ -L "$f" ]; then
    if [ "$DRY" = "1" ]; then echo "  would remove: $rel"
    else rm -f "$f"; removed=$((removed+1)); fi
  fi
done < <(sort -u "$MANIFEST")

if [ "$DRY" = "1" ]; then
  echo "== dry-run 끝 — 실제 제거하려면 --dry-run 없이 재실행"
else
  # 빈 디렉터리 정리 (skills/scripts/bin 안쪽만, 얕은 것부터 안전하게)
  for d in skills scripts bin; do
    [ -d "$DATA/$d" ] && find "$DATA/$d" -depth -type d -empty -delete 2>/dev/null || true
  done
  rm -f "$MANIFEST"
  echo "== 파일 ${removed}개 제거 완료"
  echo "   남겨둔 것 — 직접 확인 후 필요하면 지울 것:"
  echo "   - $DATA/wiki/ (실제 노트가 있을 수 있음)"
  echo "   - $DATA/SOUL.md, $DATA/search-profile.yaml"
  echo "   - cron/jobs.json 안에 병합한 잡 항목, config.yaml 안에 병합한 설정"
fi
