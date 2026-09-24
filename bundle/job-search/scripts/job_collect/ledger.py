#!/usr/bin/env python3
"""job_collect.ledger — URL 장부·거부·스테이징·로테이션 및 공고 노트 I/O(job-collect.py 원본에서 이식, 동작 동일)."""
import json
import os
import re
import sys
import time

from . import config
from .identity import esc, key


def _wjson(path, obj, indent=1):
    """원자적 JSON 쓰기 — 임시파일 후 os.replace (도중 사망 시 원본 보존)."""
    if config.DRY_RUN:   # 노트뿐 아니라 '장부'도 안 건드려야 한다 — 처리 기록이 남으면 그 공고는 영영 안 들어온다
        print(f"[DRY-RUN] {os.path.basename(path)} 갱신 생략 ({len(obj)}건) — {config.why_dry()}")
        return
    tmp = f"{path}.{os.getpid()}.tmp"  # 동시 실행 시 tmp 충돌 방지
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
    os.chmod(tmp, os.stat(path).st_mode & 0o7777 if os.path.exists(path) else 0o600)  # 지원이력 → 비공개
    os.replace(tmp, path)


def _load_json_or_die(path, default):
    """상태 파일 로드. 없으면 기본값(첫 실행), **깨졌으면 중단**한다.

    빈 값으로 계속 돌면 그 위에 새 파일을 써서 손상 원본을 덮는다 —
    특히 .job-seen.json(처리 완료 URL 장부)이 비면 과거 공고 수천 건이 전부 재수집된다.
    그래서 손상본은 .corrupt-<timestamp>로 보존하고 멈춘다(사람이 보고 판단할 문제)."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        if config.DRY_RUN:   # 읽기 전용인데 원본 이름을 바꾸면 안 된다 — 알리기만 하고 멈춘다
            raise SystemExit(f"❌ 상태 파일 손상: {path}\n   {e}\n"
                             f"   (읽기 전용이라 파일은 그대로 뒀다. 쓰기 스위치를 켜고 다시 실행하면 "
                             f".corrupt-* 로 보존한다)")
        bak = f"{path}.corrupt-{int(time.time())}"
        try:
            os.rename(path, bak)
        except OSError:
            bak = "(보존 실패)"
        raise SystemExit(f"❌ 상태 파일 손상: {path}\n   {e}\n   원본 보존: {bak}\n"
                         f"   빈 값으로 계속하면 장부가 날아가 과거 공고가 전부 재수집된다 → 중단한다.")


def load_ledger():
    return set(_load_json_or_die(config.LEDGER, {"urls": []}).get("urls", []))


def load_rejected():
    return _load_json_or_die(config.REJECTED, {})


def load_staging():
    return _load_json_or_die(config.STAGING, [])


def next_block_idx(num_blocks):
    """실행마다 결정론적으로 블록 순환(지속 카운터). 과거 hour//6 방식은
    블록 1·3만 쓰고 0·2는 사문이 됐음 — 지속 카운터로 전 블록 커버."""
    try:
        c = json.load(open(config.ROTATION)).get("i", -1)
    except FileNotFoundError:
        c = -1                       # 첫 실행 — 정상
    except (OSError, ValueError) as e:
        # 손상됐는데 조용히 0부터 다시 돌면 특정 블록만 반복 검색된다 → 알리고 이어간다
        print(f"⚠️ {config.ROTATION} 손상({e}) — 로테이션을 처음부터 재시작한다(검색 블록이 한 바퀴 밀림)", file=sys.stderr)
        c = -1
    c = (c + 1) % num_blocks
    if not config.DRY_RUN:          # 읽기 전용인데 카운터가 돌면 다음 실 수집이 블록을 건너뛴다
        try:
            _wjson(config.ROTATION, {"i": c}, 0)   # 다른 상태 파일과 같은 원자적 저장(부분 JSON 방지)
        except OSError:
            pass
    return c


def field(t, name):
    m = re.search(r'^' + name + r':\s*"?([^"\n]*)', t, re.M)
    return (m.group(1).strip() if m else "")


def reap_rejected(ledger, rejected, today):
    """트리아지가 ❌(급부적합)로 판정한 노트를 백업으로 치우고 URL을 장부/거부에 등록.
    → 위키엔 알짜배기(✅/🔧/미판정)만 남고, ❌는 재스크랩돼도 재수집 안 됨. 되돌리기: .backups."""
    if not os.path.isdir(config.POSTINGS):
        return 0
    bk = f"{config.DATA}/.backups/{today.replace('-', '')}/reaped-postings"
    moved = 0
    for fn in sorted(os.listdir(config.POSTINGS)):
        if not fn.endswith(".md"):
            continue
        fp = f"{config.POSTINGS}/{fn}"
        t = open(fp, encoding="utf-8").read()
        if not field(t, "적합도").startswith("❌"):
            continue
        # 사용자가 손댄 노트(지원/면접/오퍼/완료 등)는 보존
        if field(t, "상태") not in ("open", "예정", "") or field(t, "세부") not in ("발견", ""):
            continue
        url = field(t, "링크")
        config.move(fp, f"{bk}/{fn}")
        if url:
            ledger.add(url)
            rejected[url] = {"reason": "triage-❌", "회사": field(t, "회사"),
                             "포지션": field(t, "포지션"), "when": today}
        moved += 1
    return moved


def desuffix_orphans():
    """base 없는 고아 `X-N.md`/`X N.md`를 클린 이름으로 되돌린다(흉터 자기치유)."""
    if not os.path.isdir(config.POSTINGS):
        return 0
    n = 0
    for fn in sorted(os.listdir(config.POSTINGS)):
        if not fn.endswith(".md"):
            continue
        m = re.match(r"^(.*?)[ -]\d+\.md$", fn)
        if not m:
            continue
        clean = m.group(1) + ".md"
        if not os.path.exists(f"{config.POSTINGS}/{clean}"):
            if config.DRY_RUN:          # 이름 변경도 '쓰기'다 — 스위치 꺼져 있으면 손대지 않는다
                print(f"[DRY-RUN] 이름 정리 생략: {fn} → {clean}")
                continue
            os.rename(f"{config.POSTINGS}/{fn}", f"{config.POSTINGS}/{clean}")
            n += 1
    return n


def scan_existing():
    """{key: filepath} 로 기존 공고 노트 인덱스."""
    idx = {}
    if not os.path.isdir(config.POSTINGS):
        return idx
    for fn in os.listdir(config.POSTINGS):
        if not fn.endswith(".md"):
            continue
        t = open(f"{config.POSTINGS}/{fn}", encoding="utf-8").read()
        c = re.search(r'^회사:\s*"?([^"\n]*)', t, re.M)
        p = re.search(r'^포지션:\s*"?([^"\n]*)', t, re.M)
        if c and p:
            idx.setdefault(key(c.group(1), p.group(1)), f"{config.POSTINGS}/{fn}")
    return idx


def add_extra_link(fp, url):
    """기존 노트의 빈 추가링크 슬롯에 url 추가. 자리 없으면 False."""
    t = open(fp, encoding="utf-8").read()
    if url in set(re.findall(r"https?://[^\"\s\n]+", t)):
        return True
    for f in ("추가링크1", "추가링크2"):
        m = re.search(r'^' + f + r':\s*"?([^"\n]*)"?\s*$', t, re.M)
        if m and not m.group(1).strip():
            t = re.sub(r'^' + f + r':\s*"?[^"\n]*"?\s*$', f + ': ' + esc(url), t, count=1, flags=re.M)
            config.write_atomic(fp, t)
            return True
        if not m:  # 필드 없으면 링크 뒤에 삽입
            t = re.sub(r'^(링크: [^\n]*$)', r'\1\n' + f + ': ' + esc(url), t, count=1, flags=re.M)
            config.write_atomic(fp, t)
            return True
    # 슬롯(추가링크1·2)이 꽉 참 → 본문에 적재. 여기서 False를 주면 호출부가 URL을 장부에만 넣고
    # 링크는 어디에도 안 남아 영구 유실된다(같은 공고의 3번째 URL).
    if "## 추가 링크" in t:
        t = t.replace("## 추가 링크\n", f"## 추가 링크\n- {url}\n", 1)
    else:
        t = t.rstrip("\n") + f"\n\n## 추가 링크\n- {url}\n"
    config.write_atomic(fp, t)
    return True
