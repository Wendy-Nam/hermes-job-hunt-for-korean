"""Two adapters, one media session, one decode — and no accidental game mode.

The routing rule is the point: a session becomes game-shaped only because the
caller said so. There is deliberately no heuristic, so the test that matters
most here is the negative one — an ordinary music video must not produce a
single game frame no matter how much it moves.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
for _p in (str(_HERE), str(_HERE.parent / "_shared"),
           str(_HERE.parent / "video-perception")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_realtime_flags as flags  # noqa: E402
import hermes_realtime_runtime as rtr  # noqa: E402
import music_realtime_runtime as mrr  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe not installed — no frame source exists here",
)


@pytest.fixture(scope="module")
def clip(tmp_path_factory) -> str:
    out = tmp_path_factory.mktemp("clip") / "v.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc2=size=320x240:rate=15", "-t", "6",
         "-pix_fmt", "yuv420p", str(out)],
        check=True, capture_output=True, timeout=120,
    )
    return str(out)


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_DATA", str(tmp_path))
    monkeypatch.setenv(flags.env_name(flags.FLAG_MUSIC), "1")
    monkeypatch.setenv(flags.env_name(flags.FLAG_GAME), "1")
    flags.invalidate()
    rtr.reset_manager_for_tests()
    # Never touch the network in a test: acquisition always fails, which is the
    # honest "we could not fetch the audio" path and leaves the frame fan-out —
    # the thing under test — completely unaffected.
    monkeypatch.setattr(mrr, "_make_acquire", lambda *a, **k: (lambda: None))
    yield
    rtr.reset_manager_for_tests()
    flags.invalidate()


class _Ctx:
    pass


def _call(**args) -> dict:
    return json.loads(mrr.make_handler(_Ctx())(args))


def test_an_ordinary_music_video_never_opens_a_game_session(clip):
    out = _call(action="report", media_id="m:song", video_id="abc",
                position_s=3.0, video_path=clip)   # no content_mode
    assert out["status"] == "accepted"
    assert "game" not in out
    assert rtr.manager().get(mrr.GAME_NAMESPACE, "m:song") is None


def test_game_mode_without_a_video_path_stays_music_only(clip):
    out = _call(action="report", media_id="m:g0", video_id="abc",
                position_s=3.0, content_mode="game")
    assert "game" not in out
    assert rtr.manager().get(mrr.GAME_NAMESPACE, "m:g0") is None


def test_the_game_flag_gates_the_fan_out(clip, monkeypatch):
    monkeypatch.setenv(flags.env_name(flags.FLAG_GAME), "0")
    flags.invalidate()
    out = _call(action="report", media_id="m:g1", video_id="abc",
                position_s=3.0, content_mode="game", video_path=clip)
    assert "game" not in out
    assert rtr.manager().get(mrr.GAME_NAMESPACE, "m:g1") is None


def test_explicit_game_mode_fans_out_from_the_same_session(clip):
    out = _call(action="report", media_id="m:g2", video_id="abc",
                position_s=2.0, content_mode="game", video_path=clip)
    assert out["status"] == "accepted"
    game = out.get("game")
    assert game and not game.get("error"), out
    assert game["frames_fed"] > 0, "no real frames were decoded"
    assert game["probes"] >= game["frames_fed"]

    mgr = rtr.manager()
    music = mgr.get(mrr.NAMESPACE, "m:g2")
    gsess = mgr.get(mrr.GAME_NAMESPACE, "m:g2")
    assert music is not None and gsess is not None
    # Two engines, two contexts, one media session.
    assert music.engine is not gsess.engine
    assert music.engine.context is not gsess.engine.context
    assert gsess.owner.media_id == music.owner.media_id


def test_the_advance_only_probes_what_the_listener_crossed(clip):
    _call(action="report", media_id="m:g3", video_id="abc", position_s=1.0,
          content_mode="game", video_path=clip)
    gsess = rtr.manager().get(mrr.GAME_NAMESPACE, "m:g3")
    first = gsess.owner.stream.probes
    assert gsess.owner.last_media_ms == 1000

    out = _call(action="report", media_id="m:g3", video_id="abc", position_s=2.0)
    second = gsess.owner.stream.probes - first
    # One further second of media, not a re-sweep from zero.
    assert 0 < second <= 2, out
    assert gsess.owner.last_media_ms == 2000


def test_killing_the_game_session_leaves_the_music_session_playing(clip):
    _call(action="report", media_id="m:g4", video_id="abc", position_s=2.0,
          content_mode="game", video_path=clip)
    mgr = rtr.manager()
    assert mgr.close_namespace(mrr.GAME_NAMESPACE, "test") == 1
    assert mgr.get(mrr.GAME_NAMESPACE, "m:g4") is None
    music = mgr.get(mrr.NAMESPACE, "m:g4")
    assert music is not None and music.engine.closed is False
    # ...and the music path still answers.
    out = _call(action="report", media_id="m:g4", video_id="abc", position_s=3.0)
    assert out["status"] == "accepted"
    assert "game" not in out
