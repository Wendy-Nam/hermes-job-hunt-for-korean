"""YouTube metadata + audio acquisition — download-once, cache-forever (until
evicted), used both for direct YouTube music links and as the actual
playable-audio source for Spotify-resolved tracks (task item 12: Spotify's
page URL alone never yields audio; this is the "existing audio acquisition
path" a Spotify resolution connects into).

Install dependency (vendored — see __init__.py's `.vendor` note for why it is
not in /opt/hermes/.venv): uv pip install --target .vendor yt-dlp

**The fallback chain applies to every yt-dlp call, not just the download.**
That distinction is the entire bug this module was rewritten for: YouTube
answers some videos from a datacenter IP with "Sign in to confirm you're not
a bot", and the *metadata* call is the first one the pipeline makes. The
proxy fallback used to exist only in resolve_audio, one stage too late — so
metadata failed, resolution raised, and the download that would have
succeeded over the proxy was never attempted. Verified live: video
JeucohIa5LQ fails direct on every player_client and resolves over the proxy
in ~2.5s.

Transport order is cache -> direct -> proxynet-resolved proxy -> typed
failure, never a fabricated success.
"""
from __future__ import annotations

import logging
import os
import sys
import time
import uuid
from pathlib import Path

import music_failure as failure

logger = logging.getLogger(__name__)

MAX_DURATION_S = 15 * 60  # tracks longer than this are unlikely to be a single song
DOWNLOAD_TIMEOUT_S = 90

# Once direct egress has been refused for a *video*, every later call about
# that same video would pay the same doomed round trip before failing over.
# Remember it briefly and go straight to the proxy instead. Short enough that a
# transient block heals on its own without a restart.
#
# **Keyed by video, not by egress.** This memo used to be a single global
# deadline: one bot-checked link disabled direct egress for fifteen minutes for
# *every* video the process touched afterwards. That is wrong, and measurably
# so — YouTube's bot check on this deployment is per-video, not per-IP. Measured
# 2026-08-11 from the production container: `JeucohIa5LQ`, `gdZLi9oWNZg` and
# `aqz-KE-bpKQ` bot_check on every player_client while `dQw4w9WgXcQ` resolves
# direct in 1.2s from the same address, repeatedly. Under the global memo one
# gated link sent every subsequent video down the proxy leg, which measured
# 3-5s against direct's ~1s — so the memo tripled the latency of the requests
# that were still working, in order to save a round trip on the ones that
# weren't.
_DIRECT_BLOCK_MEMO_S = 15 * 60
#: video_id -> monotonic deadline. Bounded so a long-lived process cannot grow
#: this without limit; entries are evicted oldest-deadline-first.
_MAX_BLOCK_MEMO_ENTRIES = 256
_direct_blocked_until: dict[str, float] = {}

# Separately from the per-video memo: the *observation* that direct and proxy
# have both been refused recently is a fact about this deployment's egress, and
# it is the right input for deciding whether a purely speculative extra request
# is worth making (see `egress_looks_gated`). It is deliberately not allowed to
# skip a transport — a video that would have worked must still be tried.
_EGRESS_GATED_WINDOW_S = 5 * 60
_egress_gate_observations: list[float] = []
_EGRESS_GATED_MIN_OBSERVATIONS = 2


class AudioResolveError(Exception):
    pass


# Two copies of proxynet ship in this deployment and they are NOT the same
# module: `skills/job-search` is the legacy one exposing only `resolve_proxy()`
# (a single proxy, TTL-cached), while `skills/shared-utils` adds
# `resolve_proxies(n)` — the multi-candidate API the youtube-content transcript
# path already uses. This module hardcoded the legacy path, so shared-music
# could only ever see one of the five configured egress candidates.
#
# Loaded by explicit file path rather than `import proxynet`: both files claim
# the same module name, so whichever landed in sys.modules first would win and
# the choice would silently depend on import order elsewhere in the process.
_PROXYNET_DIRS = ("shared-utils", "job-search")


