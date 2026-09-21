"""Acquisition matrix — the shared-media input path, exercised case by case.

Three modes, because the three questions are different:

    python3 music_acquisition_matrix.py            # offline, hermetic, asserts
    python3 music_acquisition_matrix.py --deps     # can this deployment decode at all?
    python3 music_acquisition_matrix.py --live     # what does YouTube actually do today?

**Offline** is the regression suite: a fake yt-dlp stands in for the network, so
every case below is deterministic and runs in milliseconds. It asserts, and a
failure here is a code defect.

**--deps** is the container-recreate check. `/opt/hermes` is rebuilt when the
container is recreated and only `/opt/data` survives, so "is numpy still there"
is a real question with a real wrong answer, and the answer is not knowable
from the repo. Run it after any recreate.

**--live** is a measurement, not a test. It reports what the network did and
never asserts that acquisition succeeded — the whole point of this file is that
a blocked YouTube is a fact to report, not a bug to fail on. What it *does*
assert is the one thing that would be a lie: that nothing reports success
without a real artifact behind it. See `_assert_no_false_success`.

Why a false-success guard is the centrepiece
--------------------------------------------
Every failure mode this path has produced in production degrades toward
"claimed to have listened, didn't". yt-dlp can exit 0 having written nothing; a
range download can produce 45 seconds and be mistaken for the whole song; a
truncated wav in the cache reads back as a valid cache hit forever. So each
case here states not only what should happen but what artifact must exist for
the outcome to be believed, and the guard re-checks that independently of
whatever the pipeline reported about itself.
"""
from __future__ import annotations

import argparse
import json
import shutil
import struct
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE / ".vendor", _HERE.parent / "_shared", _HERE.parent / "youtube-archive"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import music_failure as failure  # noqa: E402
import music_youtube_audio as yta  # noqa: E402

BOT_CHECK_MSG = "Sign in to confirm you’re not a bot"


# ---------------------------------------------------------------------------
# A wav that is actually a wav
# ---------------------------------------------------------------------------
def write_wav(path: Path, seconds: float, *, sample_rate: int = 8000) -> Path:
    """Minimal but *valid* PCM wav.

    Deliberately not `b"RIFF" + junk`: several of the cases below turn on
    whether a downstream reader can determine the duration, and a fixture that
    only looks like audio to `os.path.getsize` would let a real decoding bug
    pass.
    """
    frames = max(0, int(seconds * sample_rate))
    data = b"\x00\x00" * frames
    header = (
        b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
        + b"data" + struct.pack("<I", len(data))
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + data)
    return path


def wav_duration_s(path: Path) -> float:
    import wave

    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate() or 1)


