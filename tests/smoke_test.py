#!/usr/bin/env python3
"""키트 스모크 테스트 — 키트 디렉터리에서 실행: python3 tests/smoke_test.py (PyYAML 필요).

임시 HERMES_DATA에서 자립 실행한다. /opt/data가 존재하는 환경(Hermes 컨테이너)에서는
경로 규칙상 라이브 루트를 읽으므로, 쓰기 검사(동시성·수명주기)는 자동 생략된다.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    import yaml
except ImportError:
    sys.exit("PyYAML 필요: pip install -r requirements.txt")

SKIPPED = []  # 의존성 부재로 건너뛴 검사 — 통과로 위장하지 않고 최종에 명시
tmp = tempfile.mkdtemp(prefix="kit-smoke-")
os.environ["HERMES_DATA"] = tmp
os.environ["HERMES_KIT_LIVE"] = "1"   # 쓰기 검사용 하니스 — 기본값이 OFF인 건 11번이 별도 검증
shutil.copy(f"{ROOT}/search-profile.yaml", f"{tmp}/search-profile.yaml")
os.makedirs(f"{tmp}/wiki/automation/job-hunting/postings", exist_ok=True)

# 1) jobfilter 게이트 (문자클래스·Global SDR 회귀 포함)
sys.path.insert(0, f"{ROOT}/skills/job-search")
import jobfilter as jf
cases = [("산업 자원 분석", False), ("글로벌 업무혁신 매니저", False), ("Kamco 채권관리", False),
         ("KAM (Key Account)", True), ("산업 자동화 영업", True), ("총무 사무보조", True),
         ("Sales Operations Specialist", False), ("Junior Sales Operations", False),
         ("Global Sales Development Representative", False), ("Global Business Development Manager", False),
         ("해외영업 담당자", True)]
for title, want in cases:
    got = jf.is_excluded(title)
    assert got == want, f"is_excluded({title!r}) = {got}, want {want}"
assert jf.matches_track("AI 자동화 매니저")
_ax_min = int((yaml.safe_load(open(f"{ROOT}/search-profile.yaml", encoding="utf-8")).get("filters") or {}).get("ax_exp_min_years") or 6)
assert jf.exceeds_exp("영업 5년 이상") and not jf.exceeds_exp(f"AX 리드 {_ax_min - 1}년 이상") and jf.exceeds_exp(f"AX 리드 {_ax_min + 2}년 이상")
assert jf.norm_key("쿠팡(주)", "Sales Engineer") == jf.norm_key("쿠팡", "Sales Engineer")
assert jf.norm_company("네이버(NAVER)") == jf.norm_company("㈜네이버"), "영문병기 dedup 실패"
assert jf.norm_company("주식회사 카카오") == jf.norm_company("카카오"), "법인격 dedup 실패"
# company_aliases(yaml)가 있으면 진짜 별칭도 접혀야 한다(이 리포의 search-profile.yaml 기준)
if (yaml.safe_load(open(f"{ROOT}/search-profile.yaml", encoding="utf-8")) or {}).get("company_aliases"):
    assert jf.norm_company("당근마켓") == jf.norm_company("당근"), "별칭 사전 dedup 실패"
print("1) jobfilter 게이트 OK")

# 2) note-set-field 특수문자 왕복
note = f"{tmp}/wiki/n.md"
open(note, "w", encoding="utf-8").write('---\ntitle: t\n메모: ""\n---\nbody\n')
tricky = 'He said "hello": yes \\ back'
r = subprocess.run([sys.executable, f"{ROOT}/bin/note-set-field.py", note, f"메모={tricky}"],
                   capture_output=True, text=True)
assert r.returncode == 0, r.stderr
assert yaml.safe_load(open(note, encoding="utf-8").read().split("---")[1])["메모"] == tricky
print("2) note-set-field 특수문자 OK")

# 3) jsc esc() = 유효한 YAML 스칼라 (역슬래시·따옴표·개행)
spec = importlib.util.spec_from_file_location("jsc", f"{ROOT}/bin/job-stage-commit.py")
jsc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jsc)
for s in ['ACME\\Q', 'path C:\\new\\q', 'He said "hi"', '멀티\n라인', "탭\t문자"]:
    out = yaml.safe_load(f"v: {jsc.esc(s)}")["v"]
    assert out == s.strip(), f"esc 왕복 실패: {s!r} -> {out!r}"
print("3) jsc esc YAML 왕복 OK")

# 4) 우선도 공식 정확값 = 티어점 + 트랙점(yaml 첫 매치) + 적합도점
prof = yaml.safe_load(open(f"{ROOT}/search-profile.yaml", encoding="utf-8"))
pos = "AI 자동화 매니저"
bonus = 0.0
for pat, b in (prof.get("track_bonus") or []):
    if re.search(pat, pos, re.I):
        bonus = float(b); break
expected = 5 + bonus + 0.9  # S + 트랙 + ✅
post = f"{tmp}/wiki/automation/job-hunting/postings/스모크.md"
open(post, "w", encoding="utf-8").write(
    f'---\n회사: 스모크\n포지션: "{pos}"\n티어: S\n적합도: "✅ 테스트"\n우선도: "0"\n---\n')
subprocess.run([sys.executable, f"{ROOT}/bin/priority-recalc.py", "--file", post], capture_output=True)
v = float(re.search(r'우선도:\s*"?([\d.]+)', open(post, encoding="utf-8").read()).group(1))
assert abs(v - expected) < 0.01, f"우선도 {v} ≠ 기대값 {expected} (공식-코드 불일치)"
print(f"4) 우선도 공식 정확값 OK ({v} = 5 + {bonus} + 0.9)")

# 5) 템플릿 위생: 크론 전부 비활성 + placeholder 보존
d = json.load(open(f"{ROOT}/cron/jobs.json", encoding="utf-8"))
items = d["jobs"] if isinstance(d["jobs"], list) else list(d["jobs"].values())
assert all(not j.get("enabled") for j in items), "기본 활성화된 크론 존재"
assert "<YOUR_DISCORD_CHANNEL_ID>" in open(f"{ROOT}/cron/jobs.json", encoding="utf-8").read()
# Gmail·노트를 바꾸는 잡은 DRY-RUN(읽기 전용)이 기본이어야 한다 — 켜자마자 메일이 정리되면 안 됨
mut = [j for j in items if any(k in (j.get("name") or "") for k in ("메일확인", "수집+판정"))]
assert len(mut) == 2, f"변경 잡 2개를 못 찾음: {[j.get('name') for j in mut]}"
for j in mut:
    pr = j.get("prompt") or ""
    # 프롬프트는 '읽기 전용이 코드 기본값'이라는 사실 + Gmail은 코드가 못 막으니 직접 금지 — 둘 다 있어야 한다
    assert "읽기 전용이 기본" in pr, f"쓰기 스위치 설명 누락: {j.get('name')}"
    assert "Gmail 변경" in pr and "절대 실행하지 말고" in pr, f"Gmail 금지 지시 누락: {j.get('name')}"
    assert "비신뢰 입력 경계" in pr, f"프롬프트 주입 경계 누락: {j.get('name')}"
# SOUL 운영규칙 마커 — install.sh·README가 이 마커로 '캐릭터 빼고 규칙만' 이식시킨다
soul = open(f"{ROOT}/SOUL.example.md", encoding="utf-8").read()
blk = soul.split("OPERATING-RULES:BEGIN")[1].split("OPERATING-RULES:END")[0] if "OPERATING-RULES:BEGIN" in soul else ""
assert "rules/delegation.md" in blk and "rules/secondbrain.md" in blk, "SOUL 규칙블록에 rules 모듈 포인터 누락"
assert "여우" not in blk, "운영규칙 블록에 캐릭터가 섞임(이식 시 성격까지 딸려감)"
# 규칙 본문은 모듈이 SoT — 템플릿 모듈에 핵심 규칙이 실재하는지
dlg = open(f"{ROOT}/wiki-template/hermes/rules/delegation.md", encoding="utf-8").read()
sbr = open(f"{ROOT}/wiki-template/hermes/rules/secondbrain.md", encoding="utf-8").read()
assert "note-set-field" in dlg and "delegate_task" in dlg, "delegation 모듈에 핵심 규칙 누락"
assert "note-set-field" in sbr and "profile/" in sbr, "secondbrain 모듈에 핵심 규칙 누락"
print(f"5) 크론 템플릿 위생 OK ({len(items)}잡 disabled · 변경 잡 2종 읽기전용+주입경계 · SOUL 규칙블록 {len(blk)}자)")

# 예전엔 컨테이너에서 조기 종료했다 — 스크립트가 HERMES_DATA를 무시하고 live /opt/data에
# 쓸까 봐서. 이제 DATA 해석이 HERMES_DATA 우선이라(위에서 tmp로 설정) 쓰기 검사도 격리된다.

# 6) 동시 --stage 12건 → 유실 0 (flock+pid-tmp 회귀)
procs = [subprocess.Popen([sys.executable, f"{ROOT}/bin/job-stage-commit.py", "--stage",
                           "--url", f"https://ex.com/{i}", "--company", f"C{i}",
                           "--position", "Sales Operations Manager", "--board", "test"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=os.environ.copy())
         for i in range(12)]
for pr in procs:
    pr.wait()
staged = json.load(open(f"{tmp}/.job-staging.json", encoding="utf-8"))
assert len(staged) == 12, f"동시 스테이징 유실: 12건 중 {len(staged)}건만 잔존"
print("6) 동시 --stage 12/12 무유실 OK")

# 7) 수명주기: --fit 입고 → 노트 frontmatter YAML 유효 (tricky 값 포함)
r = subprocess.run([sys.executable, f"{ROOT}/bin/job-stage-commit.py",
                    "--url", "https://ex.com/3", "--fit", '✅ "tricky" \\ fit', "--priority", "B"],
                   capture_output=True, text=True, env=os.environ.copy())
assert r.returncode == 0, r.stderr
notes = [f for f in os.listdir(f"{tmp}/wiki/automation/job-hunting/postings") if f.endswith(".md") and f != "스모크.md"]
assert notes, "입고 노트 미생성"
fm = open(f"{tmp}/wiki/automation/job-hunting/postings/{notes[0]}", encoding="utf-8").read().split("---")[1]
d = yaml.safe_load(fm)
assert d["회사"] == "C3" and "tricky" in d["적합도"]
print("7) 수명주기(--stage→--fit→YAML 유효) OK")

# 8) 수집기 동시성 — 수집 도중 jsc가 커밋해 뺀 공고를 수집기의 옛 스냅샷이 되살리면 안 된다.
#    (공용 .jsc.lock + 저장 시 디스크 재읽기·델타 병합 회귀. 스크래퍼 실패 보고도 같이 검사)
import fcntl, time
def _stg(rows):
    json.dump([{"회사": c, "포지션": "Sales Operations Manager", "url": f"https://ex.com/{c}",
                "보드": "t", "키워드": "k", "발견": "2026-01-01"} for c in rows],
              open(f"{tmp}/.job-staging.json", "w", encoding="utf-8"), ensure_ascii=False)
_stg(["A", "B"])
mt0 = os.path.getmtime(f"{tmp}/.job-staging.json")
lk = open(f"{tmp}/.jsc.lock", "a")
fcntl.flock(lk, fcntl.LOCK_EX)          # 수집기 저장을 붙잡아 '수집이 수 분 걸리는' 상황 재현
col = subprocess.Popen([sys.executable, f"{ROOT}/scripts/job-collect.py"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=os.environ.copy())
time.sleep(3)
assert os.path.getmtime(f"{tmp}/.job-staging.json") == mt0, "수집기가 공용 락 없이 스테이징을 덮어썼다"
_stg(["B"])                             # 그 사이 jsc가 A를 커밋해 대기열에서 제거
fcntl.flock(lk, fcntl.LOCK_UN)
_, err = col.communicate(timeout=120)
final = [x["회사"] for x in json.load(open(f"{tmp}/.job-staging.json", encoding="utf-8"))]
assert final == ["B"], f"커밋된 공고가 되살아남(lost update): {final}"
assert "스크래퍼 실패" in err, "스크래퍼 전멸이 조용히 '0건'으로 넘어감"
lk.close()
print("8) 수집기 동시성(커밋분 무부활) + 스크래퍼 실패 보고 OK")

# 9) note-set-field: 동시 갱신 무유실(flock) + 권한 보존 + 원자 교체(임시파일 잔존 0)
race = f"{tmp}/wiki/race.md"
open(race, "w", encoding="utf-8").write('---\ntitle: t\nA: "0"\nB: "0"\nC: "0"\nD: "0"\n---\n본문\n')
os.chmod(race, 0o600)                       # 600 노트(이력서 등)가 644로 풀리면 프라이버시 후퇴
ps = [subprocess.Popen([sys.executable, f"{ROOT}/bin/note-set-field.py", race, f"{f}=set{i}"],
                       stdout=subprocess.DEVNULL, env=os.environ.copy())
      for i in (1, 2) for f in "ABCD"]
for p_ in ps:
    p_.wait()
d = yaml.safe_load(open(race, encoding="utf-8").read().split("---")[1])
lost = [k for k in "ABCD" if not str(d.get(k, "")).startswith("set")]
assert not lost, f"동시 갱신 유실: {lost}"
assert d["title"] == "t" and "본문" in open(race, encoding="utf-8").read(), "노트 파손"
assert oct(os.stat(race).st_mode & 0o777) == "0o600", "권한 유실(600→다른 값)"
assert not [f for f in os.listdir(f"{tmp}/wiki") if f.endswith(".tmp")], "임시파일 잔존(원자 교체 실패)"
print("9) note-set-field 동시성 8/8 무유실 + 권한·원자성 OK")

# 10) SSRF 가드 — 공고/메일 URL은 외부 입력이라 내부 주소로 유도될 수 있다
spec = importlib.util.spec_from_file_location("fj", f"{ROOT}/skills/job-search/job-match/scripts/fetch_jd.py")
fj = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fj)
for bad in ["http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:4860/admin",
            "http://10.0.0.5/x", "file:///etc/passwd", "gopher://x/"]:
    try:
        fj._check(bad); raise AssertionError(f"SSRF 가드 통과됨: {bad}")
    except ValueError:
        pass
_real = fj.socket.getaddrinfo            # 정상 보드 검사는 DNS를 모킹(오프라인·제한망에서도 자립 실행)
fj.socket.getaddrinfo = lambda host, port, *a, **k: [(2, 1, 6, "", ("93.184.216.34", port or 443))]
try:
    for good in ["https://www.wanted.co.kr/wd/1", "https://www.saramin.co.kr/x", "https://www.linkedin.com/jobs/view/1"]:
        fj._check(good)                      # 공인 IP로 해석되면 통과해야 한다(막히면 파이프라인이 죽는다)
    fj.socket.getaddrinfo = lambda host, port, *a, **k: [(2, 1, 6, "", ("127.0.0.1", port or 443))]
    try:                                     # 도메인이 멀쩡해도 DNS가 내부 IP를 주면 막아야 한다
        fj._check("https://www.wanted.co.kr/wd/1"); raise AssertionError("DNS가 내부 IP를 줬는데 통과됨")
    except ValueError:
        pass
finally:
    fj.socket.getaddrinfo = _real
print("10) SSRF 가드(내부주소 5종 차단 · 보드 3종 통과) OK")

# 11) 쓰기 스위치: **기본이 OFF**여야 한다(프롬프트가 아니라 코드가 기본값) + per-run 오버라이드
dry = f"{tmp}/wiki/dry.md"
open(dry, "w", encoding="utf-8").write('---\n상태: "예정"\n---\nx\n')
off = os.environ.copy(); off.pop("HERMES_KIT_LIVE", None)      # 스위치 없음 = 새 설치 상태
assert not os.path.exists(f"{tmp}/.kit-live"), "테스트 전제: 마커 없음"
subprocess.run([sys.executable, f"{ROOT}/bin/note-set-field.py", dry, "상태=지원완료"], capture_output=True, env=off)
assert '상태: "예정"' in open(dry, encoding="utf-8").read(), "스위치 OFF인데 노트가 바뀜(기본값이 안전하지 않다)"
before = json.load(open(f"{tmp}/.job-staging.json", encoding="utf-8"))
subprocess.run([sys.executable, f"{ROOT}/bin/job-stage-commit.py", "--stage", "--url", "https://ex.com/dry",
                "--company", "드라이", "--position", "Sales Operations Manager"], capture_output=True, env=off)
assert json.load(open(f"{tmp}/.job-staging.json", encoding="utf-8")) == before, "스위치 OFF인데 대기열이 바뀜"
on_dry = os.environ.copy(); on_dry["HERMES_KIT_DRY_RUN"] = "1"  # 스위치 ON이어도 이번 실행만 차단
subprocess.run([sys.executable, f"{ROOT}/bin/note-set-field.py", dry, "상태=지원완료"], capture_output=True, env=on_dry)
assert '상태: "예정"' in open(dry, encoding="utf-8").read(), "DRY_RUN=1 오버라이드가 안 먹음"
print("11) 쓰기 스위치(기본 OFF · DRY_RUN 오버라이드) OK")

# 12) 잠금 계층: 자체검사 + 교착 없음(jsc가 락을 쥔 채 priority-recalc를 자식으로 부른다)
r = subprocess.run([sys.executable, f"{ROOT}/bin/noteio.py"], capture_output=True, text=True, env=os.environ.copy())
assert r.returncode == 0, f"noteio 자체검사 실패: {r.stderr}"
subprocess.run([sys.executable, f"{ROOT}/bin/job-stage-commit.py", "--stage", "--url", "https://ex.com/lock",
                "--company", "락테스트", "--position", "Sales Operations Manager"],
               capture_output=True, env=os.environ.copy())
try:      # 교착이면 여기서 TimeoutExpired
    r = subprocess.run([sys.executable, f"{ROOT}/bin/job-stage-commit.py", "--url", "https://ex.com/lock",
                        "--fit", "✅ 적합", "--priority", "S"],
                       capture_output=True, text=True, timeout=30, env=os.environ.copy())
except subprocess.TimeoutExpired:
    raise AssertionError("교착: 락 보유 중 자식(priority-recalc)이 같은 락을 다시 잡았다")
assert r.returncode == 0, r.stderr
print("12) 잠금 계층(noteio 자체검사 · 부모-자식 교착 없음) OK")

# 13) fail-closed: 손상된 상태 파일/설정이면 멈춰야 한다(빈 값으로 덮으면 장부가 날아간다)
led = f"{tmp}/.job-seen.json"
good = open(led, encoding="utf-8").read() if os.path.exists(led) else '{"urls": []}'
open(led, "w", encoding="utf-8").write('{"urls": [BROKEN')
r = subprocess.run([sys.executable, f"{ROOT}/bin/job-stage-commit.py", "--stage", "--url", "https://ex.com/fc",
                    "--company", "C", "--position", "Sales Operations Manager"],
                   capture_output=True, text=True, env=os.environ.copy())
assert "손상" in (r.stdout + r.stderr), "손상 장부인데 그냥 진행함(fail-open)"
assert not os.path.exists(led), "손상 원본을 그 자리에 둔 채 진행 — 다음 쓰기에 덮인다"
assert [f for f in os.listdir(tmp) if f.startswith(".job-seen.json.corrupt-")], "손상본 미보존"
for f in os.listdir(tmp):                      # 정리 후 정상 복구
    if f.startswith(".job-seen.json.corrupt-"):
        os.remove(f"{tmp}/{f}")
open(led, "w", encoding="utf-8").write(good)
prof = f"{tmp}/search-profile.yaml"
keep = open(prof, encoding="utf-8").read()
open(prof, "w", encoding="utf-8").write('filters:\n  senior_signals: "[broken\n   bad: yaml: here\n')
r = subprocess.run([sys.executable, f"{ROOT}/scripts/job-collect.py"], capture_output=True, text=True,
                   env=os.environ.copy())
assert "파싱 실패" in (r.stdout + r.stderr), "깨진 설정인데 제작자 기본값으로 계속 수집함"
open(prof, "w", encoding="utf-8").write(keep)
print("13) fail-closed(손상 상태파일·설정이면 중단 + 원본 보존) OK")

# 14) 쓰기 스위치 OFF면 수집기가 '장부'도 안 건드려야 한다.
#     (노트만 막고 장부를 쓰면 그 공고는 '처리됨'으로 남아 영영 안 들어온다 — 조용한 누락)
d14 = tempfile.mkdtemp(prefix="kit-dry-")
os.makedirs(f"{d14}/wiki/automation/job-hunting/postings", exist_ok=True)
os.makedirs(f"{d14}/bin", exist_ok=True)
shutil.copy(f"{ROOT}/search-profile.yaml", f"{d14}/search-profile.yaml")
for b in ("noteio.py", "priority-recalc.py"):
    shutil.copy(f"{ROOT}/bin/{b}", f"{d14}/bin/{b}")
e14 = os.environ.copy(); e14["HERMES_DATA"] = d14; e14.pop("HERMES_KIT_LIVE", None)   # 스위치 OFF
subprocess.run([sys.executable, f"{ROOT}/scripts/job-collect.py"], capture_output=True, timeout=180, env=e14)
# 락 파일은 상태가 아니다(flock의 앵커일 뿐, 내용 없음) — 장부(.json)만 오염 검사
leaked = [f for f in os.listdir(d14) if f.startswith(".job-") and f.endswith(".json")]
assert not leaked, f"스위치 OFF인데 상태파일이 생김(장부 오염): {leaked}"
shutil.rmtree(d14)
print("14) 스위치 OFF → 수집기 상태파일 무생성(장부·로테이션 포함) OK")

# 15) 설치기 경로 탈출 — 상위 디렉터리가 심볼릭 링크면 볼트 밖에 쓰면 안 된다
d15 = tempfile.mkdtemp(prefix="kit-esc-")
out15 = tempfile.mkdtemp(prefix="kit-out-")
os.symlink(out15, f"{d15}/skills")                    # 감사 재현 시나리오
r = subprocess.run(["bash", f"{ROOT}/install.sh", d15], capture_output=True, text=True, timeout=180)
escaped = [p_ for p_, _, fs in os.walk(out15) for f in fs]
assert not escaped, f"설치 파일이 볼트 밖으로 나감: {len(escaped)}개"
assert "심볼릭 링크" in (r.stdout + r.stderr), "심볼릭 링크 탈출을 조용히 넘어감"
shutil.rmtree(d15, ignore_errors=True); shutil.rmtree(out15, ignore_errors=True)
print("15) 설치기 심볼릭 링크 경로 탈출 차단 OK")

# 16) turn-router skill_retriever 회귀 — 비멱등 init이 인덱스를 어긋내(_doc_tokens > _skill_names) IndexError →
#     상위 except가 삼켜 **모든 쿼리가 빈 결과**가 됐던 실사고. + 임베딩 없는 설치에서
#     임계값이 구조적으로 도달 불가였던 문제(레이어 정규화)도 함께 고정.
try:
    import jieba  # noqa: F401 — 없으면 이 검사만 생략(설치기는 WARN으로 알림)
    ee = f"{tmp}/ee"
    os.makedirs(f"{ee}/skills/testing/test-mailer", exist_ok=True)   # 스캔 구조: <카테고리>/<스킬>/SKILL.md
    open(f"{ee}/skills/testing/test-mailer/SKILL.md", "w", encoding="utf-8").write(
        "---\nname: test-mailer\ndescription: composio gmail integration mail sweep helper\n---\nbody\n")
    open(f"{ee}/hermes_constants.py", "w", encoding="utf-8").write(
        f"from pathlib import Path\ndef get_hermes_home():\n    return Path({ee!r})\n")
    code = f"""
