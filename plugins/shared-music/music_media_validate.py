"""Is this file actually usable audio? — the gate between transport and perception.

The pre-existing check was `produced.stat().st_size == 0`. That catches the
zero-byte case and nothing else. Everything below is a file that passes it and
is still not something Fast Ear can listen to:

* a truncated wav from a download killed mid-flight — the RIFF header declares
  more bytes than the file contains, and a decoder either errors or silently
  returns the fragment;
* an HTML error page or a JSON blob saved under a `.wav` name;
* a container in a codec the DSP stack cannot open;
* a 0.2-second stub where a 5-minute track was expected.

The last one matters more than it looks: the cached-audio check
(`cached_audio_path`) treats *any* non-empty file as a cache hit, so one
truncated download becomes a permanently poisoned cache entry that every later
turn happily "listens" to. Validating on write *and* on cache read closes that.

Probing is layered cheapest-first and every layer is optional:

    magic sniff (bytes, always available)
      -> ffprobe (present in the runtime image: /usr/bin/ffprobe)
        -> soundfile.info (present wherever music_dsp can run)

`probe` is injectable so the tests exercise the real decision logic against
synthesised outcomes rather than requiring ffmpeg and a real download.
"""
from __future__ import annotations

import json
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import music_failure as failure

# Shortest thing we are willing to call a track. Below this, whatever was
# downloaded is a stub, an ad bumper, or a fragment — not the song.
MIN_DURATION_S = 1.0
# A file smaller than this cannot hold a second of any audio codec, so it is
# not worth spawning a probe for.
MIN_BYTES = 2048
PROBE_TIMEOUT_S = 20
# How far a measured duration may drift from the metadata duration before we
# call it a different (or partial) recording. Generous: encoders disagree by a
# frame or two, and YouTube's reported duration is rounded to whole seconds.
DURATION_TOLERANCE_FRAC = 0.10
DURATION_TOLERANCE_MIN_S = 3.0

# Leading bytes -> content type. Only formats the DSP stack can actually open
# are listed as audio; anything else is identified so the failure can say
# *what* arrived instead of "decode failed".
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"RIFF", "audio/wav"),
    (b"fLaC", "audio/flac"),
    (b"OggS", "audio/ogg"),
    (b"ID3", "audio/mpeg"),
    (b"\xff\xfb", "audio/mpeg"),
    (b"\xff\xf3", "audio/mpeg"),
    (b"\xff\xf2", "audio/mpeg"),
    (b"\xff\xf1", "audio/aac"),
    (b"<!DOCTYPE", "text/html"),
    (b"<html", "text/html"),
    (b"{", "application/json"),
)
_AUDIO_TYPES = frozenset(
    {"audio/wav", "audio/flac", "audio/ogg", "audio/mpeg", "audio/aac", "audio/mp4"}
)


@dataclass(frozen=True)
class ValidationOutcome:
    """Why a file is or is not usable audio, in the shared failure vocabulary."""

    ok: bool
    content_type: str = ""
    bytes_len: int = 0
    duration_s: float | None = None
    stage: str = ""
    reason: str = ""
    detail: str = ""


def _ok(**kw) -> ValidationOutcome:
    return ValidationOutcome(ok=True, **kw)


def _bad(reason: str, detail: str, *, stage: str = failure.STAGE_DECODE, **kw) -> ValidationOutcome:
    return ValidationOutcome(ok=False, stage=stage, reason=reason, detail=detail, **kw)


def sniff_content_type(head: bytes) -> str:
    """Content type from leading bytes. Empty string when nothing matches."""
    for magic, ctype in _MAGIC:
        if head.startswith(magic):
            if ctype == "audio/wav" and len(head) >= 12 and head[8:12] != b"WAVE":
                continue  # RIFF, but not a WAVE payload (e.g. AVI)
            return ctype
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return "audio/mp4"
    return ""


def _declared_riff_length(head: bytes) -> int | None:
    """The total size a RIFF header claims, in bytes (header included)."""
    if len(head) < 8 or not head.startswith(b"RIFF"):
        return None
    (riff_size,) = struct.unpack("<I", head[4:8])
    return riff_size + 8


