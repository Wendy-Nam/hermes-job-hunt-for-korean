"""Named regressions for the YouTube resolution / audio-acquisition failure
modes that took shared-music down in production (trace: `track_resolved_failed
source=youtube id=JeucohIa5LQ`).

Kept separate from test_plugin.py because these exercise the *transport* layer
— the yt-dlp attempt chain, the oEmbed fallback, error classification, cache
and temp-file safety — rather than the perception/session/Obsidian behaviour
test_plugin.py already covers.

Network is faked at the yt_dlp module boundary (a stub injected into
sys.modules) instead of by patching our own functions, so the real
option-building, transport ordering, classification and retry logic all
execute for real. Same "mock only the network edge" rule the sibling suites
use.
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
_VENDOR_DIR = HERE / ".vendor"
if _VENDOR_DIR.is_dir() and str(_VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(_VENDOR_DIR))

import music_failure as failure  # noqa: E402
import music_link  # noqa: E402
import music_resolve  # noqa: E402
import music_track_identity as identity  # noqa: E402
import music_youtube_audio as yta  # noqa: E402
import music_youtube_oembed as oembed  # noqa: E402

BOT_CHECK_MSG = (
    "ERROR: [youtube] JeucohIa5LQ: Sign in to confirm you’re not a bot. "
    "Use --cookies-from-browser or --cookies for the authentication."
)


# --------------------------------------------------------------------------
# URL normalization / identity (cases 1-5, 10)
# --------------------------------------------------------------------------
class UrlNormalizationTests(unittest.TestCase):
    """Every YouTube Music URL shape must reduce to the same video id, and the
    playlist/tracking context must never reach track identity."""

    def test_case1_plain_youtube_watch_url(self):
        link = music_link.detect_link("https://www.youtube.com/watch?v=JeucohIa5LQ")
        self.assertIsNotNone(link)
        self.assertEqual(link.id, "JeucohIa5LQ")
        self.assertFalse(link.is_music_host)

    def test_case2_youtu_be_short_url(self):
        link = music_link.detect_link("https://youtu.be/JeucohIa5LQ")
        self.assertEqual(link.id, "JeucohIa5LQ")

    def test_case3_youtube_music_watch_url(self):
        link = music_link.detect_link("https://music.youtube.com/watch?v=JeucohIa5LQ")
        self.assertEqual(link.id, "JeucohIa5LQ")
        # music.youtube.com is itself the music-intent signal — no Korean
        # intent phrase should be required for a YT Music link to trigger.
        self.assertTrue(link.is_music_host)

    def test_case4_youtube_music_url_with_tracking_params(self):
        link = music_link.detect_link(
            "https://music.youtube.com/watch?v=JeucohIa5LQ&si=abc123XYZ&feature=share"
        )
        self.assertEqual(link.id, "JeucohIa5LQ")
        self.assertTrue(link.is_music_host)

    def test_case5_playlist_context_does_not_pollute_track_identity(self):
        link = music_link.detect_link(
            "https://music.youtube.com/watch?v=JeucohIa5LQ&list=RDAMVMJeucohIa5LQ&index=1"
        )
        self.assertEqual(link.id, "JeucohIa5LQ")
        # The URL we hand to yt-dlp is rebuilt from the id, so `&list=` can
        # never turn a single-track request into a playlist extraction.
        self.assertEqual(link.url, "https://www.youtube.com/watch?v=JeucohIa5LQ")
        self.assertNotIn("list=", link.url)

    def test_case10_two_url_forms_yield_one_canonical_identity(self):
        forms = [
            "https://www.youtube.com/watch?v=JeucohIa5LQ",
            "https://youtu.be/JeucohIa5LQ?t=42",
            "https://music.youtube.com/watch?v=JeucohIa5LQ&si=xyz&list=RDAMVMJeucohIa5LQ",
            "https://www.youtube.com/embed/JeucohIa5LQ",
        ]
        ids = {music_link.detect_link(u).id for u in forms}
        self.assertEqual(ids, {"JeucohIa5LQ"})
        canonical = {
            identity.canonical_id("Gary Numan", "M.E.", 337) for _ in forms
        }
        self.assertEqual(len(canonical), 1)


# --------------------------------------------------------------------------
# Error classification (cases 7, 8, 13)
# --------------------------------------------------------------------------
class FailureClassificationTests(unittest.TestCase):
    def test_bot_check_is_classified_and_retryable(self):
        reason = failure.classify_error(RuntimeError(BOT_CHECK_MSG))
        self.assertEqual(reason, failure.REASON_BOT_CHECK)
        self.assertTrue(failure.is_retryable(reason))

    def test_bot_check_not_mislabelled_as_private(self):
        """'Sign in to confirm you're not a bot' contains 'sign in'; ordering
        in the signature table is what keeps it from reading as a private
        video, so pin it."""
        self.assertEqual(
            failure.classify_error(RuntimeError(BOT_CHECK_MSG)), failure.REASON_BOT_CHECK
        )

    def test_case13_missing_dependency_is_its_own_reason(self):
        reason = failure.classify_error(ModuleNotFoundError("No module named 'yt_dlp'"))
        self.assertEqual(reason, failure.REASON_DEPENDENCY_MISSING)
        # A missing dependency is not fixed by another egress route.
        self.assertFalse(failure.is_retryable(reason))

    def test_case8_transient_timeout_is_retryable(self):
        self.assertEqual(failure.classify_error(TimeoutError("timed out")), failure.REASON_TIMEOUT)
        self.assertTrue(failure.is_retryable(failure.REASON_TIMEOUT))

    def test_terminal_reasons_are_not_retried(self):
        for msg, expected in (
            ("This video is DRM protected", failure.REASON_DRM),
            ("Private video. Sign in if you've been granted access", failure.REASON_PRIVATE),
            ("Video unavailable", failure.REASON_REMOVED),
        ):
            with self.subTest(msg=msg):
                reason = failure.classify_error(RuntimeError(msg))
                self.assertEqual(reason, expected)
                self.assertFalse(failure.is_retryable(reason))

    def test_unknown_error_stays_retryable(self):
        """An unrecognised failure must not be treated as terminal — the
        classifier is advisory and should fail toward trying again."""
        self.assertEqual(failure.classify_error(RuntimeError("kaboom")), failure.REASON_UNKNOWN)
        self.assertTrue(failure.is_retryable(failure.REASON_UNKNOWN))


# --------------------------------------------------------------------------
# Transport fallback chain
# --------------------------------------------------------------------------
class TransportFallbackTests(unittest.TestCase):
    def setUp(self):
        yta.reset_transport_memo()

    def tearDown(self):
        yta.reset_transport_memo()

    def test_proxy_is_attempted_after_direct_bot_check(self):
        """The production bug in one test: the metadata stage had no proxy
        leg at all, so a bot-checked direct call ended the whole pipeline
        even though a working route existed one stage later."""
        seen = []

        def run(proxy):
            seen.append(proxy)
            if proxy is None:
                raise RuntimeError(BOT_CHECK_MSG)
            return {"ok": True}

        with mock.patch.object(yta, "_resolved_proxy", return_value="http://proxy:1"):
            result, label = yta._run_with_fallback(
                run, stage=failure.STAGE_METADATA, video_id="v"
            )
        self.assertEqual(seen, [None, "http://proxy:1"])
        self.assertEqual(result, {"ok": True})
        self.assertEqual(label, "proxy")

    def test_terminal_reason_skips_the_proxy_attempt(self):
        seen = []

        def run(proxy):
            seen.append(proxy)
            raise RuntimeError("This video is DRM protected")

        with mock.patch.object(yta, "_resolved_proxy", return_value="http://proxy:1"):
            with self.assertRaises(failure.MusicPipelineError) as ctx:
                yta._run_with_fallback(run, stage=failure.STAGE_METADATA, video_id="v")
        self.assertEqual(seen, [None])  # no wasted second round trip
        self.assertEqual(ctx.exception.reason, failure.REASON_DRM)

    def test_direct_block_memo_skips_doomed_direct_attempt(self):
        """The memo saves the *same video* a second doomed direct round trip."""
        seen = []

        def run(proxy):
            seen.append(proxy)
            if proxy is None:
                raise RuntimeError(BOT_CHECK_MSG)
            return "ok"

        with mock.patch.object(yta, "_resolved_proxy", return_value="http://proxy:1"):
            yta._run_with_fallback(run, stage=failure.STAGE_METADATA, video_id="v")
            seen.clear()
            yta._run_with_fallback(run, stage=failure.STAGE_METADATA, video_id="v")
        self.assertEqual(seen, ["http://proxy:1"], "direct should be skipped while memoised as blocked")

    def test_block_memo_does_not_poison_a_different_video(self):
        """One gated link must not send every later video down the proxy.

        The memo used to be a single process-wide deadline, so a bot-checked
        Art Track disabled direct egress for fifteen minutes for everything
        that followed. The bot check is per-video: measured 2026-08-11 from the
        production container, `JeucohIa5LQ` bot_checks on every player_client
        while `dQw4w9WgXcQ` resolves direct in 1.2s from the same address. The
        proxy leg measured 3-5s against direct's ~1s, so the old memo made the
        requests that were still working three times slower to save a round
        trip on the ones that weren't.
        """
        seen = []

        def run(proxy):
            seen.append(proxy)
            if proxy is None:
                raise RuntimeError(BOT_CHECK_MSG)
            return "ok"

        with mock.patch.object(yta, "_resolved_proxy", return_value="http://proxy:1"):
            yta._run_with_fallback(run, stage=failure.STAGE_METADATA, video_id="gated")
            seen.clear()
            yta._run_with_fallback(run, stage=failure.STAGE_METADATA, video_id="other")
        self.assertEqual(
            seen[0], None,
            "a different video must still get its direct attempt — the block is per-video",
        )

    def test_proxy_is_not_resolved_when_direct_succeeds(self):
        """Enumerating a proxy costs a liveness probe and, on a cold proxynet
        cache, a Webshare call — measured at 6.4s in the production container.
        Paying it to prepare a fallback that is never used made a successful
        ordinary link take 8.3s instead of 1.9s."""
        calls = []

        def run(proxy):
            return "ok"

        def _resolve():
            calls.append(1)
            return "http://proxy:1"

        with mock.patch.object(yta, "_resolved_proxy", side_effect=_resolve):
            yta._run_with_fallback(run, stage=failure.STAGE_METADATA, video_id="v")
        self.assertEqual(calls, [], "the proxy must not be resolved until direct has failed")

    def test_egress_gate_needs_several_videos_and_never_skips_a_transport(self):
        """`egress_looks_gated` is a hint for skipping *speculative* work only."""

        def run(proxy):
            raise RuntimeError(BOT_CHECK_MSG)

        with mock.patch.object(yta, "_resolved_proxy", return_value="http://proxy:1"):
            with self.assertRaises(failure.MusicPipelineError):
                yta._run_with_fallback(run, stage=failure.STAGE_METADATA, video_id="v1")
            self.assertFalse(yta.egress_looks_gated(), "one gated video is not an egress verdict")
            with self.assertRaises(failure.MusicPipelineError):
                yta._run_with_fallback(run, stage=failure.STAGE_METADATA, video_id="v2")
            self.assertTrue(yta.egress_looks_gated())

            # ...and even then, a fresh video still gets both transports tried.
            seen = []
            with self.assertRaises(failure.MusicPipelineError):
                yta._run_with_fallback(
                    lambda proxy: (seen.append(proxy), (_ for _ in ()).throw(RuntimeError(BOT_CHECK_MSG)))[1],
                    stage=failure.STAGE_METADATA, video_id="v3",
                )
            self.assertEqual(seen, [None, "http://proxy:1"])

    def test_a_non_bot_failure_does_not_count_toward_the_egress_gate(self):
        """Only a bot check is evidence about gating. A DRM video or a dead
        network says nothing about whether YouTube is refusing this address."""

        def run(proxy):
            raise RuntimeError("boom")

        with mock.patch.object(yta, "_resolved_proxy", return_value="http://proxy:1"):
            for vid in ("a", "b", "c"):
                with self.assertRaises(failure.MusicPipelineError):
                    yta._run_with_fallback(run, stage=failure.STAGE_METADATA, video_id=vid)
        self.assertFalse(yta.egress_looks_gated())

    def test_block_memo_is_bounded(self):
        """A long-lived gateway process must not accumulate one entry per
        video it has ever been shown."""
        for i in range(yta._MAX_BLOCK_MEMO_ENTRIES + 50):
            yta._note_direct_blocked(f"v{i}")
        self.assertLessEqual(len(yta._direct_blocked_until), yta._MAX_BLOCK_MEMO_ENTRIES)

    def test_no_proxy_available_still_attempts_direct(self):
        seen = []

        def run(proxy):
            seen.append(proxy)
            return "ok"

        with mock.patch.object(yta, "_resolved_proxy", return_value=None):
            result, label = yta._run_with_fallback(run, stage=failure.STAGE_METADATA, video_id="v")
        self.assertEqual(seen, [None])
        self.assertEqual((result, label), ("ok", "direct"))

    def test_failure_carries_stage_and_reason(self):
        def run(proxy):
            raise RuntimeError(BOT_CHECK_MSG)

        with mock.patch.object(yta, "_resolved_proxy", return_value=None):
            with self.assertRaises(failure.MusicPipelineError) as ctx:
                yta._run_with_fallback(run, stage=failure.STAGE_DOWNLOAD, video_id="abc")
        self.assertEqual(ctx.exception.stage, failure.STAGE_DOWNLOAD)
        self.assertEqual(ctx.exception.reason, failure.REASON_BOT_CHECK)
        self.assertEqual(ctx.exception.trace_fields()["video_id"], "abc")


# --------------------------------------------------------------------------
# oEmbed metadata fallback + degraded resolution (cases 6, 7, 14)
# --------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class OembedFallbackTests(unittest.TestCase):
    def test_topic_channel_suffix_is_stripped(self):
        """The real payload measured for JeucohIa5LQ."""
        resp = _FakeResponse({"title": "M.E.", "author_name": "Gary Numan - Topic"})
        meta = oembed.fetch_youtube_oembed("JeucohIa5LQ", opener=lambda *a, **k: resp)
        self.assertEqual(meta["title"], "M.E.")
        self.assertEqual(meta["artist"], "Gary Numan")
        self.assertIsNone(meta["duration_s"])

    def test_oembed_failure_returns_none_not_raises(self):
        def boom(*a, **k):
            raise OSError("network down")

        self.assertIsNone(oembed.fetch_youtube_oembed("x", opener=boom))

    def test_oembed_and_ytdlp_agree_on_identity_inputs(self):
        """Both metadata paths must produce the same canonical-identity
        inputs, or a blocked track would fork into a second Track note the
        day the block lifts."""
        resp = _FakeResponse({"title": "M.E.", "author_name": "Gary Numan - Topic"})
        via_oembed = oembed.fetch_youtube_oembed("JeucohIa5LQ", opener=lambda *a, **k: resp)
        # what yt-dlp returns for the same video (track/artist fields)
        via_ytdlp = {"title": "M.E.", "artist": "Gary Numan", "duration_s": 337}
        self.assertEqual(
            identity.artist_title_key(via_oembed["artist"], via_oembed["title"]),
            identity.artist_title_key(via_ytdlp["artist"], via_ytdlp["title"]),
        )


class DegradedResolutionTests(unittest.TestCase):
    """resolve_from_youtube's behaviour when the player API refuses the video."""

    def _blocked_detail(self, *_a, **_k):
        return None, failure.REASON_BOT_CHECK

    def test_case6_metadata_only_when_blocked_and_no_alternate(self):
        resolved = music_resolve.resolve_from_youtube(
            "JeucohIa5LQ",
            detail_fn=self._blocked_detail,
            oembed_fn=lambda vid: {"title": "M.E.", "artist": "Gary Numan", "duration_s": None},
            search_fn=lambda q, **k: [],
        )
        self.assertEqual(resolved.title, "M.E.")
        self.assertEqual(resolved.artist, "Gary Numan")
        self.assertEqual(resolved.audio_source, "youtube_metadata_only")
        self.assertFalse(resolved.playable, "must not claim a playable track")

    def test_case7_hard_failure_when_even_oembed_fails(self):
        with self.assertRaises(music_resolve.TrackResolveError) as ctx:
            music_resolve.resolve_from_youtube(
                "JeucohIa5LQ", detail_fn=self._blocked_detail, oembed_fn=lambda vid: None
            )
        self.assertEqual(ctx.exception.reason, failure.REASON_BOT_CHECK)
        self.assertEqual(ctx.exception.stage, failure.STAGE_METADATA)

    def test_alternate_upload_is_used_when_playable(self):
        def detail(vid, **_k):
            if vid == "JeucohIa5LQ":
                return None, failure.REASON_BOT_CHECK
            return {"title": "M.E.", "artist": "Gary Numan", "duration_s": 337}, None

        resolved = music_resolve.resolve_from_youtube(
            "JeucohIa5LQ",
            detail_fn=detail,
            oembed_fn=lambda vid: {"title": "M.E.", "artist": "Gary Numan", "duration_s": None},
            search_fn=lambda q, **k: [
                {"video_id": "ALTERNATE01", "title": "Gary Numan - M.E. (Official Audio)",
                 "channel": "Gary Numan", "duration_s": 337},
            ],
        )
        self.assertEqual(resolved.video_id, "ALTERNATE01")
        self.assertEqual(resolved.audio_source, "youtube_alternate_upload")
        self.assertTrue(resolved.playable)

    def test_alternate_upload_rejects_live_and_cover_versions(self):
        """Wrong-song collision guard: a live take is a different recording,
        so it must not silently become 'the' track."""
        resolved = music_resolve.resolve_from_youtube(
            "JeucohIa5LQ",
            detail_fn=self._blocked_detail,
            oembed_fn=lambda vid: {"title": "M.E.", "artist": "Gary Numan", "duration_s": None},
            search_fn=lambda q, **k: [
                {"video_id": "LIVEVERSION", "title": "Gary Numan - M.E. (Live at Wembley 2022)",
                 "channel": "Official Gary Numan", "duration_s": 289},
            ],
        )
        self.assertEqual(resolved.audio_source, "youtube_metadata_only")
        self.assertNotEqual(resolved.video_id, "LIVEVERSION")

    def test_alternate_upload_never_reuses_the_blocked_id(self):
        resolved = music_resolve.resolve_from_youtube(
            "JeucohIa5LQ",
            detail_fn=self._blocked_detail,
            oembed_fn=lambda vid: {"title": "M.E.", "artist": "Gary Numan", "duration_s": None},
            search_fn=lambda q, **k: [
                {"video_id": "JeucohIa5LQ", "title": "M.E.", "channel": "Gary Numan - Topic",
                 "duration_s": 338},
            ],
        )
        self.assertEqual(resolved.audio_source, "youtube_metadata_only")

    def test_blocked_art_track_does_not_crowd_out_the_playable_runner_up(self):
        """Observed live: the blocked Art Track wins its own search (exact
        title + artist + the 'Topic' channel bonus), so filtering it only
        *after* scoring threw away the playable alternative behind it."""
        def detail(vid, **_k):
            if vid == "JeucohIa5LQ":
                return None, failure.REASON_BOT_CHECK
            return {"title": "M.E.", "artist": "Gary Numan", "duration_s": 337}, None

        resolved = music_resolve.resolve_from_youtube(
            "JeucohIa5LQ",
            detail_fn=detail,
            oembed_fn=lambda vid: {"title": "M.E.", "artist": "Gary Numan", "duration_s": None},
            search_fn=lambda q, **k: [
                # highest-scoring, but it is the blocked video itself
                {"video_id": "JeucohIa5LQ", "title": "M.E.", "channel": "Gary Numan - Topic",
                 "duration_s": 338},
                # runner-up, playable
                {"video_id": "RUNNERUP001", "title": "Gary Numan - M.E. (1979)",
                 "channel": "darcyw014", "duration_s": 337},
            ],
        )
        self.assertEqual(resolved.video_id, "RUNNERUP001")
        self.assertEqual(resolved.audio_source, "youtube_alternate_upload")

    def test_successful_metadata_still_takes_the_fast_path(self):
        """No oEmbed / search round trips when the player API answers."""
        oembed_calls = []
        resolved = music_resolve.resolve_from_youtube(
            "dQw4w9WgXcQ",
            detail_fn=lambda vid, **_k: ({"title": "Never Gonna Give You Up",
                                          "artist": "Rick Astley", "duration_s": 213}, None),
            oembed_fn=lambda vid: oembed_calls.append(vid),
            search_fn=lambda q, **k: [],
        )
        self.assertEqual(resolved.audio_source, "youtube")
        self.assertTrue(resolved.playable)
        self.assertEqual(oembed_calls, [])


