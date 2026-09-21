"""Acquisition ladder, candidate identity, capability detection, turn outcome.

These cover the addendum's matrix. The theme running through them: *acquiring
something* and *acquiring the right thing* are separate failures, and both are
separate again from *claiming to have heard it*.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(HERE.parent / "_shared") not in sys.path:
    sys.path.insert(0, str(HERE.parent / "_shared"))

import music_candidate as candidates  # noqa: E402
import music_capability as capability  # noqa: E402
import music_failure as failure  # noqa: E402
import music_link  # noqa: E402
import music_turn_outcome as outcome  # noqa: E402
import music_youtube_audio as yta  # noqa: E402

_SPEC = importlib.util.spec_from_file_location("shared_music_acq_plugin", HERE / "__init__.py")
PLUGIN = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(PLUGIN)

VIDEO_ID = "dQw4w9WgXcQ"
REF = {"title": "Lemon Tang", "artist": "Hearts2Hearts", "duration_s": 201.0}


class CandidateIdentityTests(unittest.TestCase):
    """The wrong-recording guard."""

    def _reject(self, **overrides):
        cand = {"video_id": "x1", "title": "Lemon Tang", "channel": "Hearts2Hearts - Topic",
                "duration_s": 201.0}
        cand.update(overrides)
        return candidates.reject_reason(
            cand, title=REF["title"], artist=REF["artist"], reference_duration_s=REF["duration_s"]
        )

    def test_matching_upload_is_accepted(self):
        self.assertIsNone(self._reject())

    def test_live_version_rejected(self):
        self.assertEqual(self._reject(title="Lemon Tang (Live at KSPO Dome)"), "variant:live")

    def test_cover_rejected(self):
        self.assertEqual(self._reject(title="Lemon Tang - cover by someone"), "variant:cover")

    def test_remix_and_nightcore_rejected(self):
        self.assertEqual(self._reject(title="Lemon Tang (Remix)"), "variant:remix")
        self.assertEqual(self._reject(title="Lemon Tang [Nightcore]"), "variant:nightcore")

    def test_duration_mismatch_rejected(self):
        self.assertEqual(self._reject(duration_s=420.0), "duration_mismatch")

    def test_small_duration_difference_accepted(self):
        self.assertIsNone(self._reject(duration_s=209.0))

    def test_unrelated_song_rejected_on_score(self):
        self.assertEqual(
            self._reject(title="Completely Different Song", channel="Some Channel"),
            "low_identity_score",
        )

    def test_unknown_duration_does_not_reject(self):
        """The blocked videos this tier exists for are exactly the ones whose
        duration we could not read."""
        self.assertIsNone(self._reject(duration_s=None))

    def test_song_whose_own_title_contains_a_marker_is_not_rejected(self):
        self.assertIsNone(
            candidates.reject_reason(
                {"video_id": "x", "title": "Live and Let Die", "channel": "Wings - Topic", "duration_s": 200.0},
                title="Live and Let Die", artist="Wings", reference_duration_s=200.0,
            )
        )

    def test_duplicate_ids_collapse(self):
        results = [
            {"video_id": "same", "title": "Lemon Tang", "channel": "Hearts2Hearts - Topic", "duration_s": 201},
            {"video_id": "same", "title": "Lemon Tang", "channel": "Hearts2Hearts - Topic", "duration_s": 201},
        ]
        accepted, _ = candidates.rank_candidates(
            results, title=REF["title"], artist=REF["artist"], reference_duration_s=201.0
        )
        self.assertEqual(len(accepted), 1)

    def test_blocked_id_is_excluded(self):
        results = [{"video_id": "blocked", "title": "Lemon Tang", "channel": "Hearts2Hearts - Topic",
                    "duration_s": 201}]
        accepted, _ = candidates.rank_candidates(
            results, title=REF["title"], artist=REF["artist"],
            reference_duration_s=201.0, exclude_ids=("blocked",),
        )
        self.assertEqual(accepted, [])

    def test_topic_channel_outranks_a_plain_upload(self):
        results = [
            {"video_id": "plain", "title": "Lemon Tang", "channel": "randomuploader", "duration_s": 201},
            {"video_id": "topic", "title": "Lemon Tang", "channel": "Hearts2Hearts - Topic", "duration_s": 201},
        ]
        accepted, _ = candidates.rank_candidates(
            results, title=REF["title"], artist=REF["artist"], reference_duration_s=201.0
        )
        self.assertEqual(accepted[0]["video_id"], "topic")


class AlternateSourceTierTests(unittest.TestCase):
    """The ladder tier that swaps in a different upload."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="music-alt-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _finder(self, results, native_id="blocked1"):
        import music_media_fetch as mfetch
        import music_realtime

        with mock.patch.object(yta, "search_youtube", lambda q, **k: results):
            ladder = music_realtime.build_default_ladder(self.tmp)
            source = mfetch.music_source("blocked1", media_id_="m1")
            source.metadata.update(REF)
            return ladder._alternate_source_finder(source)

    def test_picks_a_verified_alternate(self):
        self.assertEqual(
            self._finder([{"video_id": "good1", "title": "Lemon Tang",
                           "channel": "Hearts2Hearts - Topic", "duration_s": 201}]),
            "good1",
        )

    def test_rejects_a_live_take_even_though_it_is_playable(self):
        self.assertIsNone(
            self._finder([{"video_id": "live1", "title": "Lemon Tang (Live)",
                           "channel": "Hearts2Hearts", "duration_s": 240}])
        )

    def test_skips_the_variant_and_takes_the_real_upload(self):
        self.assertEqual(
            self._finder([
                {"video_id": "cov", "title": "Lemon Tang cover", "channel": "fan", "duration_s": 200},
                {"video_id": "real", "title": "Lemon Tang", "channel": "Hearts2Hearts - Topic",
                 "duration_s": 201},
            ]),
            "real",
        )

    def test_never_returns_the_blocked_video_itself(self):
        self.assertIsNone(
            self._finder([{"video_id": "blocked1", "title": "Lemon Tang",
                           "channel": "Hearts2Hearts - Topic", "duration_s": 201}])
        )


