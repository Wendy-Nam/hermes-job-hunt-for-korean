"""Evidence-first cross-archive retrieval (task item 18/23) — "예전에 이 노래
보내준 적 있지?" / "이번 주에 뭐 들었지?" / "내가 Radiohead 뭐 들어봤지?" style
recall when no music session is currently active. Same token-overlap-with-
threshold shape as youtube-archive's retrieval.py / webtoon-companion's
webtoon_retrieval.py, reimplemented standalone (small enough to own, and
those two modules' retrieval internals aren't a stable public contract).

Three distinct lookup shapes (task item 23), tried in order by the caller:
  1. `search_weekly_summary` — explicit "이번 주" phrasing, no track/artist
     name needed, answered from the current week's Weekly doc alone.
  2. `search_artist` — a known artist's name appears in the message with no
     specific track named, answered from that Artist doc if one exists
     (task item 19's threshold means not every artist has one).
  3. `search` — token-overlap-with-threshold Track match (original Phase 1
     recall, field names updated for Phase 2's Track frontmatter schema).

Scans Track/Weekly/Artist notes directly at personal-archive scale (a
handful to a few hundred files) — no vector DB, per task item 18's explicit
"새로운 벡터 DB를 무조건 추가하지 말고" instruction. Task item 24: this
module is only ever invoked from a gate the caller already applies (no
active session + enough query tokens) — it must never fire on ordinary
non-music conversation, so score thresholds stay deliberately conservative.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import yaml

import music_weekly

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+|[가-힣]+")
_MIN_QUERY_TOKENS = 2
DEFAULT_THRESHOLD = 0.22

_WEEKLY_SUMMARY_RE = re.compile(r"이번\s*주.{0,10}(뭐|무슨|어떤).{0,6}(들었|음악|노래|곡)")
_WEEKLY_TRACK_HEADER_RE = re.compile(r"^### \d{4}-\d{2}-\d{2} — (.+)$", re.MULTILINE)


def _tokens(text: str) -> set[str]:
    if not text:
        return set()
    return {t.lower() for t in _TOKEN_RE.findall(text) if len(t) > 1}


def _read_frontmatter(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return {}
    if not text.startswith("---\n"):
        return {}
    try:
        end = text.index("\n---", 4)
        return yaml.safe_load(text[4:end]) or {}
    except Exception:
        return {}


def _score(query_tokens: set[str], candidate_tokens: set[str]) -> float:
    if not query_tokens or not candidate_tokens:
        return 0.0
    overlap = query_tokens & candidate_tokens
    return len(overlap) / len(query_tokens)


def search(user_message: str, wiki_root: Path, *, threshold: float = DEFAULT_THRESHOLD) -> list[dict]:
    query_tokens = _tokens(user_message)
    if len(query_tokens) < _MIN_QUERY_TOKENS:
        return []

    tracks_dir = Path(wiki_root) / "Music" / "Tracks"
    if not tracks_dir.exists():
        return []

    results = []
    for path in tracks_dir.glob("*.md"):
        fm = _read_frontmatter(path)
        title = fm.get("title", "")
        artist = fm.get("artist", "")
        candidate_tokens = _tokens(f"{title} {artist}")
        score = _score(query_tokens, candidate_tokens)
        if score >= threshold:
            results.append(
                {
                    "canonical_id": fm.get("canonical_track_id"),
                    "title": title,
                    "artist": artist,
                    "times_discussed": fm.get("times_discussed"),
                    "last_heard": fm.get("last_heard"),
                    "score": round(score, 3),
                    "path": str(path),
                }
            )
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:5]


def search_weekly_summary(user_message: str, wiki_root: Path) -> list[str] | None:
    if not _WEEKLY_SUMMARY_RE.search(user_message or ""):
        return None
    path = music_weekly.weekly_doc_path(wiki_root, music_weekly.week_key(time.time()))
    if not path.exists():
        return None
    doc = path.read_text(encoding="utf-8")
    return _WEEKLY_TRACK_HEADER_RE.findall(doc) or None


def search_artist(user_message: str, wiki_root: Path) -> dict | None:
    artists_dir = Path(wiki_root) / "Music" / "Artists"
    if not artists_dir.exists():
        return None
    query_tokens = _tokens(user_message)
    if len(query_tokens) < 1:
        return None
    for path in artists_dir.glob("*.md"):
        artist_name = path.stem
        if _tokens(artist_name) & query_tokens:
            doc = path.read_text(encoding="utf-8")
            tracks = re.findall(r"^- \[\[Music/Tracks/(.+?)\]\]$", doc, re.MULTILINE)
            return {"artist": artist_name, "tracks": tracks, "path": str(path)}
    return None


def format_context_block(results: list[dict]) -> str:
    lines = ["<trusted_local_rules scope=\"music-recall\">",
              "아래는 예전에 같이 들었던 곡 중 지금 메시지와 관련 있어 보이는 것들이다. "
              "사용자가 그 곡을 다시 언급하는 것 같으면 자연스럽게 기억하는 티를 낼 것 — "
              "몰랐던 척하지 말 것. 관련 없어 보이면 그냥 무시할 것.",
              ]
    for r in results:
        lines.append(f"- {r['artist']} - {r['title']} (같이 들은 횟수: {r.get('times_discussed', '?')})")
    lines.append("</trusted_local_rules>")
    return "\n".join(lines)


def format_weekly_summary_block(tracks: list[str]) -> str:
    lines = [
        "<trusted_local_rules scope=\"music-recall-weekly\">",
        "이번 주에 같이 들은 곡 목록이다 — 자연스럽게 대답할 것, 목록을 그대로 나열하는 리포트 "
        "말투로 읽지 말 것.",
    ]
    lines.extend(f"- {t}" for t in tracks)
    lines.append("</trusted_local_rules>")
    return "\n".join(lines)


def format_artist_block(result: dict) -> str:
    lines = [
        "<trusted_local_rules scope=\"music-recall-artist\">",
        f"{result['artist']}의 곡 중 같이 들은 것들이다 — 자연스럽게 대답할 것.",
    ]
    lines.extend(f"- {t}" for t in result["tracks"])
    lines.append("</trusted_local_rules>")
    return "\n".join(lines)
