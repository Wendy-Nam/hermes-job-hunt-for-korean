#!/usr/bin/env python3
"""job-collect.py — 구직 발견 수집 (LLM 없음, 크론 no_agent).

기존 스크래퍼(wanted/kr/linkedin/ats)를 subprocess로 재사용해, 신규만 02_work/job-hunting/postings/에
공고 노트로 생성한다. 중복/부적격 방지:
  1) URL 장부(.job-seen.json): 한 번 처리(노트/추가링크/거부)한 URL은 다시 안 본다.
     → 거부된 것도 장부에 들어가므로 재스크랩돼도 fetch 없이 즉시 스킵(재판단 0).
  2) 회사+포지션: 같은 공고가 다른 URL로 또 나오면 새 노트 대신 기존 노트의 추가링크에.
  3) 제목 게이트: 시니어/연차(제목)/무관직 → 거부(값쌈).
  4) 본문 연차 게이트: 제목 통과 신규만, JD 본문에 5년+ 하드바 있으면 거부(fetch, 상한/폴트오픈).
거부 사유는 .job-rejected.json 에 남긴다(감사·되돌리기용).
판정(적합도)은 트리아지 크론이 한다. 키워드는 런마다 블록 순환(지속 카운터, 전 블록 커버).

모듈 구성(단일책임 분리, 동작 변경 없음) — job_collect/:
  config.py        경로·상수·noteio 쓰기 스위치
  identity.py       회사·포지션 dedup/정규화 키
  profile.py        search-profile.yaml 로딩·연차 게이트 정규식
  ledger.py         URL 장부·거부·스테이징·로테이션·공고 노트 I/O
  public_export.py  공개 계약 JSONL 산출물
이 파일은 스크래퍼 호출 배선(프록시·JSON 계약)과 main() CLI 오케스트레이션만 담당한다.
"""
import fcntl
import argparse
import json
import os
import re
import subprocess
import time
import sys
from datetime import datetime

from pathlib import Path
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from job_collect.config import DATA, POSTINGS, LEDGER, REJECTED, STAGING, S, FJ, KST, FETCH_TIMEOUT, note_lock, PUBLIC_KEYS
from job_collect.identity import key
from job_collect.profile import load_profile, _exp_title, _exp_body
from job_collect.ledger import (
    _wjson, load_ledger, load_rejected, load_staging, next_block_idx,
    reap_rejected, desuffix_orphans, scan_existing, add_extra_link,
)
from job_collect.public_export import export_public_jsonl, _fixture_rows, _public_record
from noteio import write_atomic

# 스크래퍼 전용 프록시 — 원티드/유튜브 등이 VPS(클라우드 IP)를 차단하는 걸 우회한다.
# 전역 HTTPS_PROXY를 쓰면 LLM API 호출까지 느린 프록시로 가버리므로, 여기서 '자식 subprocess에만'
# 주입한다. 유저는 .env에 HERMES_SCRAPER_PROXY=http://user:pass@host:port 한 줄만 넣으면 된다.
# 프록시는 클라우드 IP를 막는 타깃(원티드)에만 주입 — 나머지 보드·ATS·JD fetch는 직접 연결(대역폭·속도).
# 런 시작에 프록시 생존검사·자동교체(bin/proxy-doctor.py)를 거친다 — 죽은 프록시가
# 전 보드를 조용히 막는 사고 방지(2026-07 실증). 닥터 부재·실패 시 종전 동작(폴트오픈).
def _resolve_proxy():
    env = os.environ.get("HERMES_SCRAPER_PROXY", "").strip()
    doctor = os.path.join(DATA, "bin", "proxy-doctor.py")
    if not os.path.exists(doctor):
        return env
    try:
        r = subprocess.run([sys.executable, doctor], capture_output=True, text=True, timeout=90)
        if r.returncode == 0:
            resolved = (r.stdout or "").strip()
            if resolved != env:
                print(f"프록시 닥터: {'교체/복구' if resolved else '전멸 → 직접 연결 폴백'}", file=sys.stderr)
            return resolved
    except Exception:
        pass
    return env

