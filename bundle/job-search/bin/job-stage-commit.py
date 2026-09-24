#!/usr/bin/env python3
"""job-stage-commit.py — 판정 결과를 스테이징에서 위키로 커밋(유일한 입고 경로).

트리아지(LLM)가 판정만 하고, 파일 조작은 이 스크립트가 결정론적으로 처리한다.
  --list                          스테이징 대기열 출력(판정용)
  --url U --fit "✅... 근거" [--priority S|A|B|C|D]
                                  합격 → postings/ 노트 생성(적합도+티어 채움) + 스테이징 제거
                                  --priority = 회사 티어(노트 `티어` 필드. S 글로벌테크·유명 유니콘 /
                                  A 검증된 강소·이름있는 외국계 / B 일반 외국계 / C 평범 /
                                  D 영세·SI·에이전시=후순위). 기존 `우선도` 필드는 숫자 점수로 별개.
  --url U --reject "사유"          불합격 → 거부장부 등록 + 스테이징 제거 (위키에 안 들어감)
  --stage --company C --position P --url U [--board B]
                                  알림메일 등 크론 밖에서 발견한 공고를 스테이징 대기열에 등록
                                  (제목 게이트 즉석 적용 — 컷이면 장부 등재 후 미등록). 판정은
                                  다음 트리아지 절차가 수행. LLM이 스테이징 JSON을 직접 만지지 말 것.
  --applied --company C --position P [--url U] [--memo M]
                                  메일 접수확인 등으로 확인된 '이미 지원한' 공고를
                                  상태=waiting 노트로 직접 생성(스테이징 무관, 멱등 —
                                  같은 회사+포지션 노트가 있으면 no-op).
"""
import argparse
import fcntl
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta

DATA = os.environ.get("HERMES_DATA") or "/opt/data"   # 명시 env 우선(테스트·호스트 격리), 없으면 컨테이너 기본
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from noteio import DRY_RUN, why_dry, write_atomic   # 쓰기 스위치·원자적 저장 = noteio 단일 계층
POSTINGS = f"{DATA}/wiki/automation/job-hunting/postings"
os.makedirs(POSTINGS, exist_ok=True)  # 새 설치에서 디렉터리 부재로 즉사 방지

def _wjson(path, obj, indent=1):
    """원자적 JSON 쓰기 — 임시파일 후 os.replace (도중 사망 시 원본 보존)."""
    if DRY_RUN:
        print(f"[DRY-RUN] {os.path.basename(path)} 갱신 생략 ({len(obj)}건)")
        return
    tmp = f"{path}.{os.getpid()}.tmp"  # 동시 실행 시 tmp 충돌 방지
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
    os.chmod(tmp, os.stat(path).st_mode & 0o7777 if os.path.exists(path) else 0o600)  # 장부=지원이력 → 비공개
    os.replace(tmp, path)

STAGING = f"{DATA}/.job-staging.json"
LEDGER = f"{DATA}/.job-seen.json"
REJECTED = f"{DATA}/.job-rejected.json"
KST = timezone(timedelta(hours=9))


