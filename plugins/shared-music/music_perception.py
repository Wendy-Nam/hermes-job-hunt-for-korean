"""RAW DSP -> COMPACT Music Perception Object.

This is the middle layer of the raw/compact/conversational split: takes the
numeric time-series from music_dsp.RawAnalysis and compresses it into a
small, structured, YAML-renderable object with no per-second numbers left in
it — sections, salient moments, contrasts, a handful of shape descriptors,
and an explicit uncertain_or_unavailable list. The LLM only ever sees this
object (or a narrower window slice of it via music_window_retrieval.py),
never the raw arrays.

Field values are internal vocabulary tags (e.g. "building", "sustained_peak"),
not prose — music_prompt.py's instructions tell the model never to echo these
tags verbatim, only to speak from the impression they produce.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

import music_dsp as dsp

PERCEPTION_VERSION = "1.0.0"

_ALWAYS_UNCERTAIN = [
    "exact_instrument_identity",
    "chord_voicings",
    "melody_transcription",
    "vocal_articulation_technique",
    "production_technique",
    "lyrics_semantics",
    "lyrics_content",
]

_ENERGY_TIER_LABELS = ("low", "medium", "high")


@dataclass
class TrackMeta:
    title: str
    artist: str
    duration_s: float


@dataclass
class Section:
    start_s: int
    end_s: int
    character: str
    energy_tier: str
    notable_changes: str


@dataclass
class SalientMoment:
    time_s: int
    type: str
    description: str
    confidence: str


@dataclass
class Contrast:
    before: str
    after: str
    description: str


@dataclass
class GlobalImpression:
    energy_shape: str
    density_shape: str
    brightness_shape: str
    rhythmic_character: str
    harmonic_character: str
    overall_motion: str


@dataclass
class MusicPerceptionObject:
    track: TrackMeta
    global_impression: GlobalImpression
    sections: list[Section]
    salient_moments: list[SalientMoment]
    contrasts: list[Contrast]
    recurring_patterns: list[str]
    uncertain_or_unavailable: list[str]
    perception_version: str = PERCEPTION_VERSION
    analyzer_version: str = dsp.ANALYZER_VERSION

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "MusicPerceptionObject":
        return MusicPerceptionObject(
            track=TrackMeta(**d["track"]),
            global_impression=GlobalImpression(**d["global_impression"]),
            sections=[Section(**s) for s in d["sections"]],
            salient_moments=[SalientMoment(**m) for m in d["salient_moments"]],
            contrasts=[Contrast(**c) for c in d["contrasts"]],
            recurring_patterns=list(d.get("recurring_patterns", [])),
            uncertain_or_unavailable=list(d.get("uncertain_or_unavailable", _ALWAYS_UNCERTAIN)),
            perception_version=d.get("perception_version", PERCEPTION_VERSION),
            analyzer_version=d.get("analyzer_version", dsp.ANALYZER_VERSION),
        )


def _energy_tier(value: float, e_lo: float, e_hi: float) -> str:
    if value <= e_lo:
        return "low"
    if value >= e_hi:
        return "high"
    return "medium"


def _tier_sequence(phases: list[dict], e_lo: float, e_hi: float) -> list[str]:
    return [_energy_tier(p["mean_energy"], e_lo, e_hi) for p in phases]


def _section_character(idx: int, n: int, tiers: list[str], means: list[float]) -> str:
    tier = tiers[idx]
    if idx == 0:
        return "sparse_opening" if tier == "low" else ("full_opening" if tier == "high" else "steady_opening")
    if idx == n - 1:
        return "quiet_outro" if tier == "low" else ("sustained_outro" if tier == "high" else "settling_outro")
    prev = means[idx - 1]
    cur = means[idx]
    nxt = means[idx + 1] if idx + 1 < n else cur
    if cur > prev and (idx == n - 1 or cur >= nxt):
        return "building_to_peak" if tier != "high" else "sustained_peak"
    if cur < prev and cur <= nxt:
        return "receding"
    if tier == "high":
        return "sustained_peak"
    if tier == "low":
        return "restrained"
    return "steady_middle"


def _energy_shape(means: list[float]) -> str:
    """Continuous trend over phase means, not the discretized tier labels —
    with only a handful of phases, tier quantization alone can collapse a
    real arc (quiet -> loud -> quiet) into "all medium" and lose the shape."""
    if len(means) < 2:
        return "steady"
    spread = (max(means) - min(means)) / (np.mean(means) + 1e-9)
    if spread < 0.15:
        return "steady"
    if all(b >= a - 1e-9 for a, b in zip(means, means[1:])):
        return "building"
    if all(b <= a + 1e-9 for a, b in zip(means, means[1:])):
        return "receding"
    peak_idx = int(np.argmax(means))
    if 0 < peak_idx < len(means) - 1:
        return "arc_up_down"
    return "dynamic"


def _trend_shape(series_1hz: np.ndarray) -> str:
    if len(series_1hz) < 6:
        return "steady"
    first_third = np.mean(series_1hz[: len(series_1hz) // 3])
    last_third = np.mean(series_1hz[-len(series_1hz) // 3 :])
    spread = np.std(series_1hz) + 1e-9
    delta = (last_third - first_third) / spread
    if delta > 0.5:
        return "thickening"
    if delta < -0.5:
        return "thinning"
    if np.std(series_1hz) / (np.mean(series_1hz) + 1e-9) > 0.6:
        return "varied"
    return "steady"


def _rhythmic_character(tempo_bpm: float, tempo_confidence: float) -> str:
    if tempo_confidence < 0.15:
        return "no_clear_steady_pulse"
    if tempo_bpm < 90:
        return "slow_steady_pulse"
    if tempo_bpm < 120:
        return "mid_tempo_steady_pulse"
    if tempo_bpm < 150:
        return "upbeat_driving_pulse"
    return "fast_propulsive_pulse"


def _harmonic_character(tonal_stability: float) -> str:
    if tonal_stability > 0.75:
        return "tonally_stable"
    if tonal_stability > 0.5:
        return "mostly_stable_some_drift"
    return "harmonically_restless"


def _recurring_patterns(tiers: list[str]) -> list[str]:
    if len(tiers) < 4:
        return []
    pairs = list(zip(tiers, tiers[1:]))
    seen: dict[tuple, int] = {}
    for p in pairs:
        seen[p] = seen.get(p, 0) + 1
    repeats = [(p, c) for p, c in seen.items() if c >= 2 and p[0] != p[1]]
    if not repeats:
        return []
    return [f"alternating_{a}_{b}_pattern (~{c}x)" for (a, b), c in repeats]


def _event_description(kind: str, delta_sign: int) -> str:
    if kind == "onset_peak":
        return "sudden_impact" if delta_sign >= 0 else "sudden_drop_out"
    return "texture_thickens" if delta_sign >= 0 else "texture_thins"


def build_perception(
    *, title: str, artist: str, raw: dsp.RawAnalysis, phases: list[dict], events: list[dict]
) -> MusicPerceptionObject:
    e_lo, e_hi, b_lo, b_hi = dsp.adaptive_thresholds(raw.energy_1hz, raw.brightness_1hz)
    tiers = _tier_sequence(phases, e_lo, e_hi)
    means = [p["mean_energy"] for p in phases]
    n = len(phases)

    sections = []
    for i, p in enumerate(phases):
        character = _section_character(i, n, tiers, means)
        if i == 0:
            notable = "opens the track"
        else:
            prev_tier = tiers[i - 1]
            notable = "energy steps up" if tiers[i] != prev_tier and means[i] > means[i - 1] else (
                "energy drops back" if means[i] < means[i - 1] else "continues at a similar level"
            )
        sections.append(
            Section(
                start_s=p["start"],
                end_s=p["end"],
                character=character,
                energy_tier=tiers[i],
                notable_changes=notable,
            )
        )

    salient_moments = []
    for ev in events:
        t = ev["time_s"]
        idx = min(t, len(raw.energy_1hz) - 1)
        prev_idx = max(0, idx - 2)
        delta = raw.energy_1hz[idx] - raw.energy_1hz[prev_idx]
        desc = _event_description(ev["kind"], 1 if delta >= 0 else -1)
        confidence = "high" if ev["strength"] > 0.15 else "medium"
        moment_type = "energy_change" if ev["kind"] == "onset_peak" else "texture_change"
        salient_moments.append(SalientMoment(time_s=t, type=moment_type, description=desc, confidence=confidence))

    mean_of_means = float(np.mean(means)) + 1e-9
    contrasts = []
    for i in range(1, n):
        rel_delta = (means[i] - means[i - 1]) / mean_of_means
        if abs(rel_delta) >= 0.2 or tiers[i] != tiers[i - 1]:
            direction = "up" if rel_delta > 0 else "down"
            contrasts.append(
                Contrast(
                    before=sections[i - 1].character,
                    after=sections[i].character,
                    description=f"energy_shift_{direction}",
                )
            )

    energy_shape = _energy_shape(means)
    global_impression = GlobalImpression(
        energy_shape=energy_shape,
        density_shape=_trend_shape(raw.flux_1hz),
        brightness_shape=_trend_shape(raw.brightness_1hz),
        rhythmic_character=_rhythmic_character(raw.tempo_bpm, raw.tempo_confidence),
        harmonic_character=_harmonic_character(raw.tonal_stability),
        overall_motion=f"{n}_section_{energy_shape}_arc",
    )

    uncertain = list(_ALWAYS_UNCERTAIN)
    if raw.tempo_confidence < 0.15:
        uncertain.append("reliable_tempo_estimate")

    return MusicPerceptionObject(
        track=TrackMeta(title=title, artist=artist, duration_s=round(raw.duration_s, 1)),
        global_impression=global_impression,
        sections=sections,
        salient_moments=salient_moments,
        contrasts=contrasts,
        recurring_patterns=_recurring_patterns(tiers),
        uncertain_or_unavailable=uncertain,
    )
