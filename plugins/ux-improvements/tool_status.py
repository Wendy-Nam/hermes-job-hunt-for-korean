"""상태 표시 단순화 — pre_tool_call 훅.

내부 tool/file/API 이름 대신 사용자 친화적인 추상 상태 메시지를 반환한다.
"어떻게"(내부 구현)가 아니라 "무엇"(사용자 관점)만 표시.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# tool_name → 사용자 친화적 한국어 상태 메시지
_TOOL_STATUS_MAP: dict[str, str] = {
    # 메모리/검색
    "snow_search": "🧠 기억 확인 중...",
    "session_search": "🧠 기억 확인 중...",
    "memory": "🧠 기억 저장 중...",
    "fact_store": "🧠 기억 저장 중...",
    # 브라우저/웹
    "browser_navigate": "🔎 정보 확인 중...",
    "browser_snapshot": "🔎 정보 확인 중...",
    "browser_click": "🔎 정보 확인 중...",
    "browser_type": "🔎 정보 확인 중...",
    "web_extract": "🔎 정보 확인 중...",
    "mcp__browser_navigate": "🔎 정보 확인 중...",
    "mcp__browser_snapshot": "🔎 정보 확인 중...",
    # 파일/코드
    "read_file": "📄 파일 확인 중...",
    "write_file": "✍️ 파일 작성 중...",
    "patch": "✍️ 수정 중...",
    "search_files": "🔎 정보 확인 중...",
    "terminal": "⚙️ 처리 중...",
    "mcp__terminal": "⚙️ 처리 중...",
    "mcp__patch": "✍️ 수정 중...",
    "mcp__read_file": "📄 파일 확인 중...",
    "mcp__write_file": "✍️ 파일 작성 중...",
    "mcp__search_files": "🔎 정보 확인 중...",
    # 이미지
    "vision_analyze": "👀 이미지 확인 중...",
    "mcp__vision_analyze": "👀 이미지 확인 중...",
    "image_generate": "🎨 이미지 생성 중...",
    "mcp__image_generate": "🎨 이미지 생성 중...",
    # 외부 서비스
    "delegate_task": "💭 생각 중...",
    "omh_status": "⚙️ 처리 중...",
    "omh_hud": "⚙️ 처리 중...",
    "omh_gather_evidence": "🔎 정보 확인 중...",
    # 캘린더/이메일
    "GOOGLECALENDAR_CREATE_EVENT": "📅 일정 확인 중...",
    "GMAIL_SEND_EMAIL": "✉️ 메일 작성 중...",
    "GOOGLEDRIVE_UPLOAD_FILE": "📁 파일 처리 중...",
    # 기본
    "DEFAULT": "💭 생각 중...",
}

# 3초 이하 짧은 작업은 상태 표시 생략 (DEBUG 모드에서만)
_SKIP_STATUS_TOOLS = frozenset({"omh_probe", "omh_role", "omh_context"})


def get_tool_status(tool_name: str) -> Optional[str]:
    """tool_name으로 사용자 친화적 상태 문자열을 반환한다.

    Returns:
        상태 문자열 (예: '🧠 기억 확인 중...')
        None이면 상태 표시 생략
    """
    if tool_name in _SKIP_STATUS_TOOLS:
        return None
    return _TOOL_STATUS_MAP.get(tool_name, _TOOL_STATUS_MAP["DEFAULT"])


def on_pre_tool_call(*, tool_name: str = "", platform: str = "", **_kwargs) -> Optional[dict]:
    """pre_tool_call 훅: 내부 도구명 대신 추상 상태 메시지를 반환.

    반환값은 Hermes 런타임이 Discord typing indicator 또는 status 메시지로 활용.
    현재 플러그인 시스템에서 status_text 키를 반환하면 런타임이 처리.

    Note:
        실제 Discord 타이핑 표시(channel.typing())는 봇 어댑터 레벨에서 처리.
        여기서는 status_text 페이로드만 제공.
    """
    try:
        # DEBUG 모드에서는 원본 도구명 표시
        if os.environ.get("HERMES_DEBUG_STATUS", "").lower() in ("1", "true", "yes"):
            return None

        status = get_tool_status(tool_name)
        if status is None:
            return None  # 이 도구는 상태 표시 생략

        return {"status_text": status}
    except Exception as e:
        logger.debug("ux-improvements pre_tool_call failed (fail-open): %s", e)
        return None