def _load_proxynet():
    import importlib.util

    skills_root = Path(os.environ.get("HERMES_DATA") or "/opt/data") / "skills"
    for name in _PROXYNET_DIRS:
        path = skills_root / name / "proxynet.py"
        if not path.is_file():
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"_shared_music_proxynet_{name}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)  # type: ignore[union-attr]
            return module
        except Exception:
            logger.debug("proxynet at %s failed to load", path, exc_info=True)
    return None


def _resolved_proxy() -> str | None:
    proxynet = _load_proxynet()
    if proxynet is None:
        return None
    try:
        return proxynet.resolve_proxy() or None
    except Exception:
        logger.debug("proxynet.resolve_proxy failed", exc_info=True)
        return None


# Enumerating candidates costs a liveness probe per proxy plus a possible
# Webshare API call — measured at 21.8s for five candidates in production, which
# is far too slow to pay per download attempt. Cached per process, briefly.
_PROXY_LIST_TTL_S = 10 * 60
_proxy_list_cache: tuple[float, list[str]] | None = None


def resolved_proxies(n: int = 2) -> list[str]:
    """Up to `n` live egress candidates, best-effort and never raising.

    Falls back to the single `resolve_proxy()` when only the legacy proxynet is
    installed, so this is safe on a deployment that has not shipped
    `skills/shared-utils`.

    A candidate being "live" only means it answered a generic reachability
    probe. It says nothing about whether the *target* accepts it — which is the
    whole reason more than one is worth having.
    """
    global _proxy_list_cache
    now = time.time()
    if _proxy_list_cache and now - _proxy_list_cache[0] < _PROXY_LIST_TTL_S:
        return _proxy_list_cache[1][:n]

    proxynet = _load_proxynet()
    proxies: list[str] = []
    if proxynet is not None:
        resolve_many = getattr(proxynet, "resolve_proxies", None)
        try:
            if callable(resolve_many):
                proxies = [p for p in (resolve_many(n=max(n, 2)) or []) if p]
            else:
                single = proxynet.resolve_proxy()
                proxies = [single] if single else []
        except Exception:
            logger.debug("proxy candidate enumeration failed", exc_info=True)
            proxies = []

    _proxy_list_cache = (now, proxies)
    return proxies[:n]


def reset_proxy_cache() -> None:
    """Test seam / operator escape hatch for the candidate-list cache."""
    global _proxy_list_cache
    _proxy_list_cache = None


def _direct_is_blocked(video_id: str | None) -> bool:
    if not video_id:
        return False
    deadline = _direct_blocked_until.get(video_id)
    return deadline is not None and time.time() < deadline


def _note_direct_blocked(video_id: str | None) -> None:
    if not video_id:
        return
    _direct_blocked_until[video_id] = time.time() + _DIRECT_BLOCK_MEMO_S
    if len(_direct_blocked_until) > _MAX_BLOCK_MEMO_ENTRIES:
        for stale, _ in sorted(_direct_blocked_until.items(), key=lambda kv: kv[1])[
            : len(_direct_blocked_until) - _MAX_BLOCK_MEMO_ENTRIES
        ]:
            _direct_blocked_until.pop(stale, None)


def _note_egress_gated() -> None:
    """Record that *every* transport refused one video. See the module header."""
    now = time.time()
    _egress_gate_observations.append(now)
    cutoff = now - _EGRESS_GATED_WINDOW_S
    _egress_gate_observations[:] = [t for t in _egress_gate_observations if t >= cutoff][-32:]


def egress_looks_gated() -> bool:
    """Have several distinct videos just been refused on every transport?

    Used only to decide whether a *speculative* request (searching for a
    different upload of the same recording) is worth its round trips. Never
    used to skip a transport for a video the caller actually asked about: the
    bot check is per-video, so "the last two links were gated" is not evidence
    about this one.
    """
    cutoff = time.time() - _EGRESS_GATED_WINDOW_S
    return sum(1 for t in _egress_gate_observations if t >= cutoff) >= _EGRESS_GATED_MIN_OBSERVATIONS


def reset_transport_memo() -> None:
    """Test seam — also lets an operator clear a stale block without a restart."""
    _direct_blocked_until.clear()
    _egress_gate_observations.clear()


