"""Identity verification for alternate uploads.

A playable video is worthless if it is the wrong recording. Listening to a
nightcore edit, a live take or a fan cover and reporting it as the song is a
worse failure than admitting the audio could not be obtained — the user gets a
confident, detailed, wrong impression, which is the one outcome this plugin's
whole honesty design exists to prevent.

The alternate-upload tier in `music_realtime.build_default_ladder` used to take
the first search hit whose id differed from the blocked one, with no scoring, no
duration check and no variant rejection. `music_resolve._find_playable_alternate`
already scored its candidates; this module is that judgement made reusable, so
both alternate paths apply the same test.
"""
from __future__ import annotations

import re

#: Uploads that are reliably a *different recording* of the same song. Matched
#: against title + channel, and only rejected when the reference title does not
#: itself contain the word — a song actually called "Live and Let Die", or one
#: whose title contains "Remix", must not be rejected for matching itself.
VARIANT_MARKERS = (
    "live", "cover", "remix", "nightcore", "sped up", "slowed", "8d audio",
    "karaoke", "instrumental", "reaction", "mashup", "acoustic version",
    "concert", "rehearsal", "tutorial", "lesson",
)

OFFICIAL_MARKERS = ("official audio", "official video", "official mv", "official music video")

#: How far a candidate's runtime may differ before it is treated as a different
#: recording. A live take or an extended mix reliably falls outside this; a
#: re-upload of the same master reliably falls inside. The floor keeps short
#: tracks from being judged on a couple of seconds of trailing silence.
DURATION_TOLERANCE_FRACTION = 0.12
DURATION_TOLERANCE_FLOOR_S = 15.0

#: Below this, the candidate is not a plausible match for the reference at all.
MIN_IDENTITY_SCORE = 0.5

_WORD_RE = re.compile(r"[0-9a-z가-힣]+")


def _words(text: str) -> set[str]:
    return set(_WORD_RE.findall((text or "").lower()))


def title_overlap(candidate_title: str, title: str) -> float:
    """Fraction of the reference title's words present in the candidate.

    Substring matching fails on exactly the uploads that matter: an Art Track
    titled "M.E." never appears verbatim inside
    "Gary Numan - M.E. (Remastered 2016)". Word overlap survives re-ordering,
    bracketed suffixes and channel-prefixed titles.
    """
    reference = _words(title)
    if not reference:
        return 0.0
    return len(reference & _words(candidate_title)) / len(reference)


def duration_matches(candidate_s, reference_s) -> bool:
    """Unknown durations never reject. Refusing every candidate whose duration
    we cannot see would disable this tier on precisely the blocked videos it
    exists to route around."""
    if not candidate_s or not reference_s:
        return True
    tolerance = max(DURATION_TOLERANCE_FLOOR_S, reference_s * DURATION_TOLERANCE_FRACTION)
    return abs(candidate_s - reference_s) <= tolerance


def variant_marker(text: str, reference_title: str) -> str | None:
    reference = (reference_title or "").lower()
    for marker in VARIANT_MARKERS:
        if marker in text and marker not in reference:
            return marker
    return None


def score_candidate(candidate: dict, *, title: str, artist: str) -> float:
    text = f"{candidate.get('title', '')} {candidate.get('channel', '')}".lower()
    score = title_overlap(candidate.get("title", ""), title)
    if artist:
        artist_words = _words(artist)
        if artist_words:
            score += 0.8 * (len(artist_words & _words(text)) / len(artist_words))
    if "topic" in (candidate.get("channel") or "").lower():
        score += 0.3  # auto-generated "Artist - Topic" uploads are the real master
    if any(marker in text for marker in OFFICIAL_MARKERS):
        score += 0.2
    return round(score, 3)


def reject_reason(candidate: dict, *, title: str, artist: str, reference_duration_s=None) -> str | None:
    """None when the candidate is an acceptable stand-in for the reference."""
    text = f"{candidate.get('title', '')} {candidate.get('channel', '')}".lower()
    marker = variant_marker(text, title)
    if marker:
        return f"variant:{marker}"
    if not duration_matches(candidate.get("duration_s"), reference_duration_s):
        return "duration_mismatch"
    if score_candidate(candidate, title=title, artist=artist) < MIN_IDENTITY_SCORE:
        return "low_identity_score"
    return None


def rank_candidates(
    results, *, title: str, artist: str, reference_duration_s=None, exclude_ids=(), limit: int = 3
) -> tuple[list[dict], list[dict]]:
    """(accepted, rejected), accepted score-descending and capped at `limit`.

    Duplicate ids collapse to one entry — a search page repeating a video would
    otherwise spend two probe slots re-proving the same block.
    """
    excluded = {i for i in exclude_ids if i}
    seen: set[str] = set()
    accepted: list[dict] = []
    rejected: list[dict] = []

    for raw in results or []:
        video_id = (raw or {}).get("video_id")
        if not video_id or video_id in excluded or video_id in seen:
            continue
        seen.add(video_id)
        why = reject_reason(raw, title=title, artist=artist, reference_duration_s=reference_duration_s)
        if why:
            rejected.append({"video_id": video_id, "reason": why})
        else:
            accepted.append(dict(raw, score=score_candidate(raw, title=title, artist=artist)))

    accepted.sort(key=lambda c: c["score"], reverse=True)
    return accepted[:limit], rejected


def best_alternate(
    results, *, title: str, artist: str, reference_duration_s=None, exclude_ids=()
) -> dict | None:
    accepted, _rejected = rank_candidates(
        results, title=title, artist=artist,
        reference_duration_s=reference_duration_s, exclude_ids=exclude_ids, limit=1,
    )
    return accepted[0] if accepted else None
