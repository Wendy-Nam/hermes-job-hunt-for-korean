"""Realtime music playhead, ahead-buffer planning, and the timed artifact cache.

Position is a `MediaPosition` — always. `Playhead` below is a *tracker*, not a
coordinate: it consumes reports of where the listener is and emits
`MediaPosition` on the `media` clock. There is deliberately no
`playback_cursor` / `audio_cursor` type here; those are banned synonyms in the
shared contract, and inventing a music-only coordinate is exactly the drift the
contract exists to prevent.

Three jobs:

**Playhead** — hold the last reported position, and *project* it forward when
asked at a later wall-clock instant. Projection is what makes the ahead-buffer
useful (we plan for where the listener will be in 30s, not where they were when
they last spoke), and it is also the part that can be wrong, so a projected
position carries `confidence < 1.0` that decays with extrapolation age. The
contract already treats `confidence < 1.0` as "inferred", and `SpoilerBoundary`
is fail-closed at an inferred boundary — so a guessed playhead can never widen
what is revealable. That interaction is the reason projection is modelled here
rather than by adding seconds to a number at the call site.

**Ahead-buffer planning** — which windows to acquire and analyse next, given
where the listener is and what has already been done. Returns `MediaRange`s.

**TimedArtifactCache** — `AnalysisArtifact`s indexed by their anchor, so
"what do we know about right now?" is a lookup rather than a re-analysis.
Keyed by `cache_identity()`, never by a URL: invariant 8.

Nothing here does DSP. Fast Ear and Deep Ear live in `music_realtime`, which
drives this module.
"""
from __future__ import annotations

import bisect
import threading
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from hermes_media import (
    CLOCK_MEDIA,
    AnalysisArtifact,
    MediaPosition,
    MediaRange,
    ValidationError,
)

# How long a reported position is trusted before projection starts eroding
# confidence, and how fast it erodes after that. A listener who last reported
# 10s ago is almost certainly still where we think; one who reported 5 minutes
# ago may have paused, seeked, or wandered off.
PROJECTION_TRUSTED_MS = 10_000
PROJECTION_HALF_LIFE_MS = 60_000
PROJECTION_MIN_CONFIDENCE = 0.15

# Default ahead-buffer geometry.
WINDOW_S = 15.0        # one analysis window
LOOKAHEAD_S = 60.0     # how far past the playhead to keep analysed
MAX_WINDOWS = 4        # per planning pass, so one call cannot queue a whole track


@dataclass
class PlayheadReport:
    """One observation of where the listener is. `wall_ms` is when it was made."""

    position_ms: int
    wall_ms: int
    playing: bool = True
    rate: float = 1.0
    sequence_no: int | None = None