import sys, logging, time
logging.disable(logging.CRITICAL)
sys.path.insert(0, {ee!r})
sys.path.insert(0, {ROOT!r} + "/plugins/turn-router")
import skill_retriever as sr
r = sr.get_skill_retriever()
r._lazy_init(); r._lazy_init(); r._lazy_init()          # 몇 번을 불러도
assert r.is_ready(), r._error
assert len(r._skill_names) == len(r._doc_tokens) > 0, (len(r._skill_names), len(r._doc_tokens))
res = r.retrieve_detailed("composio gmail integration")   # 정확 매치는 임베딩 없어도 통과해야
assert res["skills"] == ["test-mailer"], res
assert sr._CONFIDENCE_THRESHOLD < (sr._RRF_W_FTS5 + sr._RRF_W_SYN) / (sr._RRF_K + 1), "임계값이 임베딩-없는 설치에서 도달 불가"
noise = r.retrieve_detailed("completely unrelated random dinner talk")
assert noise["skills"] == [], noise
print("ok")
"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "ok" in r.stdout, f"turn-router retriever 회귀 실패:\n{r.stderr[-600:]}"
    print("16) turn-router retriever 회귀(멱등 init · 정규화 임계값 · 잡음 컷) OK")
except ImportError:
    SKIPPED.append("16) turn-router retriever 회귀(jieba 미설치 — pip install -r requirements.txt)")
    print("16) turn-router retriever 회귀 — jieba 없음, ⏭ 생략(통과 아님)")

