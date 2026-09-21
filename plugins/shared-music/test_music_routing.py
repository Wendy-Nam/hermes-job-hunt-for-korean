"""Regression tests for shared-music's trigger gate after provenance routing.

Imports ``music_intent`` and ``music_link`` directly rather than the plugin
package: the package pulls in the vendored numpy/scipy/yt-dlp stack under
.vendor/, which only exists on the server, and a gate that can only be
exercised in production is a gate nobody exercises. The gate itself lives in
music_intent.route_share for exactly that reason.

Run with:  python3 -m unittest test_music_routing -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (str(HERE), str(HERE.parent / "_shared")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import music_intent  # noqa: E402
import music_link  # noqa: E402
from hermes_media_route import (  # noqa: E402
    CATEGORY_MUSIC,
    CATEGORY_MUSIC_VIDEO,
    CATEGORY_UNKNOWN,
    FEATURE_MUSIC_SHARE,
    PROV_MEDIA_METADATA,
    PROV_USER_INTENT,
)

OEMBED = {
    "JeucohIa5LQ": {"title": "M.E.", "channel": "Gary Numan - Topic"},
    "dQw4w9WgXcQ": {"title": "Rust ownership explained", "channel": "Some Dev Channel"},
    "MMMMMMMMMM1": {"title": "NewJeans 'Ditto' Official MV", "channel": "HYBE LABELS"},
}


def offline_probe(video_id, *, source_url="", fetcher=None):
    meta = OEMBED.get(video_id)
    if meta is None:
        return None
    out = dict(meta, provider="youtube", video_id=video_id)
    if source_url:
        out["host"] = source_url
    return out


class MusicTriggerTests(unittest.TestCase):
    def gate(self, message, **kwargs):
        link = music_link.detect_link(message)
        return music_intent.route_share(
            message,
            link_source=getattr(link, "source", None),
            link_id=getattr(link, "id", None),
            probe=offline_probe,
            **kwargs,
        )

    def test_topic_art_track_now_triggers_without_any_intent_phrase(self):
        """The live defect, from the music side.

        The old gate required a phrase from a closed list, so this exact share
        never entered the music flow at all — which is how it ended up in the
        video archive instead.
        """
        self.assertFalse(music_intent.has_music_intent_phrase(
            "https://www.youtube.com/watch?v=JeucohIa5LQ"))
        is_trigger, decision = self.gate("https://www.youtube.com/watch?v=JeucohIa5LQ")
        self.assertTrue(is_trigger)
        self.assertEqual(decision.category, CATEGORY_MUSIC)
        self.assertEqual(decision.provenance, PROV_MEDIA_METADATA)
        self.assertEqual(decision.reason_code, "meta.topic_channel")

    def test_original_phrase_list_still_fires_verbatim(self):
        # Every phrase the pre-router gate accepted must still trigger — the
        # router widens this gate, it must never narrow it.
        for phrase in ("들어봐", "같이 들어보자", "감상해봐", "이거 어때",
                       "이 노래 좋지", "노래 추천", "이 곡 어때", "플레이리스트"):
            message = f"{phrase} https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            self.assertTrue(music_intent.has_music_intent_phrase(phrase), phrase)
            is_trigger, decision = self.gate(message)
            self.assertTrue(is_trigger, f"{phrase!r} stopped triggering: {decision}")

    def test_spotify_track_triggers_with_no_network(self):
        is_trigger, decision = self.gate(
            "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT")
        self.assertTrue(is_trigger)
        self.assertEqual(decision.reason_code, "meta.spotify_track")

    def test_music_youtube_host_triggers(self):
        is_trigger, decision = self.gate(
            "https://music.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertTrue(is_trigger)
        self.assertEqual(decision.reason_code, "meta.music_host")

    def test_music_share_workflow_triggers_on_a_bare_link(self):
        is_trigger, decision = self.gate(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            invoking_feature=FEATURE_MUSIC_SHARE,
        )
        self.assertTrue(is_trigger)
        self.assertEqual(decision.category, CATEGORY_MUSIC)

    def test_official_mv_is_music_video_and_still_ours(self):
        is_trigger, decision = self.gate("https://www.youtube.com/watch?v=MMMMMMMMMM1")
        self.assertTrue(is_trigger)
        self.assertEqual(decision.category, CATEGORY_MUSIC_VIDEO)

    def test_a_lecture_never_triggers_the_music_flow(self):
        is_trigger, decision = self.gate(
            "이 영상 분석해줘 https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertFalse(is_trigger)
        self.assertEqual(decision.provenance, PROV_USER_INTENT)

    def test_ambiguous_link_does_not_trigger_music_either(self):
        """Symmetry check: the fix must not simply move the over-claim.

        An undecided share belongs to nobody. If this ever starts returning
        True, shared-music has inherited exactly the bug it was written to
        fix, pointed at the other shelf.
        """
        is_trigger, decision = self.gate("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertFalse(is_trigger)
        self.assertEqual(decision.category, CATEGORY_UNKNOWN)

    def test_no_link_is_not_a_trigger(self):
        is_trigger, decision = self.gate("이 노래 들어봐")
        self.assertFalse(is_trigger)
        self.assertIsNone(decision)

    def test_probe_failure_declines_rather_than_guessing(self):
        def dead(_vid, *, source_url="", fetcher=None):
            return None

        link = music_link.detect_link("https://www.youtube.com/watch?v=JeucohIa5LQ")
        is_trigger, decision = music_intent.route_share(
            "https://www.youtube.com/watch?v=JeucohIa5LQ",
            link_source=link.source, link_id=link.id, probe=dead,
        )
        self.assertFalse(is_trigger)
        self.assertEqual(decision.category, CATEGORY_UNKNOWN)


if __name__ == "__main__":
    unittest.main(verbosity=2)