class Playhead:
    """Tracks a listener's position in one track and projects it forward.

    Out-of-order reports are dropped by `sequence_no`, the same rule
    `MediaSession.advanced_to` uses — a late-arriving stale report must not
    rewind the cursor and cause the ahead-buffer to re-analyse behind itself.
    """

    def __init__(self, media_id: str, *, duration_s: float | None = None):
        if not media_id:
            raise ValidationError("Playhead needs a media_id")
        self.media_id = media_id
        self.duration_s = duration_s
        self._last: PlayheadReport | None = None
        self._lock = threading.RLock()

    @property
    def last_report(self) -> PlayheadReport | None:
        return self._last

    def report(self, report: PlayheadReport) -> bool:
        """Accept a position report. Returns False if it was dropped as stale."""
        with self._lock:
            prev = self._last
            if prev is not None:
                if (
                    report.sequence_no is not None
                    and prev.sequence_no is not None
                    and report.sequence_no <= prev.sequence_no
                ):
                    return False
                if report.sequence_no is None and report.wall_ms < prev.wall_ms:
                    return False
            self._last = report
            return True

    def position(self, *, now_wall_ms: int | None = None) -> MediaPosition | None:
        """Where the listener is, projected to `now_wall_ms`.

        Returns None before the first report — "we do not know" rather than a
        fabricated zero, because position 0 would tell `SpoilerBoundary` the
        listener has consumed the opening, which is a claim we cannot make.
        """
        with self._lock:
            last = self._last
        if last is None:
            return None
        if now_wall_ms is None or not last.playing:
            return MediaPosition.at_ms(
                int(last.position_ms), clock=CLOCK_MEDIA,
                sequence_no=last.sequence_no, confidence=1.0,
            )

        elapsed = max(0, now_wall_ms - last.wall_ms)
        projected_ms = int(last.position_ms + elapsed * max(0.0, last.rate))
        if self.duration_s:
            projected_ms = min(projected_ms, int(self.duration_s * 1000))
        return MediaPosition.at_ms(
            projected_ms, clock=CLOCK_MEDIA, sequence_no=last.sequence_no,
            confidence=projection_confidence(elapsed),
        )

    def alignment_error_ms(self, truth_ms: int, *, now_wall_ms: int) -> int | None:
        """|projected - actual|. The benchmark's playhead-alignment metric."""
        pos = self.position(now_wall_ms=now_wall_ms)
        if pos is None or pos.timestamp_ms is None:
            return None
        return abs(int(pos.timestamp_ms) - int(truth_ms))


def projection_confidence(elapsed_ms: int) -> float:
    """1.0 while the report is fresh, then exponential decay with a floor.

    The floor exists so a long-idle playhead stays *usable* for prefetch (we
    still want to buffer somewhere) while remaining unmistakably inferred, and
    therefore fail-closed for reveal decisions.
    """
    if elapsed_ms <= PROJECTION_TRUSTED_MS:
        return 1.0
    over = elapsed_ms - PROJECTION_TRUSTED_MS
    decayed = 0.5 ** (over / PROJECTION_HALF_LIFE_MS)
    return max(PROJECTION_MIN_CONFIDENCE, round(decayed, 4))


def _range_s(start_s: float, end_s: float) -> MediaRange:
    return MediaRange(
        start=MediaPosition.at_seconds(start_s, clock=CLOCK_MEDIA),
        end=MediaPosition.at_seconds(end_s, clock=CLOCK_MEDIA),
    )