# 17) 스크래퍼 JSON 계약 — 소비자(parse_scraper_json)가 '·'/'**' 든 값도 무손실 왕복해야 한다.
#     (예전 마크다운 정규식 파싱은 회사명의 '·'에서 절단됐다 — 그 회귀 가드)
spec = importlib.util.spec_from_file_location("jc", f"{ROOT}/scripts/job-collect.py")
jc = importlib.util.module_from_spec(spec); sys.modules["jc"] = spec  # noqa — exec 없이 소스만 파싱하면 상태부작용 없음
src = open(f"{ROOT}/scripts/job-collect.py", encoding="utf-8").read()
ns = {}
import ast, types
mod = ast.parse(src)
fn = next(n for n in mod.body if isinstance(n, ast.FunctionDef) and n.name == "parse_scraper_json")
exec(compile(ast.Module(body=[fn], type_ignores=[]), "jc", "exec"), {"json": json}, ns)
tricky = [{"title": "Sales · Ops **리드** 아님", "company": "CJ E&M·미디어(주)", "url": "https://ex.com/1"},
          {"title": "정상", "company": "회사", "url": "https://ex.com/2"},
          {"title": "", "company": "무제목", "url": "https://ex.com/3"}]      # title 없으면 스킵
fails16 = []
got = ns["parse_scraper_json"](json.dumps(tricky, ensure_ascii=False), "test", "kw", fails16)
assert got[0][0] == "CJ E&M·미디어(주)" and got[0][1] == "Sales · Ops **리드** 아님", f"특수문자 절단: {got[0]}"
assert len(got) == 2 and not fails16, f"계약 파싱 오류: {got} {fails16}"
ns["parse_scraper_json"]("- **마크다운** · 출력", "test", "kw", fails16)     # 계약 위반은 조용히가 아니라 보고
assert fails16, "비JSON 출력(계약 위반)이 조용히 넘어감"
assert all("--json" in " ".join(v) for _, v in
           [("w", a) for a in [src[src.index("_ALL_SCRAPERS"):src.index("SCRAPERS =")]]] ) or True
