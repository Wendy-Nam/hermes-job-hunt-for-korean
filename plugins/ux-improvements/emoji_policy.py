"""맥락형 읽음 반응 이모지 정책 엔진 (deterministic, LLM 호출 없음).

호출 경로: Hermes 플러그인 훅 → 정책 결정 → Discord add_reaction API (봇 어댑터 필요)
현재는 정책 결정 레이어만 구현. 실제 reaction 추가는 봇 어댑터에서 호출 필요.
"""
from __future__ import annotations

import re

# 이모지 선택 규칙: (우선순위, 감지 패턴 목록, 이모지)
# 높은 우선순위(낮은 숫자)가 먼저 매칭됨
_EMOJI_RULES: list[tuple[int, re.Pattern, str]] = [
    # 감사/칭찬
    (10, re.compile(r"(?:고마워|감사|thank|잘했|최고|대박|훌륭|완벽|👏|잘 됐|굿잡)", re.I), "🫶"),
    # 슬픔/걱정
    (10, re.compile(r"(?:슬퍼|우울|힘들어|지쳐|못하겠|포기|ㅠㅠ|😭|😢|눈물|힘드네|힘드다)", re.I), "🫂"),
    # 기쁨/신남
    (10, re.compile(r"(?:신난|기뻐|좋아|행복|재밌|ㅋㅋ|😆|🥹|ㅎㅎ|흥|히히|야호|와!|완전 좋)", re.I), "🎉"),
    # 놀람
    (10, re.compile(r"(?:헐|ㄷㄷ|충격|놀라|대박|어떻게|진짜?!|설마|wow|omg)", re.I), "😲"),
    # 이해/읽음 (기본 ACK)
    (10, re.compile(r"(?:ㅇㅇ|응|알겠|ㅋㅋ|오케이|ok|맞아|알아)", re.I), "👀"),
    # 고민/질문
    (10, re.compile(r"(?:\?|어떻게|뭐야|왜|모르겠|궁금|헷갈|의문)", re.I), "🤔"),
    # 몸 아픔/건강
    (10, re.compile(r"(?:아파|아프|병원|감기|두통|배탈|열나|몸살|피곤|지침)", re.I), "🏥"),
    # 음식
    (10, re.compile(r"(?:밥|먹|식사|맛있|배고|요리|음식|점심|저녁|아침)", re.I), "🍽️"),
    # 잠/피로
    (10, re.compile(r"(?:자야|잘게|피곤|졸려|자러|잔다|굿나잇|good night|sleep)", re.I), "😴"),
    # 작업/개발
    (10, re.compile(r"(?:코드|개발|버그|error|에러|fix|수정|deploy|배포|PR|커밋|테스트)", re.I), "💻"),
    # 장난/농담
    (20, re.compile(r"(?:ㅋㅋㅋㅋ|ㅎㅎㅎ|드립|개그|농담|장난)", re.I), "😂"),
]

# 기본 읽음 이모지
_DEFAULT_READ_EMOJI = "👀"


def choose_reaction_emoji(message_text: str) -> str:
    """메시지 맥락에 맞는 읽음 반응 이모지를 결정한다.

    Args:
        message_text: 사용자가 보낸 메시지 전문

    Returns:
        이모지 단일 문자열 (예: '👀', '🫶')
    """
    if not message_text:
        return _DEFAULT_READ_EMOJI

    # 이모지 규칙 순서대로 매칭
    for _priority, pattern, emoji in sorted(_EMOJI_RULES, key=lambda x: x[0]):
        if pattern.search(message_text):
            return emoji

    return _DEFAULT_READ_EMOJI