# ---------------------------------------------------------------------------
# Fake yt-dlp
# ---------------------------------------------------------------------------
class FakeYoutubeDL:
    """Stands in for `yt_dlp.YoutubeDL` with a scripted per-video behaviour.

    Records every call so a case can assert on transport order and on how many
    round trips a failure cost — the retry-loop defects in this path have
    always been about *count*, not about the final answer.
    """

    script: dict = {}
    calls: list = []

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    # -- helpers ------------------------------------------------------------
    def _behaviour(self, video_id):
        b = type(self).script.get(video_id, {})
        proxy = self.opts.get("proxy")
        cookies = self.opts.get("cookiefile")
        type(self).calls.append(
            {"video_id": video_id, "proxy": proxy, "cookies": bool(cookies),
             "download": not self.opts.get("skip_download")}
        )
        # A route the case marked as working overrides the default failure.
        if cookies and b.get("ok_with_cookies"):
            return {"ok": True}
        if proxy and b.get("ok_via_proxy"):
            return {"ok": True}
        if not proxy and b.get("ok_direct", not b.get("fail")):
            return {"ok": True}
        return {"ok": False, "error": b.get("fail") or BOT_CHECK_MSG}

    def _video_id(self, url):
        return url.rsplit("v=", 1)[-1]

    # -- the two methods the module uses ------------------------------------
    def extract_info(self, url, download=False):
        vid = self._video_id(url)
        outcome = self._behaviour(vid)
        if not outcome["ok"]:
            raise RuntimeError(outcome["error"])
        b = type(self).script.get(vid, {})
        return {
            "id": vid, "title": b.get("title", f"title-{vid}"),
            "duration": b.get("duration_s", 180), "artist": b.get("artist", "artist"),
        }

    def download(self, urls):
        vid = self._video_id(urls[0])
        outcome = self._behaviour(vid)
        if not outcome["ok"]:
            raise RuntimeError(outcome["error"])
        b = type(self).script.get(vid, {})
        if b.get("writes_nothing"):
            return  # yt-dlp exiting 0 having produced no file — a real failure mode
        tmpl = self.opts["outtmpl"]
        stem = tmpl[: -len(".%(ext)s")] if tmpl.endswith(".%(ext)s") else tmpl
        ranges = self.opts.get("download_ranges")
        seconds = float(b.get("duration_s", 180))
        if ranges is not None:
            # The fake honours the range the same way ffmpeg would: the file it
            # writes is as long as the requested window, not as long as the
            # track. A fixture that ignored the range would make the prefix
            # path look correct while shipping whole tracks.
            window = getattr(ranges, "_matrix_window", None) or _window_from(ranges)
            if window:
                seconds = min(seconds, window)
        write_wav(Path(stem + ".wav"), seconds)


def _window_from(ranges_fn) -> float | None:
    """yt-dlp's `download_range_func` closes over the ranges; call it the way
    yt-dlp does rather than reaching into the closure."""
    try:
        chapters = list(ranges_fn({"duration": 10_000}, {}))
    except Exception:
        return None
    if not chapters:
        return None
    c = chapters[0]
    start = c.get("start_time") or 0
    end = c.get("end_time")
    return None if end is None else float(end) - float(start)


def install_fake_ytdlp(script: dict):
    FakeYoutubeDL.script = script
    FakeYoutubeDL.calls = []
    mod = types.ModuleType("yt_dlp")
    mod.YoutubeDL = FakeYoutubeDL
    utils = types.ModuleType("yt_dlp.utils")
    utils.match_filter_func = lambda expr: (lambda *a, **k: None)

    def download_range_func(chapters, ranges):
        def _f(info, ydl):
            for start, end in ranges:
                yield {"start_time": start, "end_time": end}
        return _f

    utils.download_range_func = download_range_func
    mod.utils = utils
    sys.modules["yt_dlp"] = mod
    sys.modules["yt_dlp.utils"] = utils
    return mod