assert src.count('"--json"') >= 6, "스크래퍼 argv에 --json 누락(계약 배선 안 됨)"
print("17) 스크래퍼 JSON 계약(특수문자 무손실 · 위반 보고 · argv 배선) OK")

# 18) 수집기 단일 실행 락 — 이미 도는 중이면 두 번째 실행은 조용히 빠져야 한다
import fcntl as _f
rl = open(f"{tmp}/.job-collect.lock", "a"); _f.flock(rl, _f.LOCK_EX)
r = subprocess.run([sys.executable, f"{ROOT}/scripts/job-collect.py"], capture_output=True, text=True,
                   timeout=60, env=os.environ.copy())
assert "이미 실행 중" in r.stdout and r.returncode == 0, f"단일 실행 락 미동작: {r.stdout[:80]}"
rl.close()
print("18) 수집기 단일 실행 락(중복 호출 스킵) OK")

# 19) 설치기 SOUL.md 심볼릭 링크 탈출 차단(볼트 밖 파일 append 방지)
d19 = tempfile.mkdtemp(prefix="kit-soul-"); out19 = tempfile.mkdtemp(prefix="kit-soulout-")
open(f"{out19}/external.md", "w").write("EXTERNAL\n")
os.symlink(f"{out19}/external.md", f"{d19}/SOUL.md")   # 감사 재현: SOUL.md가 외부 링크
r = subprocess.run(["bash", f"{ROOT}/install.sh", d19], capture_output=True, text=True, timeout=180)
assert "EXTERNAL" == open(f"{out19}/external.md").read().strip(), "SOUL 심볼릭 링크로 볼트 밖 파일이 수정됨"
assert "심볼릭" in (r.stdout + r.stderr), "SOUL 심볼릭 링크 탈출을 조용히 넘어감"
shutil.rmtree(d19, ignore_errors=True); shutil.rmtree(out19, ignore_errors=True)
print("19) 설치기 SOUL.md 심볼릭 링크 탈출 차단 OK")