def plan_ahead_windows(
    position: MediaPosition | None,
    *,
    duration_s: float | None,
    covered: Sequence[tuple[float, float]] = (),
    window_s: float = WINDOW_S,
    lookahead_s: float = LOOKAHEAD_S,
    max_windows: int = MAX_WINDOWS,
) -> tuple[MediaRange, ...]:
    """Windows to acquire/analyse next, nearest-first, skipping covered ground.

    Nearest-first because the whole point is perceived latency: the window the
    listener reaches in five seconds is worth more than the one they reach in
    fifty. Windows are aligned to a fixed grid so that two planning passes at
    slightly different positions produce the *same* windows and therefore the
    same cache keys — unaligned windows would miss every cache and quietly
    double the analysis cost.
    """
    if position is None or position.timestamp_ms is None:
        return ()
    if window_s <= 0:
        raise ValidationError("window_s must be > 0")

    now_s = position.timestamp_ms / 1000.0
    horizon_s = now_s + lookahead_s
    if duration_s:
        horizon_s = min(horizon_s, duration_s)
    if horizon_s <= now_s:
        return ()

    covered_sorted = sorted(covered)
    out: list[MediaRange] = []
    # Start at the grid cell containing the playhead: the listener is inside it
    # right now and may not have it analysed yet.
    index = int(now_s // window_s)
    while len(out) < max_windows:
        start = index * window_s
        end = start + window_s
        if start >= horizon_s:
            break
        if duration_s:
            end = min(end, duration_s)
        if end - start < window_s * 0.25:
            break  # a sliver at the end of the track is not worth a pass
        if not _is_covered(start, end, covered_sorted):
            out.append(_range_s(start, end))
        index += 1
    return tuple(out)


def _is_covered(start: float, end: float, covered: Sequence[tuple[float, float]]) -> bool:
    for c_start, c_end in covered:
        if c_start <= start + 1e-6 and c_end >= end - 1e-6:
            return True
    return False


class TimedArtifactCache:
    """`AnalysisArtifact`s indexed by anchor, for retrieval around a position.

    An artifact is stored once under its `cache_key` (contract invariant 8:
    derived from media identity and analysis parameters, never from a transient
    URL) and additionally indexed by start time so `around()` is a bisect
    rather than a scan. Storing the same key twice is a no-op, which is what
    makes duplicate-analysis suppression free.
    """

    def __init__(self) -> None:
        self._by_key: dict[str, AnalysisArtifact] = {}
        self._starts: list[float] = []
        self._ordered: list[tuple[float, float, str]] = []  # (start_s, end_s, key)
        self._lock = threading.RLock()
        self.duplicate_puts = 0

    def __len__(self) -> int:
        return len(self._by_key)

    def has(self, cache_key: str) -> bool:
        with self._lock:
            return cache_key in self._by_key

    def get(self, cache_key: str) -> AnalysisArtifact | None:
        with self._lock:
            return self._by_key.get(cache_key)

    def put(self, artifact: AnalysisArtifact) -> bool:
        """Store. Returns False when this exact analysis was already cached."""
        with self._lock:
            if artifact.cache_key in self._by_key:
                self.duplicate_puts += 1
                return False
            self._by_key[artifact.cache_key] = artifact
            bounds = _artifact_bounds(artifact)
            if bounds is not None:
                start, end = bounds
                idx = bisect.bisect_left(self._starts, start)
                self._starts.insert(idx, start)
                self._ordered.insert(idx, (start, end, artifact.cache_key))
            return True

    def covered_ranges(self, *, producer: str | None = None) -> tuple[tuple[float, float], ...]:
        """What is already analysed, as (start_s, end_s) — the input
        `plan_ahead_windows` needs to avoid re-doing work."""
        with self._lock:
            return tuple(
                (start, end)
                for start, end, key in self._ordered
                if producer is None or self._by_key[key].producer == producer
            )

    def around(
        self, position: MediaPosition | None, *, radius_s: float = 20.0
    ) -> tuple[AnalysisArtifact, ...]:
        """Artifacts overlapping [position - radius, position + radius].

        This is the "immediate retrieval around the current playhead" path: it
        touches no DSP, no disk and no network, so answering "what's happening
        right now?" costs a dictionary lookup.
        """
        if position is None or position.timestamp_ms is None:
            return ()
        centre = position.timestamp_ms / 1000.0
        lo, hi = centre - radius_s, centre + radius_s
        with self._lock:
            return tuple(
                self._by_key[key]
                for start, end, key in self._ordered
                if start <= hi and end >= lo
            )

    def all(self) -> tuple[AnalysisArtifact, ...]:
        with self._lock:
            return tuple(self._by_key.values())


def _artifact_bounds(artifact: AnalysisArtifact) -> tuple[float, float] | None:
    anchor = artifact.anchor
    if isinstance(anchor, MediaRange):
        if anchor.start.timestamp_ms is None or anchor.end.timestamp_ms is None:
            return None
        return (anchor.start.timestamp_ms / 1000.0, anchor.end.timestamp_ms / 1000.0)
    if isinstance(anchor, MediaPosition) and anchor.timestamp_ms is not None:
        point = anchor.timestamp_ms / 1000.0
        return (point, point)
    return None


__all__ = [
    "Playhead",
    "PlayheadReport",
    "TimedArtifactCache",
    "plan_ahead_windows",
    "projection_confidence",
    "WINDOW_S",
    "LOOKAHEAD_S",
    "MAX_WINDOWS",
    "PROJECTION_TRUSTED_MS",
]
