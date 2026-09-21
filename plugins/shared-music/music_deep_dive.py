"""Lazy Deep Analysis (task item 10): only runs when the compact perception
genuinely can't answer a question — never on every turn, never on the whole
track. Escalation is a re-analysis of just the requested window at a finer
resolution (smaller hop) plus HPSS-lite harmonic/percussive separation and
per-band energy — still 100% local DSP, zero additional API cost.

This does NOT identify instruments, chords, or melody — nothing in this
module claims those. It only produces richer *textural* evidence (band
balance, percussive vs harmonic weight, sub-second onset density) so the
model can describe a moment more confidently in perceptual terms ("타격감
있는 소리가 하나 더 겹쳐진다") without asserting what it structurally cannot
know from DSP alone. Everything in uncertain_or_unavailable stays uncertain
even after this escalation — that list is deliberately never cleared by
deep-dive results.
"""
from __future__ import annotations

import numpy as np
import soundfile as sf

import music_dsp as dsp

FINE_HOP = 256
FINE_N_FFT = 1024


def _band_label(band_energy_row: np.ndarray) -> str:
    low, mid, high = band_energy_row
    if low > 0.5:
        return "low_end_heavy"
    if high > 0.45:
        return "bright_top_heavy"
    if mid > 0.55:
        return "midrange_focused"
    return "balanced"


def deep_dive_window(audio_path: str, start_s: float, end_s: float) -> dict | None:
    try:
        with sf.SoundFile(audio_path) as f:
            sr = f.samplerate
            start_frame = int(start_s * sr)
            end_frame = int(end_s * sr)
            f.seek(max(0, start_frame))
            n_frames = max(0, end_frame - start_frame)
            if n_frames <= 0:
                return None
            y = f.read(n_frames, dtype="float64", always_2d=False)
    except Exception:
        return None
    if y is None or len(y) == 0:
        return None
    if y.ndim > 1:
        y = y.mean(axis=1)

    if sr != dsp.SR:
        from math import gcd

        from scipy.signal import resample_poly

        g = gcd(dsp.SR, sr)
        y = resample_poly(y, dsp.SR // g, sr // g)

    if len(y) < FINE_N_FFT:
        return None

    window = np.hanning(FINE_N_FFT)
    pad = FINE_N_FFT // 2
    y_pad = np.pad(y, pad, mode="reflect")
    n_frames = 1 + (len(y_pad) - FINE_N_FFT) // FINE_HOP
    if n_frames < 2:
        return None
    frames = np.lib.stride_tricks.sliding_window_view(y_pad, FINE_N_FFT)[::FINE_HOP][:n_frames]
    mag = np.abs(np.fft.rfft(frames * window, axis=1))
    freqs = np.fft.rfftfreq(FINE_N_FFT, d=1.0 / dsp.SR)

    low_mask, mid_mask, high_mask = dsp.band_masks(freqs)
    band_totals = np.array([mag[:, low_mask].sum(), mag[:, mid_mask].sum(), mag[:, high_mask].sum()])
    band_energy = band_totals / (band_totals.sum() + 1e-9)

    harmonic_ratio, percussive_ratio = dsp.harmonic_percussive_ratio(mag)

    diff = np.diff(mag, axis=0, prepend=mag[:1])
    flux = np.sqrt(np.sum(np.maximum(diff, 0) ** 2, axis=1))
    flux_norm = flux / (flux.max() + 1e-9)
    fine_onset_count = int(np.sum(flux_norm > 0.5))

    return {
        "window_s": [round(start_s, 1), round(end_s, 1)],
        "band_balance": _band_label(band_energy),
        "band_energy_fraction": {"low": round(float(band_energy[0]), 2), "mid": round(float(band_energy[1]), 2), "high": round(float(band_energy[2]), 2)},
        "harmonic_ratio": round(float(harmonic_ratio), 2),
        "percussive_ratio": round(float(percussive_ratio), 2),
        "fine_grain_onset_count": fine_onset_count,
        "texture_note": (
            "percussive_and_layered" if percussive_ratio > 0.55 and fine_onset_count > 3
            else "percussive_leaning" if percussive_ratio > 0.55
            else "sustained_harmonic" if harmonic_ratio > 0.6
            else "mixed_texture"
        ),
    }
