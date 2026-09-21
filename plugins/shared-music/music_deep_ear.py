"""Deep Ear bridge — runs in the main plugin process (Python 3.13,
/opt/hermes/.venv), never imports torch/demucs/librosa directly. Shells out
to music_deep_ear_worker.py inside the isolated `.shared-music-deepear-venv`
via subprocess, with a hard timeout and a single-flight lock (each real run
holds ~1.2GB RSS for ~25-45s on this 2-core box, measured directly — see the
project memory for the benchmark; a second concurrent run stacking on top of
that is a real risk on an 8GB box also running the live Discord bot).

Graceful degradation (task rule 34) is the whole point of this module: any
failure — venv missing, worker crash, OOM, timeout, corrupt audio — returns
`None`, never raises into the caller. Fast Ear must survive Deep Ear's death
every time.

OBSERVATION vs INTERPRETATION split (task item 11): the worker's raw JSON
*is* the observation layer (measured DSP facts only). `interpret()` below is
a small, explicit, rule-based layer on top that names a few musically
legible patterns from combinations of observations (e.g. "perceived_build").
Nothing here ever computes a "preference" — that stays the LLM's own
subjective reaction, captured afterward into session.agent_opinions exactly
like Phase 1 already does; Deep Ear has no opinion of its own.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path

import music_deep_ear_cache as deep_cache
from music_deep_ear_schema import ANALYZER_VERSION

logger = logging.getLogger(__name__)

_PLUGIN_DIR = Path(__file__).resolve().parent
DEEPEAR_VENV_PYTHON = Path(os.environ.get("HERMES_DATA") or "/opt/data") / ".shared-music-deepear-venv" / "bin" / "python3"
WORKER_SCRIPT = _PLUGIN_DIR / "music_deep_ear_worker.py"

# Measured on the live 2-core/8GB box: ~24-33s wall time for a 20s window.
# Capped generously above that so a slow-but-real run isn't killed early,
# while still bounding worst case for a single Discord tool-call turn.
WORKER_TIMEOUT_S = 90
MAX_WINDOW_S = 30.0  # bounds latency/RAM regardless of how wide the caller asks
_LOCK_PATH = Path(os.environ.get("HERMES_DATA") or "/opt/data") / "logs" / "shared-music" / ".deepear.lock"


class DeepEarUnavailable(Exception):
    pass


def is_available() -> bool:
    return DEEPEAR_VENV_PYTHON.exists() and WORKER_SCRIPT.exists()


def _acquire_lock(timeout_s: float = 2.0) -> "int | None":
    """Best-effort single-flight guard, not a hard mutex — if it can't be
    acquired promptly we still proceed (a lost race is far cheaper than
    blocking a whole conversation turn on an unrelated deep-dive)."""
    import fcntl

    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(_LOCK_PATH), os.O_CREAT | os.O_RDWR)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            time.sleep(0.2)
    os.close(fd)
    return None


def _release_lock(fd) -> None:
    if fd is None:
        return
    try:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def analyze_window(
    audio_path: str, start_s: float, end_s: float, *,
    wiki_root: Path | None = None, canonical_id: str | None = None,
    tmp_dir: str | None = None, trace_fn=None,
) -> dict | None:
    """Runs the full Deep Ear pass on [start_s, end_s], or returns a cached
    result for the same (canonical_id, window) if one exists and matches the
    current analyzer version. Returns the worker's raw observation dict, or
    None on any failure (never raises) — Fast Ear must survive Deep Ear's
    death every time (task rule 34)."""
    end_s = min(end_s, start_s + MAX_WINDOW_S)

    if wiki_root is not None and canonical_id:
        cached = deep_cache.load(wiki_root, canonical_id, start_s, end_s)
        if cached and deep_cache.is_current(cached, ANALYZER_VERSION):
            if trace_fn:
                trace_fn("deep_ear_cache_hit", canonical_id=canonical_id, window=[start_s, end_s])
            return cached
    if trace_fn:
        trace_fn("deep_ear_cache_miss", canonical_id=canonical_id, window=[start_s, end_s])

    if not is_available():
        logger.debug("deep ear unavailable: isolated venv or worker script missing")
        if trace_fn:
            trace_fn("deep_ear_unavailable", reason="venv_or_worker_missing")
        return None

    lock_fd = _acquire_lock()
    result: dict | None = None
    try:
        with _tmp_request(audio_path, start_s, end_s, tmp_dir) as (req_path, res_path):
            t0 = time.monotonic()
            try:
                proc = subprocess.run(
                    [str(DEEPEAR_VENV_PYTHON), str(WORKER_SCRIPT), str(req_path), str(res_path)],
                    capture_output=True, text=True, timeout=WORKER_TIMEOUT_S,
                )
            except subprocess.TimeoutExpired:
                logger.warning("deep ear worker timed out after %ss", WORKER_TIMEOUT_S)
                if trace_fn:
                    trace_fn("deep_ear_timeout", canonical_id=canonical_id)
                return None
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            if proc.returncode != 0:
                logger.warning("deep ear worker failed (rc=%s): %s", proc.returncode, proc.stderr[-2000:])
            if not res_path.exists():
                return None
            try:
                result = json.loads(res_path.read_text(encoding="utf-8"))
            except Exception:
                return None
            if "error" in result:
                logger.warning("deep ear worker reported error: %s", result["error"])
                if trace_fn:
                    trace_fn("deep_ear_worker_error", canonical_id=canonical_id, error=result["error"])
                return None
            if trace_fn:
                trace_fn("deep_ear_analysis_ms", canonical_id=canonical_id, ms=elapsed_ms)
    except Exception:
        logger.exception("deep ear analyze_window failed unexpectedly")
        return None
    finally:
        _release_lock(lock_fd)

    if result and wiki_root is not None and canonical_id:
        deep_cache.save(wiki_root, canonical_id, start_s, end_s, result)
    return result


from contextlib import contextmanager  # noqa: E402


@contextmanager
def _tmp_request(audio_path: str, start_s: float, end_s: float, tmp_dir: str | None):
    tmp_root = Path(tmp_dir or (Path(os.environ.get("HERMES_DATA") or "/opt/data") / "tmp" / "shared-music"))
    tmp_root.mkdir(parents=True, exist_ok=True)
    tag = f"{int(time.time() * 1000)}_{os.getpid()}"
    req_path = tmp_root / f"deepear_req_{tag}.json"
    res_path = tmp_root / f"deepear_res_{tag}.json"
    req_path.write_text(
        json.dumps({"audio_path": audio_path, "start_s": start_s, "end_s": end_s, "tmp_dir": str(tmp_root)}),
        encoding="utf-8",
    )
    try:
        yield req_path, res_path
    finally:
        req_path.unlink(missing_ok=True)
        res_path.unlink(missing_ok=True)


# --- Interpretation layer (task item 11) -----------------------------------

def interpret(observation: dict) -> dict:
    """Pure-function rule layer over the worker's raw observation dict —
    names a handful of musically legible combined patterns. Deliberately
    small and explicit (no ML, no LLM) so every claim traces back to which
    observations produced it."""
    out: dict = {}
    rhythm = observation.get("rhythm", {})
    vocal = observation.get("vocal", {})
    harmony = observation.get("harmony", {})

    build_signals = [
        rhythm.get("drum_density_trend") == "rising",
        vocal.get("dynamics_trend") == "rising",
        harmony.get("tension_trend") == "rising",
    ]
    if sum(bool(s) for s in build_signals) >= 2:
        out["perceived_build"] = "strong" if all(build_signals) else "moderate"

    release_signals = [
        rhythm.get("drum_density_trend") == "falling",
        vocal.get("dynamics_trend") == "falling",
        harmony.get("tension_trend") == "falling",
    ]
    if sum(bool(s) for s in release_signals) >= 2:
        out["perceived_release"] = "strong" if all(release_signals) else "moderate"

    if vocal.get("layering_detected") and vocal.get("presence", 0) > 0.3:
        out["vocal_layering"] = "audible_harmony_or_backing"

    if rhythm.get("syncopation", 0) > 0.35 and rhythm.get("confidence", 0) > 0.3:
        out["groove_character"] = "syncopated_off_grid"
    elif rhythm.get("confidence", 0) > 0.3:
        out["groove_character"] = "straight_on_grid"

    return out


def summarize_chords(harmony: dict, *, min_confidence: float = 0.55, min_duration_s: float = 0.8) -> dict:
    """Rolls up the raw beat-level chord_sequence (noisy in practice on a
    full mix without a trained chord-recognition model — template-matching
    on separated-but-imperfect stems flickers between adjacent chords even
    when the underlying harmony is stable, confirmed on a real track during
    development) into a small set of dominant chords + honest uncertainty,
    instead of handing the LLM a beat-by-beat chart it would present as more
    precise than it actually is."""
    sequence = harmony.get("chord_sequence", [])
    trusted = [c for c in sequence if c["confidence"] >= min_confidence and (c["end_s"] - c["start_s"]) >= min_duration_s]
    if not trusted:
        return {
            "key_guess": harmony.get("key_guess", "unknown"),
            "key_confidence": harmony.get("key_confidence", 0.0),
            "dominant_chords": [],
            "certainty": "low — harmony kept shifting between short, low-confidence guesses; treat as tonally ambiguous, not as a settled progression",
        }
    totals: dict[str, float] = {}
    for c in trusted:
        totals[c["label"]] = totals.get(c["label"], 0.0) + (c["end_s"] - c["start_s"])
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:4]
    avg_conf = sum(c["confidence"] for c in trusted) / len(trusted)
    return {
        "key_guess": harmony.get("key_guess", "unknown"),
        "key_confidence": harmony.get("key_confidence", 0.0),
        "dominant_chords": [label for label, _dur in ranked],
        "certainty": "moderate" if avg_conf >= 0.65 else "low",
    }
