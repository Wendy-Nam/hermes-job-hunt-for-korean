"""Playhead, ahead-buffer, Fast Ear / Deep Ear selection, and no-fake-listening.

The DSP stack is injected rather than mocked-in-place: `fast_ear_fn` and
`deep_ear_fn` are constructor arguments, so these tests drive the real
scheduling, caching, budgeting and spoiler logic with deterministic feature
series. numpy/scipy/soundfile are not importable in the interpreter these
suites run under, which is precisely why the DSP entry points were made
injectable in the first place.
"""
from __future__ import annotations

import struct
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
for extra in (HERE, HERE.parent / "_shared"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import music_failure as failure  # noqa: E402
import music_media_fetch as mfetch  # noqa: E402
import music_media_validate as validate  # noqa: E402
import music_playhead as ph  # noqa: E402
import music_realtime as rt  # noqa: E402
from hermes_media import (  # noqa: E402
    CLOCK_MEDIA,
    MediaPosition,
    MediaRange,
    SpoilerBoundary,
)

MEDIA_ID = "music:abc123"
VIDEO_ID = "JeucohIa5LQ"
DURATION_S = 337.0


def wav(path: Path) -> Path:
    data = b"\x00" * 8192
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 22050, 44100, 2, 16)
        + b"data" + struct.pack("<I", len(data)) + data
    )
    return path


class PlayheadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.head = ph.Playhead(MEDIA_ID, duration_s=DURATION_S)

    def test_no_position_before_the_first_report(self):
        self.assertIsNone(
            self.head.position(now_wall_ms=1000),
            "position 0 would tell the boundary the opening was consumed",
        )

    def test_position_maps_onto_a_media_clock_MediaPosition(self):
        self.head.report(ph.PlayheadReport(position_ms=30_000, wall_ms=1_000))
        pos = self.head.position()
        self.assertIsInstance(pos, MediaPosition)
        self.assertEqual(pos.timestamp_ms, 30_000)
        self.assertEqual(pos.clock, CLOCK_MEDIA)
        self.assertEqual(pos.confidence, 1.0)

    def test_projection_advances_with_wall_clock(self):
        self.head.report(ph.PlayheadReport(position_ms=30_000, wall_ms=1_000))
        pos = self.head.position(now_wall_ms=1_000 + 5_000)
        self.assertEqual(pos.timestamp_ms, 35_000)

    def test_a_paused_playhead_does_not_drift(self):
        self.head.report(ph.PlayheadReport(position_ms=30_000, wall_ms=1_000, playing=False))
        pos = self.head.position(now_wall_ms=1_000 + 60_000)
        self.assertEqual(pos.timestamp_ms, 30_000)

    def test_projection_is_marked_inferred_and_fails_closed(self):
        self.head.report(ph.PlayheadReport(position_ms=10_000, wall_ms=0))
        pos = self.head.position(now_wall_ms=120_000)
        self.assertLess(pos.confidence, 1.0)
        self.assertTrue(pos.inferred)
        boundary = SpoilerBoundary(consumed_through=pos)
        self.assertFalse(
            boundary.may_reveal(pos),
            "material at an inferred boundary must not be revealable",
        )

    def test_projection_is_clamped_to_the_track_length(self):
        self.head.report(ph.PlayheadReport(position_ms=330_000, wall_ms=0))
        pos = self.head.position(now_wall_ms=600_000)
        self.assertEqual(pos.timestamp_ms, int(DURATION_S * 1000))

    def test_stale_out_of_order_report_is_dropped(self):
        self.assertTrue(
            self.head.report(ph.PlayheadReport(position_ms=60_000, wall_ms=10, sequence_no=2))
        )
        self.assertFalse(
            self.head.report(ph.PlayheadReport(position_ms=10_000, wall_ms=20, sequence_no=1)),
            "a late-arriving stale report must not rewind the cursor",
        )
        self.assertEqual(self.head.position().timestamp_ms, 60_000)

    def test_alignment_error_is_measurable(self):
        self.head.report(ph.PlayheadReport(position_ms=30_000, wall_ms=0))
        self.assertEqual(self.head.alignment_error_ms(36_000, now_wall_ms=5_000), 1_000)


