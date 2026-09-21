"""The bounded transport ladder for music audio, behind MediaSource / MediaFetchResult.

This is Session C's half of the seam the contract describes: Fast Ear and Deep
Ear receive a `MediaFetchResult`, call `require_ok()`, and read `local_ref`.
They never learn that a proxy exists.

It is an *adaptation* of `music_youtube_audio`, not a second downloader. The
yt-dlp invocation, the staging/atomic-rename, the cookie opt-in, the error
classification and the option building all stay exactly where they are; this
module supplies the ordering, the health accounting, the validation and the
result type around them. `_run_download` and `fetch_youtube_metadata_detailed`
are called through injected callables so this file never imports yt_dlp.

Ladder (tier order; each tier is skipped when it has nothing to offer):

    T0 cache            a previously fetched artifact that still validates
    T1 direct           the canonical public source over default egress
    T2 alternate_source a different public/official upload of the same track
    T3 proxy            the configured outbound proxy
    T4 alternate_route  a second configured egress, when deployment provides one
    T5 upload           a local file the user attached

What is *not* here, deliberately: T5 does not include "metadata only". Metadata
is not audio. A metadata-only outcome returns `ok=False`, so the conversational
layer keeps AO's honest degraded mode and nothing downstream can claim to have
listened. That is the no-fake-listening invariant, and it is enforced by the
contract type itself rather than by convention — a failed `MediaFetchResult`
cannot legally hold a `local_ref`.

Live measurement (2026-08-10, production container, video JeucohIa5LQ):
direct fails `bot_check` in 1555ms, proxy then succeeds in 3049ms for metadata
and 5955ms for the download. That 1.5s is what the health breaker removes from
the second and every subsequent fetch.
"""
from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

import music_failure as failure
import music_media_validate as validate
import music_route_health as health

from hermes_media_fetch import (
    ROUTE_ALTERNATE_ROUTE,
    ROUTE_ALTERNATE_SOURCE,
    ROUTE_CACHE,
    ROUTE_DIRECT,
    ROUTE_PROXY,
    MediaFetchResult,
    MediaSource,
)
from hermes_media import KIND_MUSIC, Provenance, native_media_id

# Default tier order. Cache first because it is free; the two network tiers
# that need no configuration before the two that do.
DEFAULT_LADDER: tuple[str, ...] = (
    ROUTE_CACHE,
    ROUTE_DIRECT,
    ROUTE_ALTERNATE_SOURCE,
    ROUTE_PROXY,
    ROUTE_ALTERNATE_ROUTE,
)
# Routes that consume the per-item attempt budget. Cache does not: it makes no
# network attempt, so charging it would shorten the real ladder by one rung.
_NETWORK_ROUTES = frozenset(
    {ROUTE_DIRECT, ROUTE_ALTERNATE_SOURCE, ROUTE_PROXY, ROUTE_ALTERNATE_ROUTE}
)


def music_source(
    video_id: str,
    *,
    media_id_: str,
    reference: str = "",
    provider: str = "youtube",
    refreshable: bool = True,
) -> MediaSource:
    """A `MediaSource` for a YouTube-hosted track.

    `stable_identity=True` because the identity here is the *video id*, which
    does not rotate — not the signed CDN URL yt-dlp resolves internally and
    which never reaches this object. That is precisely the distinction
    `fetch_cache_key` refuses to let callers blur.
    """
    return MediaSource(
        source_id=f"yt:{video_id}",
        media_id=media_id_,
        kind=KIND_MUSIC,
        reference=reference or f"https://www.youtube.com/watch?v={video_id}",
        provider=provider,
        native_id=video_id,
        refreshable=refreshable,
        stable_identity=True,
    )


