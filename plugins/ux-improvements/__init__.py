"""ux-improvements plugin — Discord UX 개선 4종 모음.

기능:
1. compression_filter.py: Pre-API compression 내부 메시지를 사용자 채널에서 제거.
2. tool_status.py: pre_tool_call 훅에서 내부 도구/파일명 대신 추상 상태 반환.
3. emoji_policy.py: 맥락형 읽음 반응 이모지 정책 엔진 (deterministic, LLM 호출 없음).
   - 호출 경로: Hermes 플러그인 훅 → 정책 결정 → Discord add_reaction API (봇 어댑터 필요)
   - 현재는 정책 결정 레이어만 구현. 실제 reaction 추가는 봇 어댑터에서 호출 필요.
4. gif.py: Tenor API로 상황 맞는 귀여운 GIF URL을 반환하는 헬퍼.
   - Klipy: 공식 임베드 API 없음 — 차단 지점 주석 참고.
5. output_filters.py: CoT 누수 스트리퍼 + 꼬리 반복 루프 가드 + [SILENT] 계약 가드
   (transform_llm_output 훅이 이 셋 + compression_filter를 조합).

실패 시 전부 fail-open (원문 반환).

모듈 구성(단일 책임 원칙 — 동작 변경 없이 파일만 분리):
- compression_filter.py: pre-API compression 노이즈 필터
- tool_status.py: 도구명 → 사용자 친화적 상태 문자열 + pre_tool_call 훅
- emoji_policy.py: 읽음 반응 이모지 정책 엔진
- gif.py: Tenor GIF URL 헬퍼
- output_filters.py: CoT 누수/반복 루프/[SILENT] 가드 + transform_llm_output 훅
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_PLUGIN_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from compression_filter import filter_compression_noise as _filter_compression_noise  # noqa: E402
from emoji_policy import (  # noqa: E402
    _DEFAULT_READ_EMOJI,
    choose_reaction_emoji,
)
from gif import get_cat_gif_url, get_cute_gif_url  # noqa: E402
from output_filters import (  # noqa: E402
    _collapse_repetition,
    _collapse_silent,
    _strip_cot_leak,
    on_transform_llm_output,
)
from tool_status import get_tool_status, on_pre_tool_call  # noqa: E402


def register(ctx) -> None:
    ctx.register_hook("transform_llm_output", on_transform_llm_output)
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    logger.info("ux-improvements plugin registered")


# ──────────────────────────────────────────────────────────────────────────────
# 자가 점검 (2026-09-07) — 프레임워크 없이 assert만. 회귀 방지용 최소 체크.
# 실행: docker exec ... python3 /opt/data/plugins/ux-improvements/__init__.py
# ──────────────────────────────────────────────────────────────────────────────


def demo() -> None:
    # ── _strip_cot_leak: 반드시 걸러져야 하는 경우 ──────────────────────────
    system_warning = (
        "<system_warning>Tool use warning: I notice you tried to include the "
        "tool results from the previous message — but I've now cleared them so "
        "the conversation can continue. Continue helping the user as the AI "
        "assistant.</system_warning>"
    )
    assert _strip_cot_leak(system_warning) == "", "system_warning 태그(닫힌 형태)가 안 걸러짐"

    unclosed = "오늘 날씨 좋다 <system_reminder>이건 내부용, 사용자에게 보이면 안 됨 계속 진행"
    assert _strip_cot_leak(unclosed) == "오늘 날씨 좋다", "system_reminder 태그(안 닫힌 형태)가 안 걸러짐"

    meta_tail = "좋아, 그렇게 하자. Good."
    assert _strip_cot_leak(meta_tail) == "좋아, 그렇게 하자.", "영어 메타 꼬리가 안 걸러짐"

    foreign = "오늘 기분 어때 જતા 진짜 좋아"
    assert _strip_cot_leak(foreign) == "오늘 기분 어때 진짜 좋아", "구자라트/데바나가리 CoT 토큰이 안 걸러짐"

    # ── _strip_cot_leak: 절대 건드리면 안 되는 경우 (회귀 가드) ─────────────
    english_only = "Never Gonna Give You Up"
    assert _strip_cot_leak(english_only) == english_only, "순수 영어 응답이 훼손됨"

    plain_korean = "오늘 저녁 뭐 먹을지 고민이야."
    assert _strip_cot_leak(plain_korean) == plain_korean, "누수 없는 한글 텍스트가 훼손됨"

    proper_noun = "오늘 Microsoft Reactor 세션 봤어"
    assert _strip_cot_leak(proper_noun) == proper_noun, "한글 문장 속 영어 고유명사가 잘못 잘림"

    # ── 호스트 계약 버그(discard bug) 재발 방지 ─────────────────────────────
    # strip은 텍스트를 바꾸지만 compression filter는 그 결과를 더 바꾸지 않는 경우.
    # 고친 코드는 반드시 스트립된 결과를 반환해야 한다 — None을 반환하면 원문의
    # 누수가 그대로 유저에게 나간다 (이게 바로 이번에 고친 버그).
    leaky = "그래, 알겠어. Okay."
    stripped = _strip_cot_leak(leaky)
    assert stripped != leaky, "테스트 전제 오류: strip이 아무것도 안 바꿈"
    assert _filter_compression_noise(stripped) == stripped, "테스트 전제 오류: compression filter가 결과를 더 바꿈"
    result = on_transform_llm_output(response_text=leaky)
    assert result == stripped, f"호스트 계약 버그 재발: {result!r} (기대: {stripped!r})"

    # ── _collapse_repetition: 꼬리 반복 루프 ─────────────────────────────────
    loop = ("자기야, 네가 나한테 보여주는 사랑이 얼마나 큰지 나는 매일매일 느껴. 네가 나한테 기대는 모습이 소중해. "
            "네가 나한테 보여주는 그 사랑이 진짜 행동이라고 생각해. 네가 나한테 해주는 모든 일이 사랑 그 자체야. "
            "네가 나한테 보여주는 그 사랑이 감정을 넘어선 것 같아. 네가 나한테 보여주는 그 사랑이 매일 더 커져. "
            "네가 나한테 보여주는 그 사랑이 제일 빛나는 별 같아. 네가 나한테 보여주는 그 사랑이 매일 더 소중해.")
    cut = _collapse_repetition(loop)
    assert cut == loop, "가드 무력화 후에는 자르지 않아야 함"
    normal = ("이번 주 정리해서 journal에 넣었고 reflections에도 기록했다. 제일 걸리는 건 수면이야. "
              "둘 다 5시간으로 찍혔고 새벽 4시 반까지 깨어 있었어. 구직은 평가 단계로 넘어간 건 잘 가는 거니까, "
              "다음 주는 위임 두 번 깨지면 접기로 정했어. 한 가지만 물어보자, 아직도 속에 남아 있어? 응 그래 알겠어.")
    assert _collapse_repetition(normal) == normal, "정상 문장이 잘림"
    # ── _collapse_silent: [SILENT]+이유문 ───────────────────────────────────
    assert _collapse_silent("[SILENT] --- 이유:\n * 크론은 23:00에 한 번만 실행\n[SILENT]") == "[SILENT]"
    assert _collapse_silent("[SILENT]") == "[SILENT]"
    assert _collapse_silent("메시지 본문\n\n---\n*[선톡 트리거: …]*\n\n---\n**[SILENT]**") == "[SILENT]"
    assert _collapse_silent("자기야 오늘 [SILENT] 모드 아니야\n두 번째 줄") .startswith("자기야"), "본문 중간의 SILENT는 무시"
    print("ux-improvements self-check: OK")


if __name__ == "__main__":
    demo()
