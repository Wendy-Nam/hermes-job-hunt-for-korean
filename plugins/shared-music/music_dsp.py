"""Local DSP feature extraction — the HTF-idea "sensory organ", reimplemented from
scratch (no code taken from the reference repo, which ships without a LICENSE file;
only the general idea of a time-based structured perceptual representation is reused,
per https://github.com/v3nommy/AI-Music-Listening-Experience). All numbers here are
private perceptual evidence for music_perception.py to compress — nothing in this
module is ever shown to a user directly.

Frame convention mirrors common practice (43 fps @ 22.05kHz): SR=22050, N_FFT=2048,
HOP=512, centered/reflect-padded frames, Hann window.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import soundfile as sf
from scipy.ndimage import median_filter
from scipy.signal import find_peaks, resample_poly

logger = logging.getLogger(__name__)

SR = 22050
N_FFT = 2048
HOP = 512
FRAME_RATE = SR / HOP  # ~43.07 frames/sec

ANALYZER_VERSION = "1.0.0"


@dataclass
class RawAnalysis:
    duration_s: float
    frame_times: np.ndarray
    rms: np.ndarray
    centroid_hz: np.ndarray
    flux: np.ndarray
    onset_env: np.ndarray
    chroma: np.ndarray  # (n_frames, 12), normalized per frame
    band_energy: np.ndarray  # (n_frames, 3) low/mid/high fraction
    tempo_bpm: float
    tempo_confidence: float
    energy_1hz: np.ndarray
    brightness_1hz: np.ndarray
    flux_1hz: np.ndarray
    onset_1hz: np.ndarray
    mean_chroma: np.ndarray  # (12,)
    tonal_stability: float  # 0-1, higher = harmonically steadier


def load_audio_mono(path: str) -> tuple[np.ndarray, int]:
    y, sr = sf.read(path, always_2d=False)
    if y.ndim > 1:
        y = y.mean(axis=1)
    y = y.astype(np.float64)
    if sr != SR:
        # polyphase resample keeps this stable across arbitrary source rates
        from math import gcd

        g = gcd(SR, sr)
        y = resample_poly(y, SR // g, sr // g)
        sr = SR
    return y, sr


def _stft_mag(y: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    window = np.hanning(N_FFT)
    pad = N_FFT // 2
    y_pad = np.pad(y, pad, mode="reflect")
    n_frames = 1 + (len(y_pad) - N_FFT) // HOP
    if n_frames < 1:
        raise ValueError("audio too short to analyze")
    frames = np.lib.stride_tricks.sliding_window_view(y_pad, N_FFT)[::HOP][:n_frames]
    windowed = frames * window
    spec = np.fft.rfft(windowed, axis=1)
    mag = np.abs(spec)
    freqs = np.fft.rfftfreq(N_FFT, d=1.0 / SR)
    frame_times = np.arange(n_frames) * (HOP / SR)
    return freqs, mag, frame_times


def _chroma_matrix(freqs: np.ndarray) -> np.ndarray:
    """(n_freq_bins, 12) weight matrix mapping each FFT bin to a pitch class."""
    mat = np.zeros((len(freqs), 12))
    for i, f in enumerate(freqs):
        if f < 40.0:  # skip DC/sub-audible — no stable pitch class there
            continue
        pitch_class = int(round(12 * np.log2(f / 440.0))) % 12
        mat[i, pitch_class] = 1.0
    return mat


def band_masks(freqs: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    low = freqs < 250.0
    mid = (freqs >= 250.0) & (freqs < 2000.0)
    high = freqs >= 2000.0
    return low, mid, high


def _aggregate_1hz(values: np.ndarray, frame_times: np.ndarray, duration_s: float) -> np.ndarray:
    n_sec = max(1, int(np.ceil(duration_s)))
    out = np.zeros(n_sec)
    sec_idx = np.clip(frame_times.astype(int), 0, n_sec - 1)
    counts = np.zeros(n_sec)
    np.add.at(out, sec_idx, values)
    np.add.at(counts, sec_idx, 1)
    counts[counts == 0] = 1
    return out / counts


def _estimate_tempo(onset_env: np.ndarray) -> tuple[float, float]:
    """Autocorrelation-based tempo estimate in the 50-200 BPM range."""
    if len(onset_env) < int(FRAME_RATE * 2):
        return 0.0, 0.0
    x = onset_env - onset_env.mean()
    if np.allclose(x, 0):
        return 0.0, 0.0
    autocorr = np.correlate(x, x, mode="full")[len(x) - 1 :]
    lag_min = int(FRAME_RATE * 60 / 200)  # 200 BPM upper bound
    lag_max = int(FRAME_RATE * 60 / 50)  # 50 BPM lower bound
    lag_max = min(lag_max, len(autocorr) - 1)
    if lag_max <= lag_min:
        return 0.0, 0.0
    window = autocorr[lag_min:lag_max]
    if window.size == 0 or window.max() <= 0:
        return 0.0, 0.0
    best_lag = lag_min + int(np.argmax(window))
    tempo = 60.0 * FRAME_RATE / best_lag
    confidence = float(window.max() / (autocorr[0] + 1e-9))
    return round(tempo, 1), round(min(confidence, 1.0), 3)


def analyze(path: str) -> RawAnalysis:
    y, sr = load_audio_mono(path)
    duration_s = len(y) / sr
    freqs, mag, frame_times = _stft_mag(y)

    rms = np.sqrt(np.mean(mag**2, axis=1) + 1e-12)
    centroid = np.sum(freqs[None, :] * mag, axis=1) / (np.sum(mag, axis=1) + 1e-9)

    diff = np.diff(mag, axis=0, prepend=mag[:1])
    flux = np.sqrt(np.sum(np.maximum(diff, 0) ** 2, axis=1))
    flux = flux / (flux.max() + 1e-9)

    onset_env = np.convolve(flux, np.ones(5) / 5, mode="same")

    chroma_map = _chroma_matrix(freqs)
    chroma_raw = mag @ chroma_map
    chroma = chroma_raw / (chroma_raw.sum(axis=1, keepdims=True) + 1e-9)
    mean_chroma = chroma.mean(axis=0)
    mean_chroma = mean_chroma / (mean_chroma.sum() + 1e-9)

    # tonal stability: mean cosine similarity between chroma frames ~2s apart
    step = max(1, int(FRAME_RATE * 2))
    if len(chroma) > step:
        a, b = chroma[:-step], chroma[step:]
        num = np.sum(a * b, axis=1)
        denom = np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1) + 1e-9
        tonal_stability = float(np.clip(np.mean(num / denom), 0.0, 1.0))
    else:
        tonal_stability = 0.5

    low_mask, mid_mask, high_mask = band_masks(freqs)
    band_totals = np.stack(
        [mag[:, low_mask].sum(axis=1), mag[:, mid_mask].sum(axis=1), mag[:, high_mask].sum(axis=1)],
        axis=1,
    )
    band_energy = band_totals / (band_totals.sum(axis=1, keepdims=True) + 1e-9)

    tempo_bpm, tempo_confidence = _estimate_tempo(onset_env)

    energy_1hz = _aggregate_1hz(rms, frame_times, duration_s)
    brightness_1hz = _aggregate_1hz(centroid, frame_times, duration_s)
    flux_1hz = _aggregate_1hz(flux, frame_times, duration_s)
    onset_1hz = _aggregate_1hz(onset_env, frame_times, duration_s)

    return RawAnalysis(
        duration_s=duration_s,
        frame_times=frame_times,
        rms=rms,
        centroid_hz=centroid,
        flux=flux,
        onset_env=onset_env,
        chroma=chroma,
        band_energy=band_energy,
        tempo_bpm=tempo_bpm,
        tempo_confidence=tempo_confidence,
        energy_1hz=energy_1hz,
        brightness_1hz=brightness_1hz,
        flux_1hz=flux_1hz,
        onset_1hz=onset_1hz,
        mean_chroma=mean_chroma,
        tonal_stability=tonal_stability,
    )


def adaptive_thresholds(energy_1hz: np.ndarray, brightness_1hz: np.ndarray) -> tuple[float, float, float, float]:
    """25th/75th percentile of the track's own data — same idea as HTF's
    percentile-adaptive thresholds, so tiering is relative to this track, not a
    fixed global loudness/brightness assumption that would misjudge a quiet song."""
    e_lo, e_hi = np.percentile(energy_1hz, [25, 75])
    b_lo, b_hi = np.percentile(brightness_1hz, [25, 75])
    return float(e_lo), float(e_hi), float(b_lo), float(b_hi)


def detect_phases(energy_1hz: np.ndarray, min_phase_s: int = 15, max_phases: int = 8) -> list[dict]:
    n = len(energy_1hz)
    if n < min_phase_s * 2:
        return [{"start": 0, "end": n, "mean_energy": float(np.mean(energy_1hz))}]

    win = max(5, min_phase_s // 2)
    kernel = np.ones(win) / win
    smoothed = np.convolve(energy_1hz, kernel, mode="same")
    deriv = np.abs(np.diff(smoothed, prepend=smoothed[:1]))
    prominence = max(deriv.std(), 1e-6)
    peaks, _ = find_peaks(deriv, distance=min_phase_s, prominence=prominence)
    boundaries = sorted(p for p in peaks if min_phase_s <= p <= n - min_phase_s)[: max_phases - 1]

    edges = [0] + boundaries + [n]
    phases = []
    for start, end in zip(edges, edges[1:]):
        phases.append({"start": int(start), "end": int(end), "mean_energy": float(np.mean(energy_1hz[start:end]))})
    return phases


def find_salient_events(
    onset_1hz: np.ndarray, flux_1hz: np.ndarray, min_gap_s: int = 8, top_k: int = 10
) -> list[dict]:
    events = []
    for arr, kind in ((onset_1hz, "onset_peak"), (flux_1hz, "texture_peak")):
        if len(arr) < min_gap_s:
            continue
        prominence = max(arr.std() * 0.8, 1e-6)
        peaks, props = find_peaks(arr, distance=min_gap_s, prominence=prominence)
        for p, prom in zip(peaks, props.get("prominences", np.zeros(len(peaks)))):
            events.append({"time_s": int(p), "kind": kind, "strength": float(prom)})
    events.sort(key=lambda e: e["strength"], reverse=True)

    # dedupe events within min_gap_s of each other, keep the stronger one
    kept: list[dict] = []
    for ev in events:
        if any(abs(ev["time_s"] - k["time_s"]) < min_gap_s for k in kept):
            continue
        kept.append(ev)
        if len(kept) >= top_k:
            break
    kept.sort(key=lambda e: e["time_s"])
    return kept


def harmonic_percussive_ratio(mag_window: np.ndarray) -> tuple[float, float]:
    """Fitzgerald-style median-filter HPSS on a (frames, freq_bins) magnitude
    window — a well-known public-domain separation technique, reimplemented
    directly with scipy (not derived from the reference repo, which doesn't
    do HPSS at all). Used only by the deep-dive path on a short requested
    window, never on the whole track by default."""
    if mag_window.shape[0] < 5 or mag_window.shape[1] < 5:
        return 0.5, 0.5
    # harmonic content is smooth ALONG TIME per frequency bin -> filter axis 0 (frames)
    harmonic = median_filter(mag_window, size=(min(17, mag_window.shape[0] // 2 * 2 + 1), 1))
    # percussive content is smooth ACROSS FREQUENCY per time frame -> filter axis 1 (bins)
    percussive = median_filter(mag_window, size=(1, min(17, mag_window.shape[1] // 2 * 2 + 1)))
    h_energy = float(harmonic.sum())
    p_energy = float(percussive.sum())
    total = h_energy + p_energy + 1e-9
    return h_energy / total, p_energy / total
