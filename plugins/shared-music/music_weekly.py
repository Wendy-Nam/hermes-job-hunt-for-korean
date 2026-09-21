"""Weekly Music Document (task items 14-17) — the primary user-facing music
archive, replacing Phase 1's one-file-per-conversation Session note. One
conversation about a track no longer gets its own file; the whole week's
music talk accumulates in `Music/Weekly/<ISO-year>-W<week>.md`, keyed off
ISO 8601 week (Monday start) computed from Asia/Seoul local time — a UTC-
anchored week boundary would roll over hours early/late for a KST user,
exactly the bug task item 25 asks to guard against.

Machine state (session store, Fast Ear track cache, Deep Ear cache) still
lives under wiki/.shared-music/ — same dot-prefixed convention every sibling
plugin already uses (tasks/.tasks/, interest-graph/.interest-graph/, etc.),
confirmed compatible with plugin_health_check.py's dot-prefix-skip rule.
Task item 13 suggests a `Music/_data/` folder for exactly this purpose, but
that would make shared-music the only plugin in the codebase NOT following
the established dot-folder convention — deliberately not created; nothing
needs it since `.shared-music/` already serves the same role consistently
with every other plugin.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_KST = ZoneInfo("Asia/Seoul")


def week_key(ts: float) -> str:
    """ISO 8601 week (Monday start), computed from Asia/Seoul local time."""
    local = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(_KST)
    iso_year, iso_week, _ = local.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def week_date_range_label(ts: float) -> str:
    local = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(_KST)
    iso_year, iso_week, iso_weekday = local.isocalendar()
    monday = local.date().fromisocalendar(iso_year, iso_week, 1)
    sunday = local.date().fromisocalendar(iso_year, iso_week, 7)
    return f"{monday.isoformat()} ~ {sunday.isoformat()}"


def _weekly_dir(wiki_root: Path) -> Path:
    d = Path(wiki_root) / "Music" / "Weekly"
    d.mkdir(parents=True, exist_ok=True)
    return d


def weekly_doc_path(wiki_root: Path, week: str) -> Path:
    return _weekly_dir(wiki_root) / f"{week}.md"


def _header(week: str, ts: float) -> str:
    return f"# 🎧 같이 들은 음악 — {week.replace('-', ' ')}\n\n{week_date_range_label(ts)}\n\n## 이번 주 음악\n"


_TRACK_BLOCK_RE = lambda cid: re.compile(  # noqa: E731
    rf"<!-- WEEKLY_TRACK:{re.escape(cid)}:START -->\n.*?<!-- WEEKLY_TRACK:{re.escape(cid)}:END -->\n?", re.DOTALL
)


def _render_first_listen_block(*, canonical_id: str, date_label: str, artist: str, title: str, source_url: str,
                                agent_first: str, user_first: str | None) -> str:
    user_part = f"\n<YOUR_NAME>:\n{user_first}\n" if user_first else ""
    header = f"### {date_label} — {artist} — {title}" if artist else f"### {date_label} — {title}"
    body = (
        f"{header}\n\n"
        f"링크:\n{source_url}\n\n"
        f"#### 처음 들었을 때\n\n"
        f"<AGENT_NAME>:\n{agent_first}\n"
        f"{user_part}\n"
        f"#### 같이 얘기한 것\n\n"
        f"(대화가 이어지는 동안 자동으로 채워짐)\n\n"
        f"#### 좋았던 부분\n\n<YOUR_NAME>\n- (아직 없음)\n\n<AGENT_NAME>\n- (아직 없음)\n\n"
        f"#### 나중에 더 얘기한 것\n\n(없음)\n"
    )
    return f"<!-- WEEKLY_TRACK:{canonical_id}:START -->\n{body.strip()}\n<!-- WEEKLY_TRACK:{canonical_id}:END -->\n"


def _extract_track_block(doc: str, canonical_id: str) -> str | None:
    m = _TRACK_BLOCK_RE(canonical_id).search(doc)
    return m.group(0) if m else None


def _replace_section(block_text: str, heading: str, new_body: str) -> str:
    """Replace the content of one `#### heading` subsection inside a track
    block, up to the next `####`/`<!--` boundary, leaving everything else
    (and the outer WEEKLY_TRACK markers) untouched."""
    pattern = re.compile(
        rf"(#### {re.escape(heading)}\n\n).*?(?=\n#### |\n<!-- WEEKLY_TRACK)", re.DOTALL
    )
    if pattern.search(block_text):
        return pattern.sub(lambda m: m.group(1) + new_body.strip() + "\n", block_text, count=1)
    return block_text  # heading not found — leave block as-is rather than corrupt it


def upsert_first_listen(
    *, wiki_root: Path, ts: float, canonical_id: str, artist: str, title: str, source_url: str,
    agent_first: str, user_first: str | None = None,
) -> Path:
    """Called once, when a track is first discussed in the current week —
    creates the weekly doc if this is the week's first entry, and adds a new
    per-track block if this exact track hasn't been discussed yet this week
    (idempotent: calling again for the same (week, canonical_id) is a no-op,
    task item 27's duplicate-finalization guard)."""
    week = week_key(ts)
    path = weekly_doc_path(wiki_root, week)
    doc = path.read_text(encoding="utf-8") if path.exists() else _header(week, ts)

    if _extract_track_block(doc, canonical_id):
        return path  # already has an entry this week — never duplicate

    date_label = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(_KST).strftime("%Y-%m-%d")
    block = _render_first_listen_block(
        canonical_id=canonical_id, date_label=date_label, artist=artist, title=title,
        source_url=source_url, agent_first=agent_first, user_first=user_first,
    )
    doc = doc.rstrip() + "\n\n" + block + "\n---\n"
    path.write_text(doc, encoding="utf-8")
    return path


def append_conversation_highlights(*, wiki_root: Path, ts: float, canonical_id: str, lines: list[str]) -> None:
    """Fills in `#### 같이 얘기한 것` for this week's entry with real
    exchange lines (task item 21 — natural prose, not a mechanical
    summary — the caller passes through actual quoted turns, this function
    only places them)."""
    if not lines:
        return
    week = week_key(ts)
    path = weekly_doc_path(wiki_root, week)
    if not path.exists():
        return
    doc = path.read_text(encoding="utf-8")
    block = _extract_track_block(doc, canonical_id)
    if not block:
        return
    new_block = _replace_section(block, "같이 얘기한 것", "\n".join(lines))
    doc = _TRACK_BLOCK_RE(canonical_id).sub(new_block if new_block.endswith("\n") else new_block + "\n", doc)
    path.write_text(doc, encoding="utf-8")


def _existing_moment_bullets(block: str, side: str) -> list[str]:
    """side is "<YOUR_NAME>" or "<AGENT_NAME>" — pulls that side's existing bullets out of
    the `#### 좋았던 부분` subsection so a repeat same-week update merges
    instead of clobbering an earlier session's moments in the same week."""
    m = re.search(rf"{re.escape(side)}\n((?:- .*\n?)*)", block)
    if not m:
        return []
    return [line.strip() for line in m.group(1).splitlines() if line.strip().startswith("-") and line.strip() != "- (아직 없음)"]


def update_favorite_moments(
    *, wiki_root: Path, ts: float, canonical_id: str, user_moments: list[str], agent_moments: list[str],
) -> None:
    week = week_key(ts)
    path = weekly_doc_path(wiki_root, week)
    if not path.exists():
        return
    doc = path.read_text(encoding="utf-8")
    block = _extract_track_block(doc, canonical_id)
    if not block:
        return
    existing_user = _existing_moment_bullets(block, "<YOUR_NAME>")
    existing_agent = _existing_moment_bullets(block, "<AGENT_NAME>")
    merged_user = existing_user + [f"- {m}" for m in user_moments if f"- {m}" not in existing_user]
    merged_agent = existing_agent + [f"- {m}" for m in agent_moments if f"- {m}" not in existing_agent]
    user_text = "\n".join(merged_user) or "- (아직 없음)"
    agent_text = "\n".join(merged_agent) or "- (아직 없음)"
    new_block = _replace_section(block, "좋았던 부분", f"<YOUR_NAME>\n{user_text}\n\n<AGENT_NAME>\n{agent_text}")
    doc = _TRACK_BLOCK_RE(canonical_id).sub(new_block if new_block.endswith("\n") else new_block + "\n", doc)
    path.write_text(doc, encoding="utf-8")


def append_later_conversation(*, wiki_root: Path, ts: float, canonical_id: str, prose: str) -> None:
    """Same track discussed again LATER THE SAME WEEK (task item 16) — folds
    into `#### 나중에 더 얘기한 것` instead of creating a new entry."""
    week = week_key(ts)
    path = weekly_doc_path(wiki_root, week)
    if not path.exists():
        return
    doc = path.read_text(encoding="utf-8")
    block = _extract_track_block(doc, canonical_id)
    if not block:
        return
    existing = re.search(r"#### 나중에 더 얘기한 것\n\n(.*?)(?=\n<!-- WEEKLY_TRACK)", block, re.DOTALL)
    prior = existing.group(1).strip() if existing else ""
    prior = "" if prior in ("(없음)", "") else prior
    combined = (prior + "\n\n" if prior else "") + prose.strip()
    new_block = _replace_section(block, "나중에 더 얘기한 것", combined)
    doc = _TRACK_BLOCK_RE(canonical_id).sub(new_block if new_block.endswith("\n") else new_block + "\n", doc)
    path.write_text(doc, encoding="utf-8")


def has_entry_this_week(wiki_root: Path, ts: float, canonical_id: str) -> bool:
    path = weekly_doc_path(wiki_root, week_key(ts))
    if not path.exists():
        return False
    return _extract_track_block(path.read_text(encoding="utf-8"), canonical_id) is not None
