"""Track-identity resolution — turns a DetectedLink into a ResolvedTrack
(artist/title/duration/canonical_id/playable video_id), independent of the
plugin hook wiring so it's unit-testable with injected providers (same
Protocol-injection convention youtube-archive uses for transcript/metadata).

YouTube resolution is a ladder, not a single call, because the top rung is
the one that broke in production (`track_resolved_failed source=youtube
id=JeucohIa5LQ` — YouTube answering the player API with "Sign in to confirm
you're not a bot" from this datacenter IP):

  1. yt-dlp metadata over direct, then over the proxy — title+artist+duration
     in one call. Duration matters here specifically: it feeds the
     canonical-id/cache-hit decision *before* anything is downloaded, which
     is why oEmbed alone can't hold this rung.
  2. oEmbed (music_youtube_oembed) — survives the bot check (~75ms measured
     on the exact blocked video) but yields no duration and no audio.
  3. A different, playable upload of the same recording, scored by the
     Spotify path's existing candidate scorer so a live take or a cover
     never silently becomes "the" track.
  4. Metadata-only: the song is named, `playable` is False, no audio is
     fetched and no listening claim may be made.

Rungs 2-4 are degradation, not success. Only rung 1 and 3 yield audio.

Spotify (task item 12): oEmbed gives a title (rarely a separable artist) with
NO duration and NO audio — resolved by searching YouTube for a matching
video, scored, then treated identically to a native YouTube link from that
point on. If the resulting canonical id (or an artist+title-only match once
duration is known) hits an existing Track already resolved from a YouTube
link, both sources point at the same Track note, not two.

Spotify (task item 12): oEmbed gives a title (rarely a separable artist) with
NO duration and NO audio — resolved by searching YouTube for a matching
video, scored, then treated identically to a native YouTube link from that
point on. If the resulting canonical id (or an artist+title-only match once
duration is known) hits an existing Track already resolved from a YouTube
link, both sources point at the same Track note, not two.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import music_failure as failure
import music_spotify_resolver as spotify
import music_track_identity as identity
import music_youtube_audio as yta
import music_cross_source_identity as cross_identity
import music_youtube_oembed as oembed

logger = logging.getLogger(__name__)


class TrackResolveError(Exception):
    """Carries the classified (stage, reason) when one is known, so the hook
    can trace *why* resolution failed instead of emitting one undifferentiated
    `track_resolved_failed` line for a missing dependency, a private video and
    a datacenter-IP bot check alike."""

    def __init__(self, message: str, *, stage: str = failure.STAGE_METADATA, reason: str = failure.REASON_UNKNOWN):
        self.stage = stage
        self.reason = reason
        super().__init__(message)


@dataclass
class ResolvedTrack:
    artist: str
    title: str
    duration_s: float | None
    video_id: str
    spotify_id: str | None
    canonical_id: str
    # "youtube" | "spotify_resolved_to_youtube" | "youtube_alternate_upload"
    # | "youtube_metadata_only" | "spotify_metadata_only"
    audio_source: str
    # False when identity came from oEmbed because the player API refused the
    # video: we already know the download will fail, so the pipeline skips it
    # rather than spending the full timeout re-proving it.
    playable: bool = True


def resolve_from_youtube(
    video_id: str, *, metadata_fn=None, detail_fn=None, oembed_fn=None, search_fn=None,
    existing_lookup=None, trace_fn=None,
) -> ResolvedTrack:
    """`metadata_fn` (dict|None) stays supported because it is the seam the
    test suite injects at; when it isn't supplied we use the detailed variant
    so the real failure reason survives all the way up to the trace log."""
    if metadata_fn is not None:
        meta, reason = metadata_fn(video_id), failure.REASON_NO_METADATA
    else:
        meta, reason = (detail_fn or yta.fetch_youtube_metadata_detailed)(video_id, trace_fn=trace_fn)

    if meta and meta.get("title"):
        artist = meta.get("artist") or ""
        title = meta["title"]
        duration_s = meta.get("duration_s")
        return ResolvedTrack(
            artist=artist, title=title, duration_s=duration_s, video_id=video_id,
            spotify_id=None, canonical_id=identity.canonical_id(artist, title, duration_s),
            audio_source="youtube", playable=True,
        )

    # --- player API refused this video -------------------------------------
    # Everything below is degradation, in decreasing order of usefulness. The
    # point is that a blocked video should cost us the *audio*, not the whole
    # turn: knowing the song is named "M.E." by Gary Numan is worth far more
    # than a generic "링크를 못 열었어".
    ob = (oembed_fn or oembed.fetch_youtube_oembed)(video_id)
    if not ob:
        raise TrackResolveError(
            f"no metadata for youtube video {video_id}",
            stage=failure.STAGE_METADATA, reason=reason or failure.REASON_NO_METADATA,
        )
    if trace_fn:
        trace_fn("metadata_oembed_fallback", video_id=video_id, blocked_reason=reason)

    title, artist = ob["title"], ob.get("artist") or ""

    # A different upload of the same recording is often playable when the
    # canonical one isn't (Art Tracks are gated far more aggressively than
    # ordinary uploads). Reuses the Spotify path's candidate scorer, which
    # already penalises live/cover/remix/nightcore titles — the wrong-song
    # risk is the reason this doesn't just grab the first search hit.
    #
    # Skipped while the egress itself looks gated. This branch costs two more
    # network round trips (a search, then the candidate's own metadata call) —
    # measured at 7.0s of the 14.0s a gated link took in the production
    # container on 2026-08-11, and in both of the tracks measured the alternate
    # it found was bot-checked too. When several videos in a row have just been
    # refused on *every* transport, that spend is not a gamble with odds, it is
    # a guaranteed 7s added to a turn that is going to end in metadata-only
    # either way. Identity is already secured from oEmbed above, so nothing is
    # lost but the audio that was not going to arrive.
    if _alternate_search_gated(search_fn):
        if trace_fn:
            trace_fn("alternate_upload_skipped", blocked=video_id, reason="egress_gated")
        alt = None
    else:
        alt = _find_playable_alternate(
            video_id, title, artist,
            search_fn=search_fn or yta.search_youtube,
            detail_fn=detail_fn or yta.fetch_youtube_metadata_detailed,
            trace_fn=trace_fn,
        )
    if alt:
        alt_id, alt_meta = alt
        alt_artist = artist or alt_meta.get("artist") or ""
        alt_duration = alt_meta.get("duration_s")
        return ResolvedTrack(
            artist=alt_artist, title=title, duration_s=alt_duration, video_id=alt_id,
            spotify_id=None, canonical_id=identity.canonical_id(alt_artist, title, alt_duration),
            audio_source="youtube_alternate_upload", playable=True,
        )

    # (로컬 패치 2026-08-14) 우리 배포는 메타데이터 단계부터 봇체크에 걸리는 IP라,
    # 여기서 playable=False로 마감하면 다운로드 단계의 Apify 구제(acquisition-class
    # 폴백)가 영영 발화하지 못한다. 봇체크/지오 거부는 정확히 Apify가 존재하는 이유의
    # 실패 클래스이므로 다운로드 단계로 진행시킨다 — 직접 다운로드가 같은 사유로
    # 거부되면 apify.should_attempt가 잡고, Apify까지 실패하면 그때 정직 실패.
    if reason in (failure.REASON_BOT_CHECK, failure.REASON_GEO_BLOCKED):
        if trace_fn:
            trace_fn("metadata_gated_proceed_for_apify", video_id=video_id, reason=reason)
        return ResolvedTrack(
            artist=artist, title=title, duration_s=None, video_id=video_id,
            spotify_id=None, canonical_id=identity.canonical_id(artist, title, None),
            audio_source="youtube", playable=True,
        )

    # Metadata-only: named track, no audio, no listening claim.
    return ResolvedTrack(
        artist=artist, title=title, duration_s=None, video_id=video_id,
        spotify_id=None, canonical_id=identity.canonical_id(artist, title, None),
        audio_source="youtube_metadata_only", playable=False,
    )


def _alternate_search_gated(search_fn) -> bool:
    """Is the speculative alternate-upload search worth its round trips?

    Never gated when the caller injected its own `search_fn`: the suite's
    fixtures are free and instant, and a real-network observation must not
    silently disable a branch under test.
    """
    if search_fn is not None:
        return False
    try:
        return bool(yta.egress_looks_gated())
    except Exception:  # noqa: BLE001 — a diagnostic must never block resolution
        logger.debug("egress gate check failed", exc_info=True)
        return False


def _find_playable_alternate(
    blocked_video_id: str, title: str, artist: str, *, search_fn, detail_fn, trace_fn=None
) -> tuple[str, dict] | None:
    """Best-scoring *different* upload whose own metadata call succeeds.
    Returns (video_id, metadata) or None. Only the single best candidate is
    probed: each probe is a real ~1.5s network round trip, and walking a whole
    search page of blocked videos would add seconds to a turn that is already
    degrading."""
    # resolve_youtube_candidate calls search_fn(query, max_results=N); wrap it
    # so the search's own transport attempts show up in the trace instead of
    # this whole branch failing invisibly.
    #
    # The blocked video is dropped *before* scoring, not after. It otherwise
    # wins its own search every time — an "<Artist> - Topic" Art Track scores
    # highest by design (exact title + artist + the Topic bonus) — and the
    # runner-up that is actually playable would never be considered.
    def _traced_search(query, **kwargs):
        results = search_fn(query, trace_fn=trace_fn, **kwargs) or []
        return [c for c in results if c.get("video_id") != blocked_video_id]

    try:
        candidate = spotify.resolve_youtube_candidate(title, artist, search_fn=_traced_search)
    except Exception:
        logger.debug("alternate-upload search failed for %r", title, exc_info=True)
        candidate = None
    if not candidate:
        if trace_fn:
            trace_fn("alternate_upload_none", blocked=blocked_video_id)
        return None
    alt_id = candidate.get("video_id")
    if not alt_id or alt_id == blocked_video_id:
        if trace_fn:
            trace_fn("alternate_upload_none", blocked=blocked_video_id)
        return None
    alt_meta, alt_reason = detail_fn(alt_id, trace_fn=trace_fn)
    if not alt_meta or not alt_meta.get("title"):
        if trace_fn:
            trace_fn("alternate_upload_blocked", blocked=blocked_video_id, alternate=alt_id, reason=alt_reason)
        return None
    if trace_fn:
        trace_fn("alternate_upload_used", blocked=blocked_video_id, alternate=alt_id)
    return alt_id, alt_meta


def resolve_from_spotify(
    spotify_track_id: str,
    *,
    native_fn=None,
    oembed_fn=None,
    search_fn=None,
    metadata_fn=None,
    trace_fn=None,
) -> ResolvedTrack:
    """Seams default to None and are bound *here*, not in the signature.

    Binding them as signature defaults captured the function objects at import
    time, so patching `music_youtube_audio.search_youtube` (the way every other
    path in this plugin is exercised) silently had no effect and the real
    network call went out from under the test.

    `trace_fn` is threaded only into the *real* transports. Injected seams are
    called with exactly the arguments they were always called with, because
    they are plain test doubles like `lambda q, max_results=5: [...]` that
    would raise TypeError on an unexpected keyword.
    """
    native_fn = native_fn or spotify.fetch_spotify_track_native
    oembed_fn = oembed_fn or spotify.fetch_spotify_oembed
    search_fn = search_fn or (lambda q, **kw: yta.search_youtube(q, trace_fn=trace_fn, **kw))
    metadata_fn = metadata_fn or (lambda vid: yta.fetch_youtube_metadata(vid, trace_fn=trace_fn))

    # Prefer the authoritative Spotify Web API client (exact duration_ms +
    # artist) when the user has completed `hermes auth spotify`; it returns
    # None (never raises) when unauthenticated, so this is a plain fallback,
    # not a hard dependency.
    native = native_fn(spotify_track_id)
    spotify_duration_s = None
    if native and native.get("title"):
        title, artist = native["title"], native.get("artist", "")
        spotify_duration_s = native.get("duration_s")
    else:
        payload = oembed_fn(spotify_track_id)
        if not payload or not payload.get("title"):
            raise TrackResolveError(
                f"spotify metadata unavailable for {spotify_track_id}",
                stage=failure.STAGE_METADATA, reason=failure.REASON_NO_METADATA,
            )
        title, artist = spotify.parse_title_artist(payload["title"])
        if not title:
            raise TrackResolveError(
                "spotify oembed returned no usable title",
                stage=failure.STAGE_METADATA, reason=failure.REASON_NO_METADATA,
            )

    candidate = spotify.resolve_youtube_candidate(title, artist, search_fn=search_fn)

    # Cross-source identity gate. `resolve_youtube_candidate` picks by text
    # score alone, and text cannot separate a radio edit from an extended mix,
    # a studio master from a live take, or an original from a sped-up upload.
    # `spotify_duration_s` was already being fetched here and then discarded —
    # it is the one numeric, source-independent signal available, so it is the
    # one that gets to veto. A candidate we cannot confirm is dropped, and the
    # turn degrades to the metadata-only branch below: <AGENT_NAME> knows the song and
    # says it could not confirm the audio, instead of analysing a remix and
    # describing it as the track the user linked.
    if candidate and candidate.get("video_id"):
        verdict = cross_identity.verify(
            spotify_title=title, spotify_artist=artist,
            spotify_duration_s=spotify_duration_s,
            candidate_title=candidate.get("title") or "",
            candidate_channel=candidate.get("channel") or "",
            candidate_duration_s=candidate.get("duration_s"),
        )
        if trace_fn:
            trace_fn(
                "spotify_candidate_identity", spotify_id=spotify_track_id,
                video_id=candidate.get("video_id"), **verdict.as_dict(),
            )
        if not verdict.usable:
            candidate = None

    if not candidate or not candidate.get("video_id"):
        # Spotify named the song; only the *audio* is missing. Raising here
        # collapsed that into `track_unresolved`, which tells the model it has
        # no idea what the song is — so <AGENT_NAME> would answer "링크가 안 열려" about a
        # track whose artist and title we are holding. This is rung 4 of the
        # YouTube ladder, reached from the Spotify side: named, not playable,
        # no listening claim permitted.
        if trace_fn:
            trace_fn(
                "spotify_no_youtube_match", spotify_id=spotify_track_id,
                stage=failure.STAGE_AUDIO_RESOLVE, reason=failure.REASON_NOT_FOUND,
            )
        return ResolvedTrack(
            artist=artist, title=title, duration_s=spotify_duration_s, video_id="",
            spotify_id=spotify_track_id,
            canonical_id=identity.canonical_id(artist, title, spotify_duration_s),
            audio_source="spotify_metadata_only", playable=False,
        )

    video_id = candidate["video_id"]
    meta = metadata_fn(video_id) or {}
    # Spotify's own duration is authoritative when we have it (exact
    # duration_ms from the Web API); otherwise fall back to whatever the
    # matched YouTube video reports.
    duration_s = spotify_duration_s or meta.get("duration_s") or candidate.get("duration_s")
    # Spotify is the authoritative source for artist name when it parsed one;
    # otherwise fall back to whatever the matched YouTube video reports.
    resolved_artist = artist or meta.get("artist") or candidate.get("channel") or ""
    resolved_title = title or meta.get("title") or candidate.get("title") or ""

    canonical_id = identity.canonical_id(resolved_artist, resolved_title, duration_s)
    return ResolvedTrack(
        artist=resolved_artist, title=resolved_title, duration_s=duration_s, video_id=video_id,
        spotify_id=spotify_track_id, canonical_id=canonical_id, audio_source="spotify_resolved_to_youtube",
    )
