"""Canonical track identity — so a YouTube link and a Spotify link for the
same song resolve to one Track note instead of two (task item 12).

canonical_id = sha1(normalized_artist + "|" + normalized_title + "|" + duration_bucket)

Duration bucket is a 5-second-tolerance bucket, included only when a
duration is known — Spotify's oEmbed doesn't expose duration (no Spotify Web
API credentials exist anywhere in this codebase, confirmed by grep before
building this), so a Spotify-derived identity is artist+title only.
Colliding with an artist+title+duration id from the YouTube side is fine and
intended: TrackStore.resolve() below treats an artist+title match as
sufficient once at least one side lacks duration.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

_FEAT_RE = re.compile(r"\(feat[^)]*\)|\[feat[^\]]*\]|feat\.\s.*$", re.IGNORECASE)
_BRACKET_NOISE_RE = re.compile(
    r"\((official|audio|video|lyrics?|mv|m/v|visualizer|hd|4k)[^)]*\)"
    r"|\[(official|audio|video|lyrics?|mv|m/v|visualizer|hd|4k)[^\]]*\]",
    re.IGNORECASE,
)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9가-힣]+")


def normalize(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = _FEAT_RE.sub("", text)
    text = _BRACKET_NOISE_RE.sub("", text)
    text = text.lower()
    text = _NON_ALNUM_RE.sub(" ", text).strip()
    return re.sub(r"\s+", " ", text)


def duration_bucket(duration_s: float | None, tolerance_s: int = 5) -> str | None:
    if not duration_s or duration_s <= 0:
        return None
    return str(int(round(duration_s / tolerance_s)))


def canonical_id(artist: str, title: str, duration_s: float | None = None) -> str:
    key = f"{normalize(artist)}|{normalize(title)}|{duration_bucket(duration_s) or ''}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def artist_title_key(artist: str, title: str) -> str:
    """Duration-independent key, used to match a Spotify-derived identity
    (no duration available) against an existing YouTube-derived Track."""
    return f"{normalize(artist)}|{normalize(title)}"
