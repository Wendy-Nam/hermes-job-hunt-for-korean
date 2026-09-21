"""Acceptance tests for the Apify acquisition rung.

Hermetic: every HTTP call and every ffmpeg/ffprobe invocation is stubbed, so
these run in milliseconds and cost nothing. The live proof lives in
`docs/media/APIFY_ACQUISITION.md`; what is asserted here is the *policy* around
the call — when it may run, how often, what it refuses to believe, and that it
can never take a turn down with it.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE.parent / "_shared"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import music_apify_audio as apify  # noqa: E402
import music_failure as failure  # noqa: E402


class ShouldAttemptTests(unittest.TestCase):
    """The rung is expensive; the gate is the point of it."""

    def test_bot_check_is_worth_paying_for(self):
        self.assertTrue(apify.should_attempt(failure.REASON_BOT_CHECK))

    def test_geo_block_is_worth_paying_for(self):
        self.assertTrue(apify.should_attempt(failure.REASON_GEO_BLOCKED))

    def test_terminal_reasons_are_not(self):
        """A private/DRM/unavailable video is refused identically from Apify's
        address. Spending $0.047 and a minute to be told so twice is waste."""
        for reason in (failure.REASON_PRIVATE, failure.REASON_DRM,
                       failure.REASON_MEMBERS_ONLY, failure.REASON_NOT_FOUND):
            self.assertFalse(apify.should_attempt(reason), reason)

    def test_generic_failures_are_not(self):
        """`unknown` and `network` are retryable over a second egress, which is
        what the proxy rung is for. They are not evidence of a *gate*."""
        self.assertFalse(apify.should_attempt(failure.REASON_UNKNOWN))
        self.assertFalse(apify.should_attempt(failure.REASON_NETWORK))
        self.assertFalse(apify.should_attempt(None))


class _FakeApify:
    """Scripted stand-in for Apify's REST API."""

    def __init__(self, *, run_status="SUCCEEDED", item=None, blob=b"x" * 5000,
                 fail_on=None, cost=0.047):
        self.run_status, self.blob, self.fail_on, self.cost = run_status, blob, fail_on, cost
        self.item = item if item is not None else {
            "status": "downloaded", "kv_store_key": "audio-v1",
            "duration": 200, "file_size_bytes": len(blob), "proxy_tier_used": "residential",
        }
        self.calls: list[str] = []

    def __call__(self, path, *, method="GET", body=None, timeout=60, raw=False):
        self.calls.append(f"{method} {path}")
        if self.fail_on and self.fail_on in path:
            raise apify.ApifyAcquisitionError(f"boom at {path}")
        if path.endswith("/runs") and method == "POST":
            return {"data": {"id": "run1", "defaultKeyValueStoreId": "kv1",
                             "defaultDatasetId": "ds1", "status": self.run_status}}
        if path.startswith("/v2/actor-runs/"):
            return {"data": {"status": self.run_status, "usageTotalUsd": self.cost}}
        if path.startswith("/v2/datasets/"):
            return [self.item]
        if "/records/" in path:
            return self.blob
        raise AssertionError(path)


