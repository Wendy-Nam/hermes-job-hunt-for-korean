"""Regression cover for the ReactionCandidate reveal fail-open.

Originally written against the temporary `music_reveal` seam, which existed
because `SpoilerBoundary.visible()` resolved anchors on `.range`/`.position`
only and therefore revealed any `ReactionCandidate` (its axis fields are
`discovered_at` / `earliest_safe_position`).

At integration the defect was repaired in the contract primitive and the seam
was deleted. This file now exercises the canonical gate under the seam's old
name, so C's fail-closed coverage keeps protecting the behaviour it was written
for. The two assertions that pinned the *buggy* behaviour on purpose now assert
the repair; do not weaken them back.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
for extra in (HERE, HERE.parent / "_shared"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import music_media_fetch as mfetch  # noqa: E402
import music_realtime as rt  # noqa: E402
import hermes_media_runtime as _canonical  # noqa: E402


class reveal:  # noqa: N801 - kept lowercase: it stands in for the deleted module
    """The canonical reveal gate, under the deleted seam's call signature."""

    revealable = staticmethod(_canonical.may_reveal)
    visible = staticmethod(_canonical.visible)
    assert_revealable = staticmethod(_canonical.assert_revealable)
from hermes_media import (  # noqa: E402
    CLOCK_MEDIA,
    RISK_HIGH,
    RISK_NONE,
    MediaPosition,
    MediaRange,
    ReactionCandidate,
    SpoilerBoundary,
    SpoilerViolation,
    ValidationError,
    new_id,
)

MEDIA_ID = "music:abc123"


def at(seconds: float) -> MediaPosition:
    return MediaPosition.at_seconds(seconds, clock=CLOCK_MEDIA)


def candidate(*, safe_at=None, discovered=None, risk=RISK_HIGH) -> ReactionCandidate:
    return ReactionCandidate(
        candidate_id=new_id("cand"),
        media_id=MEDIA_ID,
        reason="the drop lands here",
        evidence_ids=("obs_1",),
        discovered_at=discovered,
        earliest_safe_position=safe_at,
        spoiler_risk=risk,
    )


class ContractDefectTests(unittest.TestCase):
    """Pins the fail-open, so the day it is fixed upstream we find out."""

    def test_frozen_visible_lets_a_future_candidate_through(self):
        boundary = SpoilerBoundary(consumed_through=at(30))
        future = candidate(safe_at=at(300), discovered=at(300))

        self.assertTrue(
            boundary.may_reveal(None),
            "unanchored items are revealable — the premise the defect rides on",
        )
        self.assertEqual(
            boundary.visible([future]), (future,)[:0],
            "REPAIRED: visible() resolves a candidate through anchor_of() and "
            "hides one whose earliest_safe_position is ahead of the boundary. "
            "This assertion used to demand the opposite, documenting the frozen "
            "contract's fail-open; the seam it justified is now deleted. Do not "
            "restore the leak to make an old assertion pass.",
        )

    def test_the_contracts_own_per_type_gate_is_correct(self):
        boundary = SpoilerBoundary(consumed_through=at(30))
        self.assertFalse(candidate(safe_at=at(300), discovered=at(300)).revealable_under(boundary))


