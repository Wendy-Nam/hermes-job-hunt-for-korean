"""The fetch ladder, route health, and media validation.

Mocking rule, inherited from test_resolver_reliability: fake the *network
edge* only. The download callable is stubbed; the ordering, budget, backoff,
breaker, validation, dedup and result construction all execute for real, so
these tests fail when that logic breaks rather than when a mock drifts.

Audio files are synthesised as real RIFF/WAVE headers with a real declared
length, because half of what is under test is "does this look like a truncated
wav" — a mock of the validator would test nothing.
"""
from __future__ import annotations

import random
import struct
import sys
import tempfile
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
for extra in (HERE, HERE.parent / "_shared"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import music_failure as failure  # noqa: E402
import music_media_fetch as mfetch  # noqa: E402
import music_media_validate as validate  # noqa: E402
import music_route_health as health  # noqa: E402
from hermes_media_fetch import (  # noqa: E402
    ROUTE_ALTERNATE_ROUTE,
    ROUTE_ALTERNATE_SOURCE,
    ROUTE_CACHE,
    ROUTE_DIRECT,
    ROUTE_PROXY,
    FetchNotSuccessful,
    MediaSource,
)

MEDIA_ID = "music:abc123"
VIDEO_ID = "JeucohIa5LQ"


def wav_bytes(payload_len: int = 8192, *, declared_len: int | None = None) -> bytes:
    """A minimal but structurally honest RIFF/WAVE file."""
    data = b"\x00" * payload_len
    declared = declared_len if declared_len is not None else 36 + len(data)
    return (
        b"RIFF" + struct.pack("<I", declared) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 22050, 44100, 2, 16)
        + b"data" + struct.pack("<I", len(data)) + data
    )


def write_wav(path: Path, **kw) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(wav_bytes(**kw))
    return path


class _Boom(Exception):
    """A transport failure carrying a pre-classified reason, like the real one."""

    def __init__(self, reason: str, stage: str = failure.STAGE_DOWNLOAD):
        self.reason = reason
        self.stage = stage
        super().__init__(f"{stage}/{reason}")


class LadderTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.now = [1_000_000]
        self.slept: list[float] = []
        self.health = health.RouteHealthStore(
            self.root / "health.json", now_ms=lambda: self.now[0]
        )

    def source(self, **kw) -> MediaSource:
        return mfetch.music_source(VIDEO_ID, media_id_=MEDIA_ID, **kw)

    def ladder(self, download, **kw) -> mfetch.MusicFetchLadder:
        kw.setdefault("proxy_provider", lambda: "http://proxy:1")
        kw.setdefault("health_store", self.health)
        # A stub decoder, because the host interpreter these suites run under
        # has neither ffprobe nor soundfile. That absence is itself correct
        # behaviour — default_probe returns None and the ladder reports
        # codec_unsupported rather than pretending — and
        # test_unsupported_codec_is_reported_as_such pins it. Everywhere else
        # we want the *transport* logic under test, not the missing decoder.
        kw.setdefault(
            "validator",
            lambda p, **kwargs: validate.validate_audio_file(
                p, probe=lambda _p: 200.0, **kwargs
            ),
        )
        return mfetch.MusicFetchLadder(
            cache_dir=self.root / "audio",
            download=download,
            sleep=self.slept.append,
            monotonic_ms=lambda: self.now[0],
            rng=random.Random(1234),
            **kw,
        )

    # -- happy paths ----------------------------------------------------------
    def test_direct_success(self):
        def dl(vid, out, proxy):
            self.assertIsNone(proxy)
            write_wav(out)

        result = self.ladder(dl).fetch(self.source())
        self.assertTrue(result.ok)
        self.assertEqual(result.route, ROUTE_DIRECT)
        self.assertTrue(Path(result.local_ref).exists())
        self.assertEqual(result.content_type, "audio/wav")
        result.require_ok()  # must not raise

    def test_cache_success_makes_no_network_attempt(self):
        calls = []
        ladder = self.ladder(lambda v, o, p: calls.append(v))
        write_wav(ladder.cached_path(self.source()))

        result = ladder.fetch(self.source())
        self.assertTrue(result.ok)
        self.assertEqual(result.route, ROUTE_CACHE)
        self.assertEqual(calls, [], "a cache hit must not touch the network")
        self.assertEqual(ladder.metrics.snapshot()["cache_hit_rate"], 1.0)

    def test_direct_fail_then_proxy_success(self):
        seen = []

        def dl(vid, out, proxy):
            seen.append(proxy)
            if proxy is None:
                raise _Boom(failure.REASON_BOT_CHECK)
            write_wav(out)

        result = self.ladder(dl).fetch(self.source())
        self.assertTrue(result.ok)
        self.assertEqual(result.route, ROUTE_PROXY)
        self.assertEqual(result.attempts, (ROUTE_CACHE, ROUTE_DIRECT, ROUTE_PROXY))
        self.assertEqual(seen, [None, "http://proxy:1"])

    def test_proxy_fail_then_alternate_route_success(self):
        def dl(vid, out, proxy):
            if proxy != "http://alt:2":
                raise _Boom(failure.REASON_TIMEOUT)
            write_wav(out)

        result = self.ladder(dl, alternate_route_provider=lambda: "http://alt:2").fetch(
            self.source()
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.route, ROUTE_ALTERNATE_ROUTE)

    def test_alternate_source_tier_tries_a_different_upload(self):
        def dl(vid, out, proxy):
            if vid == VIDEO_ID:
                raise _Boom(failure.REASON_REMOVED)  # terminal for *this* upload
            write_wav(out)

        result = self.ladder(
            dl, alternate_source_finder=lambda src: "OTHERVID123"
        ).fetch(self.source())
        self.assertTrue(result.ok)
        self.assertEqual(result.route, ROUTE_ALTERNATE_SOURCE)
        self.assertEqual(result.source.native_id, "OTHERVID123")
        self.assertEqual(result.source.media_id, MEDIA_ID,
                         "an alternate upload is the same song, so the same media identity")

    def test_unconfigured_tier_is_skipped_not_faked(self):
        def dl(vid, out, proxy):
            raise _Boom(failure.REASON_NETWORK)

        result = self.ladder(dl, proxy_provider=lambda: None).fetch(self.source())
        self.assertFalse(result.ok)
        self.assertNotIn(ROUTE_PROXY, result.attempts)

    # -- failure paths --------------------------------------------------------
    def test_all_routes_fail_produces_an_honest_failure(self):
        def dl(vid, out, proxy):
            raise _Boom(failure.REASON_NETWORK)

        result = self.ladder(dl).fetch(self.source())
        self.assertFalse(result.ok)
        self.assertEqual(result.local_ref, "", "a failure may never carry a local_ref")
        self.assertEqual(result.reason, failure.REASON_NETWORK)
        self.assertIn(result.stage, ("download", "audio_resolve", "decode"))
        self.assertTrue(result.retryable)
        with self.assertRaises(FetchNotSuccessful):
            result.require_ok()

    def test_timeout_is_classified_and_retried(self):
        attempts = []

        def dl(vid, out, proxy):
            attempts.append(proxy)
            raise _Boom(failure.REASON_TIMEOUT)

        result = self.ladder(dl).fetch(self.source())
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, failure.REASON_TIMEOUT)
        self.assertEqual(len(attempts), 2, "timeout is retryable, so both routes are tried")

    def test_403_bot_check_is_retried_over_the_proxy(self):
        def dl(vid, out, proxy):
            if proxy is None:
                raise _Boom(failure.REASON_BOT_CHECK)
            write_wav(out)

        result = self.ladder(dl).fetch(self.source())
        self.assertTrue(result.ok)
        self.assertEqual(result.route, ROUTE_PROXY)

    def test_429_style_terminal_reason_does_not_burn_every_route(self):
        attempts = []

        def dl(vid, out, proxy):
            attempts.append(proxy)
            raise _Boom(failure.REASON_DRM)  # terminal on any egress

        result = self.ladder(dl).fetch(self.source())
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, failure.REASON_DRM)
        self.assertEqual(len(attempts), 1,
                         "a DRM verdict is identical over every route — retrying only costs time")

    def test_expired_url_triggers_one_refresh_then_succeeds(self):
        state = {"refreshed": False}

        def dl(vid, out, proxy):
            if not state["refreshed"]:
                raise _Boom(failure.REASON_NOT_FOUND)
            write_wav(out)

        def refresh(src):
            state["refreshed"] = True
            return mfetch.music_source("FRESHVID999", media_id_=src.media_id)

        ladder = self.ladder(dl, refresh_source=refresh)
        result = ladder.fetch(self.source())
        self.assertTrue(result.ok)
        self.assertEqual(ladder.metrics.refreshes, 1)
        self.assertEqual(result.source.native_id, "FRESHVID999")

    def test_attempt_budget_is_bounded(self):
        attempts = []

        def dl(vid, out, proxy):
            attempts.append(proxy)
            raise _Boom(failure.REASON_NETWORK)

        ladder = self.ladder(
            dl,
            alternate_route_provider=lambda: "http://alt:2",
            alternate_source_finder=lambda s: "ALT1",
            max_attempts=2,
        )
        ladder.fetch(self.source())
        self.assertEqual(len(attempts), 2, "the per-item budget must bound total attempts")

    def test_backoff_is_applied_between_network_attempts(self):
        def dl(vid, out, proxy):
            raise _Boom(failure.REASON_NETWORK)

        self.ladder(dl).fetch(self.source())
        self.assertTrue(self.slept, "a retry must wait before hammering the next route")
        self.assertTrue(all(0 < s < 5 for s in self.slept), self.slept)

    # -- validation -----------------------------------------------------------
    def test_corrupt_download_is_rejected_and_deleted(self):
        def dl(vid, out, proxy):
            out.parent.mkdir(parents=True, exist_ok=True)
            # header claims far more than the file holds: a killed download
            out.write_bytes(wav_bytes(4096, declared_len=10_000_000))

        ladder = self.ladder(dl)
        result = ladder.fetch(self.source())
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, failure.REASON_CORRUPT_MEDIA)
        self.assertFalse(ladder.cached_path(self.source()).exists(),
                         "a corrupt artifact must never survive to become a cache hit")

    def test_html_error_page_saved_as_audio_is_not_audio(self):
        def dl(vid, out, proxy):
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"<!DOCTYPE html><html><body>403 Forbidden</body></html>" * 100)

        result = self.ladder(dl).fetch(self.source())
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, failure.REASON_MIME_MISMATCH)

    def test_unsupported_codec_is_reported_as_such(self):
        def dl(vid, out, proxy):
            write_wav(out)

        def probe(path):
            return None  # no decoder could open it

        ladder = self.ladder(
            dl,
            validator=lambda p, **kw: validate.validate_audio_file(p, probe=probe, **kw),
        )
        result = ladder.fetch(self.source())
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, failure.REASON_CODEC_UNSUPPORTED)

    def test_duration_mismatch_rejects_a_partial_recording(self):
        def dl(vid, out, proxy):
            write_wav(out)

        ladder = self.ladder(
            dl,
            validator=lambda p, **kw: validate.validate_audio_file(
                p, probe=lambda _p: 12.0, **kw
            ),
        )
        result = ladder.fetch(self.source(), expected_duration_s=337.0)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, failure.REASON_CORRUPT_MEDIA)

    def test_poisoned_cache_entry_is_evicted_on_read(self):
        ladder = self.ladder(lambda v, o, p: write_wav(o))
        path = ladder.cached_path(self.source())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(wav_bytes(4096, declared_len=10_000_000))  # truncated

        result = ladder.fetch(self.source())
        self.assertTrue(result.ok)
        self.assertEqual(result.route, ROUTE_DIRECT,
                         "the truncated cache entry must be rejected, not served")
        self.assertEqual(ladder.metrics.cache_rejected, 1)

    # -- dedup ----------------------------------------------------------------
    def test_concurrent_fetches_of_one_track_download_once(self):
        downloads = []
        gate = threading.Event()

        def dl(vid, out, proxy):
            downloads.append(vid)
            gate.wait(timeout=5)
            write_wav(out)

        ladder = self.ladder(dl)
        results = {}

        def run(tag):
            results[tag] = ladder.fetch(self.source())

        first = threading.Thread(target=run, args=("a",))
        first.start()
        while not downloads:
            pass  # first thread has claimed the in-flight slot
        second = threading.Thread(target=run, args=("b",))
        second.start()
        gate.set()
        first.join(timeout=10)
        second.join(timeout=10)

        self.assertTrue(results["a"].ok and results["b"].ok)
        self.assertEqual(len(downloads), 1, "a duplicate concurrent fetch must be suppressed")
        self.assertEqual(ladder.metrics.dedup_suppressed, 1)


class RouteHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.now = [0]
        self.store = health.RouteHealthStore(
            Path(self._tmp.name) / "h.json", now_ms=lambda: self.now[0]
        )

    def test_breaker_opens_after_consecutive_retryable_failures(self):
        for _ in range(health.BREAKER_THRESHOLD):
            self.store.record_failure("youtube", ROUTE_DIRECT, reason=failure.REASON_BOT_CHECK)
        self.assertFalse(self.store.available("youtube", ROUTE_DIRECT))

        self.now[0] += health.BREAKER_BASE_COOLDOWN_MS + 1
        self.assertTrue(self.store.available("youtube", ROUTE_DIRECT),
                        "the breaker must heal on its own, without a restart")

    def test_terminal_reasons_never_open_a_breaker(self):
        for _ in range(10):
            self.store.record_failure("youtube", ROUTE_PROXY, reason=failure.REASON_DRM)
        self.assertTrue(
            self.store.available("youtube", ROUTE_PROXY),
            "one DRM-protected video must not take a healthy proxy out of service",
        )

    def test_success_resets_the_breaker(self):
        self.store.record_failure("youtube", ROUTE_DIRECT, reason=failure.REASON_NETWORK)
        self.store.record_success("youtube", ROUTE_DIRECT, elapsed_ms=500)
        self.store.record_failure("youtube", ROUTE_DIRECT, reason=failure.REASON_NETWORK)
        self.assertTrue(self.store.available("youtube", ROUTE_DIRECT))

    def test_open_routes_are_demoted_not_dropped(self):
        for _ in range(health.BREAKER_THRESHOLD):
            self.store.record_failure("youtube", ROUTE_DIRECT, reason=failure.REASON_BOT_CHECK)
        order = self.store.order_routes("youtube", (ROUTE_DIRECT, ROUTE_PROXY))
        self.assertEqual(order, (ROUTE_PROXY, ROUTE_DIRECT))

    def test_health_survives_a_restart(self):
        for _ in range(health.BREAKER_THRESHOLD):
            self.store.record_failure("youtube", ROUTE_DIRECT, reason=failure.REASON_BOT_CHECK)
        reloaded = health.RouteHealthStore(self.store.path, now_ms=lambda: self.now[0])
        self.assertFalse(
            reloaded.available("youtube", ROUTE_DIRECT),
            "the old in-process memo paid the 1.5s direct penalty again after every restart",
        )

    def test_backoff_grows_and_stays_bounded(self):
        rng = random.Random(7)
        delays = [health.backoff_delay_ms(i, rng=rng) for i in range(1, 8)]
        self.assertEqual(health.backoff_delay_ms(0), 0)
        self.assertLess(delays[0], delays[3])
        self.assertTrue(all(d <= health.BACKOFF_MAX_MS * 1.3 for d in delays), delays)

    def test_breaker_demotion_reorders_the_live_ladder(self):
        for _ in range(health.BREAKER_THRESHOLD):
            self.store.record_failure("youtube", ROUTE_DIRECT, reason=failure.REASON_BOT_CHECK)

        tried: list[str | None] = []
        tmp = Path(self._tmp.name)

        def dl(vid, out, proxy):
            tried.append(proxy)
            write_wav(out)

        ladder = mfetch.MusicFetchLadder(
            cache_dir=tmp / "audio", download=dl,
            proxy_provider=lambda: "http://proxy:1",
            health_store=self.store, sleep=lambda s: None,
            monotonic_ms=lambda: self.now[0],
            validator=lambda p, **kw: validate.validate_audio_file(
                p, probe=lambda _p: 200.0, **kw
            ),
        )
        result = ladder.fetch(mfetch.music_source(VIDEO_ID, media_id_=MEDIA_ID))
        self.assertTrue(result.ok)
        self.assertEqual(tried, ["http://proxy:1"],
                         "a known-blocked direct route must not be tried first")


class ValidationUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_zero_bytes(self):
        p = self.root / "a.wav"
        p.write_bytes(b"")
        out = validate.validate_audio_file(p)
        self.assertFalse(out.ok)
        self.assertEqual(out.reason, failure.REASON_EMPTY_AUDIO)

    def test_valid_wav_with_probe(self):
        p = write_wav(self.root / "b.wav")
        out = validate.validate_audio_file(p, probe=lambda _p: 200.0)
        self.assertTrue(out.ok)
        self.assertEqual(out.content_type, "audio/wav")
        self.assertEqual(out.duration_s, 200.0)

    def test_riff_that_is_not_wave_is_not_audio(self):
        p = self.root / "c.wav"
        p.write_bytes(b"RIFF" + struct.pack("<I", 5000) + b"AVI " + b"\x00" * 5000)
        out = validate.validate_audio_file(p, probe=lambda _p: 10.0)
        self.assertFalse(out.ok)
        self.assertEqual(out.reason, failure.REASON_MIME_MISMATCH)

    def test_sub_second_stub_is_corrupt(self):
        p = write_wav(self.root / "d.wav")
        out = validate.validate_audio_file(p, probe=lambda _p: 0.3)
        self.assertFalse(out.ok)
        self.assertEqual(out.reason, failure.REASON_CORRUPT_MEDIA)

    def test_cache_recheck_skips_the_decoder(self):
        p = write_wav(self.root / "e.wav")
        called = []
        out = validate.validate_audio_file(
            p, require_decode=False, probe=lambda _p: called.append(1) or 1.0
        )
        self.assertTrue(out.ok)
        self.assertEqual(called, [], "the cheap re-check must not spawn ffprobe")


if __name__ == "__main__":
    unittest.main()