def _install(fake, *, probed=200.0, transcode_ok=True):
    """Patch the REST layer and the two media shell-outs."""
    def _tc(src, dest):
        if not transcode_ok:
            raise apify.ApifyAcquisitionError("ffmpeg could not decode")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"RIFFfake")
    return (
        mock.patch.object(apify, "_request", side_effect=fake),
        mock.patch.object(apify, "_probe_duration_s", return_value=probed),
        mock.patch.object(apify, "_transcode_to_wav", side_effect=_tc),
    )


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        apify.reset_attempts()
        apify.reset()
        self._tmp = tempfile.TemporaryDirectory()
        self.dest = Path(self._tmp.name) / "cache" / "v1.wav"

    def tearDown(self):
        apify.reset_attempts()
        self._tmp.cleanup()

    def _run(self, fake, **kw):
        patches = _install(fake, **kw)
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        return apify.acquire("v1", self.dest)

    def test_happy_path_returns_a_full_track_artifact(self):
        art = self._run(_FakeApify())
        self.assertTrue(art.path.exists())
        self.assertEqual(art.provenance, "apify_actor")
        self.assertTrue(art.is_full_track)
        self.assertEqual(art.source_duration_s, 200.0)
        self.assertEqual(art.analyzed_duration_s, 200.0)
        self.assertEqual(art.cost_usd, 0.047)

    def test_provenance_dict_carries_the_contract_fields(self):
        art = self._run(_FakeApify())
        p = art.provenance_dict()
        for key in ("acquisition_backend", "source_url", "video_id", "acquired_at",
                    "fallback_used", "artifact_type", "source_duration_s",
                    "analyzed_duration_s", "partial_analysis", "analyzed_range_s"):
            self.assertIn(key, p)
        self.assertFalse(p["partial_analysis"])
        self.assertTrue(p["fallback_used"])
        self.assertEqual(p["acquisition_backend"], "apify_actor")

    def test_a_clip_is_refused_rather_than_stored_as_the_track(self):
        """The false-success this rung could most easily introduce: a 30s
        preview written to the full-track cache, read back forever as the song."""
        fake = _FakeApify(item={"status": "downloaded", "kv_store_key": "k",
                                "duration": 337, "file_size_bytes": 5000})
        with self.assertRaises(apify.ApifyAcquisitionError) as ctx:
            self._run(fake, probed=30.0)
        self.assertIn("refusing to store a clip", str(ctx.exception))
        self.assertFalse(self.dest.exists())

    def test_size_mismatch_is_refused(self):
        fake = _FakeApify(item={"status": "downloaded", "kv_store_key": "k",
                                "duration": 200, "file_size_bytes": 999999})
        with self.assertRaises(apify.ApifyAcquisitionError):
            self._run(fake)
        self.assertFalse(self.dest.exists())

    def test_undecodable_artifact_is_refused(self):
        with self.assertRaises(apify.ApifyAcquisitionError):
            self._run(_FakeApify(), probed=None)
        self.assertFalse(self.dest.exists())

    def test_actor_reporting_not_downloaded_is_refused(self):
        fake = _FakeApify(item={"status": "failed", "kv_store_key": None})
        with self.assertRaises(apify.ApifyAcquisitionError):
            self._run(fake)

    def test_empty_record_is_refused(self):
        with self.assertRaises(apify.ApifyAcquisitionError):
            self._run(_FakeApify(blob=b""))

    def test_failed_run_is_refused(self):
        with self.assertRaises(apify.ApifyAcquisitionError):
            self._run(_FakeApify(run_status="FAILED"))

    def test_transcode_failure_leaves_no_cache_entry(self):
        with self.assertRaises(apify.ApifyAcquisitionError):
            self._run(_FakeApify(), transcode_ok=False)
        self.assertFalse(self.dest.exists())

    def test_one_run_per_video_even_after_failure(self):
        """A second attempt would spend the cap again to reproduce a failure."""
        fake = _FakeApify(run_status="FAILED")
        patches = _install(fake)
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        with self.assertRaises(apify.ApifyAcquisitionError):
            apify.acquire("v1", self.dest)
        starts = [c for c in fake.calls if c.startswith("POST")]
        with self.assertRaises(apify.ApifyAcquisitionError):
            apify.acquire("v1", self.dest)
        self.assertEqual([c for c in fake.calls if c.startswith("POST")], starts,
                         "a second billable run was started for the same video")

    def test_dedupe_is_per_video_not_global(self):
        fake = _FakeApify()
        patches = _install(fake)
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        apify.acquire("v1", self.dest)
        apify.acquire("v2", self.dest.with_name("v2.wav"))
        self.assertEqual(len([c for c in fake.calls if c.startswith("POST")]), 2)

    def test_attempted_reports_state(self):
        self.assertFalse(apify.attempted("v1"))
        self._run(_FakeApify())
        self.assertTrue(apify.attempted("v1"))


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._env = mock.patch.dict("os.environ", {"HERMES_DATA": str(self.root)})
        self._env.start()
        apify.reset()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_missing_token_is_reported_not_guessed(self):
        s = apify.credential_status()
        self.assertEqual(s["status"], "missing")
        self.assertFalse(s["configured"])

    def test_empty_token_is_invalid(self):
        (self.root / ".apify-api-token").write_text("")
        self.assertEqual(apify.credential_status()["status"], "empty")

    def test_present_token_usability_starts_unknown(self):
        """Validity costs a network round trip; the descriptor does not pretend
        to know. Note the live token is *scoped* — /v2/users/me answers 403
        while the storage endpoints answer 200 — so probing the account
        endpoint would report a working credential as broken."""
        (self.root / ".apify-api-token").write_text("apify_api_" + "x" * 36)
        s = apify.credential_status()
        self.assertEqual(s["status"], "configured")
        self.assertEqual(s["usable"], "unknown")

    def test_status_never_contains_the_token(self):
        secret = "apify_api_" + "s" * 36
        (self.root / ".apify-api-token").write_text(secret)
        blob = json.dumps(apify.credential_status())
        self.assertNotIn(secret, blob)
        self.assertNotIn("apify_api_", blob)

    def test_missing_token_raises_unavailable_not_acquisition_error(self):
        """`ApifyUnavailable` is the signal for 'capability absent'; the caller
        distinguishes it from 'we tried and it broke'."""
        with self.assertRaises(apify.ApifyUnavailable):
            apify._token()

    def test_auth_rejection_never_echoes_the_token(self):
        (self.root / ".apify-api-token").write_text("apify_api_" + "z" * 36)
        import urllib.error

        err = urllib.error.HTTPError("u", 401, "no", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(apify.ApifyUnavailable) as ctx:
                apify._request("/v2/acts")
        self.assertNotIn("apify_api_", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
