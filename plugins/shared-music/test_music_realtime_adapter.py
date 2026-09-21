"""Music/video adapter on the canonical realtime engine.

Everything perceptual is injected, exactly as `test_music_realtime` does it:
`fast_ear_fn`, `deep_ear_fn`, `acquire_fn`, `level_fn` and `vocal_fn` are
constructor arguments, so these tests drive the real scheduling, promotion,
caching, budgeting and staleness logic without numpy, scipy, soundfile or the
Deep Ear venv — none of which exist in the interpreter these suites run under.

The engine's clock is injected too, so nothing here sleeps to make time pass.
Wall time and media time are separate axes and the tests move them
independently, which is the only way to test a seek at all.
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
for extra in (HERE, HERE.parent / "_shared"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import music_realtime as rt  # noqa: E402
import music_realtime_adapter as mra  # noqa: E402
import hermes_realtime as rte  # noqa: E402
import hermes_realtime_engine as eng  # noqa: E402

MEDIA_ID = "music:abc123"
DURATION_S = 337.0
AUDIO = "/tmp/not-really-audio.opus"


class Clock:
    """A wall clock the test advances by hand."""

    def __init__(self, start: int = 1_000_000) -> None:
        self.now = start

    def __call__(self) -> int:
        return self.now

    def advance(self, ms: int) -> int:
        self.now += ms
        return self.now


def candidates_at(*specs) -> tuple:
    """(kind, at_s, salience) triples -> FastEarCandidates."""
    return tuple(
        rt.FastEarCandidate(kind=k, at_s=float(t), salience=float(s), detail="test")
        for k, t, s in specs
    )


class Ears:
    """Scripted Fast/Deep Ear with call recording.

    `fast_plan` maps a window start (int seconds) to the candidates that window
    yields; anything unscripted yields nothing, which is the common case and
    keeps a test's script to the windows it actually cares about.
    """

    def __init__(self, fast_plan=None, deep_result=None, deep_delay_s=0.0):
        self.fast_plan = dict(fast_plan or {})
        self.deep_result = deep_result if deep_result is not None else {
            "summary": "브리지에서 드럼 빠짐", "analyzer_version": "2.0.0"
        }
        self.deep_delay_s = deep_delay_s
        self.fast_calls: list[tuple[float, float]] = []
        self.deep_calls: list[tuple[float, float]] = []

    def fast(self, path, start_s, end_s):
        self.fast_calls.append((start_s, end_s))
        return self.fast_plan.get(int(start_s), ())

    def deep(self, path, start_s, end_s):
        self.deep_calls.append((start_s, end_s))
        if self.deep_delay_s:
            import time
            time.sleep(self.deep_delay_s)
        return self.deep_result


def build(ears: Ears | None = None, *, clock: Clock | None = None,
          acquire=lambda: AUDIO, level_fn=None, vocal_fn=None,
          deep_ear=True, **config):
    ears = ears or Ears()
    clock = clock or Clock()
    session = rt.new_music_session(media_id=MEDIA_ID, conversation_id="conv1")
    adapter = mra.MusicVideoInteractionAdapter(
        session=session,
        fast_ear_fn=ears.fast,
        deep_ear_fn=ears.deep if deep_ear else None,
        acquire_fn=acquire,
        duration_s=DURATION_S,
        level_fn=level_fn,
        vocal_fn=vocal_fn,
    )
    cfg = eng.EngineConfig(deep_timeout_ms=5_000, fast_timeout_ms=2_000, **config)
    engine = eng.RealtimeInteractionEngine(
        session.session_id, adapter, config=cfg, clock_ms=clock
    )
    return adapter, engine, ears, clock


def run(coro):
    return asyncio.run(coro)


# =============================================================================
# 1. registration
# =============================================================================

class RegistrationTests(unittest.TestCase):
    def test_the_adapter_registers_its_vocabulary(self):
        adapter, engine, _e, _c = build()
        types = engine.registry.types_for("music")
        self.assertIn(mra.EVENT_SECTION_CHANGE, types)
        self.assertIn(mra.EVENT_DEEP_LISTEN, types)
        self.assertEqual(len(types), len(mra.MUSIC_SPECS))

    def test_every_music_type_is_namespaced(self):
        for spec in mra.MUSIC_SPECS:
            self.assertTrue(
                spec.event_type.startswith("music:"),
                f"{spec.event_type} would be rejected by the engine's namespace check",
            )

    def test_fast_signals_are_on_the_fast_lane_and_deep_listen_is_not(self):
        by_type = {s.event_type: s for s in mra.MUSIC_SPECS}
        for t in (mra.EVENT_SECTION_CHANGE, mra.EVENT_ENERGY_CHANGE,
                  mra.EVENT_RHYTHM_CHANGE, mra.EVENT_VOCAL_ENTRY,
                  mra.EVENT_SILENCE, mra.EVENT_REPEATED_SEGMENT):
            self.assertEqual(by_type[t].lane, rte.LANE_FAST, t)
        self.assertEqual(by_type[mra.EVENT_DEEP_LISTEN].lane, rte.LANE_DEEP)

    def test_playhead_jump_is_context_only(self):
        by_type = {s.event_type: s for s in mra.MUSIC_SPECS}
        self.assertEqual(by_type[mra.EVENT_PLAYHEAD_JUMP].lane, rte.LANE_NONE,
                         "a seek asks nobody to react; it changes what we know")


# =============================================================================
# 2-5. playhead: advance, pause, seek, replay
# =============================================================================

class PlayheadTests(unittest.TestCase):
    def setUp(self):
        self.adapter, self.engine, self.ears, self.clock = build()

    def test_advance_is_not_a_discontinuity(self):
        self.adapter.report_position(0, wall_ms=self.clock.now)
        self.clock.advance(10_000)
        outcome = self.adapter.report_position(10_000, wall_ms=self.clock.now)
        self.assertEqual(outcome, "accepted")
        self.assertEqual(self.adapter.counters.seeks, 0)

    def test_small_drift_is_not_a_seek(self):
        self.adapter.report_position(0, wall_ms=self.clock.now)
        self.clock.advance(10_000)
        # 1.2s of clock/rate error is ordinary and must not turn the world over.
        self.assertEqual(
            self.adapter.report_position(11_200, wall_ms=self.clock.now), "accepted"
        )

    def test_pause_holds_the_position_and_is_not_a_seek(self):
        self.adapter.report_position(30_000, wall_ms=self.clock.now, playing=True)
        self.clock.advance(1_000)
        self.adapter.report_position(31_000, wall_ms=self.clock.now, playing=False)
        self.clock.advance(120_000)          # two minutes paused
        pos = self.adapter.current_position(now_wall_ms=self.clock.now)
        self.assertEqual(pos.timestamp_ms, 31_000,
                         "a paused listener has not moved, so nothing is inferred")
        self.assertEqual(pos.confidence, 1.0,
                         "a held position is reported, not projected")
        outcome = self.adapter.report_position(31_000, wall_ms=self.clock.now,
                                               playing=True)
        self.assertEqual(outcome, "accepted", "resuming where you paused is not a seek")
        self.assertEqual(self.adapter.counters.seeks, 0)

    def test_forward_seek_is_a_discontinuity(self):
        self.adapter.report_position(45_000, wall_ms=self.clock.now)
        self.clock.advance(1_000)
        outcome = self.adapter.report_position(90_000, wall_ms=self.clock.now)
        self.assertEqual(outcome, "discontinuity")
        self.assertEqual(self.adapter.counters.seeks, 1)

    def test_backward_seek_is_a_discontinuity(self):
        """The case `Playhead` alone cannot see.

        A backward jump carrying a *higher* sequence_no is accepted by
        `Playhead.report` and is indistinguishable from progress; only the
        projection comparison catches it.
        """
        self.adapter.report_position(90_000, wall_ms=self.clock.now, sequence_no=1)
        self.clock.advance(1_000)
        outcome = self.adapter.report_position(10_000, wall_ms=self.clock.now,
                                               sequence_no=2)
        self.assertEqual(outcome, "discontinuity")
        self.assertTrue(self.adapter.playhead.report(
            __import__("music_playhead").PlayheadReport(
                position_ms=10_000, wall_ms=self.clock.now, sequence_no=3)),
            "sanity: the raw playhead would have accepted this silently")

    def test_replay_from_the_top_is_a_discontinuity(self):
        self.adapter.report_position(300_000, wall_ms=self.clock.now, sequence_no=1)
        self.clock.advance(500)
        self.assertEqual(
            self.adapter.report_position(0, wall_ms=self.clock.now, sequence_no=2),
            "discontinuity",
        )

    def test_out_of_order_reports_are_dropped_without_a_phantom_seek(self):
        self.adapter.report_position(50_000, wall_ms=self.clock.now, sequence_no=5)
        self.clock.advance(1_000)
        outcome = self.adapter.report_position(10_000, wall_ms=self.clock.now,
                                               sequence_no=3)
        self.assertEqual(outcome, "stale")
        self.assertEqual(self.adapter.counters.seeks, 0,
                         "a late packet must not be reported as the user seeking")
        self.assertEqual(self.adapter.counters.stale_reports_dropped, 1)


# =============================================================================
# 6-7. section change, repeated segment
# =============================================================================

class DetectionTests(unittest.TestCase):
    def test_a_section_transition_reaches_the_fast_lane(self):
        ears = Ears(fast_plan={0: candidates_at((rt.CANDIDATE_SECTION, 7.0, 0.9))})

        async def scenario():
            adapter, engine, _e, clock = build(ears)
            await adapter.prepare()
            adapter.report_position(0, wall_ms=clock.now)
            await engine.start(run_perception=False)
            await engine.perception_tick()
            await engine.idle(timeout_s=3)
            snap = await engine.aclose("done")
            return adapter, snap

        adapter, _snap = run(scenario())
        fast = [d for d in adapter.dispatched if d[0] == rte.LANE_FAST]
        self.assertTrue(any(t == mra.EVENT_SECTION_CHANGE for _l, t, _x in fast),
                        f"dispatched={adapter.dispatched}")

    def test_both_witnesses_of_a_section_fold_into_one_event(self):
        """BOUNDARY and SECTION at the same moment are one section change."""
        ears = Ears(fast_plan={0: candidates_at(
            (rt.CANDIDATE_BOUNDARY, 7.0, 0.6),
            (rt.CANDIDATE_SECTION, 7.0, 0.8),
        )})

        async def scenario():
            adapter, engine, _e, clock = build(ears, deep_ear=False)
            await adapter.prepare()
            adapter.report_position(0, wall_ms=clock.now)
            await engine.start(run_perception=False)
            await engine.perception_tick()
            await engine.idle(timeout_s=3)
            await engine.aclose("done")
            return adapter

        adapter = run(scenario())
        sections = [d for d in adapter.dispatched if d[1] == mra.EVENT_SECTION_CHANGE]
        self.assertEqual(len(sections), 1,
                         "two witnesses of one boundary must not be said twice")

    def test_low_salience_candidates_are_context_not_utterances(self):
        ears = Ears(fast_plan={0: candidates_at((rt.CANDIDATE_ENERGY, 3.0, 0.05))})

        async def scenario():
            adapter, engine, _e, clock = build(ears, deep_ear=False)
            await adapter.prepare()
            adapter.report_position(0, wall_ms=clock.now)
            await engine.start(run_perception=False)
            await engine.perception_tick()
            await engine.idle(timeout_s=2)
            await engine.aclose("done")
            return adapter

        adapter = run(scenario())
        self.assertEqual(adapter.dispatched, [])

    def test_a_repeated_chorus_is_recognised_at_a_different_position(self):
        sig = ((rt.CANDIDATE_ENERGY, 3.0, 0.7), (rt.CANDIDATE_ONSET, 5.0, 0.6))
        ears = Ears(fast_plan={
            0: candidates_at(*sig),
            60: candidates_at(*[(k, t + 60.0, s) for k, t, s in sig]),
        })

        async def scenario():
            adapter, engine, _e, clock = build(ears, deep_ear=False)
            await adapter.prepare()
            await engine.start(run_perception=False)
            adapter.report_position(0, wall_ms=clock.now)
            await engine.perception_tick()
            clock.advance(60_000)
            adapter.report_position(60_000, wall_ms=clock.now)
            await engine.perception_tick()
            await engine.idle(timeout_s=3)
            await engine.aclose("done")
            return adapter

        adapter = run(scenario())
        self.assertEqual(adapter.counters.repeated_segments, 1,
                         "the same signature 60s later is the chorus coming round")
        self.assertTrue(any(t == mra.EVENT_REPEATED_SEGMENT
                            for _l, t, _x in adapter.dispatched))

    def test_a_featureless_window_does_not_repeat_every_other_one(self):
        ears = Ears(fast_plan={0: (), 60: ()})

        async def scenario():
            adapter, engine, _e, clock = build(ears, deep_ear=False)
            await adapter.prepare()
            await engine.start(run_perception=False)
            adapter.report_position(0, wall_ms=clock.now)
            await engine.perception_tick()
            clock.advance(60_000)
            adapter.report_position(60_000, wall_ms=clock.now)
            await engine.perception_tick()
            await engine.aclose("done")
            return adapter

        adapter = run(scenario())
        self.assertEqual(adapter.counters.repeated_segments, 0)

    def test_a_boundary_on_the_window_grid_loses_its_energy_witness(self):
        """A pre-existing Fast Ear defect, pinned rather than fixed here.

        `_step_points` only sees deltas *inside* its slice, and windows are
        aligned to a fixed 15s grid. A change landing exactly on a grid line is
        therefore invisible to the energy series in both adjacent windows: the
        earlier window ends before the step, the later one starts after it. Only
        flux survives (it is a spike *at* an index, not a delta between two), so
        a grid-aligned boundary can only ever be a single-witness
        `structural_boundary` and the two-witness `section_transition` rule
        cannot fire for it.

        Consequence: whether a real section boundary gets the higher salience
        that earns a Deep Ear pass depends on where it happens to fall relative
        to an arbitrary grid. The remedy is one sample of overlap when slicing
        (`series[lo-1:hi]`), which belongs in `music_realtime.default_fast_ear`
        and not on a migration branch. Recorded in docs/MUSIC_ENGINE_GAPS.md.
        """
        energy = [0.25] * 30 + [0.80] * 30
        flux = [0.05] * 60
        flux[30] = 1.0

        before = rt.fast_ear_from_series(
            start_s=15, energy_1hz=energy[15:30], flux_1hz=flux[15:30])
        self.assertEqual(before, (), "the step is after this window ends")

        after = rt.fast_ear_from_series(
            start_s=30, energy_1hz=energy[30:45], flux_1hz=flux[30:45])
        kinds = {c.kind for c in after}
        self.assertIn(rt.CANDIDATE_BOUNDARY, kinds)
        self.assertNotIn(rt.CANDIDATE_ENERGY, kinds,
                         "the energy step spanned the grid line and was lost")
        self.assertNotIn(rt.CANDIDATE_SECTION, kinds,
                         "so the two-witness rule cannot fire for it")

        # Two seconds later — the same musical event, inside a window — and both
        # witnesses appear.
        shifted = [0.25] * 32 + [0.80] * 28
        inside = rt.fast_ear_from_series(
            start_s=30, energy_1hz=shifted[30:45], flux_1hz=flux[30:45])
        self.assertIn(rt.CANDIDATE_ENERGY, {c.kind for c in inside})

    def test_silence_and_vocals_are_never_inferred_without_a_detector(self):
        """No level function, no silence claim. Not even from an empty window."""
        ears = Ears(fast_plan={0: ()})

        async def scenario():
            adapter, engine, _e, clock = build(ears, deep_ear=False)
            await adapter.prepare()
            adapter.report_position(0, wall_ms=clock.now)
            await engine.start(run_perception=False)
            await engine.perception_tick()
            await engine.idle(timeout_s=2)
            await engine.aclose("done")
            return adapter

        adapter = run(scenario())
        for _lane, event_type, _text in adapter.dispatched:
            self.assertNotIn(event_type, (mra.EVENT_SILENCE, mra.EVENT_VOCAL_ENTRY))

    def test_silence_is_emitted_when_a_level_detector_says_so(self):
        ears = Ears(fast_plan={0: ()})

        async def scenario():
            adapter, engine, _e, clock = build(
                ears, deep_ear=False, level_fn=lambda p, a, b: 0.0
            )
            await adapter.prepare()
            adapter.report_position(0, wall_ms=clock.now)
            await engine.start(run_perception=False)
            await engine.perception_tick()
            await engine.idle(timeout_s=2)
            await engine.aclose("done")
            return adapter

        adapter = run(scenario())
        self.assertTrue(any(t == mra.EVENT_SILENCE for _l, t, _x in adapter.dispatched))


# =============================================================================
# 8-9. fast/deep separation, escalation, the ladder
# =============================================================================

class LadderTests(unittest.TestCase):
    def test_a_salient_candidate_is_promoted_to_the_deep_lane(self):
        ears = Ears(fast_plan={0: candidates_at((rt.CANDIDATE_SECTION, 7.0, 0.9))})

        async def scenario():
            adapter, engine, e, clock = build(ears)
            await adapter.prepare()
            adapter.report_position(0, wall_ms=clock.now)
            await engine.start(run_perception=False)
            await engine.perception_tick()
            await engine.idle(timeout_s=3)
            await engine.aclose("done")
            return adapter, e

        adapter, ears_ = run(scenario())
        self.assertEqual(len(ears_.deep_calls), 1, "the ladder should have promoted once")
        self.assertEqual(adapter.counters.deep_ear_runs, 1)
        self.assertTrue(any(l == rte.LANE_DEEP for l, _t, _x in adapter.dispatched))

    def test_a_candidate_below_the_floor_is_never_promoted(self):
        # Above FAST_EVENT_FLOOR (0.20) so it is spoken, below
        # DEEP_EAR_SALIENCE_FLOOR (0.35) so it is not analysed. The two floors
        # answer different questions and this is the gap between them.
        ears = Ears(fast_plan={0: candidates_at((rt.CANDIDATE_ENERGY, 3.0, 0.30))})

        async def scenario():
            adapter, engine, e, clock = build(ears)
            await adapter.prepare()
            adapter.report_position(0, wall_ms=clock.now)
            await engine.start(run_perception=False)
            await engine.perception_tick()
            await engine.idle(timeout_s=3)
            await engine.aclose("done")
            return e

        self.assertEqual(run(scenario()).deep_calls, [])

    def test_the_per_track_budget_holds_across_passes(self):
        """The budget is per track, not per pass — spacing counts `_deep_done_s`."""
        plan = {}
        for i in range(12):
            start = i * 15
            plan[start] = candidates_at(
                (rt.CANDIDATE_SECTION, start + 7.0, 0.95)
            )
        ears = Ears(fast_plan=plan)

        async def scenario():
            adapter, engine, e, clock = build(ears)
            await adapter.prepare()
            await engine.start(run_perception=False)
            for i in range(12):
                adapter.report_position(i * 15_000, wall_ms=clock.now)
                clock.advance(15_000)
                await engine.perception_tick()
                await engine.idle(timeout_s=3)
            await engine.aclose("done")
            return e

        ears_ = run(scenario())
        self.assertLessEqual(len(ears_.deep_calls), rt.DEEP_EAR_BUDGET_PER_TRACK,
                             f"deep calls={len(ears_.deep_calls)}")

    def test_escalation_cannot_walk_past_the_budget(self):
        """The turnstile.

        The engine's persistence escalation is a second entrance to the deep
        lane that never consulted `select_deep_ear_targets`. Emitting energy
        changes directly — as the escalation path does — must still respect the
        per-track budget, or the ladder is decorative.
        """
        async def scenario():
            adapter, engine, e, clock = build(Ears())
            await adapter.prepare()
            adapter.report_position(0, wall_ms=clock.now)
            await engine.start(run_perception=False)
            # 40 energy changes on one dedupe key: folds, escalates repeatedly.
            for _ in range(40):
                await engine.emit_type(
                    mra.EVENT_ENERGY_CHANGE, source="music/fast_ear",
                    payload={"at_s": 10.0, "start_s": 5.0, "end_s": 20.0},
                )
            await engine.idle(timeout_s=6)
            await engine.aclose("done")
            return adapter, e

        adapter, ears_ = run(scenario())
        self.assertLessEqual(len(ears_.deep_calls), rt.DEEP_EAR_BUDGET_PER_TRACK,
                             f"escalation spent {len(ears_.deep_calls)} deep passes")
        self.assertGreater(adapter.counters.deep_ear_over_budget, 0,
                           "the turnstile should have refused at least once")

    def test_fast_lane_survives_a_saturated_deep_lane(self):
        """Fast Ear must not be held up by Deep Ear. The point of the migration.

        Deep Ear here blocks a real thread for 150ms a call. If it ran on the
        event loop instead of in an executor, the fast dispatch below would
        queue behind it and this assertion would fail.
        """
        ears = Ears(deep_delay_s=0.15)

        async def scenario():
            adapter, engine, e, clock = build(ears, deep_concurrency=2)
            await adapter.prepare()
            adapter.report_position(0, wall_ms=clock.now)
            await engine.start(run_perception=False)
            for i in range(6):
                await engine.emit_type(
                    mra.EVENT_DEEP_LISTEN, source=f"ladder{i}",
                    payload={"at_s": i * 40.0, "start_s": i * 40.0,
                             "end_s": i * 40.0 + 15.0},
                )
            loop = asyncio.get_running_loop()
            t0 = loop.time()
            await engine.emit_type(mra.EVENT_SECTION_CHANGE, source="fast",
                                   payload={"at_s": 1.0})
            while not any(l == rte.LANE_FAST for l, _t, _x in adapter.dispatched):
                if loop.time() - t0 > 3.0:
                    break
                await asyncio.sleep(0.002)
            elapsed = loop.time() - t0
            await engine.idle(timeout_s=8)
            await engine.aclose("done")
            return adapter, elapsed

        adapter, elapsed = run(scenario())
        self.assertTrue(any(l == rte.LANE_FAST for l, _t, _x in adapter.dispatched),
                        "the fast lane never spoke")
        self.assertLess(elapsed, 0.12,
                        f"fast reaction waited {elapsed*1000:.0f}ms behind deep work")


# =============================================================================
# 10. stale pre-seek deep result
# =============================================================================

class StaleTests(unittest.TestCase):
    def test_a_pre_seek_deep_result_is_never_spoken(self):
        """00:45 analysis in flight, user seeks to 01:30.

        The 00:45 result must not surface as a statement about now. The engine
        cancels on the generation bump; the position guard is the backstop.
        """
        ears = Ears(deep_delay_s=0.25)

        async def scenario():
            adapter, engine, e, clock = build(ears)
            await adapter.prepare()
            await engine.start(run_perception=False)
            adapter.report_position(45_000, wall_ms=clock.now, sequence_no=1)
            await engine.emit_type(
                mra.EVENT_DEEP_LISTEN, source="ladder",
                payload={"at_s": 45.0, "start_s": 40.0, "end_s": 55.0},
            )
            await asyncio.sleep(0.05)          # let it get in flight
            clock.advance(1_000)
            outcome = adapter.report_position(90_000, wall_ms=clock.now,
                                              sequence_no=2)
            await engine.perception_tick()      # emits the playhead jump
            await engine.idle(timeout_s=5)
            snap = await engine.aclose("done")
            return adapter, snap, outcome

        adapter, snap, outcome = run(scenario())
        self.assertEqual(outcome, "discontinuity")
        counters = snap["telemetry"]["counters"]
        stopped = (counters.get("stale_analysis_dropped", 0)
                   + counters.get("deep_cancelled_stale", 0))
        self.assertGreaterEqual(stopped, 1, f"counters={counters}")
        deep_said = [d for d in adapter.dispatched if d[0] == rte.LANE_DEEP]
        self.assertEqual(deep_said, [], "a pre-seek analysis was spoken as current")

    def test_cancelling_a_deep_analysis_stops_the_result_not_the_work(self):
        """Cancellation is a correctness guarantee, not a cost saving.

        `run_in_executor` hands work to a thread and Python cannot interrupt a
        running thread. When a seek cancels an in-flight deep analysis, the
        awaiting coroutine raises `CancelledError` immediately — so the result
        is never recorded, never cached and never spoken, which is the property
        that matters — but the Deep Ear call itself runs to completion in the
        background and its cost is fully paid.

        For the real Deep Ear (a Demucs subprocess, seconds) that means a user
        seeking repeatedly spawns subprocesses that all run to the end. Pinned
        here so the behaviour is a known quantity rather than a surprise;
        recorded as a gap in docs/MUSIC_ENGINE_GAPS.md.
        """
        ears = Ears(deep_delay_s=0.30)

        async def scenario():
            adapter, engine, e, clock = build(ears)
            await adapter.prepare()
            await engine.start(run_perception=False)
            adapter.report_position(45_000, wall_ms=clock.now, sequence_no=1)
            await engine.emit_type(
                mra.EVENT_DEEP_LISTEN, source="ladder",
                payload={"at_s": 45.0, "start_s": 40.0, "end_s": 55.0},
            )
            await asyncio.sleep(0.05)
            clock.advance(1_000)
            adapter.report_position(200_000, wall_ms=clock.now, sequence_no=2)
            await engine.perception_tick()          # the jump cancels it
            await engine.idle(timeout_s=3)
            await asyncio.sleep(0.35)               # outlive the executor call
            await engine.aclose("done")
            return adapter, e

        adapter, ears_ = run(scenario())
        self.assertEqual(len(ears_.deep_calls), 1,
                         "the thread was started, so the cost was incurred")
        self.assertEqual(adapter.counters.deep_ear_runs, 0,
                         "but the result must not have been recorded")
        self.assertEqual([d for d in adapter.dispatched if d[0] == rte.LANE_DEEP], [],
                         "and it must not have been spoken")
        self.assertEqual(len(adapter.artifacts.covered_ranges(
            producer=rt.DEEP_EAR_PRODUCER)), 0,
            "nor cached as though it were a completed analysis")

    def test_a_result_about_somewhere_else_is_refused_even_without_a_seek(self):
        """The domain guard: generation catches seeks, position catches distance."""
        async def scenario():
            adapter, engine, e, clock = build(Ears())
            await adapter.prepare()
            await engine.start(run_perception=False)
            adapter.report_position(200_000, wall_ms=clock.now)
            await engine.emit_type(
                mra.EVENT_DEEP_LISTEN, source="ladder",
                payload={"at_s": 12.0, "start_s": 7.0, "end_s": 22.0},
            )
            await engine.idle(timeout_s=3)
            await engine.aclose("done")
            return adapter, e

        adapter, ears_ = run(scenario())
        self.assertEqual(ears_.deep_calls, [],
                         "analysing a moment 3 minutes from the listener")
        self.assertEqual(adapter.counters.deep_ear_off_position, 1)

    def test_a_seek_discards_candidates_queued_for_the_abandoned_moment(self):
        ears = Ears(fast_plan={0: candidates_at((rt.CANDIDATE_SECTION, 7.0, 0.9))})

        async def scenario():
            adapter, engine, e, clock = build(ears, deep_ear=False)
            await adapter.prepare()
            await engine.start(run_perception=False)
            adapter.report_position(0, wall_ms=clock.now)
            adapter.perceive(engine.context, clock.now)      # candidates pending
            clock.advance(1_000)
            adapter.report_position(200_000, wall_ms=clock.now)
            await engine.perception_tick()
            await engine.idle(timeout_s=3)
            await engine.aclose("done")
            return adapter

        adapter = run(scenario())
        self.assertFalse(
            any(t == mra.EVENT_SECTION_CHANGE for _l, t, _x in adapter.dispatched),
            "a candidate from before the seek was spoken after it",
        )


# =============================================================================
# 11. cache and context
# =============================================================================

class CacheTests(unittest.TestCase):
    def test_replay_reuses_the_cache_instead_of_re_analysing(self):
        ears = Ears(fast_plan={0: candidates_at((rt.CANDIDATE_ENERGY, 3.0, 0.5))})

        async def scenario():
            adapter, engine, e, clock = build(ears, deep_ear=False)
            await adapter.prepare()
            await engine.start(run_perception=False)
            adapter.report_position(0, wall_ms=clock.now)
            await engine.perception_tick()
            first = len(e.fast_calls)
            clock.advance(60_000)
            adapter.report_position(60_000, wall_ms=clock.now)   # seek forward
            await engine.perception_tick()
            clock.advance(1_000)
            adapter.report_position(0, wall_ms=clock.now)        # replay
            await engine.perception_tick()
            before_replay = len(adapter.artifacts)
            await engine.idle(timeout_s=3)
            await engine.aclose("done")
            return adapter, e, first, before_replay

        adapter, ears_, first, before_replay = run(scenario())
        replayed = [c for c in ears_.fast_calls[first:] if c[0] == 0.0]
        self.assertEqual(replayed, [],
                         "replaying an analysed region re-ran Fast Ear")
        # Reuse happens at *planning* time, not at cache-probe time:
        # `plan_ahead_windows(covered=...)` subtracts analysed ranges, so a
        # replayed window is never planned and `_fast_ear_window` — and its
        # `fast_ear_skipped_cached` counter — is never reached. The observable
        # property is that no new artifact appeared.
        self.assertEqual(len(adapter.artifacts), before_replay)

    def test_a_deep_window_already_analysed_is_not_analysed_twice(self):
        async def scenario():
            adapter, engine, e, clock = build(Ears())
            await adapter.prepare()
            await engine.start(run_perception=False)
            adapter.report_position(45_000, wall_ms=clock.now)
            for _ in range(2):
                await engine.emit_type(
                    mra.EVENT_DEEP_LISTEN, source="ladder",
                    payload={"at_s": 45.0, "start_s": 40.0, "end_s": 55.0},
                )
                await engine.idle(timeout_s=3)
            await engine.aclose("done")
            return adapter, e

        adapter, ears_ = run(scenario())
        self.assertEqual(len(ears_.deep_calls), 1)

    def test_artifacts_are_retrievable_around_the_playhead(self):
        ears = Ears(fast_plan={0: candidates_at((rt.CANDIDATE_ENERGY, 3.0, 0.5))})

        async def scenario():
            adapter, engine, e, clock = build(ears, deep_ear=False)
            await adapter.prepare()
            await engine.start(run_perception=False)
            adapter.report_position(0, wall_ms=clock.now)
            await engine.perception_tick()
            await engine.aclose("done")
            return adapter, clock

        adapter, clock = run(scenario())
        found = adapter.what_is_happening_now(now_wall_ms=clock.now)
        self.assertTrue(found)
        self.assertEqual(adapter.counters.prefetch_hits, 1)

    def test_no_audio_means_no_analysis_and_no_claim_of_listening(self):
        """The no-fake-listening invariant, carried over unchanged."""
        async def scenario():
            adapter, engine, e, clock = build(Ears(), acquire=lambda: None)
            ok = await adapter.prepare()
            await engine.start(run_perception=False)
            adapter.report_position(0, wall_ms=clock.now)
            admitted = await engine.perception_tick()
            await engine.aclose("done")
            return adapter, e, ok, admitted

        adapter, ears_, ok, admitted = run(scenario())
        self.assertFalse(ok)
        self.assertEqual(ears_.fast_calls, [])
        self.assertEqual(admitted, 0)
        self.assertFalse(adapter.heard_anything)

    def test_nothing_is_analysed_before_the_first_position_report(self):
        async def scenario():
            adapter, engine, e, clock = build(Ears())
            await adapter.prepare()
            # `prepare()` deliberately makes one Fast Ear call to warm the
            # memoised whole-file DSP pass off the event loop; perception is
            # measured from after it.
            warmed = len(e.fast_calls)
            await engine.start(run_perception=False)
            admitted = await engine.perception_tick()
            await engine.aclose("done")
            return e, admitted, warmed

        ears_, admitted, warmed = run(scenario())
        self.assertEqual(ears_.fast_calls[warmed:], [],
                         "position 0 would claim the opening was consumed")
        self.assertEqual(admitted, 0)


# =============================================================================
# 12. telemetry
# =============================================================================

class TelemetryTests(unittest.TestCase):
    def test_metrics_are_a_projection_of_engine_telemetry(self):
        ears = Ears(fast_plan={0: candidates_at((rt.CANDIDATE_SECTION, 7.0, 0.9))})

        async def scenario():
            adapter, engine, e, clock = build(ears)
            await adapter.prepare()
            await engine.start(run_perception=False)
            adapter.report_position(0, wall_ms=clock.now)
            await engine.perception_tick()
            await engine.idle(timeout_s=3)
            metrics = adapter.realtime_metrics(engine)
            snap = await engine.aclose("done")
            return metrics, snap

        metrics, snap = run(scenario())
        # The old RealtimeMetrics key set still resolves for its consumers.
        for key in ("fast_ear_runs", "deep_ear_runs", "windows_planned",
                    "duplicate_fast_ear_rate", "prefetch_hit_rate"):
            self.assertIn(key, metrics)
        # One tick plans the whole 60s lookahead: 4 windows of 15s.
        self.assertEqual(metrics["fast_ear_runs"], 4)
        self.assertEqual(metrics["windows_planned"], 4)
        self.assertEqual(metrics["deep_ear_runs"], 1)
        self.assertTrue(metrics["heard_anything"])
        # A duration that actually resolved, from the engine — not None. The
        # first version of the projection read summary()["intervals"], a key
        # that does not exist, and every duration silently came back None while
        # looking exactly like an instrument that had not fired.
        self.assertIsNotNone(metrics["fast_reaction_compute_ms"],
                             "the engine's timing is not reaching the projection")
        self.assertIsInstance(metrics["fast_reaction_compute_ms"], float)

    def test_the_adapter_keeps_no_timing_source_of_its_own(self):
        """One instrument. A second one is what this migration removed."""
        fields = set(mra.MusicAdapterCounters().snapshot())
        self.assertFalse([f for f in fields if "_ms" in f or "latency" in f],
                         f"adapter is timing things the engine already times: {fields}")


# =============================================================================
# 13. disconnect cleanup
# =============================================================================

class DisconnectTests(unittest.TestCase):
    def test_disconnect_tears_down_without_speaking(self):
        ears = Ears(deep_delay_s=0.4)

        async def scenario():
            adapter, engine, e, clock = build(ears)
            await adapter.prepare()
            await engine.start(run_perception=False)
            adapter.report_position(45_000, wall_ms=clock.now)
            await engine.emit_type(
                mra.EVENT_DEEP_LISTEN, source="ladder",
                payload={"at_s": 45.0, "start_s": 40.0, "end_s": 55.0},
            )
            await asyncio.sleep(0.05)
            await engine.emit_type(rte.EVENT_DISCONNECTED, source="transport")
            return adapter, engine.snapshot()

        adapter, snap = run(scenario())
        self.assertTrue(snap["closed"])
        self.assertEqual(snap["close_reason"], "disconnected")
        self.assertEqual(snap["deep_inflight"], 0)
        self.assertGreaterEqual(
            snap["telemetry"]["counters"].get("deep_cancelled_on_close", 0), 1,
            "the in-flight analysis should have been counted before cancelling",
        )

    def test_close_clears_adapter_state(self):
        async def scenario():
            adapter, engine, e, clock = build(Ears(), deep_ear=False)
            await adapter.prepare()
            await engine.start(run_perception=False)
            await engine.aclose("done")
            return adapter

        adapter = run(scenario())
        self.assertEqual(adapter._pending, [])
        self.assertEqual(adapter._signatures, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