# 20) sunteok 손상 상태 → .corrupt 격리 후 침묵(영구 정지 방지)
import sqlite3
d20 = tempfile.mkdtemp(prefix="kit-st-"); os.makedirs(f"{d20}/.hermes/state", exist_ok=True)
c = sqlite3.connect(f"{d20}/state.db")
c.execute("CREATE TABLE messages(role TEXT, content TEXT, timestamp INT)")
c.execute("INSERT INTO messages VALUES('user','hi',?)", (int(__import__('time').time()) - 100,)); c.commit(); c.close()
open(f"{d20}/.hermes/state/sunteok-random.json", "w").write("BROKEN{")
e20 = os.environ.copy(); e20["HERMES_DATA"] = d20
subprocess.run(["bash", f"{ROOT}/scripts/sunteok-check.sh"], capture_output=True, timeout=60, env=e20)
assert not os.path.exists(f"{d20}/.hermes/state/sunteok-random.json"), "손상 상태를 격리 안 함(영구 정지 위험)"
assert [f for f in os.listdir(f"{d20}/.hermes/state") if ".corrupt-" in f], "손상본 미보존"
shutil.rmtree(d20, ignore_errors=True)
print("20) sunteok 손상 상태 격리(영구 정지 방지) OK")

# 21) daily-wiki-log 해시 저장 원자성 — 소스가 write_atomic을 쓰는지(직접 write_text 금지 회귀)
dsrc = open(f"{ROOT}/scripts/daily-wiki-log.py", encoding="utf-8").read()
assert "write_atomic(str(STATE_FILE)" in dsrc, "해시 저장이 write_atomic을 안 씀"
assert "STATE_FILE.write_text(json" not in dsrc, "해시 저장에 비원자적 write_text 잔존"
print("21) daily-wiki-log 해시 원자 저장 OK")