# --------------------------------------------------------------------------
# Audio cache / temp-file safety (cases 9, 11, 12, 17)
# --------------------------------------------------------------------------
def _fake_ytdlp_module(on_download):
    """Minimal stand-in for the yt_dlp package, injected into sys.modules so
    the real option-building and staging/rename logic runs."""
    mod = types.ModuleType("yt_dlp")

    class _YDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def download(self, urls):
            on_download(self.opts, urls)

        def extract_info(self, url, download=False):
            return on_download(self.opts, [url])

    utils = types.ModuleType("yt_dlp.utils")
    utils.match_filter_func = lambda expr: expr
    mod.YoutubeDL = _YDL
    mod.utils = utils
    return mod


class AudioCacheAndTempFileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_root = Path(self._tmp.name)
        yta.reset_transport_memo()

    def tearDown(self):
        self._tmp.cleanup()
        yta.reset_transport_memo()
        sys.modules.pop("yt_dlp", None)

    def _cache_dir(self):
        return self.data_root / "audio_cache" / "shared-music"

    def test_case11_zero_byte_cached_file_is_not_a_cache_hit(self):
        d = self._cache_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "vid1.wav").write_bytes(b"")
        self.assertIsNone(yta.cached_audio_path(self.data_root, "vid1"))

    def test_case9_nonempty_cached_file_is_a_cache_hit(self):
        d = self._cache_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "vid1.wav").write_bytes(b"RIFFdata")
        events = []
        path = yta.resolve_audio(self.data_root, "vid1", trace_fn=lambda e, **f: events.append(e))
        self.assertEqual(path, d / "vid1.wav")
        self.assertIn("audio_cache_hit", events)

    def test_case11b_download_producing_zero_bytes_is_an_error_not_a_cache_entry(self):
        def on_download(opts, urls):
            # yt-dlp "succeeds" but writes an empty file, the real failure
            # mode behind a track that plays as silence forever after.
            staged = Path(opts["outtmpl"].replace(".%(ext)s", ".wav"))
            staged.write_bytes(b"")

        sys.modules["yt_dlp"] = _fake_ytdlp_module(on_download)
        with mock.patch.object(yta, "_resolved_proxy", return_value=None):
            with self.assertRaises(yta.AudioResolveError):
                yta.resolve_audio(self.data_root, "vid2")
        self.assertIsNone(yta.cached_audio_path(self.data_root, "vid2"))

    def test_case17_staging_files_are_cleaned_up_on_failure(self):
        def on_download(opts, urls):
            staged = Path(opts["outtmpl"].replace(".%(ext)s", ".wav"))
            staged.write_bytes(b"partial")
            raise RuntimeError("connection reset")

        sys.modules["yt_dlp"] = _fake_ytdlp_module(on_download)
        with mock.patch.object(yta, "_resolved_proxy", return_value=None):
            with self.assertRaises(yta.AudioResolveError):
                yta.resolve_audio(self.data_root, "vid3")
        leftovers = list(self._cache_dir().glob("*"))
        self.assertEqual(leftovers, [], f"temp files left behind: {leftovers}")

    def test_successful_download_publishes_atomically(self):
        def on_download(opts, urls):
            staged = Path(opts["outtmpl"].replace(".%(ext)s", ".wav"))
            staged.write_bytes(b"RIFFrealaudio")

        sys.modules["yt_dlp"] = _fake_ytdlp_module(on_download)
        with mock.patch.object(yta, "_resolved_proxy", return_value=None):
            path = yta.resolve_audio(self.data_root, "vid4")
        self.assertEqual(path.read_bytes(), b"RIFFrealaudio")
        self.assertEqual([p.name for p in self._cache_dir().glob("*")], ["vid4.wav"])

    def test_case12_concurrent_same_track_downloads_do_not_corrupt_the_cache(self):
        barrier = threading.Barrier(2)

        def on_download(opts, urls):
            staged = Path(opts["outtmpl"].replace(".%(ext)s", ".wav"))
            barrier.wait(timeout=10)  # force maximum overlap
            staged.write_bytes(b"RIFF" + b"x" * 512)

        sys.modules["yt_dlp"] = _fake_ytdlp_module(on_download)
        results, errors = [], []

        def worker():
            try:
                with mock.patch.object(yta, "_resolved_proxy", return_value=None):
                    results.append(yta.resolve_audio(self.data_root, "vid5"))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        # Exactly one published file, full length, and no .part debris.
        names = sorted(p.name for p in self._cache_dir().glob("*"))
        self.assertEqual(names, ["vid5.wav"])
        self.assertEqual((self._cache_dir() / "vid5.wav").stat().st_size, 516)

    def test_cookiefile_is_opt_in_and_absent_by_default(self):
        with mock.patch.dict("os.environ", {"HERMES_DATA": str(self.data_root)}):
            self.assertIsNone(yta.cookie_file())
            self.assertNotIn("cookiefile", yta._base_opts(None))
            (self.data_root / ".youtube-cookies.txt").write_text("# Netscape HTTP Cookie File\n")
            self.assertIsNotNone(yta.cookie_file())
            self.assertIn("cookiefile", yta._base_opts(None))


if __name__ == "__main__":
    unittest.main()
