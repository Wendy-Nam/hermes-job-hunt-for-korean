"""Failure taxonomy for the shared-music pipeline.

Why this exists: the whole pipeline used to collapse every distinct failure
into one trace line — `track_resolved_failed source=youtube` — because
`fetch_youtube_metadata` caught *every* exception and returned None. A missing
dependency, a private video, a DRM-protected stream and YouTube's datacenter-IP
bot check were indistinguishable in the log, so the live failure that motivated
this work could not be diagnosed from `logs/shared-music/trace.jsonl` at all.

Two orthogonal axes, deliberately kept separate:

  STAGE  — *where* the pipeline stopped. Ordered, one per pipeline step, so a
           log reader can tell "we never got metadata" from "we had metadata
           and the download died".
  REASON — *why* that stage stopped. Reasons are transport-independent so the
           same reason can arise at more than one stage (a bot check can hit
           metadata *and* download).

Classification is **advisory only**. The fallback chain in
music_youtube_audio retries on its own schedule; nothing about recovery
depends on parsing an error string. `RETRYABLE_VIA_PROXY` is used purely to
skip a pointless second network round trip on terminal reasons (DRM, private,
removed) — if the classifier ever mislabels one of those, the cost is one
wasted retry or one missed retry, never a wrong answer. This is why string
matching is acceptable here and would not be as the actual fix.
"""
from __future__ import annotations

# --- stages -----------------------------------------------------------------
STAGE_DETECT = "detect"
STAGE_NORMALIZE = "normalize"
STAGE_METADATA = "metadata"
STAGE_AUDIO_RESOLVE = "audio_resolve"
STAGE_DOWNLOAD = "download"
STAGE_DECODE = "decode"
STAGE_ANALYSIS = "analysis"
STAGE_DEEP_DIVE = "deep_dive"

# --- reasons ----------------------------------------------------------------
REASON_BOT_CHECK = "bot_check"
REASON_DRM = "drm_protected"
REASON_PRIVATE = "private"
REASON_REMOVED = "removed"
REASON_AGE_RESTRICTED = "age_restricted"
REASON_GEO_BLOCKED = "geo_blocked"
REASON_MEMBERS_ONLY = "members_only"
REASON_LIVE = "live_stream"
REASON_TOO_LONG = "too_long"
REASON_NETWORK = "network"
REASON_TIMEOUT = "timeout"
REASON_DEPENDENCY_MISSING = "dependency_missing"
REASON_EMPTY_AUDIO = "empty_audio"
REASON_NO_METADATA = "no_metadata"
# Present in the contract vocabulary since the wave began; mirrored here so
# the two modules stay a true mirror rather than a one-way subset.
REASON_NOT_FOUND = "not_found"
# --- content-integrity reasons (Session C extension, 2026-08-10) ------------
# Added because the frozen vocabulary could express "the transport failed" but
# not "the transport succeeded and handed us something that is not usable
# audio" — a truncated wav, an HTML error page saved under an audio name, or a
# container no decoder here can open. Those are exactly the cases
# music_media_validate exists to catch, and collapsing them into
# REASON_EMPTY_AUDIO would make a poisoned cache entry indistinguishable from a
# zero-byte download in the trace.
REASON_CORRUPT_MEDIA = "corrupt_media"          # partial / truncated / undersized
REASON_MIME_MISMATCH = "mime_mismatch"          # served something that is not audio
REASON_CODEC_UNSUPPORTED = "codec_unsupported"  # audio, but nothing here decodes it
REASON_UNKNOWN = "unknown"

# Reasons where a second attempt over a different egress IP can plausibly
# succeed. Everything absent from this set is terminal for *this* video no
# matter which route we take, so retrying only costs latency.
# REASON_CORRUPT_MEDIA is in here and its two siblings are not, deliberately: a
# truncated download is a transport accident a second attempt can fix, whereas
# an HTML error body or an undecodable codec arrives identically over every
# egress and retrying only costs latency.
RETRYABLE_VIA_PROXY = frozenset(
    {
        REASON_BOT_CHECK, REASON_GEO_BLOCKED, REASON_NETWORK, REASON_TIMEOUT,
        REASON_UNKNOWN, REASON_CORRUPT_MEDIA,
    }
)

# Ordered most-specific-first: "sign in to confirm you're not a bot" also
# contains "sign in", so a naive dict iteration could mislabel it as private.
_SIGNATURES: tuple[tuple[str, str], ...] = (
    ("confirm you're not a bot", REASON_BOT_CHECK),
    ("confirm you’re not a bot", REASON_BOT_CHECK),  # U+2019, what yt-dlp actually emits
    ("sign in to confirm", REASON_BOT_CHECK),
    ("drm protected", REASON_DRM),
    ("drm-protected", REASON_DRM),
    ("members-only", REASON_MEMBERS_ONLY),
    ("join this channel", REASON_MEMBERS_ONLY),
    ("private video", REASON_PRIVATE),
    ("age-restricted", REASON_AGE_RESTRICTED),
    ("inappropriate for some users", REASON_AGE_RESTRICTED),
    ("removed by the uploader", REASON_REMOVED),
    ("video unavailable", REASON_REMOVED),
    ("no longer available", REASON_REMOVED),
    ("not available in your country", REASON_GEO_BLOCKED),
    ("blocked it in your country", REASON_GEO_BLOCKED),
    ("live event will begin", REASON_LIVE),
    ("is live", REASON_LIVE),
    ("timed out", REASON_TIMEOUT),
    ("timeout", REASON_TIMEOUT),
    ("connection reset", REASON_NETWORK),
    ("temporary failure in name resolution", REASON_NETWORK),
    ("unable to download", REASON_NETWORK),
)


