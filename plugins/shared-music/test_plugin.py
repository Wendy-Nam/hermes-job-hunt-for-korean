"""Regression tests for the shared-music plugin (task item 24's 14 required
cases, plus module-level unit tests). Run with:
    python3 -m unittest test_plugin -v

No real network calls anywhere: music_resolve.resolve_from_youtube/
resolve_from_spotify and music_youtube_audio.resolve_audio are monkeypatched
to point at synthetic WAV fixtures generated in setUp — everything
downstream of that boundary (DSP analysis, perception building, caching,
session lifecycle, Obsidian writing) runs for real, same "mock only the
network edge" strategy youtube-archive's own tests use.
"""
from __future__ import annotations

import importlib.util
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

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

import music_cache  # noqa: E402
import music_dsp as dsp  # noqa: E402
import music_intent  # noqa: E402
import music_link  # noqa: E402
import music_obsidian  # noqa: E402
import music_perception as mp  # noqa: E402
import music_resolve  # noqa: E402
import music_session  # noqa: E402
import music_track_identity as identity  # noqa: E402
import music_window_retrieval as mwr  # noqa: E402
import music_youtube_audio as yta  # noqa: E402

_SPEC = importlib.util.spec_from_file_location("shared_music_plugin", HERE / "__init__.py")
PLUGIN = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(PLUGIN)


def _make_wav(path: Path, *, dur_s: float = 60.0, sr: int = 22050, seed: int = 0) -> None:
    rng = np.random.default_rng(seed)
    t = np.arange(int(sr * dur_s)) / sr
    env = np.piecewise(
        t,
        [t < dur_s * 0.25, (t >= dur_s * 0.25) & (t < dur_s * 0.55), (t >= dur_s * 0.55) & (t < dur_s * 0.8), t >= dur_s * 0.8],
        [0.15, lambda t: 0.15 + 0.65 * (t - t.min()) / max(t.max() - t.min(), 1e-9), 0.8, 0.3],
    )
    tone = np.sin(2 * np.pi * 220 * t) * env
    bright = np.where(t >= dur_s * 0.55, 0.3 * np.sin(2 * np.pi * 880 * t), 0.0)
    kicks = np.zeros_like(t)
    for beat in np.arange(0, dur_s, 0.5):
        idx = int(beat * sr)
        if idx < len(kicks) - 200:
            kicks[idx : idx + 200] += np.hanning(200) * 0.25
    noise = rng.normal(scale=0.01, size=len(t))
    y = tone + bright + kicks + noise
    y = y / (np.max(np.abs(y)) + 1e-9)
    sf.write(str(path), y, sr)


class PluginTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_root = Path(self._tmp.name)
        self.wiki_root = self.data_root / "wiki"
        self.wiki_root.mkdir(parents=True, exist_ok=True)

        PLUGIN._store = music_session.SessionStore(self.wiki_root)
        PLUGIN._data_root = lambda: self.data_root
        PLUGIN._wiki_root = lambda: self.wiki_root

        self.wav_a = self.data_root / "track_a.wav"
        _make_wav(self.wav_a, dur_s=60.0, seed=1)
        self.wav_b = self.data_root / "track_b.wav"
        _make_wav(self.wav_b, dur_s=45.0, seed=2)

        self._orig_resolve_youtube = music_resolve.resolve_from_youtube
        self._orig_resolve_spotify = music_resolve.resolve_from_spotify
        self._orig_resolve_audio = yta.resolve_audio
        self.analyze_call_count = 0
        self._orig_analyze = dsp.analyze

        def counting_analyze(path):
            self.analyze_call_count += 1
            return self._orig_analyze(path)

        dsp.analyze = counting_analyze
        PLUGIN.dsp.analyze = counting_analyze

    def tearDown(self):
        music_resolve.resolve_from_youtube = self._orig_resolve_youtube
        music_resolve.resolve_from_spotify = self._orig_resolve_spotify
        yta.resolve_audio = self._orig_resolve_audio
        dsp.analyze = self._orig_analyze
        PLUGIN.dsp.analyze = self._orig_analyze
        PLUGIN._store = None
        self._tmp.cleanup()

    def _stub_youtube_track(self, video_id: str, *, artist: str, title: str, wav_path: Path, duration_s: float):
        def fake_resolve_from_youtube(vid, **kwargs):
            self.assertEqual(vid, video_id)
            return music_resolve.ResolvedTrack(
                artist=artist, title=title, duration_s=duration_s, video_id=video_id,
                spotify_id=None, canonical_id=identity.canonical_id(artist, title, duration_s),
                audio_source="youtube",
            )

        def fake_resolve_audio(data_root, vid, **kwargs):
            self.assertEqual(vid, video_id)
            return wav_path

        music_resolve.resolve_from_youtube = fake_resolve_from_youtube
        PLUGIN.music_resolve.resolve_from_youtube = fake_resolve_from_youtube
        yta.resolve_audio = fake_resolve_audio
        PLUGIN.yta.resolve_audio = fake_resolve_audio


