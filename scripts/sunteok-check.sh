#!/usr/bin/env bash
set -euo pipefail

python3 - <<'PY'
import fcntl
import json
import os
import random
import sqlite3
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

DATA = os.environ.get('HERMES_DATA', '/opt/data')
DB = Path(f'{DATA}/state.db')
STATE_FILE = Path(f'{DATA}/.hermes/state/sunteok-random.json')
FORCE_TEST = Path(f'{DATA}/.hermes/state/sunteok-force-test')
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

# 동시 실행 직렬화 — wake-gate가 5분마다 도는데 두 개가 겹치면 각자 저장해 lost update가 난다.
_lk = open(f"{STATE_FILE}.lock", "a")
fcntl.flock(_lk, fcntl.LOCK_EX)


def silent_exit():
    # Wake-gate: tell the cron scheduler to skip the agent entirely.
    # Without this the LLM wakes every 5 minutes just to say [SILENT].
    print('{"wakeAgent": false}')
    raise SystemExit(0)


def print_recent_sunteok(limit=2):
    """직전에 보낸 선톡 본문을 컨텍스트로 출력 — 같은 건수 재탕 방지용."""
    # 잡 ID는 설치본마다 다르다 — 환경변수로 받고, 없으면 최근 선톡 참고를 건너뛴다.
    job_id = os.environ.get('SUNTEOK_JOB_ID', '')
    if not job_id:
        return
    out_dir = Path(f'{DATA}/cron/output/{job_id}')
    if not out_dir.is_dir():
        return
    files = sorted(out_dir.glob('*.md'), key=lambda p: p.stat().st_mtime, reverse=True)
    shown = 0
    print('=== 최근 보낸 선톡 (반복 금지 대상) ===')
    for f in files:
        if shown >= limit:
            break
        try:
            txt = f.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        if '## Response' not in txt:
            continue
        body = txt.split('## Response', 1)[1].strip()
        if not body or body == '[SILENT]':
            continue
        stamp = f.stem.replace('_', ' ')
        print(f'[{stamp}] {body[:400]}')
        shown += 1
    if not shown:
        print('(없음)')


if FORCE_TEST.exists():
    now_utc = int(time.time())
    now_kst = datetime.fromtimestamp(now_utc, tz=timezone.utc).astimezone(ZoneInfo('Asia/Seoul'))
    print('=== 선톡 트리거 ===')
    print(f'current_kst: {now_kst:%Y-%m-%d %H:%M:%S} ({now_kst:%A})')
    print('trigger: forced test send')
    print_recent_sunteok()
    FORCE_TEST.unlink(missing_ok=True)
    raise SystemExit(0)

if not DB.exists():
    silent_exit()

conn = sqlite3.connect(str(DB))
# 침묵 윈도우는 "사용자" 메시지 기준으로만 잡는다.
# assistant 행까지 세면 배달된 선톡/브리핑이 윈도우를 리셋해서
# 선톡이 자기 메시지에 반응해 30~60분마다 무한 자가루프를 돈다.
# (같은 user 메시지에 대해선 notified_for_ts 가드로 딱 1회만 발화)
cur = conn.execute('''
    SELECT MAX(timestamp)
    FROM messages
    WHERE role = "user"
      AND COALESCE(TRIM(content), "") != ""
''')
row = cur.fetchone()
conn.close()
last_ts = int(row[0]) if row and row[0] else 0
if not last_ts:
    silent_exit()

now_utc = int(time.time())
now_kst = datetime.fromtimestamp(now_utc, tz=timezone.utc).astimezone(ZoneInfo('Asia/Seoul'))
if 2 <= now_kst.hour < 7:
    silent_exit()

def _save(st):
    # 원자적 저장 — 중단 시 부분 JSON이 남아 다음 실행이 손상으로 오판하는 것 방지
    tmp = f"{STATE_FILE}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)

try:
    state = json.loads(STATE_FILE.read_text())
except FileNotFoundError:
    state = {}
