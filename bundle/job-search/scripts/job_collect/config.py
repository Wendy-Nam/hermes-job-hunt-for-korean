#!/usr/bin/env python3
"""job_collect.config — 경로·상수 및 noteio 쓰기 스위치 계층(job-collect.py 원본에서 이식, 동작 동일)."""
import os
import sys
from datetime import timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "bin"))
from noteio import DRY_RUN, note_lock, write_atomic, move, why_dry   # 잠금·원자성·쓰기스위치 단일 계층  # noqa: E402

DATA = os.environ.get("HERMES_DATA") or "/opt/data"   # 명시 env 우선(테스트·호스트 격리), 없으면 컨테이너 기본
_VENDOR = os.environ.get("HERMES_PLUGIN_VENDOR", f"{DATA}/python-site")
if _VENDOR and os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

POSTINGS = f"{DATA}/wiki/automation/job-hunting/postings"
LEDGER = f"{DATA}/.job-seen.json"
REJECTED = f"{DATA}/.job-rejected.json"
STAGING = f"{DATA}/.job-staging.json"
ROTATION = f"{DATA}/.job-rotation.json"
S = f"{DATA}/skills/job-search"
FJ = f"{S}/job-match/scripts/fetch_jd.py"
KST = timezone(timedelta(hours=9))
FETCH_TIMEOUT = 25
PROFILE_PATH = f"{DATA}/search-profile.yaml"
PUBLIC_KEYS = ("url", "title", "company", "jd_text", "location", "employment", "fetched_at", "provenance")
PUBLIC_MAX_RECORDS = 1000
PUBLIC_MAX_BYTES = 2_000_000