class Case1To4Tests(PluginTestBase):
    def test_1_youtube_music_link_generates_perception(self):
        self._stub_youtube_track("vid00000001", artist="Test Artist", title="Test Song", wav_path=self.wav_a, duration_s=60.0)
        result = PLUGIN.on_pre_llm_call(
            user_message="https://www.youtube.com/watch?v=vid00000001 이거 들어봐",
            session_id="conv-1", platform="discord",
        )
        self.assertIsNotNone(result)
        self.assertIn("music_perception", result["context"])
        active = PLUGIN._get_store().get_active("conv-1")
        self.assertIsNotNone(active)
        self.assertEqual(active.title, "Test Song")
        cache = music_cache.load(self.wiki_root, active.canonical_id)
        self.assertIsNotNone(cache)
        self.assertEqual(cache["perception_version"], mp.PERCEPTION_VERSION)

    def test_2_resharing_same_track_hits_cache_no_reanalysis(self):
        self._stub_youtube_track("vid00000002", artist="Cache Artist", title="Cache Song", wav_path=self.wav_a, duration_s=60.0)
        PLUGIN.on_pre_llm_call(user_message="https://youtu.be/vid00000002 들어봐", session_id="conv-2a", platform="discord")
        self.assertEqual(self.analyze_call_count, 1)
        PLUGIN.on_post_llm_call(session_id="conv-2a", user_message="이 노래 얘기 그만", assistant_response="ㅇㅋ", platform="discord")

        # a second, unrelated conversation mentions the same track later
        PLUGIN.on_pre_llm_call(user_message="https://youtu.be/vid00000002 이거 어때", session_id="conv-2b", platform="discord")
        self.assertEqual(self.analyze_call_count, 1, "second mention of the same track must not re-run DSP analysis")

    def test_3_spotify_and_youtube_same_track_share_one_canonical_id(self):
        artist, title, duration = "Shared Artist", "Shared Song", 60.0
        self._stub_youtube_track("vid00000003", artist=artist, title=title, wav_path=self.wav_a, duration_s=duration)
        PLUGIN.on_pre_llm_call(user_message="https://youtu.be/vid00000003 들어봐", session_id="conv-3a", platform="discord")
        yt_canonical = PLUGIN._get_store().get_active("conv-3a").canonical_id
        PLUGIN.on_post_llm_call(session_id="conv-3a", user_message="이 노래 얘기 그만", assistant_response="ㅇㅋ", platform="discord")

        def fake_resolve_from_spotify(spotify_id, **kwargs):
            return music_resolve.ResolvedTrack(
                artist=artist, title=title, duration_s=duration, video_id="vid00000003",
                spotify_id=spotify_id, canonical_id=identity.canonical_id(artist, title, duration),
                audio_source="spotify_resolved_to_youtube",
            )

        music_resolve.resolve_from_spotify = fake_resolve_from_spotify
        PLUGIN.music_resolve.resolve_from_spotify = fake_resolve_from_spotify

        PLUGIN.on_pre_llm_call(
            user_message="https://open.spotify.com/track/abc123spotify 이거 들어봐", session_id="conv-3b", platform="discord",
        )
        sp_canonical = PLUGIN._get_store().get_active("conv-3b").canonical_id
        self.assertEqual(yt_canonical, sp_canonical)
        # conv-3a's earlier finalize already wrote the one Track note for this canonical id
        track_files = list((self.wiki_root / "Music" / "Tracks").glob("*.md"))
        self.assertEqual(len(track_files), 1)
        # finalizing conv-3b (same canonical id) must update that same file, not create a second one
        PLUGIN.on_post_llm_call(session_id="conv-3b", user_message="이 노래 얘기 그만", assistant_response="ㅇㅋ", platform="discord")
        track_files = list((self.wiki_root / "Music" / "Tracks").glob("*.md"))
        self.assertEqual(len(track_files), 1)

    def test_4_ordinary_youtube_link_without_music_intent_does_not_trigger(self):
        self._stub_youtube_track("vid00000004", artist="X", title="Y", wav_path=self.wav_a, duration_s=60.0)
        result = PLUGIN.on_pre_llm_call(
            user_message="https://www.youtube.com/watch?v=vid00000004 이 강의 요약 좀 해줘",
            session_id="conv-4", platform="discord",
        )
        self.assertIsNone(result)
        self.assertIsNone(PLUGIN._get_store().get_active("conv-4"))
        self.assertEqual(self.analyze_call_count, 0)


class Case5To8Tests(PluginTestBase):
    def test_5_raw_dsp_numbers_never_leak_into_context(self):
        self._stub_youtube_track("vid00000005", artist="Num Artist", title="Num Song", wav_path=self.wav_a, duration_s=60.0)
        result = PLUGIN.on_pre_llm_call(user_message="https://youtu.be/vid00000005 들어봐", session_id="conv-5", platform="discord")
        ctx = result["context"]
        for forbidden in ("rms", "centroid_hz", "spectral_flux", "onset_env", "energy_1hz", "brightness_1hz"):
            self.assertNotIn(forbidden, ctx)

    def test_6_timestamp_followup_retrieves_scoped_window(self):
        self._stub_youtube_track("vid00000006", artist="TS Artist", title="TS Song", wav_path=self.wav_a, duration_s=60.0)
        PLUGIN.on_pre_llm_call(user_message="https://youtu.be/vid00000006 들어봐", session_id="conv-6", platform="discord")
        PLUGIN.on_post_llm_call(session_id="conv-6", user_message="https://youtu.be/vid00000006 들어봐", assistant_response="오 좋다", platform="discord")

        result = PLUGIN.on_pre_llm_call(user_message="30초 부분 뭐야?", session_id="conv-6", platform="discord")
        self.assertIsNotNone(result)
        self.assertIn("music_window", result["context"])
        self.assertIn("center_s: 30", result["context"])

    def test_7_instrument_question_escalates_without_hallucinating(self):
        # Deep Ear forced unavailable here so this test deterministically
        # exercises Phase 1's shallow fallback path regardless of whether the
        # environment running the suite happens to have the isolated
        # torch/demucs venv — DeepEarIntegrationTests covers the rich path.
        self._stub_youtube_track("vid00000007", artist="Deep Artist", title="Deep Song", wav_path=self.wav_a, duration_s=60.0)
        PLUGIN.on_pre_llm_call(user_message="https://youtu.be/vid00000007 들어봐", session_id="conv-7", platform="discord")
        PLUGIN.on_post_llm_call(session_id="conv-7", user_message="https://youtu.be/vid00000007 들어봐", assistant_response="오 좋다", platform="discord")

        with mock.patch.object(PLUGIN.deep_ear, "is_available", return_value=False):
            handler = PLUGIN._make_music_deep_dive_handler(None)
            out = handler({"time_s": 20}, session_id="conv-7")
        self.assertIn("music_deep_dive_evidence", out)
        for forbidden in ("기타", "피아노", "드럼", "guitar", "piano", "drum", "코드 진행"):
            self.assertNotIn(forbidden, out)
        self.assertIn("악기명이나 코드", out)

    def test_8_followup_content_accumulates_in_session_state(self):
        self._stub_youtube_track("vid00000008", artist="Acc Artist", title="Acc Song", wav_path=self.wav_a, duration_s=60.0)
        PLUGIN.on_pre_llm_call(user_message="https://youtu.be/vid00000008 들어봐", session_id="conv-8", platform="discord")
        PLUGIN.on_post_llm_call(session_id="conv-8", user_message="https://youtu.be/vid00000008 들어봐", assistant_response="오 이거 좋다", platform="discord")
        PLUGIN.on_post_llm_call(session_id="conv-8", user_message="나는 후렴보다 그 직전이 더 좋음", assistant_response="아 그거 취향 좋다ㅋㅋ", platform="discord")

        active = PLUGIN._get_store().get_active("conv-8")
        self.assertEqual(active.turn_count, 2)
        self.assertGreaterEqual(len(active.exchange_log), 4)
        self.assertTrue(any("좋음" in o for o in active.user_opinions))
        self.assertTrue(any("좋다" in o for o in active.agent_opinions))