@dataclass
class FetchMetrics:
    """Counters for the benchmark. Plain ints; the caller snapshots them."""

    requests: int = 0
    successes: int = 0
    failures: int = 0
    cache_hits: int = 0
    cache_rejected: int = 0
    dedup_suppressed: int = 0
    refreshes: int = 0
    validation_failures: int = 0
    attempts_by_route: dict = field(default_factory=dict)
    successes_by_route: dict = field(default_factory=dict)
    durations_ms: list = field(default_factory=list)

    def _bump(self, bucket: dict, route: str) -> None:
        bucket[route] = bucket.get(route, 0) + 1

    def percentile_ms(self, pct: float) -> float | None:
        if not self.durations_ms:
            return None
        ordered = sorted(self.durations_ms)
        idx = min(len(ordered) - 1, max(0, int(round((pct / 100.0) * (len(ordered) - 1)))))
        return float(ordered[idx])

    def snapshot(self) -> dict:
        total = self.successes + self.failures
        network_attempts = sum(
            c for r, c in self.attempts_by_route.items() if r in _NETWORK_ROUTES
        )
        return {
            "requests": self.requests,
            "successes": self.successes,
            "failures": self.failures,
            "success_rate": (self.successes / total) if total else None,
            "cache_hit_rate": (self.cache_hits / self.requests) if self.requests else None,
            "cache_rejected": self.cache_rejected,
            "dedup_suppressed": self.dedup_suppressed,
            "validation_failures": self.validation_failures,
            "refreshes": self.refreshes,
            "attempts_by_route": dict(self.attempts_by_route),
            "successes_by_route": dict(self.successes_by_route),
            "retries_per_item": (network_attempts / self.requests) if self.requests else None,
            "p50_ms": self.percentile_ms(50),
            "p90_ms": self.percentile_ms(90),
        }


class _Attempt(Exception):
    """Internal: a tier failed with a classified reason."""

    def __init__(self, reason: str, stage: str, detail: str = ""):
        self.reason = reason
        self.stage = stage
        self.detail = detail
        super().__init__(detail or f"{stage}/{reason}")