def load(path, default):
    """없으면 기본값(첫 실행), **깨졌으면 중단**(빈 값으로 덮어 장부를 날리는 것 방지)."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        if DRY_RUN:   # --list 같은 조회도 이 경로를 탄다 — 읽기 전용이면 원본을 건드리지 않는다
            raise SystemExit(f"❌ 상태 파일 손상: {path}\n   {e}\n"
                             f"   (읽기 전용이라 파일은 그대로 뒀다)")
        bak = f"{path}.corrupt-{int(time.time())}"
        try:
            os.rename(path, bak)
        except OSError:
            bak = "(보존 실패)"
        raise SystemExit(f"❌ 상태 파일 손상: {path}\n   {e}\n   원본 보존: {bak} → 사람이 확인할 것(중단)")


def esc(v):
    # JSON 문자열 = 유효한 YAML 이중따옴표 스칼라 (따옴표·역슬래시·개행 전부 안전)
    return json.dumps(str(v or "").strip(), ensure_ascii=False)


def slug(company, position):
    s = re.sub(r"[\\/:*?\"<>|#\[\]^]", "", f"{company}-{position}")
    return re.sub(r"\s+", " ", s).strip()[:70]


def add_ledger(url):
    led = set(load(LEDGER, {"urls": []}).get("urls", []))
    led.add(url)
    _wjson(LEDGER, {"urls": sorted(led)}, 0)


def make_note(item, fit, status="open", sebu="발견", memo="", priority=""):
    if DRY_RUN:
        print(f"[DRY-RUN] 노트 생성 생략: {item.get('회사')} · {item.get('포지션', '')[:30]}")
        return "(dry-run)"
    company, position = item.get("회사", ""), item.get("포지션", "")
    base = slug(company, position) or "job"
    name, i = base, 2
    while os.path.exists(f"{POSTINGS}/{name}.md"):
        name = f"{base}-{i}"
        i += 1
    now = datetime.now(KST)
    fm = ["---", f'title: {esc(company + " · " + position)}', f"회사: {esc(company)}", f"포지션: {esc(position)}",
          f"보드: {esc(item.get('보드', ''))}", f"적합도: {esc(fit)}", f"티어: {esc(priority)}",
          f"상태: {esc(status)}", f"세부: {esc(sebu)}",
          f'게시일: {esc(item.get("발견", now.strftime("%Y-%m-%d")))}', f"링크: {esc(item.get('url', ''))}",
          f"키워드: {esc(item.get('키워드', ''))}", '추가링크1: ""', '추가링크2: ""', f"메모: {esc(memo)}",
          "tags: [구직, 공고, task]", "private: true", "---", "", f"# {company} · {position}",
          "", "## JD 요약", "", "## 적합도 근거", f"- {fit}", "", "## 지원 현황", ""]
    fp = f"{POSTINGS}/{name}.md"
    write_atomic(fp, "\n".join(fm))   # 공용 I/O 계층 — 중간에 죽어도 반쪽 노트가 안 남는다
    os.chmod(fp, 0o600)        # private: true는 에이전트용 표식일 뿐 — OS 권한도 맞춰준다
    import subprocess
    subprocess.run(["chown", "10000:10000", fp], stderr=subprocess.DEVNULL, check=False)
    r = subprocess.run([sys.executable, f"{DATA}/bin/priority-recalc.py", "--file", fp],
                       capture_output=True, text=True, check=False)  # 우선도 파생
    if r.returncode != 0:   # 조용히 넘어가면 대시보드 정렬 필드가 통째로 빠진 노트가 생긴다
        print(f"⚠️ 우선도 계산 실패({r.returncode}) — 노트는 만들었지만 우선도 없음: "
              f"{(r.stderr or '').strip()[:120]}", file=sys.stderr)
    return fp


def find_note(company, position):
    """매칭 노트 탐색 — 정확일치 우선, 정규화(크로스플랫폼) 폴백. 멱등·중복 가드."""
    rc = re.compile(r'^회사:\s*"?' + re.escape(company) + r'"?\s*$', re.M)
    rp = re.compile(r'^포지션:\s*"?' + re.escape(position) + r'"?\s*$', re.M)
    nk = None
    try:
        import sys as _s
        _s.path.insert(0, f"{DATA}/skills/job-search")
        import jobfilter as _jf
        nk = _jf.norm_key(company, position)
    except Exception:
        _jf = None
    norm_hit = None
    for fn in sorted(os.listdir(POSTINGS)):
        if not fn.endswith(".md"):
            continue
        t = open(f"{POSTINGS}/{fn}", encoding="utf-8", errors="ignore").read()
        if rc.search(t) and rp.search(t):
            return fn  # 정확일치 최우선
        if nk and norm_hit is None:
            mc = re.search(r'^회사:\s*"?([^"\n]*)', t, re.M)
            mp = re.search(r'^포지션:\s*"?([^"\n]*)', t, re.M)
            if mc and mp and _jf.norm_key(mc.group(1).strip(), mp.group(1).strip()) == nk:
                norm_hit = fn
    return norm_hit  # 정확일치 없으면 정규화 매칭(크로스플랫폼)


def main():
    # 동시 호출(크론·메일스윕·수동) 직렬화 — lost update 방지
    _lk = open(f"{DATA}/.jsc.lock", "a")
    fcntl.flock(_lk, fcntl.LOCK_EX)
    os.environ["HERMES_KIT_LOCK_HELD"] = "1"   # 자식(priority-recalc)이 같은 락을 다시 잡아 교착되지 않게
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--url")
    ap.add_argument("--fit")
    ap.add_argument("--reject")
    ap.add_argument("--stage", action="store_true")
    ap.add_argument("--board")
    ap.add_argument("--applied", action="store_true")
    ap.add_argument("--company")
    ap.add_argument("--position")
    ap.add_argument("--memo")
    ap.add_argument("--priority", choices=list("SABCD"))
    a = ap.parse_args()
    staging = load(STAGING, [])

    if a.list:
        if not staging:
            print("(스테이징 비어있음)")
            return 0
        for x in staging:
            print(f"- {x.get('회사','?')} | {x.get('포지션','?')} | {x.get('보드','?')} | {x.get('url','')}")
        print(f"총 {len(staging)}건 판정 대기")
        return 0

    if a.stage:
        if not (a.company and a.position and a.url):
            print("usage: --stage --company C --position P --url U [--board B]", file=sys.stderr)
            return 2
        led = set(load(LEDGER, {"urls": []}).get("urls", []))
        if a.url in led or any(x.get("url") == a.url for x in staging):
            print(f"이미 처리/대기 중: {a.url}")
            return 0
        if find_note(a.company, a.position):
            add_ledger(a.url)
            print("이미 위키에 있는 공고 — 스킵")
            return 0
        try:  # 제목 게이트 즉석 적용(수집기와 동일 기준)
            import sys as _s
            _s.path.insert(0, f"{DATA}/skills/job-search")
            import jobfilter as _jf
            if _jf.is_senior(a.position) or _jf.is_excluded(a.position) or not _jf.matches_track(a.position) or _jf.exceeds_exp(a.position):
                add_ledger(a.url)
                rej = load(REJECTED, {})
                rej[a.url] = {"reason": "gate-at-stage(알림메일)", "회사": a.company, "포지션": a.position,
                              "when": datetime.now(KST).strftime("%Y-%m-%d")}
                _wjson(REJECTED, rej, 1)
                print(f"게이트 컷: {a.position[:40]}")
                return 0
        except ImportError:
            pass
        staging.append({"회사": a.company, "포지션": a.position, "보드": a.board or "알림메일",
                        "url": a.url, "키워드": "alert-mail",
                        "발견": datetime.now(KST).strftime("%Y-%m-%d")})
        _wjson(STAGING, staging, 1)
        print(f"{'[DRY-RUN] 등록했을 것: ' if DRY_RUN else '스테이징 등록: '}{a.company} · {a.position[:35]}"
              f"{'' if DRY_RUN else f' (대기열 {len(staging)})'}")
        return 0

    if a.applied:
        if not (a.company and a.position):
            print("usage: --applied --company C --position P [--url U] [--memo M]", file=sys.stderr)
            return 2
        hit = find_note(a.company, a.position)
        if hit:
            print(f"이미 존재: {hit} — 상태 갱신은 note-set-field.py로")
            return 0
        item = {"회사": a.company, "포지션": a.position, "보드": "메일확인",
                "url": a.url or "", "키워드": "applied-mail"}
        fp = make_note(item, "", status="waiting", sebu="지원",
                       memo=a.memo or "메일 접수확인으로 자동 기록", priority=a.priority or "C")
        if a.url:
            add_ledger(a.url)
            # 스테이징에 같은 URL이 대기 중이면 제거(이미 지원했으니 판정 불필요)
            staging = [x for x in staging if x.get("url") != a.url]
            _wjson(STAGING, staging, 1)
        print(f"{'[DRY-RUN] 생성했을 것: ' if DRY_RUN else '지원완료 노트 생성: '}{os.path.basename(fp)}")
        return 0

    if not a.url or not (a.fit or a.reject):
        print("usage: --list | --url U (--fit '✅...' | --reject '사유') | --applied --company C --position P", file=sys.stderr)
        return 2
    hit = [x for x in staging if x.get("url") == a.url]
    if not hit:
        print(f"스테이징에 없음: {a.url}", file=sys.stderr)
        return 1
    item = hit[0]
    staging = [x for x in staging if x.get("url") != a.url]

    add_ledger(a.url)

    if a.fit:
        pos = item.get("포지션", "")
        # 커밋 게이트: 스테이징이 낡았어도 위키 진입 직전에 '현재' 규칙으로 재검사(레이스 방어)
        try:
            import sys as _s
            _s.path.insert(0, f"{DATA}/skills/job-search")
            import jobfilter as _jf
            if _jf.is_senior(pos) or _jf.is_excluded(pos) or not _jf.matches_track(pos) or _jf.exceeds_exp(pos):
                rej = load(REJECTED, {})
                rej[a.url] = {"reason": "gate-at-commit(현행 규칙 재검사)", "회사": item.get("회사", ""),
                              "포지션": pos, "when": datetime.now(KST).strftime("%Y-%m-%d")}
                _wjson(REJECTED, rej, 1)
                _wjson(STAGING, staging, 1)
                print(f"{'[DRY-RUN] ' if DRY_RUN else ''}게이트 거부(커밋 시점): {pos[:40]}")
                return 0
        except ImportError:
            pass  # jobfilter 자체가 없는 환경(호스트 조회 등)에서만 게이트 생략
        except Exception as e:
            # 정규식 오류·설정 파손·코드 결함으로 게이트가 못 돌면 '통과'가 아니라 '거부'다.
            # 여긴 현행 규칙으로 재검사하는 최종 방어선이라, 못 돌면 입고를 멈춰야 한다.
            print(f"❌ 커밋 게이트 실행 실패 — 입고 중단(안전): {type(e).__name__}: {e}", file=sys.stderr)
            return 1
        # 중복 게이트: 같은 회사+포지션 노트가 이미 있으면 새 노트 대신 스킵(URL만 장부·스테이징 정리)
        dup = find_note(item.get("회사", ""), pos)
        if dup:
            _wjson(STAGING, staging, 1)
            print(f"중복 스킵(기존: {dup})")
            return 0
        fp = make_note(item, a.fit, priority=a.priority or "C")  # 미지정=기본 C(평범)
        print(f"{'[DRY-RUN] 입고했을 것: ' if DRY_RUN else '입고: '}{os.path.basename(fp)} · 우선도={a.priority or '-'} · {a.fit[:30]}")
    else:
        rej = load(REJECTED, {})
        rej[a.url] = {"reason": f"triage: {a.reject}", "회사": item.get("회사", ""),
                      "포지션": item.get("포지션", ""), "when": datetime.now(KST).strftime("%Y-%m-%d")}
        _wjson(REJECTED, rej, 1)
        print(f"거부: {item.get('회사','?')} · {a.reject[:40]}")
    _wjson(STAGING, staging, 1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
