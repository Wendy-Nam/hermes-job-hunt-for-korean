"""Route health, retry budget and circuit breaking for music media acquisition.

Why this exists: `music_youtube_audio` already had the right *idea* — a memo
that remembers "direct egress is currently bot-checked" so the next call skips
a doomed 1.6s round trip. Live measurement on 2026-08-10 confirms the cost is
real: a direct attempt on a gated video burns 1555ms before failing, every
time, and the proxy then succeeds in ~3.0s.

Three things that memo could not do, which this module adds:

1. **It was process-local and single-flavoured.** One module global, reset on
   every gateway restart, and only ever set for `bot_check` on `direct`. A
   timeout on the proxy route left no trace at all.
2. **It had no budget.** Nothing bounded total attempts per item; the bound
   came incidentally from there being exactly two transports.
3. **It had no backoff.** A route that just failed was retried at full speed.

What this module deliberately does NOT do: pick routes. It answers
"is this route currently worth trying, and how long should I wait first?" The
ladder in `music_media_fetch` owns the order.

Only *retryable* reasons open a breaker. A DRM-protected or private video
fails identically on every egress on earth; counting that against the route
would take a healthy proxy out of service because of one bad video. This
distinction is the whole reason `RETRYABLE_REASONS` is consulted here rather
than a raw failure count.

Determinism: `now_ms` and `rng` are injected. Nothing here calls `time.time()`
or `random` on its own, so the tests exercise real backoff arithmetic instead
of a mock.
"""
from __future__ import annotations

import json
import os
import random
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Mapping

try:  # pragma: no cover - import shape differs between plugin and test contexts
    from hermes_media_fetch import RETRYABLE_REASONS, ROUTES
except ImportError:  # pragma: no cover
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_shared"))
    from hermes_media_fetch import RETRYABLE_REASONS, ROUTES

# --- tunables ----------------------------------------------------------------
# Consecutive retryable failures before a route is taken out of rotation.
# 2 rather than 1: a single timeout is noise, two in a row is a pattern.
BREAKER_THRESHOLD = 2
# First cooldown, doubled per consecutive breaker trip, capped. The cap matches
# the 15 minutes the old `_DIRECT_BLOCK_MEMO_S` used, which was chosen because
# YouTube's datacenter-IP blocks heal on their own well inside that window.
BREAKER_BASE_COOLDOWN_MS = 60_000
BREAKER_MAX_COOLDOWN_MS = 15 * 60_000
# Total attempts across *all* routes for one item. Bounds worst-case latency.
MAX_ATTEMPTS_PER_ITEM = 4
# Backoff between attempts on the same item.
BACKOFF_BASE_MS = 250
BACKOFF_MAX_MS = 4_000
BACKOFF_JITTER = 0.25  # +/- 25%


@dataclass
class RouteState:
    """Health of one (provider, route) pair."""

    provider: str
    route: str
    consecutive_failures: int = 0
    breaker_trips: int = 0
    opened_until_ms: int = 0
    last_reason: str = ""
    last_failure_ms: int = 0
    last_success_ms: int = 0
    successes: int = 0
    failures: int = 0
    total_success_ms: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def backoff_delay_ms(attempt: int, *, rng: random.Random | None = None) -> int:
    """Exponential backoff with symmetric jitter. `attempt` is 0-based, so the
    first retry waits ~BACKOFF_BASE_MS, not zero and not a full second."""
    if attempt <= 0:
        return 0
    raw = min(BACKOFF_BASE_MS * (2 ** (attempt - 1)), BACKOFF_MAX_MS)
    if rng is None:
        return int(raw)
    factor = 1.0 + rng.uniform(-BACKOFF_JITTER, BACKOFF_JITTER)
    return max(0, int(raw * factor))


