"""Spotify track identity resolution.

Correction after an initial investigation gap: no *user-plugin* Spotify code
exists under /opt/data (confirmed by grep across plugins/skills/config
before building this) — but the bundled hermes-agent framework itself ships
a native Spotify Web API integration at /opt/hermes/plugins/spotify/
(`kind: backend`, always auto-loaded, 7 tools: playback/devices/queue/
search/playlists/albums/library, PKCE OAuth via `hermes auth spotify`). This
was only discovered *after* first deploying with an oEmbed-only resolver —
`fetch_spotify_track_native()` below now prefers that authoritative client
(exact duration_ms + artist from GET /tracks/{id}) when the user has
completed `hermes auth spotify`, confirmed not the case as of this build
(`auth.json`'s `providers` dict is empty), so it currently falls back to the
oEmbed path every time in practice — but will silently start using the
better data source the moment the user authenticates Spotify, no plugin
change needed. The oEmbed path (open.spotify.com/oembed, public, no auth)
remains the fallback: it returns title/thumbnail/provider but NOT duration
or artist as separate fields (the oEmbed title is usually
"Song Name - Artist Name" or just "Song Name", parsed best-effort below).

Per task item 12: a Spotify page URL never yields audio directly either way
— Spotify's Web API has no audio-download endpoint (preview_url is usually
null post-2023 deprecation, and even when present is a 30s clip, not used
here). Real audio comes from resolving (artist, title) to a matching
YouTube video via music_youtube_audio.search_youtube, then downloading/
analyzing that video exactly like a native YouTube link would be.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request

logger = logging.getLogger(__name__)


def fetch_spotify_track_native(track_id: str) -> dict | None:
    """Authoritative (title, artist, duration_s) via the bundled Spotify Web
    API client, only when the user has completed `hermes auth spotify`.
    Returns None (never raises) if unauthenticated, the import isn't
    available (e.g. running outside the real hermes-agent process, like
    this plugin's own unit tests), or the API call fails — callers must
    fall back to fetch_spotify_oembed in every one of those cases."""
    try:
        from hermes_cli.auth import get_auth_status
        from plugins.spotify.client import SpotifyClient

        if not get_auth_status("spotify").get("logged_in"):
            return None
        track = SpotifyClient().request("GET", f"/tracks/{track_id}")
        if not track or not track.get("name"):
            return None
        artists = ", ".join(a.get("name", "") for a in track.get("artists", []) if a.get("name"))
        duration_ms = track.get("duration_ms")
        return {
            "title": track["name"],
            "artist": artists,
            "duration_s": (duration_ms / 1000.0) if duration_ms else None,
        }
    except Exception:
        logger.debug("native spotify track fetch failed for %s (falling back to oembed)", track_id, exc_info=True)
        return None

OEMBED_URL = "https://open.spotify.com/oembed?url=https://open.spotify.com/track/{track_id}"
FETCH_TIMEOUT_S = 6

_TITLE_ARTIST_SEP_RE = re.compile(r"\s+(?:-|–|—|\|)\s+")


class SpotifyResolveError(Exception):
    pass


def fetch_spotify_oembed(track_id: str) -> dict | None:
    try:
        url = OEMBED_URL.format(track_id=track_id)
        with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        logger.debug("spotify oembed fetch failed for %s", track_id, exc_info=True)
        return None


def parse_title_artist(oembed_title: str) -> tuple[str, str]:
    """Best-effort split of oEmbed's single title string into (title, artist).
    Spotify's oEmbed title is usually just the track name with no artist —
    when no separator is found, artist is returned empty and the caller
    should fall back to a title-only YouTube search."""
    if not oembed_title:
        return "", ""
    parts = _TITLE_ARTIST_SEP_RE.split(oembed_title, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return oembed_title.strip(), ""


def _score_candidate(candidate: dict, title: str, artist: str) -> float:
    text = f"{candidate.get('title', '')} {candidate.get('channel', '')}".lower()
    score = 0.0
    if title and title.lower() in text:
        score += 1.0
    if artist:
        if artist.lower() in text:
            score += 0.8
    if "topic" in (candidate.get("channel") or "").lower():
        score += 0.3  # auto-generated "Artist - Topic" channels are reliably the real track
    if any(k in text for k in ("official audio", "official video", "official mv")):
        score += 0.2
    if any(k in text for k in ("live", "reaction", "cover", "remix", "sped up", "nightcore", "8d audio")):
        score -= 0.6
    return score


def resolve_youtube_candidate(title: str, artist: str, *, search_fn) -> dict | None:
    """search_fn: music_youtube_audio.search_youtube, injected so tests never
    hit the network. Returns the best-scoring candidate dict or None."""
    query = f"{artist} {title}".strip() if artist else title
    if not query:
        return None
    candidates = search_fn(query, max_results=5)
    if not candidates:
        return None
    scored = [(c, _score_candidate(c, title, artist)) for c in candidates]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    best, best_score = scored[0]
    if best_score <= 0:
        return None  # nothing looked like a real match — caller should fail gracefully
    return best