class RevealSeamTests(unittest.TestCase):
    def setUp(self) -> None:
        self.boundary = SpoilerBoundary(consumed_through=at(30))

    # -- the required regression ---------------------------------------------
    def test_candidate_ahead_of_the_boundary_is_not_visible(self):
        future = candidate(safe_at=at(300), discovered=at(300))
        self.assertFalse(reveal.revealable(self.boundary, future))
        self.assertEqual(reveal.visible(self.boundary, [future]), ())

    def test_candidate_behind_the_boundary_is_visible(self):
        past = candidate(safe_at=at(10), discovered=at(10))
        self.assertTrue(reveal.revealable(self.boundary, past))
        self.assertEqual(reveal.visible(self.boundary, [past]), (past,))

    def test_no_consumed_position_reveals_nothing(self):
        empty = SpoilerBoundary()
        self.assertFalse(reveal.revealable(empty, candidate(safe_at=at(1), discovered=at(1))))

    # -- fail-closed edges ----------------------------------------------------
    def test_candidate_with_a_discovery_but_no_safe_position_is_refused(self):
        """Defence in depth, and the guard is genuinely unreachable today.

        Both `__post_init__` and `from_dict` reject this combination, so the
        only way to build one is to bypass the frozen dataclass — which is what
        this test does. The guard stays because `revealable_under` would fall
        back to `spoiler_risk` for such an object, and a candidate that knows
        where it was found but not when it is safe must never be judged on a
        risk label alone.
        """
        for factory in (
            lambda: ReactionCandidate(
                candidate_id="c1", media_id=MEDIA_ID, reason="r",
                evidence_ids=("obs_1",), discovered_at=at(300),
                earliest_safe_position=None, spoiler_risk=RISK_NONE,
            ),
            lambda: ReactionCandidate.from_dict({
                "candidate_id": "c1", "media_id": MEDIA_ID, "reason": "r",
                "evidence_ids": ("obs_1",), "discovered_at": at(300).to_dict(),
                "spoiler_risk": RISK_NONE,
            }),
        ):
            with self.assertRaises(ValidationError):
                factory()

        broken = candidate(safe_at=at(300), discovered=at(300), risk=RISK_NONE)
        object.__setattr__(broken, "earliest_safe_position", None)
        self.assertFalse(
            broken.revealable_under(self.boundary),
            "REPAIRED: a missing earliest_safe_position now fails closed in the "
            "contract's own gate instead of falling back to spoiler_risk. This "
            "assertion used to be assertTrue, documenting that fallback.",
        )
        self.assertFalse(
            reveal.revealable(self.boundary, broken),
            "knowing where it was found but not when it is safe is ambiguous — refuse",
        )

    def test_incomparable_axis_is_refused(self):
        ordinal_only = ReactionCandidate(
            candidate_id="c2", media_id=MEDIA_ID, reason="r", evidence_ids=("obs_1",),
            earliest_safe_position=MediaPosition.at_ordinal(4, sequence="cut"),
            spoiler_risk=RISK_HIGH,
        )
        self.assertFalse(
            reveal.revealable(self.boundary, ordinal_only),
            "a cut index and a millisecond offset are not comparable — refuse",
        )

    def test_candidate_shaped_dict_does_not_slip_through_the_unanchored_branch(self):
        self.assertFalse(
            reveal.revealable(
                self.boundary,
                {"candidate_id": "c3", "earliest_safe_position": {"timestamp_ms": 300_000}},
            )
        )

    def test_unanchored_candidate_needs_an_explicit_declaration_to_stay_visible(self):
        """Decision 2: a missing position is ambiguous, and ambiguous means no.

        This used to assert that risk=none plus no position was revealable --
        the frozen contract's permissive reading. A music candidate comes out of
        timed audio analysis, so trusting a producer-set enum over an absent
        position is exactly the fail-open Session E flagged. Revealing now
        requires the producer to say, in the record itself, that the candidate
        carries no media-derived content.
        """
        note = candidate(risk=RISK_NONE)
        self.assertIsNone(note.discovered_at)
        self.assertIsNone(note.earliest_safe_position)
        self.assertFalse(
            reveal.revealable(self.boundary, note),
            "risk=none must not substitute for a position",
        )

        declared = ReactionCandidate(
            candidate_id=new_id("cand"),
            media_id=MEDIA_ID,
            reason="track metadata, not a listening observation",
            evidence_ids=("obs_1",),
            spoiler_risk=RISK_NONE,
            unanchored_metadata=True,
        )
        self.assertTrue(reveal.revealable(self.boundary, declared))

    def test_a_none_boundary_reveals_nothing(self):
        self.assertFalse(reveal.revealable(None, candidate(safe_at=at(1), discovered=at(1))))

    def test_assert_revealable_raises_on_a_future_candidate(self):
        with self.assertRaises(SpoilerViolation):
            reveal.assert_revealable(self.boundary, candidate(safe_at=at(300), discovered=at(300)))

    # -- non-candidate items keep working ------------------------------------
    def test_ranges_and_positions_still_filter_normally(self):
        past = MediaRange(start=at(0), end=at(10))
        future = MediaRange(start=at(100), end=at(120))
        self.assertEqual(reveal.visible(self.boundary, [past, future]), (past,))

    def test_artifacts_anchored_by_range_still_filter_normally(self):
        from hermes_media import AnalysisArtifact

        def art(start, end, key):
            return AnalysisArtifact(
                artifact_id=new_id("art"), media_id=MEDIA_ID,
                artifact_kind="fast_ear_window", cache_key=key,
                producer="p", range=MediaRange(start=at(start), end=at(end)),
                body_ref=f"x://{key}",
            )

        past, future = art(0, 10, "k1"), art(100, 120, "k2")
        self.assertEqual(reveal.visible(self.boundary, [past, future]), (past,))


class CompanionRevealTests(unittest.TestCase):
    """The companion's own filter must inherit the fail-closed behaviour."""

    def test_visible_now_gates_a_future_candidate(self):
        companion = rt.RealtimeMusicCompanion(
            session=rt.new_music_session(media_id=MEDIA_ID, conversation_id="c"),
            source=mfetch.music_source("VID", media_id_=MEDIA_ID),
            ladder=None,
            fast_ear_fn=lambda *a: (),
        )
        companion.session = companion.session.advanced_to(at(30), consumed=True)

        future = candidate(safe_at=at(300), discovered=at(300))
        past = candidate(safe_at=at(5), discovered=at(5))
        self.assertEqual(companion.visible_now([past, future]), (past,))


if __name__ == "__main__":
    unittest.main()
