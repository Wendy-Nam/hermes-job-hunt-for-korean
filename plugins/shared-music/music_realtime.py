"""Realtime Music Companion — the layer that ties playhead, fetch and ears together.

Reading order: `music_playhead` (where the listener is), `music_media_fetch`
(how bytes arrive), then this (what gets analysed, when).

The single most important line in this file is in `ensure_audio`:

    if not result.ok:
        return None

Everything downstream — Fast Ear, Deep Ear, the artifact cache, the
conversational layer — is reachable only through a `MediaFetchResult` that
passed `require_ok()`. There is no path that produces an `AnalysisArtifact`
from a failed fetch, which is the property `test_music_realtime` pins down as
the no-fake-listening invariant. `RealtimeMusicCompanion.heard_anything` stays
False when acquisition fails, so the conversational layer has a truthful thing
to check instead of inferring from the absence of an exception.

Fast Ear vs Deep Ear:

  **Fast Ear** is cheap and runs on every ahead-buffer window. It reuses
  `music_dsp`'s existing 1Hz feature series — energy, spectral flux,
  brightness, onset envelope — and turns changes in them into *candidates*:
  energy change, onset/rhythm change, spectral/timbral change, structural
  boundary, section transition. It emits `TimedObservation`s. It never decides
  what anything means.

  **Deep Ear** is expensive (an isolated-venv subprocess, ~seconds, bounded by
  `music_deep_ear.MAX_WINDOW_S`) and runs only where Fast Ear says something is
  worth a closer look. Selection is by salience, with a floor, a minimum
  spacing, and a hard per-track budget — "do not Deep-Ear every second" is a
  scheduling property, not a hope. Results become `AnalysisArtifact`s.

The DSP entry points are injected (`fast_ear_fn`, `deep_ear_fn`) so this
module's scheduling logic is testable without numpy, scipy, soundfile or the
Deep Ear venv — none of which exist in the plain host interpreter the
shared-music suites run under.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

import music_media_fetch as mfetch
import music_playhead as playhead_mod
import hermes_media_runtime as media_runtime

from hermes_media import (
    CLOCK_MEDIA,
    KIND_MUSIC,
    MODALITY_AUDIO,
    AnalysisArtifact,
    MediaPosition,
    MediaRange,
    MediaSession,
    Provenance,
    SpoilerBoundary,
    TimedObservation,
    cache_identity,
    new_id,
)
from hermes_media_fetch import MediaFetchResult, MediaSource

logger = logging.getLogger(__name__)

FAST_EAR_PRODUCER = "shared-music/fast-ear"
DEEP_EAR_PRODUCER = "shared-music/deep-ear"
FAST_EAR_VERSION = "1.0.0"

# Candidate kinds Fast Ear can raise.
CANDIDATE_ENERGY = "energy_change"
CANDIDATE_ONSET = "onset_change"
CANDIDATE_SPECTRAL = "spectral_change"
CANDIDATE_BOUNDARY = "structural_boundary"
CANDIDATE_SECTION = "section_transition"
CANDIDATE_KINDS = (
    CANDIDATE_ENERGY, CANDIDATE_ONSET, CANDIDATE_SPECTRAL,
    CANDIDATE_BOUNDARY, CANDIDATE_SECTION,
)

# Deep Ear selection policy.
DEEP_EAR_SALIENCE_FLOOR = 0.35   # below this, nothing is worth a subprocess
DEEP_EAR_MIN_SPACING_S = 20.0    # two peaks 3s apart describe one event
DEEP_EAR_BUDGET_PER_TRACK = 6    # hard cap; a 5-minute track is not 300 passes


@dataclass(frozen=True)
class FastEarCandidate:
    """A cheap signal that *something* changed. Not an interpretation."""

    kind: str
    at_s: float
    salience: float          # 0..1
    detail: str = ""

    def __post_init__(self) -> None:
        if self.kind not in CANDIDATE_KINDS:
            raise ValueError(f"unknown Fast Ear candidate kind: {self.kind!r}")


def window_cache_key(media_id: str, window: MediaRange, *, producer: str, version: str) -> str:
    """Deterministic identity for one analysis of one window.

    Built from media identity plus the window bounds — never from the fetched
    path or a resolved URL, so the same window analysed after a cache eviction
    and a fresh download produces the same key (contract invariant 8).
    """
    return cache_identity(
        media_id_=media_id,
        artifact_kind="window_analysis",
        producer=producer,
        params={
            "start_ms": window.start.timestamp_ms,
            "end_ms": window.end.timestamp_ms,
            "version": version,
        },
    )


# =============================================================================
# Fast Ear
# =============================================================================

def fast_ear_from_series(
    *,
    start_s: float,
    energy_1hz: Sequence[float],
    flux_1hz: Sequence[float] | None = None,
    brightness_1hz: Sequence[float] | None = None,
    onset_1hz: Sequence[float] | None = None,
) -> tuple[FastEarCandidate, ...]:
    """Turn 1Hz feature series into candidates. Pure arithmetic, no numpy.

    Deliberately plain Python over lists: these series are one per second over
    a 15-second window, so the arrays are ~15 elements and a vectorised path
    would cost more in import time than it saves. It also keeps Fast Ear
    runnable in contexts where the DSP stack is not installed.

    Salience is a normalised step size — how large a jump is relative to the
    window's own spread. Relative, not absolute, because a quiet ambient track
    and a loud mix should both be able to have a "big" change.
    """
    candidates: list[FastEarCandidate] = []
    series = [
        (CANDIDATE_ENERGY, list(energy_1hz), "loudness steps"),
        (CANDIDATE_ONSET, list(onset_1hz or []), "rhythmic density steps"),
        (CANDIDATE_SPECTRAL, list(brightness_1hz or []), "timbre shifts"),
        (CANDIDATE_BOUNDARY, list(flux_1hz or []), "spectral flux peak"),
    ]
    for kind, values, detail in series:
        for at_s, salience in _step_points(values):
            candidates.append(
                FastEarCandidate(
                    kind=kind, at_s=start_s + at_s, salience=salience, detail=detail
                )
            )

    # A structural boundary that coincides with an energy change is a section
    # transition — the one candidate kind that needs two signals to agree, and
    # the one most worth Deep Ear's time.
    energies = {round(c.at_s, 1) for c in candidates if c.kind == CANDIDATE_ENERGY}
    for c in list(candidates):
        if c.kind == CANDIDATE_BOUNDARY and round(c.at_s, 1) in energies:
            candidates.append(
                FastEarCandidate(
                    kind=CANDIDATE_SECTION, at_s=c.at_s,
                    salience=min(1.0, c.salience + 0.2),
                    detail="flux peak coincident with an energy step",
                )
            )
    return tuple(sorted(candidates, key=lambda c: (c.at_s, c.kind)))


def _step_points(values: Sequence[float]) -> list[tuple[float, float]]:
    """(offset_s, salience) for each significant step in a 1Hz series."""
    if len(values) < 3:
        return []
    deltas = [abs(values[i] - values[i - 1]) for i in range(1, len(values))]
    spread = max(values) - min(values)
    if spread <= 1e-9:
        return []
    biggest = max(deltas)
    if biggest <= 1e-9:
        return []
    out: list[tuple[float, float]] = []
    for i, delta in enumerate(deltas):
        salience = delta / spread
        # A step must be both a meaningful fraction of the window's range and
        # comparable to the window's largest step, or every bit of noise in a
        # flat passage becomes a "change".
        if salience >= 0.25 and delta >= 0.5 * biggest:
            out.append((float(i + 1), round(min(1.0, salience), 4)))
    return out


def observations_from_candidates(
    candidates: Sequence[FastEarCandidate], *, media_id: str
) -> tuple[TimedObservation, ...]:
    """Candidates -> canonical `TimedObservation`s on the media clock."""
    prov = Provenance(producer=FAST_EAR_PRODUCER, producer_version=FAST_EAR_VERSION)
    return tuple(
        TimedObservation(
            observation_id=new_id("obs"),
            media_id=media_id,
            modality=MODALITY_AUDIO,
            kind="fast_ear_candidate",
            position=MediaPosition.at_seconds(c.at_s, clock=CLOCK_MEDIA),
            confidence=c.salience,
            payload={"candidate": c.kind, "salience": c.salience, "detail": c.detail},
            provenance=prov,
        )
        for c in candidates
    )


def select_deep_ear_targets(
    candidates: Sequence[FastEarCandidate],
    *,
    already_analysed_s: Sequence[float] = (),
    floor: float = DEEP_EAR_SALIENCE_FLOOR,
    min_spacing_s: float = DEEP_EAR_MIN_SPACING_S,
    budget: int = DEEP_EAR_BUDGET_PER_TRACK,
) -> tuple[FastEarCandidate, ...]:
    """Which candidates justify a Deep Ear pass. Highest salience first.

    Three independent bounds, because any one alone is insufficient: the floor
    stops noise, the spacing stops one event from consuming the whole budget
    across four adjacent seconds, and the budget stops a genuinely eventful
    track from costing thirty subprocesses.
    """
    picked: list[FastEarCandidate] = []
    taken = list(already_analysed_s)
    for c in sorted(candidates, key=lambda x: (-x.salience, x.at_s)):
        if len(picked) >= budget:
            break
        if c.salience < floor:
            break  # sorted, so nothing after this clears the floor either
        if any(abs(c.at_s - t) < min_spacing_s for t in taken):
            continue
        picked.append(c)
        taken.append(c.at_s)
    return tuple(sorted(picked, key=lambda c: c.at_s))


# =============================================================================
# The companion
# =============================================================================

@dataclass
class RealtimeMetrics:
    fast_ear_runs: int = 0
    fast_ear_skipped_cached: int = 0
    deep_ear_runs: int = 0
    deep_ear_skipped_cached: int = 0
    deep_ear_declined: int = 0
    windows_planned: int = 0
    prefetch_hits: int = 0
    position_lookups: int = 0
    position_cache_hits: int = 0
    fast_ear_ms: list = field(default_factory=list)
    deep_ear_ms: list = field(default_factory=list)

    def snapshot(self) -> dict:
        def mean(xs):
            return (sum(xs) / len(xs)) if xs else None

        return {
            "fast_ear_runs": self.fast_ear_runs,
            "fast_ear_skipped_cached": self.fast_ear_skipped_cached,
            "deep_ear_runs": self.deep_ear_runs,
            "deep_ear_skipped_cached": self.deep_ear_skipped_cached,
            "deep_ear_declined": self.deep_ear_declined,
            "windows_planned": self.windows_planned,
            "prefetch_hit_rate": (
                self.prefetch_hits / self.position_lookups if self.position_lookups else None
            ),
            "position_cache_hit_rate": (
                self.position_cache_hits / self.position_lookups
                if self.position_lookups else None
            ),
            "duplicate_fast_ear_rate": (
                self.fast_ear_skipped_cached
                / (self.fast_ear_runs + self.fast_ear_skipped_cached)
                if (self.fast_ear_runs + self.fast_ear_skipped_cached) else None
            ),
            "mean_fast_ear_ms": mean(self.fast_ear_ms),
            "mean_deep_ear_ms": mean(self.deep_ear_ms),
        }


class RealtimeMusicCompanion:
    """One track, one listener, one live analysis state.

    Owns a `MediaSession` (so the spoiler boundary and lifecycle come from the
    contract rather than a music-only invention), a `Playhead`, and a
    `TimedArtifactCache`.
    """

    def __init__(
        self,
        *,
        session: MediaSession,
        source: MediaSource,
        ladder: mfetch.MusicFetchLadder,
        fast_ear_fn: Callable[[str, float, float], tuple[FastEarCandidate, ...]],
        deep_ear_fn: Callable[[str, float, float], dict | None] | None = None,
        duration_s: float | None = None,
        clock_ms: Callable[[], int] | None = None,
        trace_fn: Callable[..., None] | None = None,
    ) -> None:
        self.session = session
        self.source = source
        self.ladder = ladder
        self._fast_ear = fast_ear_fn
        self._deep_ear = deep_ear_fn
        self.duration_s = duration_s
        self._clock_ms = clock_ms or (lambda: 0)
        self._trace = trace_fn or (lambda *a, **k: None)

        self.playhead = playhead_mod.Playhead(session.media_id, duration_s=duration_s)
        self.artifacts = playhead_mod.TimedArtifactCache()
        self.observations: list[TimedObservation] = []
        self.metrics = RealtimeMetrics()

        self._audio: MediaFetchResult | None = None
        self._fetch_failure: MediaFetchResult | None = None
        self._deep_done_s: list[float] = []

    # -- acquisition ----------------------------------------------------------
    @property
    def heard_anything(self) -> bool:
        """True only if validated audio was acquired *and* something analysed.

        The conversational layer asks this before saying anything that implies
        listening. It is deliberately not "did a fetch happen".
        """
        return self._audio is not None and bool(self.observations)

    @property
    def audio(self) -> MediaFetchResult | None:
        return self._audio

    @property
    def last_failure(self) -> MediaFetchResult | None:
        return self._fetch_failure

    def ensure_audio(self, *, upload_ref: str | None = None) -> MediaFetchResult | None:
        """Acquire playable audio once. Returns None when every route failed.

        Returning None rather than raising because a failed acquisition is an
        ordinary, expected outcome that the caller must handle by degrading
        honestly — not an exception to be caught and forgotten.
        """
        if self._audio is not None:
            return self._audio
        result = self.ladder.fetch(
            self.source, expected_duration_s=self.duration_s, upload_ref=upload_ref
        )
        if not result.ok:
            self._fetch_failure = result
            self._trace("realtime_audio_unavailable", media_id=self.session.media_id,
                        stage=result.stage, reason=result.reason)
            return None
        self._audio = result
        if result.duration_s and not self.duration_s:
            self.duration_s = result.duration_s
            self.playhead.duration_s = result.duration_s
        return result

    # -- playhead -------------------------------------------------------------
    def report_position(
        self, position_ms: int, *, wall_ms: int, playing: bool = True,
        rate: float = 1.0, sequence_no: int | None = None, consumed: bool = True,
    ) -> bool:
        """Record where the listener is, and move the session's boundary.

        `consumed=True` moves the user-visible boundary; the prefetch path uses
        `advanced_to(..., consumed=False)` instead, which is why lookahead
        analysis can run far ahead without ever widening what may be revealed.
        """
        accepted = self.playhead.report(
            playhead_mod.PlayheadReport(
                position_ms=position_ms, wall_ms=wall_ms,
                playing=playing, rate=rate, sequence_no=sequence_no,
            )
        )
        if not accepted:
            self._trace("playhead_stale_report_dropped", media_id=self.session.media_id)
            return False
        pos = MediaPosition.at_ms(
            int(position_ms), clock=CLOCK_MEDIA, sequence_no=sequence_no
        )
        self.session = self.session.advanced_to(pos, consumed=consumed)
        return True

    def current_position(self, *, now_wall_ms: int | None = None) -> MediaPosition | None:
        return self.playhead.position(now_wall_ms=now_wall_ms)

    def what_is_happening_now(
        self, *, now_wall_ms: int | None = None, radius_s: float = 20.0
    ) -> tuple[AnalysisArtifact, ...]:
        """Artifacts around the playhead — the immediate-retrieval path.

        Counts a prefetch hit when the answer was already in the cache, which
        is what the ahead-buffer exists to produce.
        """
        pos = self.current_position(now_wall_ms=now_wall_ms)
        self.metrics.position_lookups += 1
        found = self.artifacts.around(pos, radius_s=radius_s)
        if found:
            self.metrics.position_cache_hits += 1
            self.metrics.prefetch_hits += 1
        return found

    def visible_now(self, items) -> tuple:
        """Filter items through the session's boundary, failing closed.

        Delegates to the canonical reveal gate. This used to go through a local
        `music_reveal` seam, because `SpoilerBoundary.visible()` resolved an
        item's anchor from `.range`/`.position` only and a `ReactionCandidate`
        has neither — so a candidate anchored ahead of the listener read as
        unanchored metadata and was revealed. That defect is repaired in the
        contract primitive (`hermes_media.anchor_of`), and the local seam is
        deleted: there is one reveal authority, not two.
        """
        return media_runtime.visible(self.session.spoiler, items)

    # -- the ahead-buffer loop ------------------------------------------------
    def advance(
        self,
        *,
        now_wall_ms: int | None = None,
        window_s: float = playhead_mod.WINDOW_S,
        lookahead_s: float = playhead_mod.LOOKAHEAD_S,
        max_windows: int = playhead_mod.MAX_WINDOWS,
    ) -> tuple[AnalysisArtifact, ...]:
        """One ahead-buffer pass: plan, Fast Ear, selectively Deep Ear.

        No audio means no pass. This is the choke point that makes fake
        listening structurally impossible rather than merely discouraged.
        """
        audio = self.ensure_audio()
        if audio is None:
            return ()

        pos = self.current_position(now_wall_ms=now_wall_ms)
        windows = playhead_mod.plan_ahead_windows(
            pos,
            duration_s=self.duration_s,
            covered=self.artifacts.covered_ranges(producer=FAST_EAR_PRODUCER),
            window_s=window_s,
            lookahead_s=lookahead_s,
            max_windows=max_windows,
        )
        self.metrics.windows_planned += len(windows)

        produced: list[AnalysisArtifact] = []
        for window in windows:
            artifact = self._fast_ear_window(audio.local_ref, window)
            if artifact is not None:
                produced.append(artifact)
            # The ahead-buffer moves the analysis frontier, never the consumed
            # boundary — analysing minute three does not mean the listener
            # reached it.
            self.session = self.session.advanced_to(window.end, consumed=False)
        produced.extend(self._deep_ear_pass(audio.local_ref))
        return tuple(produced)

    def _fast_ear_window(self, audio_path: str, window: MediaRange) -> AnalysisArtifact | None:
        key = window_cache_key(
            self.session.media_id, window,
            producer=FAST_EAR_PRODUCER, version=FAST_EAR_VERSION,
        )
        if self.artifacts.has(key):
            self.metrics.fast_ear_skipped_cached += 1
            return None

        start_s = window.start.timestamp_ms / 1000.0
        end_s = window.end.timestamp_ms / 1000.0
        t0 = self._clock_ms()
        try:
            candidates = self._fast_ear(audio_path, start_s, end_s)
        except Exception:  # noqa: BLE001 — Fast Ear must never take a turn down
            logger.debug("fast ear failed on [%s, %s]", start_s, end_s, exc_info=True)
            self._trace("fast_ear_failed", media_id=self.session.media_id, start_s=start_s)
            return None
        self.metrics.fast_ear_runs += 1
        self.metrics.fast_ear_ms.append(max(0, self._clock_ms() - t0))

        observations = observations_from_candidates(
            candidates, media_id=self.session.media_id
        )
        self.observations.extend(observations)

        artifact = AnalysisArtifact(
            artifact_id=new_id("art"),
            media_id=self.session.media_id,
            artifact_kind="fast_ear_window",
            cache_key=key,
            producer=FAST_EAR_PRODUCER,
            producer_version=FAST_EAR_VERSION,
            range=window,
            source_observation_ids=tuple(o.observation_id for o in observations),
            body_ref=f"fastear://{key}",
            summary={
                "candidates": len(candidates),
                "start_s": start_s,
                "end_s": end_s,
            },
            provenance=Provenance(
                producer=FAST_EAR_PRODUCER, producer_version=FAST_EAR_VERSION
            ),
        )
        self.artifacts.put(artifact)
        return artifact

    def _deep_ear_pass(self, audio_path: str) -> list[AnalysisArtifact]:
        if self._deep_ear is None:
            return []
        candidates = [
            FastEarCandidate(
                kind=o.payload["candidate"],
                at_s=o.position.timestamp_ms / 1000.0,
                salience=float(o.payload.get("salience") or 0.0),
                detail=str(o.payload.get("detail") or ""),
            )
            for o in self.observations
            if o.kind == "fast_ear_candidate" and o.position is not None
        ]
        targets = select_deep_ear_targets(
            candidates,
            already_analysed_s=tuple(self._deep_done_s),
            budget=max(0, DEEP_EAR_BUDGET_PER_TRACK - len(self._deep_done_s)),
        )
        self.metrics.deep_ear_declined += max(0, len(candidates) - len(targets))

        produced: list[AnalysisArtifact] = []
        for target in targets:
            start_s = max(0.0, target.at_s - 5.0)
            end_s = start_s + 15.0
            if self.duration_s:
                end_s = min(end_s, self.duration_s)
            window = MediaRange(
                start=MediaPosition.at_seconds(start_s, clock=CLOCK_MEDIA),
                end=MediaPosition.at_seconds(end_s, clock=CLOCK_MEDIA),
            )
            key = window_cache_key(
                self.session.media_id, window,
                producer=DEEP_EAR_PRODUCER, version=FAST_EAR_VERSION,
            )
            if self.artifacts.has(key):
                self.metrics.deep_ear_skipped_cached += 1
                self._deep_done_s.append(target.at_s)
                continue

            t0 = self._clock_ms()
            try:
                observation = self._deep_ear(audio_path, start_s, end_s)
            except Exception:  # noqa: BLE001 — Fast Ear survives Deep Ear's death
                logger.debug("deep ear failed at %ss", target.at_s, exc_info=True)
                self._trace("deep_ear_failed", media_id=self.session.media_id,
                            start_s=start_s)
                self._deep_done_s.append(target.at_s)
                continue
            self._deep_done_s.append(target.at_s)
            if not observation:
                continue
            self.metrics.deep_ear_runs += 1
            self.metrics.deep_ear_ms.append(max(0, self._clock_ms() - t0))

            artifact = AnalysisArtifact(
                artifact_id=new_id("art"),
                media_id=self.session.media_id,
                artifact_kind="deep_ear_window",
                cache_key=key,
                producer=DEEP_EAR_PRODUCER,
                producer_version=str(observation.get("analyzer_version") or FAST_EAR_VERSION),
                range=window,
                body_ref=f"deepear://{key}",
                summary={"at_s": target.at_s, "triggered_by": target.kind},
                provenance=Provenance(producer=DEEP_EAR_PRODUCER),
            )
            self.artifacts.put(artifact)
            produced.append(artifact)
        return produced

    # -- reporting ------------------------------------------------------------
    def benchmark_snapshot(self) -> dict:
        return {
            "fetch": self.ladder.metrics.snapshot(),
            "realtime": self.metrics.snapshot(),
            "route_health": self.ladder.health.metrics(),
            "artifacts": len(self.artifacts),
            "duplicate_artifact_puts": self.artifacts.duplicate_puts,
            "observations": len(self.observations),
            "heard_anything": self.heard_anything,
        }


# =============================================================================
# Production wiring
# =============================================================================

def build_default_ladder(
    data_root: Path,
    *,
    health_path: Path | None = None,
    trace_fn: Callable[..., None] | None = None,
) -> mfetch.MusicFetchLadder:
    """Wire the ladder to the real `music_youtube_audio` transport.

    Imported here rather than at module scope so that importing
    `music_realtime` in a test context does not drag in yt-dlp's neighbourhood.
    """
    import music_youtube_audio as ya

    data_root = Path(data_root)
    cache_dir = data_root / "audio_cache" / "shared-music"
    health_path = health_path or (data_root / "logs" / "shared-music" / "route_health.json")

    def _alternate_source(source: MediaSource) -> str | None:
        """A different public upload of the same *recording*.

        Uses the same `search_youtube` the Spotify resolver already relies on,
        so this tier introduces no new extractor and no new access path — it
        looks for another *public* upload exactly as a user searching would.

        Every candidate must pass `music_candidate.reject_reason` first. This
        previously returned the first search hit whose id merely differed from
        the blocked one, which is how a live take, a cover or a nightcore edit
        could be downloaded and then described as the song — a confident wrong
        impression, the worst outcome available to this pipeline. A rejected
        candidate is traced with its reason so "no alternate" is never silent.
        """
        import music_candidate as candidates

        title = str(source.metadata.get("title") or "")
        artist = str(source.metadata.get("artist") or "")
        if not title:
            return None
        results = ya.search_youtube(f"{artist} {title}".strip(), max_results=5)
        accepted, rejected = candidates.rank_candidates(
            results, title=title, artist=artist,
            reference_duration_s=source.metadata.get("duration_s"),
            exclude_ids=(source.native_id,), limit=1,
        )
        if trace_fn and rejected:
            trace_fn("alternate_candidates_rejected", media_id=source.media_id, rejected=rejected)
        if not accepted:
            return None
        if trace_fn:
            trace_fn("alternate_candidate_selected", media_id=source.media_id,
                     video_id=accepted[0]["video_id"], score=accepted[0]["score"])
        return accepted[0]["video_id"]

    # The two egress tiers are two *different* candidates from the same pool.
    # This host configures five (`$HERMES_DATA/.scraper-proxy` plus a Webshare
    # key that can mint more), and `skills/shared-utils/proxynet` exposes them
    # via `resolve_proxies(n)` — the API the youtube-content transcript path
    # already uses. The previous wiring took `resolve_proxy()`, which returns a
    # single TTL-cached candidate, so `alternate_route` had nothing to offer and
    # was skipped on every fetch. Measured caveat: a candidate being live does
    # not mean YouTube accepts it — on the gated Art Tracks every candidate
    # answers bot_check, so this widens the ladder for network/geo failures
    # rather than defeating the bot check.
    def _egress(index: int):
        def provider() -> str | None:
            candidates = ya.resolved_proxies(n=2)
            return candidates[index] if len(candidates) > index else None

        return provider

    return mfetch.MusicFetchLadder(
        cache_dir=cache_dir,
        download=lambda vid, out, proxy: ya._run_download(vid, out, proxy),
        proxy_provider=_egress(0),
        alternate_route_provider=_egress(1),
        alternate_source_finder=_alternate_source,
        health_store=__import__("music_route_health").RouteHealthStore(health_path),
        trace_fn=trace_fn,
    )


# One whole-file DSP pass per audio file, reused by every window of that file.
# Bounded to a couple of tracks because RawAnalysis holds per-frame arrays for a
# whole song; the realtime path only ever works on the track being listened to
# plus, briefly, the one before it.
_RAW_CACHE: "dict[tuple, object]" = {}
_RAW_CACHE_MAX = 2


def _raw_analysis(audio_path: str):
    """`music_dsp.analyze`, memoised on file identity.

    Keyed on (path, mtime_ns, size) rather than the path alone: the audio cache
    reuses a filename when a track is re-downloaded, and serving the previous
    file's spectrum would be a silent wrong answer rather than a slow one.
    """
    import music_dsp as dsp

    stat = Path(audio_path).stat()
    key = (str(audio_path), stat.st_mtime_ns, stat.st_size)
    cached = _RAW_CACHE.get(key)
    if cached is not None:
        return cached
    raw = dsp.analyze(audio_path)
    if len(_RAW_CACHE) >= _RAW_CACHE_MAX:
        _RAW_CACHE.pop(next(iter(_RAW_CACHE)))
    _RAW_CACHE[key] = raw
    return raw


def default_fast_ear(audio_path: str, start_s: float, end_s: float) -> tuple[FastEarCandidate, ...]:
    """Fast Ear over the real DSP stack, window-sliced from the 1Hz series.

    `music_dsp.analyze` is whole-file by construction and already produces
    exactly the features Fast Ear wants, so the unit of work is one analysis per
    file (memoised above) and a slice per window — not one analysis per window,
    which measured at ~4.9s of STFT for every 15 seconds of audio.
    """
    raw = _raw_analysis(audio_path)
    lo, hi = int(max(0, start_s)), int(max(0, end_s))

    def _slice(series):
        return [float(v) for v in series[lo:hi]] if series is not None else []

    return fast_ear_from_series(
        start_s=float(lo),
        energy_1hz=_slice(raw.energy_1hz),
        flux_1hz=_slice(raw.flux_1hz),
        brightness_1hz=_slice(raw.brightness_1hz),
        onset_1hz=_slice(raw.onset_1hz),
    )


def new_music_session(
    *, media_id: str, conversation_id: str, platform: str = ""
) -> MediaSession:
    """A fresh session with a fail-closed boundary — nothing revealable yet."""
    return MediaSession(
        session_id=new_id("msess"),
        media_id=media_id,
        kind=KIND_MUSIC,
        conversation_id=conversation_id,
        platform=platform,
        capabilities=("seek", "prefetch", "deep_analysis"),
        spoiler=SpoilerBoundary(),
    )


__all__ = [
    "RealtimeMusicCompanion",
    "RealtimeMetrics",
    "FastEarCandidate",
    "fast_ear_from_series",
    "observations_from_candidates",
    "select_deep_ear_targets",
    "window_cache_key",
    "build_default_ladder",
    "default_fast_ear",
    "new_music_session",
    "CANDIDATE_KINDS",
    "FAST_EAR_PRODUCER",
    "DEEP_EAR_PRODUCER",
]