class Case9To14Tests(PluginTestBase):
    def test_9_topic_end_creates_weekly_and_track_archive(self):
        self._stub_youtube_track("vid00000009", artist="Arch Artist", title="Arch Song", wav_path=self.wav_a, duration_s=60.0)
        PLUGIN.on_pre_llm_call(user_message="https://youtu.be/vid00000009 들어봐", session_id="conv-9", platform="discord")
        PLUGIN.on_post_llm_call(session_id="conv-9", user_message="https://youtu.be/vid00000009 들어봐", assistant_response="오 좋다", platform="discord")
        PLUGIN.on_post_llm_call(session_id="conv-9", user_message="이 노래 얘기 그만", assistant_response="ㅇㅋ 재밌었음", platform="discord")

        self.assertIsNone(PLUGIN._get_store().get_active("conv-9"))
        weekly_files = list((self.wiki_root / "Music" / "Weekly").glob("*.md"))
        self.assertEqual(len(weekly_files), 1)
        track_files = list((self.wiki_root / "Music" / "Tracks").glob("*.md"))
        self.assertEqual(len(track_files), 1)
        self.assertIn("Arch Artist", track_files[0].read_text(encoding="utf-8"))

    def test_10_weekly_entry_links_back_to_conversation_id_not_full_dump(self):
        self._stub_youtube_track("vid00000010", artist="Ref Artist", title="Ref Song", wav_path=self.wav_a, duration_s=60.0)
        PLUGIN.on_pre_llm_call(user_message="https://youtu.be/vid00000010 들어봐", session_id="conv-10", platform="discord")
        PLUGIN.on_post_llm_call(session_id="conv-10", user_message="https://youtu.be/vid00000010 들어봐", assistant_response="오 좋다", platform="discord")
        PLUGIN.on_post_llm_call(session_id="conv-10", user_message="이 노래 얘기 그만", assistant_response="ㅇㅋ", platform="discord")

        weekly_file = next((self.wiki_root / "Music" / "Weekly").glob("*.md"))
        text = weekly_file.read_text(encoding="utf-8")
        self.assertIn("Ref Artist", text)
        self.assertIn("Ref Song", text)
        # Weekly is a readable index/diary, not the raw SSOT — the full turn
        # log stays in memory-core's own conversation archive, not duplicated
        # here (task item 20/15). Weekly holds real quotes, not a JSON dump.
        self.assertNotIn("centroid_hz", text)
        self.assertNotIn("rms", text)

    def test_11_multiple_sessions_same_track_link_to_one_track_note(self):
        artist, title, duration = "Multi Artist", "Multi Song", 60.0
        self._stub_youtube_track("vid00000011", artist=artist, title=title, wav_path=self.wav_a, duration_s=duration)

        PLUGIN.on_pre_llm_call(user_message="https://youtu.be/vid00000011 들어봐", session_id="conv-11a", platform="discord")
        PLUGIN.on_post_llm_call(session_id="conv-11a", user_message="https://youtu.be/vid00000011 들어봐", assistant_response="오 좋다", platform="discord")
        PLUGIN.on_post_llm_call(session_id="conv-11a", user_message="이 노래 진짜 좋다", assistant_response="그치ㅋㅋ", platform="discord")
        PLUGIN.on_post_llm_call(session_id="conv-11a", user_message="이 노래 얘기 그만", assistant_response="ㅇㅋ", platform="discord")

        PLUGIN.on_pre_llm_call(user_message="https://youtu.be/vid00000011 다시 들어봐", session_id="conv-11b", platform="discord")
        PLUGIN.on_post_llm_call(session_id="conv-11b", user_message="https://youtu.be/vid00000011 다시 들어봐", assistant_response="또 들어도 좋네", platform="discord")
        PLUGIN.on_post_llm_call(session_id="conv-11b", user_message="이 노래 얘기 그만", assistant_response="ㅇㅋ", platform="discord")

        track_files = list((self.wiki_root / "Music" / "Tracks").glob("*.md"))
        self.assertEqual(len(track_files), 1)
        text = track_files[0].read_text(encoding="utf-8")
        self.assertIn("times_discussed: 2", text)
        self.assertIn("이 노래 진짜 좋다", text)
        self.assertIn("나중 감상", text)  # second session's initial reaction preserved, not overwritten (task item 22)

    def test_12_audio_resolve_failure_never_fabricates_a_listen(self):
        def failing_resolve_from_youtube(vid, **kwargs):
            return music_resolve.ResolvedTrack(
                artist="Fail Artist", title="Fail Song", duration_s=60.0, video_id=vid,
                spotify_id=None, canonical_id=identity.canonical_id("Fail Artist", "Fail Song", 60.0),
                audio_source="youtube",
            )

        def failing_resolve_audio(data_root, vid, **kwargs):
            raise yta.AudioResolveError("simulated network failure")

        music_resolve.resolve_from_youtube = failing_resolve_from_youtube
        PLUGIN.music_resolve.resolve_from_youtube = failing_resolve_from_youtube
        yta.resolve_audio = failing_resolve_audio
        PLUGIN.yta.resolve_audio = failing_resolve_audio

        result = PLUGIN.on_pre_llm_call(user_message="https://youtu.be/vid00000012 들어봐", session_id="conv-12", platform="discord")
        self.assertIsNotNone(result)
        self.assertIn("들은 척", result["context"])
        self.assertIsNone(PLUGIN._get_store().get_active("conv-12"))
        self.assertEqual(self.analyze_call_count, 0)

    def test_13_youtube_archive_own_link_extraction_untouched(self):
        # shared-music reuses yt_video_link but must not monkeypatch or wrap it
        # in a way that would affect youtube-archive's own behavior.
        video_id = music_link.extract_youtube_video_id("https://www.youtube.com/watch?v=zzzzzzzzzzz")
        self.assertEqual(video_id, "zzzzzzzzzzz")

    def test_14_ordinary_conversation_is_a_pure_noop(self):
        result = PLUGIN.on_pre_llm_call(user_message="오늘 저녁 뭐 먹지", session_id="conv-14", platform="discord")
        self.assertIsNone(result)
        PLUGIN.on_post_llm_call(session_id="conv-14", user_message="오늘 저녁 뭐 먹지", assistant_response="글쎄ㅋㅋ", platform="discord")
        self.assertIsNone(PLUGIN._get_store().get_active("conv-14"))
        self.assertFalse((self.wiki_root / "Music").exists())


class DspUnitTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wav = Path(self._tmp.name) / "song.wav"
        _make_wav(self.wav, dur_s=90.0, seed=42)

    def tearDown(self):
        self._tmp.cleanup()

    def test_analyze_produces_finite_arrays(self):
        raw = dsp.analyze(str(self.wav))
        self.assertTrue(np.all(np.isfinite(raw.energy_1hz)))
        self.assertTrue(np.all(np.isfinite(raw.brightness_1hz)))
        self.assertGreater(raw.duration_s, 89.0)

    def test_phase_detection_finds_the_build_up(self):
        raw = dsp.analyze(str(self.wav))
        phases = dsp.detect_phases(raw.energy_1hz)
        self.assertGreaterEqual(len(phases), 2)

    def test_hpss_ratio_is_normalized(self):
        raw = dsp.analyze(str(self.wav))
        freqs, mag, _ = dsp._stft_mag(dsp.load_audio_mono(str(self.wav))[0])
        h, p = dsp.harmonic_percussive_ratio(mag[:200])
        self.assertAlmostEqual(h + p, 1.0, places=3)


class IdentityAndIntentTests(unittest.TestCase):
    def test_normalize_strips_official_video_noise(self):
        a = identity.canonical_id("Artist", "Song Title (Official Audio)", 200)
        b = identity.canonical_id("artist", "song title", 200)
        self.assertEqual(a, b)

    def test_duration_tolerance_bucket(self):
        self.assertEqual(identity.duration_bucket(200), identity.duration_bucket(202))
        self.assertNotEqual(identity.duration_bucket(200), identity.duration_bucket(230))

    def test_music_intent_phrases(self):
        self.assertTrue(music_intent.has_music_intent_phrase("이거 들어봐"))
        self.assertTrue(music_intent.has_music_intent_phrase("이 노래 좋지 않냐"))
        self.assertFalse(music_intent.has_music_intent_phrase("이 강의 요약해줘"))

    def test_spotify_link_detection(self):
        link = music_link.detect_link("https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC?si=abc")
        self.assertIsNotNone(link)
        self.assertEqual(link.source, "spotify")

    def test_youtube_music_host_is_unambiguous(self):
        link = music_link.detect_link("https://music.youtube.com/watch?v=abcdefghijk 이거")
        self.assertIsNotNone(link)
        self.assertTrue(link.is_music_host)