class ProxyCandidateTests(unittest.TestCase):
    def setUp(self):
        yta.reset_proxy_cache()

    def tearDown(self):
        yta.reset_proxy_cache()

    def test_uses_multi_candidate_api_when_available(self):
        fake = mock.MagicMock()
        fake.resolve_proxies = lambda n=3: ["p1", "p2", "p3"]
        with mock.patch.object(yta, "_load_proxynet", lambda: fake):
            self.assertEqual(yta.resolved_proxies(n=2), ["p1", "p2"])

    def test_falls_back_to_single_proxy_on_legacy_proxynet(self):
        legacy = mock.MagicMock(spec=["resolve_proxy"])
        legacy.resolve_proxy = lambda: "only-one"
        with mock.patch.object(yta, "_load_proxynet", lambda: legacy):
            self.assertEqual(yta.resolved_proxies(n=3), ["only-one"])

    def test_no_proxynet_is_not_an_error(self):
        with mock.patch.object(yta, "_load_proxynet", lambda: None):
            self.assertEqual(yta.resolved_proxies(), [])

    def test_candidate_list_is_cached(self):
        calls = []

        def _fake():
            calls.append(1)
            m = mock.MagicMock()
            m.resolve_proxies = lambda n=3: ["p1"]
            return m

        with mock.patch.object(yta, "_load_proxynet", _fake):
            yta.resolved_proxies()
            yta.resolved_proxies()
        self.assertEqual(len(calls), 1, "enumeration costs ~20s live; it must not repeat per call")


class CookieCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="music-cap-"))
        self._env = mock.patch.dict(os.environ, {"HERMES_DATA": str(self.tmp)})
        self._env.start()
        capability.reset()

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_absent_cookie_reports_capability_missing(self):
        status = capability.youtube_cookie_status()
        self.assertEqual(status["status"], capability.STATUS_MISSING)
        self.assertFalse(status["configured"])
        self.assertIsNone(yta.cookie_file())

    def test_empty_cookie_file_is_invalid_not_configured_ok(self):
        (self.tmp / ".youtube-cookies.txt").write_text("")
        self.assertEqual(capability.youtube_cookie_status()["status"], capability.STATUS_EMPTY)

    def test_preexisting_generic_jar_is_discovered(self):
        """Hermes already ships $HERMES_DATA/cookies.txt on some hosts; asking
        for a second export while sitting on one is the duplication to avoid."""
        (self.tmp / "cookies.txt").write_text("# Netscape\n.youtube.com\tTRUE\t/\tTRUE\t0\tX\tY\n")
        status = capability.youtube_cookie_status()
        self.assertTrue(status["configured"])
        self.assertTrue(status["has_youtube_entries"])
        self.assertEqual(yta.cookie_file(), self.tmp / "cookies.txt")

    def test_dedicated_jar_wins_over_generic(self):
        (self.tmp / "cookies.txt").write_text("# Netscape\n.youtube.com\tTRUE\t/\tTRUE\t0\tX\tY\n")
        (self.tmp / ".youtube-cookies.txt").write_text("# Netscape\n.youtube.com\tTRUE\t/\tTRUE\t0\tA\tB\n")
        self.assertEqual(yta.cookie_file(), self.tmp / ".youtube-cookies.txt")

    def test_jar_present_but_refused_is_reported_invalid(self):
        (self.tmp / ".youtube-cookies.txt").write_text("# Netscape\n.youtube.com\tTRUE\t/\tTRUE\t0\tX\tY\n")
        capability.note_cookie_result(succeeded=False)
        self.assertEqual(capability.youtube_cookie_status()["usable"], capability.STATUS_INVALID)

    def test_capability_report_leaks_no_secrets(self):
        (self.tmp / ".youtube-cookies.txt").write_text(
            "# Netscape\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tSUPERSECRETVALUE\n"
        )
        with mock.patch.object(yta, "resolved_proxies",
                               lambda n=3: ["http://user:pass@1.2.3.4:9999"]):
            blob = json.dumps(capability.acquisition_capabilities(), default=str)
        self.assertNotIn("SUPERSECRETVALUE", blob)
        self.assertNotIn("SID", blob)
        self.assertNotIn("1.2.3.4", blob)
        self.assertNotIn("pass", blob)
        self.assertIn("proxy_candidates", blob)


