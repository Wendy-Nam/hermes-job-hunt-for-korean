"""End-to-end smoke for the Discord-DM-link -> listening-impression path.

What makes this different from test_plugin.py (which already generates real
WAVs and runs real DSP): the only thing stubbed here is the *network*. The
audio arrives as a genuine compressed .m4a and is turned into a wav by a real
ffmpeg subprocess, through `music_youtube_audio.resolve_audio`'s own caching,
staging and atomic-rename logic. So a broken ffmpeg, a broken cache path or a
perception that degenerates into an empty shell all fail here, and none of
them would fail a seam-injected unit test.

The load-bearing assertion is the *hallucination invariant*:

    a turn's injected context claims a listen  <=>  perception evidence
    was actually produced this turn

Anything that lets the model say "들어봤는데..." without perception evidence
behind it is a hard failure, not a soft one — see `test_no_listen_claim_*`.

Run standalone:
    python3 -m pytest test_music_e2e.py -v
    python3 test_music_e2e.py            # prints the success/failure matrix
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
_VENDOR_DIR = HERE / ".vendor"
if _VENDOR_DIR.is_dir() and str(_VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(_VENDOR_DIR))

import music_cache  # noqa: E402
import music_e2e_fixture as fx  # noqa: E402
import music_link  # noqa: E402
import music_resolve  # noqa: E402
import music_youtube_audio as yta  # noqa: E402

_SPEC = importlib.util.spec_from_file_location("shared_music_e2e_plugin", HERE / "__init__.py")
PLUGIN = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(PLUGIN)

# Phrases that only a turn which really analysed audio is allowed to license.
LISTEN_CLAIM_MARKER = "<music_perception>"
NO_FAKE_LISTENING_MARKER = "들은 척"

VIDEO_ID = "dQw4w9WgXcQ"
DISCORD_DM = "이 노래 들어봐 https://www.youtube.com/watch?v=" + VIDEO_ID


def _trace_events(data_root: Path) -> list[dict]:
    path = Path(data_root) / "logs" / "shared-music" / "trace.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


@unittest.skipUnless(fx.ffmpeg_available(), "ffmpeg is required for the real-decode E2E")
class MusicEndToEndTest(unittest.TestCase):
    """Full Discord-DM -> perception -> context -> follow-up -> archive path."""

    @classmethod
    def setUpClass(cls):
        cls._fixture_dir = Path(tempfile.mkdtemp(prefix="music-e2e-fixtures-"))
        # Encoding is slow enough that doing it per-test dominates runtime.
        cls.song_m4a = fx.make_encoded_fixture(cls._fixture_dir, "song", fx.structured_song(120.0))
        cls.short_m4a = fx.make_encoded_fixture(cls._fixture_dir, "short", fx.structured_song(25.0))
        cls.flat_m4a = fx.make_encoded_fixture(cls._fixture_dir, "flat", fx.flat_tone(60.0))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._fixture_dir, ignore_errors=True)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="music-e2e-"))
        self.data_root = self.tmp / "data"
        (self.data_root / "wiki").mkdir(parents=True, exist_ok=True)
        self._env = mock.patch.dict("os.environ", {"HERMES_DATA": str(self.data_root)})
        self._env.start()
        PLUGIN._store = None
        yta.reset_transport_memo()
        self.source_audio = self.song_m4a
        self.download_calls: list[str] = []

        def _fake_download(video_id, out_path, proxy):
            """Replaces only the network fetch. The ffmpeg transcode to wav is
            real, and so is resolve_audio's cache/rename logic around it."""
            self.download_calls.append(video_id)
            fx.decode_to_wav(self.source_audio, Path(out_path))

        self._dl = mock.patch.object(yta, "_run_download", _fake_download)
        self._dl.start()

        self.metadata = {"title": "Test Song", "artist": "Test Artist", "duration_s": 120.0}
        self._meta = mock.patch.object(
            yta, "fetch_youtube_metadata_detailed",
            lambda vid, trace_fn=None: (self.metadata, None) if self.metadata else (None, "no_metadata"),
        )
        self._meta.start()

    def tearDown(self):
        self._meta.stop()
        self._dl.stop()
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pre(self, message, session_id="dm-1", platform="discord"):
        return PLUGIN.on_pre_llm_call(
            user_message=message, session_id=session_id, platform=platform
        )

    # -- happy path ------------------------------------------------------

    def test_youtube_dm_link_produces_real_perception_evidence(self):
        t0 = time.monotonic()
        result = self._pre(DISCORD_DM)
        elapsed_ms = (time.monotonic() - t0) * 1000

        self.assertIsNotNone(result, "a music DM link must inject context")
        ctx = result["context"]
        self.assertIn(LISTEN_CLAIM_MARKER, ctx)

        # The audio really was fetched and decoded this turn.
        self.assertEqual(self.download_calls, [VIDEO_ID])
        cached_wav = yta.cached_audio_path(self.data_root, VIDEO_ID)
        self.assertIsNotNone(cached_wav, "decoded wav must be cached on disk")
        self.assertGreater(cached_wav.stat().st_size, 100_000)

        # ...and the perception is not an empty shell.
        cache_files = list((self.data_root / "wiki" / ".shared-music" / "track").glob("*.json"))
        self.assertEqual(len(cache_files), 1)
        cache = json.loads(cache_files[0].read_text(encoding="utf-8"))
        perception = cache["perception"]
        self.assertGreaterEqual(len(perception["sections"]), 2, "structured song must yield >1 section")
        self.assertTrue(perception["salient_moments"], "structured song must yield salient moments")
        self.assertTrue(perception["contrasts"], "structured song must yield contrasts")
        self.assertNotEqual(perception["global_impression"]["energy_shape"], "steady")
        self.assertAlmostEqual(perception["track"]["duration_s"], 120.0, delta=2.0)
        self.assertTrue(cache["raw_index"]["energy_1hz"], "1Hz index must be populated")

        events = {e["event"] for e in _trace_events(self.data_root)}
        self.assertIn("htf_analysis_ms", events, "perception must be traceable at runtime")
        self.assertIn("track_resolved", events)
        print(f"\n[latency] first listen (120s track, cold): {elapsed_ms:.0f} ms")

    def test_followup_turn_returns_window_evidence(self):
        self._pre(DISCORD_DM)
        PLUGIN.on_post_llm_call(
            session_id="dm-1", user_message=DISCORD_DM,
            assistant_response="오 이거 뒤로 갈수록 확 올라오네", platform="discord",
        )
        result = self._pre("1:10 쯤에 그 부분 뭐야?")
        self.assertIsNotNone(result, "a timestamped follow-up must retrieve a window")
        self.assertIn("<music_window>", result["context"])
        self.assertIn("center_s: 70", result["context"])

    def test_duplicate_link_same_session_is_noop(self):
        self._pre(DISCORD_DM)
        self.assertIsNone(self._pre(DISCORD_DM), "resending the same track must not re-analyse")
        self.assertEqual(len(self.download_calls), 1)

    def test_cache_hit_skips_download_on_second_session(self):
        self._pre(DISCORD_DM, session_id="dm-1")
        PLUGIN._store = None
        self._pre(DISCORD_DM, session_id="dm-2")
        self.assertEqual(len(self.download_calls), 1, "second session must reuse the cached analysis")
        events = [e["event"] for e in _trace_events(self.data_root)]
        self.assertIn("perception_cache_hit", events)

    def test_short_track_still_yields_perception(self):
        self.source_audio = self.short_m4a
        self.metadata = {"title": "Short", "artist": "A", "duration_s": 25.0}
        result = self._pre(DISCORD_DM)
        self.assertIn(LISTEN_CLAIM_MARKER, result["context"])
        cache = music_cache.load(self.data_root / "wiki", self._only_cache_id())
        self.assertTrue(cache["perception"]["sections"])

    def test_featureless_track_reports_steady_rather_than_inventing_an_arc(self):
        self.source_audio = self.flat_m4a
        self.metadata = {"title": "Flat", "artist": "A", "duration_s": 60.0}
        self._pre(DISCORD_DM)
        cache = music_cache.load(self.data_root / "wiki", self._only_cache_id())
        gi = cache["perception"]["global_impression"]
        self.assertEqual(gi["energy_shape"], "steady")
        self.assertIn("reliable_tempo_estimate", cache["perception"]["uncertain_or_unavailable"])

    def _only_cache_id(self):
        files = list((self.data_root / "wiki" / ".shared-music" / "track").glob("*.json"))
        self.assertEqual(len(files), 1)
        return files[0].stem

    # -- the hallucination invariant -------------------------------------

    def _assert_no_listen_claim(self, result):
        self.assertIsNotNone(result, "a failure must still inject an honesty rule")
        ctx = result["context"]
        self.assertNotIn(LISTEN_CLAIM_MARKER, ctx, "no perception evidence -> must not license a listen claim")
        self.assertIn(NO_FAKE_LISTENING_MARKER, ctx, "failure context must forbid faking a listen")

    def test_no_listen_claim_when_metadata_unresolvable(self):
        self.metadata = None
        with mock.patch.object(music_resolve.oembed, "fetch_youtube_oembed", lambda vid: None):
            self._assert_no_listen_claim(self._pre(DISCORD_DM))

    def test_no_listen_claim_when_audio_download_fails(self):
        def _boom(video_id, out_path, proxy):
            raise RuntimeError("Video unavailable")

        with mock.patch.object(yta, "_run_download", _boom):
            self._assert_no_listen_claim(self._pre(DISCORD_DM))
        self.assertIsNone(yta.cached_audio_path(self.data_root, VIDEO_ID))

    def test_no_listen_claim_when_track_too_long(self):
        self.metadata = {"title": "Long Mix", "artist": "A", "duration_s": 3600.0}
        result = self._pre(DISCORD_DM)
        self._assert_no_listen_claim(result)
        self.assertEqual(self.download_calls, [], "an over-length track must not be downloaded at all")

    def test_no_listen_claim_when_video_unplayable_metadata_only(self):
        self.metadata = None
        with mock.patch.object(
            music_resolve.oembed, "fetch_youtube_oembed",
            lambda vid: {"title": "Gated Song", "artist": "Gated Artist"},
        ), mock.patch.object(yta, "search_youtube", lambda *a, **k: []):
            result = self._pre(DISCORD_DM)
        self._assert_no_listen_claim(result)
        # degraded-but-named: the song is still identified for the reply
        self.assertIn("Gated Song", result["context"])
        self.assertEqual(self.download_calls, [], "a known-unplayable video must not be downloaded")

    def test_no_listen_claim_when_dsp_analysis_fails(self):
        with mock.patch.object(PLUGIN.dsp, "analyze", side_effect=ValueError("audio too short to analyze")):
            self._assert_no_listen_claim(self._pre(DISCORD_DM))

    # -- turn outcome taxonomy -------------------------------------------

    def _last_verdict(self):
        events = [e for e in _trace_events(self.data_root) if e["event"] == "music_turn_outcome"]
        self.assertTrue(events, "every music turn must end with a verdict")
        return events[-1]

    def test_successful_listen_is_reported_as_LISTENED(self):
        self._pre(DISCORD_DM)
        verdict = self._last_verdict()
        self.assertEqual(verdict["outcome"], "LISTENED")
        self.assertEqual(verdict["final_capability"], "LISTENED")
        # The stage timings the next performance wave will optimise from.
        self.assertIn("dsp", verdict["stages_ms"])
        self.assertIn("resolve", verdict["stages_ms"])
        self.assertGreater(verdict["total_ms"], 0)

    def test_audio_failure_is_reported_as_METADATA_ONLY_not_a_listen(self):
        with mock.patch.object(yta, "_run_download", side_effect=RuntimeError("Sign in to confirm you're not a bot")):
            self._pre(DISCORD_DM)
        verdict = self._last_verdict()
        self.assertEqual(verdict["outcome"], "METADATA_ONLY")
        self.assertEqual(verdict["code"], "BOT_CHECK")

    def test_unresolvable_track_is_reported_as_FAILED(self):
        self.metadata = None
        with mock.patch.object(music_resolve.oembed, "fetch_youtube_oembed", lambda vid: None):
            self._pre(DISCORD_DM)
        verdict = self._last_verdict()
        self.assertEqual(verdict["outcome"], "FAILED")

    def test_metadata_only_and_listened_are_distinguishable_in_the_trace(self):
        """The whole point: `track_resolved` alone must never be readable as a
        listen. 177 production triggers looked identical for this reason."""
        self.metadata = {"title": "Gated", "artist": "A", "duration_s": 120.0}
        with mock.patch.object(yta, "_run_download", side_effect=RuntimeError("Video unavailable")):
            self._pre(DISCORD_DM, session_id="s-a")
        blocked = self._last_verdict()
        PLUGIN._store = None
        self._pre(DISCORD_DM, session_id="s-b")
        heard = self._last_verdict()
        self.assertNotEqual(blocked["outcome"], heard["outcome"])
        self.assertEqual(heard["outcome"], "LISTENED")

    def test_turn_outcome_never_carries_credential_material(self):
        with mock.patch.object(yta, "resolved_proxies", lambda n=3: ["http://u:pw@9.9.9.9:1"]):
            with mock.patch.object(yta, "_run_download", side_effect=RuntimeError("bot")):
                self._pre(DISCORD_DM)
        blob = json.dumps(self._last_verdict(), ensure_ascii=False)
        self.assertNotIn("9.9.9.9", blob)
        self.assertNotIn("pw@", blob)

    def test_router_music_verdict_makes_a_bare_link_listen(self):
        bare = f"https://www.youtube.com/watch?v={VIDEO_ID}"
        self.assertIsNone(self._pre(bare, session_id="bare-1"))  # legacy gate declines
        result = PLUGIN.on_pre_llm_call(
            user_message=bare, session_id="bare-2", platform="discord", media_route="music",
        )
        self.assertIsNotNone(result)
        self.assertIn(LISTEN_CLAIM_MARKER, result["context"])

    def test_router_video_verdict_makes_the_music_pipeline_stand_down(self):
        result = PLUGIN.on_pre_llm_call(
            user_message="이 노래 들어봐 " + DISCORD_DM, session_id="v-1",
            platform="discord", media_route={"kind": "video.analysis"},
        )
        self.assertIsNone(result, "a link A routed to video must not be claimed by music")
        self.assertEqual(self.download_calls, [])

    # -- resolver / link matrix ------------------------------------------

    def test_malformed_and_non_music_urls_do_not_trigger(self):
        for message in [
            "https://www.youtube.com/watch?v=short",          # truncated id
            "https://example.com/track/abc",                   # unrelated host
            "https://open.spotify.com/album/4aawyAB9vmqN3uQ7", # album, out of scope
            "그냥 잡담인데 링크 없음",
        ]:
            with self.subTest(message=message):
                self.assertEqual(self.download_calls, [])
                result = self._pre(message, session_id=f"s-{hash(message)}")
                if result is not None:
                    self.assertNotIn(LISTEN_CLAIM_MARKER, result["context"])

    def test_youtube_music_host_and_playlist_context_resolve_to_bare_track(self):
        link = music_link.detect_link(
            f"https://music.youtube.com/watch?v={VIDEO_ID}&list=RDAMVM{VIDEO_ID}&si=xyz"
        )
        self.assertEqual(link.source, "youtube")
        self.assertEqual(link.id, VIDEO_ID)
        self.assertTrue(link.is_music_host)

    def test_spotify_link_resolves_through_youtube_audio(self):
        with mock.patch.object(
            music_resolve.spotify, "fetch_spotify_track_native",
            lambda sid: {"title": "Test Song", "artist": "Test Artist", "duration_s": 120.0},
        ), mock.patch.object(
            yta, "search_youtube",
            lambda q, **k: [{"video_id": VIDEO_ID, "title": "Test Artist - Test Song",
                             "channel": "Test Artist - Topic", "duration_s": 120.0}],
        ), mock.patch.object(yta, "fetch_youtube_metadata", lambda vid, **k: self.metadata):
            result = self._pre("https://open.spotify.com/track/0NGFAcYQVHCIdQea2qSs1I")
        self.assertIn(LISTEN_CLAIM_MARKER, result["context"])
        self.assertEqual(self.download_calls, [VIDEO_ID])

    def test_isolated_platforms_never_touch_session_state(self):
        for platform in ("cron", "subagent", "cli"):
            with self.subTest(platform=platform):
                self.assertIsNone(self._pre(DISCORD_DM, session_id="iso", platform=platform))
        self.assertEqual(self.download_calls, [])

    # -- temp / cache hygiene ---------------------------------------------

    def test_spotify_named_but_unplayable_still_names_the_track(self):
        """Spotify knew the song, YouTube had no usable match. The reply must
        be about a *known* song we couldn't play — not "I have no idea what
        this is", which is what the old raise-based path produced."""
        with mock.patch.object(
            music_resolve.spotify, "fetch_spotify_track_native",
            lambda sid: {"title": "Lemon Tang", "artist": "Hearts2Hearts", "duration_s": 201.0},
        ), mock.patch.object(yta, "search_youtube", lambda q, **k: []):
            result = self._pre("https://open.spotify.com/track/0NGFAcYQVHCIdQea2qSs1I")
        self._assert_no_listen_claim(result)
        self.assertIn("Lemon Tang", result["context"])
        self.assertIn("Hearts2Hearts", result["context"])
        self.assertEqual(self.download_calls, [])
        events = {e["event"] for e in _trace_events(self.data_root)}
        self.assertIn("spotify_no_youtube_match", events)

    def test_spotify_branch_emits_transport_traces(self):
        """Regression: the Spotify branch used to run its yt-dlp calls with no
        trace_fn, so a production failure on this path left nothing to read."""
        def _blocked_search(query, *, max_results=5, trace_fn=None):
            if trace_fn:
                trace_fn("ytdlp_attempt_failed", stage="metadata", transport="direct", reason="bot_check")
            return []

        with mock.patch.object(
            music_resolve.spotify, "fetch_spotify_track_native",
            lambda sid: {"title": "Lemon Tang", "artist": "Hearts2Hearts", "duration_s": 201.0},
        ), mock.patch.object(yta, "search_youtube", _blocked_search):
            self._pre("https://open.spotify.com/track/0NGFAcYQVHCIdQea2qSs1I")
        events = [e for e in _trace_events(self.data_root) if e["event"] == "ytdlp_attempt_failed"]
        self.assertTrue(events, "Spotify-path transport attempts must be traced")