def _transports(video_id: str | None = None):
    """Yield (label, proxy) attempts in order, **resolving the proxy lazily**.

    Enumerating a proxy costs a liveness probe and, when proxynet's TTL cache
    has lapsed, a Webshare API call: measured at 6.4s in the production
    container on a cold cache. Building the whole attempt list up front paid
    that cost on every request, including the ones where the very first
    (direct) attempt was about to succeed — so a successful ordinary link took
    8.3s of which 6.4s was preparing a fallback it never used.

    A generator moves that cost behind the failure it exists for.
    """
    tried_direct = False
    if not _direct_is_blocked(video_id):
        tried_direct = True
        yield ("direct", None)
    proxy = _resolved_proxy()
    if proxy:
        yield ("proxy", proxy)
    elif not tried_direct:
        # Memo says direct is blocked for this video but no proxy is available —
        # try direct anyway rather than failing without making any attempt.
        yield ("direct", None)


def _run_with_fallback(run, *, stage: str, video_id: str | None, trace_fn=None):
    """Run `run(proxy)` over each transport in turn. Returns (result, label).
    Raises MusicPipelineError carrying the classified reason of the *last*
    attempt when every transport fails."""
    last_exc: BaseException | None = None
    last_reason = failure.REASON_UNKNOWN
    attempted = 0
    gated = 0
    for label, proxy in _transports(video_id):
        attempted += 1
        t0 = time.monotonic()
        try:
            result = run(proxy)
        except Exception as exc:  # noqa: BLE001 — classified below, never swallowed
            last_exc = exc
            # A stage that already knows what went wrong keeps its verdict.
            # `classify_error` reads an exception's *message*, so a
            # `MusicPipelineError` raised deliberately by `_run_download` —
            # "yt-dlp reported success but produced no audio file" — matched no
            # pattern and came back `unknown`. That lost the diagnosis, and
            # because `unknown` is retryable it also spent a proxy round trip
            # re-proving a failure that had nothing to do with the transport.
            if isinstance(exc, failure.MusicPipelineError) and exc.reason:
                last_reason = exc.reason
            else:
                last_reason = failure.classify_error(exc)
            if trace_fn:
                trace_fn(
                    "ytdlp_attempt_failed", stage=stage, transport=label, reason=last_reason,
                    video_id=video_id, ms=int((time.monotonic() - t0) * 1000),
                )
            logger.debug("%s via %s failed for %s (%s)", stage, label, video_id, last_reason, exc_info=True)
            if last_reason == failure.REASON_BOT_CHECK:
                gated += 1
                if label == "direct":
                    _note_direct_blocked(video_id)
            if not failure.is_retryable(last_reason):
                break  # terminal for this video on any route — don't burn a retry
            continue
        if trace_fn:
            trace_fn(
                "ytdlp_attempt_ok", stage=stage, transport=label, video_id=video_id,
                ms=int((time.monotonic() - t0) * 1000),
            )
        return result, label
    # Every transport we had refused this video. If they all refused it with a
    # bot check, that is the egress-level observation `egress_looks_gated`
    # accumulates — recorded once per video, not once per attempt, so a
    # deployment with more proxies does not trip the threshold faster.
    if attempted and gated >= attempted:
        _note_egress_gated()
    raise failure.MusicPipelineError(
        stage, last_reason, f"{stage} failed for {video_id}: {last_exc}", video_id=video_id
    )


def _audio_cache_dir(data_root: Path) -> Path:
    d = Path(data_root) / "audio_cache" / "shared-music"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cached_audio_path(data_root: Path, video_id: str) -> Path | None:
    path = _audio_cache_dir(data_root) / f"{video_id}.wav"
    if not (path.exists() and path.stat().st_size > 0):
        return None
    # Touch on read so eviction below is least-*recently-used* rather than
    # oldest-download: a track the two of them keep coming back to should not
    # be evicted just because it was first heard months ago.
    try:
        os.utime(path)
    except OSError:
        pass
    return path