class SpotifyResolutionTests(unittest.TestCase):
    def test_native_client_preferred_over_oembed_when_authenticated(self):
        oembed_called = []

        def fake_native(track_id):
            return {"title": "Native Title", "artist": "Native Artist", "duration_s": 201.234}

        def fake_oembed(track_id):
            oembed_called.append(track_id)
            return {"title": "Oembed Title - Oembed Artist"}

        def fake_search(query, max_results=5):
            self.assertIn("Native Artist", query)
            return [{"video_id": "vidnative01", "title": "Native Title (Official Audio)", "channel": "Native Artist - Topic", "duration_s": 201}]

        def fake_meta(video_id):
            return {"title": "Native Title", "artist": "Native Artist", "duration_s": 201}

        resolved = music_resolve.resolve_from_spotify(
            "abc123", native_fn=fake_native, oembed_fn=fake_oembed, search_fn=fake_search, metadata_fn=fake_meta,
        )
        self.assertEqual(resolved.title, "Native Title")
        self.assertEqual(resolved.duration_s, 201.234)
        self.assertEqual(oembed_called, [])  # oEmbed never called when native succeeded

    def test_falls_back_to_oembed_when_native_unavailable(self):
        def fake_native(track_id):
            return None  # not authenticated

        def fake_oembed(track_id):
            return {"title": "Fallback Title - Fallback Artist"}

        def fake_search(query, max_results=5):
            return [{"video_id": "vidfallback1", "title": "Fallback Title", "channel": "Fallback Artist - Topic", "duration_s": 180}]

        def fake_meta(video_id):
            return {"title": "Fallback Title", "artist": "Fallback Artist", "duration_s": 180}

        resolved = music_resolve.resolve_from_spotify(
            "def456", native_fn=fake_native, oembed_fn=fake_oembed, search_fn=fake_search, metadata_fn=fake_meta,
        )
        self.assertEqual(resolved.title, "Fallback Title")
        self.assertEqual(resolved.duration_s, 180)

    def test_native_fetch_gracefully_returns_none_when_unavailable(self):
        # Degrades to None (never raises) both when hermes_cli/plugins.spotify
        # aren't importable (this plugin's own dev env) and, inside the real
        # process, when the user hasn't run `hermes auth spotify` yet.
        import music_spotify_resolver as spotify_mod
        self.assertIsNone(spotify_mod.fetch_spotify_track_native("whatever"))


class WindowRetrievalTests(unittest.TestCase):
    def test_parse_various_timestamp_formats(self):
        self.assertEqual(mwr.parse_timestamp_s("1:23"), 83)
        self.assertEqual(mwr.parse_timestamp_s("2분 14초"), 134)
        self.assertEqual(mwr.parse_timestamp_s("73초쯤"), 73)

    def test_structural_reference_mapping(self):
        self.assertEqual(mwr.parse_structural_reference("후렴 부분이 좋아"), "peak")
        self.assertEqual(mwr.parse_structural_reference("도입부는 별로"), "first")


class ObsidianDedupTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wiki_root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_upsert_track_note_dedupes_repeated_bullets(self):
        perception = mp.build_perception(
            title="T", artist="A",
            raw=dsp.RawAnalysis(
                duration_s=60, frame_times=np.array([0.0]), rms=np.array([0.1]), centroid_hz=np.array([100.0]),
                flux=np.array([0.1]), onset_env=np.array([0.1]), chroma=np.zeros((1, 12)), band_energy=np.zeros((1, 3)),
                tempo_bpm=100.0, tempo_confidence=0.5, energy_1hz=np.array([0.1] * 60), brightness_1hz=np.array([100.0] * 60),
                flux_1hz=np.array([0.1] * 60), onset_1hz=np.array([0.1] * 60), mean_chroma=np.zeros(12), tonal_stability=0.8,
            ),
            phases=[{"start": 0, "end": 60, "mean_energy": 0.1}], events=[],
        )
        cache = music_cache.save_new_analysis(
            wiki_root=self.wiki_root, canonical_id="cid1", artist="A", title="T",
            youtube_video_id="v1", spotify_track_id=None, audio_source="youtube",
            perception=perception,
            raw=dsp.RawAnalysis(
                duration_s=60, frame_times=np.array([0.0]), rms=np.array([0.1]), centroid_hz=np.array([100.0]),
                flux=np.array([0.1]), onset_env=np.array([0.1]), chroma=np.zeros((1, 12)), band_energy=np.zeros((1, 3)),
                tempo_bpm=100.0, tempo_confidence=0.5, energy_1hz=np.array([0.1] * 60), brightness_1hz=np.array([100.0] * 60),
                flux_1hz=np.array([0.1] * 60), onset_1hz=np.array([0.1] * 60), mean_chroma=np.zeros(12), tonal_stability=0.8,
            ),
        )
        session = music_session.MusicConversationSession(
            conversation_id="c1", canonical_id="cid1", title="T", artist="A", video_id="v1", spotify_id=None,
            initial_impression="첫 감상", user_opinions=["좋다"],
        )
        music_obsidian.upsert_track_note(wiki_root=self.wiki_root, cache=cache, session=session, is_first_session=True, week="2026-W32")
        session2 = music_session.MusicConversationSession(
            conversation_id="c2", canonical_id="cid1", title="T", artist="A", video_id="v1", spotify_id=None,
            user_opinions=["좋다", "새로운 의견"],
        )
        music_obsidian.upsert_track_note(wiki_root=self.wiki_root, cache=cache, session=session2, is_first_session=False, week="2026-W33")
        path = music_obsidian.track_note_path(self.wiki_root, "A", "T")
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count("- 좋다"), 1)
        self.assertIn("새로운 의견", text)


class WeeklyRolloverTests(unittest.TestCase):
    """task item 25 — Asia/Seoul week boundary, not UTC."""

    def test_sunday_2359_kst_and_monday_0000_kst_are_different_weeks(self):
        import music_weekly as weekly
        from datetime import datetime
        from zoneinfo import ZoneInfo

        kst = ZoneInfo("Asia/Seoul")
        sunday_2359 = datetime(2026, 8, 9, 23, 59, tzinfo=kst).timestamp()
        monday_0000 = datetime(2026, 8, 10, 0, 0, 1, tzinfo=kst).timestamp()
        self.assertNotEqual(weekly.week_key(sunday_2359), weekly.week_key(monday_0000))
        self.assertEqual(weekly.week_key(sunday_2359), "2026-W32")
        self.assertEqual(weekly.week_key(monday_0000), "2026-W33")

    def test_utc_time_that_is_still_monday_in_kst_uses_kst_week(self):
        # 2026-08-09 16:00 UTC == 2026-08-10 01:00 KST (already Monday in Seoul)
        import music_weekly as weekly
        from datetime import datetime, timezone

        utc_ts = datetime(2026, 8, 9, 16, 0, tzinfo=timezone.utc).timestamp()
        self.assertEqual(weekly.week_key(utc_ts), "2026-W33")


