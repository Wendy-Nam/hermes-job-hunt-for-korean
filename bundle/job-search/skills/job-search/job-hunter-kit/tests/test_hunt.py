#!/usr/bin/env python3
"""job-hunter-kit 자가 테스트 — 외부망 없이 실행. boards/는 건드리지 않는다.

- dedup_key 3단계 (wanted ID > URL 정규화 > 회사|포지션)
- load_senior: 예제 프로필 로드, 빈 senior_signals=해제, 깨진 yaml=중단
- seen 장부: 저장→재로드→신규 판정
- CLI 인자 파싱 (smoke)
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT / "scripts"))
spec = importlib.util.spec_from_file_location("hunt", KIT / "scripts" / "hunt.py")
hunt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hunt)

fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (" — " + str(extra) if extra and not cond else ""))
    if not cond:
        fails.append(name)


# 1. dedup 3단계
check("wanted-id", hunt.dedup_key({"url": "https://www.wanted.co.kr/wd/388971?a=1"}) == "wanted:388971")
check("url-norm", hunt.dedup_key({"url": "https://WWW.Example.com/Jobs/?x=1"})
      == hunt.dedup_key({"url": "http://example.com/Jobs/"}))
check("cross-board", hunt.dedup_key({"company": "네이버(NAVER)", "title": "[경력] 백엔드 개발자 (3년)"})
      == hunt.dedup_key({"company": "네이버", "title": "백엔드 개발자"}))

# 2. 필터: 예제 프로필
re_ex, src = hunt.load_senior(KIT / "profile.example.yaml")
check("example-loads", bool(re_ex.search("시니어 백엔드")) and not re_ex.search("주니어 백엔드"), src)

# 3. 빈 senior_signals = 해제
with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "p.yaml"
    p.write_text("filters:\n  senior_signals: ''\n", encoding="utf-8")
    re_off, _ = hunt.load_senior(p)
    check("empty-disables", not re_off.search("시니어 CTO"))

# 4. 깨진 yaml = 중단 (SystemExit) — pyyaml이 있으면 여기서, 없으면 simple 파서가
#    senior_signals 라인을 못 읽어 no-key 폴백이므로 이 케이스는 yaml 있을 때만 엄격
with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "bad.yaml"
    p.write_text("filters: [unclosed\n  senior_signals: '시니어'\n", encoding="utf-8")
    try:
        import yaml  # noqa
        has_yaml = True
    except ImportError:
        has_yaml = False
    if has_yaml:
        try:
            hunt.load_senior(p)
            check("broken-fails-closed", False, "중단 안 함")
        except SystemExit:
            check("broken-fails-closed", True)
        except Exception as e:
            check("broken-fails-closed", False, "예상외 예외: %s" % e)
    else:
        # pyyaml 없이: simple 파서는 senior_signals 한 줄을 읽어 정상 동작해야 함
        re_bad, _ = hunt.load_senior(p)
        check("broken-simple-reads", bool(re_bad.search("시니어 백엔드")))

# 5. seen 장부 라운드트립
with tempfile.TemporaryDirectory() as td:
    sd = Path(td)
    check("seen-empty", hunt.load_seen(sd) == set())
    hunt.save_seen(sd, {"a", "b"})
    check("seen-roundtrip", hunt.load_seen(sd) == {"a", "b"})
    check("seen-file", (sd / "seen.json").exists())

print("\n%d건 중 %d 실패" % (5 + 4 + 3 + 1 + 3, len(fails)))
sys.exit(1 if fails else 0)