class AudioAcquisitionTest(unittest.TestCase):
    """resolve_audio's own contract, exercised without the plugin hooks."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="music-audio-"))
        yta.reset_transport_memo()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_partial_wav_is_not_published_as_a_cache_hit(self):
        """A zero-byte wav in the cache dir must read as a miss, not a hit —
        otherwise a crashed download poisons the track permanently."""
        cache_dir = self.tmp / "audio_cache" / "shared-music"
        cache_dir.mkdir(parents=True)
        (cache_dir / f"{VIDEO_ID}.wav").write_bytes(b"")
        self.assertIsNone(yta.cached_audio_path(self.tmp, VIDEO_ID))

    def _install_fake_ytdlp(self, behaviour):
        """Stub the yt_dlp module itself, so the real `_run_download` (staging
        name, postprocessor output path, atomic rename, finally-cleanup) is the
        code under test rather than a stand-in for it."""
        class _FakeYDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def download(self, urls):
                behaviour(self.opts)

        fake = mock.MagicMock()
        fake.YoutubeDL = _FakeYDL
        fake.utils.match_filter_func = lambda expr: expr
        return mock.patch.dict(sys.modules, {"yt_dlp": fake})

    def test_successful_download_publishes_atomically_and_leaves_no_staging(self):
        def _write_wav(opts):
            # yt-dlp's postprocessor writes "<outtmpl stem>.wav"; it also leaves
            # the pre-conversion container behind on some paths.
            stem = opts["outtmpl"].replace(".%(ext)s", "")
            Path(stem + ".webm").write_bytes(b"intermediate")
            Path(stem + ".wav").write_bytes(b"RIFF" + b"\0" * 4096)

        cache_dir = self.tmp / "audio_cache" / "shared-music"
        with self._install_fake_ytdlp(_write_wav):
            out = yta.resolve_audio(self.tmp, VIDEO_ID)
        self.assertEqual(out.name, f"{VIDEO_ID}.wav")
        self.assertTrue(out.exists())
        leftovers = [p.name for p in cache_dir.glob("*") if p.name != f"{VIDEO_ID}.wav"]
        self.assertEqual(leftovers, [], f"leftover staging files: {leftovers}")

    def test_failed_download_leaves_no_staging_files(self):
        def _fail_midway(opts):
            stem = opts["outtmpl"].replace(".%(ext)s", "")
            Path(stem + ".webm").write_bytes(b"partial")
            raise RuntimeError("Video unavailable")

        cache_dir = self.tmp / "audio_cache" / "shared-music"
        with self._install_fake_ytdlp(_fail_midway):
            with self.assertRaises(yta.AudioResolveError):
                yta.resolve_audio(self.tmp, VIDEO_ID)
        leftovers = [p.name for p in cache_dir.glob("*")]
        self.assertEqual(leftovers, [], f"failed download left files behind: {leftovers}")

    def test_ytdlp_success_with_no_audio_file_is_not_published(self):
        """yt-dlp exiting 0 without producing audio must not create an empty
        cache entry that later reads as a valid hit."""
        with self._install_fake_ytdlp(lambda opts: None):
            with self.assertRaises(yta.AudioResolveError):
                yta.resolve_audio(self.tmp, VIDEO_ID)
        self.assertIsNone(yta.cached_audio_path(self.tmp, VIDEO_ID))

    def _write_cached(self, video_id, size, mtime):
        cache_dir = self.tmp / "audio_cache" / "shared-music"
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = cache_dir / f"{video_id}.wav"
        path.write_bytes(b"\0" * size)
        os.utime(path, (mtime, mtime))
        return path

    def test_cache_prunes_least_recently_used_over_cap(self):
        now = time.time()
        self._write_cached("oldest", 1000, now - 3000)
        self._write_cached("middle", 1000, now - 2000)
        newest = self._write_cached("newest", 1000, now - 1000)
        with mock.patch.dict("os.environ", {"HERMES_MUSIC_AUDIO_CACHE_MAX_BYTES": "2000"}):
            evicted = yta.prune_audio_cache(self.tmp)
        self.assertEqual(evicted, 1)
        self.assertIsNone(yta.cached_audio_path(self.tmp, "oldest"))
        self.assertIsNotNone(yta.cached_audio_path(self.tmp, "middle"))
        self.assertTrue(newest.exists())

    def test_prune_never_evicts_the_file_just_downloaded(self):
        now = time.time()
        keep = self._write_cached("justnow", 5000, now)
        self._write_cached("other", 10, now - 9999)
        with mock.patch.dict("os.environ", {"HERMES_MUSIC_AUDIO_CACHE_MAX_BYTES": "100"}):
            yta.prune_audio_cache(self.tmp, keep=keep)
        self.assertTrue(keep.exists(), "the track being analysed must survive pruning")

    def test_cache_under_cap_is_untouched(self):
        self._write_cached("a", 100, time.time())
        with mock.patch.dict("os.environ", {"HERMES_MUSIC_AUDIO_CACHE_MAX_BYTES": str(10 * 1024)}):
            self.assertEqual(yta.prune_audio_cache(self.tmp), 0)
        self.assertIsNotNone(yta.cached_audio_path(self.tmp, "a"))

    def test_cache_hit_refreshes_recency(self):
        old = time.time() - 5000
        path = self._write_cached("touched", 100, old)
        yta.cached_audio_path(self.tmp, "touched")
        self.assertGreater(path.stat().st_mtime, old, "a cache hit must count as a use")

    def test_download_failure_raises_typed_error(self):
        with mock.patch.object(yta, "_run_download", side_effect=RuntimeError("Private video")):
            with self.assertRaises(yta.AudioResolveError) as ctx:
                yta.resolve_audio(self.tmp, VIDEO_ID)
        self.assertTrue(hasattr(ctx.exception, "stage"))
        self.assertTrue(hasattr(ctx.exception, "reason"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
