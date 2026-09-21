#!/usr/bin/env python3
"""One-command re-verification of the shared-music listening pipeline.

    python3 music_e2e_smoke.py            # offline: fixtures + real ffmpeg/DSP
    python3 music_e2e_smoke.py --live     # additionally probe real YouTube/Spotify

Offline mode is hermetic and safe to run anywhere: it stubs only the network,
decodes real encoded audio through real ffmpeg, and asserts that perception
evidence is genuinely produced (and genuinely *absent* on every failure path).

--live mode makes real network calls and is the only way to answer "does
resolution actually work from this host today". It writes exclusively to a
scratch data root passed as a parameter — it never mutates the production
archive — but it does download audio, so run it deliberately.

Exit code is non-zero if any case fails, so this is usable as a health check.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
_VENDOR = HERE / ".vendor"
if _VENDOR.is_dir():
    sys.path.insert(0, str(_VENDOR))

# The live probe deliberately reuses the videos that failed in production, so a
# regression in the resolver ladder shows up as a changed verdict here rather
# than as a silent metadata-only reply months later.
LIVE_CASES = [
    ("youtube_ordinary", "youtube", "dQw4w9WgXcQ"),
    ("youtube_gated_arttrack", "youtube", "JeucohIa5LQ"),
    ("youtube_nonexistent", "youtube", "aaaaaaaaaaa"),
    ("spotify_track", "spotify", "0NGFAcYQVHCIdQea2qSs1I"),
]


def run_offline(verbosity: int) -> bool:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite(
        loader.loadTestsFromName(name)
        for name in ("test_music_e2e.MusicEndToEndTest", "test_music_e2e.AudioAcquisitionTest")
    )
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    return result.wasSuccessful()


def run_live(scratch: Path) -> bool:
    import music_dsp as dsp
    import music_perception as mp
    import music_resolve
    import music_youtube_audio as yta

    rows = []
    for name, source, ident in LIVE_CASES:
        row = {"case": name, "id": ident, "resolve": "-", "audio": "-", "perception": "-"}
        t0 = time.monotonic()
        try:
            if source == "youtube":
                r = music_resolve.resolve_from_youtube(ident)
            else:
                r = music_resolve.resolve_from_spotify(ident)
        except Exception as exc:
            row["resolve"] = f"FAIL ({type(exc).__name__})"
            row["ms"] = int((time.monotonic() - t0) * 1000)
            rows.append(row)
            continue
        row["resolve"] = "OK"
        row["track"] = f"{r.artist} - {r.title}"[:48]
        if not r.playable:
            row["audio"] = "skipped (metadata-only)"
            row["ms"] = int((time.monotonic() - t0) * 1000)
            rows.append(row)
            continue
        try:
            path = yta.resolve_audio(scratch, r.video_id)
            row["audio"] = f"OK ({Path(path).stat().st_size // 1024}KB)"
        except Exception as exc:
            row["audio"] = f"FAIL ({getattr(exc, 'reason', type(exc).__name__)})"
            row["ms"] = int((time.monotonic() - t0) * 1000)
            rows.append(row)
            continue
        raw = dsp.analyze(str(path))
        perception = mp.build_perception(
            title=r.title, artist=r.artist, raw=raw,
            phases=dsp.detect_phases(raw.energy_1hz),
            events=dsp.find_salient_events(raw.onset_1hz, raw.flux_1hz),
        ).to_dict()
        row["perception"] = f"OK ({len(perception['sections'])} sections)"
        row["ms"] = int((time.monotonic() - t0) * 1000)
        rows.append(row)

    print("\n=== LIVE RESOLVER MATRIX ===")
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))
    # A gated track degrading to metadata-only is correct behaviour, not a
    # failure — the pipeline is only broken if resolution itself dies.
    return all(r["resolve"] != "-" and not r["resolve"].startswith("FAIL")
               for r in rows if r["case"] != "youtube_nonexistent")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="also probe real YouTube/Spotify (network)")
    parser.add_argument("--scratch", default=None, help="scratch data root for --live downloads")
    parser.add_argument("-v", "--verbose", action="count", default=1)
    args = parser.parse_args()

    ok = run_offline(args.verbose)
    print(f"\noffline E2E: {'PASS' if ok else 'FAIL'}")

    if args.live:
        scratch = Path(args.scratch or tempfile.mkdtemp(prefix="music-live-smoke-"))
        print(f"live probe scratch root: {scratch}")
        live_ok = run_live(scratch)
        print(f"live probe: {'PASS' if live_ok else 'FAIL'}")
        ok = ok and live_ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