_PROXY = _resolve_proxy()
# ponytail: 막히는 보드가 늘면 여기 추가. 무료 프록시 1GB/월이 전 보드 경유로 소진됐던 사고(2026-09-12) 후 타깃 한정.
PROXY_BOARDS = ("wanted",)
PROXY_HOSTS = ("wanted.co.kr",)

def _subenv(target):
    """target=보드명 또는 URL. 프록시 대상이면 자식 env에 HTTPS_PROXY 주입, 아니면 None(부모 env 상속=직접 연결)."""
    if _PROXY and (target in PROXY_BOARDS or any(h in target for h in PROXY_HOSTS)):
        return {**os.environ, "HTTPS_PROXY": _PROXY, "HTTP_PROXY": _PROXY,
                "https_proxy": _PROXY, "http_proxy": _PROXY}
    return None


_P = load_profile()
LIMIT = _P["limit"]
BODY_CAP = _P["body_cap"]          # 실행당 본문 fetch 상한
LATTICE = _P["blocks"]             # 런마다 한 블록(next_block_idx 지속 카운터로 전 블록 순환)
_SENIOR = re.compile("(?i)" + _P["senior"])
_EXP = _exp_title(_P["title_yr"])                    # 제목 N년+ 요구 → 제외
_EXP_BODY = _exp_body(_P["body_yr"])                 # 본문 M년+ 하드바 → 제외(보수적)
_AX = re.compile("(?i)(" + _P["ax_title"] + ")") if _P["ax_title"] else None  # AX/AI자동화 → 연차 완화
_EXP_AX = _exp_title(_P["ax_yr"])
_EXP_BODY_AX = _exp_body(_P["ax_yr"])
_EXC_NOAX = re.compile("(?i)(" + _P["exc_noax"] + ")") if _P["exc_noax"] else None  # AX 아니면 제외(전략기획)
_ADMIN_B = re.compile("(?i)(" + _P["admin_b"] + ")") if _P["admin_b"] else None   # 본문 어드민 신호
_CORE_B = re.compile("(?i)(" + _P["core_b"] + ")") if _P["core_b"] else None      # 본문 코어(옵스/세일즈) 신호
_EXCLUDE = re.compile("(?i)" + _P["exclude"])        # 무관직(순수 개발 등) → 제외
_TRACK = re.compile("(?i)(" + _P["track"] + ")") if _P["track"] else None  # 트랙게이트: 제목이 직군 무관이면 컷

for i, a in enumerate(sys.argv):
    if a == "--limit" and i + 1 < len(sys.argv):
        LIMIT = int(sys.argv[i + 1])
    if a == "--body-cap" and i + 1 < len(sys.argv):
        BODY_CAP = int(sys.argv[i + 1])

_ALL_SCRAPERS = {
    "wanted":   ("wanted",   [f"{S}/wanted-search/scripts/wanted_search.py", "{kw}", "--limit", "{lim}", "--json"]),
    "saramin":  ("saramin",  [f"{S}/kr-search/scripts/kr_job_search.py", "{kw}", "--board", "saramin", "--limit", "{lim}", "--json"]),
    "jobkorea": ("jobkorea", [f"{S}/kr-search/scripts/kr_job_search.py", "{kw}", "--board", "jobkorea", "--limit", "{lim}", "--json"]),
    "web":      ("web",      [f"{S}/web-search/scripts/web_job_search.py", "{kw}", "--limit", "{lim}", "--json"]),
    "linkedin": ("linkedin", [f"{S}/linkedin-search/scripts/linkedin_jobs.py", "{kw}", "--level", "신입,인턴,주니어", "--limit", "{lim}", "--json"]),
}
SCRAPERS = [_ALL_SCRAPERS[b] for b in _P["boards"] if b in _ALL_SCRAPERS]
ATS = [f"{S}/ats-search/scripts/ats_search.py", "--keyword", "{kw}", "--limit", "{lim}", "--json"] if "ats" in _P["boards"] else None


