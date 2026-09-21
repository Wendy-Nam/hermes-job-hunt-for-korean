"""Music/video realtime **adapter** — the existing ears on the canonical engine.

This module contains no DSP, no new model, no new provider and no new fetch
route. Every perceptual primitive it uses already existed at baseline:

* ``music_realtime.fast_ear_from_series`` / the injected ``fast_ear_fn``
* ``music_realtime.select_deep_ear_targets`` — the promotion ladder, verbatim
* ``music_realtime.window_cache_key`` — contract invariant 8 identity
* ``music_playhead.Playhead`` / ``plan_ahead_windows`` / ``TimedArtifactCache``
* ``MediaSession`` and its two distinct advances (consumed vs analysis frontier)

What *is* new is the removal of the coupling. At baseline
``RealtimeMusicCompanion.advance()`` ran Fast Ear over every planned window and
then ran Deep Ear — an isolated-venv subprocess taking seconds — synchronously
in the same call, pull-driven inside one turn. Nothing observed while nobody
was asking, and no reaction could be faster than the analysis it was bundled
with. Here the two are separate engine lanes.

Reading order: ``docs/MUSIC_REALTIME_MIGRATION_SEMANTICS.md`` first — it records
what the baseline meant, and every non-obvious decision below is justified
there rather than re-argued here.

Three things in this file are easy to get wrong and are load-bearing:

**1. Fast Ear stays fast because the expensive part happens once, elsewhere.**
``perceive()`` is synchronous and runs on the event loop, so it must not touch
the DSP stack: ``music_dsp.analyze`` is a whole-file STFT measured at ~4.9s.
:meth:`MusicVideoInteractionAdapter.prepare` does acquisition *and* that
whole-file pass in an executor, once, before the engine starts; ``perceive()``
then only slices a memoised 1Hz series, which is plain-Python arithmetic over
~15 floats. An adapter that instead let ``perceive()`` fault the DSP in would
stall every lane on the first window and look like an engine bug.

**2. Deep Ear must not run on the event loop.** The engine is single-threaded
asyncio. A ``deep_analysis`` that called the blocking subprocess directly would
block the loop and take the fast lane down with it — reproducing the exact
baseline coupling inside a design whose entire claim is that it removed it.
Hence :meth:`_run_deep_ear` goes through ``run_in_executor``.

**3. The deep budget is enforced at the consumer, not at the selector.**
Baseline had one promotion path (salience/spacing/budget in
``select_deep_ear_targets``). The engine adds a second, independent one —
``EventSpec.escalate_after``, which promotes on *persistence* rather than
amplitude. That is genuinely additive, but it means a budget checked only
inside the selector is a budget the escalation path walks straight past. One
ladder, two entrances, one turnstile: :meth:`deep_analysis` re-checks.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

HERE = Path(__file__).resolve().parent
for _extra in (HERE, HERE.parent / "_shared"):
    if str(_extra) not in sys.path:  # plugins are loaded by path, not as packages
        sys.path.insert(0, str(_extra))

import music_playhead as playhead_mod  # noqa: E402
import music_realtime as rt  # noqa: E402

import hermes_realtime as rte  # noqa: E402
import hermes_realtime_engine as eng  # noqa: E402
from hermes_media import (  # noqa: E402
    CLOCK_MEDIA,
    AnalysisArtifact,
    MediaPosition,
    MediaRange,
    MediaSession,
    Provenance,
    new_id,
)

logger = logging.getLogger(__name__)

NAMESPACE = "music"

# -- event vocabulary ---------------------------------------------------------
EVENT_SECTION_CHANGE = "music:section_change"
EVENT_ENERGY_CHANGE = "music:energy_change"
EVENT_RHYTHM_CHANGE = "music:rhythm_change"
EVENT_TIMBRE_CHANGE = "music:timbre_change"
EVENT_VOCAL_ENTRY = "music:vocal_entry"
EVENT_SILENCE = "music:silence"
EVENT_REPEATED_SEGMENT = "music:repeated_segment"
EVENT_DEEP_LISTEN = "music:deep_listen"
EVENT_PLAYHEAD_JUMP = "music:playhead_jump"

#: How far a reported position may differ from the projected one before it is a
#: discontinuity rather than drift. Normal reporting jitter and rate error are
#: sub-second; a deliberate seek is essentially never under two seconds.
#: Deliberately *not* "any backward motion": that would miss a forward chorus
#: skip, which for music is the common case.
SEEK_EPSILON_MS = 2_000

#: A deep result is about a moment. Past this distance from the current
#: playhead it may still be a true statement about the track, but it is no
#: longer a statement about *now*, and <AGENT_NAME> must not narrate it as one.
DEEP_RELEVANCE_RADIUS_S = 30.0

#: Salience below which a Fast Ear candidate is context, not an utterance.
#: Distinct from ``DEEP_EAR_SALIENCE_FLOOR`` — this one decides whether we
#: *mention* it, that one decides whether we *analyse* it.
FAST_EVENT_FLOOR = 0.20

_CANDIDATE_EVENT: dict[str, str] = {
    rt.CANDIDATE_ENERGY: EVENT_ENERGY_CHANGE,
    rt.CANDIDATE_ONSET: EVENT_RHYTHM_CHANGE,
    rt.CANDIDATE_SPECTRAL: EVENT_TIMBRE_CHANGE,
    # A structural boundary is a single-witness section signal; CANDIDATE_SECTION
    # is the two-witness one (flux peak coincident with an energy step) and
    # arrives with higher salience. Both map to the same event so that coalescing
    # folds them: ``folded`` keeps the maximum confidence, so the two-witness
    # reading wins without a branch anywhere.
    rt.CANDIDATE_BOUNDARY: EVENT_SECTION_CHANGE,
    rt.CANDIDATE_SECTION: EVENT_SECTION_CHANGE,
}

MUSIC_SPECS: tuple[eng.EventSpec, ...] = (
    eng.EventSpec(
        EVENT_SECTION_CHANGE, lane=rte.LANE_FAST, priority=rte.Priority.HIGH,
        ttl_ms=8_000, coalesce=rte.COALESCE_REPLACE, coalesce_window_ms=1_500,
        description="the music moved into a different part of itself",
    ),
    eng.EventSpec(
        EVENT_ENERGY_CHANGE, lane=rte.LANE_FAST, priority=rte.Priority.NORMAL,
        ttl_ms=5_000, coalesce=rte.COALESCE_MERGE, coalesce_window_ms=1_200,
        # Persistence-based promotion. A long grinding build-up never produces
        # one large step, so the salience ladder is structurally blind to it;
        # four folded energy steps inside the window are that build-up.
        escalate_after=4, escalate_to=EVENT_DEEP_LISTEN,
        description="loudness stepped",
    ),
    eng.EventSpec(
        EVENT_RHYTHM_CHANGE, lane=rte.LANE_FAST, priority=rte.Priority.NORMAL,
        ttl_ms=5_000, coalesce=rte.COALESCE_MERGE, coalesce_window_ms=1_200,
        description="onset/rhythmic density stepped",
    ),
    eng.EventSpec(
        EVENT_TIMBRE_CHANGE, lane=rte.LANE_FAST, priority=rte.Priority.LOW,
        ttl_ms=5_000, coalesce=rte.COALESCE_MERGE, coalesce_window_ms=1_500,
        description="spectral centroid shifted; the colour of the sound changed",
    ),
    eng.EventSpec(
        EVENT_VOCAL_ENTRY, lane=rte.LANE_FAST, priority=rte.Priority.HIGH,
        ttl_ms=6_000, coalesce=rte.COALESCE_SUPPRESS, coalesce_window_ms=2_000,
        description="a voice entered (detector-gated; never inferred from brightness)",
    ),
    eng.EventSpec(
        EVENT_SILENCE, lane=rte.LANE_FAST, priority=rte.Priority.LOW,
        ttl_ms=10_000, coalesce=rte.COALESCE_SUPPRESS, coalesce_window_ms=5_000,
        description="the track went quiet (detector-gated)",
    ),
    eng.EventSpec(
        EVENT_REPEATED_SEGMENT, lane=rte.LANE_FAST, priority=rte.Priority.LOW,
        ttl_ms=10_000, coalesce=rte.COALESCE_SUPPRESS, coalesce_window_ms=3_000,
        description="this window looks like one already analysed elsewhere",
    ),
    eng.EventSpec(
        EVENT_DEEP_LISTEN, lane=rte.LANE_DEEP, priority=rte.Priority.HIGH,
        ttl_ms=30_000, coalesce=rte.COALESCE_NONE,
        # A deep result is a claim about a moment; if the listener left that
        # moment the claim is not wrong, it is merely no longer an answer to
        # anything anyone asked.
        cancel_when_stale=True,
        description="a window worth Deep Ear's time",
    ),
    eng.EventSpec(
        # Context-only: it changes what the engine *knows* (the generation) and
        # asks nobody to react. Emitting it is what cancels in-flight deep work
        # for a moment the listener has left.
        EVENT_PLAYHEAD_JUMP, lane=rte.LANE_NONE, priority=rte.Priority.HIGH,
        ttl_ms=30_000, coalesce=rte.COALESCE_NONE,
        description="the playhead moved discontinuously (seek/replay)",
    ),
)


@dataclass
class MusicAdapterCounters:
    """Domain counts the engine cannot know.

    Deliberately holds **no timings**. Every duration in this subsystem now
    comes from ``eng.Telemetry``; a second timing source is exactly the
    duplication this migration exists to remove. See :meth:`realtime_metrics`.
    """

    fast_ear_runs: int = 0
    fast_ear_skipped_cached: int = 0
    deep_ear_runs: int = 0
    deep_ear_skipped_cached: int = 0
    deep_ear_declined: int = 0
    deep_ear_over_budget: int = 0
    deep_ear_off_position: int = 0
    #: Deep Ear passes the engine's cancellation token stopped before the
    #: executor hand-off, and results thrown away because the listener moved
    #: while Demucs ran. Separate counters because they cost different amounts:
    #: the first is free, the second is paid for and discarded.
    deep_ear_cancelled_before_start: int = 0
    deep_ear_discarded_stale: int = 0
    windows_planned: int = 0
    position_lookups: int = 0
    position_cache_hits: int = 0
    prefetch_hits: int = 0
    seeks: int = 0
    stale_reports_dropped: int = 0
    repeated_segments: int = 0

    def snapshot(self) -> dict:
        return dict(self.__dict__)


@dataclass(frozen=True)
class _WindowSignature:
    """What makes two windows 'the same passage' for repeat detection.

    The candidate-kind multiset plus salience quantised to 0.1. Quantised
    because two performances of a chorus are never bit-identical and an exact
    float match would detect nothing; coarse enough that a verse and a chorus
    with the same *number* of energy steps still differ on kind ordering.
    """

    key: tuple

    @staticmethod
    def of(candidates: Sequence[rt.FastEarCandidate], start_s: float) -> "_WindowSignature":
        return _WindowSignature(
            tuple(sorted(
                (c.kind, round(c.salience * 10) / 10, round(c.at_s - start_s, 0))
                for c in candidates
            ))
        )

    @property
    def informative(self) -> bool:
        # An empty or single-candidate window is not distinctive enough to
        # claim two passages are the same thing. Without this, every quiet
        # window "repeats" every other quiet window.
        return len(self.key) >= 2


class MusicVideoInteractionAdapter(eng.BaseAdapter):
    """One track, one listener, on the canonical realtime engine.

    Ownership, unchanged from baseline: the playhead, the musical section
    state, the artifact cache, the fetch ladder and the promotion policy are
    all still music's. Scheduling, TTL, coalescing, lane routing, escalation,
    bounded work, stale cancellation and timing are all now the engine's.
    """

    namespace = NAMESPACE

    def __init__(
        self,
        *,
        session: MediaSession,
        fast_ear_fn: Callable[[str, float, float], Sequence[rt.FastEarCandidate]],
        deep_ear_fn: Callable[[str, float, float], dict | None] | None = None,
        acquire_fn: Callable[[], str | None],
        duration_s: float | None = None,
        level_fn: Callable[[str, float, float], float | None] | None = None,
        vocal_fn: Callable[[str, float, float], float | None] | None = None,
        sink: Callable[[eng.Reaction, rte.RealtimeEvent], Awaitable[None]] | None = None,
        window_s: float = playhead_mod.WINDOW_S,
        lookahead_s: float = playhead_mod.LOOKAHEAD_S,
        max_windows: int = playhead_mod.MAX_WINDOWS,
        silence_level: float = 0.02,
    ) -> None:
        self.session = session
        self.media_id = session.media_id
        self._fast_ear = fast_ear_fn
        self._deep_ear = deep_ear_fn
        self._acquire = acquire_fn
        self._level_fn = level_fn
        self._vocal_fn = vocal_fn
        self._sink = sink
        self._window_s = window_s
        self._lookahead_s = lookahead_s
        self._max_windows = max_windows
        self._silence_level = silence_level

        self.duration_s = duration_s
        self.playhead = playhead_mod.Playhead(session.media_id, duration_s=duration_s)
        self.artifacts = playhead_mod.TimedArtifactCache()
        self.counters = MusicAdapterCounters()

        self._audio_path: str | None = None
        self._prepared = False
        self._acquisition_failed = False
        self._deep_done_s: list[float] = []
        self._pending_jump: dict | None = None
        self._pending: list[tuple[rt.FastEarCandidate, MediaRange]] = []
        self._signatures: dict[tuple, float] = {}   # signature -> first start_s
        self._repeats: list[tuple[float, float, str]] = []   # (at_s, same_as_s, key)
        self._silent_windows: set[float] = set()
        self._vocal_seen: set[float] = set()
        self.dispatched: list[tuple[str, str, str]] = []   # (lane, event_type, text)

    # -- adapter contract ----------------------------------------------------
    def event_specs(self) -> Sequence[eng.EventSpec]:
        return MUSIC_SPECS

    def bumps_generation(self, event: rte.RealtimeEvent) -> bool:
        """Only a playhead discontinuity turns the world over.

        Notably **not** a section change. A new section does not invalidate an
        analysis of the previous one — that artifact stays anchored in media
        time and stays true. Bumping here would cancel the deep analysis of the
        very section that just started (Deep Ear takes seconds, sections last
        tens of seconds), which is why the relevance check in
        :meth:`deep_analysis` is a position test rather than a generation test.
        """
        return event.event_type in (EVENT_PLAYHEAD_JUMP, rte.EVENT_DISCONNECTED)

    # -- acquisition ---------------------------------------------------------
    @property
    def heard_anything(self) -> bool:
        """Validated audio *and* something actually analysed.

        The no-fake-listening invariant, carried over unchanged: the
        conversational layer asks this before saying anything that implies
        listening, and it is deliberately not "did a fetch happen".
        """
        return self._audio_path is not None and bool(self.artifacts.all())

    async def prepare(self) -> bool:
        """Acquire audio and warm the whole-file DSP pass, off the event loop.

        Both halves are slow and both are once-per-track, so they happen here
        rather than faulting in on the first ``perceive()`` — see the module
        docstring. Returns False when acquisition failed, which is an ordinary
        expected outcome the caller degrades honestly from, not an exception.
        """
        if self._prepared:
            return self._audio_path is not None
        loop = asyncio.get_running_loop()
        path = await loop.run_in_executor(None, self._acquire)
        self._prepared = True
        if not path:
            self._acquisition_failed = True
            return False
        self._audio_path = str(path)
        # Warm the memoised whole-file analysis so the first perception tick
        # slices a series that already exists instead of computing an STFT.
        try:
            await loop.run_in_executor(
                None, self._fast_ear, self._audio_path, 0.0, float(self._window_s)
            )
        except Exception:  # noqa: BLE001 — a cold cache is slow, never fatal
            logger.debug("fast-ear warmup failed", exc_info=True)
        return True

    # -- playhead ------------------------------------------------------------
    def report_position(
        self, position_ms: int, *, wall_ms: int, playing: bool = True,
        rate: float = 1.0, sequence_no: int | None = None, consumed: bool = True,
    ) -> str:
        """Record where the listener is. Returns accepted|stale|discontinuity.

        Discontinuity is *derived*, because ``Playhead`` cannot detect a seek:
        it only rejects out-of-order reports, so a backward jump carrying a
        higher ``sequence_no`` is accepted silently and is indistinguishable
        from ordinary progress. We compare the incoming position against the
        one the playhead would have projected for the same wall instant.
        """
        projected = self.playhead.position(now_wall_ms=wall_ms)
        jump = None
        if projected is not None and projected.timestamp_ms is not None:
            drift = abs(int(position_ms) - int(projected.timestamp_ms))
            if drift > SEEK_EPSILON_MS:
                jump = {
                    "from_ms": int(projected.timestamp_ms),
                    "to_ms": int(position_ms),
                    "drift_ms": drift,
                    "direction": "back" if position_ms < projected.timestamp_ms else "forward",
                }

        accepted = self.playhead.report(
            playhead_mod.PlayheadReport(
                position_ms=int(position_ms), wall_ms=int(wall_ms),
                playing=playing, rate=rate, sequence_no=sequence_no,
            )
        )
        if not accepted:
            # A late-arriving stale report must not rewind the cursor and make
            # the ahead-buffer re-analyse behind itself. It also must not be
            # allowed to raise a phantom seek.
            self.counters.stale_reports_dropped += 1
            return "stale"

        pos = MediaPosition.at_ms(int(position_ms), clock=CLOCK_MEDIA,
                                  sequence_no=sequence_no)
        self.session = self.session.advanced_to(pos, consumed=consumed)
        if jump is not None:
            self.counters.seeks += 1
            self._pending_jump = jump
            return "discontinuity"
        return "accepted"

    def current_position(self, *, now_wall_ms: int | None = None) -> MediaPosition | None:
        return self.playhead.position(now_wall_ms=now_wall_ms)

    def what_is_happening_now(
        self, *, now_wall_ms: int | None = None, radius_s: float = 20.0
    ) -> tuple[AnalysisArtifact, ...]:
        pos = self.current_position(now_wall_ms=now_wall_ms)
        self.counters.position_lookups += 1
        found = self.artifacts.around(pos, radius_s=radius_s)
        if found:
            self.counters.position_cache_hits += 1
            self.counters.prefetch_hits += 1
        return found

    # -- perception ----------------------------------------------------------
    def perceive(self, ctx, now_ms: int) -> Sequence[rte.PerceptionSample]:
        """Plan the ahead-buffer and run Fast Ear over it. Cheap by construction.

        No audio means no pass — the choke point that makes fake listening
        structurally impossible rather than merely discouraged.
        """
        if self._audio_path is None:
            return ()
        pos = self.playhead.position(now_wall_ms=now_ms)
        if pos is None:
            return ()   # no report yet: "we do not know", not a fabricated zero

        windows = playhead_mod.plan_ahead_windows(
            pos,
            duration_s=self.duration_s,
            covered=self.artifacts.covered_ranges(producer=rt.FAST_EAR_PRODUCER),
            window_s=self._window_s,
            lookahead_s=self._lookahead_s,
            max_windows=self._max_windows,
        )
        self.counters.windows_planned += len(windows)

        samples: list[rte.PerceptionSample] = []
        for window in windows:
            sample = self._fast_ear_window(window, now_ms)
            if sample is not None:
                samples.append(sample)
            # The ahead-buffer moves the analysis frontier, never the consumed
            # boundary — analysing minute three does not mean the listener
            # reached it.
            self.session = self.session.advanced_to(window.end, consumed=False)
        return tuple(samples)

    def _fast_ear_window(
        self, window: MediaRange, now_ms: int
    ) -> rte.PerceptionSample | None:
        key = rt.window_cache_key(
            self.media_id, window,
            producer=rt.FAST_EAR_PRODUCER, version=rt.FAST_EAR_VERSION,
        )
        if self.artifacts.has(key):
            self.counters.fast_ear_skipped_cached += 1
            return None

        start_s = window.start.timestamp_ms / 1000.0
        end_s = window.end.timestamp_ms / 1000.0
        try:
            candidates = tuple(self._fast_ear(self._audio_path, start_s, end_s) or ())
        except Exception:  # noqa: BLE001 — Fast Ear must never take a turn down
            logger.debug("fast ear failed on [%s, %s]", start_s, end_s, exc_info=True)
            return None
        self.counters.fast_ear_runs += 1

        artifact = AnalysisArtifact(
            artifact_id=new_id("art"),
            media_id=self.media_id,
            artifact_kind="fast_ear_window",
            cache_key=key,
            producer=rt.FAST_EAR_PRODUCER,
            producer_version=rt.FAST_EAR_VERSION,
            range=window,
            body_ref=f"fastear://{key}",
            summary={"candidates": len(candidates), "start_s": start_s, "end_s": end_s},
            provenance=Provenance(producer=rt.FAST_EAR_PRODUCER,
                                  producer_version=rt.FAST_EAR_VERSION),
        )
        self.artifacts.put(artifact)

        for c in candidates:
            self._pending.append((c, window))
        self._note_repeat(candidates, start_s, key)
        self._note_level(start_s, end_s)
        self._note_vocal(start_s, end_s)

        return rte.PerceptionSample(
            session_id=self.session.session_id,
            timestamp_ms=int(now_ms),
            source=f"{NAMESPACE}/fast_ear",
            kind="fast",
            values={
                "start_s": start_s,
                "end_s": end_s,
                "candidates": len(candidates),
                "max_salience": max((c.salience for c in candidates), default=0.0),
            },
            evidence=(f"artifact:{key}",),
        )

    def _note_repeat(
        self, candidates: Sequence[rt.FastEarCandidate], start_s: float, key: str
    ) -> None:
        sig = _WindowSignature.of(candidates, start_s)
        if not sig.informative:
            return
        first = self._signatures.get(sig.key)
        if first is None:
            self._signatures[sig.key] = start_s
            return
        if abs(first - start_s) < self._window_s:
            return   # the same window, not a repeat of it
        self._repeats.append((start_s, first, key))

    def _note_level(self, start_s: float, end_s: float) -> None:
        """Silence is detector-gated. Absent a level function we say nothing.

        Fast Ear's candidates describe *change*, not level, so "no candidates"
        means "nothing changed" and emphatically not "it went quiet" — a long
        sustained chord and a silent passage are identical to it. Inferring
        silence from an empty candidate list would be an overclaim.
        """
        if self._level_fn is None:
            return
        try:
            level = self._level_fn(self._audio_path, start_s, end_s)
        except Exception:  # noqa: BLE001
            return
        if level is not None and level <= self._silence_level:
            self._silent_windows.add(start_s)

    def _note_vocal(self, start_s: float, end_s: float) -> None:
        """Vocal entry is detector-gated for the same reason.

        Brightness (``CANDIDATE_SPECTRAL``) is a tempting proxy and a wrong
        one: a cymbal, a synth filter sweep and a voice all raise the spectral
        centroid. Without a real vocal detector this adapter never claims a
        voice entered.
        """
        if self._vocal_fn is None:
            return
        try:
            onset = self._vocal_fn(self._audio_path, start_s, end_s)
        except Exception:  # noqa: BLE001
            return
        if onset is not None:
            self._vocal_seen.add(float(onset))

    # -- detection -----------------------------------------------------------
    def detect(self, ctx, samples, now_ms: int) -> Sequence[rte.RealtimeEvent]:
        """Fast Ear candidates -> events, and the promotion ladder -> deep events.

        The playhead jump is emitted **first** so that the generation bump
        lands before this tick's other events; everything after it is stamped
        with the new generation and any deep work for the abandoned moment is
        already cancelled.
        """
        registry = getattr(ctx, "registry", None)
        if registry is None:
            return ()
        out: list[rte.RealtimeEvent] = []

        if self._pending_jump is not None:
            jump, self._pending_jump = self._pending_jump, None
            out.append(self._make(registry, EVENT_PLAYHEAD_JUMP, now_ms,
                                  source=f"{NAMESPACE}/playhead",
                                  payload=jump))
            # Candidates queued for windows the listener has left are not wrong,
            # but they are answers to a question nobody is asking any more.
            self._pending.clear()

        pending, self._pending = self._pending, []
        for candidate, window in pending:
            if candidate.salience < FAST_EVENT_FLOOR:
                continue
            event_type = _CANDIDATE_EVENT.get(candidate.kind)
            if event_type is None:
                continue
            out.append(self._make(
                registry, event_type, now_ms,
                source=f"{NAMESPACE}/fast_ear",
                confidence=min(1.0, max(0.0, candidate.salience)),
                payload={
                    "at_s": candidate.at_s,
                    "candidate": candidate.kind,
                    "salience": candidate.salience,
                    "detail": candidate.detail,
                },
                # Two candidate kinds map to section_change; folding them on
                # position rather than on kind is what lets the two-witness
                # reading override the single-witness one.
                dedupe_key=f"{event_type}|{int(candidate.at_s // 5)}",
            ))

        for start_s, first_s, key in self._repeats:
            self.counters.repeated_segments += 1
            out.append(self._make(
                registry, EVENT_REPEATED_SEGMENT, now_ms,
                source=f"{NAMESPACE}/repeat",
                payload={"at_s": start_s, "same_as_s": first_s},
                evidence=(f"artifact:{key}",),
            ))
        self._repeats.clear()

        for start_s in sorted(self._silent_windows):
            out.append(self._make(registry, EVENT_SILENCE, now_ms,
                                  source=f"{NAMESPACE}/level",
                                  payload={"at_s": start_s}))
        self._silent_windows.clear()

        for at_s in sorted(self._vocal_seen):
            out.append(self._make(registry, EVENT_VOCAL_ENTRY, now_ms,
                                  source=f"{NAMESPACE}/vocal",
                                  payload={"at_s": at_s}))
        self._vocal_seen.clear()

        out.extend(self._promote(registry, [c for c, _w in pending], now_ms))
        return tuple(out)

    def _promote(
        self, registry, candidates: Sequence[rt.FastEarCandidate], now_ms: int
    ) -> list[rte.RealtimeEvent]:
        """The baseline ladder, unchanged, expressed as deep-lane events.

        ``select_deep_ear_targets`` is called with exactly the arguments
        ``RealtimeMusicCompanion._deep_ear_pass`` used: the session-wide
        ``_deep_done_s`` as ``already_analysed_s`` so the 20s spacing is per
        track rather than per pass, and the budget reduced by what has already
        been spent.
        """
        if self._deep_ear is None or not candidates:
            return []
        remaining = max(0, rt.DEEP_EAR_BUDGET_PER_TRACK - len(self._deep_done_s))
        targets = rt.select_deep_ear_targets(
            candidates,
            already_analysed_s=tuple(self._deep_done_s),
            budget=remaining,
        )
        self.counters.deep_ear_declined += max(0, len(candidates) - len(targets))

        events = []
        for target in targets:
            start_s, end_s = self._deep_window_bounds(target.at_s)
            events.append(self._make(
                registry, EVENT_DEEP_LISTEN, now_ms,
                source=f"{NAMESPACE}/ladder",
                confidence=min(1.0, target.salience),
                payload={
                    "at_s": target.at_s,
                    "start_s": start_s,
                    "end_s": end_s,
                    "triggered_by": target.kind,
                },
            ))
        return events

    def _deep_window_bounds(self, at_s: float) -> tuple[float, float]:
        start_s = max(0.0, at_s - 5.0)
        end_s = start_s + 15.0
        if self.duration_s:
            end_s = min(end_s, self.duration_s)
        return start_s, end_s

    def _make(self, registry, event_type: str, now_ms: int, **kw) -> rte.RealtimeEvent:
        return rte.make_event(registry, event_type,
                              session_id=self.session.session_id,
                              timestamp_ms=int(now_ms), **kw)

    # -- fast lane -----------------------------------------------------------
    async def fast_reaction(self, event, ctx) -> eng.Reaction | None:
        """Pure and allocation-light. No I/O, no DSP, no awaiting anything.

        Anything that could block belongs in the deep lane; a fast reaction
        that awaits is a fast lane that is not fast.
        """
        text = _FAST_TEXT.get(event.event_type)
        if text is None:
            return None
        return eng.Reaction(
            lane=rte.LANE_FAST, kind="observe",
            text=text,
            payload={"at_s": event.payload.get("at_s"),
                     "occurrences": event.occurrences},
            evidence=(event.event_id,),
        )

    # -- deep lane -----------------------------------------------------------
    async def deep_analysis(self, event, projection, *,
                            cancelled=None) -> eng.Reaction | None:
        """Deep Ear for one window.

        ``cancelled`` is the engine's token, set before the awaiting task is
        cancelled. MUSIC_ENGINE_GAPS E6 measured what its absence costs: 4064
        cancellations beside Deep Ear invocations that every one of them ran to
        completion, because ``run_in_executor`` cannot interrupt a running
        thread and a user seeking repeatedly spawns subprocesses that each run
        to the end.

        Checking it here — before the executor hand-off, and again before the
        artifact is written — is what a Python-level token can honestly do. It
        does not kill a running Demucs process; stopping that needs the Popen
        handle, which belongs to the Deep Ear callable and not to this adapter.
        What it does guarantee is that a seeked-away window costs at most the
        pass already in flight, rather than every pass the backlog had queued.
        """
        if event.event_type != EVENT_DEEP_LISTEN or self._deep_ear is None:
            return None
        if cancelled is not None and cancelled.is_set():
            self.counters.deep_ear_cancelled_before_start += 1
            return None
        at_s = float(event.payload.get("at_s") or 0.0)
        start_s = float(event.payload.get("start_s") or 0.0)
        end_s = float(event.payload.get("end_s") or start_s + 15.0)

        # The turnstile, and the last line of it. The engine now asks
        # approve_escalation() before an escalation is even queued, so the
        # persistence path is refused at admission rather than after occupying
        # deep-lane capacity it was never going to be allowed to use. This check
        # stays because the salience ladder emits deep_listen directly and does
        # not pass through escalation at all — one predicate, two entrances.
        if not self._within_deep_budget(at_s):
            self.counters.deep_ear_over_budget += 1
            return None

        # Domain staleness, on top of the engine's generation check. Generation
        # catches a seek that happened; this catches a result that is simply
        # about somewhere else — a backlog that drained slowly, or a window
        # promoted far ahead of the listener.
        if not self._still_relevant(at_s):
            self.counters.deep_ear_off_position += 1
            return None

        window = MediaRange(
            start=MediaPosition.at_seconds(start_s, clock=CLOCK_MEDIA),
            end=MediaPosition.at_seconds(end_s, clock=CLOCK_MEDIA),
        )
        key = rt.window_cache_key(
            self.media_id, window,
            producer=rt.DEEP_EAR_PRODUCER, version=rt.FAST_EAR_VERSION,
        )
        if self.artifacts.has(key):
            # Replay and backward seek land here: the cache key is media
            # identity + window bounds, so re-entering an analysed region is a
            # hit rather than a second subprocess.
            self.counters.deep_ear_skipped_cached += 1
            self._deep_done_s.append(at_s)
            return None

        observation = await self._run_deep_ear(start_s, end_s)
        if cancelled is not None and cancelled.is_set():
            # The listener moved while Demucs ran. The pass is paid for either
            # way; what must not happen is it landing in the artifact store as
            # though it were about where they are now.
            self._deep_done_s.append(at_s)
            self.counters.deep_ear_discarded_stale += 1
            return None
        # Appended on every attempt, including failure — budget counts attempts,
        # not successes, so a failing window is not retried forever.
        self._deep_done_s.append(at_s)
        if not observation:
            return None
        self.counters.deep_ear_runs += 1

        artifact = AnalysisArtifact(
            artifact_id=new_id("art"),
            media_id=self.media_id,
            artifact_kind="deep_ear_window",
            cache_key=key,
            producer=rt.DEEP_EAR_PRODUCER,
            producer_version=str(observation.get("analyzer_version")
                                 or rt.FAST_EAR_VERSION),
            range=window,
            body_ref=f"deepear://{key}",
            summary={"at_s": at_s, "triggered_by": event.payload.get("triggered_by")},
            provenance=Provenance(producer=rt.DEEP_EAR_PRODUCER),
        )
        self.artifacts.put(artifact)

        return eng.Reaction(
            lane=rte.LANE_DEEP, kind="analysis",
            text=str(observation.get("summary") or ""),
            payload={"at_s": at_s, "cache_key": key},
            evidence=(event.event_id, f"artifact:{key}"),
        )

    def _within_deep_budget(self, at_s: float) -> bool:
        """Is another Deep Ear pass allowed at ``at_s``?

        The one place the per-track budget and the minimum spacing are decided.
        Deep Ear is a Demucs subprocess measured in seconds, so this is what
        stops a five-minute track from costing thirty of them.
        """
        if len(self._deep_done_s) >= rt.DEEP_EAR_BUDGET_PER_TRACK:
            return False
        return not any(abs(at_s - t) < rt.DEEP_EAR_MIN_SPACING_S
                       for t in self._deep_done_s)

    def approve_escalation(self, original, promoted) -> bool:
        """Make the engine's persistence promotion pass the domain's turnstile.

        MUSIC_ENGINE_GAPS E2: ``escalate_after`` is a genuinely useful second
        promotion path — a long grinding build-up never makes one large step, so
        a salience ladder is structurally blind to it — but it reached the deep
        lane without consulting the budget that exists to stop a track costing
        thirty subprocesses. The adapter compensated by re-checking inside
        ``deep_analysis``, which worked and put the same policy in two places.

        The engine now asks first, so the refusal happens before the event takes
        deep-lane capacity. Same predicate, one definition.
        """
        if promoted.event_type != EVENT_DEEP_LISTEN:
            return True
        at_s = float(promoted.payload.get("at_s") or 0.0)
        if self._within_deep_budget(at_s):
            return True
        self.counters.deep_ear_over_budget += 1
        return False

    async def _run_deep_ear(self, start_s: float, end_s: float) -> dict | None:
        """Deep Ear in an executor. See point 2 of the module docstring."""
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                None, self._deep_ear, self._audio_path, start_s, end_s
            )
        except Exception:  # noqa: BLE001 — Fast Ear survives Deep Ear's death
            logger.debug("deep ear failed at %ss", start_s, exc_info=True)
            return None

    def _still_relevant(self, at_s: float) -> bool:
        """Is this window still worth analysing, given where the listener is?

        **Asymmetric, and it has to be.** A symmetric radius looks reasonable
        and silently destroys the ahead-buffer: `plan_ahead_windows` analyses up
        to `LOOKAHEAD_S` (60s) in front of the playhead, so every prefetched
        window is "far from the listener" by construction and a symmetric guard
        refuses precisely the work the prefetch existed to do. (It did: the
        first run of replay scenario B promoted nothing at all.)

        Ahead is the point. Behind is the problem — a window the listener has
        already passed is a statement about a moment that is over.
        """
        report = self.playhead.last_report
        if report is None:
            return True
        delta_s = at_s - report.position_ms / 1000.0
        if delta_s < -DEEP_RELEVANCE_RADIUS_S:
            return False                       # the listener has moved past it
        # Beyond anything the ahead-buffer would ever have planned: a backlog
        # left over from before a backward seek.
        return delta_s <= self._lookahead_s + self._window_s

    # -- dispatch ------------------------------------------------------------
    async def dispatch(self, reaction: eng.Reaction, event) -> None:
        self.dispatched.append((reaction.lane, event.event_type, reaction.text))
        if self._sink is not None:
            await self._sink(reaction, event)

    async def on_close(self, reason: str) -> None:
        self._pending.clear()
        self._repeats.clear()
        self._silent_windows.clear()
        self._vocal_seen.clear()
        self._signatures.clear()

    # -- reporting -----------------------------------------------------------
    def realtime_metrics(self, engine: "eng.RealtimeInteractionEngine") -> dict:
        """The old ``RealtimeMetrics.snapshot()`` shape, as a **projection**.

        Kept for the benchmark and the conversational layer, which already read
        these key names. Every count comes from the adapter and every duration
        comes from the engine's telemetry: there is one timing source in this
        subsystem now, and this function is a view over it rather than a second
        instrument that could disagree with the first.
        """
        summary = engine.telemetry.summary()
        counters = summary.get("counters", {})
        # The key is "latency", not "intervals". Reading the wrong name returned
        # None for every duration and looked exactly like an instrument that had
        # not fired — which is the R4 failure the engine's own MARKS docstring
        # warns about, reproduced one layer up. Hence `_median` below returns a
        # sentinel string rather than None when a metric is genuinely absent: a
        # missing measurement and a measurement of zero must not look alike.
        latency = summary.get("latency", {})
        c = self.counters

        def _median(metric: str):
            entry = latency.get(metric)
            if not entry or not entry.get("n"):
                return None
            return entry.get("median")

        fast_total = c.fast_ear_runs + c.fast_ear_skipped_cached
        return {
            "fast_ear_runs": c.fast_ear_runs,
            "fast_ear_skipped_cached": c.fast_ear_skipped_cached,
            "deep_ear_runs": c.deep_ear_runs,
            "deep_ear_skipped_cached": c.deep_ear_skipped_cached,
            "deep_ear_declined": c.deep_ear_declined,
            "windows_planned": c.windows_planned,
            "prefetch_hit_rate": (
                c.prefetch_hits / c.position_lookups if c.position_lookups else None
            ),
            "position_cache_hit_rate": (
                c.position_cache_hits / c.position_lookups if c.position_lookups else None
            ),
            "duplicate_fast_ear_rate": (
                c.fast_ear_skipped_cached / fast_total if fast_total else None
            ),
            # Durations are the engine's. The old mean_fast_ear_ms /
            # mean_deep_ear_ms measured the DSP call; these measure
            # detection-to-reaction, which is what the listener experiences.
            #
            # **Only valid when the engine runs on a real clock.** The engine
            # stamps `event_detected`/`event_enqueued` from its injected
            # `clock_ms` but every later mark from wall time, so under a test or
            # replay clock these two derive to the difference between the two
            # epochs (~56 years) and are recorded silently, since the only check
            # is `delta >= 0`. See E7 in docs/MUSIC_ENGINE_GAPS.md.
            "fast_lane_ms": _median("event_to_fast_reaction"),
            "deep_lane_ms": _median("event_to_deep_analysis"),
            # These two are stamped from a single source at both ends, so they
            # are trustworthy under any clock — which is why the fast/deep
            # isolation claim is measured with them and with a wall-clock probe
            # in replay scenario I, not with the two above.
            "fast_reaction_compute_ms": _median("fast_reaction_compute"),
            "deep_analysis_compute_ms": _median("deep_analysis_compute"),
            "stale_analysis_dropped": counters.get("stale_analysis_dropped", 0),
            "deep_cancelled_stale": counters.get("deep_cancelled_stale", 0),
            "seeks": c.seeks,
            "repeated_segments": c.repeated_segments,
            "deep_over_budget": c.deep_ear_over_budget,
            "deep_cancelled_before_start": c.deep_ear_cancelled_before_start,
            "deep_discarded_stale": c.deep_ear_discarded_stale,
            "deep_off_position": c.deep_ear_off_position,
            "heard_anything": self.heard_anything,
            "artifacts": len(self.artifacts),
            "duplicate_artifact_puts": self.artifacts.duplicate_puts,
        }


_FAST_TEXT: dict[str, str] = {
    EVENT_SECTION_CHANGE: "여기서 분위기 바뀌네",
    EVENT_VOCAL_ENTRY: "목소리 들어왔다",
    EVENT_REPEATED_SEGMENT: "이 부분 아까 그거다",
    EVENT_SILENCE: "갑자기 조용해지네",
}


__all__ = [
    "MusicVideoInteractionAdapter", "MusicAdapterCounters", "MUSIC_SPECS",
    "NAMESPACE", "SEEK_EPSILON_MS", "DEEP_RELEVANCE_RADIUS_S", "FAST_EVENT_FLOOR",
    "EVENT_SECTION_CHANGE", "EVENT_ENERGY_CHANGE", "EVENT_RHYTHM_CHANGE",
    "EVENT_TIMBRE_CHANGE", "EVENT_VOCAL_ENTRY", "EVENT_SILENCE",
    "EVENT_REPEATED_SEGMENT", "EVENT_DEEP_LISTEN", "EVENT_PLAYHEAD_JUMP",
]
