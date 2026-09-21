#!/usr/bin/env bash
# Hermes 헬스체크 — 컨테이너 안에서 실행. 상태를 한 화면에 요약.
# 사용: docker exec <container> bash /opt/data/bin/hermes-health.sh
set -u
D="${HERMES_DATA:-/opt/data}"
TR=$D/plugins/turn-router
say(){ printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

say "빌드 / 런타임"
echo "build_sha: $(cat /opt/hermes/.hermes_build_sha 2>/dev/null)"
echo "python:    $(python3 --version 2>&1)"

say "정적 스킬 인덱스 (매 턴 실리는 것)"
python3 - <<'PY' 2>/dev/null || echo "  (렌더 실패 — prompt_builder 확인)"
import sys; sys.path.insert(0,"/opt/hermes")
from agent.prompt_builder import build_skills_system_prompt
p = build_skills_system_prompt() or ""
import re
n = len(re.findall(r"^\s*- \S", p, re.M))
print(f"  렌더 스킬 {n}개 · {len(p)}자 ≈ {len(p)//4} 토큰")
PY

say "turn-router 동적 리트리버"
if [ -f "$TR/skill_retriever.py" ]; then
  python3 - <<'PY' 2>/dev/null || echo "  (retriever import 실패)"
import sys; sys.path.insert(0,"/opt/data/plugins/turn-router")
from skill_retriever import SkillRetriever
import re
src = open("/opt/data/plugins/turn-router/skill_retriever.py").read()
tri = len(re.findall(r'\("[^"]+",\s*"[a-z0-9-]+"\)', src))
print(f"  하드트리거 {tri}개")
for q in ("지금 몇 시야","일기 써줘","오늘 날씨"):
    print(f"    {q!r} -> {SkillRetriever._hard_trigger(q)}")
PY
else echo "  없음"; fi

say "커스텀 스킬 수"
echo "  /opt/data/skills: $(find $D/skills -maxdepth 3 -name SKILL.md 2>/dev/null | wc -l) 개"

say "최근 에러 (errors.log 마지막 5줄)"
tail -5 "$D/logs/errors.log" 2>/dev/null | sed 's/^/  /' || echo "  로그 없음"

say "위키 페이지 수 / 빈 디렉토리"
echo "  총 .md: $(find $D/wiki -name '*.md' 2>/dev/null | wc -l)"
for dir in "$D"/wiki/*/; do
  c=$(find "$dir" -type f 2>/dev/null | wc -l)
  [ "$c" -eq 0 ] && echo "  빈 디렉토리: ${dir#$D/wiki/}"
done

say "디스크"
df -h /opt/data 2>/dev/null | tail -1 | awk '{print "  /opt/data 사용 "$5" ("$3"/"$2")"}'
echo
echo "완료. 상세 절차는 wiki/hermes/hermes-ops.md 참조."
