"""Live wiring for :class:`MusicVideoInteractionAdapter`.

What was missing
----------------
The music realtime layer had every part except a listener. ``music_realtime``
and ``music_realtime_adapter`` were reachable only from tests and the replay
rig: nothing in the live plugin ever constructed a ``RealtimeMusicCompanion``
or an adapter, and nothing ever told either one where the listener was. The
live shared-music path is hook-driven — a link arrives, the track is analysed
once, the conversation continues — which is a *batch* perception, not a
realtime one.

So this module supplies the two things activation actually needed:

``build_session``
    the factory that assembles session + Playhead + Fast Ear + Deep Ear +
    acquisition ladder into one adapter and hands it to the runtime manager;

``music_playback``
    the ingress. It is the direct analogue of ``webtoon_viewport``: the model
    calls it when the user says where they are in a track ("2절 들어갔어",
    "1분 30초쯤"), and that report moves the playhead. Everything downstream —
    ahead-buffer planning, Fast Ear windows, section/vocal/repeat detection,
    the selective Deep Ear escalation — already existed and now has an input.

Fast/deep split, concretely
---------------------------
``report`` returns as soon as the playhead has moved and the engine has been
ticked: the Fast Ear pass over the ahead-buffer is slicing a memoised
whole-file analysis (~sub-ms per window), and the Deep Ear subprocess — which
is the part that takes tens of seconds — runs on the deep lane and is never
awaited by the tool. A deep pass that is still running when the listener seeks
is cancelled by the engine's generation bump rather than returned late against
the wrong position.

Acquisition is done once per session in :meth:`MusicRealtimeSession.start`,
off the loop thread, and a failure is an honest ``heard_anything=False`` rather
than an exception — nothing here may make <AGENT_NAME> sound like it listened to
something it could not fetch.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

_PLUGIN_DIR = Path(__file__).resolve().parent
_SHARED_DIR = _PLUGIN_DIR.parent / "_shared"
for _p in (str(_PLUGIN_DIR), str(_SHARED_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hermes_realtime_engine as eng  # noqa: E402
import hermes_realtime_flags as flags  # noqa: E402
import hermes_realtime_runtime as rtr  # noqa: E402

import music_media_fetch as mfetch  # noqa: E402
import music_realtime as rt  # noqa: E402
import music_realtime_adapter as mra  # noqa: E402

logger = logging.getLogger(__name__)

NAMESPACE = mra.NAMESPACE

ACTION_REPORT = "report"
ACTION_CONTEXT = "context"
ACTION_STATUS = "status"

#: A listener who has said nothing for this long is not in a live session any
#: more. Longer than a track so a quiet outro does not tear the session down.
IDLE_TTL_S = 900.0


def _data_root() -> Path:
    return Path(os.environ.get("HERMES_DATA") or "/opt/data")


def _wiki_root() -> Path:
    return Path(os.environ.get("HERMES_WIKI_ROOT") or (_data_root() / "wiki"))


def _make_acquire(media_id: str, video_id: str, reference: str):
    """Return a zero-arg callable that yields a local audio path, or None.

    Built lazily so importing this module never touches yt-dlp's neighbourhood,
    and so a session for a track we cannot fetch costs one failed ladder walk
    rather than an import error at registration time.
    """
    def _acquire() -> str | None:
        try:
            ladder = rt.build_default_ladder(_data_root())
            source = mfetch.music_source(
                video_id, media_id_=media_id, reference=reference
            )
            result = ladder.fetch(source)
            if not result.ok or not result.local_ref:
                logger.info(
                    "music realtime acquisition failed media=%s stage=%s",
                    media_id, getattr(result, "stage", ""),
                )
                return None
            return str(result.local_ref)
        except Exception:  # noqa: BLE001 — acquisition failure is expected, not fatal
            logger.warning("music realtime acquisition raised", exc_info=True)
            return None

    return _acquire


def _make_deep_ear(media_id: str):
    def _deep(audio_path: str, start_s: float, end_s: float) -> dict | None:
        try:
            import music_deep_ear as deep

            return deep.analyze_window(
                audio_path, float(start_s), float(end_s),
                wiki_root=_wiki_root(), canonical_id=media_id,
            )
        except Exception:  # noqa: BLE001 — Fast Ear must survive Deep Ear's death
            logger.debug("deep ear window failed", exc_info=True)
            return None

    return _deep


class MusicRealtimeSession:
    """Engine + adapter + acquisition, lifecycled as one unit."""

    def __init__(
        self,
        *,
        media_id: str,
        video_id: str,
        conversation_id: str,
        platform: str = "",
        reference: str = "",
        duration_s: float | None = None,
        config: eng.EngineConfig | None = None,
    ) -> None:
        self.media_id = media_id
        self.video_id = video_id
        self.session = rt.new_music_session(
            media_id=media_id, conversation_id=conversation_id, platform=platform
        )
        self.adapter = mra.MusicVideoInteractionAdapter(
            session=self.session,
            fast_ear_fn=rt.default_fast_ear,
            deep_ear_fn=_make_deep_ear(media_id),
            acquire_fn=_make_acquire(media_id, video_id, reference),
            duration_s=duration_s,
            # No sink: reactions are recorded, never sent. Output belongs to the
            # reaction/intervention layer and is gated by FLAG_OUTPUT there.
            sink=None,
        )
        rtr.apply_lane_flags(self.adapter)
        cfg = config or eng.EngineConfig(
            perception_interval_ms=1_000,   # the playhead moves in seconds
            deep_concurrency=1,             # one Deep Ear subprocess at a time
            deep_timeout_ms=180_000,
            context_window_ms=60_000,
        )
        self.engine = eng.RealtimeInteractionEngine(
            self.session.session_id, self.adapter, config=cfg
        )
        self.acquired: bool | None = None

    async def start(self, **kw: Any) -> None:
        # Perception is left running: unlike a webtoon reader, a listener's
        # position advances by itself between reports, and the ahead-buffer is
        # what makes the next report cheap.
        await self.engine.start(**kw)
        self.acquired = await self.adapter.prepare()
        if not self.acquired:
            logger.info("music realtime session started without audio media=%s",
                        self.media_id)

    async def aclose(self, reason: str = "closed") -> dict:
        return await self.engine.aclose(reason)


def build_session(**kw: Any) -> MusicRealtimeSession:
    return MusicRealtimeSession(**kw)


# =============================================================================
# game fan-out
# =============================================================================
#
# The game adapter watches the same media session from a different angle:
# frames rather than audio windows. It is a *second engine* on the *same*
# ``MediaFrameStream``, not a second decode -- one engine holds one adapter, and
# the alternative (one engine multiplexing two adapters) would put a slow vision
# pass and a Fast Ear window in the same lane budget.
#
# Routing is explicit and one-directional: a session becomes game-shaped only
# because the caller said ``content_mode="game"``. Nothing infers it from the
# media, and there is no heuristic to misfire -- an ordinary music video cannot
# start a game session by looking action-heavy, which is the failure that would
# make <AGENT_NAME> comment on someone's boss fight during a Coldplay video.

CONTENT_MODE_MUSIC = "music"
CONTENT_MODE_GAME = "game"
GAME_NAMESPACE = "game"


class GameVideoSession:
    """GameInteractionAdapter over a shared media frame stream."""

    def __init__(self, *, media_id: str, video_path: str,
                 config: eng.EngineConfig | None = None) -> None:
        import hermes_game_adapter as ga
        import hermes_media_frames as mf

        self.media_id = media_id
        self.stream = mf.MediaFrameStream.acquire(media_id, video_path)
        self.adapter = ga.GameInteractionAdapter(
            # No vision backend and no sink: the deep lane has nothing to call
            # and the fast lane has nowhere to send. Shadow by construction, not
            # by a flag someone can forget.
            vision=None,
            sink=None,
        )
        rtr.apply_lane_flags(self.adapter)
        cfg = config or eng.EngineConfig(
            perception_interval_ms=1_000,
            deep_concurrency=1,
            deep_timeout_ms=60_000,
            context_window_ms=60_000,
        )
        self.engine = eng.RealtimeInteractionEngine(
            f"game:{media_id}", self.adapter, config=cfg
        )
        self.adapter.attach(self.engine)
        self.frames_fed = 0
        #: Media position already probed. Frames are pulled for the interval the
        #: listener just crossed, so a paused playhead costs nothing and a seek
        #: backwards re-probes the span it landed in rather than the whole file.
        self.last_media_ms = 0

    async def start(self, **kw: Any) -> None:
        await self.engine.start(**kw)

    def feed_interval(self, from_ms: int, to_ms: int, *, wall_ms: int) -> int:
        frames = self.stream.game_frames_for(from_ms, to_ms, wall_ms=wall_ms)
        for frame in frames:
            self.adapter.feed_frame(frame)
        self.frames_fed += len(frames)
        return len(frames)

    async def aclose(self, reason: str = "closed") -> dict:
        try:
            return await self.engine.aclose(reason)
        finally:
            self.stream.release()


def open_game_session(*, media_id: str, video_path: str) -> rtr.SessionHandle:
    return rtr.manager().open(
        GAME_NAMESPACE, media_id,
        lambda: GameVideoSession(media_id=media_id, video_path=video_path),
        idle_ttl_s=IDLE_TTL_S,
        timeout=60.0,
    )


def open_session(
    *, media_id: str, video_id: str, conversation_id: str, platform: str = "",
    reference: str = "", duration_s: float | None = None,
) -> rtr.SessionHandle:
    """Get or create the runtime session for this track."""
    mgr = rtr.manager()
    return mgr.open(
        NAMESPACE, media_id,
        lambda: build_session(
            media_id=media_id, video_id=video_id,
            conversation_id=conversation_id, platform=platform,
            reference=reference, duration_s=duration_s,
        ),
        idle_ttl_s=IDLE_TTL_S,
        # Acquisition walks a download ladder on first open.
        timeout=120.0,
    )


MUSIC_PLAYBACK_SCHEMA = {
    "name": "music_playback",
    "description": (
        "같이 듣는 중인 곡에서 사용자가 지금 어디를 듣고 있는지 동기화하고(report), "
        "그 지점에서 실제로 들린 것을 가져온다(context). 사용자가 '지금 후렴 들어갔어', "
        "'1분 30초쯤', '다시 처음부터' 같이 재생 위치를 말할 때 report를 써라. "
        "위치가 움직이면 그 앞부분을 미리 듣고(Fast Ear), 확신이 안 서는 구간만 "
        "느린 정밀 분석(Deep Ear)으로 따로 돌린다 — report는 기다리지 않고 바로 돌아온다. "
        "**아직 안 들은 뒷부분은 돌려주지 않는다.** 오디오를 못 받아온 곡이면 "
        "heard_anything=false로 정직하게 나오고, 그때는 들은 척하면 안 된다."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [ACTION_REPORT, ACTION_CONTEXT, ACTION_STATUS],
                "description": "report=위치 갱신, context=지금 지점 근거, status=진단",
            },
            "media_id": {
                "type": "string",
                "description": "곡의 canonical id (shared-music이 쓰는 것과 동일)",
            },
            "video_id": {
                "type": "string",
                "description": "YouTube video id — 세션을 처음 열 때 필요",
            },
            "position_s": {
                "type": "number",
                "description": "재생 위치(초). report에 필수",
            },
            "playing": {"type": "boolean", "description": "재생 중인지 (기본 true)"},
            "content_mode": {
                "type": "string",
                "enum": [CONTENT_MODE_MUSIC, CONTENT_MODE_GAME],
                "description": (
                    "이 영상이 게임 플레이 영상일 때만 'game'. 기본은 'music'이다. "
                    "**사용자가 게임 영상이라고 명시했을 때만 game으로 열어라** — "
                    "액션이 많아 보인다고 추측하지 마라. 평범한 뮤직비디오가 게임 "
                    "반응을 유발하면 안 된다."
                ),
            },
            "video_path": {
                "type": "string",
                "description": (
                    "로컬에 있는 영상 파일 경로 (예: 첨부로 받은 mp4). "
                    "content_mode=game일 때 프레임을 뽑는 원본이다."
                ),
            },
            "radius_s": {
                "type": "number",
                "description": "context에서 지금 지점 앞뒤로 볼 범위(초, 기본 20)",
            },
        },
        "required": ["action", "media_id"],
    },
}


def _report(handle: rtr.SessionHandle, args: dict) -> dict:
    if args.get("position_s") is None:
        raise ValueError("position_s is required to report a playback position")
    owner: MusicRealtimeSession = handle.owner
    adapter = owner.adapter
    position_ms = int(float(args["position_s"]) * 1000)
    playing = args.get("playing")
    playing = True if playing is None else bool(playing)

    mgr = rtr.manager()

    def _apply() -> str:
        return adapter.report_position(
            position_ms, wall_ms=int(time.time() * 1000), playing=playing
        )

    outcome = mgr.call(_apply)
    # One perception+detection pass so an explicit report is never merely
    # queued behind the next interval tick. The deep lane is not awaited.
    admitted = mgr.run(handle.engine.perception_tick(), timeout=30.0)
    handle.touch()

    payload = {
        "status": outcome,
        "position_s": round(position_ms / 1000.0, 2),
        "events_admitted": admitted,
        "heard_anything": adapter.heard_anything,
        "audio_available": bool(owner.acquired),
        "counters": adapter.counters.snapshot(),
    }

    game = mgr.get(GAME_NAMESPACE, handle.key)
    if game is not None and not game.engine.closed:
        payload["game"] = _advance_game(game, position_ms)
    return payload


def _advance_game(game: rtr.SessionHandle, position_ms: int) -> dict:
    """Pull the frames for the span just crossed and tick the game engine.

    Same playhead, same media session, one decode. The probe runs on the loop
    thread with the rest of the session's state, and the tick is not awaited by
    the deep lane, so a slow seek costs this call and nothing else.
    """
    mgr = rtr.manager()
    owner: GameVideoSession = game.owner
    wall = int(time.time() * 1000)
    previous = owner.last_media_ms

    def _feed() -> int:
        count = owner.feed_interval(previous, int(position_ms), wall_ms=wall)
        owner.last_media_ms = int(position_ms)
        return count

    try:
        fed = mgr.call(_feed, timeout=60.0)
        admitted = mgr.run(game.engine.perception_tick(), timeout=30.0)
    except Exception as exc:  # noqa: BLE001 — the music answer must survive this
        logger.warning("game fan-out failed for %s", owner.media_id, exc_info=True)
        return {"error": f"{type(exc).__name__}: {exc}"}
    game.touch()
    return {
        "frames_fed": fed,
        "frames_total": owner.frames_fed,
        "events_admitted": admitted,
        "probes": owner.stream.probes,
        "probe_misses": owner.stream.probe_misses,
        "events_emitted": owner.adapter.stats.events_emitted,
        "buffered": owner.adapter.buffered,
    }


def _context(handle: rtr.SessionHandle, args: dict) -> dict:
    owner: MusicRealtimeSession = handle.owner
    adapter = owner.adapter
    radius = float(args.get("radius_s") or 20.0)
    mgr = rtr.manager()
    artifacts = mgr.call(
        lambda: adapter.what_is_happening_now(radius_s=radius)
    )
    handle.touch()
    return {
        "status": "ok" if artifacts else "cold",
        "heard_anything": adapter.heard_anything,
        "audio_available": bool(owner.acquired),
        "artifacts": [
            {
                "producer": getattr(a, "producer", ""),
                "range_s": [
                    getattr(getattr(a, "range", None), "start_s", None),
                    getattr(getattr(a, "range", None), "end_s", None),
                ],
                "payload": getattr(a, "payload", None),
            }
            for a in artifacts
        ],
        "note": (
            "사용자가 아직 안 들은 지점의 분석은 여기 포함되지 않는다."
            if artifacts
            else "이 지점은 아직 들은 게 없다 — 들은 척하지 마라."
        ),
    }


def _status(handle: rtr.SessionHandle) -> dict:
    owner: MusicRealtimeSession = handle.owner
    return {
        "status": "ok",
        "media_id": owner.media_id,
        "engine_session_id": handle.engine.session_id,
        "audio_available": bool(owner.acquired),
        "heard_anything": owner.adapter.heard_anything,
        "metrics": owner.adapter.realtime_metrics(handle.engine),
    }


def make_handler(_ctx):
    def handler(args: dict, **_kwargs) -> str:
        args = args or {}
        action = str(args.get("action") or "").strip()
        media_id = str(args.get("media_id") or "").strip()
        if action not in (ACTION_REPORT, ACTION_CONTEXT, ACTION_STATUS):
            return json.dumps({"error": f"unknown action: {action!r}"})
        if not media_id:
            return json.dumps({"error": "media_id is required"})
        if not flags.enabled(flags.FLAG_MUSIC):
            return json.dumps({
                "error": "realtime music/video is disabled",
                "hint": f"set {flags.env_name(flags.FLAG_MUSIC)}=1 to enable",
            })

        try:
            handle = rtr.manager().get(NAMESPACE, media_id)
            if handle is None or handle.engine.closed:
                video_id = str(args.get("video_id") or "").strip()
                if not video_id:
                    return json.dumps({
                        "error": "no live session for this track",
                        "hint": "pass video_id to open one",
                    }, ensure_ascii=False)
                handle = open_session(
                    media_id=media_id, video_id=video_id,
                    conversation_id=str(_kwargs.get("session_id") or ""),
                    platform=str(_kwargs.get("platform") or ""),
                )

            # Game fan-out: explicit mode, explicit file, explicit flag. Any of
            # the three missing means this stays an ordinary music session.
            mode = str(args.get("content_mode") or CONTENT_MODE_MUSIC).strip()
            video_path = str(args.get("video_path") or "").strip()
            if mode == CONTENT_MODE_GAME and video_path \
                    and flags.enabled(flags.FLAG_GAME) \
                    and rtr.manager().get(GAME_NAMESPACE, media_id) is None:
                try:
                    open_game_session(media_id=media_id, video_path=video_path)
                except Exception:  # noqa: BLE001 — no frames is not no music
                    logger.warning("game fan-out could not open for %s", media_id,
                                   exc_info=True)
            if action == ACTION_REPORT:
                payload = _report(handle, args)
            elif action == ACTION_CONTEXT:
                payload = _context(handle, args)
            else:
                payload = _status(handle)
        except rtr.RuntimeDisabled as exc:
            return json.dumps({"error": str(exc)})
        except ValueError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001 — a playback call must never break a turn
            logger.warning("music_playback %s failed", action, exc_info=True)
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"},
                              ensure_ascii=False)
        return json.dumps(payload, ensure_ascii=False, default=str)

    return handler


def register(ctx) -> None:
    ctx.register_tool(
        "music_playback",
        "shared-music",
        MUSIC_PLAYBACK_SCHEMA,
        make_handler(ctx),
        description=MUSIC_PLAYBACK_SCHEMA["description"],
    )


__all__ = [
    "ACTION_CONTEXT",
    "ACTION_REPORT",
    "ACTION_STATUS",
    "IDLE_TTL_S",
    "MUSIC_PLAYBACK_SCHEMA",
    "MusicRealtimeSession",
    "NAMESPACE",
    "build_session",
    "make_handler",
    "open_session",
    "register",
]
