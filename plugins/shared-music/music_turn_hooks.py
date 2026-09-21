"""pre_llm_call / post_llm_call turn hooks for the shared-music plugin.

Extracted out of `__init__.py` (which was carrying ~800 lines of business
logic behind a ~20-line `register(ctx)`) so `__init__.py` can stay a thin
wiring module. Nothing here changes behavior — this is the same code that
used to live at module scope in `__init__.py`.
"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

import music_cache
import music_capability as capability
import music_turn_outcome as outcome
import music_failure as failure
import music_link
import music_intent
import music_obsidian
import music_perception as mp
import music_prompt
import music_resolve
import music_retrieval
import music_session
import music_weekly
import music_window_retrieval as mwr
import music_youtube_audio as yta
import music_dsp as dsp

# Reliability telemetry is strictly optional and fail-open: when the
# functionality-registry plugin is absent (or broken) music keeps working.
try:
    from functionality_registry_telemetry import (
        safe_record_failure as _reliability_fail,
        safe_record_success as _reliability_ok,
    )
except Exception:  # noqa: BLE001 — any import problem degrades to no-op
    def _reliability_ok(*_a, **_k):
        pass

    def _reliability_fail(*_a, **_k):
        pass
import shared_music_trace as trace_mod

# Isolation classes live in one place — plugins/_shared/hermes_traffic.py —
# so a new non-user execution context (this is how "cli" was missed) is a
# one-line change there rather than an edit to every plugin that learns.
from hermes_traffic import (
    NON_USER_TRAFFIC_PLATFORMS as _ISOLATED_PLATFORMS,
)
import hermes_media_membership as membership

# Shared, provenance-first archive routing — see
# plugins/_shared/hermes_media_route.py. This plugin's own trigger used to be
# a closed list of Korean listening phrases, which meant an unambiguous music
# share with no phrase ("<link>" alone, a "- Topic" Art Track) never entered
# the music flow at all, while youtube-archive happily filed it as a video.
# The router widens this gate without loosening it: the phrase list still
# fires exactly as before, and metadata can now speak when the user didn't.
# The call itself lives in music_intent.route_share so it stays importable
# without this module's vendored numpy/yt-dlp stack.
import hermes_media_route as _media_route

from hermes_media_archivist import Archivist as _Archivist

logger = logging.getLogger(__name__)

# Media tools whose invocation is itself evidence that this turn is still about
# the track — a one-word "응" after a deep dive is a member, and no lexical
# signal in that word could have said so.
_MEDIA_TOOL_NAMES = frozenset({"music_deep_dive"})


def _media_tool_names(kwargs: dict) -> tuple[str, ...]:
    raw = kwargs.get("tool_names") or kwargs.get("tools_used") or ()
    if isinstance(raw, str):
        raw = [raw]
    return tuple(n for n in raw if n in _MEDIA_TOOL_NAMES)


def _turn_media_ids(user_message: str) -> tuple[str, ...]:
    """Canonical ids for links in this turn, via the plugin's own detector.

    Resolution is deliberately not attempted here: `_resolve_track` costs a
    network round trip, and for the membership question "is this a *different*
    item" the raw link identity is enough. A same-track resend is normalised by
    `music_link` to the same id, which is what makes the same-media check work
    without a resolve.
    """
    link = music_link.detect_link(user_message or "")
    if not link:
        return ()
    return (f"{link.source}:{link.id}",)


#: Archive identity this plugin claims in the router's ownership map.
ARCHIVE_OWNER = _media_route.OWNER_MUSIC

_archivist: "_Archivist | None" = None


def _get_archivist() -> "_Archivist":
    global _archivist
    if _archivist is None:
        _archivist = _Archivist()
    return _archivist


_OPINION_RE = re.compile(r"좋|별로|최고|구려|아쉽|맘에|취향|괜찮|별루|짱|지린|노잼|재밌|지루|굳|별론")
_MIN_RECALL_TOKENS = 2

_store: music_session.SessionStore | None = None


def _data_root() -> Path:
    return Path(os.environ.get("HERMES_DATA") or "/opt/data")


def _wiki_root() -> Path:
    wiki_env = os.environ.get("WIKI_PATH") or os.environ.get("OBSIDIAN_VAULT_PATH")
    if wiki_env:
        wp = Path(wiki_env)
        if (wp / "media").is_dir() or wp.is_dir():
            return wp / "media"
    vaults = _data_root() / "vaults"
    if vaults.is_dir():
        try:
            for v in vaults.iterdir():
                if v.is_dir() and (v / "media").is_dir():
                    return v / "media"
        except OSError:
            pass
    return _data_root() / "wiki" / "media"


def _get_store() -> music_session.SessionStore:
    global _store
    if _store is None:
        _store = music_session.SessionStore(_wiki_root())
    return _store


def _trace(event: str, **fields) -> None:
    trace_mod.log_event(_data_root(), event, **fields)


def _is_internal_or_isolated(platform: str, session_id: str) -> bool:
    return platform in _ISOLATED_PLATFORMS or not session_id


def _finalize_session(store: music_session.SessionStore, conversation_id: str, *, reason: str) -> None:
    session = store.finalize(conversation_id)
    if not session:
        return
    wiki_root = _wiki_root()
    cache = music_cache.load(wiki_root, session.canonical_id)
    if not cache:
        _trace("archive_session_finalized_no_cache", canonical_id=session.canonical_id, reason=reason)
        return

    is_first_session = cache.get("discussion_count", 0) == 0
    week = music_weekly.week_key(session.started_at)
    already_this_week = music_weekly.has_entry_this_week(wiki_root, session.started_at, session.canonical_id)

    music_obsidian.upsert_track_note(
        wiki_root=wiki_root, cache=cache, session=session, is_first_session=is_first_session, week=week,
    )
    music_cache.touch_discussion(wiki_root, session.canonical_id)
    if session.artist:
        music_obsidian.maybe_upsert_artist_note(wiki_root, session.artist)

    exchange_lines = [f"{'<AGENT_NAME>' if ex.get('speaker') == 'agent' else '<YOUR_NAME>'}: {ex.get('text', '')}" for ex in session.exchange_log]

    if not already_this_week and session.source_url:
        # the link itself is already shown on its own "링크:" line above —
        # strip it out of the quoted trigger message so it isn't repeated.
        user_first = re.sub(r"https?://\S+", "", session.trigger_excerpt or "").strip() or None
        music_weekly.upsert_first_listen(
            wiki_root=wiki_root, ts=session.started_at, canonical_id=session.canonical_id,
            artist=session.artist, title=session.title, source_url=session.source_url,
            agent_first=session.initial_impression or "(기록 없음)", user_first=user_first,
        )
        # first two exchange entries are the trigger message + first impression,
        # already shown in "처음 들었을 때" — the rest is genuinely new content.
        music_weekly.append_conversation_highlights(
            wiki_root=wiki_root, ts=session.started_at, canonical_id=session.canonical_id,
            lines=exchange_lines[2:] if len(exchange_lines) > 2 else ["(짧은 감상만 오간 대화)"],
        )
    elif already_this_week:
        prose = "\n".join(exchange_lines) if exchange_lines else "(추가 대화 없음)"
        music_weekly.append_later_conversation(
            wiki_root=wiki_root, ts=session.started_at, canonical_id=session.canonical_id, prose=prose,
        )

    music_weekly.update_favorite_moments(
        wiki_root=wiki_root, ts=session.started_at, canonical_id=session.canonical_id,
        user_moments=session.user_opinions, agent_moments=session.agent_opinions,
    )

    _trace(
        "archive_session_created", canonical_id=session.canonical_id, reason=reason,
        turns=session.turn_count, week=week,
    )


def _sweep_stale(store: music_session.SessionStore) -> None:
    """Finalize every aged-out session, not just this conversation's.

    The incumbent `_maybe_expire_stale` only inspected the conversation that
    was currently speaking. A session belonging to any *other* conversation —
    including one orphaned by a container restart, which is the common case
    here since nothing rehydrates in-memory state on boot — was therefore never
    reconsidered and sat in `active/` indefinitely, ready to absorb whatever
    that conversation said next, hours or days later.
    """
    for conversation_id, reason in store.stale_conversation_ids():
        _finalize_session(store, conversation_id, reason=reason)


def _acquire_audio(resolved: music_resolve.ResolvedTrack, turn: outcome.TurnTrace):
    """Obtain playable audio through the *shared* acquisition ladder.

    This used to be a single `yta.resolve_audio` call, which is the reason the
    failure production actually produces was unrecoverable: the canonical video
    passed its metadata check and then bot_checked at download, with no tier
    behind it. `MusicFetchLadder` — cache -> direct -> alternate_source ->
    proxy -> alternate_route, with the route-health breaker, in-flight dedup and
    artifact validation — already existed and was wired only into the dormant
    realtime path. Consuming it here is the seam, not a second downloader.

    Falls back to the direct call when the ladder cannot be constructed (a
    deployment without `_shared/hermes_media_fetch`), so this is strictly
    additive.
    """
    data_root = _data_root()

    # Tier 1-3: cache -> direct -> proxy. This stays `resolve_audio` rather than
    # becoming a ladder route, because it is already exactly those three tiers
    # and it is the seam the whole existing suite injects at.
    with turn.stage(outcome.STAGE_DOWNLOAD):
        try:
            path = yta.resolve_audio(data_root, resolved.video_id, trace_fn=_trace)
            turn.record_backend("direct", ok=True)
            return path
        except yta.AudioResolveError as exc:
            first_error = exc
            turn.record_backend(
                "direct", ok=False,
                code=failure.backend_failure_code("direct", getattr(exc, "reason", None)),
            )

    # Tier 4: Apify actor acquisition, and *only* when YouTube refused us.
    #
    # This runs an actor on the deployment's existing Apify account, whose
    # residential tier is not our address and so is not bot-checked. Measured
    # 2026-08-11: all three gated fixtures — Art Track, label MV, and a Blender
    # short — came back in full this way, after failing every yt-dlp
    # player_client and both proxynet proxies.
    #
    # Guarded rather than chained, because it is expensive in a way the other
    # rungs are not: ~$0.047 per track against a $5.00/month account cap, and
    # 38-164s wall clock against direct's 1.7-2.5s. `should_attempt` limits it
    # to bot-check-class reasons — a private or DRM'd video is refused just as
    # hard from Apify's address, so paying to be told that twice is waste.
    # `music_apify_audio` additionally allows one run per video_id per process.
    #
    # Every failure here is swallowed into "carry on down the ladder". A
    # billing cap, an expired token or a wedged actor must cost this turn its
    # audio, never the turn itself.
    apify_reason = getattr(first_error, "reason", None)
    try:
        import music_apify_audio as apify
    except Exception:  # noqa: BLE001 — deployment without the module
        apify = None
    if apify is not None and apify.should_attempt(apify_reason):
        with turn.stage(outcome.STAGE_DOWNLOAD):
            try:
                artifact = apify.acquire(
                    resolved.video_id,
                    yta._audio_cache_dir(data_root) / f"{resolved.video_id}.wav",
                    trace_fn=_trace,
                )
                turn.record_backend(apify.PROVENANCE, ok=True)
                turn.selected_backend = apify.PROVENANCE
                _prov = artifact.provenance_dict()
                _prov.pop("video_id", None)  # (로컬 패치 2026-08-14) 명시 kwarg와 중복 → TypeError
                _trace(
                    "audio_candidate_found", video_id=resolved.video_id,
                    route=apify.PROVENANCE, **_prov,
                )
                return artifact.path
            except Exception as exc:  # noqa: BLE001 — never fatal, see above
                logger.debug("apify acquisition failed for %s", resolved.video_id, exc_info=True)
                turn.record_backend(
                    apify.PROVENANCE, ok=False, code=failure.BACKEND_REMOTE_FAILED,
                )
                _trace(
                    "apify_acquisition_failed", video_id=resolved.video_id,
                    error=type(exc).__name__,
                )

    # Tiers 5-6: the two rungs the batch path never had — a *different public
    # upload* of the same recording, and a *second egress*. Both already exist,
    # tested, in MusicFetchLadder; only the dormant realtime path was using
    # them. The ladder is deliberately restricted to these two routes so the
    # three above are not re-attempted.
    try:
        import music_media_fetch as mfetch
        import music_realtime

        ladder = music_realtime.build_default_ladder(data_root, trace_fn=_trace)
        ladder.ladder = (mfetch.ROUTE_ALTERNATE_SOURCE, mfetch.ROUTE_ALTERNATE_ROUTE)
        source = mfetch.music_source(
            resolved.video_id,
            media_id_=resolved.canonical_id,
            reference=music_link.youtube_canonical_url(resolved.video_id),
        )
    except Exception:
        logger.debug("shared acquisition ladder unavailable; direct transport was final", exc_info=True)
        raise first_error

    with turn.stage(outcome.STAGE_SEARCH):
        result = ladder.fetch(source, expected_duration_s=resolved.duration_s)

    for route in result.attempts:
        turn.record_backend(
            route, ok=(result.ok and route == result.route),
            code=None if result.ok else failure.backend_failure_code(route, result.reason),
        )
    if not result.ok:
        # Report the *first* failure's reason: it describes the track the user
        # actually linked, which is what the reply is about. The alternate
        # tiers failing is why we have nothing, not what went wrong with it.
        raise first_error
    turn.selected_backend = result.route
    _trace("audio_candidate_found", video_id=resolved.video_id, route=result.route)
    return Path(result.local_ref)


def _analyze_and_cache(
    resolved: music_resolve.ResolvedTrack, wiki_root: Path, turn: outcome.TurnTrace | None = None
) -> dict | None:
    turn = turn or outcome.TurnTrace()
    cache = music_cache.load(wiki_root, resolved.canonical_id)
    if cache and music_cache.is_current(cache):
        _trace("perception_cache_hit", canonical_id=resolved.canonical_id)
        turn.record_backend("cache", ok=True)
        return cache
    _trace("perception_cache_miss", canonical_id=resolved.canonical_id)

    if resolved.duration_s and resolved.duration_s > yta.MAX_DURATION_S:
        _trace("track_too_long", canonical_id=resolved.canonical_id, duration_s=resolved.duration_s)
        turn.reason, turn.code = failure.REASON_TOO_LONG, failure.CODE_OVER_LENGTH
        return None

    if not resolved.playable:
        # Identity came from oEmbed precisely because the player API refused
        # this video — the download would fail the same way, so don't spend
        # the timeout re-proving it. Metadata-only mode from here.
        _trace("audio_skipped_unplayable", canonical_id=resolved.canonical_id, video_id=resolved.video_id)
        turn.reason = failure.REASON_NOT_FOUND
        turn.code = failure.BACKEND_METADATA_ONLY
        return None

    data_root = _data_root()
    cached_audio_before_acquire = yta.cached_audio_path(data_root, resolved.video_id) is not None
    try:
        audio_path = _acquire_audio(resolved, turn)
    except yta.AudioResolveError as exc:
        reason = getattr(exc, "reason", failure.REASON_UNKNOWN)
        turn.reason = reason
        turn.code = failure.public_failure_code(reason)
        _trace(
            "audio_resolve_failed", video_id=resolved.video_id,
            stage=getattr(exc, "stage", failure.STAGE_DOWNLOAD),
            reason=reason, code=turn.code,
            auth_capability=capability.youtube_cookie_status()["status"],
        )
        # yt-dlp acquisition is the fragile required dependency — record on
        # the dependency node so the feature degrades via projection.
        _reliability_fail("dep_ytdlp", exc=exc, note="audio_resolve_failed")
        return None
    _trace("audio_acquisition_succeeded", video_id=resolved.video_id, backend=turn.selected_backend)

    # Audio is a processing input, not durable storage. Existing cache hits are
    # retained; anything acquired for this turn is removed after DSP.
    audio_was_cached = cached_audio_before_acquire
    try:
        with turn.stage(outcome.STAGE_DSP):
            raw = dsp.analyze(str(audio_path))
    except Exception as exc:
        logger.exception("music_dsp.analyze failed for %s", resolved.video_id)
        turn.reason, turn.code = failure.REASON_CODEC_UNSUPPORTED, failure.CODE_DECODER
        _trace("analysis_failed", video_id=resolved.video_id, code=turn.code)
        _reliability_fail("shared_music", exc=exc, note="dsp_analysis_failed")
        if not audio_was_cached:
            audio_path.unlink(missing_ok=True)
        return None
    _trace("decode_succeeded", canonical_id=resolved.canonical_id)
    _trace("htf_analysis_ms", canonical_id=resolved.canonical_id, ms=turn.stages_ms.get(outcome.STAGE_DSP, 0))

    with turn.stage(outcome.STAGE_PACKAGING):
        phases = dsp.detect_phases(raw.energy_1hz)
        events = dsp.find_salient_events(raw.onset_1hz, raw.flux_1hz)
        perception = mp.build_perception(
            title=resolved.title, artist=resolved.artist, raw=raw, phases=phases, events=events
        )
        cache = music_cache.save_new_analysis(
            wiki_root=wiki_root, canonical_id=resolved.canonical_id, artist=resolved.artist, title=resolved.title,
            youtube_video_id=resolved.video_id, spotify_track_id=resolved.spotify_id,
            audio_source=resolved.audio_source, perception=perception, raw=raw,
        )
    if not audio_was_cached:
        audio_path.unlink(missing_ok=True)
    _trace("perception_succeeded", canonical_id=resolved.canonical_id)
    # Success boundary: fresh audio acquired + DSP perception produced +
    # cached — the user actually gets a perception-backed listen this turn.
    # (Cache hits above return early on purpose: they prove nothing about
    # yt-dlp/DSP being alive today.)
    _reliability_ok("shared_music", note="fresh_analysis")
    _reliability_ok("dep_ytdlp")
    return cache


def _resolve_track(link: music_link.DetectedLink) -> music_resolve.ResolvedTrack | None:
    # The event name is kept (it is what the runbook greps for) but it now
    # carries stage+reason — the old line was identical for a missing
    # dependency, a private video and a datacenter-IP bot check.
    try:
        if link.source == "youtube":
            return music_resolve.resolve_from_youtube(link.id, trace_fn=_trace)
        # The Spotify branch used to be called without a trace_fn, so none of
        # the yt-dlp calls it makes (search + metadata for the matched video)
        # emitted `ytdlp_attempt_*`. The one Spotify failure in production
        # (2026-08-08, track 0NGFAcYQVHCIdQea2qSs1I) is undiagnosable from the
        # trace for exactly that reason.
        return music_resolve.resolve_from_spotify(link.id, trace_fn=_trace)
    except music_resolve.TrackResolveError as exc:
        _trace(
            "track_resolved_failed", source=link.source, id=link.id,
            stage=getattr(exc, "stage", failure.STAGE_METADATA),
            reason=getattr(exc, "reason", failure.REASON_UNKNOWN),
        )
        _reliability_fail("shared_music", exc=exc, note="track_resolve_failed")
        return None
    except Exception as exc:
        logger.exception("unexpected error resolving %s link %s", link.source, link.id)
        _trace(
            "track_resolved_failed", source=link.source, id=link.id,
            stage=failure.STAGE_METADATA, reason=failure.classify_error(exc),
        )
        return None


# --- Session A router integration contract ----------------------------------
# Session A owns provenance-first URL classification. This plugin does NOT run a
# competing classifier; it accepts A's verdict when one is supplied and keeps
# its own conservative gate only as the legacy/no-router fallback.
#
#   A says kind=music / music_video   -> shared-music attempts a real listen,
#                                        regardless of the legacy intent gate
#   A says any other kind             -> shared-music does not take the link,
#                                        even if the legacy gate would have
#   A says nothing                    -> legacy gate decides, exactly as before
#
# The verdict arrives as a `media_route` kwarg (a bare kind string, or a mapping
# with a "kind"). Hooks already receive **kwargs, so A can start passing it
# without any signature change here.
MUSIC_ROUTE_KINDS = frozenset({"music", "music_video"})


def _route_kind(media_route) -> str | None:
    if not media_route:
        return None
    if isinstance(media_route, str):
        return media_route.strip().lower() or None
    if isinstance(media_route, dict):
        kind = media_route.get("kind") or media_route.get("category")
        return str(kind).strip().lower() if kind else None
    return None


def should_enter_music_flow(link, user_message: str, media_route=None) -> bool:
    """Whether this turn enters the shared-music listening pipeline."""
    if link is None:
        return False
    kind = _route_kind(media_route)
    if kind is not None:
        # A routed this URL. Its verdict is authoritative in both directions —
        # taking a link A classified as video.analysis is precisely the
        # category-theft bug this contract exists to prevent.
        return kind in MUSIC_ROUTE_KINDS
    return bool(
        link.source == "spotify" or link.is_music_host or music_intent.has_music_intent_phrase(user_message)
    )


def _emit_turn_outcome(conversation_id: str, turn: outcome.TurnTrace) -> None:
    """One line per music turn saying what actually happened. `LISTENED` is the
    only value that means audio was heard; nothing else may be read as one."""
    _trace("music_turn_outcome", conversation_id=conversation_id, **turn.as_fields())


def on_pre_llm_call(
    *, user_message: str = "", session_id: str = "", task_id: str = "", platform: str = "",
    media_route=None, **_kwargs,
) -> dict | None:
    if _is_internal_or_isolated(platform, session_id):
        return None

    store = _get_store()
    conversation_id = session_id
    wiki_root = _wiki_root()

    _sweep_stale(store)
    active = store.get_active(conversation_id)

    link = music_link.detect_link(user_message)

    # Provenance-first trigger. The link is the *transport*; what decides
    # whether this is music is A's router, over user intent -> invoking
    # feature -> active session -> media metadata, in that order. A YouTube
    # link with no music evidence anywhere routes to `unknown` and this
    # plugin declines it — it does not fall back to "probably music" any more
    # than youtube-archive may fall back to "probably video".
    #
    # A's verdict can arrive two ways and they are the same authority, not
    # two: pre-computed upstream and handed in as the `media_route` kwarg, or
    # computed here by calling the router directly. The kwarg wins when it is
    # present because that decision was made earlier in the same turn with the
    # dispatcher's context; otherwise the router runs in-process. D never
    # decides *whether* this is music — it only decides, later, whether the
    # audio was actually heard.
    archivist = _get_archivist()
    try:
        archivist.on_user_message(conversation_id, user_message)
    except Exception:
        logger.debug("music answer attribution failed (non-fatal)", exc_info=True)

    is_trigger, decision = music_intent.route_share(
        user_message,
        link_source=getattr(link, "source", None),
        link_id=getattr(link, "id", None),
        invoking_feature=_kwargs.get("invoking_feature"),
        active_session_kind=(_media_route.CATEGORY_MUSIC if active else None),
        archivist=archivist,
        session_id=conversation_id,
    )
    _route_source = "router"
    _upstream_kind = _route_kind(media_route)
    if _upstream_kind is not None:
        is_trigger = _upstream_kind in MUSIC_ROUTE_KINDS
        _route_source = "media_route_kwarg"
    _trace(
        "music_trigger", conversation_id=conversation_id, is_trigger=is_trigger,
        has_link=bool(link), route_source=_route_source,
        upstream_route=_upstream_kind or "",
        **(decision.as_log_fields() if decision is not None else {}),
    )
    if decision is not None:
        logger.info(
            "shared-music route link=%s trigger=%s source=%s %s",
            getattr(link, "id", None), is_trigger, _route_source,
            decision.as_log_fields(),
        )

    if is_trigger:
        _trace("music_intent_accepted", conversation_id=conversation_id,
               source=link.source, route=_route_kind(media_route) or "legacy_gate")
        turn = outcome.TurnTrace()
        with turn.stage(outcome.STAGE_RESOLVE):
            resolved = _resolve_track(link)
        if resolved is None:
            # Nothing resolved at all — we don't even know the song title, so
            # this is a different honesty problem from "known song, no audio".
            turn.outcome = outcome.OUTCOME_FAILED
            turn.code = failure.CODE_NOT_FOUND
            _emit_turn_outcome(conversation_id, turn)
            return {"context": music_prompt.build_failure_context("track_unresolved")}
        _trace("metadata_resolved", canonical_id=resolved.canonical_id, source=link.source,
               audio_source=resolved.audio_source, playable=resolved.playable)
        _trace("track_resolved", canonical_id=resolved.canonical_id, source=link.source)

        if active and active.canonical_id == resolved.canonical_id:
            return None  # same track resent in the same conversation -> no-op

        if active:
            _finalize_session(store, conversation_id, reason="new_track")

        cache = _analyze_and_cache(resolved, wiki_root, turn)
        if cache is None:
            reason = "too_long" if resolved.duration_s and resolved.duration_s > yta.MAX_DURATION_S else "audio_unavailable"
            # Metadata-only degraded mode: resolution succeeded, so the track
            # is named even though no audio was heard. This is emphatically not
            # "I couldn't find the song" — the name is right there.
            turn.outcome = outcome.OUTCOME_METADATA_ONLY
            _emit_turn_outcome(conversation_id, turn)
            return {
                "context": music_prompt.build_failure_context(
                    reason, artist=resolved.artist, title=resolved.title
                )
            }
        turn.outcome = outcome.OUTCOME_LISTENED
        _emit_turn_outcome(conversation_id, turn)

        store.start(
            conversation_id=conversation_id, canonical_id=resolved.canonical_id, title=resolved.title,
            artist=resolved.artist, video_id=resolved.video_id, spotify_id=resolved.spotify_id,
            platform=platform, trigger_excerpt=user_message[:300], source_url=link.url,
            route_decision=decision,
        )
        perception = mp.MusicPerceptionObject.from_dict(cache["perception"])
        return {"context": music_prompt.build_first_listen_context(perception)}

    if active:
        cache = music_cache.load(wiki_root, active.canonical_id)
        if not cache:
            return None
        window = mwr.resolve_window(cache, user_message, last_focus_section_idx=active.last_focus_section_idx)
        if window:
            _trace("retrieved_window", conversation_id=conversation_id, window=window.get("window"))
            if window.get("section_idx") is not None:
                active.last_focus_section_idx = window["section_idx"]
                active.discussed_topics.append(f"window:{window['window']}")
                store.save(active)
            session_summary = {
                "recent_topics": active.discussed_topics[-5:],
                "recent_user_opinions": active.user_opinions[-3:],
            }
            return {"context": music_prompt.build_followup_context(window, session_summary)}
        return None

    tokens = re.findall(r"[a-zA-Z0-9]+|[가-힣]+", user_message or "")
    if len([t for t in tokens if len(t) > 1]) < _MIN_RECALL_TOKENS:
        return None
    try:
        weekly_tracks = music_retrieval.search_weekly_summary(user_message, wiki_root)
        if weekly_tracks:
            return {"context": music_retrieval.format_weekly_summary_block(weekly_tracks)}

        artist_result = music_retrieval.search_artist(user_message, wiki_root)
        if artist_result:
            return {"context": music_retrieval.format_artist_block(artist_result)}

        results = music_retrieval.search(user_message, wiki_root)
    except Exception:
        logger.debug("shared-music recall search failed (non-fatal)", exc_info=True)
        return None
    if not results:
        return None
    return {"context": music_retrieval.format_context_block(results)}


def on_post_llm_call(
    *, session_id: str = "", user_message: str = "", assistant_response: str = "", platform: str = "", **_kwargs,
) -> None:
    if _is_internal_or_isolated(platform, session_id):
        return
    store = _get_store()
    conversation_id = session_id
    active = store.get_active(conversation_id)
    if not active:
        return

    is_first_turn = not active.initial_impression
    reply = (assistant_response or "").strip()

    # The membership gate. Before this existed, reaching here at all meant the
    # turn was transcribed — which is how "루나로 수동으로 바꿔줘", model-routing
    # discussion and gateway-restart requests were published as quoted dialogue
    # about a song.
    #
    # Every turn is evaluated, the trigger turn included: it carries the link
    # itself, so it matches on media identity and is admitted on evidence
    # rather than by exemption — and a first turn that is *already* an ending
    # ("이 노래 얘기 그만" as the very next message) still closes the session,
    # which an exemption would have swallowed.
    turn = membership.Turn(
        text=user_message or "",
        assistant_text=reply,
        at_s=time.time(),
        media_ids=_turn_media_ids(user_message),
        has_link=bool(music_link.detect_link(user_message or "")),
        tool_names=_media_tool_names(_kwargs),
    )
    decision = membership.evaluate_turn(turn, active.view())
    store.record_decision(active, decision, excerpt=user_message or "")
    active.phase = decision.next_phase
    active.drift_count = decision.drift_count
    _trace(
        "membership_decision", conversation_id=conversation_id,
        canonical_id=active.canonical_id, include=decision.include,
        reason=decision.reason, phase=decision.next_phase,
    )
    if not decision.include:
        if is_first_turn and reply:
            # <AGENT_NAME>'s own first listening impression is the one piece of
            # a rejected first turn that is still genuinely about the track —
            # it was produced from the perception object, not from whatever
            # the user's message turned out to be about.
            active.initial_impression = reply
        # Persist the phase/reason first, then finalize — the archive must
        # never be written from a session whose exclusion was lost.
        store.touch(active, counts_as_turn=False)
        if decision.finalizes:
            _finalize_session(
                store, conversation_id,
                reason=decision.finalize_reason or decision.reason,
            )
        return

    if is_first_turn:
        active.phase = music_session.PHASE_ACTIVE
        active.initial_impression = reply
        if reply:
            active.agent_opinions.append(reply)
        active.exchange_log.append({"speaker": "user", "text": active.trigger_excerpt})
        active.exchange_log.append({"speaker": "agent", "text": reply})
    else:
        active.exchange_log.append({"speaker": "user", "text": user_message})
        active.exchange_log.append({"speaker": "agent", "text": reply})
        if user_message and _OPINION_RE.search(user_message):
            active.user_opinions.append(user_message.strip())
        if reply and _OPINION_RE.search(reply):
            active.agent_opinions.append(reply)

    store.touch(active)
    # No explicit-termination check here any more: an included turn is by
    # construction one the membership machine did *not* read as an ending, and
    # every ending it does read — explicit phrase, control-plane command, dev
    # command, new media, drift, idle, cap — finalizes on the exclusion path
    # above. Two independent termination rules is how the incumbent ended up
    # with one that only fired on a media-anchored phrase.
