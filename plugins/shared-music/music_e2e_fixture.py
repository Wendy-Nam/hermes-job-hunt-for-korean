"""Real-audio fixtures for the shared-music end-to-end smoke.

Everything else in this plugin's test suite injects at a seam and asserts on
the shape of a dict. That proves the wiring; it proves nothing about whether
audio is actually decoded or whether perception is anything but an empty
shell — which is exactly the failure mode this fixture exists to rule out.

So the fixtures here are *real encoded audio files*, synthesised with numpy
and encoded with the same ffmpeg the download path relies on. A test that
consumes them exercises: ffmpeg decode -> soundfile read -> STFT -> phase
segmentation -> perception object. Nothing about that chain is stubbed.

The synthetic songs are deliberately *structured* (quiet intro, build, loud
chorus, drop, outro) rather than noise, because a flat noise buffer would
produce a technically-non-empty perception with no sections and no contrasts
— i.e. it would pass a shallow assertion while proving nothing.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 44100


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _tone(freq: float, dur_s: float, amp: float, sr: int = SR) -> np.ndarray:
    t = np.linspace(0, dur_s, int(sr * dur_s), endpoint=False)
    # a couple of harmonics so the spectral centroid actually moves with
    # "brightness" instead of sitting on a single bin
    wave = np.sin(2 * np.pi * freq * t)
    wave += 0.4 * np.sin(2 * np.pi * freq * 2 * t)
    wave += 0.2 * np.sin(2 * np.pi * freq * 3 * t)
    return amp * wave


def _beats(dur_s: float, bpm: float, amp: float, sr: int = SR) -> np.ndarray:
    """Percussive clicks at a fixed tempo so tempo estimation has something
    real to lock onto — a pure pad would yield tempo_confidence ~0 and the
    perception would honestly say 'no clear pulse', which is not what we want
    to be asserting on for the happy path."""
    out = np.zeros(int(sr * dur_s))
    period = int(sr * 60.0 / bpm)
    decay = np.exp(-np.linspace(0, 12, int(sr * 0.05)))
    click = decay * np.random.RandomState(0).randn(len(decay))
    for start in range(0, len(out) - len(click), period):
        out[start : start + len(click)] += click
    return amp * out


def structured_song(duration_s: float = 120.0, bpm: float = 120.0) -> np.ndarray:
    """quiet intro -> build -> loud bright chorus -> breakdown -> outro."""
    seg = duration_s / 5.0
    parts = [
        _tone(220.0, seg, 0.08) + _beats(seg, bpm, 0.02),
        _tone(277.0, seg, 0.25) + _beats(seg, bpm, 0.10),
        _tone(440.0, seg, 0.75) + _beats(seg, bpm, 0.35),
        _tone(233.0, seg, 0.18) + _beats(seg, bpm, 0.06),
        _tone(220.0, seg, 0.10) + _beats(seg, bpm, 0.03),
    ]
    y = np.concatenate(parts)
    return np.clip(y / (np.abs(y).max() + 1e-9) * 0.9, -1.0, 1.0)


def flat_tone(duration_s: float = 90.0) -> np.ndarray:
    """A deliberately featureless track — used to prove the pipeline reports
    'steady / no clear pulse' honestly instead of inventing an arc."""
    return _tone(330.0, duration_s, 0.5)


def write_wav(path: Path, samples: np.ndarray, sr: int = SR) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), samples, sr)
    return path


def encode_m4a(wav_path: Path, out_path: Path) -> Path:
    """Encode to a real compressed container, so the decode step under test is
    an actual ffmpeg transcode and not a wav-to-wav copy."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav_path), "-c:a", "aac", "-b:a", "128k", str(out_path)],
        check=True,
    )
    return out_path


def decode_to_wav(src_path: Path, out_path: Path) -> Path:
    """The real ffmpeg decode the yt-dlp postprocessor performs, invoked
    directly. Used by the E2E download stub so the transcode is genuinely
    exercised while only the *network* is replaced."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src_path), "-ac", "2", "-ar", "44100", str(out_path)],
        check=True,
    )
    return out_path


def make_encoded_fixture(tmp_dir: Path, name: str, samples: np.ndarray) -> Path:
    """Returns a path to a real .m4a of `samples`."""
    tmp_dir = Path(tmp_dir)
    wav = write_wav(tmp_dir / f"{name}.src.wav", samples)
    return encode_m4a(wav, tmp_dir / f"{name}.m4a")