class AheadBufferTests(unittest.TestCase):
    def pos(self, seconds: float) -> MediaPosition:
        return MediaPosition.at_seconds(seconds, clock=CLOCK_MEDIA)

    def test_windows_start_at_the_playhead_and_run_forward(self):
        windows = ph.plan_ahead_windows(
            self.pos(20), duration_s=DURATION_S, window_s=15, lookahead_s=45, max_windows=4
        )
        self.assertEqual(
            [(w.start.timestamp_ms / 1000, w.end.timestamp_ms / 1000) for w in windows],
            [(15.0, 30.0), (30.0, 45.0), (45.0, 60.0), (60.0, 75.0)],
            "lookahead is measured from the playhead, so a window opening at 60s "
            "is still inside a 45s horizon from 20s",
        )

    def test_windows_are_grid_aligned_so_cache_keys_are_stable(self):
        a = ph.plan_ahead_windows(self.pos(20), duration_s=DURATION_S, lookahead_s=30)
        b = ph.plan_ahead_windows(self.pos(23), duration_s=DURATION_S, lookahead_s=30)
        self.assertEqual(
            [w.start.timestamp_ms for w in a][:1], [w.start.timestamp_ms for w in b][:1],
            "unaligned windows would miss every cache and double the analysis cost",
        )

    def test_covered_ground_is_not_replanned(self):
        windows = ph.plan_ahead_windows(
            self.pos(20), duration_s=DURATION_S, covered=[(15.0, 30.0)],
            window_s=15, lookahead_s=45,
        )
        self.assertNotIn(15.0, [w.start.timestamp_ms / 1000 for w in windows])

    def test_planning_is_bounded(self):
        windows = ph.plan_ahead_windows(
            self.pos(0), duration_s=DURATION_S, window_s=5, lookahead_s=300, max_windows=3
        )
        self.assertEqual(len(windows), 3)

    def test_no_position_means_no_plan(self):
        self.assertEqual(ph.plan_ahead_windows(None, duration_s=DURATION_S), ())

    def test_planning_stops_at_the_end_of_the_track(self):
        windows = ph.plan_ahead_windows(
            self.pos(330), duration_s=DURATION_S, window_s=15, lookahead_s=120
        )
        for w in windows:
            self.assertLessEqual(w.end.timestamp_ms / 1000, DURATION_S)


class FastEarTests(unittest.TestCase):
    def test_a_flat_window_produces_no_candidates(self):
        self.assertEqual(
            rt.fast_ear_from_series(start_s=0, energy_1hz=[0.5] * 15), ()
        )

    def test_an_energy_step_is_detected_and_positioned(self):
        series = [0.1] * 8 + [0.9] * 7
        candidates = rt.fast_ear_from_series(start_s=30.0, energy_1hz=series)
        energy = [c for c in candidates if c.kind == rt.CANDIDATE_ENERGY]
        self.assertEqual(len(energy), 1)
        self.assertEqual(energy[0].at_s, 38.0, "offset must be relative to the window start")
        self.assertGreater(energy[0].salience, 0.9)

    def test_each_feature_maps_to_its_own_candidate_kind(self):
        step = [0.1] * 8 + [0.9] * 7
        flat = [0.5] * 15
        kinds = {
            c.kind
            for c in rt.fast_ear_from_series(
                start_s=0, energy_1hz=flat, onset_1hz=step,
                brightness_1hz=flat, flux_1hz=flat,
            )
        }
        self.assertEqual(kinds, {rt.CANDIDATE_ONSET})

    def test_a_section_transition_requires_two_signals_to_agree(self):
        step = [0.1] * 8 + [0.9] * 7
        candidates = rt.fast_ear_from_series(start_s=0, energy_1hz=step, flux_1hz=step)
        kinds = {c.kind for c in candidates}
        self.assertIn(rt.CANDIDATE_SECTION, kinds)
        self.assertIn(rt.CANDIDATE_BOUNDARY, kinds)

    def test_candidates_become_canonical_TimedObservations(self):
        candidates = rt.fast_ear_from_series(
            start_s=10, energy_1hz=[0.1] * 8 + [0.9] * 7
        )
        obs = rt.observations_from_candidates(candidates, media_id=MEDIA_ID)
        self.assertTrue(obs)
        first = obs[0]
        self.assertEqual(first.media_id, MEDIA_ID)
        self.assertEqual(first.modality, "audio")
        self.assertEqual(first.position.clock, CLOCK_MEDIA)
        self.assertEqual(first.provenance.producer, rt.FAST_EAR_PRODUCER)