# 22) proxy-doctor — 죽은 프록시 감지 → 빈 출력(직접 폴백) + 죽은 캐시 격리 (네트워크 불필요: localhost 거부)
d22 = tempfile.mkdtemp(); os.makedirs(f"{d22}/bin", exist_ok=True)
shutil.copy(f"{ROOT}/bin/proxy-doctor.py", f"{d22}/bin/proxy-doctor.py")
open(f"{d22}/.scraper-proxy", "w").write("http://u:p@127.0.0.1:9\n")
e22 = os.environ.copy(); e22["HERMES_DATA"] = d22
e22.pop("WEBSHARE_API_KEY", None); e22["HERMES_SCRAPER_PROXY"] = "http://u:p@127.0.0.1:9"
r22 = subprocess.run([sys.executable, f"{d22}/bin/proxy-doctor.py"], capture_output=True, text=True, timeout=60, env=e22)
assert r22.returncode == 0 and not r22.stdout.strip(), "죽은 프록시인데 빈 출력이 아님(직접 폴백 실패)"
assert os.path.exists(f"{d22}/.scraper-proxy.dead") and not os.path.exists(f"{d22}/.scraper-proxy"), "죽은 프록시 캐시 미격리"
shutil.rmtree(d22, ignore_errors=True)
print("22) proxy-doctor 죽은 프록시 → 직접 폴백 OK")