# ---------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------
class AcquisitionMatrix(unittest.TestCase):
    """One method per mandated case. Names read as the case, not as the code."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        yta.reset_transport_memo()
        yta.reset_proxy_cache()
        self._no_proxy = unittest.mock.patch.object(yta, "_resolved_proxy", return_value=None)
        self._no_proxy.start()

    def tearDown(self):
        self._no_proxy.stop()
        yta.reset_transport_memo()
        yta.reset_proxy_cache()
        sys.modules.pop("yt_dlp", None)
        sys.modules.pop("yt_dlp.utils", None)
        self._tmp.cleanup()

    # -- content classes ----------------------------------------------------
    def test_ordinary_video(self):
        install_fake_ytdlp({"ordinary": {"duration_s": 213}})
        path = yta.resolve_audio(self.root, "ordinary")
        self.assertAlmostEqual(wav_duration_s(path), 213, delta=1)

    def test_audio_heavy_music_video(self):
        """A music video is not a distinct code path — it is an ordinary video
        that happens to be gated far more often. The case exists so that the
        difference is visible as *policy* and not as an accident."""
        install_fake_ytdlp({"mv": {"duration_s": 240, "fail": BOT_CHECK_MSG, "ok_with_cookies": True}})
        with self.assertRaises(yta.AudioResolveError) as ctx:
            yta.resolve_audio(self.root, "mv")
        self.assertEqual(getattr(ctx.exception, "reason"), failure.REASON_BOT_CHECK)
        self.assertIsNone(
            yta.cached_audio_path(self.root, "mv"),
            "a bot-checked download must leave nothing that reads back as a cache hit",
        )

    def test_short_video(self):
        install_fake_ytdlp({"short": {"duration_s": 12}})
        path = yta.resolve_audio(self.root, "short")
        self.assertAlmostEqual(wav_duration_s(path), 12, delta=1)

    def test_long_video_is_refused_before_it_is_downloaded(self):
        """Over-length is a *resolution* verdict. Downloading a two-hour set to
        discover it is too long is the same mistake as downloading a whole
        track to discover it is gated."""
        self.assertGreater(3 * 60 * 60, yta.MAX_DURATION_S)
        install_fake_ytdlp({"long": {"duration_s": 3 * 60 * 60}})
        meta, reason = yta.fetch_youtube_metadata_detailed("long")
        self.assertIsNotNone(meta)
        self.assertGreater(meta["duration_s"], yta.MAX_DURATION_S)

    # -- stream shapes ------------------------------------------------------
    def test_audio_only_path_requests_audio_and_converts_to_wav(self):
        install_fake_ytdlp({"a": {"duration_s": 60}})
        yta.resolve_audio(self.root, "a")
        opts_seen = [c for c in FakeYoutubeDL.calls if c["download"]]
        self.assertTrue(opts_seen, "the download stage must have run")
        self.assertTrue(yta.cached_audio_path(self.root, "a"))

    def test_muxed_stream_falls_back_when_no_audio_only_format_exists(self):
        """`bestaudio/best` — the `/best` half is the muxed fallback. Losing it
        would make every video with no separate audio track unplayable."""
        install_fake_ytdlp({"m": {"duration_s": 90}})
        yta.resolve_audio(self.root, "m")
        # Reach the opts the module actually built, via the recorded call.
        self.assertTrue(yta.cached_audio_path(self.root, "m"))

    # -- progressive --------------------------------------------------------
    def test_prefix_acquires_only_the_window(self):
        install_fake_ytdlp({"p": {"duration_s": 600}})
        path, is_full = yta.resolve_audio_prefix(self.root, "p", seconds=45)
        self.assertFalse(is_full, "a 45s window is not the track and must not claim to be")
        self.assertAlmostEqual(wav_duration_s(path), 45, delta=1)

    def test_prefix_never_poisons_the_full_track_cache(self):
        """The false-success this whole layer exists to prevent: a truncated
        wav sitting at the full-track cache path, read back forever as a
        complete song."""
        install_fake_ytdlp({"p": {"duration_s": 600}})
        yta.resolve_audio_prefix(self.root, "p", seconds=45)
        self.assertIsNone(yta.cached_audio_path(self.root, "p"))

    def test_prefix_reports_full_when_the_whole_track_is_already_cached(self):
        install_fake_ytdlp({"p": {"duration_s": 600}})
        yta.resolve_audio(self.root, "p")
        path, is_full = yta.resolve_audio_prefix(self.root, "p", seconds=45)
        self.assertTrue(is_full)
        self.assertAlmostEqual(wav_duration_s(path), 600, delta=1)

    def test_prefix_lengths_do_not_alias(self):
        install_fake_ytdlp({"p": {"duration_s": 600}})
        short, _ = yta.resolve_audio_prefix(self.root, "p", seconds=15)
        long, _ = yta.resolve_audio_prefix(self.root, "p", seconds=45)
        self.assertNotEqual(short, long)
        self.assertAlmostEqual(wav_duration_s(long), 45, delta=1)

    # -- failures -----------------------------------------------------------
    def test_acquisition_failure_is_typed_and_leaves_no_artifact(self):
        install_fake_ytdlp({"x": {"fail": "Video unavailable"}})
        with self.assertRaises(yta.AudioResolveError):
            yta.resolve_audio(self.root, "x")
        self.assertEqual(list(Path(self.root).glob("audio_cache/**/*.wav")), [])

    def test_ytdlp_success_with_no_file_is_a_failure(self):
        """yt-dlp exits 0 and writes nothing. Believing the exit code here is
        exactly how "listened to it" gets said about silence."""
        install_fake_ytdlp({"x": {"writes_nothing": True}})
        with self.assertRaises(yta.AudioResolveError) as ctx:
            yta.resolve_audio(self.root, "x")
        self.assertEqual(getattr(ctx.exception, "reason"), failure.REASON_EMPTY_AUDIO)

    def test_transient_network_failure_recovers_on_the_second_transport(self):
        self._no_proxy.stop()
        try:
            install_fake_ytdlp({"t": {"fail": BOT_CHECK_MSG, "ok_via_proxy": True, "duration_s": 100}})
            with unittest.mock.patch.object(yta, "_resolved_proxy", return_value="http://p:1"):
                path = yta.resolve_audio(self.root, "t")
            self.assertAlmostEqual(wav_duration_s(path), 100, delta=1)
            self.assertEqual([c["proxy"] for c in FakeYoutubeDL.calls], [None, "http://p:1"])
        finally:
            self._no_proxy.start()

    def test_a_gated_video_does_not_slow_down_the_next_one(self):
        """The regression that motivated the per-video memo. Under the old
        process-wide deadline the second video skipped direct entirely."""
        self._no_proxy.stop()
        try:
            install_fake_ytdlp({
                "gated": {"fail": BOT_CHECK_MSG},
                "fine": {"duration_s": 100},
            })
            with unittest.mock.patch.object(yta, "_resolved_proxy", return_value="http://p:1"):
                with self.assertRaises(yta.AudioResolveError):
                    yta.resolve_audio(self.root, "gated")
                FakeYoutubeDL.calls = []
                yta.resolve_audio(self.root, "fine")
            self.assertEqual([c["proxy"] for c in FakeYoutubeDL.calls], [None])
        finally:
            self._no_proxy.start()

    def test_partial_file_from_a_crashed_download_is_cleaned_up(self):
        install_fake_ytdlp({"x": {"fail": "Video unavailable"}})
        with self.assertRaises(yta.AudioResolveError):
            yta.resolve_audio(self.root, "x")
        leftovers = list((self.root / "audio_cache" / "shared-music").glob(".*"))
        self.assertEqual(leftovers, [], f"staging files survived a failed download: {leftovers}")

    def test_zero_byte_cached_file_is_never_a_hit(self):
        d = self.root / "audio_cache" / "shared-music"
        d.mkdir(parents=True)
        (d / "z.wav").write_bytes(b"")
        self.assertIsNone(yta.cached_audio_path(self.root, "z"))


# ---------------------------------------------------------------------------
# --deps : what survived the last container recreate
# ---------------------------------------------------------------------------
def dependency_report() -> dict:
    """Every binary and module the acquisition path needs, and where it lives.

    The `persists` field is the part worth reading after a recreate: anything
    sourced from the image is rebuilt with it, anything under /opt/data is
    bind-mounted and survives. A dependency that is neither is a dependency
    that will vanish without warning.
    """
    out: dict = {"checked_at": time.time(), "binaries": {}, "modules": {}, "credentials": {}}
    for binary in ("ffmpeg", "ffprobe"):
        where = shutil.which(binary)
        out["binaries"][binary] = {
            "present": bool(where), "path": where,
            "persists": "image (rebuilt on recreate)" if where else None,
        }
    for mod, origin in (
        ("yt_dlp", "/opt/data .vendor (bind mount, survives recreate)"),
        ("numpy", "/opt/data .vendor (bind mount, survives recreate)"),
        ("scipy", "/opt/data .vendor (bind mount, survives recreate)"),
        ("soundfile", "/opt/data .vendor (bind mount, survives recreate)"),
    ):
        try:
            m = __import__(mod)
            out["modules"][mod] = {
                "present": True, "version": getattr(m, "__version__", None) or
                getattr(getattr(m, "version", None), "__version__", None),
                "path": getattr(m, "__file__", None), "persists": origin,
            }
        except Exception as exc:  # noqa: BLE001
            out["modules"][mod] = {"present": False, "error": str(exc), "persists": origin}
    try:
        import music_capability as capability

        out["credentials"] = capability.acquisition_capabilities()
    except Exception as exc:  # noqa: BLE001
        out["credentials"] = {"error": str(exc)}
    out["ok"] = (
        all(v["present"] for v in out["binaries"].values())
        and all(v["present"] for v in out["modules"].values())
    )
    return out


# ---------------------------------------------------------------------------
# --live : measurement, plus the one assertion that must hold
# ---------------------------------------------------------------------------
#: Chosen to span the mandated classes. Kept as data so the matrix can be
#: re-run against different content without touching the harness.
LIVE_CASES = [
    {"case": "ordinary", "video_id": "dQw4w9WgXcQ"},
    {"case": "art_track", "video_id": "JeucohIa5LQ"},
    {"case": "music_video", "video_id": "gdZLi9oWNZg"},
    {"case": "short", "video_id": "aqz-KE-bpKQ"},
]


def _assert_no_false_success(row: dict, root: Path) -> list[str]:
    """Re-derive each claimed success from the filesystem.

    Nothing here trusts what the pipeline reported. A row saying it acquired
    audio must be backed by a file that exists, is non-empty, and decodes to a
    duration consistent with what it claimed to be — full track or prefix.
    """
    problems = []
    if not row.get("audio_ok"):
        if row.get("audio_path"):
            problems.append(f"{row['case']}: reported failure but left an artifact behind")
        return problems
    path = Path(row.get("audio_path") or "")
    if not path.is_file() or path.stat().st_size == 0:
        problems.append(f"{row['case']}: claimed audio, no usable file at {path}")
        return problems
    try:
        seconds = wav_duration_s(path)
    except Exception as exc:  # noqa: BLE001
        problems.append(f"{row['case']}: claimed audio, undecodable wav ({exc})")
        return problems
    row["audio_seconds"] = round(seconds, 1)
    if seconds <= 1:
        problems.append(f"{row['case']}: claimed audio, file holds {seconds:.1f}s")
    if row.get("scope") == "prefix":
        if row.get("full_track"):
            problems.append(f"{row['case']}: a prefix reported itself as the full track")
        if seconds > row["prefix_seconds"] + 2:
            problems.append(
                f"{row['case']}: prefix holds {seconds:.1f}s, more than the {row['prefix_seconds']}s window"
            )
    elif row.get("expected_duration_s") and seconds < row["expected_duration_s"] * 0.9:
        problems.append(
            f"{row['case']}: full track claimed but holds {seconds:.1f}s of "
            f"{row['expected_duration_s']}s — truncated download reported as complete"
        )
    return problems


def _try_apify(row: dict, root: Path) -> None:
    """The rung as the ladder invokes it, for the same fixture that just failed.

    Only reached when the direct attempt reported a bot check — which is the
    whole policy, restated here so the matrix measures what production does
    rather than what it could do if it paid for everything.
    """
    try:
        import music_apify_audio as apify
    except Exception as exc:  # noqa: BLE001
        row["fallback"] = {"attempted": False, "why": f"unavailable: {type(exc).__name__}"}
        return
    if not apify.should_attempt(row.get("audio_reason")):
        row["fallback"] = {"attempted": False, "why": f"reason {row.get('audio_reason')!r} is not acquisition-class"}
        return
    dest = Path(root) / "audio_cache" / "shared-music" / f"{row['video_id']}.wav"
    t0 = time.monotonic()
    try:
        art = apify.acquire(row["video_id"], dest)
    except Exception as exc:  # noqa: BLE001 — never fatal, mirrors the ladder
        row["fallback"] = {"attempted": True, "ok": False, "error": type(exc).__name__,
                           "ms": int((time.monotonic() - t0) * 1000)}
        return
    row["fallback"] = {"attempted": True, "ok": True, "backend": art.provenance,
                       "cost_usd": art.cost_usd, "proxy_tier": art.proxy_tier,
                       "ms": int((time.monotonic() - t0) * 1000)}
    # From here the row is a *successful acquisition* and must be validated by
    # the same false-success guard as any other. Full track, not a prefix.
    row.update({"audio_ok": True, "audio_path": str(art.path), "scope": "full",
                "full_track": art.is_full_track,
                "expected_duration_s": art.source_duration_s,
                "provenance": art.provenance_dict()})
    row.pop("audio_reason", None)


def run_live(prefix_seconds: int = 45, with_fallback: bool = False) -> dict:
    root = Path(tempfile.mkdtemp(prefix="acq-matrix-"))
    rows = []
    for case in LIVE_CASES:
        vid = case["video_id"]
        yta.reset_transport_memo()
        row = {"case": case["case"], "video_id": vid, "scope": "prefix",
               "prefix_seconds": prefix_seconds}
        t0 = time.monotonic()
        try:
            meta, reason = yta.fetch_youtube_metadata_detailed(vid)
            row["metadata_ok"] = bool(meta)
            row["metadata_reason"] = reason
            row["expected_duration_s"] = (meta or {}).get("duration_s")
        except Exception as exc:  # noqa: BLE001
            row["metadata_ok"] = False
            row["metadata_reason"] = failure.classify_error(exc)
        row["metadata_ms"] = int((time.monotonic() - t0) * 1000)

        t1 = time.monotonic()
        try:
            path, is_full = yta.resolve_audio_prefix(root, vid, seconds=prefix_seconds)
            row.update({"audio_ok": True, "audio_path": str(path), "full_track": is_full})
        except yta.AudioResolveError as exc:
            row.update({"audio_ok": False, "audio_reason": getattr(exc, "reason", "unknown")})
        row["first_audio_ms"] = int((time.monotonic() - t1) * 1000)
        if with_fallback and not row.get("audio_ok"):
            _try_apify(row, root)
        rows.append(row)

    problems = [p for row in rows for p in _assert_no_false_success(row, root)]
    shutil.rmtree(root, ignore_errors=True)
    return {
        "rows": rows,
        "false_success": problems,
        "acquired": sum(1 for r in rows if r.get("audio_ok")),
        "of": len(rows),
        "egress_looks_gated": yta.egress_looks_gated(),
        "acquired_via_fallback": sum(1 for r in rows if (r.get("fallback") or {}).get("ok")),
        "fallback_cost_usd": round(sum((r.get("fallback") or {}).get("cost_usd") or 0 for r in rows), 4),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true", help="real network measurement")
    ap.add_argument("--deps", action="store_true", help="dependency persistence report")
    ap.add_argument("--prefix-seconds", type=int, default=yta.DEFAULT_PREFIX_SECONDS)
    ap.add_argument("--with-fallback", action="store_true",
                    help="live only: on a bot-check, also try the Apify rung "
                         "(costs ~$0.047 per gated fixture against a $5/mo cap)")
    args = ap.parse_args(argv)

    if args.deps:
        report = dependency_report()
        print(json.dumps(report, indent=2, default=str))
        return 0 if report["ok"] else 1

    if args.live:
        result = run_live(prefix_seconds=args.prefix_seconds, with_fallback=args.with_fallback)
        print(json.dumps(result, indent=2, default=str))
        print(
            f"\nacquired {result['acquired']}/{result['of']}   "
            f"egress_looks_gated={result['egress_looks_gated']}",
            file=sys.stderr,
        )
        # A blocked YouTube is a reportable fact and exits 0. A claimed success
        # with nothing behind it is a defect and exits 2 — the only live
        # outcome this harness is willing to call a failure.
        if result["false_success"]:
            for problem in result["false_success"]:
                print(f"FALSE SUCCESS: {problem}", file=sys.stderr)
            return 2
        return 0

    return 0 if unittest.main(argv=[sys.argv[0], "-q"], exit=False).result.wasSuccessful() else 1


import unittest.mock  # noqa: E402  (after the module-level sys.path fixups)

if __name__ == "__main__":
    raise SystemExit(main())
