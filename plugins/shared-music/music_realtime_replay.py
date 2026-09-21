"""Synthetic media replay for the music/video adapter — no network, no DSP stack.

Scenarios A~J from the migration brief, plus a micro soak. Nothing here reaches
the network, the audio cache, yt-dlp, numpy or the Deep Ear venv, so it runs in
the plain host interpreter and in CI.

The synthesis is deliberately *shallow but real*: a :class:`SyntheticTrack`
produces 1Hz feature series and the harness feeds them to
``music_realtime.fast_ear_from_series`` — the actual Fast Ear arithmetic,
thresholds and section-coincidence rule, not a stub that returns canned
candidates. A scenario named "rapid energy change" therefore fails if the real
``_step_points`` stops firing on real steps, which a canned-candidate harness
could never notice.

What is faked is exactly one thing: the bytes. ``deep_ear`` sleeps instead of
running Demucs, and ``acquire`` returns a path nobody opens.

Usage::

    python3 music_realtime_replay.py              # A~J, human readable
    python3 music_realtime_replay.py --json
    python3 music_realtime_replay.py --only F G
    python3 music_realtime_replay.py --soak 60    # micro soak, seconds
"""
from __future__ import annotations

import argparse
import asyncio
import gc
import json
import resource
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

HERE = Path(__file__).resolve().parent
for _extra in (HERE, HERE.parent / "_shared"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

import music_realtime as rt  # noqa: E402
import music_realtime_adapter as mra  # noqa: E402
import hermes_realtime as rte  # noqa: E402
import hermes_realtime_engine as eng  # noqa: E402

DURATION_S = 240.0
MEDIA_ID = "music:synthetic"
AUDIO = "/synthetic/track.opus"


# =============================================================================
# synthetic media
# =============================================================================

@dataclass
class SyntheticTrack:
    """1Hz feature series for a whole track, plus optional vocal onsets.

    ``sections`` is a list of (start_s, energy, brightness, onset) plateaus.
    Fast Ear sees the *steps between* plateaus, which is exactly how a real
    section boundary presents in these features.
    """

    duration_s: float = DURATION_S
    sections: Sequence[tuple[float, float, float, float]] = ()
    vocal_onsets: Sequence[float] = ()
    silent_from: float | None = None
    silent_to: float | None = None

    energy: list = field(default_factory=list)
    brightness: list = field(default_factory=list)
    onset: list = field(default_factory=list)
    flux: list = field(default_factory=list)

    def __post_init__(self) -> None:
        n = int(self.duration_s)
        sections = sorted(self.sections) or [(0.0, 0.5, 0.5, 0.5)]
        for t in range(n):
            cur = sections[0]
            for s in sections:
                if s[0] <= t:
                    cur = s
            self.energy.append(cur[1])
            self.brightness.append(cur[2])
            self.onset.append(cur[3])
        # Flux is a boundary detector: it spikes *at* a change and is otherwise
        # flat, which is what makes the two-witness CANDIDATE_SECTION rule fire
        # only where a boundary and an energy step coincide.
        self.flux = [0.05] * n
        for s in sections:
            i = int(s[0])
            if 0 < i < n:
                self.flux[i] = 1.0
        if self.silent_from is not None:
            lo = int(self.silent_from)
            hi = int(self.silent_to if self.silent_to is not None else self.duration_s)
            for i in range(max(0, lo), min(n, hi)):
                self.energy[i] = 0.0

    # -- injected perception ------------------------------------------------
    def fast_ear(self, path: str, start_s: float, end_s: float):
        lo, hi = int(max(0, start_s)), int(max(0, end_s))
        return rt.fast_ear_from_series(
            start_s=float(lo),
            energy_1hz=self.energy[lo:hi],
            flux_1hz=self.flux[lo:hi],
            brightness_1hz=self.brightness[lo:hi],
            onset_1hz=self.onset[lo:hi],
        )

    def level(self, path: str, start_s: float, end_s: float) -> float | None:
        lo, hi = int(max(0, start_s)), int(max(0, end_s))
        window = self.energy[lo:hi]
        return (sum(window) / len(window)) if window else None

    def vocal(self, path: str, start_s: float, end_s: float) -> float | None:
        for onset in self.vocal_onsets:
            if start_s <= onset < end_s:
                return float(onset)
        return None


def steady_track() -> SyntheticTrack:
    return SyntheticTrack(sections=[(0.0, 0.5, 0.5, 0.5)])


def sectioned_track() -> SyntheticTrack:
    return SyntheticTrack(sections=[
        (0.0, 0.20, 0.30, 0.20),
        # Deliberately *off* the 15s window grid. A change landing exactly on a
        # grid line is invisible to the energy series in both adjacent windows
        # — `_step_points` only sees deltas inside its slice — so a grid-aligned
        # boundary can only ever be a single-witness flux spike and the
        # two-witness CANDIDATE_SECTION rule can never fire. See
        # docs/MUSIC_ENGINE_GAPS.md; pinned by
        # `test_a_boundary_on_the_window_grid_loses_its_energy_witness`.
        (47.0, 0.85, 0.70, 0.80),      # the drop
        (122.0, 0.30, 0.35, 0.30),     # the bridge
        (167.0, 0.90, 0.75, 0.85),     # final chorus
    ])


def chorus_track() -> SyntheticTrack:
    """Verse/chorus/verse/chorus with the two choruses feature-identical."""
    return SyntheticTrack(sections=[
        (0.0, 0.25, 0.30, 0.25),
        # Both choruses sit at the same offset (+2s) inside their window, so
        # they produce an identical Fast Ear signature — which is what makes
        # them recognisable as the same passage. Off-grid for the reason above.
        (32.0, 0.80, 0.70, 0.75),      # chorus 1
        (92.0, 0.25, 0.30, 0.25),
        (152.0, 0.80, 0.70, 0.75),     # chorus 2 — same step, same shape
    ])


# =============================================================================
# plumbing
# =============================================================================

class Clock:
    def __init__(self, start: int = 5_000_000) -> None:
        self.now = start

    def __call__(self) -> int:
        return self.now

    def advance(self, ms: int) -> int:
        self.now += ms
        return self.now


class DeepEar:
    def __init__(self, delay_s: float = 0.0) -> None:
        self.delay_s = delay_s
        self.calls: list[tuple[float, float]] = []

    def __call__(self, path: str, start_s: float, end_s: float) -> dict | None:
        self.calls.append((start_s, end_s))
        if self.delay_s:
            time.sleep(self.delay_s)
        return {"summary": f"deep@{start_s:.0f}", "analyzer_version": "2.0.0"}


@dataclass
class Rig:
    adapter: mra.MusicVideoInteractionAdapter
    engine: eng.RealtimeInteractionEngine
    clock: Clock
    track: SyntheticTrack
    deep: DeepEar

    async def play_to(self, media_ms: int, *, step_ms: int = 5_000,
                      consumed: bool = True) -> None:
        """Advance wall time and media time together, ticking perception."""
        pos = self.adapter.playhead.last_report
        start = pos.position_ms if pos else 0
        t = start
        while t < media_ms:
            t = min(media_ms, t + step_ms)
            self.clock.advance(step_ms)
            self.adapter.report_position(t, wall_ms=self.clock.now,
                                         consumed=consumed)
            await self.engine.perception_tick()
            await asyncio.sleep(0)

    async def seek(self, media_ms: int, *, wall_gap_ms: int = 500) -> str:
        self.clock.advance(wall_gap_ms)
        outcome = self.adapter.report_position(media_ms, wall_ms=self.clock.now)
        await self.engine.perception_tick()
        return outcome


async def build_rig(track: SyntheticTrack, *, deep_delay_s: float = 0.0,
                    detectors: bool = True, **cfg) -> Rig:
    clock = Clock()
    deep = DeepEar(deep_delay_s)
    session = rt.new_music_session(media_id=MEDIA_ID, conversation_id="replay")
    adapter = mra.MusicVideoInteractionAdapter(
        session=session,
        fast_ear_fn=track.fast_ear,
        deep_ear_fn=deep,
        acquire_fn=lambda: AUDIO,
        duration_s=track.duration_s,
        level_fn=track.level if detectors else None,
        vocal_fn=track.vocal if detectors else None,
    )
    config = eng.EngineConfig(deep_timeout_ms=10_000, fast_timeout_ms=2_000, **cfg)
    engine = eng.RealtimeInteractionEngine(session.session_id, adapter,
                                           config=config, clock_ms=clock)
    await adapter.prepare()
    await engine.start(run_perception=False)
    return Rig(adapter, engine, clock, track, deep)


@dataclass
class ScenarioResult:
    name: str
    title: str
    passed: bool
    detail: str
    snapshot: dict = field(default_factory=dict)
    checks: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "scenario": self.name, "title": self.title, "passed": self.passed,
            "detail": self.detail,
            "checks": [{"name": n, "ok": ok, "note": note}
                       for n, ok, note in self.checks],
            "snapshot": self.snapshot,
        }


