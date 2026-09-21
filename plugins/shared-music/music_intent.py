"""Music-listening intent detection — the trigger gate (task item 22).

Superseded in part, 2026-08-10 — read this first
------------------------------------------------
The phrase list below is no longer the whole gate. It is now *tier 1* of the
provenance-first router in ``plugins/_shared/hermes_media_route.py``, reached
through :func:`route_share` at the bottom of this file.

The reason is a live defect. The rationale below argues that "a YouTube link
alone is NOT enough to enter the Shared Music flow", which is still true — but
the code that followed from it carried an unstated assumption: that anything
this gate rejected was therefore *video*, safe to leave to youtube-archive.
Nothing enforced that, and youtube-archive claimed every youtube.com link
unconditionally. So a bare share of a "Gary Numan - Topic" Art Track — no
listening phrase, unambiguously music by its channel — was rejected here and
filed as a video document in ``wiki/Youtube/``.

The router keeps the conservative principle and removes the false dichotomy:
this phrase list fires exactly as it always did (tier 1), media metadata gets
to speak when the user didn't (tier 4), and a share neither can classify
becomes ``unknown`` — held, not handed to the video archive by default.

Original rationale follows.

Deliberately conservative and deterministic (no LLM call, no yt-dlp category
lookup before we even know whether to proceed): a YouTube link alone is NOT
enough to enter the Shared Music flow (it might be a lecture/podcast/review —
that stays on the existing youtube-content/youtube-archive paths). A message
only enters the Shared Music flow when:

  - it has a Spotify *track* link (unambiguously music), OR
  - the YouTube link is on music.youtube.com (unambiguously music), OR
  - a YouTube link is paired with an explicit music-listening intent phrase
    ("들어봐", "이거 어때", "감상해봐", "노래 좋지", "같이 들어보자", ...).

This intentionally does not try to classify video content by title/category —
that would need an extra network call before we even know whether to spend
one, and the explicit-intent-phrase path already covers the common real
usage pattern from the spec's own examples.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_INTENT_PATTERNS = [
    r"들어\s*봐",
    r"들어\s*볼래",
    r"같이\s*들어",
    r"감상\s*해\s*봐",
    r"감상\s*좀",
    r"이거\s*어때",
    r"이\s*노래\s*(좋|어때|괜찮)",
    r"노래\s*좋지",
    r"이거\s*노래\s*좋",
    r"이\s*곡\s*(좋|어때|괜찮)",
    r"노래\s*하나\s*(들어|보내)",
    r"플레이\s*리스트",
    r"이거\s*플리",
    r"노래\s*추천",
]

_INTENT_RE = re.compile("|".join(_INTENT_PATTERNS))


def has_music_intent_phrase(text: str) -> bool:
    return bool(text and _INTENT_RE.search(text))


# --- provenance-first trigger -------------------------------------------------
#
# Lives here rather than in the plugin's __init__ so it is importable — and
# therefore testable — without pulling in numpy/scipy/yt-dlp, which are
# vendored under .vendor/ and only present on the server. A gate that can only
# be exercised on production is a gate nobody exercises.

_PLUGIN_DIR = Path(__file__).resolve().parent
# Prefer the plugin-local compatibility mount.  Plugin Doctor copies a plugin
# into an isolated temporary HERMES_HOME, so resolving only the production
# sibling (``plugins/_shared``) makes the genuine shared implementation look
# missing even though this checkout carries it via ``_shared -> ../_shared``.
_SHARED_DIR = next(
    (
        candidate
        for candidate in (
            _PLUGIN_DIR / "_shared",
            _PLUGIN_DIR.parent / "_shared",
        )
        if candidate.is_dir()
    ),
    _PLUGIN_DIR.parent / "_shared",
)
if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))

import hermes_media_probe as _media_probe  # noqa: E402
import hermes_media_route as _media_route  # noqa: E402

OWNER = _media_route.OWNER_MUSIC


def route_share(
    user_message: str,
    *,
    link_source: str | None,
    link_id: str | None,
    invoking_feature: str | None = None,
    active_session_kind: str | None = None,
    probe=None,
    archivist=None,
    session_id: str = "",
):
    """``(is_trigger, decision)`` for a detected link, or ``(False, None)``.

    ``probe`` is injectable purely so tests can answer the metadata question
    offline; production passes nothing and gets the cached oEmbed probe.
    ``archivist``/``session_id`` opt into the stateful path — user-resolved
    destinations, learned preferences, and staged questions.
    """
    if not link_source or not link_id:
        return False, None

    probe_fn = probe or _media_probe.probe_youtube
    if link_source == "spotify":
        metadata = _media_probe.spotify_metadata(link_id)
    else:
        metadata = probe_fn(link_id, source_url=user_message)

    if archivist is not None and session_id:
        # Full path: honours a destination the user has already given for this
        # session, consults their past corrections, and stages a question when
        # the share is genuinely ambiguous.
        outcome = archivist.decide(
            user_message, owner=OWNER, session_id=session_id, media_ref=link_id,
            metadata=metadata, invoking_feature=invoking_feature,
            active_session_kind=active_session_kind,
        )
        return outcome.may_archive, outcome.decision

    # Stateless path, for callers with no session (and for tests that only
    # care about classification): same precedence, no learning, no questions.
    return _media_route.route(
        user_message,
        owner=OWNER,
        invoking_feature=invoking_feature,
        active_session_kind=active_session_kind,
        metadata=metadata,
    )