class DeepEarSelectionTests(unittest.TestCase):
    def cand(self, at_s, salience, kind=rt.CANDIDATE_ENERGY):
        return rt.FastEarCandidate(kind=kind, at_s=at_s, salience=salience)

    def test_low_salience_is_declined(self):
        picked = rt.select_deep_ear_targets([self.cand(10, 0.1), self.cand(40, 0.2)])
        self.assertEqual(picked, ())

    def test_adjacent_peaks_collapse_to_one_pass(self):
        picked = rt.select_deep_ear_targets(
            [self.cand(30, 0.9), self.cand(32, 0.8), self.cand(34, 0.85)]
        )
        self.assertEqual(len(picked), 1, "three seconds apart is one event, not three")

    def test_budget_caps_an_eventful_track(self):
        candidates = [self.cand(i * 30.0, 0.9) for i in range(30)]
        picked = rt.select_deep_ear_targets(candidates, budget=4)
        self.assertEqual(len(picked), 4)

    def test_already_analysed_regions_are_skipped(self):
        picked = rt.select_deep_ear_targets(
            [self.cand(30, 0.9)], already_analysed_s=[32.0]
        )
        self.assertEqual(picked, ())

    def test_highest_salience_wins_the_budget(self):
        picked = rt.select_deep_ear_targets(
            [self.cand(30, 0.5), self.cand(90, 0.95), self.cand(150, 0.6)], budget=1
        )
        self.assertEqual(picked[0].at_s, 90)


class TimedArtifactCacheTests(unittest.TestCase):
    def artifact(self, start_s, end_s, key):
        from hermes_media import AnalysisArtifact, new_id

        return AnalysisArtifact(
            artifact_id=new_id("art"), media_id=MEDIA_ID,
            artifact_kind="fast_ear_window",
            cache_key=key, producer=rt.FAST_EAR_PRODUCER,
            range=MediaRange(
                start=MediaPosition.at_seconds(start_s, clock=CLOCK_MEDIA),
                end=MediaPosition.at_seconds(end_s, clock=CLOCK_MEDIA),
            ),
            body_ref=f"x://{key}",
        )

    def test_duplicate_put_is_suppressed(self):
        cache = ph.TimedArtifactCache()
        self.assertTrue(cache.put(self.artifact(0, 15, "k1")))
        self.assertFalse(cache.put(self.artifact(0, 15, "k1")))
        self.assertEqual(len(cache), 1)
        self.assertEqual(cache.duplicate_puts, 1)

    def test_retrieval_around_the_playhead(self):
        cache = ph.TimedArtifactCache()
        cache.put(self.artifact(0, 15, "k1"))
        cache.put(self.artifact(60, 75, "k2"))
        found = cache.around(MediaPosition.at_seconds(65, clock=CLOCK_MEDIA), radius_s=10)
        self.assertEqual([a.cache_key for a in found], ["k2"])

    def test_cache_key_never_depends_on_a_url(self):
        window = MediaRange(
            start=MediaPosition.at_seconds(30, clock=CLOCK_MEDIA),
            end=MediaPosition.at_seconds(45, clock=CLOCK_MEDIA),
        )
        a = rt.window_cache_key(MEDIA_ID, window, producer="p", version="1")
        b = rt.window_cache_key(MEDIA_ID, window, producer="p", version="1")
        self.assertEqual(a, b, "the same window must key identically across fetches")


class CompanionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.fast_calls: list[tuple[float, float]] = []
        self.deep_calls: list[tuple[float, float]] = []

    def build(self, *, download=None, deep=True, **kw):
        def default_download(vid, out, proxy):
            wav(out)

        ladder = mfetch.MusicFetchLadder(
            cache_dir=self.root / "audio",
            download=download or default_download,
            proxy_provider=lambda: "http://proxy:1",
            sleep=lambda s: None,
            monotonic_ms=lambda: 0,
            validator=lambda p, **kwargs: validate.validate_audio_file(
                p, probe=lambda _p: DURATION_S, **kwargs
            ),
        )

        def fast(path, start_s, end_s):
            self.fast_calls.append((start_s, end_s))
            return rt.fast_ear_from_series(
                start_s=start_s, energy_1hz=[0.1] * 8 + [0.9] * 7
            )

        def deep_fn(path, start_s, end_s):
            self.deep_calls.append((start_s, end_s))
            return {"analyzer_version": "1.0.0", "harmony": {}}

        return rt.RealtimeMusicCompanion(
            session=rt.new_music_session(media_id=MEDIA_ID, conversation_id="conv1"),
            source=mfetch.music_source(VIDEO_ID, media_id_=MEDIA_ID),
            ladder=ladder,
            fast_ear_fn=fast,
            deep_ear_fn=deep_fn if deep else None,
            duration_s=DURATION_S,
            **kw,
        )

    # -- the invariant --------------------------------------------------------
    def test_no_fake_listening_when_every_route_fails(self):
        def dead(vid, out, proxy):
            raise RuntimeError("Temporary failure in name resolution")

        companion = self.build(download=dead)
        produced = companion.advance()

        self.assertEqual(produced, (), "no artifact may come from a failed fetch")
        self.assertIsNone(companion.audio)
        self.assertEqual(companion.observations, [])
        self.assertEqual(len(companion.artifacts), 0)
        self.assertFalse(
            companion.heard_anything,
            "the system must not be able to claim it heard the song",
        )
        self.assertEqual(self.fast_calls, [], "Fast Ear must not run on imaginary input")
        self.assertEqual(self.deep_calls, [])

        found = companion.last_failure
        self.assertIsNotNone(found)
        self.assertFalse(found.ok)
        self.assertEqual(found.local_ref, "")
        self.assertEqual(found.reason, failure.REASON_NETWORK)

    def test_no_fake_listening_when_the_bytes_are_not_audio(self):
        def html(vid, out, proxy):
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"<!DOCTYPE html>" + b"x" * 5000)

        companion = self.build(download=html)
        self.assertEqual(companion.advance(), ())
        self.assertFalse(companion.heard_anything)
        self.assertEqual(self.fast_calls, [])
        self.assertEqual(companion.last_failure.reason, failure.REASON_MIME_MISMATCH)

    def test_heard_anything_is_false_until_something_is_analysed(self):
        companion = self.build()
        self.assertFalse(companion.heard_anything)
        self.assertIsNotNone(companion.ensure_audio())
        self.assertFalse(
            companion.heard_anything,
            "acquiring bytes is not the same as having listened to them",
        )
        companion.report_position(0, wall_ms=0)
        companion.advance()
        self.assertTrue(companion.heard_anything)

    # -- realtime behaviour ---------------------------------------------------
    def test_advance_analyses_the_window_ahead_of_the_playhead(self):
        companion = self.build()
        companion.report_position(30_000, wall_ms=0)
        produced = companion.advance(window_s=15, lookahead_s=45, max_windows=3)

        self.assertTrue(produced)
        self.assertEqual([s for s, _ in self.fast_calls], [30.0, 45.0, 60.0])

    def test_the_ahead_buffer_does_not_move_the_consumed_boundary(self):
        companion = self.build()
        companion.report_position(30_000, wall_ms=0)
        companion.advance(window_s=15, lookahead_s=45)

        boundary = companion.session.spoiler
        ahead = MediaPosition.at_seconds(70, clock=CLOCK_MEDIA)
        self.assertFalse(
            boundary.may_reveal(ahead),
            "analysed-through ran ahead; consumed-through must not have",
        )
        self.assertTrue(boundary.may_reveal(MediaPosition.at_seconds(10, clock=CLOCK_MEDIA)))

    def test_future_analysis_exists_while_reveal_stays_blocked(self):
        companion = self.build()
        companion.report_position(30_000, wall_ms=0)
        companion.advance(window_s=15, lookahead_s=60)

        future = [
            a for a in companion.artifacts.all()
            if a.range and a.range.start.timestamp_ms > 40_000
        ]
        self.assertTrue(future, "lookahead analysis is the point of the ahead-buffer")
        self.assertEqual(
            companion.visible_now(future), (),
            "and none of it may be revealed before the listener gets there",
        )

    def test_a_second_pass_reuses_the_buffer_instead_of_re_analysing(self):
        companion = self.build()
        companion.report_position(30_000, wall_ms=0)
        companion.advance(window_s=15, lookahead_s=45)
        first = len(self.fast_calls)

        artifacts_after_first = len(companion.artifacts)

        companion.advance(window_s=15, lookahead_s=45)
        self.assertEqual(len(self.fast_calls), first, "no duplicate Fast Ear passes")
        self.assertEqual(len(companion.artifacts), artifacts_after_first)

    def test_covered_ground_is_skipped_at_planning_not_after_the_work(self):
        """Where duplicate suppression actually happens, and where it does not.

        `plan_ahead_windows` filters against `covered_ranges`, so a second pass
        plans nothing and Fast Ear is never entered — the cheap path. The
        cache-key check inside `_fast_ear_window` is the second line of
        defence, for the case planning cannot see: two passes interleaving, or
        a window reached from a different playhead position.
        """
        companion = self.build()
        companion.report_position(30_000, wall_ms=0)
        companion.advance(window_s=15, lookahead_s=45)
        planned_after_first = companion.metrics.windows_planned

        companion.advance(window_s=15, lookahead_s=45)
        self.assertEqual(
            companion.metrics.windows_planned, planned_after_first,
            "covered windows must not even be planned",
        )

        # Now force the window past the planner, straight at the worker.
        window = MediaRange(
            start=MediaPosition.at_seconds(30, clock=CLOCK_MEDIA),
            end=MediaPosition.at_seconds(45, clock=CLOCK_MEDIA),
        )
        before = len(self.fast_calls)
        self.assertIsNone(companion._fast_ear_window(companion.audio.local_ref, window))
        self.assertEqual(len(self.fast_calls), before, "the cache key must stop it here too")
        self.assertEqual(companion.metrics.fast_ear_skipped_cached, 1)

    def test_audio_is_fetched_once_across_passes(self):
        downloads = []

        def counting(vid, out, proxy):
            downloads.append(vid)
            wav(out)

        companion = self.build(download=counting)
        companion.report_position(0, wall_ms=0)
        companion.advance()
        companion.advance()
        self.assertEqual(len(downloads), 1)

    def test_immediate_retrieval_around_the_playhead_hits_the_buffer(self):
        companion = self.build()
        companion.report_position(30_000, wall_ms=0)
        companion.advance(window_s=15, lookahead_s=60)

        found = companion.what_is_happening_now(radius_s=10)
        self.assertTrue(found, "the window the listener is inside must be cached")
        self.assertEqual(companion.metrics.snapshot()["prefetch_hit_rate"], 1.0)

    def test_deep_ear_is_selective_not_per_second(self):
        companion = self.build()
        companion.report_position(0, wall_ms=0)
        companion.advance(window_s=15, lookahead_s=120, max_windows=8)

        self.assertTrue(self.deep_calls, "something salient should have been inspected")
        self.assertLessEqual(
            len(self.deep_calls), rt.DEEP_EAR_BUDGET_PER_TRACK,
            "Deep Ear must stay inside its per-track budget",
        )
        self.assertLess(
            len(self.deep_calls), len(companion.observations),
            "far fewer deep passes than Fast Ear candidates",
        )

    def test_fast_ear_survives_deep_ear_dying(self):
        companion = self.build()

        def exploding(path, start_s, end_s):
            raise RuntimeError("deep ear venv is missing")

        companion._deep_ear = exploding
        companion.report_position(0, wall_ms=0)
        produced = companion.advance(window_s=15, lookahead_s=45)

        self.assertTrue(produced, "Fast Ear artifacts must survive Deep Ear's death")
        self.assertTrue(companion.heard_anything)

    def test_stale_position_report_is_rejected(self):
        companion = self.build()
        self.assertTrue(companion.report_position(60_000, wall_ms=10, sequence_no=2))
        self.assertFalse(companion.report_position(10_000, wall_ms=20, sequence_no=1))

    def test_benchmark_snapshot_reports_real_counters(self):
        companion = self.build()
        companion.report_position(0, wall_ms=0)
        companion.advance()
        snap = companion.benchmark_snapshot()

        self.assertTrue(snap["heard_anything"])
        self.assertEqual(snap["fetch"]["successes"], 1)
        self.assertGreater(snap["realtime"]["fast_ear_runs"], 0)
        self.assertIn("youtube/direct", snap["route_health"])


if __name__ == "__main__":
    unittest.main()
