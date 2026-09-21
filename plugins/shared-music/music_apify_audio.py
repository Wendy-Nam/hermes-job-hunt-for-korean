"""Apify actor acquisition — the rung that runs only when YouTube refuses us.

Why this exists
---------------
Measured 2026-08-11 from the production container: of 12 video ids probed across
13 yt-dlp ``player_client`` values, exactly one resolves. `JeucohIa5LQ` (an Art
Track), `gdZLi9oWNZg` (a label MV) and `aqz-KE-bpKQ` (a Blender Foundation
short — not music, not label content) answer "Sign in to confirm you're not a
bot" on **every** client and through **both** proxynet proxies. The gate is
address-wide, and no cookie jar is installed.

The Apify account this deployment already has holds a residential proxy tier.
An actor running there is not on our address, so it is not refused: all three
gated fixtures above were acquired in full through it, each reporting
``proxy_tier_used=residential``.

This is deliberately the *fourth* rung, not the first
-----------------------------------------------------
Direct yt-dlp acquires a track in 1.7-2.5s and costs nothing. One actor run
measured **$0.047** against a **$5.00/month** account cap, and took 38-164s
wall clock. So this path is worth roughly 100 tracks a month and an extra
minute, which is an excellent trade for a track that is otherwise
*unobtainable* and a terrible one for a track direct would have delivered.
Hence :func:`should_attempt` — bot-check-class failures only, never a fallback
for "the network hiccuped".

Not routed through Composio, on purpose
---------------------------------------
The rest of Hermes reaches Apify through Composio's MCP server. That route
cannot work here: Composio's upstream read timeout is 120s, and one of the
three fixtures took 163.5s from run start to bytes in hand. It also exposes no
key-value-store reader at all, and an actor puts audio in a KVS because audio
does not fit in a dataset item — which is why this capability looked like
"transcript only" until a token existed. Talking to Apify's REST API directly
with ``Authorization: Bearer`` removes both problems and the polling loop below
has no ceiling but its own budget.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import music_apify_budget as budget
import music_failure as failure

logger = logging.getLogger(__name__)

API_ROOT = "https://api.apify.com"
#: Free actor. Named here rather than configured because switching actors
#: changes the output contract (`kv_store_key`, `duration`, `file_size_bytes`)
#: that :func:`_validated_artifact` checks, and a silently swapped actor would
#: be a silently different artifact.
ACTOR_ID = "lurkapi~youtube-to-mp3-audio-downloader"

#: Whole-operation budget. The slowest measured fixture took 163.5s end to end
#: (a 223s track); the 635s / 30.7MB one took 58.6s. 300s leaves headroom
#: without letting a wedged run hold a turn open indefinitely.
DEFAULT_BUDGET_S = 300
_POLL_INTERVAL_S = 5
_RUN_START_TIMEOUT_S = 60
_RETRIEVE_TIMEOUT_S = 240

PROVENANCE = "apify_actor"

#: A retrieved artifact must be at least this fraction of the duration the
#: actor reported for the source. Under it we have a *clip*, not the track, and
#: storing a clip in the full-track cache would make every later turn analyse a
#: fragment while believing it heard the song.
MIN_DURATION_RATIO = 0.95


class ApifyUnavailable(RuntimeError):
    """No token, or the credential/plan cannot serve this request."""


class ApifyAcquisitionError(RuntimeError):
    """The run happened and did not produce a usable artifact."""


# ---------------------------------------------------------------------------
# Credential
# ---------------------------------------------------------------------------
def token_path() -> Path:
    return Path(os.environ.get("HERMES_DATA") or "/opt/data") / ".apify-api-token"


def _token() -> str:
    """The token, or raise. Never logged, never returned to a caller that
    traces, never placed in an exception message."""
    path = token_path()
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ApifyUnavailable(f"apify token unreadable at {path}: {exc.__class__.__name__}") from None
    if not value:
        raise ApifyUnavailable("apify token file is empty")
    return value


def credential_status() -> dict:
    """Capability descriptor. Safe to log verbatim — booleans and lengths only.

    Mirrors `music_capability.youtube_cookie_status()`: presence and
    readability are reported, validity is not guessed. The token issued for
    this deployment is a *scoped* token — `/v2/users/me` answers 403
    `insufficient-permissions` while `/v2/acts`, `/v2/actor-runs`,
    `/v2/datasets` and `/v2/key-value-stores` all answer 200 — so a probe
    against the account endpoint would report a working credential as broken.
    """
    path = token_path()
    try:
        if not path.is_file():
            return {"status": "missing", "configured": False, "usable": "invalid"}
        size = path.stat().st_size
    except OSError:
        return {"status": "unreadable", "configured": True, "usable": "unknown"}
    if size == 0:
        return {"status": "empty", "configured": True, "usable": "invalid"}
    return {"status": "configured", "configured": True, "usable": _usable_memo,
            "token_bytes": size}


_usable_memo = "unknown"


def note_result(*, succeeded: bool) -> None:
    global _usable_memo
    _usable_memo = "usable" if succeeded else "invalid"


def reset() -> None:
    global _usable_memo
    _usable_memo = "unknown"


# ---------------------------------------------------------------------------
# Should we even try?
# ---------------------------------------------------------------------------
#: Failure reasons where a different egress plausibly changes the answer. A
#: private or DRM'd video is refused identically from Apify's address, so
#: spending $0.047 and a minute to be told the same thing is pure waste.
ACQUISITION_CLASS_REASONS = frozenset({
    failure.REASON_BOT_CHECK,
    failure.REASON_GEO_BLOCKED,
})


def should_attempt(reason: str | None) -> bool:
    return reason in ACQUISITION_CLASS_REASONS


# ---------------------------------------------------------------------------
# Per-video de-duplication
# ---------------------------------------------------------------------------
@dataclass
class _Attempt:
    started_at: float
    done: threading.Event = field(default_factory=threading.Event)
    path: Path | None = None
    error: BaseException | None = None
    cost_usd: float | None = None


#: video_id -> attempt. One run per video per process, ever: a second attempt
#: for a track that already failed would spend the cap again to reproduce a
#: failure, and two concurrent turns about the same track must not start two
#: billable runs. Entries are kept (not evicted on completion) precisely so the
#: "already tried, don't pay again" answer survives.
_attempts: dict[str, _Attempt] = {}
_attempts_lock = threading.Lock()
_MAX_ATTEMPTS_TRACKED = 512


def reset_attempts() -> None:
    """Test seam / operator escape hatch."""
    with _attempts_lock:
        _attempts.clear()


def attempted(video_id: str) -> bool:
    with _attempts_lock:
        return video_id in _attempts


# ---------------------------------------------------------------------------
# REST plumbing
# ---------------------------------------------------------------------------
def _request(path: str, *, method: str = "GET", body: dict | None = None,
             timeout: int = 60, raw: bool = False):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {_token()}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API_ROOT + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read() if raw else json.load(resp)
    except urllib.error.HTTPError as exc:
        # The body may carry Apify's typed error. The token is in the *request*
        # headers, never in the response, so nothing here can echo it.
        detail = ""
        try:
            detail = (json.load(exc) or {}).get("error", {}).get("type", "")
        except Exception:  # noqa: BLE001
            pass
        if exc.code in (401, 403):
            raise ApifyUnavailable(f"apify rejected the credential ({exc.code} {detail})") from None
        raise ApifyAcquisitionError(f"apify {method} {path} failed: {exc.code} {detail}") from None


def _download_url(url: str, *, timeout: int) -> bytes:
    """Fetch an actor-produced KVS URL using the same scoped bearer token."""
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {_token()}", "Accept": "audio/mpeg"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise ApifyUnavailable(f"apify audio URL rejected ({exc.code})") from None
        raise ApifyAcquisitionError(f"apify audio URL failed: {exc.code}") from None
    except (OSError, urllib.error.URLError) as exc:
        raise ApifyAcquisitionError(f"apify audio URL unavailable: {type(exc).__name__}") from None


# ---------------------------------------------------------------------------
# Artifact validation
# ---------------------------------------------------------------------------
def _probe_duration_s(path: Path) -> float | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-of", "json", "-show_entries", "format=duration", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        return float(json.loads(out.stdout)["format"]["duration"])
    except Exception:  # noqa: BLE001
        return None


@dataclass
class ApifyArtifact:
    """What was acquired, and what is licensed to be said about it.

    `source_duration_s` comes from the actor's own report about the *video*;
    `analyzed_duration_s` is what the file on disk actually holds. They are
    separate fields because conflating them is the defect
    `docs/media/PARTIAL_ANALYSIS_CONTRACT.md` exists to prevent — a short file
    presents as a short song, not as a truncated download.
    """
    path: Path
    provenance: str
    video_id: str
    source_url: str
    acquired_at: float
    source_duration_s: float | None
    analyzed_duration_s: float | None
    is_full_track: bool
    artifact_type: str
    fallback_used: bool = True
    cost_usd: float | None = None
    proxy_tier: str | None = None

    def provenance_dict(self) -> dict:
        return {
            "acquisition_backend": self.provenance,
            "source_url": self.source_url,
            "video_id": self.video_id,
            "acquired_at": self.acquired_at,
            "fallback_used": self.fallback_used,
            "artifact_type": self.artifact_type,
            "source_duration_s": self.source_duration_s,
            "analyzed_duration_s": self.analyzed_duration_s,
            "partial_analysis": not self.is_full_track,
            "analyzed_range_s": (0.0, self.analyzed_duration_s) if self.analyzed_duration_s else None,
            "cost_usd": self.cost_usd,
            "proxy_tier": self.proxy_tier,
        }


def _transcode_to_wav(src: Path, dest: Path) -> None:
    """m4a/AAC -> wav, because `music_dsp.load_audio_mono` reads via soundfile,
    which does not open AAC. Written to a unique staging name and renamed, so a
    crashed transcode cannot leave a truncated wav that a later turn reads back
    as a valid full-track cache hit."""
    staging = dest.with_name(f".{dest.stem}.{uuid.uuid4().hex}.part.wav")
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(src), str(staging)],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0 or not staging.exists() or staging.stat().st_size == 0:
            raise ApifyAcquisitionError(
                f"ffmpeg could not decode the apify artifact: {proc.stderr[:160]}"
            )
        os.replace(staging, dest)
    finally:
        if staging.exists():
            try:
                staging.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# The rung
# ---------------------------------------------------------------------------
def acquire(video_id: str, dest: Path, *, budget_s: int = DEFAULT_BUDGET_S,
            trace_fn=None) -> ApifyArtifact:
    """Run the actor, retrieve the audio, write a validated wav at `dest`.

    Raises `ApifyUnavailable` when the capability is not configured and
    `ApifyAcquisitionError` when a run happened but produced nothing usable.
    Callers must treat both as "continue down the ladder", never as fatal.
    """
    # Monthly cap. Checked before the dedupe map is touched so a refused run
    # is not recorded as "already attempted" -- next month it must be free to
    # try again. ApifyUnavailable is the ladder's "skip this rung" signal, so
    # cache/direct/proxy/alternate-source/alternate-route all still run and
    # the turn completes with whatever they produce.
    if budget.would_exceed():
        snap = budget.snapshot()
        if trace_fn:
            trace_fn("apify_budget_exhausted", video_id=video_id,
                     spent_usd=round(snap.spent_usd, 4), runs=snap.runs,
                     cap_usd=budget.monthly_cap_usd(), period=snap.period)
        raise ApifyUnavailable(
            f"apify monthly budget exhausted (${snap.spent_usd:.2f} of "
            f"${budget.monthly_cap_usd():.2f} in {snap.period})")

    with _attempts_lock:
        prior = _attempts.get(video_id)
        if prior is not None:
            if trace_fn:
                trace_fn("apify_attempt_deduped", video_id=video_id)
            prior.done.wait(timeout=budget_s)
            if prior.path is not None:
                return _artifact_from_cached(prior, video_id, dest)
            raise ApifyAcquisitionError(f"apify already attempted {video_id} without success")
        if len(_attempts) >= _MAX_ATTEMPTS_TRACKED:
            for stale, _ in sorted(_attempts.items(), key=lambda kv: kv[1].started_at)[:64]:
                _attempts.pop(stale, None)
        attempt = _Attempt(started_at=time.time())
        _attempts[video_id] = attempt

    t0 = time.monotonic()
    source_url = f"https://www.youtube.com/watch?v={video_id}"
    # Debit up front: Apify bills for runs that produce nothing, and two
    # concurrent turns that both read the pre-run total would both start a
    # run. settle() corrects the estimate once the API reports the real cost.
    reserved = budget.reserve()
    if not reserved:
        with _attempts_lock:
            _attempts.pop(video_id, None)
        attempt.done.set()
        snap = budget.snapshot()
        if trace_fn:
            trace_fn("apify_budget_exhausted", video_id=video_id,
                     spent_usd=round(snap.spent_usd, 4), runs=snap.runs,
                     cap_usd=budget.monthly_cap_usd(), period=snap.period)
        raise ApifyUnavailable(
            f"apify monthly budget exhausted (${snap.spent_usd:.2f} of "
            f"${budget.monthly_cap_usd():.2f} in {snap.period})")
    try:
        artifact = _run_and_retrieve(video_id, source_url, dest, budget_s, trace_fn, t0)
        attempt.path = artifact.path
        attempt.cost_usd = artifact.cost_usd
        budget.settle(artifact.cost_usd)
        if trace_fn:
            snap = budget.snapshot()
            trace_fn("apify_spend", video_id=video_id,
                     cost_usd=artifact.cost_usd,
                     spent_usd=round(snap.spent_usd, 4),
                     cap_usd=budget.monthly_cap_usd(), period=snap.period)
        note_result(succeeded=True)
        return artifact
    except BaseException as exc:
        attempt.error = exc
        if isinstance(exc, ApifyUnavailable):
            note_result(succeeded=False)
        raise
    finally:
        attempt.done.set()


def _artifact_from_cached(prior: _Attempt, video_id: str, dest: Path) -> ApifyArtifact:
    path = prior.path
    dur = _probe_duration_s(path) if path and path.exists() else None
    return ApifyArtifact(
        path=path, provenance=PROVENANCE, video_id=video_id,
        source_url=f"https://www.youtube.com/watch?v={video_id}",
        acquired_at=prior.started_at, source_duration_s=dur,
        analyzed_duration_s=dur, is_full_track=True, artifact_type="wav",
        cost_usd=prior.cost_usd,
    )


def _run_and_retrieve(video_id, source_url, dest, budget_s, trace_fn, t0) -> ApifyArtifact:
    # -- start (async; see the module docstring on why not the sync endpoint) --
    run = _request(f"/v2/acts/{ACTOR_ID}/runs", method="POST",
                   body={"videoUrls": [source_url]}, timeout=_RUN_START_TIMEOUT_S)["data"]
    run_id, store_id, dataset_id = run["id"], run["defaultKeyValueStoreId"], run["defaultDatasetId"]
    status = run.get("status")
    if trace_fn:
        trace_fn("apify_run_started", video_id=video_id, run_id=run_id)

    while status in ("READY", "RUNNING"):
        if time.monotonic() - t0 > budget_s:
            raise ApifyAcquisitionError(f"apify run exceeded {budget_s}s budget (run {run_id})")
        time.sleep(_POLL_INTERVAL_S)
        status = _request(f"/v2/actor-runs/{run_id}")["data"].get("status")
    if status != "SUCCEEDED":
        raise ApifyAcquisitionError(f"apify run {run_id} ended {status}")

    detail = _request(f"/v2/actor-runs/{run_id}")["data"]
    cost = detail.get("usageTotalUsd")

    items = _request(f"/v2/datasets/{dataset_id}/items")
    item = items[0] if isinstance(items, list) and items else {}
    if item.get("status", "").lower() not in ("downloaded", "success"):
        raise ApifyAcquisitionError(
            f"apify actor reported {item.get('status')!r} for {video_id}, no artifact"
        )
    source_duration = item.get("duration")

    # -- retrieve --
    audio_url = item.get("audioFileUrl")
    if audio_url:
        blob = _download_url(audio_url, timeout=_RETRIEVE_TIMEOUT_S)
    elif item.get("kv_store_key"):
        blob = _request(f"/v2/key-value-stores/{store_id}/records/{item['kv_store_key']}",
                        timeout=_RETRIEVE_TIMEOUT_S, raw=True)
    else:
        raise ApifyAcquisitionError(f"apify actor returned no audio URL for {video_id}")
    if not blob:
        raise ApifyAcquisitionError(f"apify key-value record for {video_id} was empty")
    declared = item.get("file_size_bytes")
    if declared and abs(len(blob) - int(declared)) > 1024:
        raise ApifyAcquisitionError(
            f"apify artifact size mismatch for {video_id}: got {len(blob)}, declared {declared}"
        )

    tmpdir = Path(tempfile.mkdtemp(prefix="apify-audio-"))
    try:
        raw_path = tmpdir / f"{video_id}.mp3"
        raw_path.write_bytes(blob)
        probed = _probe_duration_s(raw_path)
        if probed is None:
            raise ApifyAcquisitionError(f"apify artifact for {video_id} is not decodable media")
        # The false-success gate. A clip is not the track, and the full-track
        # cache is the one place a clip must never land.
        if source_duration and probed < float(source_duration) * MIN_DURATION_RATIO:
            raise ApifyAcquisitionError(
                f"apify artifact for {video_id} holds {probed:.1f}s of a "
                f"{source_duration}s source — refusing to store a clip as a full track"
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        _transcode_to_wav(raw_path, dest)
        final = _probe_duration_s(dest)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    if trace_fn:
        trace_fn("apify_audio_acquired", video_id=video_id, run_id=run_id,
                 seconds=round(final or 0, 1), bytes=dest.stat().st_size,
                 cost_usd=cost, proxy_tier=item.get("proxy_tier_used"),
                 ms=int((time.monotonic() - t0) * 1000))
    return ApifyArtifact(
        path=dest, provenance=PROVENANCE, video_id=video_id, source_url=source_url,
        acquired_at=time.time(), source_duration_s=float(source_duration) if source_duration else None,
        analyzed_duration_s=final, is_full_track=True, artifact_type="wav",
        cost_usd=cost, proxy_tier=item.get("proxy_tier_used"),
    )


__all__ = [
    "ACTOR_ID", "PROVENANCE", "ApifyAcquisitionError", "ApifyArtifact", "ApifyUnavailable",
    "acquire", "attempted", "credential_status", "reset", "reset_attempts", "should_attempt",
    "token_path",
]
