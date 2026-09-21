"""Track notes (task item 18) — one persistent, accumulated document per
canonical track, index/identity role only (not a transcript copy). Session-
per-conversation notes (Phase 1's `write_session_note`/`Music/Sessions/`)
are gone in Phase 2 — replaced by music_weekly.py's Weekly rollup, per task
items 14-17. Zero real production data existed under `Music/Sessions/` at
the time of this redesign (verified before touching anything, same
due-diligence convention every prior migration in this codebase's history
follows), so there was nothing to migrate.

Deliberately NOT reusing webtoon-companion's doc_sections.py marker system
across the plugin boundary — the klipy-catgif/reaction-director cross-import
already bit this codebase once with a hidden-coupling breakage (see
[[project_klipy_catgif_plugin_2026]]); this plugin's own section-marker
needs are small enough to own outright.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

import music_dsp as dsp
import music_perception as mp
from music_session import MusicConversationSession

_MARKER_RE = lambda name: re.compile(  # noqa: E731
    rf"<!-- MUSIC_BLOCK:{re.escape(name)}:START -->\n.*?<!-- MUSIC_BLOCK:{re.escape(name)}:END -->\n?",
    re.DOTALL,
)


def _slugify(text: str) -> str:
    text = re.sub(r"[\\/:*?\"<>|]", " ", text).strip()
    text = re.sub(r"\s+", " ", text)
    return text[:150] or "untitled"


def _tracks_dir(wiki_root: Path) -> Path:
    d = Path(wiki_root) / "Music" / "Tracks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def track_slug(artist: str, title: str) -> str:
    name = f"{artist} - {title}" if artist else title
    return _slugify(name)


def track_note_path(wiki_root: Path, artist: str, title: str) -> Path:
    return _tracks_dir(wiki_root) / f"{track_slug(artist, title)}.md"


def track_wikilink(artist: str, title: str) -> str:
    return f"[[Music/Tracks/{track_slug(artist, title)}]]"


def _render_block(name: str, content: str) -> str:
    return f"<!-- MUSIC_BLOCK:{name}:START -->\n{content.strip()}\n<!-- MUSIC_BLOCK:{name}:END -->\n"


def _upsert_block(doc: str, name: str, content: str, *, heading: str) -> str:
    block = _render_block(name, content)
    pattern = _MARKER_RE(name)
    if pattern.search(doc):
        return pattern.sub(block, doc)
    return doc.rstrip() + f"\n\n{heading}\n{block}"


def _dedup_append(existing_lines: list[str], new_lines: list[str]) -> list[str]:
    seen = set(existing_lines)
    out = list(existing_lines)
    for line in new_lines:
        if line not in seen:
            out.append(line)
            seen.add(line)
    return out


def _extract_block(doc: str, name: str) -> str:
    m = _MARKER_RE(name).search(doc)
    if not m:
        return ""
    inner = m.group(0)
    inner = inner.split(":START -->\n", 1)[-1].rsplit("\n<!-- MUSIC_BLOCK", 1)[0]
    return inner


def _existing_bullets(doc: str, name: str) -> list[str]:
    block = _extract_block(doc, name)
    return [line for line in block.splitlines() if line.strip().startswith("-")]


def _humanize_characteristics(perception: mp.MusicPerceptionObject) -> str:
    gi = perception.global_impression
    return (
        f"- 에너지 흐름: {gi.energy_shape} / 밀도 변화: {gi.density_shape} / 밝기 변화: {gi.brightness_shape}\n"
        f"- 리듬感: {gi.rhythmic_character}\n"
        f"- 화성적 안정성: {gi.harmonic_character}\n"
        f"- 구간 수: {len(perception.sections)}개 ({gi.overall_motion})"
    )


def _notable_moments_text(perception: mp.MusicPerceptionObject) -> str:
    if not perception.salient_moments:
        return "- (뚜렷한 salient moment 없음)"
    lines = []
    for m in perception.salient_moments:
        mm, ss = divmod(m.time_s, 60)
        lines.append(f"- {mm:02d}:{ss:02d} — {m.type} ({m.description}, confidence={m.confidence})")
    return "\n".join(lines)


def _iso(ts) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()


def upsert_track_note(
    *, wiki_root: Path, cache: dict, session: MusicConversationSession, is_first_session: bool, week: str,
) -> Path:
    """task item 18's redesigned schema/sections. `week` is the ISO week key
    (music_weekly.week_key) this session's finalize falls in, appended to
    `# 같이 이야기한 주` as a wikilink (deduped — re-finalizing the same
    week never adds a second identical backlink, task item 27)."""
    perception = mp.MusicPerceptionObject.from_dict(cache["perception"])
    path = track_note_path(wiki_root, cache["artist"], cache["title"])
    doc = path.read_text(encoding="utf-8") if path.exists() else ""

    frontmatter = {
        "title": cache["title"],
        "artist": cache["artist"],
        "canonical_track_id": cache["canonical_id"],
        "first_heard": _iso(cache.get("first_heard")),
        "last_heard": _iso(time.time()),
        "times_discussed": cache.get("discussion_count", 0) + 1,
        "youtube": cache.get("youtube_video_id"),
        "spotify": cache.get("spotify_track_id"),
        "analysis_version": f"perception={mp.PERCEPTION_VERSION},analyzer={dsp.ANALYZER_VERSION}",
    }
    fm_text = "---\n" + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False) + "---\n"

    body = doc.split("---\n", 2)[-1] if doc.startswith("---\n") else doc
    if not body.strip():
        body = f"# {cache['artist']} - {cache['title']}\n" if cache["artist"] else f"# {cache['title']}\n"

    if is_first_session:
        impression_text = session.initial_impression or "(첫 감상 기록 없음)"
    else:
        existing = _extract_block(body, "impression")
        date_label = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        later = f"\n\n**나중 감상 ({date_label})**\n{session.initial_impression}" if session.initial_impression else ""
        impression_text = (existing.strip() or "(첫 감상 기록 없음)") + later
    body = _upsert_block(body, "impression", impression_text, heading="# <AGENT_NAME>의 누적 감상")

    existing_user = _existing_bullets(body, "user_reactions")
    new_user = [f"- {o}" for o in session.user_opinions]
    body = _upsert_block(
        body, "user_reactions", "\n".join(_dedup_append(existing_user, new_user)) or "- (아직 없음)",
        heading="# <YOUR_NAME>의 반응",
    )

    body = _upsert_block(body, "moments", _notable_moments_text(perception), heading="# 중요한 구간")
    body = _upsert_block(body, "characteristics", _humanize_characteristics(perception), heading="# 음악적 특징")

    existing_weeks = _existing_bullets(body, "weeks")
    week_line = f"- [[Music/Weekly/{week}]]"
    body = _upsert_block(
        body, "weeks", "\n".join(_dedup_append(existing_weeks, [week_line])), heading="# 같이 이야기한 주",
    )

    path.write_text(fm_text + body, encoding="utf-8")
    return path


# --- Artist notes (task item 19) --------------------------------------------

ARTIST_DOC_THRESHOLD = 2  # a single track's artist doesn't get its own file — avoids file-count blowup


def _artists_dir(wiki_root: Path) -> Path:
    d = Path(wiki_root) / "Music" / "Artists"
    d.mkdir(parents=True, exist_ok=True)
    return d


def artist_note_path(wiki_root: Path, artist: str) -> Path:
    return _artists_dir(wiki_root) / f"{_slugify(artist)}.md"


def _read_frontmatter(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            return {}
        end = text.index("\n---", 4)
        return yaml.safe_load(text[4:end]) or {}
    except Exception:
        return {}


def tracks_for_artist(wiki_root: Path, artist: str) -> list[dict]:
    if not artist:
        return []
    out = []
    for path in _tracks_dir(wiki_root).glob("*.md"):
        fm = _read_frontmatter(path)
        if fm.get("artist") == artist:
            out.append(fm)
    return out


def maybe_upsert_artist_note(wiki_root: Path, artist: str) -> Path | None:
    """Only creates/updates an Artist note once >= ARTIST_DOC_THRESHOLD
    distinct tracks by this artist have been discussed — task item 19's
    explicit anti-file-blowup requirement. Regenerates the track list from
    a live scan every time (never trusted stale), same convention
    interest-graph's adjacency index and person-model's person_index use."""
    if not artist:
        return None
    tracks = tracks_for_artist(wiki_root, artist)
    if len(tracks) < ARTIST_DOC_THRESHOLD:
        return None

    path = artist_note_path(wiki_root, artist)
    track_links = "\n".join(f"- {track_wikilink(artist, t.get('title', ''))}" for t in tracks)
    doc = path.read_text(encoding="utf-8") if path.exists() else f"# {artist}\n"
    body = doc.split("# 같이 들은 곡", 1)[0].rstrip() if "# 같이 들은 곡" in doc else doc
    doc = (
        f"{body.rstrip()}\n\n"
        f"# 같이 들은 곡\n\n{track_links}\n\n"
        f"# 누적 인상\n\n(총 {len(tracks)}곡 함께 들음 — 곡별 감상은 각 Track 문서 참고)\n\n"
        f"# <YOUR_NAME> 반응 경향\n\n(아직 패턴을 단정할 만큼 근거가 쌓이지 않음 — 여러 곡에 걸친 반복 반응이 보이면 여기 추가)\n"
    )
    path.write_text(doc, encoding="utf-8")
    return path