def fetch_body(url):
    """JD 본문 텍스트. 실패/타임아웃이면 ''(폴트오픈: 못 읽으면 살려서 노트→트리아지)."""
    try:
        r = subprocess.run([sys.executable, FJ, url], capture_output=True, text=True, timeout=FETCH_TIMEOUT, env=_subenv(url))
        return r.stdout or ""
    except Exception:
        return ""


def parse_scraper_json(out, board, kw, fails):
    """스크래퍼 --json 출력 파싱 — 계약: [{"title","company","url",...}].
    예전엔 사람용 마크다운(- **{직무}** · {회사})을 정규식으로 재파싱했는데, 회사명에 '·'가
    들어가면 절단되고 출력 문구가 바뀌면 수집이 조용히 죽었다. JSON이 유일한 기계 계약이다."""
    if not out.strip():
        return []
    try:
        rows = json.loads(out)
    except ValueError:
        fails.append(f"{board}/{kw}: JSON 파싱 실패(스크래퍼 출력 계약 위반)")
        return []
    got = []
    for r in rows if isinstance(rows, list) else []:
        title = (r.get("title") or "").strip()
        comp = (r.get("company") or "").strip()
        url = (r.get("url") or "").strip()
        loc = (r.get("location") or "").strip()   # 4/6 보드가 제공. 없으면 빈 문자열.
        if title and url:
            got.append((comp, title, url, board, kw, loc))
    return got


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--public-jsonl-out")
    ap.add_argument("--quiet-machine", action="store_true")
    ap.add_argument("--log-out")
    ap.add_argument("--fixture-input")
    ap.add_argument("--dry-run", action="store_true")
    opts, _ = ap.parse_known_args()
    if opts.quiet_machine:
        if opts.log_out:
            sys.stderr = open(opts.log_out, "a", encoding="utf-8")
        else:
            sys.stderr = open(os.devnull, "w")
    if opts.fixture_input or (opts.dry_run and opts.public_jsonl_out):
        if not opts.fixture_input:
            print(json.dumps({"status": "BLOCK", "reason": "fixture_input_required"}, separators=(",", ":")))
            return 2
        count = export_public_jsonl(_fixture_rows(opts.fixture_input), opts.public_jsonl_out or "public-candidates.jsonl")
        print(json.dumps({"status": "OK", "count": count, "artifact": os.path.abspath(opts.public_jsonl_out or "public-candidates.jsonl")}, separators=(",", ":")))
        return 0
    # 단일 실행 락 — 수동 실행과 크론이 겹치면 네트워크 호출이 통째로 중복된다(저장은 note_lock이
    # 지키지만 수집 자체가 이중으로 돎). 이미 도는 중이면 조용히 빠진다.
    runlock = open(f"{DATA}/.job-collect.lock", "a")
    try:
        fcntl.flock(runlock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("job-collect: 이미 실행 중 — 이번 호출은 스킵(중복 수집 방지)")
        return 0

    # 전역 데드라인 — 보드 8키워드×6보드×본문 fetch가 네트워크 장애를 만나면 이론상 수십 분.
    # 넘으면 루프를 끊고 지금까지 모은 것을 정상 저장한다.
    deadline = time.monotonic() + int(os.environ.get("HERMES_COLLECT_DEADLINE_MIN", "25")) * 60

    now = datetime.now(KST)
    today = now.strftime("%Y-%m-%d")
    bidx = next_block_idx(len(LATTICE))
    block = LATTICE[bidx]
    ledger = load_ledger()
    rejected = load_rejected()
    os.makedirs(POSTINGS, exist_ok=True)
    with note_lock():        # 노트 이동·이름수정은 상태갱신·판정커밋과 겹치면 안 된다
        reaped = reap_rejected(ledger, rejected, today)   # 트리아지 ❌ 노트 정리(알짜배기만 유지)
        healed = desuffix_orphans()
    existing = scan_existing()
    staging = load_staging()
    staged_keys = {key(x.get("회사",""), x.get("포지션","")) for x in staging}
    stg0_n, rej0 = len(staging), set(rejected)   # 델타 기준선(수집은 수 분 — 그 사이 jsc가 바꾼 걸 덮지 않으려고)

    def reject(url, comp, pos, reason):
        ledger.add(url)
        rejected[url] = {"reason": reason, "회사": comp, "포지션": pos, "when": today}

    new, extra, rej, capped, fetches = 0, 0, 0, 0, 0
    public_candidates = []
    fails = []                                  # 스크래퍼 실패 — 빈 결과와 구분해서 보고(차단·파서변경 감지)
    hit_deadline = False
    for kw in block:
        if time.monotonic() > deadline:
            print("⚠️ 수집 데드라인 도달 — 남은 키워드 생략, 지금까지 모은 것 저장", file=sys.stderr)
            hit_deadline = True
            break
        jobs = []
        for board, argv in SCRAPERS:
            cmd = [sys.executable] + [a.replace("{kw}", kw).replace("{lim}", str(LIMIT)) for a in argv]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=40, env=_subenv(board))
                out = r.stdout
                if r.returncode != 0:
                    fails.append(f"{board}/{kw}: rc={r.returncode} {(r.stderr or '').strip()[:80]}")
            except Exception as e:
                out = ""
                fails.append(f"{board}/{kw}: {type(e).__name__}")
            jobs.extend(parse_scraper_json(out, board, kw, fails))
        out = ""
        if ATS:
            cmd = [sys.executable] + [a.replace("{kw}", kw).replace("{lim}", str(LIMIT)) for a in ATS]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=40, env=_subenv('ats'))
                out = r.stdout
                if r.returncode != 0:
                    fails.append(f"ats/{kw}: rc={r.returncode} {(r.stderr or '').strip()[:80]}")
            except Exception as e:
                out = ""
                fails.append(f"ats/{kw}: {type(e).__name__}")
        jobs.extend(parse_scraper_json(out, "ats", kw, fails))

        for comp, pos, url, board, jkw, loc in jobs:
            public_candidates.append({"url": url, "title": pos, "company": comp,
                                      "provenance": {"board": board, "keyword": jkw}})
            if url in ledger:                       # 이미 처리 → 재수집·재판단 안 함
                continue
            ax = bool(_AX and _AX.search(pos))  # AX/AI자동화면 연차 컷 완화(ax_yr년+만)
            if _SENIOR.search(pos) or (_EXP_AX if ax else _EXP).search(pos) or _EXCLUDE.search(pos) \
                    or (not ax and _EXC_NOAX and _EXC_NOAX.search(pos)):
                reject(url, comp, pos, "title")     # 제목 게이트(값쌈)
                continue
            if _TRACK and not _TRACK.search(pos):   # 트랙게이트: 직군 무관 제목은 애초에 컷
                reject(url, comp, pos, "off-track")
                continue
            k = key(comp, pos)
            if k in existing:                       # 이미 위키에 있는 공고 → 추가링크
                ledger.add(url)
                with note_lock():
                    added = add_extra_link(existing[k], url)
                if added:
                    extra += 1
                continue
            if k in staged_keys:                    # 이미 스테이징에 대기 중
                ledger.add(url)
                continue
            if fetches < BODY_CAP:                  # 본문 연차 게이트
                body = fetch_body(url)
                fetches += 1
                if body and (_EXP_BODY_AX if ax else _EXP_BODY).search(body):
                    reject(url, comp, pos, "body-exp")
                    rej += 1
                    continue
                # 완전 어드민성 게이트(보수적): 어드민 신호 2종+ & 코어 신호 0 → 컷.
                # 사무가 일부 섞인 옵스는 코어 신호가 있어 통과(최종 판단=트리아지).
                if body and _ADMIN_B and _CORE_B and \
                        len(set(m.lower() for m in _ADMIN_B.findall(body))) >= 2 and not _CORE_B.search(body):
                    reject(url, comp, pos, "body-admin")
                    rej += 1
                    continue
            else:
                capped += 1
            ledger.add(url)
            staged_keys.add(k)                      # 위키 직행 금지 — 판정 대기열(스테이징)로
            staging.append({"회사": comp, "포지션": pos, "보드": board, "url": url, "키워드": jkw, "발견": today, "근무지": loc})
            new += 1

    # 저장 — jsc와 같은 락으로 직렬화하고, 디스크를 다시 읽어 우리 델타만 병합한다.
    # 통째쓰기였을 때: 수집 8분 사이 jsc가 커밋해 뺀 공고를 8분 전 스냅샷이 되살렸음(lost update).
    with note_lock():
        ledger = load_ledger() | ledger                                  # 장부는 추가 전용 → 합집합
        merged = load_rejected()
        merged.update({u: r for u, r in rejected.items() if u not in rej0})   # 우리가 새로 거부한 것만
        rejected = merged
        cur = load_staging()
        have = {x.get("url") for x in cur}
        cur += [s for s in staging[stg0_n:] if s.get("url") not in have]      # 우리가 새로 담은 것만
        staging = cur
        _wjson(LEDGER, {"urls": sorted(ledger)}, 0)
        _wjson(REJECTED, rejected, 1)
        _wjson(STAGING, staging, 1)
    subprocess.run([sys.executable, f"{DATA}/bin/dedup-postings.py"], capture_output=True, timeout=60)   # jsc 밖 경로가 만든 변형중복 수거(norm_key)
    _nz = subprocess.run([sys.executable, f"{DATA}/bin/normalize-postings.py"], capture_output=True, text=True,
                         env={**os.environ, "NORM_APPLY": "1"}, timeout=60)                              # 변칙 frontmatter 정규화(백스톱)
    _m = re.search(r"변칙 변경 (\d+)", _nz.stdout or "")
    if _m and int(_m.group(1)) > 0:   # 0이 아니면 jsc 밖 경로가 노트를 만들고 있다는 소리 — 예방규율 점검 신호
        print(f"⚠️ frontmatter 변칙 {_m.group(1)}건 자동정규화 — jsc/note-set-field 밖에서 노트가 생성/편집되고 있다(규율 점검 필요)", file=sys.stderr)
    subprocess.run([sys.executable, f"{DATA}/bin/priority-recalc.py"], capture_output=True, timeout=60)  # 우선도 파생 자가치유
    if opts.public_jsonl_out:
        export_public_jsonl(public_candidates, opts.public_jsonl_out)
    dl_note = ", ⏰데드라인" if hit_deadline else ""
    summary = f"job-collect: 스테이징 {new}건(판정대기 총 {len(staging)}), 추가링크 {extra}, 본문거부 {rej}, 상한통과 {capped}, ❌수거 {reaped}, 고아 {healed} (블록{bidx}, fetch {fetches}{dl_note}) · 장부 {len(ledger)} · 거부 {len(rejected)}"
    if opts.quiet_machine:
        print(json.dumps({"status": "OK", "new": new, "public_count": len(public_candidates)}, separators=(",", ":")))
    else:
        print(summary)
    if fails:  # 수집 0건이 '오늘 공고 없음'인지 '보드가 죽었음'인지 구분되게
        print(f"⚠️  스크래퍼 실패 {len(fails)}건: " + " | ".join(fails[:6]), file=sys.stderr)
        # 전 보드가 죽었는데 exit 0을 주면 크론·모니터에 '정상 실행, 신규 0건'으로 기록된다.
        attempted = len(block) * (len(SCRAPERS) + (1 if ATS else 0))
        if attempted and len(fails) >= attempted:
            print("❌ 모든 보드 수집 실패 — 비정상 종료(사이트 차단·파서 파손·네트워크 확인)", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
