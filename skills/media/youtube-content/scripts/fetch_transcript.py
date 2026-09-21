#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["youtube-transcript-api"]
# ///
"""
Fetch a YouTube video transcript and output it as structured JSON.

Usage:
    uv run python3 fetch_transcript.py <url_or_video_id> [--language en,tr] [--timestamps]

Output (JSON):
    {
        "video_id": "...",
        "language": "en",
        "segments": [{"text": "...", "start": 0.0, "duration": 2.5}, ...],
        "full_text": "complete transcript as plain text",
        "timestamped_text": "00:00 first line\n00:05 second line\n..."
    }

Install dependency:  uv pip install youtube-transcript-api
"""

import argparse
import os
import json
import re
import sys


def extract_video_id(url_or_id: str) -> str:
    """Extract the 11-character video ID from various YouTube URL formats."""
    url_or_id = url_or_id.strip()
    patterns = [
        r'(?:v=|youtu\.be/|shorts/|embed/|live/)([a-zA-Z0-9_-]{11})',
        r'^([a-zA-Z0-9_-]{11})$',
    ]
    for pattern in patterns:
        match = re.search(pattern, url_or_id)
        if match:
            return match.group(1)
    return url_or_id


def format_timestamp(seconds: float) -> str:
    """Convert seconds to HH:MM:SS or MM:SS format."""
    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def fetch_transcript(video_id: str, languages: list = None):
    """Fetch transcript segments from YouTube.

    Returns a list of dicts with 'text', 'start', and 'duration' keys.
    Compatible with youtube-transcript-api v1.x.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        print("Error: youtube-transcript-api not installed. Run: uv pip install youtube-transcript-api",
              file=sys.stderr)
        sys.exit(1)

    # 프록시 슬롯 — 유튜브는 VPS(클라우드 IP)를 봇으로 막는다("RequestBlocked/IpBlocked").
    #   ① Webshare(무료 티어 가능): WEBSHARE_PROXY_USERNAME + WEBSHARE_PROXY_PASSWORD
    #   ② 범용 프록시:              HERMES_SCRAPER_PROXY 또는 HTTPS_PROXY (http://user:pass@host:port)
    # 둘 다 없으면 프록시 없이(현행 — 인기 영상만 되고 나머지는 IP 차단당함).
    proxy_config = None
    wu, wp = os.environ.get("WEBSHARE_PROXY_USERNAME"), os.environ.get("WEBSHARE_PROXY_PASSWORD")
    def _resolved_proxy():
        # proxynet 리졸버(죽은 프록시→direct 강등, TTL 캐시). 부재·실패 시 종전 env 동작.
        try:
            sys.path.insert(0, os.path.join(
                os.environ.get("HERMES_DATA", "/opt/data"), "skills", "job-search"))
            import proxynet
            return proxynet.resolve_proxy()
        except Exception:
            return os.environ.get("HERMES_SCRAPER_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")

    generic = _resolved_proxy()
    try:
        if wu and wp:
            from youtube_transcript_api.proxies import WebshareProxyConfig
            proxy_config = WebshareProxyConfig(proxy_username=wu, proxy_password=wp)
        elif generic:
            from youtube_transcript_api.proxies import GenericProxyConfig
            proxy_config = GenericProxyConfig(http_url=generic, https_url=generic)
    except ImportError:
        pass   # 구버전 youtube-transcript-api면 프록시 미지원 — 직접 연결로 폴백

    api = YouTubeTranscriptApi(proxy_config=proxy_config) if proxy_config else YouTubeTranscriptApi()
    if languages:
        result = api.fetch(video_id, languages=languages)
    else:
        result = api.fetch(video_id)

    # v1.x returns FetchedTranscriptSnippet objects; normalize to dicts
    return [
        {"text": seg.text, "start": seg.start, "duration": seg.duration}
        for seg in result
    ]


def main():
    parser = argparse.ArgumentParser(description="Fetch YouTube transcript as JSON")
    parser.add_argument("url", help="YouTube URL or video ID")
    parser.add_argument("--language", "-l", default=None,
                        help="Comma-separated language codes (e.g. en,tr). Default: auto")
    parser.add_argument("--timestamps", "-t", action="store_true",
                        help="Include timestamped text in output")
    parser.add_argument("--text-only", action="store_true",
                        help="Output plain text instead of JSON")
    args = parser.parse_args()

    video_id = extract_video_id(args.url)
    languages = [l.strip() for l in args.language.split(",")] if args.language else None

    try:
        segments = fetch_transcript(video_id, languages)
    except Exception as e:
        error_msg = str(e)
        if "disabled" in error_msg.lower():
            print(json.dumps({"error": "Transcripts are disabled for this video."}))
        elif "no transcript" in error_msg.lower():
            print(json.dumps({"error": f"No transcript found. Try specifying a language with --language."}))
        else:
            print(json.dumps({"error": error_msg}))
        sys.exit(1)

    full_text = " ".join(seg["text"] for seg in segments)
    timestamped = "\n".join(
        f"{format_timestamp(seg['start'])} {seg['text']}" for seg in segments
    )

    if args.text_only:
        print(timestamped if args.timestamps else full_text)
        return

    result = {
        "video_id": video_id,
        "segment_count": len(segments),
        "duration": format_timestamp(segments[-1]["start"] + segments[-1]["duration"]) if segments else "0:00",
        "full_text": full_text,
    }
    if args.timestamps:
        result["timestamped_text"] = timestamped

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