class MusicFetchLadder:
    """Bounded, health-aware acquisition of playable audio for one track.

    Every collaborator is injected. The production wiring lives in
    `music_realtime.build_default_ladder`, which supplies the real
    `music_youtube_audio` functions; the tests supply deterministic stubs and
    therefore exercise this class's real ordering, budget, breaker, dedup and
    validation logic rather than a mock of it.
    """

    def __init__(
        self,
        *,
        cache_dir: Path,
        download: Callable[[str, Path, str | None], None],
        proxy_provider: Callable[[], str | None] | None = None,
        alternate_route_provider: Callable[[], str | None] | None = None,
        alternate_source_finder: Callable[[MediaSource], str | None] | None = None,
        refresh_source: Callable[[MediaSource], MediaSource | None] | None = None,
        health_store: health.RouteHealthStore | None = None,
        validator: Callable[..., validate.ValidationOutcome] | None = None,
        ladder: Sequence[str] = DEFAULT_LADDER,
        max_attempts: int = health.MAX_ATTEMPTS_PER_ITEM,
        sleep: Callable[[float], None] | None = None,
        monotonic_ms: Callable[[], int] | None = None,
        rng: random.Random | None = None,
        trace_fn: Callable[..., None] | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self._download = download
        self._proxy_provider = proxy_provider or (lambda: None)
        self._alternate_route_provider = alternate_route_provider or (lambda: None)
        self._alternate_source_finder = alternate_source_finder
        self._refresh_source = refresh_source
        self.health = health_store or health.RouteHealthStore()
        self._validate = validator or validate.validate_audio_file
        self.ladder = tuple(ladder)
        self.max_attempts = max_attempts
        self._sleep = sleep if sleep is not None else time.sleep
        self._monotonic_ms = monotonic_ms or (lambda: int(time.monotonic() * 1000))
        self._rng = rng or random.Random(0)
        self._trace = trace_fn or (lambda *a, **k: None)
        self.metrics = FetchMetrics()
        self._inflight: dict[str, threading.Event] = {}
        self._inflight_lock = threading.Lock()

    # -- paths ----------------------------------------------------------------
    def cached_path(self, source: MediaSource) -> Path:
        """Where a fetched artifact lives.

        Keyed on `native_id`, i.e. on media identity, so the path survives a
        signed-URL rotation. `fetch_cache_key` is the contract-level identity
        and refuses unstable sources outright; this is the filesystem name.
        """
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        stem = source.native_id or source.source_id.replace(":", "_")
        return self.cache_dir / f"{stem}.wav"

    # -- the ladder -----------------------------------------------------------
    def fetch(
        self,
        source: MediaSource,
        *,
        expected_duration_s: float | None = None,
        upload_ref: str | None = None,
    ) -> MediaFetchResult:
        """Walk the ladder. Returns a success or a typed, honest failure."""
        started = self._monotonic_ms()
        self.metrics.requests += 1
        dedup_key = f"{source.provider}:{source.native_id or source.source_id}"

        waited = self._await_inflight(dedup_key)
        if waited:
            # Another thread just fetched this exact track. Its artifact is on
            # disk and already validated, so serve it instead of downloading
            # the same 64MB a second time.
            cached = self._try_cache(source, expected_duration_s)
            if cached is not None:
                self.metrics.dedup_suppressed += 1
                self.metrics.successes += 1
                self.metrics.durations_ms.append(self._monotonic_ms() - started)
                self._trace("media_fetch_dedup", media_id=source.media_id)
                return cached

        self._claim_inflight(dedup_key)
        try:
            return self._walk(source, expected_duration_s, upload_ref, started)
        finally:
            self._release_inflight(dedup_key)

    def _walk(
        self,
        source: MediaSource,
        expected_duration_s: float | None,
        upload_ref: str | None,
        started: int,
    ) -> MediaFetchResult:
        attempts: list[str] = []
        network_attempts = 0
        last_reason = failure.REASON_UNKNOWN
        last_stage = failure.STAGE_DOWNLOAD
        last_detail = ""
        refreshed = False
        # Set when a tier returns a verdict that is about the *media*, not the
        # route: DRM, private, removed, age-restricted. Every egress on earth
        # gets the same answer, so the only tier still worth running is the one
        # that swaps in a different upload.
        upload_is_terminal = False

        order = self._ordered_ladder(source)
        for route in order:
            if route in _NETWORK_ROUTES and network_attempts >= self.max_attempts:
                self._trace("media_fetch_budget_exhausted", media_id=source.media_id,
                            attempts=network_attempts)
                break
            if upload_is_terminal and route != ROUTE_ALTERNATE_SOURCE:
                continue

            if route == ROUTE_CACHE:
                hit = self._try_cache(source, expected_duration_s)
                attempts.append(ROUTE_CACHE)
                if hit is not None:
                    self.metrics.cache_hits += 1
                    self.metrics.successes += 1
                    self.metrics._bump(self.metrics.successes_by_route, ROUTE_CACHE)
                    self.metrics.durations_ms.append(self._monotonic_ms() - started)
                    self._trace("media_cache_hit", media_id=source.media_id)
                    return hit
                continue

            proxy = self._proxy_for(route)
            if proxy is _UNCONFIGURED:
                continue  # this tier has no configuration in this deployment

            target_id, target_source = self._target_for(route, source)
            if target_id is None:
                continue  # no alternate upload found; tier has nothing to offer

            if network_attempts:
                delay = health.backoff_delay_ms(network_attempts, rng=self._rng)
                if delay:
                    self._sleep(delay / 1000.0)

            if route == ROUTE_ALTERNATE_SOURCE:
                upload_is_terminal = False  # the verdict was about the old upload

            attempts.append(route)
            network_attempts += 1
            self.metrics._bump(self.metrics.attempts_by_route, route)
            t0 = self._monotonic_ms()
            out_path = self.cached_path(target_source)
            try:
                self._download(target_id, out_path, proxy)
            except Exception as exc:  # noqa: BLE001 — classified, never swallowed
                reason = _classified(exc)
                stage = getattr(exc, "stage", None) or failure.STAGE_DOWNLOAD
                last_reason, last_stage, last_detail = reason, stage, str(exc)[:300]
                self.health.record_failure(source.provider, route, reason=reason)
                self._trace("media_fetch_attempt_failed", route=route, reason=reason,
                            media_id=source.media_id, ms=self._monotonic_ms() - t0)

                if (
                    reason in (failure.REASON_NOT_FOUND, failure.REASON_NETWORK)
                    and target_source.refreshable
                    and self._refresh_source
                    and not refreshed
                ):
                    # A grant that expired between resolution and download.
                    # Refresh once and retry the same route before demoting it;
                    # falling through to the proxy would blame the wrong thing.
                    refreshed = True
                    fresh = self._refresh_source(target_source)
                    if fresh is not None:
                        self.metrics.refreshes += 1
                        self._trace("media_fetch_source_refreshed",
                                    media_id=source.media_id, route=route)
                        source = fresh if target_source is source else source
                        try:
                            self._download(fresh.native_id, self.cached_path(fresh), proxy)
                        except Exception as exc2:  # noqa: BLE001
                            last_reason = _classified(exc2)
                            last_detail = str(exc2)[:300]
                            continue
                        target_source = fresh
                        out_path = self.cached_path(fresh)
                    else:
                        continue
                elif not failure.is_retryable(reason):
                    # Terminal for this media on any egress. Keep walking only
                    # to the alternate-*source* tier, which is a different
                    # recording and therefore not covered by that verdict.
                    if ROUTE_ALTERNATE_SOURCE in order[order.index(route) + 1:]:
                        upload_is_terminal = True
                        continue
                    break
                else:
                    continue

            outcome = self._validate(out_path, expected_duration_s=expected_duration_s)
            if not outcome.ok:
                # Downloaded something; it is not usable audio. Delete it so it
                # can never be served as a cache hit, and treat it as this
                # route's failure.
                _unlink(out_path)
                self.metrics.validation_failures += 1
                last_reason, last_stage = outcome.reason, outcome.stage
                last_detail = outcome.detail
                self.health.record_failure(source.provider, route, reason=outcome.reason)
                self._trace("media_fetch_validation_failed", route=route,
                            reason=outcome.reason, media_id=source.media_id,
                            detail=outcome.detail)
                if not failure.is_retryable(outcome.reason):
                    if ROUTE_ALTERNATE_SOURCE in order[order.index(route) + 1:]:
                        upload_is_terminal = True
                        continue
                    break
                continue

            elapsed = self._monotonic_ms() - t0
            self.health.record_success(source.provider, route, elapsed_ms=elapsed)
            self.metrics.successes += 1
            self.metrics._bump(self.metrics.successes_by_route, route)
            self.metrics.durations_ms.append(self._monotonic_ms() - started)
            self._trace("media_fetch_ok", route=route, media_id=source.media_id, ms=elapsed)
            return MediaFetchResult(
                ok=True,
                source=target_source,
                route=route,
                local_ref=str(out_path),
                resolved_reference=target_source.reference,
                content_type=outcome.content_type,
                bytes_len=outcome.bytes_len,
                duration_s=outcome.duration_s,
                attempts=tuple(attempts),
                provenance=Provenance(producer="shared-music/media-fetch"),
            )

        # T5 — a file the user attached. Real bytes, so a real success; it is
        # the only non-network tier that can produce one.
        if upload_ref:
            outcome = self._validate(Path(upload_ref), expected_duration_s=expected_duration_s)
            if outcome.ok:
                upload = MediaSource(
                    source_id=f"upload:{source.media_id}", media_id=source.media_id,
                    kind=KIND_MUSIC, source_kind="upload", reference=str(upload_ref),
                    provider="local", stable_identity=True,
                )
                self.metrics.successes += 1
                self.metrics.durations_ms.append(self._monotonic_ms() - started)
                self._trace("media_fetch_upload", media_id=source.media_id)
                return MediaFetchResult(
                    ok=True, source=upload, route=ROUTE_DIRECT, local_ref=str(upload_ref),
                    content_type=outcome.content_type, bytes_len=outcome.bytes_len,
                    duration_s=outcome.duration_s, attempts=tuple(attempts),
                    provenance=Provenance(producer="shared-music/user-upload"),
                )
            last_reason, last_stage, last_detail = outcome.reason, outcome.stage, outcome.detail

        self.metrics.failures += 1
        self.metrics.durations_ms.append(self._monotonic_ms() - started)
        self._trace("media_fetch_exhausted", media_id=source.media_id,
                    reason=last_reason, attempts=len(attempts))
        return MediaFetchResult.failure(
            source, stage=last_stage, reason=last_reason,
            route=attempts[-1] if attempts else ROUTE_DIRECT,
            attempts=tuple(attempts), detail=last_detail,
        )

    # -- tiers ----------------------------------------------------------------
    def _ordered_ladder(self, source: MediaSource) -> tuple[str, ...]:
        """Configured order, with open-breaker routes demoted to the back.

        Cache keeps its place at the front regardless: it is not a route whose
        health can degrade, and reordering it would only cost a free hit.
        """
        network = tuple(r for r in self.ladder if r in _NETWORK_ROUTES)
        reordered = self.health.order_routes(source.provider, network)
        out: list[str] = []
        pending = list(reordered)
        for route in self.ladder:
            if route in _NETWORK_ROUTES:
                if pending:
                    out.append(pending.pop(0))
            else:
                out.append(route)
        return tuple(out)

    def _proxy_for(self, route: str):
        if route in (ROUTE_DIRECT, ROUTE_ALTERNATE_SOURCE):
            return None
        provider = (
            self._proxy_provider if route == ROUTE_PROXY else self._alternate_route_provider
        )
        value = provider()
        return value if value else _UNCONFIGURED

    def _target_for(self, route: str, source: MediaSource):
        if route != ROUTE_ALTERNATE_SOURCE:
            return (source.native_id or None), source
        if not self._alternate_source_finder:
            return None, source
        alt_id = self._alternate_source_finder(source)
        if not alt_id or alt_id == source.native_id:
            return None, source
        alt = music_source(
            alt_id,
            media_id_=source.media_id,  # same song, so the same media identity
            provider=source.provider,
        )
        return alt_id, alt

    def _try_cache(
        self, source: MediaSource, expected_duration_s: float | None
    ) -> MediaFetchResult | None:
        """A cache hit that is re-validated, not merely non-empty.

        `require_decode=False`: the artifact passed a full probe when it was
        written, so the cheap structural checks (size, magic, RIFF length) are
        enough to catch the case this guards against — a truncated file left by
        a crash — without paying ffprobe on every turn.
        """
        path = self.cached_path(source)
        if not path.exists():
            return None
        outcome = self._validate(
            path, expected_duration_s=expected_duration_s, require_decode=False
        )
        if not outcome.ok:
            _unlink(path)
            self.metrics.cache_rejected += 1
            self._trace("media_cache_rejected", media_id=source.media_id,
                        reason=outcome.reason, detail=outcome.detail)
            return None
        return MediaFetchResult(
            ok=True, source=source, route=ROUTE_CACHE, local_ref=str(path),
            content_type=outcome.content_type, bytes_len=outcome.bytes_len,
            duration_s=outcome.duration_s, attempts=(ROUTE_CACHE,),
            provenance=Provenance(producer="shared-music/media-cache"),
        )

    # -- duplicate suppression ------------------------------------------------
    def _await_inflight(self, key: str) -> bool:
        with self._inflight_lock:
            event = self._inflight.get(key)
        if event is None:
            return False
        event.wait(timeout=180)
        return True

    def _claim_inflight(self, key: str) -> None:
        with self._inflight_lock:
            self._inflight[key] = threading.Event()

    def _release_inflight(self, key: str) -> None:
        with self._inflight_lock:
            event = self._inflight.pop(key, None)
        if event is not None:
            event.set()


class _Unconfigured:
    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "<unconfigured>"


_UNCONFIGURED = _Unconfigured()


def _classified(exc: BaseException) -> str:
    """Prefer a reason the raiser already computed over re-parsing its text."""
    reason = getattr(exc, "reason", None)
    if isinstance(reason, str) and reason in failure_reasons():
        return reason
    return failure.classify_error(exc)


def failure_reasons() -> frozenset:
    return frozenset(
        value for name, value in vars(failure).items()
        if name.startswith("REASON_") and isinstance(value, str)
    )


def _unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


__all__ = [
    "MusicFetchLadder",
    "FetchMetrics",
    "music_source",
    "DEFAULT_LADDER",
]