def ffprobe_duration(path: Path, *, timeout_s: int = PROBE_TIMEOUT_S) -> float | None:
    """Decoded duration in seconds, or None if ffprobe is absent or unhappy."""
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "json", str(path),
            ],
            capture_output=True, text=True, timeout=timeout_s, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        value = json.loads(proc.stdout)["format"]["duration"]
        return float(value)
    except (ValueError, KeyError, TypeError):
        return None


def soundfile_duration(path: Path) -> float | None:
    try:
        import soundfile as sf  # imported lazily: not present in every context
    except ImportError:
        return None
    try:
        info = sf.info(str(path))
        return float(info.frames) / float(info.samplerate) if info.samplerate else None
    except Exception:
        return None


def default_probe(path: Path) -> float | None:
    duration = ffprobe_duration(path)
    if duration is None:
        duration = soundfile_duration(path)
    return duration


def validate_audio_file(
    path: Path | str,
    *,
    expected_duration_s: float | None = None,
    probe: Callable[[Path], float | None] | None = None,
    min_bytes: int = MIN_BYTES,
    require_decode: bool = True,
) -> ValidationOutcome:
    """Decide whether `path` may be handed to a perception layer.

    `require_decode=False` is for the cheap re-check on a cache read, where the
    file already passed a full probe once on the way in and re-spawning ffprobe
    on every turn would cost more than it protects.
    """
    path = Path(path)
    if not path.exists():
        return _bad(failure.REASON_EMPTY_AUDIO, f"{path} does not exist",
                    stage=failure.STAGE_DOWNLOAD)

    size = path.stat().st_size
    if size == 0:
        return _bad(failure.REASON_EMPTY_AUDIO, "file is zero bytes",
                    stage=failure.STAGE_DOWNLOAD, bytes_len=0)
    if size < min_bytes:
        return _bad(
            failure.REASON_CORRUPT_MEDIA,
            f"only {size} bytes — too small to hold audio",
            stage=failure.STAGE_DOWNLOAD, bytes_len=size,
        )

    with path.open("rb") as fh:
        head = fh.read(64)
    content_type = sniff_content_type(head)

    if not content_type:
        return _bad(failure.REASON_MIME_MISMATCH,
                    "leading bytes match no known media container",
                    bytes_len=size)
    if content_type not in _AUDIO_TYPES:
        # An HTML error page or a JSON error body saved under an audio name.
        # This is the "200 OK is not audio" case, and it is a content problem,
        # not an egress problem — retrying over another route gets the same page.
        return _bad(failure.REASON_MIME_MISMATCH,
                    f"served {content_type}, not audio", bytes_len=size,
                    content_type=content_type)

    declared = _declared_riff_length(head)
    if declared is not None and size < declared:
        return _bad(
            failure.REASON_CORRUPT_MEDIA,
            f"truncated: header declares {declared} bytes, file has {size}",
            bytes_len=size, content_type=content_type,
        )

    duration = None
    if require_decode:
        probe_fn = probe or default_probe
        duration = probe_fn(path)
        if duration is None:
            return _bad(failure.REASON_CODEC_UNSUPPORTED,
                        "no decoder could open the file", bytes_len=size,
                        content_type=content_type)
        if duration < MIN_DURATION_S:
            return _bad(
                failure.REASON_CORRUPT_MEDIA,
                f"decoded duration {duration:.2f}s is below the {MIN_DURATION_S}s floor",
                bytes_len=size, content_type=content_type, duration_s=duration,
            )
        if expected_duration_s and expected_duration_s > 0:
            tolerance = max(
                DURATION_TOLERANCE_MIN_S, expected_duration_s * DURATION_TOLERANCE_FRAC
            )
            if abs(duration - expected_duration_s) > tolerance:
                # Short is the interesting direction (a partial download), but
                # a wildly *longer* file is just as wrong: it means the
                # alternate-source tier resolved a different recording.
                return _bad(
                    failure.REASON_CORRUPT_MEDIA,
                    f"decoded {duration:.1f}s against an expected "
                    f"{expected_duration_s:.1f}s (tolerance {tolerance:.1f}s)",
                    bytes_len=size, content_type=content_type, duration_s=duration,
                )

    return _ok(content_type=content_type, bytes_len=size, duration_s=duration)


__all__ = [
    "ValidationOutcome",
    "validate_audio_file",
    "sniff_content_type",
    "ffprobe_duration",
    "soundfile_duration",
    "default_probe",
    "MIN_DURATION_S",
    "MIN_BYTES",
    "DURATION_TOLERANCE_FRAC",
]