class FailureTaxonomyTests(unittest.TestCase):
    def test_reasons_map_to_stable_public_codes(self):
        cases = {
            failure.REASON_BOT_CHECK: failure.CODE_BOT_CHECK,
            failure.REASON_GEO_BLOCKED: failure.CODE_REGION_BLOCK,
            failure.REASON_PRIVATE: failure.CODE_PRIVATE,
            failure.REASON_NOT_FOUND: failure.CODE_NOT_FOUND,
            failure.REASON_NETWORK: failure.CODE_NETWORK,
            failure.REASON_TOO_LONG: failure.CODE_OVER_LENGTH,
            failure.REASON_CODEC_UNSUPPORTED: failure.CODE_DECODER,
        }
        for reason, code in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual(failure.public_failure_code(reason), code)

    def test_unknown_reason_is_not_silently_dropped(self):
        self.assertEqual(failure.public_failure_code("something_new"), failure.CODE_UNKNOWN)
        self.assertEqual(failure.public_failure_code(None), failure.CODE_UNKNOWN)

    def test_bot_check_is_attributed_to_the_backend_that_hit_it(self):
        self.assertEqual(
            failure.backend_failure_code("direct", failure.REASON_BOT_CHECK),
            failure.BACKEND_DIRECT_BOT_CHECK,
        )
        self.assertEqual(
            failure.backend_failure_code("proxy", failure.REASON_BOT_CHECK),
            failure.BACKEND_PROXY_FAILED,
        )

    def test_authenticated_refusal_is_a_stale_jar_not_a_missing_one(self):
        self.assertEqual(
            failure.backend_failure_code("direct", failure.REASON_BOT_CHECK, authenticated=True),
            failure.BACKEND_AUTH_INVALID,
        )


class RouterContractTests(unittest.TestCase):
    """Session A owns classification; this plugin obeys it in both directions."""

    def setUp(self):
        self.bare = music_link.detect_link(f"https://www.youtube.com/watch?v={VIDEO_ID}")
        self.spotify = music_link.detect_link("https://open.spotify.com/track/abc123")

    def test_bare_youtube_url_is_not_taken_without_a_router(self):
        self.assertFalse(PLUGIN.should_enter_music_flow(self.bare, "이거 봐봐"))

    def test_router_music_verdict_forces_dispatch_of_a_bare_url(self):
        for route in ("music", "music_video", {"kind": "music"}):
            with self.subTest(route=route):
                self.assertTrue(PLUGIN.should_enter_music_flow(self.bare, "이거 봐봐", route))

    def test_router_video_verdict_blocks_music_even_with_an_intent_phrase(self):
        """A's classification must beat the legacy gate, or the plugin steals a
        category A already assigned elsewhere."""
        for route in ("video.analysis", "video.funny", {"kind": "video.analysis"}):
            with self.subTest(route=route):
                self.assertFalse(
                    PLUGIN.should_enter_music_flow(self.bare, "이 노래 들어봐", route)
                )

    def test_router_video_verdict_blocks_even_a_spotify_link(self):
        self.assertFalse(PLUGIN.should_enter_music_flow(self.spotify, "들어봐", "video.analysis"))

    def test_legacy_gate_still_applies_when_no_route_is_supplied(self):
        self.assertTrue(PLUGIN.should_enter_music_flow(self.bare, "이 노래 들어봐"))
        self.assertTrue(PLUGIN.should_enter_music_flow(self.spotify, "아무말"))

    def test_no_link_is_never_a_music_turn(self):
        self.assertFalse(PLUGIN.should_enter_music_flow(None, "노래 들어봐", "music"))


class TurnOutcomeTests(unittest.TestCase):
    def test_stage_timings_accumulate_across_repeated_stages(self):
        turn = outcome.TurnTrace()
        turn.mark(outcome.STAGE_DOWNLOAD, 100)
        turn.mark(outcome.STAGE_DOWNLOAD, 250)
        self.assertEqual(turn.stages_ms[outcome.STAGE_DOWNLOAD], 350,
                         "a failed attempt's cost must not be overwritten by the next")

    def test_fields_record_the_full_fallback_decision(self):
        turn = outcome.TurnTrace()
        turn.record_backend("direct", ok=False, code=failure.BACKEND_DIRECT_BOT_CHECK)
        turn.record_backend("alternate_source", ok=True)
        turn.outcome = outcome.OUTCOME_LISTENED
        fields = turn.as_fields()
        self.assertEqual(fields["attempted_backends"], ["direct", "alternate_source"])
        self.assertEqual(fields["selected_backend"], "alternate_source")
        self.assertEqual(fields["final_capability"], outcome.OUTCOME_LISTENED)

    def test_default_outcome_is_failure_not_success(self):
        """A turn that crashes before setting a verdict must not read as a
        listen."""
        self.assertEqual(outcome.TurnTrace().outcome, outcome.OUTCOME_FAILED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
