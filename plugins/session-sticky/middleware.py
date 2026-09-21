"""llm_request middleware: FreeLLMAPI session affinity + auto-escalation wiring."""

from __future__ import annotations

import logging
import os
from typing import Any

from escalation import (
    BASE_MODEL,
    ESCALATION_MODEL,
    ESCALATION_PROVIDER,
    extract_user_text,
    should_escalate,
)

logger = logging.getLogger(__name__)


def add_freellmapi_session_affinity(
    *,
    request: dict[str, Any],
    session_id: str = "",
    provider: str = "",
    base_url: str = "",
    **_kwargs: Any,
) -> dict[str, Any] | None:
    """LLM request middleware: session affinity + auto-escalation.

    1. Checks if the current request should be escalated (regex / Jev System One).
    2. If escalated, rewrites model/provider/base_url to commandcode.
    3. Otherwise, injects X-Session-Id for FreeLLMAPI session pinning.
    """
    rewritten = dict(request)
    current_model = str(request.get("model", "")).lower()

    # ── Auto-escalation ──────────────────────────────────────────
    is_base_model = BASE_MODEL in current_model or "solar" in current_model
    is_freellmapi = (
        provider.lower() == "freellmapi"
        or "freellmapi" in str(base_url).lower()
    )

    is_cron = session_id.startswith("cron_") or "cron" in session_id.lower()
    has_commandcode_key = bool(os.getenv("COMMANDCODE_API_KEY"))
    # Only escalate if base model (solar-pro4) AND commandcode key is explicitly configured
    if not is_cron and is_base_model and has_commandcode_key:
        user_text = extract_user_text(request)
        if user_text and should_escalate(user_text, session_id):
            rewritten["model"] = ESCALATION_MODEL
            rewritten.pop("provider", None)
            # Remove FreeLLMAPI-specific headers since we're routing elsewhere
            headers = dict(rewritten.get("extra_headers") or {})
            headers.pop("X-Session-Id", None)
            if headers:
                rewritten["extra_headers"] = headers
            else:
                rewritten.pop("extra_headers", None)
            logger.info(
                "Request escalated: %s/%s -> %s/%s (session=%s)",
                provider, current_model,
                ESCALATION_PROVIDER, ESCALATION_MODEL,
                session_id[:8] if session_id else "none",
            )
            return {"request": rewritten}

    # ── FreeLLMAPI session affinity ──────────────────────────────
    if not session_id:
        return None
    if not is_freellmapi:
        return None

    headers = dict(rewritten.get("extra_headers") or {})
    headers["X-Session-Id"] = str(session_id)
    rewritten["extra_headers"] = headers
    return {"request": rewritten}