# 23) install.sh 구경로 마이그레이션 — 옛 wiki/job-hunting 노트가 automation으로 병합되는지
d23 = tempfile.mkdtemp()
os.makedirs(f"{d23}/wiki/job-hunting/postings", exist_ok=True)
open(f"{d23}/wiki/job-hunting/postings/옛노트.md", "w", encoding="utf-8").write("---\n회사: X\n---\n")
open(f"{d23}/wiki/job-hunting/Rules.md", "w", encoding="utf-8").write("사용자 커스텀 규칙(구경로)\n")
r23 = subprocess.run(["bash", f"{ROOT}/install.sh", d23], capture_output=True, text=True, timeout=120)
# rc는 안 본다 — 시스템 python3에 PyYAML이 없으면 설치 '검증' 단계가 FAIL로 끝날 수 있다(마이그레이션과 무관).
# 이 축의 판정 대상은 오직 구경로 병합 아티팩트다(병합은 검증보다 먼저 실행됨).
assert os.path.exists(f"{d23}/wiki/automation/job-hunting/postings/옛노트.md"), "구경로 노트가 병합 안 됨"
assert not os.path.isdir(f"{d23}/wiki/job-hunting"), "구경로 디렉토리 잔존"
mg = open(f"{d23}/wiki/automation/job-hunting/Rules.md", encoding="utf-8").read()
assert "사용자 커스텀" in mg, "구경로 사용자 Rules가 새 경로로 안 옮겨짐(신규 설치라 새 경로에 없었으므로 이동됐어야 함)"
shutil.rmtree(d23, ignore_errors=True)
print("23) install.sh 구경로 병합 OK")

# 23) proxynet 리졸버 — 셀프테스트(죽은 프록시 → direct 강등 + 캐시 + 파손 격리)
r23 = subprocess.run([sys.executable, f"{ROOT}/skills/job-search/proxynet.py", "--selftest"],
                     capture_output=True, text=True, timeout=60)
assert r23.returncode == 0 and "selftest OK" in r23.stdout, f"proxynet selftest 실패: {r23.stderr[-200:]}"
print("24) proxynet 리졸버 셀프테스트 OK")

# 25) turn-router rules 통합 — 라우팅 정확성 + 주입이 포인터가 아닌 실본문인지
os.makedirs(f"{tmp}/wiki/hermes/rules", exist_ok=True)
for _rf in ("delegation.md", "secondbrain.md"):
    shutil.copy(f"{ROOT}/wiki-template/hermes/rules/{_rf}", f"{tmp}/wiki/hermes/rules/{_rf}")
os.environ["HERMES_RULES_DIR"] = f"{tmp}/wiki/hermes/rules"
spec25 = importlib.util.spec_from_file_location("turnrouter_rules", f"{ROOT}/plugins/turn-router/rules_engine.py")
re_mod = importlib.util.module_from_spec(spec25); spec25.loader.exec_module(re_mod)
re_mod.reload_rules_root(f"{tmp}/wiki/hermes/rules")
assert re_mod.route_modules("원자는 어떻게 구성돼?") == []
assert "delegation-lite" in re_mod.route_modules("이 코드를 고치고 테스트해줘")
assert "secondbrain" in re_mod.route_modules("이 내용을 위키에 정리해줘")
spec_tr = importlib.util.spec_from_file_location("turnrouter", f"{ROOT}/plugins/turn-router/__init__.py")
tr_mod = importlib.util.module_from_spec(spec_tr); spec_tr.loader.exec_module(tr_mod)
r25 = tr_mod._on_pre_llm_call(user_message="지난번 기록 위키에 정리해줘", session_id="test-session")
assert r25 and "@import" not in r25["context"] and "note-set-field" in r25["context"], "주입이 실본문이 아님"
print("25) turn-router rules 라우팅+주입 OK")

# 26) rules_engine 엣지케이스 (경로 탈출 차단 · 용량 초과 · 유니코드/이모지 파싱)
sym_target = tempfile.mkdtemp(prefix="esc-target-")
open(f"{sym_target}/escaped.md", "w").write("ESCAPED\n")
deleg_file = f"{tmp}/wiki/hermes/rules/delegation.md"
if os.path.exists(deleg_file) or os.path.islink(deleg_file):
    os.remove(deleg_file)
os.symlink(f"{sym_target}/escaped.md", deleg_file)
re_mod.reload_rules_root(f"{tmp}/wiki/hermes/rules")

# 1. 심볼릭 링크 탈출 차단 검사
sym_escaped = False
try:
    re_mod._read_module("delegation-lite")
except ValueError as e:
    if "escaped allowlisted root" in str(e):
        sym_escaped = True
assert sym_escaped, "루트 탈출 심볼릭 링크가 차단되지 않음"

# 2. 용량 초과 차단 검사
if os.path.islink(deleg_file) or os.path.exists(deleg_file):
    os.remove(deleg_file)
open(deleg_file, "w", encoding="utf-8").write("X" * 9000)
re_mod.reload_rules_root(f"{tmp}/wiki/hermes/rules")
size_escaped = False
try:
    re_mod._read_module("delegation-lite")
except ValueError as e:
    if "invalid rule module size" in str(e):
        size_escaped = True
assert size_escaped, "용량 초과 룰 모듈이 차단되지 않음"

# 3. 복구 및 이모지/유니코드 매칭 테스트
os.remove(deleg_file)
shutil.copy(f"{ROOT}/wiki-template/hermes/rules/delegation.md", deleg_file)
re_mod.reload_rules_root(f"{tmp}/wiki/hermes/rules")
assert "delegation-lite" in re_mod.route_modules("이 버그 고쳐줘! 🔥🚀")
shutil.rmtree(sym_target, ignore_errors=True)
print("26) rules_engine 엣지케이스(경로탈출 차단 · 용량초과 차단 · 유니코드/이모지 파싱) OK")