# Decoded audio is stored as wav — roughly 10MB per minute, so a single
# 3-minute track costs ~40MB. The module has always described the cache as
# "download-once, cache-forever (until evicted)", but nothing ever evicted:
# the directory grew without bound until the disk did. This is that eviction.
DEFAULT_AUDIO_CACHE_MAX_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB ≈ 50 tracks


def audio_cache_max_bytes() -> int:
    raw = os.environ.get("HERMES_MUSIC_AUDIO_CACHE_MAX_BYTES")
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            logger.debug("ignoring non-integer HERMES_MUSIC_AUDIO_CACHE_MAX_BYTES=%r", raw)
    return DEFAULT_AUDIO_CACHE_MAX_BYTES


def prune_audio_cache(data_root: Path, *, keep: Path | None = None, trace_fn=None) -> int:
    """Evict least-recently-used wavs until the cache fits its cap.

    `keep` is never evicted no matter how large the cache is — it is the file
    the caller is about to analyse, and deleting it to satisfy the cap would
    turn a successful download into a failure. Best-effort throughout: a cache
    that cannot be pruned is not a reason to fail a turn.
    """
    cap = audio_cache_max_bytes()
    if cap <= 0:
        return 0
    try:
        entries = []
        for path in _audio_cache_dir(data_root).glob("*.wav"):
            try:
                st = path.stat()
            except OSError:
                continue
            entries.append((st.st_mtime, st.st_size, path))
    except OSError:
        return 0

    total = sum(size for _, size, _ in entries)
    if total <= cap:
        return 0

    keep = Path(keep).resolve() if keep else None
    evicted = 0
    for _mtime, size, path in sorted(entries):  # oldest access first
        if total <= cap:
            break
        if keep is not None and path.resolve() == keep:
            continue
        try:
            path.unlink()
        except OSError:
            continue
        total -= size
        evicted += 1
        if trace_fn:
            trace_fn("audio_cache_evicted", video_id=path.stem, bytes=size)
    return evicted


def cookie_file() -> Path | None:
    """Netscape-format cookie jar, if this deployment has one.

    yt-dlp's own documented answer to "Sign in to confirm you're not a bot" is
    an authenticated cookie jar. Wired as a pure opt-in: the file is picked up
    with no code change, and the pipeline behaves exactly as before when it is
    absent. Nothing here creates, fetches or requires credentials.

    Two paths are checked, dedicated first. The second is a jar Hermes may
    *already* hold: the acquisition audit found `$HERMES_DATA/cookies.txt` with
    YouTube entries on this host, and requiring the user to export a second jar
    while the system sits on one is exactly the credential duplication worth
    avoiding. (On this deployment that jar turned out to be expired — it still
    answers bot_check — which is why capability is reported from a real attempt
    in music_capability rather than inferred from the file existing.)
    """
    data_root = Path(os.environ.get("HERMES_DATA") or "/opt/data")
    for path in (data_root / ".youtube-cookies.txt", data_root / "cookies.txt"):
        try:
            if path.is_file() and path.stat().st_size > 0:
                return path
        except OSError:
            continue
    return None


