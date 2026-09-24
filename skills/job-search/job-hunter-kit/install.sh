#!/bin/bash
# job-hunter-kit 설치기 — 스킬 + no-agent 크론 1개를 한 번에 묶는다.
# Usage:
#   ./install.sh                          # 스킬 검증 + 크론 등록 (기본: 매일 08:00, "백엔드")
#   ./install.sh --keyword "데이터 엔지니어" --schedule "0 8 * * *" --deliver telegram
#   ./install.sh --uninstall               # 크론 + 상태 제거 (스킬 폴더는 유지)
#   ./install.sh --dry-run                 # 실제 등록 없이 명령만 출력
set -euo pipefail
KIT="$(cd "$(dirname "$0")" && pwd)"
KW="백엔드"; SCHED="0 8 * * *"; DELIVER="local"; UNINSTALL=0; DRY=0
while [ $# -gt 0 ]; do case "$1" in
  --keyword) KW="$2"; shift 2;;
  --schedule) SCHED="$2"; shift 2;;
  --deliver) DELIVER="$2"; shift 2;;
  --uninstall) UNINSTALL=1; shift;;
  --dry-run) DRY=1; shift;;
  *) echo "Unknown: $1 (options: --keyword --schedule --deliver --uninstall --dry-run)"; exit 1;;
esac; done

digest_sh="$HOME/.hermes/scripts/job-hunter-digest.sh"
cron_name="구직 다이제스트 ($KW)"

if [ "$UNINSTALL" = 1 ]; then
  id=$(hermes cron list 2>/dev/null | grep -B5 -F "$cron_name" | grep -oE '[0-9a-f]{12}' | head -n 1 || true)
  if [ -n "${id:-}" ]; then
    [ "$DRY" = 1 ] && echo "DRY: hermes cron remove $id" || hermes cron remove "$id"
  else echo "크론 없음: $cron_name"; fi
  [ "$DRY" = 1 ] && echo "DRY: rm $digest_sh" || rm -f "$digest_sh"
  echo "제거 완료 (스킬 폴더·seen 상태는 유지)"; exit 0
fi

echo "== 1/3 자가 테스트"
python3 "$KIT/tests/test_hunt.py" 2>&1 | tail -n 3
echo "== 2/3 보드 실동작 확인 (wanted 1건)"
python3 "$KIT/scripts/hunt.py" "$KW" --limit 1 --boards wanted 2>&1 | head -n 5
echo "== 3/3 다이제스트 래퍼 설치 → $digest_sh"
mkdir -p "$HOME/.hermes/scripts"
PY="$(command -v python3)"
cat > "$digest_sh" <<EOF
#!/bin/bash
# 자동생성 (job-hunter-kit install.sh). 직접 수정 금지 — 재설치 시 덮어씀.
exec "$PY" "$KIT/scripts/digest.py" "$KW" --state-dir "\$HOME/.hermes/job-hunter"
EOF
chmod +x "$digest_sh"
echo "== 크론 등록 (no-agent, 빈 결과=미발송)"
if [ "$DRY" = 1 ]; then
  echo "DRY: hermes cron create --no-agent --script job-hunter-digest.sh --name '$cron_name' --deliver $DELIVER '$SCHED'"
else
  hermes cron create --no-agent --script job-hunter-digest.sh \
    --name "$cron_name" --deliver "$DELIVER" "$SCHED"
fi
echo "완료. 다음 실행: hermes cron list | grep -F '$cron_name'"
echo "테스트 발송: $digest_sh | head (신규 없으면 빈 출력이 정상)"
