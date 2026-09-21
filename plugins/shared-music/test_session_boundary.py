"""End-to-end regression for the experience-session boundary.

`test_plugin.py` covers the happy path of the archive. This file covers the
reported failure: after listening to a song together, a model switch, a
model-routing discussion and a gateway restart request were transcribed into
the track's session and republished by `music_weekly` as quoted dialogue about
the song. Everything below runs through the real hooks and the real Obsidian
writers — only the network edge is stubbed, the same way `test_plugin.py` does
it — so the assertions are about what actually lands in the vault.
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import music_session  # noqa: E402
from test_plugin import PLUGIN, PluginTestBase  # noqa: E402

# The live contamination, turn by turn. `expected` is membership, not politeness.
CONTAMINATION_FIXTURE = [
    ("이 곡 후렴 진짜 좋다", True),
    ("보컬 톤이 1:12쯤부터 확 바뀌네", True),
    ("그 부분 다시 들어보자", True),
    ("루나로 수동으로 바꿔줘", False),
    ("모델 라우팅이 자꾸 딴 데로 새는 것 같은데", False),
    ("게이트웨이 한 번 재시작해줘", False),
    ("아 오늘 왜 이렇게 피곤하지", False),
    ("내일 뭐하지", False),
]

CONV = "conv-boundary"


class SessionBoundaryTests(PluginTestBase):
    def _open_session(self, video_id="vidboundar1", *, conversation_id=CONV,
                      artist="M.E.", title="Blue Hour"):
        self._stub_youtube_track(
            video_id, artist=artist, title=title, wav_path=self.wav_a, duration_s=60.0,
        )
        msg = f"https://youtu.be/{video_id} 이거 들어봐"
        PLUGIN.on_pre_llm_call(user_message=msg, session_id=conversation_id, platform="discord")
        PLUGIN.on_post_llm_call(
            session_id=conversation_id, user_message=msg,
            assistant_response="첫 인상: 후반부가 확 열린다", platform="discord",
        )
        return conversation_id

    def _turn(self, text, *, conversation_id=CONV, reply="ㅇㅇ"):
        PLUGIN.on_pre_llm_call(user_message=text, session_id=conversation_id, platform="discord")
        PLUGIN.on_post_llm_call(
            session_id=conversation_id, user_message=text,
            assistant_response=reply, platform="discord",
        )

    def _transcript_text(self):
        """Everything the vault ended up saying about this session."""
        return "\n".join(
            p.read_text(encoding="utf-8")
            for p in self.wiki_root.rglob("*.md")
        )

    # --- the reported failure ------------------------------------------------

    def test_unrelated_turns_never_reach_the_vault(self):
        self._open_session()
        for text, _ in CONTAMINATION_FIXTURE:
            self._turn(text)
        # whatever is still open gets archived, so the note is complete
        PLUGIN._finalize_session(PLUGIN._get_store(), CONV, reason="test_flush")

        published = self._transcript_text()
        for text, expected in CONTAMINATION_FIXTURE:
            if expected:
                continue
            self.assertNotIn(
                text, published,
                f"unrelated turn published into the media archive: {text!r}",
            )

    def test_genuine_listening_turns_do_reach_the_vault(self):
        self._open_session()
        for text, _ in CONTAMINATION_FIXTURE:
            self._turn(text)
        PLUGIN._finalize_session(PLUGIN._get_store(), CONV, reason="test_flush")

        published = self._transcript_text()
        for text, expected in CONTAMINATION_FIXTURE:
            if expected:
                self.assertIn(text, published, f"genuine turn dropped: {text!r}")

    def test_session_is_closed_by_the_control_plane_turn(self):
        self._open_session()
        store = PLUGIN._get_store()
        for text, _ in CONTAMINATION_FIXTURE[:3]:
            self._turn(text)
        self.assertIsNotNone(store.get_active(CONV))

        self._turn("루나로 수동으로 바꿔줘")
        self._turn("모델 라우팅이 자꾸 딴 데로 새는 것 같은데")
        self.assertIsNone(
            store.get_active(CONV),
            "the session stayed open through the operator detour",
        )

    def test_excluded_turns_are_explained_in_the_archived_session(self):
        self._open_session()
        for text, _ in CONTAMINATION_FIXTURE[:4]:
            self._turn(text)
        PLUGIN._finalize_session(PLUGIN._get_store(), CONV, reason="test_flush")

        closed = sorted((self.wiki_root / ".shared-music" / "session" / "closed").glob("*.json"))
        self.assertTrue(closed)
        import json
        session = json.loads(closed[-1].read_text(encoding="utf-8"))
        by_excerpt = {e["excerpt"]: e for e in session["membership_log"]}
        self.assertFalse(by_excerpt["루나로 수동으로 바꿔줘"]["include"])
        self.assertEqual(
            by_excerpt["루나로 수동으로 바꿔줘"]["reason"], "exclude_mode_switch_request",
        )
        self.assertTrue(by_excerpt["이 곡 후렴 진짜 좋다"]["include"])

    # --- the additional scenarios -------------------------------------------

    def test_followup_about_the_same_song_stays_in(self):
        self._open_session()
        self._turn("이 노래 베이스 라인 좋다")
        self._turn("2:10 그 구간 다시 들어보자")
        PLUGIN._finalize_session(PLUGIN._get_store(), CONV, reason="test_flush")
        published = self._transcript_text()
        self.assertIn("베이스 라인 좋다", published)
        self.assertIn("다시 들어보자", published)

    def test_a_new_song_finalizes_the_previous_session(self):
        self._open_session()
        self._turn("이 곡 좋다")
        store = PLUGIN._get_store()
        first = store.get_active(CONV)
        self.assertIsNotNone(first)

        self._stub_youtube_track(
            "vidboundar2", artist="Other", title="Second Song",
            wav_path=self.wav_b, duration_s=45.0,
        )
        msg = "https://youtu.be/vidboundar2 이것도 들어봐"
        PLUGIN.on_pre_llm_call(user_message=msg, session_id=CONV, platform="discord")
        second = store.get_active(CONV)
        self.assertIsNotNone(second)
        self.assertNotEqual(second.canonical_id, first.canonical_id)

        closed = list((self.wiki_root / ".shared-music" / "session" / "closed").glob("*.json"))
        self.assertEqual(len(closed), 1, "the first song's session was not closed")

    def test_two_media_sessions_back_to_back_do_not_share_a_transcript(self):
        self._open_session()
        self._turn("이 곡 후렴 좋다")
        self._stub_youtube_track(
            "vidboundar3", artist="Other", title="Second Song",
            wav_path=self.wav_b, duration_s=45.0,
        )
        msg = "https://youtu.be/vidboundar3 이것도 들어봐"
        PLUGIN.on_pre_llm_call(user_message=msg, session_id=CONV, platform="discord")
        PLUGIN.on_post_llm_call(
            session_id=CONV, user_message=msg,
            assistant_response="두 번째 곡 인상", platform="discord",
        )
        self._turn("얘는 드럼이 더 세네")
        PLUGIN._finalize_session(PLUGIN._get_store(), CONV, reason="test_flush")

        import json
        sessions = [
            json.loads(p.read_text(encoding="utf-8"))
            for p in sorted((self.wiki_root / ".shared-music" / "session" / "closed").glob("*.json"))
        ]
        self.assertEqual(len(sessions), 2)
        first_log = " ".join(e["text"] for e in sessions[0]["exchange_log"])
        self.assertNotIn("드럼이 더 세네", first_log)

    def test_rapid_topic_switch_and_return(self):
        self._open_session()
        self._turn("내일 날씨 어때")          # soft: excluded, grace window
        self._turn("아 근데 이 곡 후렴 다시 들어보자")  # revives
        store = PLUGIN._get_store()
        self.assertIsNotNone(store.get_active(CONV))
        PLUGIN._finalize_session(store, CONV, reason="test_flush")
        published = self._transcript_text()
        self.assertNotIn("내일 날씨 어때", published)
        self.assertIn("후렴 다시 들어보자", published)

    def test_system_note_injection_neither_joins_nor_ends_the_session(self):
        self._open_session()
        self._turn("이 곡 좋다")
        store = PLUGIN._get_store()
        before = store.get_active(CONV)
        self._turn("[ASYNC DELEGATION BATCH COMPLETE — deleg_9] done")
        after = store.get_active(CONV)
        self.assertIsNotNone(after, "a system envelope ended the user's session")
        self.assertEqual(after.turn_count, before.turn_count)
        PLUGIN._finalize_session(store, CONV, reason="test_flush")
        self.assertNotIn("DELEGATION BATCH", self._transcript_text())

    def test_turn_just_before_and_just_after_the_timeout(self):
        self._open_session()
        store = PLUGIN._get_store()

        session = store.get_active(CONV)
        session.last_active_at = time.time() - (music_session.IDLE_TIMEOUT_S - 60)
        store.save(session)
        self._turn("이 곡 후렴 좋다")
        self.assertIsNotNone(store.get_active(CONV), "a turn inside the timeout was dropped")

        session = store.get_active(CONV)
        session.last_active_at = time.time() - (music_session.IDLE_TIMEOUT_S + 60)
        store.save(session)
        self._turn("이 곡 브릿지도 좋다")
        self.assertIsNone(store.get_active(CONV))
        self.assertNotIn("브릿지도 좋다", self._transcript_text())

    def test_stale_session_from_another_conversation_is_swept_after_a_restart(self):
        """The restart case: nothing rehydrates in-memory state on boot, and the
        incumbent expiry only ever looked at the conversation that was speaking,
        so another conversation's session sat in active/ indefinitely."""
        self._open_session(conversation_id="conv-orphan")
        store = PLUGIN._get_store()
        orphan = store.get_active("conv-orphan")
        orphan.last_active_at = time.time() - (music_session.IDLE_TIMEOUT_S + 600)
        store.save(orphan)

        # process restart: a brand new store object, no in-memory state at all
        PLUGIN._store = music_session.SessionStore(self.wiki_root)
        PLUGIN.on_pre_llm_call(
            user_message="딴 얘기 좀 하자", session_id="conv-unrelated", platform="discord",
        )
        self.assertEqual(
            list((self.wiki_root / ".shared-music" / "session" / "active").glob("*.json")), [],
            "an orphaned session survived the sweep",
        )

    def test_finalized_session_never_takes_another_turn(self):
        self._open_session()
        self._turn("이 곡 좋다")
        store = PLUGIN._get_store()
        session = store.get_active(CONV)

        # a finalized session left behind in active/ by a crash between the
        # closed/ write and the unlink
        session.phase = music_session.PHASE_FINALIZED
        store.save(session)
        self._turn("이 노래 후렴 진짜 좋다")
        self.assertIsNone(store.get_active(CONV))
        self.assertNotIn("후렴 진짜 좋다", self._transcript_text())

    # --- archive decision pending -------------------------------------------

    def test_transcript_closes_before_the_filing_question_is_answered(self):
        """The addendum's invariant: an unresolved archive destination must not
        keep the media session alive. Waiting to be told where to file it is
        exactly the excuse that would reinstate the contamination."""
        self._open_session()
        self._turn("이 곡 후렴 좋다")
        store = PLUGIN._get_store()

        pending = store.close_transcript(CONV, reason="unclassified")
        self.assertIsNotNone(pending)
        self.assertEqual(pending.phase, music_session.PHASE_ARCHIVE_PENDING)
        self.assertIsNone(store.get_active(CONV), "the session stayed active")
        self.assertEqual(
            list((self.wiki_root / ".shared-music" / "session" / "active").glob("*.json")), [],
        )

        # ordinary conversation while the question is outstanding is ordinary
        self._turn("이 노래 후렴 진짜 좋았는데")
        self.assertIsNone(store.get_active(CONV))
        reloaded = store.pending_archive_decisions()
        self.assertEqual(len(reloaded), 1)
        self.assertNotIn(
            "진짜 좋았는데",
            " ".join(e["text"] for e in reloaded[0].exchange_log),
            "a turn was transcribed into a transcript-closed session",
        )

    def test_answering_the_filing_question_finalizes_the_session(self):
        self._open_session()
        self._turn("이 곡 후렴 좋다")
        store = PLUGIN._get_store()
        pending = store.close_transcript(CONV, reason="unclassified")

        import hermes_media_membership as membership
        reply = membership.archive_reply(
            membership.Turn(text="Music 폴더에 저장해줘"), options=("Music",),
        )
        self.assertIsNotNone(reply)
        finalized = store.resolve_archive_decision(pending, destination=reply.destination)
        self.assertEqual(finalized.phase, music_session.PHASE_FINALIZED)
        self.assertEqual(finalized.archive_destination, "Music")
        self.assertEqual(store.pending_archive_decisions(), [])
        closed = list((self.wiki_root / ".shared-music" / "session" / "closed").glob("*.json"))
        self.assertEqual(len(closed), 1)

    def test_the_router_integration_sequence_end_to_end(self):
        """Pins the contract Session A must follow: close_transcript ->
        (classified? resolve : PENDING -> reply -> resolve) -> FINALIZED."""
        import hermes_media_membership as membership

        self._open_session()
        self._turn("이 곡 후렴 좋다")
        store = PLUGIN._get_store()

        pending = store.close_transcript(CONV, reason="router_unclassified")
        self.assertEqual(pending.phase, music_session.PHASE_ARCHIVE_PENDING)
        self.assertIsNone(store.get_active(CONV))

        # the router could not classify, so the user is asked; their reply is
        # consumed as the pending classification event and nothing else is
        self.assertIsNone(membership.archive_reply(
            membership.Turn(text="아 근데 오늘 피곤하다"), options=("Music",),
        ))
        reply = membership.archive_reply(
            membership.Turn(text="Music 폴더에 저장해줘"), options=("Music",),
        )
        finalized = store.resolve_archive_decision(pending, destination=reply.destination)
        self.assertEqual(finalized.phase, music_session.PHASE_FINALIZED)
        self.assertEqual(store.pending_archive_decisions(), [])

    def test_the_router_cannot_reach_finalized_without_the_closed_write(self):
        """The bypass the contract forbids: unlinking the pending file (or
        stamping FINALIZED on it) skips the closed/ write, which drops the
        transcript instead of archiving it. Asserted so the cost is on record
        rather than discovered in the vault."""
        self._open_session()
        self._turn("이 곡 후렴 좋다")
        store = PLUGIN._get_store()
        pending = store.close_transcript(CONV, reason="router_unclassified")

        pending_dir = self.wiki_root / ".shared-music" / "session" / "archive-pending"
        files = list(pending_dir.glob("*.json"))
        self.assertEqual(len(files), 1)
        files[0].unlink()  # what a router must NOT do

        self.assertEqual(store.pending_archive_decisions(), [])
        self.assertEqual(
            list((self.wiki_root / ".shared-music" / "session" / "closed").glob("*.json")), [],
            "the transcript was lost, not archived",
        )
        self.assertIsNone(
            store.resolve_archive_decision(pending, destination="Music"),
            "resolve must refuse a session whose pending record is gone",
        )

    def test_an_unanswered_filing_question_expires(self):
        self._open_session()
        store = PLUGIN._get_store()
        pending = store.close_transcript(CONV, reason="unclassified")
        pending.last_active_at = time.time() - (music_session.ARCHIVE_PENDING_TIMEOUT_S + 60)

        import hermes_media_membership as membership
        self.assertTrue(
            membership.archive_decision_expired(pending.view(), now_s=time.time()),
        )

    def test_excluded_turns_do_not_consume_the_turn_budget(self):
        self._open_session()
        store = PLUGIN._get_store()
        before = store.get_active(CONV).turn_count
        self._turn("내일 날씨 어때")
        self.assertEqual(store.get_active(CONV).turn_count, before)


if __name__ == "__main__":
    unittest.main()
