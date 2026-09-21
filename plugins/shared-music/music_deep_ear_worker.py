"""Deep Ear worker — runs INSIDE the isolated `.shared-music-deepear-venv`
(torch-cpu + demucs + librosa), never inside the main hermes-agent process.
Invoked as a subprocess: `<deepear-venv>/bin/python3 music_deep_ear_worker.py
<request.json> <result.json>`. Reads a small JSON request, writes a JSON
result, and never talks to the network itself (the caller already resolved
and cached the source audio).

Why a subprocess worker instead of an in-process import: this plugin's main
code runs inside /opt/hermes/.venv (Python 3.13, the framework's own venv).
torch/demucs/librosa are NOT installed there and never will be — see
__init__.py's `.vendor` comment for why even lightweight deps stopped
surviving container recreation, and torch+demucs are two orders of magnitude
heavier than anything vendor-able that way. Isolating this in its own venv
means a broken/missing/OOM-killed worker can never take down the main
gateway process — the caller just gets a non-zero exit code or timeout and
degrades to "Deep Ear unavailable, Fast Ear still works" (task rule 34).

One worker invocation always computes the FULL deep-listening object for
the requested window in one pass (stems -> rhythm/harmony/melody/vocal/
production all derived from the same separation), even if the triggering
question only needed one category — Demucs dominates the cost and running
it once serves every question about that same window from cache afterward,
which is cheaper than re-separating per category.
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from music_deep_ear_schema import ANALYZER_VERSION, SCHEMA_VERSION  # noqa: E402

warnings.filterwarnings("ignore")

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import librosa  # noqa: E402

_MAJOR_TEMPLATE = np.array([1, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0], dtype=float)
_MINOR_TEMPLATE = np.array([1, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0], dtype=float)
_PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Krumhansl-Schmuckler key profiles, for a coarse major/minor tonal-center guess
_KS_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_KS_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def _separate(audio_path: str, out_dir: Path) -> dict[str, np.ndarray]:
    import torch
    from demucs.apply import apply_model
    from demucs.audio import AudioFile
    from demucs.pretrained import get_model

    model = get_model("htdemucs")
    model.eval()
    wav = AudioFile(audio_path).read(streams=0, samplerate=model.samplerate, channels=model.audio_channels)
    ref = wav.mean(0)
    wav = (wav - ref.mean()) / (ref.std() + 1e-8)
    with torch.no_grad():
        sources = apply_model(model, wav[None], device="cpu", progress=False)[0]
    sources = sources * ref.std() + ref.mean()
    names = model.sources  # ['drums', 'bass', 'other', 'vocals']
    return {name: sources[i].numpy() for i, name in enumerate(names)}, model.samplerate


def _to_mono(stem: np.ndarray) -> np.ndarray:
    return stem.mean(axis=0) if stem.ndim > 1 else stem


def _rms(y: np.ndarray) -> float:
    return float(np.sqrt(np.mean(y**2) + 1e-12))


def _stem_presence(stems: dict[str, np.ndarray]) -> dict[str, float]:
    energies = {name: _rms(_to_mono(y)) for name, y in stems.items()}
    total = sum(energies.values()) + 1e-9
    return {name: round(e / total, 3) for name, e in energies.items()}


def _scalar(x) -> float:
    """librosa (numba/numpy 2.x) sometimes returns a shape-(1,) array where a
    plain float is expected — plain float() on that raises TypeError since
    numpy 2.x, unlike older numpy which merely deprecated it."""
    return float(np.asarray(x).reshape(-1)[0])


def _rhythm(drums: np.ndarray, sr: int) -> dict:
    y = _to_mono(drums)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    tempo, beat_frames = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr)
    tempo = _scalar(tempo)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr)
    onsets = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, units="time")
    duration = len(y) / sr
    drum_density = round(len(onsets) / max(duration, 0.1), 2)

    syncopation = 0.0
    if len(beat_times) > 1 and len(onsets) > 0:
        beat_period = float(np.median(np.diff(beat_times))) if len(beat_times) > 1 else 0.5
        tol = beat_period * 0.12
        off_grid = 0
        for t in onsets:
            nearest_beat = beat_times[np.argmin(np.abs(beat_times - t))]
            if abs(t - nearest_beat) > tol:
                off_grid += 1
        syncopation = round(off_grid / max(len(onsets), 1), 2)

    half = len(y) // 2
    density_first = len(librosa.onset.onset_detect(y=y[:half], sr=sr, units="time")) if half > sr else 0
    density_second = len(librosa.onset.onset_detect(y=y[half:], sr=sr, units="time")) if half > sr else 0
    drum_density_trend = "steady"
    if density_first + density_second > 2:
        if density_second > density_first * 1.3:
            drum_density_trend = "rising"
        elif density_first > density_second * 1.3:
            drum_density_trend = "falling"

    return {
        "tempo_bpm": round(tempo, 1),
        "beat_count": int(len(beat_times)),
        "drum_density_per_s": drum_density,
        "drum_density_trend": drum_density_trend,
        "syncopation": syncopation,
        "confidence": round(min(1.0, len(beat_times) / max(duration, 1) / 3.0), 2),
    }


def _chroma_templates_score(chroma_vec: np.ndarray) -> tuple[str, float]:
    best_label, best_score = "N", -1.0
    norm = np.linalg.norm(chroma_vec) + 1e-9
    for shift in range(12):
        maj = np.roll(_MAJOR_TEMPLATE, shift)
        minr = np.roll(_MINOR_TEMPLATE, shift)
        maj_score = float(np.dot(chroma_vec, maj) / (norm * np.linalg.norm(maj)))
        min_score = float(np.dot(chroma_vec, minr) / (norm * np.linalg.norm(minr)))
        if maj_score > best_score:
            best_score, best_label = maj_score, f"{_PITCH_NAMES[shift]}maj"
        if min_score > best_score:
            best_score, best_label = min_score, f"{_PITCH_NAMES[shift]}min"
    return best_label, round(max(best_score, 0.0), 2)


def _harmony(other: np.ndarray, bass: np.ndarray, sr: int, beat_times: np.ndarray) -> dict:
    harmonic_mix = _to_mono(other) + _to_mono(bass)
    chroma = librosa.feature.chroma_cqt(y=harmonic_mix, sr=sr)
    if len(beat_times) >= 2:
        beat_frames = librosa.time_to_frames(beat_times, sr=sr, hop_length=512)
        chroma_sync = librosa.util.sync(chroma, beat_frames, aggregate=np.median)
    else:
        chroma_sync = chroma
    # smooth across a small beat window before classifying — raw beat-synced
    # chroma still flickers between adjacent templates every beat from vocal
    # bleed/passing bass noise even when the underlying harmony is stable;
    # a real chord holds for several beats, transient noise mostly doesn't.
    if chroma_sync.shape[1] >= 3:
        from scipy.ndimage import median_filter as _mf

        chroma_sync = _mf(chroma_sync, size=(1, 3))

    # librosa.util.sync's beat-aggregated frames can have one more column
    # than len(beat_times) (a trailing segment from the last beat to the end
    # of the signal) — anchor that segment's start at the LAST beat, not 0.0.
    last_beat = float(beat_times[-1]) if len(beat_times) else 0.0
    chords = []
    for i in range(chroma_sync.shape[1]):
        label, conf = _chroma_templates_score(chroma_sync[:, i])
        start = float(beat_times[i]) if i < len(beat_times) else last_beat
        end = float(beat_times[i + 1]) if i + 1 < len(beat_times) else start + 1.0
        chords.append({"start_s": round(start, 2), "end_s": round(end, 2), "label": label, "confidence": conf})

    # merge consecutive same-label spans; a lone single-beat span sandwiched
    # between two matching spans is almost always flicker, not a real chord —
    # fold it into its neighbor instead of keeping it as its own entry.
    merged: list[dict] = []
    for c in chords:
        if merged and merged[-1]["label"] == c["label"]:
            merged[-1]["end_s"] = c["end_s"]
            merged[-1]["confidence"] = round((merged[-1]["confidence"] + c["confidence"]) / 2, 2)
        elif merged and merged[-1]["end_s"] - merged[-1]["start_s"] < 0.35:
            merged[-1] = c  # previous span was too short to trust — replace, don't keep
        else:
            merged.append(dict(c))

    mean_chroma = chroma.mean(axis=1)
    mean_chroma = mean_chroma / (mean_chroma.sum() + 1e-9)
    key_scores = []
    for shift in range(12):
        maj_corr = float(np.corrcoef(np.roll(mean_chroma, -shift), _KS_MAJOR)[0, 1])
        min_corr = float(np.corrcoef(np.roll(mean_chroma, -shift), _KS_MINOR)[0, 1])
        key_scores.append((f"{_PITCH_NAMES[shift]} major", maj_corr))
        key_scores.append((f"{_PITCH_NAMES[shift]} minor", min_corr))
    key_scores.sort(key=lambda kv: kv[1], reverse=True)
    key_guess, key_conf = key_scores[0]

    tension_series = [c["confidence"] for c in merged]
    tension_trend = "steady"
    if len(tension_series) >= 4:
        first, last = np.mean(tension_series[: len(tension_series) // 2]), np.mean(tension_series[len(tension_series) // 2 :])
        if last < first - 0.1:
            tension_trend = "rising"  # lower template-match confidence -> more ambiguous/tense harmony
        elif last > first + 0.1:
            tension_trend = "falling"

    return {
        "key_guess": key_guess,
        "key_confidence": round(max(key_conf, 0.0), 2),
        "chord_sequence": merged[:24],  # cap so a long window still stays compact
        "tension_trend": tension_trend,
    }


def _melody(vocals: np.ndarray, sr: int) -> dict:
    y = _to_mono(vocals)
    if _rms(y) < 1e-4:
        return {"contour": "unavailable", "range_semitones": 0, "register": "unavailable", "repeated_motif": False, "confidence": 0.0}
    f0, voiced_flag, voiced_prob = librosa.pyin(
        y, fmin=librosa.note_to_hz("C2"), fmax=librosa.note_to_hz("C7"), sr=sr
    )
    voiced = f0[voiced_flag & ~np.isnan(f0)]
    if len(voiced) < 5:
        return {"contour": "unavailable", "range_semitones": 0, "register": "unavailable", "repeated_motif": False, "confidence": 0.0}

    third = max(1, len(voiced) // 3)
    first_third, last_third = np.mean(voiced[:third]), np.mean(voiced[-third:])
    ratio = last_third / (first_third + 1e-9)
    if ratio > 1.08:
        contour = "rising"
    elif ratio < 0.92:
        contour = "falling"
    else:
        peak_idx = int(np.argmax(voiced))
        contour = "arc" if 0 < peak_idx < len(voiced) - 1 else "flat"

    range_semitones = round(float(12 * np.log2(np.max(voiced) / np.min(voiced))), 1)
    median_hz = float(np.median(voiced))
    register = "low" if median_hz < 165 else ("high" if median_hz > 350 else "mid")

    autocorr = np.correlate(voiced - voiced.mean(), voiced - voiced.mean(), mode="full")
    mid = len(autocorr) // 2
    repeated_motif = bool(len(autocorr) > mid + 10 and np.max(autocorr[mid + 5 : mid + 30]) > 0.5 * autocorr[mid])

    return {
        "contour": contour,
        "range_semitones": range_semitones,
        "register": register,
        "repeated_motif": repeated_motif,
        "confidence": round(float(np.mean(voiced_prob[voiced_flag])) if voiced_flag.any() else 0.0, 2),
    }


def _vocal(vocals: np.ndarray, sr: int, presence: float, melody: dict) -> dict:
    y = _to_mono(vocals)
    if presence < 0.05:
        return {"presence": presence, "layering_detected": False, "dynamics_trend": "unavailable"}
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    frame_max = chroma.max(axis=0) + 1e-9
    active_bins = (chroma > 0.15 * frame_max).sum(axis=0)
    layering_detected = bool(np.mean(active_bins) > 2.4)  # heuristic, framed as such to the caller

    rms = librosa.feature.rms(y=y)[0]
    if len(rms) > 4:
        first, last = np.mean(rms[: len(rms) // 3]), np.mean(rms[-len(rms) // 3 :])
        dynamics_trend = "rising" if last > first * 1.15 else ("falling" if first > last * 1.15 else "steady")
    else:
        dynamics_trend = "steady"

    return {
        "presence": presence,
        "register": melody.get("register", "unavailable"),
        "layering_detected": layering_detected,
        "dynamics_trend": dynamics_trend,
    }


def _production(stereo_y: np.ndarray, sr: int) -> dict:
    if stereo_y.ndim < 2 or stereo_y.shape[0] < 2:
        return {"stereo_width": "unavailable (mono source)", "density": "unknown", "space_note": "unavailable"}
    left, right = stereo_y[0], stereo_y[1]
    n = min(len(left), len(right))
    frame = sr // 2
    widths = []
    for i in range(0, n - frame, frame):
        l, r = left[i : i + frame], right[i : i + frame]
        if np.std(l) < 1e-6 or np.std(r) < 1e-6:
            continue
        corr = float(np.corrcoef(l, r)[0, 1])
        widths.append(1 - abs(corr))
    width_val = float(np.mean(widths)) if widths else 0.0
    stereo_width = "narrow" if width_val < 0.15 else ("wide" if width_val > 0.4 else "moderate")

    mono = stereo_y.mean(axis=0)
    stft = np.abs(librosa.stft(mono, n_fft=2048))
    occupancy = float(np.mean(stft > (0.05 * stft.max())))
    density = "sparse" if occupancy < 0.15 else ("dense" if occupancy > 0.35 else "moderate")

    onset_frames = librosa.onset.onset_detect(y=mono, sr=sr, units="frames")
    decay_ratios = []
    hop = 512
    for f in onset_frames[:20]:
        t0, t1 = f * hop, f * hop + int(sr * 0.15)
        t2, t3 = t1, t1 + int(sr * 0.15)
        if t3 >= len(mono):
            continue
        e0 = _rms(mono[t0:t1])
        e1 = _rms(mono[t2:t3])
        if e0 > 1e-5:
            decay_ratios.append(e1 / e0)
    space_val = float(np.mean(decay_ratios)) if decay_ratios else 0.0
    space_note = "intimate_dry" if space_val < 0.3 else ("spacious_leaning" if space_val > 0.6 else "moderate_space")

    return {"stereo_width": stereo_width, "density": density, "space_note": space_note}


def run(request: dict) -> dict:
    audio_path = request["audio_path"]
    start_s = float(request.get("start_s", 0.0))
    end_s = float(request.get("end_s") or 0.0) or None

    info = sf.info(audio_path)
    sr_native = info.samplerate
    with sf.SoundFile(audio_path) as f:
        start_frame = int(start_s * sr_native)
        f.seek(max(0, start_frame))
        n_frames = int((end_s - start_s) * sr_native) if end_s else (info.frames - start_frame)
        stereo = f.read(max(1, n_frames), dtype="float64", always_2d=True).T  # (channels, samples)

    tmp_dir = Path(request.get("tmp_dir", "/tmp"))
    seg_path = tmp_dir / f"deepear_seg_{int(start_s)}_{int(end_s or 0)}.wav"
    sf.write(str(seg_path), stereo.T, sr_native)

    try:
        stems, sr = _separate(str(seg_path), tmp_dir)
    finally:
        seg_path.unlink(missing_ok=True)

    presence = _stem_presence(stems)
    rhythm = _rhythm(stems["drums"], sr)
    beat_times = np.array([])
    try:
        y_drums = _to_mono(stems["drums"])
        onset_env = librosa.onset.onset_strength(y=y_drums, sr=sr)
        _, beat_frames = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr)
    except Exception:
        pass
    harmony = _harmony(stems["other"], stems["bass"], sr, beat_times)
    melody = _melody(stems["vocals"], sr)
    vocal = _vocal(stems["vocals"], sr, presence.get("vocals", 0.0), melody)
    production = _production(stereo, sr_native)

    return {
        "schema_version": SCHEMA_VERSION,
        "analyzer_version": ANALYZER_VERSION,
        "window_s": [round(start_s, 1), round(start_s + stereo.shape[1] / sr_native, 1)],
        "instrumentation": {
            "sources": [
                {"label": "lead_vocal", "confidence": presence.get("vocals", 0.0)},
                {"label": "drums", "confidence": presence.get("drums", 0.0)},
                {"label": "bass", "confidence": presence.get("bass", 0.0)},
                {"label": "other_harmonic_accompaniment", "confidence": presence.get("other", 0.0),
                 "note": "guitar/piano/synth/strings not separated further by this model — texture only"},
            ],
        },
        "rhythm": rhythm,
        "harmony": harmony,
        "melody": melody,
        "vocal": vocal,
        "production": production,
    }


def main() -> None:
    request_path, result_path = sys.argv[1], sys.argv[2]
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    try:
        result = run(request)
    except Exception as e:  # noqa: BLE001
        result = {"error": f"{type(e).__name__}: {e}"}
    Path(result_path).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