class _Checker:
    def __init__(self) -> None:
        self.checks: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, note: str = "") -> bool:
        self.checks.append((name, bool(ok), note))
        return bool(ok)

    @property
    def passed(self) -> bool:
        return all(ok for _n, ok, _x in self.checks)

    @property
    def failures(self) -> str:
        bad = [f"{n} ({note})" if note else n
               for n, ok, note in self.checks if not ok]
        return "; ".join(bad) or "all checks passed"


def said(adapter, event_type: str) -> list:
    return [d for d in adapter.dispatched if d[1] == event_type]


def lane_said(adapter, lane: str) -> list:
    return [d for d in adapter.dispatched if d[0] == lane]


# =============================================================================
# scenarios
# =============================================================================

async def scenario_a_steady() -> ScenarioResult:
    """A: a steady section produces context, not chatter."""
    c = _Checker()
    rig = await build_rig(steady_track())
    await rig.play_to(60_000)
    await rig.engine.idle(timeout_s=5)
    snap = await rig.engine.aclose("done")
    c.check("fast ear ran", rig.adapter.counters.fast_ear_runs > 0,
            f"runs={rig.adapter.counters.fast_ear_runs}")
    c.check("a flat track says nothing",
            not lane_said(rig.adapter, rte.LANE_FAST),
            f"said={rig.adapter.dispatched}")
    c.check("and costs no deep passes", rig.deep.calls == [],
            f"deep={rig.deep.calls}")
    c.check("heard_anything is true", rig.adapter.heard_anything)
    return ScenarioResult("A", "steady section", c.passed, c.failures,
                          snap, c.checks)