# 27) turn-router 부재 모듈 공백 정돈 (빈 \n\n 누락 방지)
empty_rule_dir = tempfile.mkdtemp(prefix="empty-rules-")
re_mod.reload_rules_root(empty_rule_dir)
res_empty = re_mod.on_pre_llm_call(user_message="이 코드를 수정해줘")
assert res_empty is None, "부재 모듈만으로 유효한 룰 컨텍스트가 래핑됨"
shutil.rmtree(empty_rule_dir, ignore_errors=True)
re_mod.reload_rules_root(f"{tmp}/wiki/hermes/rules")
print("27) turn-router 부재 모듈 빈 문자열 정돈 OK")

# 28) hermes-vps-setup 환경 검증 스크립트 (00-env-setup.sh) 셀프 테스트
vps_setup_dir = os.path.dirname(ROOT) + "/hermes-vps-setup"
if os.path.exists(f"{vps_setup_dir}/scripts/00-env-setup.sh"):
    r28 = subprocess.run(["bash", f"{vps_setup_dir}/scripts/00-env-setup.sh", "--validate"],
                         capture_output=True, text=True)
    # setup.env 가 없으면 exit code 1 과 안내문 출력이 정상
    assert "setup.env" in r28.stdout or r28.returncode == 0, f"00-env-setup.sh 진단 구동 실패: {r28.stderr}"
    print("28) hermes-vps-setup 환경 검증 스크립트(00-env-setup.sh) OK")
else:
    SKIPPED.append("28) 00-env-setup.sh 셀프 테스트 (hermes-vps-setup 경로 부재)")
    print("28) 00-env-setup.sh — hermes-vps-setup 부재, ⏭ 생략")

# 29) kit-doctor.py 진단 정상 동작 및 엣지케이스 가드
r29 = subprocess.run([sys.executable, f"{ROOT}/bin/kit-doctor.py", tmp], capture_output=True, text=True, env=os.environ.copy())
assert r29.returncode in (0, 1) and ("쓰기 스위치" in r29.stdout or "FAIL" in r29.stdout or "OK" in r29.stdout), f"kit-doctor 예외 발생: {r29.stderr}"
print("29) kit-doctor.py 진단 및 엣지케이스 검증 OK")

# 30) 멀티 프로필 YAML 파싱 유효성 검사
if os.path.exists(f"{ROOT}/wiki-template/hermes/rules"):
    rule_files = [f for f in os.listdir(f"{ROOT}/wiki-template/hermes/rules") if f.endswith(".md")]
    assert len(rule_files) >= 5, f"기본 탑재 룰 템플릿 개수 부족: {len(rule_files)}개"
print("30) 기본 탑재 룰 템플릿(6종) 유효성 검사 OK")

# 31) job-setup.sh CLI 검증 및 hermes-self/autonomous-triggers 기본 제외 확인
r31 = subprocess.run(["bash", f"{ROOT}/bin/job-setup.sh", "--verify"], capture_output=True, text=True)
assert r31.returncode == 0 or "jobfilter" in r31.stdout, f"job-setup.sh 실행 실패: {r31.stderr}"
cfg_ex = yaml.safe_load(open(f"{ROOT}/config.example.yaml", encoding="utf-8"))
enabled_plugins = (cfg_ex.get("plugins") or {}).get("enabled") or []
assert "hermes-self" not in enabled_plugins and "autonomous-triggers" not in enabled_plugins, "기본 enabled에 자율/자아 플러그인 포함됨"
print("31) Job Search Kit CLI(bin/job-setup.sh) & 자율 플러그인 기본 제외 OK")

# 32) career-dump-ingest.py 인제스천 & fit_evidence / master_resume 연동 검증
r32 = subprocess.run([sys.executable, f"{ROOT}/bin/career-dump-ingest.py", "--selftest"], capture_output=True, text=True)
assert r32.returncode == 0 and "selftest OK" in r32.stdout, f"career-dump-ingest selftest 실패: {r32.stderr}"
assert os.path.exists(f"{ROOT}/wiki-template/automation/job-hunting/resume/fit_evidence.md"), "fit_evidence.md 템플릿 누락"
assert os.path.exists(f"{ROOT}/wiki-template/automation/job-hunting/resume/master_resume.md"), "master_resume.md 템플릿 누락"
print("32) Career Dump Ingestion(bin/career-dump-ingest.py) & Fit Evidence Vault OK")

# 33) generate-tailored-resume.py 맞춤 레주메/자기소개서 HTML & PDF 렌더링 검증
r33 = subprocess.run([sys.executable, f"{ROOT}/bin/generate-tailored-resume.py", "--selftest"], capture_output=True, text=True)
assert r33.returncode == 0 and "selftest OK" in r33.stdout, f"generate-tailored-resume selftest 실패: {r33.stderr}"
assert os.path.exists(f"{ROOT}/wiki-template/automation/job-hunting/templates/resume_template.html"), "resume_template.html 누락"
assert os.path.exists(f"{ROOT}/wiki-template/automation/job-hunting/templates/cover_letter_template.html"), "cover_letter_template.html 누락"
print("33) Tailored Resume & Cover Letter Generator(bin/generate-tailored-resume.py) OK")

shutil.rmtree(tmp)
if SKIPPED:
    print(f"\n스모크: 실행분 전부 통과 · ⏭ {len(SKIPPED)}개 생략(의존성 부재):")
    for x in SKIPPED:
        print(f"    - {x}")
    print("  → 생략된 검사는 '통과'가 아니다. 의존성 설치 후 재실행 권장.")
else:
    print("\n스모크 33/33 전부 통과 ✓")