def classify_error(exc: BaseException | None) -> str:
    """Map an exception to a REASON_* constant. Advisory only — see module
    docstring. Never raises, and returns REASON_UNKNOWN rather than guessing
    when nothing matches, so an unrecognised failure stays retryable."""
    if exc is None:
        return REASON_UNKNOWN
    if isinstance(exc, (ImportError, ModuleNotFoundError)):
        return REASON_DEPENDENCY_MISSING
    text = f"{type(exc).__name__}: {exc}".lower()
    for needle, reason in _SIGNATURES:
        if needle in text:
            return reason
    if isinstance(exc, (OSError, TimeoutError)):
        return REASON_NETWORK
    return REASON_UNKNOWN


def is_retryable(reason: str) -> bool:
    return reason in RETRYABLE_VIA_PROXY


# --- public failure codes ----------------------------------------------------
# The internal REASON_* vocabulary is fine-grained and grew organically; the
# codes below are the stable, coarse set that diagnostics and any future
# dashboard read. Keeping them separate means a new internal reason does not
# silently become a new public code, and a public code can stay stable while the
# classifier underneath it gets more precise.
CODE_BOT_CHECK = "BOT_CHECK"
CODE_REGION_BLOCK = "REGION_BLOCK"
CODE_PRIVATE = "PRIVATE"
CODE_NOT_FOUND = "NOT_FOUND"
CODE_NETWORK = "NETWORK"
CODE_DECODER = "DECODER"
CODE_OVER_LENGTH = "OVER_LENGTH"
CODE_RATE_LIMIT = "RATE_LIMIT"
CODE_MEDIA_UNAVAILABLE = "MEDIA_UNAVAILABLE"
CODE_UNKNOWN = "UNKNOWN"

_REASON_TO_CODE: dict[str, str] = {
    REASON_BOT_CHECK: CODE_BOT_CHECK,
    REASON_GEO_BLOCKED: CODE_REGION_BLOCK,
    REASON_PRIVATE: CODE_PRIVATE,
    REASON_MEMBERS_ONLY: CODE_PRIVATE,
    REASON_AGE_RESTRICTED: CODE_PRIVATE,
    REASON_DRM: CODE_MEDIA_UNAVAILABLE,
    REASON_REMOVED: CODE_MEDIA_UNAVAILABLE,
    REASON_LIVE: CODE_MEDIA_UNAVAILABLE,
    REASON_EMPTY_AUDIO: CODE_MEDIA_UNAVAILABLE,
    REASON_NOT_FOUND: CODE_NOT_FOUND,
    REASON_NO_METADATA: CODE_NOT_FOUND,
    REASON_NETWORK: CODE_NETWORK,
    REASON_TIMEOUT: CODE_NETWORK,
    REASON_TOO_LONG: CODE_OVER_LENGTH,
    REASON_CORRUPT_MEDIA: CODE_DECODER,
    REASON_MIME_MISMATCH: CODE_DECODER,
    REASON_CODEC_UNSUPPORTED: CODE_DECODER,
    REASON_DEPENDENCY_MISSING: CODE_DECODER,
}


def public_failure_code(reason: str | None) -> str:
    """Coarse, stable code for a fine-grained internal reason."""
    return _REASON_TO_CODE.get(reason or "", CODE_UNKNOWN)


# --- backend-level outcome codes ---------------------------------------------
# Which *backend* failed, as distinct from why the media was refused. A
# bot_check over the direct egress and a bot_check over a proxy are the same
# REASON and very different operational facts: the first says "this IP is
# burned", the second says "the whole egress pool is burned", which is what the
# gated-track measurement actually showed.
BACKEND_DIRECT_BOT_CHECK = "DIRECT_BOT_CHECK"
BACKEND_PROXY_FAILED = "PROXY_FAILED"
BACKEND_AUTH_UNAVAILABLE = "AUTH_SESSION_UNAVAILABLE"
BACKEND_AUTH_INVALID = "AUTH_SESSION_INVALID"
BACKEND_REMOTE_FAILED = "REMOTE_BACKEND_FAILED"
BACKEND_METADATA_ONLY = "METADATA_ONLY"


def backend_failure_code(route: str, reason: str | None, *, authenticated: bool = False) -> str:
    """Classify a single tier's failure by backend, not just by media reason."""
    if authenticated and reason == REASON_BOT_CHECK:
        # Credentials were presented and still refused: the jar is stale or
        # wrong, which is a different action for the operator than "get a jar".
        return BACKEND_AUTH_INVALID
    if route in ("proxy", "alternate_route"):
        return BACKEND_PROXY_FAILED
    if route == "direct" and reason == REASON_BOT_CHECK:
        return BACKEND_DIRECT_BOT_CHECK
    return public_failure_code(reason)


class MusicPipelineError(Exception):
    """Carries the (stage, reason) pair alongside the message so callers can
    trace and branch on structure instead of re-parsing an error string."""

    def __init__(self, stage: str, reason: str, message: str = "", *, video_id: str | None = None):
        self.stage = stage
        self.reason = reason
        self.video_id = video_id
        super().__init__(message or f"{stage}/{reason}")

    def trace_fields(self) -> dict:
        fields = {"stage": self.stage, "reason": self.reason}
        if self.video_id:
            fields["video_id"] = self.video_id
        return fields