def _base_opts(proxy: str | None) -> dict:
    opts = {"quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True}
    if proxy:
        opts["proxy"] = proxy
    cookies = cookie_file()
    if cookies:
        opts["cookiefile"] = str(cookies)
    return opts


def _run_download(video_id: str, out_path: Path, proxy: str | None) -> None:
    import yt_dlp  # imported lazily so the plugin still loads if the dep is missing

    # Download to a caller-unique stem and atomically rename on success: two
    # turns racing on the same track would otherwise have yt-dlp and ffmpeg
    # writing the same destination concurrently, and the loser could publish a
    # truncated wav that later reads as a perfectly valid cache hit. uuid4
    # rather than the pid because the gateway handles turns on threads, so a
    # pid is not unique per in-flight download.
    staging = out_path.with_name(f".{out_path.stem}.{uuid.uuid4().hex}.part")
    ydl_opts = _base_opts(proxy)
    ydl_opts.update(
        {
            "format": "bestaudio/best",
            "outtmpl": str(staging) + ".%(ext)s",
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
            "socket_timeout": DOWNLOAD_TIMEOUT_S,
            "match_filter": yt_dlp.utils.match_filter_func(f"duration < {MAX_DURATION_S}"),
        }
    )
    # Append, never with_suffix(): the staging stem already ends in ".part",
    # which with_suffix() would *replace*, leaving us looking for a file
    # yt-dlp never wrote.
    produced = staging.with_name(staging.name + ".wav")
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
        if not produced.exists() or produced.stat().st_size == 0:
            raise failure.MusicPipelineError(
                failure.STAGE_DOWNLOAD, failure.REASON_EMPTY_AUDIO,
                "yt-dlp reported success but produced no audio file", video_id=video_id,
            )
        os.replace(produced, out_path)
    finally:
        # Any leftovers from a failed/partial run — the staged wav plus
        # whatever intermediate container yt-dlp fetched before conversion.
        for leftover in staging.parent.glob(staging.name + "*"):
            try:
                leftover.unlink()
            except OSError:
                pass


def resolve_audio(data_root: Path, video_id: str, *, trace_fn=None) -> Path:
    """Returns a local wav path for `video_id`, downloading+caching on first use."""
    cached = cached_audio_path(data_root, video_id)
    if cached:
        if trace_fn:
            trace_fn("audio_cache_hit", video_id=video_id)
        return cached
    if trace_fn:
        trace_fn("audio_cache_miss", video_id=video_id)

    out_path = _audio_cache_dir(data_root) / f"{video_id}.wav"
    try:
        _run_with_fallback(
            lambda proxy: _run_download(video_id, out_path, proxy),
            stage=failure.STAGE_DOWNLOAD, video_id=video_id, trace_fn=trace_fn,
        )
    except failure.MusicPipelineError as exc:
        # Preserved as AudioResolveError so existing callers/tests keep working;
        # the typed stage/reason rides along for tracing.
        err = AudioResolveError(f"could not acquire audio for {video_id}: {exc}")
        err.stage, err.reason = exc.stage, exc.reason  # type: ignore[attr-defined]
        raise err from exc
    prune_audio_cache(data_root, keep=out_path, trace_fn=trace_fn)
    return out_path


def fetch_youtube_metadata_detailed(video_id: str, *, trace_fn=None) -> tuple[dict | None, str | None]:
    """(metadata, reason) — reason is None on success, else a REASON_* constant.

    Used instead of oEmbed for track resolution because oEmbed doesn't expose
    duration, which the canonical-id/cache-hit check needs before deciding
    whether to download anything at all.

    The URL is rebuilt from the extracted id rather than passed through from
    the user's message, so YouTube Music playlist context (`&list=RDAMVM...`)
    and tracking params (`&si=`) can never turn a single-track request into a
    playlist extraction or leak into track identity.
    """

    def _run(proxy: str | None) -> dict:
        import yt_dlp

        opts = _base_opts(proxy)
        opts["skip_download"] = True
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
        if not info:
            raise failure.MusicPipelineError(
                failure.STAGE_METADATA, failure.REASON_NO_METADATA, "empty info", video_id=video_id
            )
        return info

    try:
        info, _label = _run_with_fallback(
            _run, stage=failure.STAGE_METADATA, video_id=video_id, trace_fn=trace_fn
        )
    except failure.MusicPipelineError as exc:
        return None, exc.reason

    title = info.get("track") or info.get("title") or ""
    if not title:
        return None, failure.REASON_NO_METADATA
    return (
        {
            "title": title,
            # `artist` is the music-metadata field YouTube Music populates;
            # channel/uploader is the fallback for a plain upload.
            "artist": info.get("artist") or info.get("channel") or info.get("uploader") or "",
            "duration_s": info.get("duration"),
        },
        None,
    )


def fetch_youtube_metadata(video_id: str, *, trace_fn=None) -> dict | None:
    """Back-compat wrapper (this is the seam the test suite monkeypatches)."""
    meta, _reason = fetch_youtube_metadata_detailed(video_id, trace_fn=trace_fn)
    return meta


def search_youtube(query: str, *, max_results: int = 5, trace_fn=None) -> list[dict]:
    """ytsearch-based lookup, used only by the Spotify resolver to find a
    matching YouTube video for actual playback/analysis. Returns
    [{video_id, title, channel, duration_s}, ...], best-effort (empty list
    on any failure, never raises) — but now over the same transport chain, so
    a bot-checked direct route no longer silently yields "no results"."""

    def _run(proxy: str | None) -> dict:
        import yt_dlp

        opts = _base_opts(proxy)
        opts.update({"extract_flat": "in_playlist", "skip_download": True})
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(f"ytsearch{max_results}:{query}", download=False) or {}

    try:
        info, _label = _run_with_fallback(
            _run, stage=failure.STAGE_METADATA, video_id=None, trace_fn=trace_fn
        )
    except failure.MusicPipelineError:
        logger.debug("youtube search failed for query=%r", query, exc_info=True)
        return []

    out = []
    for e in info.get("entries") or []:
        if not e:
            continue
        out.append(
            {
                "video_id": e.get("id"),
                "title": e.get("title") or "",
                "channel": e.get("channel") or e.get("uploader") or "",
                "duration_s": e.get("duration"),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Progressive acquisition — first audio before the whole track has arrived
# ---------------------------------------------------------------------------
# The batch path downloads the entire track to wav and only then analyses it,
# so nothing at all is known about the music until the last byte has landed and
# the DSP has walked the whole file. Measured in the production container on a
# 213s track: resolve 1.5s + download 2.5s + DSP 12.3s ≈ 16.4s of blocked turn
# before <AGENT_NAME> can say one grounded thing about what is playing.
#
# Almost all of that is proportional to duration, and almost none of it is
# needed for the *first* thing worth saying. A 45-second prefix is enough for
# tempo, key, brightness and opening dynamics — the things a listener reacts to
# in the first bar — and costs a fraction of the download and a fraction of the
# DSP. The full track still follows; this only changes when the first grounded
# statement becomes possible.
#
# The prefix is deliberately cached **somewhere else**. Writing a truncated wav
# to `audio_cache/shared-music/<video_id>.wav` would later read back as a
# perfectly valid full-track cache hit, and every subsequent turn would analyse
# 45 seconds while claiming to have heard the whole song. That is the exact
# false-success this layer is not allowed to produce, so the two caches cannot
# share a namespace.

#: How much of the track the first-audio pass acquires.
DEFAULT_PREFIX_SECONDS = 45
#: A prefix download that has not produced audio by now is not a fast path.
PREFIX_TIMEOUT_S = 30


def _prefix_cache_dir(data_root: Path) -> Path:
    d = Path(data_root) / "audio_cache" / "shared-music-prefix"
    d.mkdir(parents=True, exist_ok=True)
    return d


def prefix_audio_path(data_root: Path, video_id: str, seconds: int) -> Path:
    """Where a `seconds`-long prefix of `video_id` lives.

    The length is part of the filename, not just of the file: a 30s prefix and
    a 45s prefix are different artifacts, and a cache keyed only by video id
    would hand a caller asking for 45s whatever length happened to be fetched
    first.
    """
    return _prefix_cache_dir(data_root) / f"{video_id}.{int(seconds)}s.wav"


def cached_prefix_path(data_root: Path, video_id: str, seconds: int) -> Path | None:
    """A usable cached prefix, or None. A *full* track already in cache wins:
    it is strictly more audio than the prefix, so there is nothing to fetch."""
    full = cached_audio_path(data_root, video_id)
    if full is not None:
        return full
    path = prefix_audio_path(data_root, video_id, seconds)
    try:
        if path.exists() and path.stat().st_size > 0:
            os.utime(path)
            return path
    except OSError:
        return None
    return None


def _run_prefix_download(video_id: str, out_path: Path, proxy: str | None, seconds: int) -> None:
    import yt_dlp

    staging = out_path.with_name(f".{out_path.stem}.{uuid.uuid4().hex}.part")
    ydl_opts = _base_opts(proxy)
    ydl_opts.update(
        {
            "format": "bestaudio/best",
            "outtmpl": str(staging) + ".%(ext)s",
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
            "socket_timeout": PREFIX_TIMEOUT_S,
            # The range is expressed against the *source* timeline and cut by
            # ffmpeg. `force_keyframes_at_cuts` is left off on purpose: forcing
            # a keyframe cut re-encodes, which is the cost this path exists to
            # avoid, and a prefix that starts a fraction of a second early is
            # perfectly good evidence about the opening of a song.
            "download_ranges": yt_dlp.utils.download_range_func(None, [(0, float(seconds))]),
        }
    )
    produced = staging.with_name(staging.name + ".wav")
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
        if not produced.exists() or produced.stat().st_size == 0:
            raise failure.MusicPipelineError(
                failure.STAGE_DOWNLOAD, failure.REASON_EMPTY_AUDIO,
                "prefix download reported success but produced no audio", video_id=video_id,
            )
        os.replace(produced, out_path)
    finally:
        for leftover in staging.parent.glob(staging.name + "*"):
            try:
                leftover.unlink()
            except OSError:
                pass


def resolve_audio_prefix(
    data_root: Path, video_id: str, *, seconds: int = DEFAULT_PREFIX_SECONDS, trace_fn=None
) -> tuple[Path, bool]:
    """First-audio acquisition: `(path, is_full_track)`.

    `is_full_track` is the half of the return value that stops this from
    becoming a lie. A caller that analyses the returned path and then reports
    on "the song" must know whether it heard the song or the first 45 seconds
    of it — every downstream claim about structure, outro or overall arc is
    only licensed when this is True. It is True only when the path is a genuine
    complete-track artifact, never inferred from the prefix looking long enough.

    Raises `AudioResolveError` exactly as `resolve_audio` does. There is no
    silent degradation to "no audio but here's a path".
    """
    seconds = max(1, int(seconds))
    full = cached_audio_path(data_root, video_id)
    if full is not None:
        if trace_fn:
            trace_fn("audio_cache_hit", video_id=video_id, scope="full")
        return full, True

    prefix = prefix_audio_path(data_root, video_id, seconds)
    try:
        if prefix.exists() and prefix.stat().st_size > 0:
            os.utime(prefix)
            if trace_fn:
                trace_fn("audio_cache_hit", video_id=video_id, scope="prefix", seconds=seconds)
            return prefix, False
    except OSError:
        pass
    if trace_fn:
        trace_fn("audio_cache_miss", video_id=video_id, scope="prefix", seconds=seconds)

    try:
        _run_with_fallback(
            lambda proxy: _run_prefix_download(video_id, prefix, proxy, seconds),
            stage=failure.STAGE_DOWNLOAD, video_id=video_id, trace_fn=trace_fn,
        )
    except failure.MusicPipelineError as exc:
        err = AudioResolveError(f"could not acquire audio prefix for {video_id}: {exc}")
        err.stage, err.reason = exc.stage, exc.reason  # type: ignore[attr-defined]
        raise err from exc
    if trace_fn:
        trace_fn(
            "audio_prefix_acquired", video_id=video_id, seconds=seconds,
            bytes=prefix.stat().st_size,
        )
    return prefix, False


def prune_prefix_cache(data_root: Path, *, keep: Path | None = None) -> int:
    """Prefixes are cheap to refetch and worthless once the full track lands.

    Kept far smaller than the full-track cache for that reason: it is a latency
    cache, not an archive.
    """
    cap = max(0, audio_cache_max_bytes() // 16)
    if cap <= 0:
        return 0
    try:
        entries = []
        for path in _prefix_cache_dir(data_root).glob("*.wav"):
            try:
                st = path.stat()
            except OSError:
                continue
            entries.append((st.st_mtime, st.st_size, path))
    except OSError:
        return 0
    total = sum(size for _, size, _ in entries)
    if total <= cap:
        return 0
    keep = Path(keep).resolve() if keep else None
    evicted = 0
    for _mtime, size, path in sorted(entries):
        if total <= cap:
            break
        if keep is not None and path.resolve() == keep:
            continue
        try:
            path.unlink()
        except OSError:
            continue
        total -= size
        evicted += 1
    return evicted
