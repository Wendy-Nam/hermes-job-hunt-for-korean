"""Pre-API compression 메시지 필터.

Hermes/Claude Code runtime이 삽입하는 내부 디버그 텍스트("Pre-API compression:
~N tokens near the context/output limit. Compacting...")가 사용자 채널에
그대로 노출되는 것을 막는다.
"""
from __future__ import annotations

import re

# 차단할 내부 상태 메시지 패턴 (Hermes/Claude Code runtime이 삽입하는 디버그 텍스트)
_COMPRESSION_PATTERNS: list[re.Pattern] = [
    re.compile(
        r"(?:📦\s*)?Pre-API compression[^\n]*?(?:tokens?|context|output|limit)[^\n]*\.",
        re.IGNORECASE,
    ),
    re.compile(
        r"near the context[/\s]output limit[^\n]*\.",
        re.IGNORECASE,
    ),
    re.compile(
        r"Compacting before the next model call[^\n]*\.",
        re.IGNORECASE,
    ),
    re.compile(
        r"context compaction[^\n]*\.",
        re.IGNORECASE,
    ),
]


def filter_compression_noise(text: str) -> str:
    """사용자에게 보여지는 응답에서 내부 compression 메시지를 제거한다."""
    result = text
    for pat in _COMPRESSION_PATTERNS:
        result = pat.sub("", result)
    # 남은 빈 줄 정리 (연속 빈 줄 → 최대 1줄)
    result = re.sub(r"\n{3,}", "\n\n", result).strip()
    return result