class WeeklySameWeekDedupTests(PluginTestBase):
    def test_same_track_same_week_updates_one_entry_not_two(self):
        self._stub_youtube_track("vidweekly01", artist="Weekly Artist", title="Weekly Song", wav_path=self.wav_a, duration_s=60.0)
        PLUGIN.on_pre_llm_call(user_message="https://youtu.be/vidweekly01 들어봐", session_id="conv-w1", platform="discord")
        PLUGIN.on_post_llm_call(session_id="conv-w1", user_message="https://youtu.be/vidweekly01 들어봐", assistant_response="오 좋다", platform="discord")
        PLUGIN.on_post_llm_call(session_id="conv-w1", user_message="이 노래 얘기 그만", assistant_response="ㅇㅋ", platform="discord")

        PLUGIN.on_pre_llm_call(user_message="https://youtu.be/vidweekly01 다시 들어봐", session_id="conv-w2", platform="discord")
        PLUGIN.on_post_llm_call(session_id="conv-w2", user_message="https://youtu.be/vidweekly01 다시 들어봐", assistant_response="여전히 좋네", platform="discord")
        PLUGIN.on_post_llm_call(session_id="conv-w2", user_message="이 노래 얘기 그만", assistant_response="ㅇㅋ", platform="discord")

        weekly_files = list((self.wiki_root / "Music" / "Weekly").glob("*.md"))
        self.assertEqual(len(weekly_files), 1)
        text = weekly_files[0].read_text(encoding="utf-8")
        self.assertEqual(text.count("Weekly Artist — Weekly Song"), 1)  # one track header, not two
        self.assertIn("나중에 더 얘기한 것", text)
        self.assertIn("여전히 좋네", text)  # the second conversation's content was actually folded in

    def test_multiple_different_songs_same_week_get_separate_entries(self):
        self._stub_youtube_track("vidweekly02", artist="First Artist", title="First Song", wav_path=self.wav_a, duration_s=60.0)
        PLUGIN.on_pre_llm_call(user_message="https://youtu.be/vidweekly02 들어봐", session_id="conv-w3", platform="discord")
        PLUGIN.on_post_llm_call(session_id="conv-w3", user_message="https://youtu.be/vidweekly02 들어봐", assistant_response="좋다", platform="discord")
        PLUGIN.on_post_llm_call(session_id="conv-w3", user_message="이 노래 얘기 그만", assistant_response="ㅇㅋ", platform="discord")

        self._stub_youtube_track("vidweekly03", artist="Second Artist", title="Second Song", wav_path=self.wav_b, duration_s=45.0)
        PLUGIN.on_pre_llm_call(user_message="https://youtu.be/vidweekly03 들어봐", session_id="conv-w4", platform="discord")
        PLUGIN.on_post_llm_call(session_id="conv-w4", user_message="https://youtu.be/vidweekly03 들어봐", assistant_response="이것도 좋다", platform="discord")
        PLUGIN.on_post_llm_call(session_id="conv-w4", user_message="이 노래 얘기 그만", assistant_response="ㅇㅋ", platform="discord")

        weekly_files = list((self.wiki_root / "Music" / "Weekly").glob("*.md"))
        self.assertEqual(len(weekly_files), 1, "same week -> same weekly file")
        text = weekly_files[0].read_text(encoding="utf-8")
        self.assertIn("First Artist — First Song", text)
        self.assertIn("Second Artist — Second Song", text)

    def test_same_track_next_week_gets_new_weekly_entry_same_track_note(self):
        import music_weekly as weekly

        self._stub_youtube_track("vidweekly04", artist="Recur Artist", title="Recur Song", wav_path=self.wav_a, duration_s=60.0)
        PLUGIN.on_pre_llm_call(user_message="https://youtu.be/vidweekly04 들어봐", session_id="conv-w5", platform="discord")
        PLUGIN.on_post_llm_call(session_id="conv-w5", user_message="https://youtu.be/vidweekly04 들어봐", assistant_response="좋다", platform="discord")
        PLUGIN.on_post_llm_call(session_id="conv-w5", user_message="이 노래 얘기 그만", assistant_response="ㅇㅋ", platform="discord")

        # simulate "next week" by directly calling the finalize path with a
        # started_at 8 days later, rather than waiting a real week
        active = PLUGIN._get_store()
        session2_conv = "conv-w6"
        PLUGIN.on_pre_llm_call(user_message="https://youtu.be/vidweekly04 다시 들어봐", session_id=session2_conv, platform="discord")
        s2 = PLUGIN._get_store().get_active(session2_conv)
        s2.started_at -= 8 * 86400  # push into a different ISO week
        PLUGIN._get_store().save(s2)
        PLUGIN.on_post_llm_call(session_id=session2_conv, user_message="https://youtu.be/vidweekly04 다시 들어봐", assistant_response="또 좋네", platform="discord")
        PLUGIN.on_post_llm_call(session_id=session2_conv, user_message="이 노래 얘기 그만", assistant_response="ㅇㅋ", platform="discord")

        weekly_files = list((self.wiki_root / "Music" / "Weekly").glob("*.md"))
        self.assertEqual(len(weekly_files), 2, "different week -> a second weekly file")
        track_files = list((self.wiki_root / "Music" / "Tracks").glob("*.md"))
        self.assertEqual(len(track_files), 1, "but still one accumulated Track note")
        track_text = track_files[0].read_text(encoding="utf-8")
        self.assertEqual(track_text.count("[[Music/Weekly/"), 2)  # backlinked to both weeks