class RouteHealthStore:
    """Cross-process health state, persisted as one small JSON document.

    Persisted rather than in-memory because the gateway is not the only thing
    that fetches audio (cron batch jobs do too), and because a restart used to
    throw away a perfectly good "direct is blocked" observation and pay the
    1.6s penalty all over again on the next turn.

    Writes are atomic (tmp + os.replace) and best-effort: health data is an
    optimisation, and a fetch must never fail because a cache of route stats
    could not be written.
    """

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        now_ms: Callable[[], int] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.path = Path(path) if path else None
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._rng = rng
        self._lock = threading.RLock()
        self._states: dict[tuple[str, str], RouteState] = {}
        self._loaded = False

    # -- persistence ----------------------------------------------------------
    def _key(self, provider: str, route: str) -> tuple[str, str]:
        return (provider or "unknown", route)

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return  # corrupt health file is not worth failing a fetch over
        for entry in raw.get("routes", []):
            try:
                state = RouteState(**entry)
            except TypeError:
                continue  # a field this version does not know: skip, don't crash
            self._states[self._key(state.provider, state.route)] = state

    def _flush(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"routes": [s.to_dict() for s in self._states.values()]}
            tmp = self.path.with_name(f".{self.path.name}.tmp{os.getpid()}")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass

    # -- queries --------------------------------------------------------------
    def state(self, provider: str, route: str) -> RouteState:
        with self._lock:
            self._load()
            key = self._key(provider, route)
            if key not in self._states:
                self._states[key] = RouteState(provider=key[0], route=route)
            return self._states[key]

    def available(self, provider: str, route: str) -> bool:
        """False while this route's breaker is open."""
        return self.state(provider, route).opened_until_ms <= self._now_ms()

    def opens_in_ms(self, provider: str, route: str) -> int:
        return max(0, self.state(provider, route).opened_until_ms - self._now_ms())

    # -- updates --------------------------------------------------------------
    def record_success(self, provider: str, route: str, *, elapsed_ms: int = 0) -> None:
        with self._lock:
            st = self.state(provider, route)
            st.consecutive_failures = 0
            st.breaker_trips = 0
            st.opened_until_ms = 0
            st.last_reason = ""
            st.last_success_ms = self._now_ms()
            st.successes += 1
            st.total_success_ms += max(0, int(elapsed_ms))
            self._flush()

    def record_failure(self, provider: str, route: str, *, reason: str) -> None:
        """Count a failure; open the breaker only for route-attributable ones."""
        with self._lock:
            st = self.state(provider, route)
            st.failures += 1
            st.last_reason = reason
            st.last_failure_ms = self._now_ms()
            if reason not in RETRYABLE_REASONS:
                # Terminal for this media on any egress — says nothing about the
                # route's health, so it must not push the breaker toward open.
                st.consecutive_failures = 0
                self._flush()
                return
            st.consecutive_failures += 1
            if st.consecutive_failures >= BREAKER_THRESHOLD:
                st.breaker_trips += 1
                cooldown = min(
                    BREAKER_BASE_COOLDOWN_MS * (2 ** (st.breaker_trips - 1)),
                    BREAKER_MAX_COOLDOWN_MS,
                )
                st.opened_until_ms = self._now_ms() + cooldown
                st.consecutive_failures = 0
            self._flush()

    def reset(self, provider: str | None = None, route: str | None = None) -> None:
        """Operator/test seam — clear a stale breaker without a restart."""
        with self._lock:
            self._load()
            for key in list(self._states):
                if provider and key[0] != provider:
                    continue
                if route and key[1] != route:
                    continue
                del self._states[key]
            self._flush()

    # -- metrics --------------------------------------------------------------
    def metrics(self) -> dict:
        with self._lock:
            self._load()
            out: dict[str, dict] = {}
            for (prov, route), st in self._states.items():
                attempts = st.successes + st.failures
                out[f"{prov}/{route}"] = {
                    "attempts": attempts,
                    "successes": st.successes,
                    "failures": st.failures,
                    "success_rate": (st.successes / attempts) if attempts else None,
                    "mean_success_ms": (
                        st.total_success_ms / st.successes if st.successes else None
                    ),
                    "breaker_open": st.opened_until_ms > self._now_ms(),
                    "breaker_trips": st.breaker_trips,
                    "last_reason": st.last_reason,
                }
            return out

    def order_routes(self, provider: str, preferred: tuple[str, ...]) -> tuple[str, ...]:
        """Preferred order with currently-open routes pushed to the back.

        Pushed back, not dropped: if every route's breaker is open, trying a
        doomed route beats making no attempt at all and reporting a failure the
        network never actually produced. This is the same judgement
        `_transports()` made when no proxy was configured.
        """
        for route in preferred:
            if route not in ROUTES:
                raise ValueError(f"unknown route class: {route!r}")
        healthy = [r for r in preferred if self.available(provider, r)]
        blocked = [r for r in preferred if not self.available(provider, r)]
        return tuple(healthy + blocked)


__all__ = [
    "RouteState",
    "RouteHealthStore",
    "backoff_delay_ms",
    "BREAKER_THRESHOLD",
    "BREAKER_BASE_COOLDOWN_MS",
    "BREAKER_MAX_COOLDOWN_MS",
    "MAX_ATTEMPTS_PER_ITEM",
    "BACKOFF_BASE_MS",
    "BACKOFF_MAX_MS",
]
