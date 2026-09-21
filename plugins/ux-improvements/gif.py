"""GIF URL 생성 헬퍼 (Tenor API).

Klipy 차단 지점:
  Klipy(https://klipy.co)는 공개 임베드/검색 API를 제공하지 않음.
  GIF를 봇이 전송하려면 Klipy 측의 공식 Bot API 또는 파트너십이 필요.
  현재는 Tenor API를 대안으로 사용.

Discord에서 GIF 전송 방법:
  1. GIF URL을 메시지 본문에 포함하면 Discord가 자동으로 미리보기 렌더링
  2. 파일 첨부 (discord.File)로 직접 업로드
  방법 1이 외부 URL을 사용하므로 추가 다운로드 없이 구현 가능
"""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)


def get_cute_gif_url(context: str = "cute cat", limit: int = 1) -> Optional[str]:
    """상황에 맞는 귀여운 GIF URL을 Tenor API에서 검색한다.

    Args:
        context: 검색어 (예: 'cute cat', 'happy', 'excited')
        limit: 검색 결과 수 (기본 1)

    Returns:
        tinygif URL 문자열 또는 None (API 키 없음/오류 시)

    Note:
        TENOR_API_KEY 환경변수가 필요.
        없으면 None 반환 (fail-open).
    """
    api_key = os.environ.get("TENOR_API_KEY", "").strip()
    if not api_key:
        logger.debug("get_cute_gif_url: TENOR_API_KEY not set, skipping")
        return None

    query = urllib.parse.quote_plus(context)
    url = (
        f"https://tenor.googleapis.com/v2/search"
        f"?q={query}&limit={limit}&key={api_key}"
        f"&contentfilter=medium&media_filter=tinygif"
    )
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.load(resp)
        results = data.get("results", [])
        if not results:
            return None
        gif_url = results[0].get("media_formats", {}).get("tinygif", {}).get("url")
        return gif_url
    except Exception as e:
        logger.debug("get_cute_gif_url failed: %s", e)
        return None


def get_cat_gif_url() -> Optional[str]:
    """귀여운 고양이 GIF URL을 반환한다."""
    return get_cute_gif_url("cute cat kawaii")
