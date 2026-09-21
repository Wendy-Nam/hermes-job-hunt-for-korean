"""MusicConversationSession (task item 8) — keeps the perception + everything
said about one track alive across a whole conversation, not just the first
turn. Store convention mirrors youtube-archive's SessionStore (per-conversation
active-session JSON, finalize-on-explicit-termination) plus memory-core's raw
archive closure rules (idle timeout + turn cap as *additional* finalize
triggers — no core "topic ended" hook exists to hang off of, confirmed by
reading hermes_cli/plugins.py's VALID_HOOKS before building this).

Session *membership* — which turns actually belong in `exchange_log` — is not
decided here. It lives in `plugins/_shared/hermes_media_membership.py`, shared
with youtube-archive, because the defect it fixes was identical in both: an
open session transcribed every turn in the conversation, so model-switch and
gateway-restart talk was published as quoted dialogue about a song. This module
owns durability and the phase field; that module owns the verdict.

Finalize triggers, in the order checked:
  1. a per-turn termination signal from the membership machine (explicit end,
     control-plane command, dev command, new media item, topic drift)
  2. a *different* track link arrives while a session is active (task's own
     "함께 듣는 음악" flow naturally moves song to song)
  3. idle timeout (no turns in IDLE_TIMEOUT_S) — swept across *every* active
     session at the start of the next pre_llm_call, not just the current
     conversation's, so a session orphaned by a process restart or by a
     conversation that simply went quiet cannot stay open forever
  4. a hard turn cap, so a runaway session can't grow unbounded
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

_SHARED_DIR = str(Path(__file__).resolve().parent.parent / "_shared")
if _SHARED_DIR not in sys.path:
    sys.path.insert(0, _SHARED_DIR)

from hermes_media_membership import (  # noqa: E402
    ARCHIVE_PENDING_TIMEOUT_S,
    DRIFT_LIMIT,
    IDLE_TIMEOUT_S,
    MAX_TURNS,
    PHASE_ACTIVE,
    PHASE_ARCHIVE_PENDING,
    PHASE_FINALIZED,
    PHASE_STARTING,
    SessionView,
)
import hermes_media_membership as membership  # noqa: E402

__all__ = [
    "IDLE_TIMEOUT_S", "MAX_TURNS", "DRIFT_LIMIT", "ARCHIVE_PENDING_TIMEOUT_S",
    "PHASE_ACTIVE", "PHASE_ARCHIVE_PENDING", "PHASE_FINALIZED", "PHASE_STARTING",
    "MusicConversationSession", "SessionStore", "is_explicit_termination",
]

# The incumbent regex required a media anchor (노래|음악|이 곡|이거) within 12
# characters of the stop verb, so "됐고", "다른 얘기 하자" and a bare "그만하자"
# — the forms the user actually types — were not terminations at all. Kept as a
# thin alias so existing callers and tests keep working, but the pattern is now
# the shared one.
def is_explicit_termination(text: str) -> bool:
    if not text:
        return False
    return bool(membership._EXPLICIT_END_RE.search(membership._nfc(text)))


@dataclass
class MusicConversationSession:
    conversation_id: str
    canonical_id: str
    title: str
    artist: str
    video_id: str | None
    spotify_id: str | None
    initial_impression: str = ""
    discussed_topics: list[str] = field(default_factory=list)
    user_opinions: list[str] = field(default_factory=list)
    agent_opinions: list[str] = field(default_factory=list)
    shared_observations: list[str] = field(default_factory=list)
    exchange_log: list[dict] = field(default_factory=list)
    last_focus_section_idx: int | None = None
    started_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)
    turn_count: int = 0
    platform: str = ""
    trigger_excerpt: str = ""
    source_url: str = ""
    # Explicit lifecycle. Sessions written before this field existed load as
    # ACTIVE, which is what "the file was in active/" always meant.
    phase: str = PHASE_ACTIVE
    drift_count: int = 0
    # Membership decisions, newest last, capped. Kept on the session rather
    # than only in the log so "why is this turn in (or out of) the note?" is
    # answerable from the archived session itself.
    membership_log: list[dict] = field(default_factory=list)
    # Archive-decision bookkeeping. Set only while the transcript is closed and
    # the filing question is outstanding; see `SessionStore.close_transcript`.
    archive_pending_since: float = 0.0
    archive_pending_reason: str = ""
    archive_destination: str = ""
    archive_declined: bool = False
    # Why this share entered the music flow — the routing decision that
    # triggered it, so "why is this on the music shelf?" is answerable from
    # the session file alone rather than by replaying the turn.
    route_category: str = ""
    route_subtype: str = ""
    route_confidence: float = 0.0
    route_provenance: str = ""
    route_reason_code: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "MusicConversationSession":
        # Unknown keys are dropped rather than raising: a session written by a
        # newer build must not make this one treat a live session as corrupt
        # (the except-path deletes state).
        fields = set(MusicConversationSession.__dataclass_fields__)
        return MusicConversationSession(**{k: v for k, v in d.items() if k in fields})

    def view(self) -> SessionView:
        # `canonical_id` lives in music's own identity namespace; a turn's link
        # detector produces `youtube:<id>` / `spotify:<id>`. Both denote this
        # track, so both are handed over — otherwise resending the same link
        # would look like a different item and close the session.
        alt = tuple(
            v for v in (
                f"youtube:{self.video_id}" if self.video_id else "",
                f"spotify:{self.spotify_id}" if self.spotify_id else "",
            ) if v
        )
        return SessionView(
            media_id=self.canonical_id,
            alt_ids=alt,
            phase=self.phase,
            title=self.title,
            artist=self.artist,
            last_active_at_s=self.last_active_at,
            turn_count=self.turn_count,
            drift_count=self.drift_count,
        )


def _active_dir(wiki_root: Path) -> Path:
    d = Path(wiki_root) / ".shared-music" / "session" / "active"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _closed_dir(wiki_root: Path) -> Path:
    d = Path(wiki_root) / ".shared-music" / "session" / "closed"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _pending_dir(wiki_root: Path) -> Path:
    """Sessions whose transcript is closed but whose filing is unresolved.

    A separate directory rather than a flag inside `active/`, so that every
    reader of `active/` — including `all_active()` and any future one — is
    structurally incapable of treating a transcript-closed session as a live
    one. The addendum's whole point is that waiting on an archive answer must
    not keep the media session absorbing turns.
    """
    d = Path(wiki_root) / ".shared-music" / "session" / "archive-pending"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_name(conversation_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", conversation_id)[:120]


class SessionStore:
    def __init__(self, wiki_root: Path):
        self.wiki_root = Path(wiki_root)

    def _path(self, conversation_id: str) -> Path:
        return _active_dir(self.wiki_root) / f"{_safe_name(conversation_id)}.json"

    def get_active(self, conversation_id: str) -> MusicConversationSession | None:
        path = self._path(conversation_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            session = MusicConversationSession.from_dict(data)
        except Exception:
            return None
        if session.phase == PHASE_FINALIZED:
            # A finalized session left behind in active/ (crash between the
            # closed/ write and the unlink) must never be resumed. Terminality
            # is a one-way door — youtube-archive paid for that lesson.
            return None
        if time.time() - session.last_active_at > IDLE_TIMEOUT_S:
            return None  # stale — caller should finalize, not resume
        return session

    def peek_stale(self, conversation_id: str) -> MusicConversationSession | None:
        """Like get_active but returns an idle-expired session instead of
        hiding it, so the caller can finalize it before starting a new one."""
        path = self._path(conversation_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return MusicConversationSession.from_dict(data)
        except Exception:
            return None

    def save(self, session: MusicConversationSession) -> None:
        path = self._path(session.conversation_id)
        tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
        tmp.write_text(json.dumps(session.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def start(
        self, *, conversation_id: str, canonical_id: str, title: str, artist: str,
        video_id: str | None, spotify_id: str | None, platform: str,
        trigger_excerpt: str = "", source_url: str = "", route_decision=None,
    ) -> MusicConversationSession:
        session = MusicConversationSession(
            conversation_id=conversation_id, canonical_id=canonical_id, title=title, artist=artist,
            video_id=video_id, spotify_id=spotify_id, platform=platform,
            trigger_excerpt=trigger_excerpt, source_url=source_url,
            phase=PHASE_STARTING,
            route_category=getattr(route_decision, "category", "") or "",
            route_subtype=getattr(route_decision, "subtype", "") or "",
            route_confidence=float(getattr(route_decision, "confidence", 0.0) or 0.0),
            route_provenance=getattr(route_decision, "provenance", "") or "",
            route_reason_code=getattr(route_decision, "reason_code", "") or "",
        )
        self.save(session)
        return session

    def all_active(self) -> list[MusicConversationSession]:
        """Every session sitting in `active/`, across all conversations.

        The incumbent expiry path only ever looked at the conversation that was
        speaking, so a session belonging to any other conversation — or one
        that outlived a process restart — was never reconsidered. This is what
        the sweep iterates.
        """
        out: list[MusicConversationSession] = []
        for path in sorted(_active_dir(self.wiki_root).glob("*.json")):
            if path.name.startswith("."):
                continue  # our own `.<name>.tmp<pid>` write-and-rename staging
            try:
                out.append(
                    MusicConversationSession.from_dict(
                        json.loads(path.read_text(encoding="utf-8"))
                    )
                )
            except Exception:
                continue
        return out

    def stale_conversation_ids(self, *, now: float | None = None) -> list[tuple[str, str]]:
        """`(conversation_id, finalize_reason)` for every session that has aged
        out or run over the turn cap, whoever it belongs to."""
        stamp = time.time() if now is None else now
        stale = []
        for session in self.all_active():
            if session.phase == PHASE_FINALIZED:
                continue
            if stamp - session.last_active_at > IDLE_TIMEOUT_S:
                stale.append((session.conversation_id, "idle_timeout"))
            elif session.turn_count >= MAX_TURNS:
                stale.append((session.conversation_id, "turn_cap"))
        return stale

    def touch(self, session: MusicConversationSession, *, counts_as_turn: bool = True) -> None:
        """Mark activity. `counts_as_turn=False` for a turn that was evaluated
        and *excluded* — an excluded turn is not part of the experience, so it
        must not consume the session's turn budget either."""
        session.last_active_at = time.time()
        if counts_as_turn:
            session.turn_count += 1
        self.save(session)

    def record_decision(
        self, session: MusicConversationSession, decision, *, excerpt: str = "",
    ) -> None:
        session.membership_log.append({
            "at": time.time(),
            "include": bool(decision.include),
            "reason": decision.reason,
            "phase": decision.next_phase,
            "excerpt": (excerpt or "")[:80],
        })
        del session.membership_log[:-MAX_TURNS]

    def should_finalize_for_turn_cap(self, session: MusicConversationSession) -> bool:
        return session.turn_count >= MAX_TURNS

    def _stem(self, session: MusicConversationSession) -> str:
        # `{conversation}-{started_at_seconds}` alone collides: song-to-song
        # switching is the flow this plugin is built for, and two sessions in
        # one conversation routinely start inside the same second — the second
        # close silently overwrote the first one's archived transcript. The
        # canonical id disambiguates them.
        return (
            f"{_safe_name(session.conversation_id)}-{int(session.started_at)}"
            f"-{_safe_name(session.canonical_id)[:16]}"
        )

    def _take_active(self, conversation_id: str) -> MusicConversationSession | None:
        """Read and remove the active session file, whatever state it is in."""
        path = self._path(conversation_id)
        if not path.exists():
            return None
        try:
            session = MusicConversationSession.from_dict(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except Exception:
            path.unlink(missing_ok=True)
            return None
        path.unlink(missing_ok=True)
        return session

    def finalize(self, conversation_id: str) -> MusicConversationSession | None:
        session = self._take_active(conversation_id)
        if session is None:
            return None
        return self._write_closed(session)

    def _write_closed(self, session: MusicConversationSession) -> MusicConversationSession:
        session.phase = PHASE_FINALIZED
        closed_path = _closed_dir(self.wiki_root) / f"{self._stem(session)}.json"
        closed_path.write_text(
            json.dumps(session.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8",
        )
        return session

    # -- archive decision -----------------------------------------------------

    def close_transcript(
        self, conversation_id: str, *, reason: str = "",
    ) -> MusicConversationSession | None:
        """End the media discussion but leave the *filing* unresolved.

        The session leaves `active/` immediately, so from this moment it takes
        no further turns — that is the invariant the addendum asks for, and
        keeping it open "until the user answers where to file it" would
        reinstate exactly the contamination this branch removes.

        Integration contract with the archive router (Session A)
        --------------------------------------------------------
        The router owns *classification*. This store owns *lifecycle*. The only
        supported sequence is::

            close_transcript(conversation_id)
                -> classification resolved?
                     yes: resolve_archive_decision(session, destination=...)
                     no : leave in ARCHIVE_DECISION_PENDING, ask the user
                -> user's archive reply (hermes_media_membership.archive_reply)
                -> resolve_archive_decision(session, destination=... | declined=True)
                -> FINALIZED

        The router must not unlink an `active/` or `archive-pending/` file, and
        must not write `phase = FINALIZED` itself. Both are reachable — these
        are plain JSON files — and both would skip the closed/ write, so the
        transcript would be dropped rather than archived, silently. Route
        every transition through these two methods; if the router needs a
        state this pair cannot express, that is a change here, not a bypass.
        """
        session = self._take_active(conversation_id)
        if session is None:
            return None
        session.phase = PHASE_ARCHIVE_PENDING
        session.archive_pending_since = time.time()
        session.archive_pending_reason = reason
        path = _pending_dir(self.wiki_root) / f"{self._stem(session)}.json"
        path.write_text(
            json.dumps(session.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8",
        )
        return session

    def pending_archive_decisions(self) -> list[MusicConversationSession]:
        out: list[MusicConversationSession] = []
        for path in sorted(_pending_dir(self.wiki_root).glob("*.json")):
            if path.name.startswith("."):
                continue
            try:
                out.append(
                    MusicConversationSession.from_dict(
                        json.loads(path.read_text(encoding="utf-8"))
                    )
                )
            except Exception:
                continue
        return out

    def resolve_archive_decision(
        self, session: MusicConversationSession, *, destination: str = "", declined: bool = False,
    ) -> MusicConversationSession | None:
        """Apply the user's filing answer (or a default) and finalize."""
        path = _pending_dir(self.wiki_root) / f"{self._stem(session)}.json"
        if not path.exists():
            return None
        session.archive_destination = destination
        session.archive_declined = bool(declined)
        finalized = self._write_closed(session)
        path.unlink(missing_ok=True)
        return finalized
