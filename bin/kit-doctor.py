#!/usr/bin/env python3
"""kit-doctor.py — 설치본 점검. 병합하다 흔히 나는 실수를 잡아준다.

config.yaml과 36KB짜리 jobs.json을 손으로 병합하는 구조라, 오타 하나가 조용한 오작동이 된다.
이 스크립트는 '설치 후'와 '스위치 켜기 전'에 돌려서 눈으로 확인할 것들을 대신 본다.

사용: python3 bin/kit-doctor.py [DATA_DIR]     (기본 /opt/data)
종료코드: 0=문제 없음 · 1=문제 있음(고칠 것)
"""
import json
import re
import os
import sys

OK, WARN, FAIL = [], [], []


def chk(cond, ok_msg, fail_msg, hard=True):
    (OK if cond else (FAIL if hard else WARN)).append(ok_msg if cond else fail_msg)


def main():
    data = sys.argv[1] if len(sys.argv) > 1 else "/opt/data"
    # 구경로 잔존(2026-07-28): 한때 템플릿이 wiki/job-hunting로 깔리던 창 — 코드는 automation을 쓴다
    if os.path.isdir(os.path.join(data, "wiki", "job-hunting")):
        FAIL.append("wiki/job-hunting(구경로) 잔존 — 코드는 wiki/automation/job-hunting을 쓴다. ./install.sh 재실행하면 자동 병합된다")
    # conditional-rules 플러그인 정합: 켜져 있는데 규칙 파일이 없으면 조용히 무효(주입 0)가 된다
    cfgp = os.path.join(data, "config.yaml")
    if os.path.exists(cfgp) and "conditional-rules" in open(cfgp, encoding="utf-8", errors="ignore").read():
        for rf in ("delegation.md", "secondbrain.md"):
            if not os.path.exists(os.path.join(data, "wiki", "hermes", "rules", rf)):
                FAIL.append(f"conditional-rules 활성인데 wiki/hermes/rules/{rf} 없음 — 주입이 조용히 실패한다")
    if not os.path.isdir(data):
        print(f"❌ {data} 없음 — 설치 경로를 인자로 주라"); return 1

    # 1) 쓰기 스위치 — 지금 쓰는 상태인지 사람이 알고 있어야 한다
    live = os.path.exists(f"{data}/.kit-live")
    (OK if not live else WARN).append(
        "쓰기 스위치 OFF — 로컬 파일(노트·상태)만 읽기 전용(코드가 강제). "
        "⚠️ Gmail 라벨·이동·삭제 등 외부 서비스(Composio) 쓰기는 이 스위치로 못 막는다 — 프롬프트 DRY-RUN 문단으로만 통제. "
        "켜기: touch %s/.kit-live" % data if not live
        else "쓰기 스위치 ON — 로컬 노트·상태가 실제로 바뀐다(.kit-live 존재). 관찰 중이면 지울 것. "
             "(Gmail 등 외부 쓰기는 이 스위치와 무관 — 프롬프트 DRY-RUN 문단이 통제)")

    # 1b) Hermes(uid 10000) 실제 쓰기 가능 검사 — install.sh는 보는데 doctor는 안 봤다.
    #     파일이 다 있어도 uid 10000이 못 쓰면 파이프라인이 죽는다(설치기 FAIL과 같은 판정).
    import stat as _stat
    for d in (data, f"{data}/wiki/automation/job-hunting/postings", f"{data}/tmp"):
        if not os.path.isdir(d):
            continue
        st = os.stat(d); m = st.st_mode
        ok = ((st.st_uid == 10000 and m & _stat.S_IWUSR) or (st.st_gid == 10000 and m & _stat.S_IWGRP)
              or (m & _stat.S_IWOTH))
        chk(ok, f"쓰기가능(uid 10000) {d}", f"uid 10000이 못 씀: {d} — chown 10000:10000 필요")

    # 2) 필수 파일
    for f in ("bin/noteio.py", "bin/job-stage-commit.py", "bin/note-set-field.py", "bin/priority-recalc.py",
              "scripts/job-collect.py", "skills/job-search/jobfilter.py", "search-profile.yaml"):
        chk(os.path.exists(f"{data}/{f}"), f"있음 {f}", f"누락 {f} — install.sh 다시 실행")

    # 3) search-profile.yaml — 깨져 있으면 수집이 통째로 멈춘다(fail-closed)
    try:
        import yaml
        prof = yaml.safe_load(open(f"{data}/search-profile.yaml", encoding="utf-8")) or {}
        chk(bool(prof.get("keyword_blocks")), "search-profile: 키워드 블록 있음",
            "search-profile: keyword_blocks 비어 있음 — 아무것도 안 걸린다")
        chk(bool((prof.get("filters") or {}).get("title_must_match")), "search-profile: 트랙 게이트 있음",
            "search-profile: filters.title_must_match 없음 — 직군 무관 공고가 다 들어온다", hard=False)
        # 정규식 노브 컴파일 검사 — 깨진 패턴은 실행 시 fail-closed로 죽는다. 여기서 미리 짚어준다.
        import re as _re
        flt = prof.get("filters") or {}
        def _flat(v):
            if isinstance(v, dict):
                return "|".join(x for vals in v.values() for x in (vals if isinstance(vals, list) else [vals]))
            return "|".join(v) if isinstance(v, list) else (v or "")
        bad_rx = 0
        for knob in ("senior_signals", "exclude_roles", "exclude_unless_ax", "ax_title",
                     "title_must_match", "body_admin_signals", "body_core_signals"):
            pat = _flat(flt.get(knob))
            if not pat:
                continue
            try:
                _re.compile("(?i)(" + pat + ")")
            except _re.error as e:
                bad_rx += 1
                FAIL.append(f"search-profile filters.{knob}: 정규식 오류 — 수집·판정이 중단된다: {e}")
        for row in (prof.get("track_bonus") or []):
            try:
                _re.compile("(?i)(" + row[0] + ")"); float(row[1])
            except Exception as e:
                bad_rx += 1
                FAIL.append(f"search-profile track_bonus 행 {row!r}: 잘못됨 — 우선도 계산이 중단된다: {e}")
        if not bad_rx:
            OK.append("search-profile: 정규식 노브 전부 컴파일 OK")
    except FileNotFoundError:
        FAIL.append("search-profile.yaml 없음 — 수집기가 제작자 기본값으로 돈다")
    except Exception as e:
        FAIL.append(f"search-profile.yaml 파싱 실패 — 수집이 중단된다: {e}")

    # 4) 상태 파일 — 손상되면 장부가 날아갈 수 있어서 미리 본다
    for f in (".job-seen.json", ".job-staging.json", ".job-rejected.json"):
        p = f"{data}/{f}"
        if not os.path.exists(p):
            OK.append(f"{f}: 아직 없음(첫 실행 전 — 정상)"); continue
        try:
            json.load(open(p, encoding="utf-8")); OK.append(f"{f}: JSON 정상")
        except ValueError as e:
            FAIL.append(f"{f}: 손상 — 실행 시 중단된다. 백업 후 고칠 것 ({e})")

    # 5) 크론 — 병합 실수 단골
    p = f"{data}/cron/jobs.json"
    if os.path.exists(p):
        try:
            d = json.load(open(p, encoding="utf-8"))
            js = d if isinstance(d, list) else (d.get("jobs") or [])
            js = js if isinstance(js, list) else list(js.values())
            on = [j.get("name") for j in js if j.get("enabled")]
            OK.append(f"cron/jobs.json: {len(js)}잡 · 활성 {len(on)}개 {on if on else ''}")
            for j in js:
                o = j.get("origin") or {}
                # no_agent = 스크립트만 도는 잡이라 배달 대상이 없다(chat_id 불필요)
                if j.get("enabled") and not j.get("no_agent") and not o.get("chat_id"):
                    FAIL.append(f"크론 '{j.get('name')}': origin.chat_id 없음 — 배달이 증발한다")
                if o.get("thread_id"):
                    WARN.append(f"크론 '{j.get('name')}': thread_id 설정됨 — 스레드로 배달된다(의도한 것 맞나?)")
                sc = j.get("schedule") or {}
                # display에 '(12h)' 같은 사람용 주석이 붙는 건 정상 — expr을 담고 있으면 통과
                if sc.get("expr") and sc.get("display") and sc["expr"] not in sc["display"]:
                    WARN.append(f"크론 '{j.get('name')}': 표시({sc['display']})와 실제({sc['expr']})가 다르다")
                # expr 형식 검증 — 필드 수 틀리거나 이상문자면 안 뜨거나 엉뚱한 시각에 뜬다(무음 실패)
                expr = sc.get("expr")
                if expr is not None:
                    parts = str(expr).split()
                    ok = len(parts) == 5 and all(re.fullmatch(r"[0-9*/,\-A-Za-z]+", p) for p in parts)
                    if not ok:
                        FAIL.append(f"크론 '{j.get('name')}': schedule.expr {expr!r} 형식 이상(5필드 아님) — 안 뜨거나 엉뚱한 시각에 뜬다")
            ph = json.dumps(d, ensure_ascii=False)
            chk("<YOUR_DISCORD_CHANNEL_ID>" not in ph, "cron: placeholder 남지 않음",
                "cron: <YOUR_DISCORD_CHANNEL_ID> 그대로 — 본인 채널 ID로 바꿀 것")
        except ValueError as e:
            FAIL.append(f"cron/jobs.json 파싱 실패 — 크론이 전부 죽는다: {e}")
    else:
        WARN.append("cron/jobs.json 없음 — 자동화를 안 쓰면 정상")

    # 6) 의존성
    try:
        import yaml  # noqa: F401
        OK.append("PyYAML 있음")
    except ImportError:
        FAIL.append("PyYAML 없음 — pip install -r requirements.txt")
    # jieba는 Hermes 플러그인 벤더 경로에 있을 수 있다(turn-router가 sys.path에 넣어 쓴다).
    # 맨 import만 보고 '없음'이라고 하면 거짓 경고가 된다.
    vendor = os.environ.get("HERMES_PLUGIN_VENDOR", f"{data}/python-site")
    if os.path.isdir(vendor) and vendor not in sys.path:
        sys.path.insert(0, vendor)
    try:
        import jieba  # noqa: F401
        OK.append(f"jieba 있음(turn-router 검색 레이어) — {os.path.dirname(jieba.__file__)}")
    except ImportError:
        WARN.append(f"jieba 없음 — turn-router가 L1 키워드 트리거만 돈다. pip install jieba (또는 {vendor}에 설치)")

    for m in OK:
        print(f"  ✅ {m}")
    for m in WARN:
        print(f"  ⚠️  {m}")
    for m in FAIL:
        print(f"  ❌ {m}")
    print(f"\n== {'FAIL' if FAIL else 'PASS'} — 정상 {len(OK)} · 경고 {len(WARN)} · 문제 {len(FAIL)}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