except Exception:
    # 상태 손상 — 격리하지 않고 침묵만 하면 다음 실행도 같은 손상을 읽어 '영구 정지'한다.
    # .corrupt-<ts>로 옮긴 뒤 침묵 → 다음 실행은 파일이 없어 정상 재생성된다.
    try:
        os.replace(STATE_FILE, f"{STATE_FILE}.corrupt-{now_utc}")
    except OSError:
        pass
    silent_exit()

if state.get('last_contact_ts') != last_ts:
    target_ts = last_ts + random.SystemRandom().randint(30 * 60, 60 * 60)
    state = {
        'last_contact_ts': last_ts,
        'target_ts': target_ts,
        'notified_for_ts': None,
        'reengage_count': 0,      # 유저가 말했으니 재접근 예산 리셋
    }
    _save(state)
else:
    target_ts = int(state.get('target_ts') or 0)
    if not target_ts:
        target_ts = last_ts + random.SystemRandom().randint(30 * 60, 60 * 60)
        state['target_ts'] = target_ts
        _save(state)

if now_utc < target_ts:
    silent_exit()

REENGAGE_SECS = 6 * 3600   # 무응답이 이만큼 지나면 재접근 후보
MAX_REENGAGE = 2           # 같은 침묵 구간에서 재접근 최대 횟수(넘으면 유저가 말할 때까지 침묵).
                           # 0으로 두면 재접근 없음. 이 상한이 없으면 6시간마다 무기한 발사된다.

if state.get('notified_for_ts') == last_ts:
    # 이 유저 메시지에 대해선 이미 선톡했음 → 기본은 침묵.
    # 단, 마지막 발화 후 6시간 넘게 유저가 조용하면 재접근 허용(MAX_REENGAGE회까지).
    last_fire = int(state.get('last_fire_ts') or 0)
    if not last_fire or now_utc - last_fire < REENGAGE_SECS:
        silent_exit()
    if int(state.get('reengage_count') or 0) >= MAX_REENGAGE:
        silent_exit()   # 상한 도달 — 유저가 한 마디라도 하면 상태가 리셋되며 다시 열린다
    last_contact_kst = datetime.fromtimestamp(last_ts, tz=timezone.utc).astimezone(ZoneInfo('Asia/Seoul'))
    print('=== 선톡 트리거 ===')
    print(f'current_kst: {now_kst:%Y-%m-%d %H:%M:%S} ({now_kst:%A})')
    print(f'last_contact_kst: {last_contact_kst:%Y-%m-%d %H:%M:%S}')
    print(f'silent_hours_since_last_fire: {(now_utc - last_fire) // 3600}')
    print('trigger: re-engagement after 6h+ user silence — 직전 선톡과 완전히 다른 각도로, 가볍게')
    print_recent_sunteok()
    state['last_fire_ts'] = now_utc
    state['reengage_count'] = int(state.get('reengage_count') or 0) + 1
    _save(state)
    raise SystemExit(0)

last_contact_kst = datetime.fromtimestamp(last_ts, tz=timezone.utc).astimezone(ZoneInfo('Asia/Seoul'))
target_kst = datetime.fromtimestamp(target_ts, tz=timezone.utc).astimezone(ZoneInfo('Asia/Seoul'))
elapsed_min = max(0, (now_utc - last_ts) // 60)
wait_min = max(0, (target_ts - last_ts) // 60)

print('=== 선톡 트리거 ===')
print(f'current_kst: {now_kst:%Y-%m-%d %H:%M:%S} ({now_kst:%A})')
print(f'last_contact_kst: {last_contact_kst:%Y-%m-%d %H:%M:%S}')
print(f'target_kst: {target_kst:%Y-%m-%d %H:%M:%S}')
print(f'elapsed_minutes: {elapsed_min}')
print(f'random_window_minutes: 30~60')
print(f'chosen_wait_minutes: {wait_min}')
print('trigger: random 30~60min since last user message, within active hours')
print_recent_sunteok()

state['notified_for_ts'] = last_ts
state['last_fire_ts'] = now_utc
_save(state)
PY
