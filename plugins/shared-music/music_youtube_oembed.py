"""YouTube oEmbed — a metadata path that survives the player-API bot check.

Measured on the live server against the exact video that broke this feature
(JeucohIa5LQ, a "Gary Numan - Topic" Art Track): yt-dlp's player API returns
"Sign in to confirm you're not a bot" on every player_client and over the
proxy, while oEmbed answers in ~75ms with title "M.E." and author "Gary Numan
- Topic". oEmbed is a public, unauthenticated, documented endpoint that never
touches the player/format machinery, which is exactly why the bot check
doesn't apply to it.

It cannot replace yt-dlp: it returns no duration and no audio formats. What it
buys is the difference between "링크가 안 열려" and "아 이 곡 알지" — the
metadata-only degraded mode. Audio, and therefore any actual listening claim,
still requires the player API to work.

Field-for-field compatible with music_youtube_audio.fetch_youtube_metadata's
return shape *on purpose*: for a Topic Art Track, yt-dlp yields
track="M.E."/artist="Gary Numan" and oEmbed yields title="M.E."/author="Gary
Numan - Topic" -> "Gary Numan", so both paths produce the same
canonical-identity inputs and the same track file. Only duration differs
(None here), which music_cache.find_by_artist_title already exists to bridge.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

OEMBED_URL = "https://www.youtube.com/oembed"
FETCH_TIMEOUT_S = 6

# YouTube's auto-generated per-artist channels are always "<Artist> - Topic";
# the suffix is channel plumbing, never part of the artist's name.
_TOPIC_SUFFIX_RE = re.compile(r"\s*-\s*Topic\s*$", re.IGNORECASE)
# Same idea for the "VEVO" channel-name convention.
_VEVO_SUFFIX_RE = re.compile(r"VEVO\s*$", re.IGNORECASE)


def _clean_author(author: str) -> str:
    if not author:
        return ""
    author = _TOPIC_SUFFIX_RE.sub("", author)
    author = _VEVO_SUFFIX_RE.sub("", author)
    return author.strip()


def fetch_youtube_oembed(video_id: str, *, opener=None) -> dict | None:
    """Returns {"title", "artist", "duration_s": None} or None. Never raises —
    this is a fallback, and a failure here must not mask the original
    player-API failure that sent us down this path."""
    url = OEMBED_URL + "?" + urllib.parse.urlencode(
        {"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"}
    )
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        open_fn = opener or urllib.request.urlopen
        with open_fn(request, timeout=FETCH_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        logger.debug("youtube oembed fetch failed for %s", video_id, exc_info=True)
        return None
    title = (payload or {}).get("title") or ""
    if not title:
        return None
    return {
        "title": title,
        "artist": _clean_author((payload or {}).get("author_name") or ""),
        # oEmbed genuinely has no duration field — left explicit rather than
        # guessed, because a wrong duration would silently fork canonical ids.
        "duration_s": None,
    }
