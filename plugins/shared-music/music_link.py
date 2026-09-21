"""Link detection for the Shared Music flow.

YouTube id extraction reuses youtube-archive's yt_video_link module directly
(cross-plugin import via sys.path insert — same convention klipy-catgif /
reaction-director already use for klipy_client, see
[[project_klipy_catgif_plugin_2026]]). Only two small, stable, single-purpose
functions are imported (extract_video_id, canonical_url) so a future change
to youtube-archive's session/store internals can't break this plugin; if
youtube-archive is ever disabled or its file moves, this degrades to "no
YouTube link detected" rather than raising, so shared-music never depends on
youtube-archive being enabled.

Spotify has no equivalent existing plugin anywhere in this codebase (grepped
before writing this — see the plugin's design report), so track/album/
episode link parsing here is new.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_YT_ARCHIVE_DIR = Path(os.environ.get("HERMES_DATA") or "/opt/data") / "plugins" / "youtube-archive"


def _load_yt_video_link():
    try:
        if str(_YT_ARCHIVE_DIR) not in sys.path:
            sys.path.insert(0, str(_YT_ARCHIVE_DIR))
        import yt_video_link  # type: ignore

        return yt_video_link
    except Exception:
        return None


_yt_video_link_mod = _load_yt_video_link()

_YT_MUSIC_HOST_RE = re.compile(r"music\.youtube\.com", re.IGNORECASE)

_SPOTIFY_URL_RE = re.compile(
    r"open\.spotify\.com/(?:intl-[a-z]{2}/)?(track|album|episode|show)/([A-Za-z0-9]+)",
    re.IGNORECASE,
)


def extract_youtube_video_id(text: str) -> str | None:
    if _yt_video_link_mod is None:
        return _fallback_youtube_id(text)
    try:
        return _yt_video_link_mod.extract_video_id(text)
    except Exception:
        return _fallback_youtube_id(text)


_FALLBACK_HOST_HINT_RE = re.compile(r"(?:youtube\.com|youtu\.be)", re.IGNORECASE)
_FALLBACK_VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([a-zA-Z0-9_-]{11})")


def _fallback_youtube_id(text: str) -> str | None:
    """Used only if youtube-archive isn't installed/enabled — mirrors its
    regex exactly so behavior is identical either way."""
    if not text or not _FALLBACK_HOST_HINT_RE.search(text):
        return None
    m = _FALLBACK_VIDEO_ID_RE.search(text)
    return m.group(1) if m else None


def is_youtube_music_host(text: str) -> bool:
    return bool(text and _YT_MUSIC_HOST_RE.search(text))


def youtube_canonical_url(video_id: str) -> str:
    if _yt_video_link_mod is not None:
        try:
            return _yt_video_link_mod.canonical_url(video_id)
        except Exception:
            pass
    return f"https://www.youtube.com/watch?v={video_id}"


def extract_spotify_link(text: str) -> tuple[str, str] | None:
    """Return (kind, spotify_id) for a track/album/episode/show link, else None."""
    if not text:
        return None
    m = _SPOTIFY_URL_RE.search(text)
    if not m:
        return None
    return m.group(1).lower(), m.group(2)


def spotify_track_url(spotify_id: str) -> str:
    return f"https://open.spotify.com/track/{spotify_id}"


class DetectedLink:
    __slots__ = ("source", "id", "url", "is_music_host")

    def __init__(self, source: str, id_: str, url: str, is_music_host: bool = False):
        self.source = source  # "youtube" | "spotify"
        self.id = id_
        self.url = url
        self.is_music_host = is_music_host

    def __repr__(self) -> str:  # pragma: no cover
        return f"DetectedLink({self.source}, {self.id})"


def detect_link(text: str) -> DetectedLink | None:
    spotify = extract_spotify_link(text)
    if spotify:
        kind, sid = spotify
        if kind in ("track",):
            return DetectedLink("spotify", sid, spotify_track_url(sid), is_music_host=True)
        # album/episode/show links are out of MVP scope (no single-track identity) —
        # not treated as a music link here, left to fall through to normal chat.
        return None

    video_id = extract_youtube_video_id(text)
    if video_id:
        return DetectedLink(
            "youtube", video_id, youtube_canonical_url(video_id), is_music_host=is_youtube_music_host(text)
        )
    return None
