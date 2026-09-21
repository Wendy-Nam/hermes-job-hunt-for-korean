"""shared-music plugin — "함께 듣는 음악 / Shared Music Archive".

Fast Path (default, zero extra API cost):
  link + music intent -> metadata resolve -> audio resolve/cache -> local
  HTF-idea DSP -> compact Music Perception Object -> injected as per-turn
  context so the *existing* conversational LLM call produces the actual
  listening impression itself (this plugin never makes its own LLM call to
  pre-generate an impression — the main turn's response IS the impression).

Lazy Deep Analysis (task item 10): the `music_deep_dive` tool, called by the
model only when a question needs more than the compact perception/window
evidence can honestly support.

Hook wiring mirrors youtube-archive's pre_llm_call/post_llm_call shape:
pre_llm_call detects triggers/follow-ups and injects context; post_llm_call
captures the turn into session state and checks for session finalization.
Isolation (cron/subagent turns never touch session state) copies
youtube-archive's exact platform filter.

The actual hook bodies live in `music_turn_hooks.py` and the
`music_deep_dive` tool lives in `music_deep_dive_tool.py` — this module is
just wiring (sys.path bootstrap + `register(ctx)`).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

# numpy/scipy/soundfile/pyyaml/yt-dlp are NOT part of the stock hermes-agent
# image and were found to NOT survive a container recreation (only bind
# mounts under HERMES_DATA do) — installing them into /opt/hermes/.venv
# worked until the very next unrelated container recreation silently wiped
# them (found the hard way: Fast Ear ImportErrored in prod after another
# concurrent session's core-patch deploy recreated the container). Vendored
# here instead (`uv pip install --target .vendor ...`, cp313 wheels) so Fast
# Ear's dependencies live inside this bind-mounted plugin directory itself
# and survive any future recreation with zero reinstall step.
_VENDOR_DIR = _PLUGIN_DIR / ".vendor"
if _VENDOR_DIR.is_dir() and str(_VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(_VENDOR_DIR))

# Isolation classes live in one place — plugins/_shared/hermes_traffic.py —
# so a new non-user execution context (this is how "cli" was missed) is a
# one-line change there rather than an edit to every plugin that learns.
_SHARED_DIR = str(Path(__file__).resolve().parent.parent / "_shared")
if _SHARED_DIR not in sys.path:
    sys.path.insert(0, _SHARED_DIR)

from music_turn_hooks import on_pre_llm_call, on_post_llm_call  # noqa: E402
from music_deep_dive_tool import (  # noqa: E402
    MUSIC_DEEP_DIVE_SCHEMA,
    _make_music_deep_dive_handler,
)

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    # The realtime listening path is additive: it adds an ingress for "where
    # the listener is right now", and the batch link->analysis path below keeps
    # working untouched whether it registers or not.
    try:
        import hermes_realtime_flags as _rt_flags
        import music_realtime_runtime as _rt_runtime

        if _rt_flags.enabled(_rt_flags.FLAG_ENGINE) and \
                _rt_flags.enabled(_rt_flags.FLAG_MUSIC):
            _rt_runtime.register(ctx)
            logger.info("shared-music realtime playback ingress registered")
    except Exception:  # noqa: BLE001 — never required
        logger.warning("shared-music realtime layer not registered (non-fatal)",
                       exc_info=True)

    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("post_llm_call", on_post_llm_call)
    ctx.register_tool(
        "music_deep_dive", "shared-music", MUSIC_DEEP_DIVE_SCHEMA, _make_music_deep_dive_handler(ctx),
        description=MUSIC_DEEP_DIVE_SCHEMA["description"],
    )
    logger.info("shared-music plugin registered (pre_llm_call, post_llm_call, music_deep_dive)")
