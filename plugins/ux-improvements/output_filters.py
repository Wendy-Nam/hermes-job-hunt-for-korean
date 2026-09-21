"""LLM 응답 후처리 필터 3종 + transform_llm_output 훅.

1. CoT 누수 스트리퍼 (2026-08-14, 2026-09-07 확장): go/deepseek-flash(추론 변형)가
   내부 추론을 본문에 흘림: ①비한글·비CJK 외국 문자 토큰(구자라트·데바나가리·아랍·
   태국 등 — 이 배포에선 정당한 용례 없음) ②말미의 짧은 영어 메타 조각("Good.",
   "Let's answer.", "Need no ...") ③<system_warning>/<system_reminder> 내부 태그
   블록(닫히지 않은 변형 포함 — 실측 message id 209429·209419·208886).  fail-open.
2. 꼬리 반복 루프 가드 (2026-09-11): 실사례로 폴백 모델(cohere)이 장문 감정 메시지를
   반복한 사고 이후 추가. 현재 로컬 패치로 비활성화됨(아래 주석 참고).
3. [SILENT] 계약 가드 (2026-09-11 22:45 사고): 크론 계약은 "[SILENT] 단독"만
   억제한다. 약한 폴백 모델이 이유문을 붙이면 통째로 배달되는 것을 접는다.

fail-open 전부: 오류 시 원문 반환.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from compression_filter import filter_compression_noise

logger = logging.getLogger(__name__)

_FOREIGN_SCRIPT_RE = re.compile(r"[؀-ۿऀ-෿฀-๿]+\S*\s?")
_META_TAIL_RE = re.compile(
    r"(?<=[다요어야지죠네걸래줘함임음까\.\?\!~ㅋㅎ])\s+"
    r"([A-Za-z][\x20-\x7E]{0,89})\s*$")
_META_WORDS_RE = re.compile(
    r"\b(Good|Okay|OK|Let'?s|answer|respond|reply|Korean|sentences?|Need|maybe|priori|"
    r"rules?|Should|write|tone|Now|no\.)\b", re.I)
# 닫힌 형태(</system_warning>)와 안 닫힌 형태(끝까지 안 닫히면 문자열 끝까지 제거) 둘 다 매칭.
_SYSTEM_LEAK_RE = re.compile(
    r"<(system_warning|system_reminder)>.*?(?:</\1>|\Z)",
    re.IGNORECASE | re.DOTALL,
)


def _strip_cot_leak(text: str) -> str:
    try:
        out = _SYSTEM_LEAK_RE.sub("", text)
        had_system_leak = out != text  # 태그 블록 자체가 누수였으면 아래 "전부 지워짐" 되돌림 가드 예외
        out = _FOREIGN_SCRIPT_RE.sub("", out)
        m = _META_TAIL_RE.search(out)
        if m and _META_WORDS_RE.search(m.group(1)) and not re.search(r"[가-힣]", m.group(1)):
            out = out[: m.start()]
        out = out.rstrip()
        if out.strip():
            return out
        # 휴리스틱(외국문자/메타꼬리)만으로 전부 지워졌다면 과잉제거 방지로 원문 유지.
        # 단, 시스템 태그가 통째로 누수였던 경우(내용 전체가 누수)는 빈 결과를 그대로 인정.
        return out if had_system_leak else text
    except Exception:
        return text


_SENT_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+|\n+")
_WORD_RE = re.compile(r"[가-힣a-zA-Z0-9]+")
_LOOP_MIN_CHARS, _LOOP_COUNT, _LOOP_KEEP = 200, 4, 2


def _grams3(s: str) -> list:
    w = _WORD_RE.findall(s)
    return [tuple(w[j:j + 3]) for j in range(len(w) - 2)]


# ponytail: 정확 3-gram 카운트만(퍼지 없음). 100자 미만으로 줄면 원문 유지. fail-open.
def _collapse_repetition(text: str) -> str:
    return text  # local patch 2026-09-18: DISABLED — false positives destroyed legit
    # messages daily (IP octets, refrains); adapter chunk cap backstops real loops.
    try:
        if len(text) < _LOOP_MIN_CHARS:
            return text
        from collections import Counter
        counts = Counter(_grams3(text))
        if not counts:
            return text
        top, n = counts.most_common(1)[0]
        if n < _LOOP_COUNT:
            return text
        sents = [x for x in _SENT_SPLIT_RE.split(text) if x.strip()]
        seen = 0
        for i, x in enumerate(sents):
            seen += _grams3(x).count(top)
            if seen > _LOOP_KEEP:
                kept = "\n".join(sents[:i]).rstrip()  # local fix 2026-09-18: space-join이 개행을 전부 파괴했음
                if i >= 2 and len(kept) >= 100:  # 앞 두 문장은 살리고, 루프가 일찍 시작해도 절단
                    logger.info("ux-improvements: repetition loop cut (%d -> %d chars, %r x%d)",
                                len(text), len(kept), " ".join(top), n)
                    return kept
                return text
        return text
    except Exception:
        return text


# **[SILENT]** 같은 강조 감싸기도 인식 (2026-09-12 07:00 선톡: 본문+메타+**[SILENT]**가 통째 배달)
_SILENT_LINE_RE = re.compile(r"^\s*[*_`]*\[?SILENT\]?[*_`]*\s*$", re.I)


def _collapse_silent(text: str) -> str:
    try:
        lines = [l for l in text.strip().splitlines() if l.strip()]
        if len(lines) >= 2 and (_SILENT_LINE_RE.match(lines[0]) or _SILENT_LINE_RE.match(lines[-1])):
            logger.info("ux-improvements: [SILENT]+content collapsed (%d chars dropped)", len(text))
            return "[SILENT]"
        return text
    except Exception:
        return text


def on_transform_llm_output(*, response_text: str = "", **_kwargs) -> Optional[str]:
    """transform_llm_output 훅: CoT 누수와 compression 노이즈를 제거한다.

    fail-open: 오류 시 None 반환 → 호스트가 원문 사용.
    주의: 호스트 계약상 None/빈 문자열은 "변경 없음"이므로, 비교는 반드시
    가공 전 원문(original)과 해야 한다. 중간 결과와 비교하면 스트립 결과가 버려진다.
    """
    original = response_text
    try:
        out = _collapse_silent(_collapse_repetition(filter_compression_noise(_strip_cot_leak(original))))
        if not out or out == original:
            return None
        logger.info(
            "ux-improvements: output filtered (%d -> %d chars)",
            len(original), len(out),
        )
        return out
    except Exception as e:
        logger.debug("ux-improvements transform_llm_output failed (fail-open): %s", e)
        return None