class ArtistDocThresholdTests(PluginTestBase):
    def test_single_track_artist_gets_no_artist_doc(self):
        self._stub_youtube_track("vidartist01", artist="Solo Artist", title="Only Song", wav_path=self.wav_a, duration_s=60.0)
        PLUGIN.on_pre_llm_call(user_message="https://youtu.be/vidartist01 들어봐", session_id="conv-a1", platform="discord")
        PLUGIN.on_post_llm_call(session_id="conv-a1", user_message="https://youtu.be/vidartist01 들어봐", assistant_response="좋다", platform="discord")
        PLUGIN.on_post_llm_call(session_id="conv-a1", user_message="이 노래 얘기 그만", assistant_response="ㅇㅋ", platform="discord")

        self.assertFalse((self.wiki_root / "Music" / "Artists").exists() and
                          list((self.wiki_root / "Music" / "Artists").glob("*.md")))

    def test_second_track_crosses_threshold_and_creates_artist_doc(self):
        self._stub_youtube_track("vidartist02", artist="Prolific Artist", title="Song One", wav_path=self.wav_a, duration_s=60.0)
        PLUGIN.on_pre_llm_call(user_message="https://youtu.be/vidartist02 들어봐", session_id="conv-a2", platform="discord")
        PLUGIN.on_post_llm_call(session_id="conv-a2", user_message="https://youtu.be/vidartist02 들어봐", assistant_response="좋다", platform="discord")
        PLUGIN.on_post_llm_call(session_id="conv-a2", user_message="이 노래 얘기 그만", assistant_response="ㅇㅋ", platform="discord")

        self._stub_youtube_track("vidartist03", artist="Prolific Artist", title="Song Two", wav_path=self.wav_b, duration_s=45.0)
        PLUGIN.on_pre_llm_call(user_message="https://youtu.be/vidartist03 들어봐", session_id="conv-a3", platform="discord")
        PLUGIN.on_post_llm_call(session_id="conv-a3", user_message="https://youtu.be/vidartist03 들어봐", assistant_response="이것도 좋다", platform="discord")
        PLUGIN.on_post_llm_call(session_id="conv-a3", user_message="이 노래 얘기 그만", assistant_response="ㅇㅋ", platform="discord")

        artist_files = list((self.wiki_root / "Music" / "Artists").glob("*.md"))
        self.assertEqual(len(artist_files), 1)
        text = artist_files[0].read_text(encoding="utf-8")
        self.assertIn("Song One", text)
        self.assertIn("Song Two", text)


class DeepEarIntegrationTests(PluginTestBase):
    """Demucs/librosa live in an isolated venv this test suite doesn't have
    (and shouldn't need — a real run takes ~25-45s, inappropriate for a unit
    test). music_deep_ear.analyze_window is monkeypatched to return a canned
    observation, same "mock the expensive/external boundary" strategy as
    Phase 1's network mocking — everything downstream (routing by aspect,
    interpretation, hallucination guard, graceful degradation) runs for real.
    """

    _CANNED_OBSERVATION = {
        "schema_version": "1.0.0", "analyzer_version": "test-version",
        "window_s": [10.0, 20.0],
        "instrumentation": {"sources": [
            {"label": "lead_vocal", "confidence": 0.6}, {"label": "drums", "confidence": 0.3},
            {"label": "bass", "confidence": 0.05}, {"label": "other_harmonic_accompaniment", "confidence": 0.05},
        ]},
        "rhythm": {"tempo_bpm": 120.0, "beat_count": 10, "drum_density_per_s": 2.0,
                    "drum_density_trend": "rising", "syncopation": 0.4, "confidence": 0.7},
        "harmony": {"key_guess": "A minor", "key_confidence": 0.6, "tension_trend": "rising",
                     "chord_sequence": [
                         {"start_s": 10.0, "end_s": 10.5, "label": "Amin", "confidence": 0.3},
                         {"start_s": 10.5, "end_s": 11.0, "label": "Dmin", "confidence": 0.3},
                     ]},
        "melody": {"contour": "rising", "range_semitones": 10.0, "register": "mid", "repeated_motif": True, "confidence": 0.5},
        "vocal": {"presence": 0.6, "register": "mid", "layering_detected": True, "dynamics_trend": "rising"},
        "production": {"stereo_width": "wide", "density": "dense", "space_note": "spacious_leaning"},
    }

    def _setup_active_session_with_cache(self, conv_id: str, video_id: str):
        self._stub_youtube_track(video_id, artist="Deep Artist", title="Deep Song", wav_path=self.wav_a, duration_s=60.0)
        PLUGIN.on_pre_llm_call(user_message=f"https://youtu.be/{video_id} 들어봐", session_id=conv_id, platform="discord")
        PLUGIN.on_post_llm_call(session_id=conv_id, user_message=f"https://youtu.be/{video_id} 들어봐", assistant_response="오 좋다", platform="discord")

    def test_aspect_routing_shows_only_requested_category(self):
        self._setup_active_session_with_cache("conv-deep1", "viddeep0001")
        with mock.patch.object(PLUGIN.deep_ear, "analyze_window", return_value=dict(self._CANNED_OBSERVATION)):
            handler = PLUGIN._make_music_deep_dive_handler(None)
            out = handler({"time_s": 15, "aspect": "rhythm"}, session_id="conv-deep1")
        self.assertIn("drum_density_trend", out)
        self.assertNotIn("stereo_width", out)  # production category excluded when aspect=rhythm

    def test_low_confidence_chords_are_hedged_not_asserted(self):
        self._setup_active_session_with_cache("conv-deep2", "viddeep0002")
        with mock.patch.object(PLUGIN.deep_ear, "analyze_window", return_value=dict(self._CANNED_OBSERVATION)):
            handler = PLUGIN._make_music_deep_dive_handler(None)
            out = handler({"time_s": 15, "aspect": "harmony"}, session_id="conv-deep2")
        self.assertIn("certainty: low", out)  # the canned chords are all confidence=0.3, below rollup threshold

    def test_instrument_names_beyond_the_4_stems_never_appear(self):
        # only the <deep_listening> DATA block is checked — the instructional
        # prose around it legitimately names 기타/피아노/신스 as examples of
        # what NOT to claim, which isn't itself a hallucinated claim.
        self._setup_active_session_with_cache("conv-deep3", "viddeep0003")
        with mock.patch.object(PLUGIN.deep_ear, "analyze_window", return_value=dict(self._CANNED_OBSERVATION)):
            handler = PLUGIN._make_music_deep_dive_handler(None)
            out = handler({"time_s": 15, "aspect": "instrumentation"}, session_id="conv-deep3")
        data_block = out.split("<deep_listening>", 1)[1].split("</deep_listening>", 1)[0]
        for forbidden in ("기타", "피아노", "신스", "guitar", "piano", "synth"):
            self.assertNotIn(forbidden, data_block)

    def test_deep_ear_failure_falls_back_to_phase1_shallow_dive(self):
        self._setup_active_session_with_cache("conv-deep4", "viddeep0004")
        with mock.patch.object(PLUGIN.deep_ear, "analyze_window", return_value=None):  # isolated venv missing/failed
            handler = PLUGIN._make_music_deep_dive_handler(None)
            out = handler({"time_s": 15}, session_id="conv-deep4")
        self.assertIn("music_deep_dive_evidence", out)  # Phase 1's fallback formatter, not a bare error


class DeepEarCacheTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wiki_root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_save_and_load_roundtrip(self):
        import music_deep_ear_cache as deep_cache

        result = {"analyzer_version": "v1", "foo": "bar"}
        deep_cache.save(self.wiki_root, "cid1", 10.0, 20.0, result)
        loaded = deep_cache.load(self.wiki_root, "cid1", 10.0, 20.0)
        self.assertEqual(loaded, result)
        self.assertTrue(deep_cache.is_current(loaded, "v1"))
        self.assertFalse(deep_cache.is_current(loaded, "v2"))

    def test_different_window_is_a_different_cache_entry(self):
        import music_deep_ear_cache as deep_cache

        deep_cache.save(self.wiki_root, "cid1", 10.0, 20.0, {"analyzer_version": "v1"})
        self.assertIsNone(deep_cache.load(self.wiki_root, "cid1", 30.0, 40.0))


class ChordSummarizationTests(unittest.TestCase):
    def test_high_confidence_stable_chord_rolls_up_cleanly(self):
        import music_deep_ear as deep_ear_mod

        harmony = {
            "key_guess": "C major", "key_confidence": 0.8,
            "chord_sequence": [
                {"start_s": 0, "end_s": 2, "label": "Cmaj", "confidence": 0.85},
                {"start_s": 2, "end_s": 4, "label": "Cmaj", "confidence": 0.82},
                {"start_s": 4, "end_s": 6, "label": "Gmaj", "confidence": 0.8},
            ],
        }
        summary = deep_ear_mod.summarize_chords(harmony)
        self.assertEqual(summary["certainty"], "moderate")
        self.assertIn("Cmaj", summary["dominant_chords"])

    def test_flickering_low_confidence_chords_marked_uncertain(self):
        import music_deep_ear as deep_ear_mod

        harmony = {
            "key_guess": "unknown", "key_confidence": 0.1,
            "chord_sequence": [
                {"start_s": 0, "end_s": 0.4, "label": "Cmaj", "confidence": 0.4},
                {"start_s": 0.4, "end_s": 0.7, "label": "Dmin", "confidence": 0.35},
            ],
        }
        summary = deep_ear_mod.summarize_chords(harmony)
        self.assertTrue(summary["certainty"].startswith("low"))
        self.assertEqual(summary["dominant_chords"], [])


class RetrievalWeeklyArtistTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wiki_root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_weekly_summary_phrase_triggers_lookup(self):
        import music_retrieval as retrieval
        import music_weekly as weekly

        weekly.upsert_first_listen(
            wiki_root=self.wiki_root, ts=time.time(), canonical_id="cid1", artist="A", title="T",
            source_url="https://x", agent_first="좋다",
        )
        tracks = retrieval.search_weekly_summary("이번 주에 뭐 들었지?", self.wiki_root)
        self.assertIsNotNone(tracks)
        self.assertTrue(any("A — T" in t for t in tracks))

    def test_unrelated_message_does_not_trigger_weekly_summary(self):
        import music_retrieval as retrieval

        self.assertIsNone(retrieval.search_weekly_summary("이번 주에 뭐 했지", self.wiki_root))

    def test_artist_lookup_finds_matching_artist_doc(self):
        import music_obsidian as obsidian
        import music_retrieval as retrieval

        artists_dir = self.wiki_root / "Music" / "Artists"
        artists_dir.mkdir(parents=True)
        (artists_dir / "Radiohead.md").write_text(
            "# Radiohead\n\n# 같이 들은 곡\n\n- [[Music/Tracks/Radiohead - Nude]]\n- [[Music/Tracks/Radiohead - Weird Fishes]]\n",
            encoding="utf-8",
        )
        result = retrieval.search_artist("내가 Radiohead 뭐 들어봤지", self.wiki_root)
        self.assertIsNotNone(result)
        self.assertEqual(result["artist"], "Radiohead")
        self.assertEqual(len(result["tracks"]), 2)


if __name__ == "__main__":
    unittest.main()
