"""The `music_deep_dive` tool — Lazy Deep Analysis escalation (task item 10).

Extracted out of `__init__.py` alongside `music_turn_hooks.py` so the package
entrypoint stays a thin `register(ctx)`. Behavior is unchanged.
"""
from __future__ import annotations

import music_cache
import music_deep_dive
import music_deep_ear as deep_ear
import music_prompt
import music_youtube_audio as yta

from music_turn_hooks import _data_root, _get_store, _trace, _wiki_root

MUSIC_DEEP_DIVE_SCHEMA = {
    "name": "music_deep_dive",
    "description": (
        "지금 같이 듣고 있는 곡(활성 Music Conversation Session)의 특정 구간을 실제 stem "
        "분리(보컬/드럼/베이스/그 외)까지 포함해 더 정밀하게 재분석한다. 압축된 perception/"
        "window 근거만으로는 답하기 어려운 질문 — 악기/드럼/코드/멜로디/보컬/프로덕션처럼 "
        "구체적인 질문일 때만 호출할 것. 매 턴 기본으로 호출하지 말 것. aspect를 질문 종류에 "
        "맞게 지정하면 그 부분만 집중해서 보여줌. 이 도구를 써도 여전히 정확한 악기명이나 "
        "코드 보이싱까지 확정하지는 못할 수 있다 — 그럴 땐 결과의 confidence/certainty를 보고 "
        "솔직하게 hedge할 것."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "time_s": {"type": "number", "description": "질문이 가리키는 대략적인 시점(초)"},
            "start_s": {"type": "number", "description": "직접 지정할 구간 시작(초), time_s 대신 사용 가능"},
            "end_s": {"type": "number", "description": "직접 지정할 구간 끝(초)"},
            "aspect": {
                "type": "string",
                "enum": ["instrumentation", "rhythm", "harmony", "melody", "vocal", "production", "general"],
                "description": "질문이 뭘 묻는지 — 악기->instrumentation, 드럼/리듬->rhythm, 코드/화음->harmony, "
                "멜로디->melody, 보컬->vocal, 공간감/믹싱->production, 애매하면 general",
            },
        },
        "required": [],
    },
}

_ASPECT_TO_CATEGORY = {
    "instrumentation": ["instrumentation"],
    "rhythm": ["rhythm"],
    "harmony": ["harmony"],
    "melody": ["melody"],
    "vocal": ["vocal", "melody"],
    "production": ["production"],
    "general": None,  # None -> show everything
}


def _resolve_deep_dive_window(active, args: dict) -> "tuple[float, float] | str":
    """Returns (start_s, end_s) or an error message string."""
    start_s = args.get("start_s")
    end_s = args.get("end_s")
    if start_s is not None and end_s is not None:
        return float(start_s), float(end_s)

    time_s = args.get("time_s")
    if time_s is not None:
        return max(0, time_s - 6), time_s + 6

    cache = music_cache.load(_wiki_root(), active.canonical_id)
    sections = (cache or {}).get("perception", {}).get("sections", [])
    if not sections or active.last_focus_section_idx is None or active.last_focus_section_idx >= len(sections):
        return "어느 구간인지 특정할 수 없어서 다시 들어볼 수 없음 — 시간대를 알려줘야 함."
    s = sections[active.last_focus_section_idx]
    return float(s["start_s"]), float(s["end_s"])


def _make_music_deep_dive_handler(_ctx):
    def handler(args: dict, **kwargs) -> str:
        args = args or {}
        conversation_id = kwargs.get("session_id") or kwargs.get("conversation_id") or ""
        store = _get_store()
        active = store.get_active(conversation_id) if conversation_id else None
        if not active or not active.video_id:
            return "지금 활성화된 음악 감상 세션이 없어서 구간을 다시 들어볼 수가 없음."

        window = _resolve_deep_dive_window(active, args)
        if isinstance(window, str):
            return window
        start_s, end_s = window

        try:
            audio_path = yta.resolve_audio(_data_root(), active.video_id, trace_fn=_trace)
        except yta.AudioResolveError:
            return "오디오를 다시 확보하지 못해서 그 구간을 다시 들어볼 수 없었음."

        aspect = args.get("aspect") or "general"
        categories = _ASPECT_TO_CATEGORY.get(aspect)
        _trace(
            "deep_analysis_reason", conversation_id=conversation_id, canonical_id=active.canonical_id,
            window=[start_s, end_s], aspect=aspect,
        )

        observation = deep_ear.analyze_window(
            str(audio_path), start_s, end_s,
            wiki_root=_wiki_root(), canonical_id=active.canonical_id,
            tmp_dir=str(_data_root() / "tmp" / "shared-music"), trace_fn=_trace,
        )
        if observation:
            interpretation = deep_ear.interpret(observation)
            active.shared_observations.append(
                f"{start_s:.0f}-{end_s:.0f}s deep listen: {', '.join(interpretation.keys()) or 'no strong combined pattern'}"
            )
            store.save(active)
            return music_prompt.build_deep_ear_result_text(observation, interpretation, categories)

        # Deep Ear unavailable/failed -> fall back to Phase 1's lightweight,
        # always-local HPSS/band-energy dive rather than a bare failure
        # (task rule 34: Fast Ear's own deep-dive path must still work).
        evidence = music_deep_dive.deep_dive_window(str(audio_path), start_s, end_s)
        if not evidence:
            return "그 구간을 다시 분석했는데 뚜렷한 근거를 못 찾았음 — 확실하지 않은 건 확실하지 않다고 말하는 게 나을 듯."
        active.shared_observations.append(f"{start_s:.0f}-{end_s:.0f}s: {evidence['texture_note']}")
        store.save(active)
        return music_prompt.build_deep_dive_result_text(evidence)

    return handler
