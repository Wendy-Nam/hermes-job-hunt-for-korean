"""Cross-source identity tests — the wrong-remix guard.

Every case here is a shape that text scoring alone gets wrong, which is why
the gate exists at all.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import music_cross_source_identity as ci  # noqa: E402


def v(**kw):
    base = dict(spotify_title="Blinding Lights", spotify_artist="The Weeknd",
                spotify_duration_s=200.0, candidate_title="The Weeknd - Blinding Lights",
                candidate_channel="The Weeknd - Topic", candidate_duration_s=200.5)
    base.update(kw)
    return ci.verify(**base)


class HappyPathTests(unittest.TestCase):
    def test_exact_match_is_verified(self):
        r = v()
        self.assertEqual(r.status, ci.VERIFIED)
        self.assertTrue(r.usable)

    def test_small_encode_difference_still_verified(self):
        """Platform masters differ by a second or two; that is an encode, not
        a different recording."""
        self.assertEqual(v(candidate_duration_s=201.8).status, ci.VERIFIED)


class DurationVetoTests(unittest.TestCase):
    def test_extended_mix_is_rejected_even_with_perfect_text(self):
        """The headline case. Title and artist match exactly and the channel is
        authoritative — text scoring would take it happily."""
        r = v(candidate_title="The Weeknd - Blinding Lights", candidate_duration_s=380.0)
        self.assertEqual(r.status, ci.UNCONFIRMED)
        self.assertFalse(r.usable)
        self.assertIn("duration differs", r.reasons[0])

    def test_duration_veto_beats_every_positive_signal(self):
        r = v(candidate_title="The Weeknd - Blinding Lights (Official Video)",
              candidate_channel="The Weeknd - Topic", candidate_duration_s=120.0)
        self.assertEqual(r.status, ci.UNCONFIRMED)
        self.assertEqual(r.confidence, 0.0)

    def test_borderline_duration_is_plausible_not_verified(self):
        r = v(candidate_duration_s=204.0)
        self.assertEqual(r.status, ci.PLAUSIBLE)
        self.assertTrue(r.usable)

    def test_delta_is_reported(self):
        self.assertAlmostEqual(v(candidate_duration_s=203.0).duration_delta_s, 3.0, places=1)


class VersionMarkerTests(unittest.TestCase):
    def test_remix_candidate_for_a_plain_track_is_rejected(self):
        r = v(candidate_title="Blinding Lights (Chill Remix)", candidate_duration_s=200.2)
        self.assertEqual(r.status, ci.UNCONFIRMED)
        self.assertIn("version mismatch", r.reasons[0])

    def test_live_take_for_a_studio_track_is_rejected(self):
        self.assertEqual(v(candidate_title="Blinding Lights (Live at Wembley)").status, ci.UNCONFIRMED)

    def test_sped_up_upload_is_rejected(self):
        self.assertEqual(v(candidate_title="Blinding Lights (sped up)").status, ci.UNCONFIRMED)

    def test_cover_is_rejected(self):
        self.assertEqual(v(candidate_title="Blinding Lights - cover by someone").status, ci.UNCONFIRMED)

    def test_the_mirror_case_studio_take_for_a_live_link(self):
        """A Spotify link to the *live* version resolving to the studio master
        is the same mistake, and the asymmetric check has to catch both."""
        r = v(spotify_title="Blinding Lights - Live", candidate_title="The Weeknd - Blinding Lights")
        self.assertEqual(r.status, ci.UNCONFIRMED)

    def test_matching_live_versions_are_accepted(self):
        r = v(spotify_title="Blinding Lights - Live", candidate_title="Blinding Lights (Live)")
        self.assertTrue(r.usable)

    def test_remaster_is_not_disqualifying(self):
        """A remaster is the same performance and the same duration; treating
        it as a mismatch would reject the right answer for most catalogue."""
        r = v(candidate_title="Blinding Lights (2024 Remaster)")
        self.assertTrue(r.usable)


class TextSignalTests(unittest.TestCase):
    def test_missing_duration_on_either_side_cannot_verify(self):
        """Without duration there is no numeric corroboration, so the best
        available verdict is PLAUSIBLE — never VERIFIED."""
        r = v(spotify_duration_s=None, candidate_duration_s=None)
        self.assertNotEqual(r.status, ci.VERIFIED)

    def test_wrong_artist_with_matching_duration_is_not_verified(self):
        r = v(candidate_title="Some Other Band - Blinding Lights", candidate_channel="randomuploads")
        self.assertNotEqual(r.status, ci.VERIFIED)

    def test_featured_artist_split_still_agrees(self):
        r = v(spotify_artist="The Weeknd, Rosalía",
              candidate_title="The Weeknd - Blinding Lights (feat. Rosalía)")
        self.assertTrue(r.usable)

    def test_korean_nfkc_normalisation(self):
        r = ci.verify(spotify_title="밤편지", spotify_artist="아이유", spotify_duration_s=250.0,
                      candidate_title="아이유 - 밤편지", candidate_channel="IU - Topic",
                      candidate_duration_s=250.4)
        self.assertEqual(r.status, ci.VERIFIED)

    def test_korean_version_marker_is_caught(self):
        r = ci.verify(spotify_title="밤편지", spotify_artist="아이유", spotify_duration_s=250.0,
                      candidate_title="밤편지 (라이브)", candidate_channel="IU - Topic",
                      candidate_duration_s=250.4)
        self.assertEqual(r.status, ci.UNCONFIRMED)


class VerdictShapeTests(unittest.TestCase):
    def test_as_dict_is_traceable_and_carries_no_secret(self):
        d = v().as_dict()
        for k in ("identity_status", "identity_confidence", "identity_reasons",
                  "duration_delta_s", "candidate_version_markers"):
            self.assertIn(k, d)

    def test_only_verified_and_plausible_are_usable(self):
        self.assertTrue(ci.IdentityVerdict(ci.VERIFIED, 1.0, ()).usable)
        self.assertTrue(ci.IdentityVerdict(ci.PLAUSIBLE, 0.6, ()).usable)
        self.assertFalse(ci.IdentityVerdict(ci.UNCONFIRMED, 0.0, ()).usable)


class ResolveIntegrationTests(unittest.TestCase):
    """The gate as `resolve_from_spotify` uses it."""

    def setUp(self):
        import music_resolve
        self.mr = music_resolve

    def _resolve(self, candidate, duration_s=200.0):
        return self.mr.resolve_from_spotify(
            "sp1",
            native_fn=lambda tid: {"title": "Blinding Lights", "artist": "The Weeknd",
                                   "duration_s": duration_s},
            search_fn=lambda q, **kw: [candidate],
            metadata_fn=lambda vid: {"title": candidate["title"], "duration_s": candidate.get("duration_s")},
        )

    def test_a_wrong_version_degrades_to_metadata_only(self):
        r = self._resolve({"video_id": "yt1", "title": "Blinding Lights (Extended Mix)",
                           "channel": "The Weeknd - Topic", "duration_s": 400.0})
        self.assertFalse(r.playable)
        self.assertEqual(r.audio_source, "spotify_metadata_only")
        self.assertEqual(r.title, "Blinding Lights", "identity must survive the degrade")
        self.assertEqual(r.artist, "The Weeknd")

    def test_a_right_version_is_accepted(self):
        r = self._resolve({"video_id": "yt1", "title": "The Weeknd - Blinding Lights",
                           "channel": "The Weeknd - Topic", "duration_s": 200.4})
        self.assertTrue(r.playable)
        self.assertEqual(r.video_id, "yt1")


if __name__ == "__main__":
    unittest.main()
