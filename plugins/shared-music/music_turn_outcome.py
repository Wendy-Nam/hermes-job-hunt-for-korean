"""Per-turn outcome taxonomy and stage timing.

The failure this exists to prevent is a reading failure, not a code failure.
The production trace could say `track_resolved` and nothing else, and a reader
(human or dashboard) would conclude the track had been *listened to*. It had
not: over three days, 177 music_trigger events produced 2 resolutions and zero
perceptions, and no single field in the log said so.

So every music turn now ends with exactly one verdict:

    LISTENED        real audio was decoded and a perception object built
    METADATA_ONLY   the song is identified; no audio was obtained
    FAILED          we do not even know what the song is

and the stage timings that produced it. Nothing here decides anything — it only
makes the decision that was already made legible, which is why it is a separate
module from the pipeline that makes it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

OUTCOME_LISTENED = "LISTENED"
OUTCOME_METADATA_ONLY = "METADATA_ONLY"
OUTCOME_FAILED = "FAILED"
OUTCOMES = (OUTCOME_LISTENED, OUTCOME_METADATA_ONLY, OUTCOME_FAILED)

# Stage names, ordered as the pipeline walks them. Kept as constants so the
# timing keys can't drift from the event names a runbook greps for.
STAGE_RESOLVE = "resolve"
STAGE_SEARCH = "search"
STAGE_DOWNLOAD = "download"
STAGE_TRANSCODE = "transcode"
STAGE_DECODE = "decode"
STAGE_DSP = "dsp"
STAGE_PACKAGING = "perception_packaging"


@dataclass
class TurnTrace:
    """Accumulates stage timings and backend attempts for one music turn.

    Deliberately not a context manager per stage: several stages are optional
    and some are entered from inside a callback, so an explicit `mark` reads
    more honestly than nested `with` blocks that lie about what ran.
    """

    outcome: str = OUTCOME_FAILED
    reason: str | None = None
    code: str | None = None
    stages_ms: dict = field(default_factory=dict)
    attempted_backends: list = field(default_factory=list)
    backend_results: list = field(default_factory=list)
    selected_backend: str | None = None
    _clock: object = time.monotonic

    def stage(self, name: str):
        return _StageTimer(self, name)

    def mark(self, name: str, ms: int) -> None:
        # Accumulate rather than overwrite: the ladder can enter `download`
        # several times, and reporting only the last one would hide the cost of
        # the attempts that failed.
        self.stages_ms[name] = self.stages_ms.get(name, 0) + int(ms)

    def record_backend(self, route: str, *, ok: bool, code: str | None = None, ms: int = 0) -> None:
        self.attempted_backends.append(route)
        self.backend_results.append({"route": route, "ok": ok, "code": code, "ms": ms})
        if ok:
            self.selected_backend = route

    @property
    def total_ms(self) -> int:
        return sum(self.stages_ms.values())

    def as_fields(self) -> dict:
        """Flat dict for the trace log. No secrets: routes are class labels
        ("proxy"), never URLs, and no credential material passes through here."""
        return {
            "outcome": self.outcome,
            "reason": self.reason,
            "code": self.code,
            "stages_ms": dict(self.stages_ms),
            "total_ms": self.total_ms,
            "attempted_backends": list(self.attempted_backends),
            "backend_results": list(self.backend_results),
            "selected_backend": self.selected_backend,
            "final_capability": self.outcome,
        }


class _StageTimer:
    def __init__(self, trace: TurnTrace, name: str):
        self._trace = trace
        self._name = name

    def __enter__(self):
        self._t0 = time.monotonic()
        return self

    def __exit__(self, *_exc):
        self._trace.mark(self._name, int((time.monotonic() - self._t0) * 1000))
        return False