async def scenario_b_section_transition() -> ScenarioResult:
    """B: a real section boundary is detected and promoted."""
    c = _Checker()
    rig = await build_rig(sectioned_track())
    await rig.play_to(70_000)
    await rig.engine.idle(timeout_s=5)
    snap = await rig.engine.aclose("done")
    c.check("the boundary was noticed", bool(said(rig.adapter,
                                                  mra.EVENT_SECTION_CHANGE)),
            f"said={[d[1] for d in rig.adapter.dispatched]}")
    c.check("it was worth a deep pass", len(rig.deep.calls) >= 1,
            f"deep={rig.deep.calls}")
    c.check("the deep lane spoke", bool(lane_said(rig.adapter, rte.LANE_DEEP)))
    return ScenarioResult("B", "section transition", c.passed, c.failures,
                          snap, c.checks)


async def scenario_c_rapid_energy() -> ScenarioResult:
    """C: rapid energy changes coalesce rather than firing once per step."""
    c = _Checker()
    track = SyntheticTrack(sections=[
        (float(t), 0.9 if (t // 5) % 2 else 0.1, 0.5, 0.5)
        for t in range(0, 60, 5)
    ])
    rig = await build_rig(track)
    await rig.play_to(60_000)
    await rig.engine.idle(timeout_s=6)
    snap = await rig.engine.aclose("done")
    energy_events = snap["telemetry"]["counters"].get("offer_accepted", 0)
    coalesced = (snap["telemetry"]["counters"].get("offer_suppressed", 0)
                 + snap["telemetry"]["counters"].get("offer_merged", 0)
                 + snap["telemetry"]["counters"].get("offer_escalated", 0))
    c.check("changes were detected", rig.adapter.counters.fast_ear_runs > 0)
    c.check("the deep budget held",
            len(rig.deep.calls) <= rt.DEEP_EAR_BUDGET_PER_TRACK,
            f"deep={len(rig.deep.calls)}")
    c.check("nothing was dropped silently",
            snap["fast_queue"]["dropped_backpressure"] == 0,
            f"dropped={snap['fast_queue']['dropped_backpressure']}")
    c.check("scheduler saw the traffic", energy_events + coalesced > 0)
    return ScenarioResult("C", "rapid energy change", c.passed, c.failures,
                          snap, c.checks)


async def scenario_d_vocal_entry() -> ScenarioResult:
    """D: vocal entry fires from the detector, and never without it."""
    c = _Checker()
    track = sectioned_track()
    track.vocal_onsets = (20.0,)
    rig = await build_rig(track)
    await rig.play_to(40_000)
    await rig.engine.idle(timeout_s=5)
    snap = await rig.engine.aclose("done")
    c.check("vocal entry was announced",
            bool(said(rig.adapter, mra.EVENT_VOCAL_ENTRY)),
            f"said={[d[1] for d in rig.adapter.dispatched]}")

    blind = await build_rig(sectioned_track(), detectors=False)
    blind.track.vocal_onsets = (20.0,)
    await blind.play_to(40_000)
    await blind.engine.idle(timeout_s=5)
    await blind.engine.aclose("done")
    c.check("without a detector nothing is claimed",
            not said(blind.adapter, mra.EVENT_VOCAL_ENTRY)
            and not said(blind.adapter, mra.EVENT_SILENCE),
            f"said={[d[1] for d in blind.adapter.dispatched]}")
    return ScenarioResult("D", "vocal entry", c.passed, c.failures, snap, c.checks)


async def scenario_e_pause_resume() -> ScenarioResult:
    """E: pause costs nothing and is not a seek; resume does not re-analyse."""
    c = _Checker()
    rig = await build_rig(sectioned_track())
    await rig.play_to(40_000)
    analysed = len(rig.adapter.artifacts)
    seeks_before = rig.adapter.counters.seeks

    rig.clock.advance(1_000)
    rig.adapter.report_position(41_000, wall_ms=rig.clock.now, playing=False)
    for _ in range(5):                       # a long pause, several ticks
        rig.clock.advance(30_000)
        await rig.engine.perception_tick()
    paused_artifacts = len(rig.adapter.artifacts)

    pos = rig.adapter.current_position(now_wall_ms=rig.clock.now)
    c.check("a paused playhead does not drift",
            pos is not None and pos.timestamp_ms == 41_000,
            f"pos={pos.timestamp_ms if pos else None}")
    c.check("a held position is reported, not inferred",
            pos is not None and pos.confidence == 1.0)
    c.check("pause is not a seek",
            rig.adapter.counters.seeks == seeks_before,
            f"seeks={rig.adapter.counters.seeks}")

    outcome = rig.adapter.report_position(41_000, wall_ms=rig.clock.now,
                                          playing=True)
    c.check("resume is not a seek either", outcome == "accepted", outcome)
    c.check("pause re-analysed nothing",
            paused_artifacts == analysed
            or paused_artifacts <= analysed + mra.playhead_mod.MAX_WINDOWS,
            f"{analysed} -> {paused_artifacts}")
    await rig.engine.idle(timeout_s=5)
    snap = await rig.engine.aclose("done")
    return ScenarioResult("E", "pause/resume", c.passed, c.failures, snap, c.checks)


async def scenario_f_seek_forward() -> ScenarioResult:
    """F: seek forward. The pre-seek deep result must not be spoken."""
    c = _Checker()
    rig = await build_rig(sectioned_track(), deep_delay_s=0.30)
    await rig.play_to(45_000)
    # Let a deep pass get in flight for where we are now.
    await asyncio.sleep(0.05)
    deep_before = len(lane_said(rig.adapter, rte.LANE_DEEP))
    outcome = await rig.seek(150_000)
    await rig.engine.idle(timeout_s=8)
    snap = await rig.engine.aclose("done")
    counters = snap["telemetry"]["counters"]
    stale = (counters.get("stale_analysis_dropped", 0)
             + counters.get("deep_cancelled_stale", 0))
    spoken_after = [d for d in lane_said(rig.adapter, rte.LANE_DEEP)][deep_before:]
    c.check("the seek was seen as a discontinuity", outcome == "discontinuity",
            outcome)
    c.check("generation advanced", snap["generation"] >= 1,
            f"gen={snap['generation']}")
    c.check("stale deep work was stopped", stale >= 1, f"counters={counters}")
    c.check("nothing about 00:45 was said as if it were now",
            all("deep@45" not in (d[2] or "") for d in spoken_after),
            f"spoken={spoken_after}")
    return ScenarioResult("F", "seek forward", c.passed, c.failures, snap, c.checks)


async def scenario_g_seek_backward() -> ScenarioResult:
    """G: seek backward — the case the raw Playhead cannot see."""
    c = _Checker()
    rig = await build_rig(sectioned_track())
    await rig.play_to(150_000)
    analysed = len(rig.adapter.artifacts)
    outcome = await rig.seek(20_000)
    await rig.engine.idle(timeout_s=6)
    snap = await rig.engine.aclose("done")
    c.check("a backward jump is a discontinuity", outcome == "discontinuity",
            outcome)
    c.check("it bumped the generation", snap["generation"] >= 1)
    c.check("already-analysed ground was not re-analysed",
            len(rig.adapter.artifacts) >= analysed,
            f"{analysed} -> {len(rig.adapter.artifacts)}")
    c.check("the playhead followed the listener back",
            rig.adapter.playhead.last_report.position_ms == 20_000)
    return ScenarioResult("G", "seek backward", c.passed, c.failures, snap, c.checks)


async def scenario_h_repeated_chorus() -> ScenarioResult:
    """H: the same chorus 120s later is recognised as the same passage."""
    c = _Checker()
    rig = await build_rig(chorus_track())
    await rig.play_to(200_000)
    await rig.engine.idle(timeout_s=8)
    snap = await rig.engine.aclose("done")
    c.check("the repeat was recognised",
            rig.adapter.counters.repeated_segments >= 1,
            f"repeats={rig.adapter.counters.repeated_segments}")
    c.check("and it was cheap",
            len(rig.deep.calls) <= rt.DEEP_EAR_BUDGET_PER_TRACK,
            f"deep={len(rig.deep.calls)}")
    return ScenarioResult("H", "repeated chorus", c.passed, c.failures,
                          snap, c.checks)


async def scenario_i_deep_saturation() -> ScenarioResult:
    """I: the fast lane must not slow down while the deep lane is saturated.

    The measurement that makes the migration worth doing. If Deep Ear ran on
    the event loop rather than in an executor, the fast reaction below would
    queue behind ~6 * 200ms of blocking work.
    """
    c = _Checker()
    rig = await build_rig(sectioned_track(), deep_delay_s=0.20,
                          deep_concurrency=2)
    rig.adapter.report_position(45_000, wall_ms=rig.clock.now)
    for i in range(8):
        await rig.engine.emit_type(
            mra.EVENT_DEEP_LISTEN, source=f"sat{i}",
            payload={"at_s": 45.0 + i * 0.1, "start_s": 40.0, "end_s": 55.0},
        )
    loop = asyncio.get_running_loop()
    before = len(lane_said(rig.adapter, rte.LANE_FAST))
    t0 = loop.time()
    await rig.engine.emit_type(mra.EVENT_SECTION_CHANGE, source="fast",
                               payload={"at_s": 45.0})
    while len(lane_said(rig.adapter, rte.LANE_FAST)) == before:
        if loop.time() - t0 > 3.0:
            break
        await asyncio.sleep(0.001)
    latency_ms = (loop.time() - t0) * 1000
    await rig.engine.idle(timeout_s=10)
    snap = await rig.engine.aclose("done")
    c.check("the fast lane spoke",
            len(lane_said(rig.adapter, rte.LANE_FAST)) > before)
    c.check("and it did not wait for the deep lane", latency_ms < 100,
            f"fast reaction took {latency_ms:.1f}ms under deep saturation")
    c.check("the deep lane was genuinely saturated",
            snap["telemetry"]["counters"].get("offer_accepted", 0) > 0)
    result = ScenarioResult("I", "deep saturation", c.passed, c.failures,
                            snap, c.checks)
    result.snapshot["fast_latency_under_saturation_ms"] = round(latency_ms, 2)
    return result


async def scenario_j_disconnect() -> ScenarioResult:
    """J: disconnect tears down mid-analysis without speaking into the void."""
    c = _Checker()
    rig = await build_rig(sectioned_track(), deep_delay_s=0.5)
    rig.adapter.report_position(45_000, wall_ms=rig.clock.now)
    await rig.engine.emit_type(
        mra.EVENT_DEEP_LISTEN, source="ladder",
        payload={"at_s": 45.0, "start_s": 40.0, "end_s": 55.0},
    )
    await asyncio.sleep(0.05)
    await rig.engine.emit_type(rte.EVENT_DISCONNECTED, source="transport")
    snap = rig.engine.snapshot()
    c.check("the session closed", snap["closed"])
    c.check("for the right reason", snap["close_reason"] == "disconnected",
            snap["close_reason"])
    c.check("no analysis was left in flight", snap["deep_inflight"] == 0)
    c.check("the discard was counted before it happened",
            snap["telemetry"]["counters"].get("deep_cancelled_on_close", 0) >= 1,
            f"counters={snap['telemetry']['counters']}")
    c.check("adapter state was cleared", rig.adapter._pending == [])
    return ScenarioResult("J", "disconnect", c.passed, c.failures, snap, c.checks)


SCENARIOS: tuple[tuple[str, Callable[[], Any]], ...] = (
    ("A", scenario_a_steady),
    ("B", scenario_b_section_transition),
    ("C", scenario_c_rapid_energy),
    ("D", scenario_d_vocal_entry),
    ("E", scenario_e_pause_resume),
    ("F", scenario_f_seek_forward),
    ("G", scenario_g_seek_backward),
    ("H", scenario_h_repeated_chorus),
    ("I", scenario_i_deep_saturation),
    ("J", scenario_j_disconnect),
)


async def run_all(only: Sequence[str] = ()) -> list[ScenarioResult]:
    wanted = {o.upper() for o in only}
    out = []
    for name, fn in SCENARIOS:
        if wanted and name not in wanted:
            continue
        try:
            out.append(await fn())
        except Exception as exc:  # a scenario that explodes is a failure, not a crash
            out.append(ScenarioResult(name, fn.__doc__ or "", False,
                                      f"{type(exc).__name__}: {exc}"))
    return out


# =============================================================================
# micro soak
# =============================================================================

def _rss_kb() -> int:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(rss if sys.platform != "darwin" else rss / 1024)


async def soak(seconds: float = 60.0) -> dict:
    """Repeat pause/seek/replay/section-change for `seconds`. Bounded by design.

    Deliberately a *micro* soak: minutes, not hours. It is looking for unbounded
    growth — a cache that never stops, a task that never ends, an engine that
    does not actually go away when closed — all of which show up in the first
    minute or not at all.

    **It rotates tracks, and that is not decoration.** The deep lane is capped
    at ``DEEP_EAR_BUDGET_PER_TRACK`` passes *per track*, so a single-track soak
    exercises Deep Ear at most six times no matter how long it runs — the first
    version of this function reported STABLE with ``deep_runs=0`` and was
    measuring nothing but an idle loop. Rotating the session is also the honest
    shape of the workload (a listener moves between songs) and puts engine
    churn — build, run, close, discard — under the leak check, which is where a
    realtime system actually leaks.
    """
    gc.collect()
    rss0 = _rss_kb()
    tasks0 = len(asyncio.all_tasks())
    started = time.monotonic()
    cycles = 0
    samples: list[dict] = []
    totals = {"deep_runs": 0, "deep_over_budget": 0, "deep_off_position": 0,
              "seeks": 0, "repeats": 0, "fast_ear_runs": 0,
              "stale_dropped": 0, "cancelled_stale": 0, "fast_said": 0}
    coalesced: dict[str, int] = {}
    ceiling_breaches: list[str] = []

    while time.monotonic() - started < seconds:
        cycles += 1
        track = chorus_track() if cycles % 2 else sectioned_track()
        # Deep Ear slow enough that a seek reliably lands while an analysis is
        # in flight. With a fast Deep Ear and a settle before every seek, the
        # stale path is never entered and the soak reports STABLE having never
        # tested the one behaviour a seek exists to trigger.
        rig = await build_rig(track, deep_delay_s=0.05)
        # Each cycle is a whole listening session: play, pause, resume, seek
        # forward, play on, replay from the top.
        # Settled: these analyses complete, so the soak measures the deep lane
        # actually doing work.
        await rig.play_to(60_000, step_ms=10_000)
        await rig.engine.idle(timeout_s=5)

        # Unsettled: seek straight out of a tick so an analysis is in flight
        # when the generation turns over, and the stale path is measured too.
        await rig.play_to(120_000, step_ms=10_000)
        await rig.seek(30_000)

        rig.clock.advance(1_000)
        rig.adapter.report_position(31_000, wall_ms=rig.clock.now, playing=False)
        for _ in range(3):
            rig.clock.advance(30_000)
            await rig.engine.perception_tick()
        rig.adapter.report_position(31_000, wall_ms=rig.clock.now, playing=True)

        await rig.seek(160_000)
        await rig.play_to(190_000, step_ms=10_000)
        await rig.engine.idle(timeout_s=5)
        await rig.seek(5_000)
        await rig.play_to(40_000, step_ms=10_000)
        await rig.engine.idle(timeout_s=5)

        # A track has a finite number of 15s windows; one session's cache must
        # converge on that. Checked per session, because a global count over
        # rotating tracks would hide a cache keyed on something transient.
        max_windows = int(track.duration_s / mra.playhead_mod.WINDOW_S) + 2
        if len(rig.adapter.artifacts) > max_windows:
            ceiling_breaches.append(
                f"cycle {cycles}: {len(rig.adapter.artifacts)} artifacts for "
                f"~{max_windows} windows"
            )

        c = rig.adapter.counters
        totals["deep_runs"] += c.deep_ear_runs
        totals["deep_over_budget"] += c.deep_ear_over_budget
        totals["deep_off_position"] += c.deep_ear_off_position
        totals["seeks"] += c.seeks
        totals["repeats"] += c.repeated_segments
        totals["fast_ear_runs"] += c.fast_ear_runs
        totals["fast_said"] += len(lane_said(rig.adapter, rte.LANE_FAST))

        snap = await rig.engine.aclose("cycle done")
        counters = snap["telemetry"]["counters"]
        totals["stale_dropped"] += counters.get("stale_analysis_dropped", 0)
        totals["cancelled_stale"] += counters.get("deep_cancelled_stale", 0)
        for k, v in counters.items():
            if k.startswith("offer_"):
                coalesced[k] = coalesced.get(k, 0) + v
        if snap["fast_queue"]["depth"] or snap["deep_queue"]["depth"]:
            ceiling_breaches.append(f"cycle {cycles}: queues did not drain")

        del rig
        if cycles % 5 == 0:
            gc.collect()
            samples.append({
                "cycle": cycles,
                "rss_kb": _rss_kb(),
                "tasks": len(asyncio.all_tasks()),
                "deep_runs": totals["deep_runs"],
            })

    gc.collect()
    rss1 = _rss_kb()
    tasks1 = len(asyncio.all_tasks())

    reasons = list(ceiling_breaches[:5])
    if tasks1 > tasks0 + 2:
        reasons.append(f"task count {tasks0} -> {tasks1}: engines are not going away")
    # RSS over a rotating workload is the leak signal. A flat allowance would
    # fail on a short soak's allocator warm-up, so this is proportional with an
    # absolute floor.
    if rss1 > rss0 * 1.6 and rss1 - rss0 > 80_000:
        reasons.append(f"RSS {rss0}kB -> {rss1}kB across {cycles} sessions")
    if cycles and totals["deep_runs"] == 0:
        reasons.append("the deep lane never ran — this soak measured nothing")
    if cycles and totals["fast_said"] == 0:
        reasons.append("the fast lane never spoke — this soak measured nothing")
    # The soak seeks constantly; if that never cancelled an in-flight analysis,
    # the seek/stale path was not exercised and a STABLE verdict would be
    # overclaiming on the axis the seeks were there to test.
    if totals["seeks"] and not (totals["cancelled_stale"] + totals["stale_dropped"]):
        reasons.append(
            f"{totals['seeks']} seeks cancelled no deep work — the stale path "
            f"was never entered, so this soak did not measure it"
        )

    return {
        "seconds": round(time.monotonic() - started, 1),
        "cycles": cycles,
        "verdict": "STABLE" if not reasons else "UNSTABLE",
        "reasons": reasons,
        "rss_kb": {"start": rss0, "end": rss1, "delta": rss1 - rss0},
        "rss_kb_per_session": (
            round((rss1 - rss0) / cycles, 2) if cycles else None
        ),
        "tasks": {"start": tasks0, "end": tasks1},
        "totals": totals,
        "coalesced": coalesced,
        "samples": samples,
    }


# =============================================================================
# cli
# =============================================================================

def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--only", nargs="*", default=())
    ap.add_argument("--soak", type=float, default=0.0,
                    help="run the micro soak for N seconds instead of A~J")
    args = ap.parse_args(argv)

    if args.soak:
        report = asyncio.run(soak(args.soak))
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print(f"soak {report['verdict']} — {report['cycles']} cycles in "
                  f"{report['seconds']}s")
            print(f"  RSS {report['rss_kb']['start']} -> {report['rss_kb']['end']}kB "
                  f"({report['rss_kb']['delta']:+d})")
            print(f"  tasks {report['tasks']['start']} -> {report['tasks']['end']}")
            print(f"  RSS/session {report['rss_kb_per_session']}kB")
            t = report['totals']
            print(f"  seeks={t['seeks']} stale_dropped={t['stale_dropped']} "
                  f"cancelled_stale={t['cancelled_stale']}")
            print(f"  deep runs={t['deep_runs']} over_budget={t['deep_over_budget']} "
                  f"off_position={t['deep_off_position']}")
            print(f"  fast: ran={t['fast_ear_runs']} said={t['fast_said']} "
                  f"repeats={t['repeats']}")
            print(f"  coalescing={report['coalesced']}")
            for reason in report["reasons"]:
                print(f"  ! {reason}")
        return 0 if report["verdict"] == "STABLE" else 1

    results = asyncio.run(run_all(args.only))
    if args.json:
        print(json.dumps([r.to_dict() for r in results], indent=2,
                         ensure_ascii=False))
    else:
        for r in results:
            print(f"[{'PASS' if r.passed else 'FAIL'}] {r.name}  {r.title}")
            if not r.passed:
                print(f"       {r.detail}")
            for name, ok, note in r.checks:
                if not ok:
                    print(f"         - {name}: {note}")
        ok = sum(1 for r in results if r.passed)
        print(f"\n{ok}/{len(results)} scenarios passed")
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
