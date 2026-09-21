"""Session affinity + Jev-based auto-escalation for Hermes → FreeLLMAPI.

Features:
1. X-Session-Id injection — pins FreeLLMAPI to the same upstream model within
   a conversation, preventing mid-turn model jitter.
2. Difficulty-aware model escalation — uses fast regex heuristics + Jev System One
   classifier (api.typesafe.ai) to detect complex technical/coding queries and
   automatically promote from base model to DeepSeek v4.1 Flash (commandcode)
   when the request warrants it.

The escalation uses a session ratchet: once promoted within a session,
the model stays promoted for _RATCHET_TTL seconds.

Module layout (split for single-responsibility, no behavior change):
- escalation.py: regex/Jev classifier + session ratchet state
- middleware.py: the llm_request middleware entry point that ties escalation
  + FreeLLMAPI session-affinity header injection together
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PLUGIN_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from middleware import add_freellmapi_session_affinity  # noqa: E402


def register(ctx: Any) -> None:
    register_fn = getattr(ctx, "register_middleware", None)
    if callable(register_fn):
        register_fn("llm_request", add_freellmapi_session_affinity)
        logger.info("session-sticky plugin registered (llm_request middleware)")
    elif hasattr(ctx, "register_hook"):
        try:
            ctx.register_hook("llm_request", add_freellmapi_session_affinity)
            logger.info("session-sticky plugin registered (llm_request hook)")
        except ValueError:
            pass
