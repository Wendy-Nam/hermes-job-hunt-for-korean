"""Track-level cache — metadata, resolved audio identity, compact perception,
and the raw 1Hz index needed for window retrieval (task item 19). One JSON
file per canonical track under wiki/.shared-music/track/<canonical_id>.json —
same per-entity-JSON-file convention as tasks/story-tracker/interest-graph,
not a database, since this is personal-archive scale.

Versioned for invalidation (task item 19): a track's cached perception is
only reused when both perception_version and analyzer_version match the
current code's constants. A version bump forces one re-analysis per track on
next mention, not a bulk migration.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import music_dsp as dsp
import music_perception as mp
import music_track_identity as identity


def _track_dir(wiki_root: Path) -> Path:
    d = Path(wiki_root) / ".shared-music" / "track"
    d.mkdir(parents=True, exist_ok=True)
    return d


def track_cache_path(wiki_root: Path, canonical_id: str) -> Path:
    return _track_dir(wiki_root) / f"{canonical_id}.json"


def _atomic_write(path: Path, data: dict) -> None:
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def load(wiki_root: Path, canonical_id: str) -> dict | None:
    path = track_cache_path(wiki_root, canonical_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def is_current(cache: dict) -> bool:
    return (
        cache.get("perception_version") == mp.PERCEPTION_VERSION
        and cache.get("analyzer_version") == dsp.ANALYZER_VERSION
    )


def save_new_analysis(
    *,
    wiki_root: Path,
    canonical_id: str,
    artist: str,
    title: str,
    youtube_video_id: str | None,
    spotify_track_id: str | None,
    audio_source: str,
    perception: mp.MusicPerceptionObject,
    raw: dsp.RawAnalysis,
) -> dict:
    existing = load(wiki_root, canonical_id) or {}
    now = time.time()
    raw_index = {
        "duration_s": raw.duration_s,
        "energy_1hz": [round(float(x), 4) for x in raw.energy_1hz],
        "brightness_1hz": [round(float(x), 1) for x in raw.brightness_1hz],
        "flux_1hz": [round(float(x), 4) for x in raw.flux_1hz],
        "onset_1hz": [round(float(x), 4) for x in raw.onset_1hz],
        "tempo_bpm": raw.tempo_bpm,
        "tempo_confidence": raw.tempo_confidence,
    }
    data = {
        "canonical_id": canonical_id,
        "artist": artist,
        "title": title,
        "duration_s": round(raw.duration_s, 1),
        "youtube_video_id": youtube_video_id or existing.get("youtube_video_id"),
        "spotify_track_id": spotify_track_id or existing.get("spotify_track_id"),
        "audio_source": audio_source,
        "perception": perception.to_dict(),
        "perception_version": mp.PERCEPTION_VERSION,
        "analyzer_version": dsp.ANALYZER_VERSION,
        "raw_index": raw_index,
        "lyrics": existing.get("lyrics"),
        "first_heard": existing.get("first_heard", now),
        "last_discussed": now,
        "discussion_count": existing.get("discussion_count", 0),
    }
    _atomic_write(track_cache_path(wiki_root, canonical_id), data)
    return data


def touch_discussion(wiki_root: Path, canonical_id: str) -> None:
    cache = load(wiki_root, canonical_id)
    if not cache:
        return
    cache["last_discussed"] = time.time()
    cache["discussion_count"] = cache.get("discussion_count", 0) + 1
    _atomic_write(track_cache_path(wiki_root, canonical_id), cache)


def find_by_artist_title(wiki_root: Path, artist: str, title: str) -> dict | None:
    """Cross-source match with no duration available (the Spotify-resolution
    case) — scans existing track files' artist/title, fine at personal-
    archive scale (same justification every sibling plugin's retrieval
    module already uses)."""
    key = identity.artist_title_key(artist, title)
    d = _track_dir(wiki_root)
    for f in d.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if identity.artist_title_key(data.get("artist", ""), data.get("title", "")) == key:
            return data
    return None
