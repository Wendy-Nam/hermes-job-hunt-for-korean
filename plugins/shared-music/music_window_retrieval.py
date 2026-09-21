"""Follow-up-question-scoped retrieval (task item 9): a message referencing a
specific moment or section pulls only that window out of the cached
raw_index/perception, never the whole object again.

Two reference styles are supported:
  - an explicit timestamp ("2분 14초", "1:23", "73초") -> a small window
    around that second.
  - a coarse structural word (도입/후렴/아웃트로/브릿지/직전/다음) mapped onto
    the DSP-visible energy phases. This is an approximate best-effort
    mapping, not verified verse/chorus alignment — HTF-style energy
    segmentation has no lyrics timing, so "2절" for example can't be
    reliably distinguished from "후렴" without vocal/lyric alignment this
    plugin doesn't have. That gap is surfaced internally (not to the user)
    via the returned `approximate: True` flag so the prompt layer can hedge
    ("이 부분 말하는 거 맞나?") instead of asserting a section it isn't sure of.
"""
from __future__ import annotations

import re

_TIMESTAMP_MMSS_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_TIMESTAMP_KOR_RE = re.compile(r"(\d+)\s*분\s*(\d+)?\s*초?")
_TIMESTAMP_SEC_ONLY_RE = re.compile(r"(\d+)\s*초(?:\s*(?:쯤|경|부근|근처))?")

_STRUCTURAL_WORDS = {
    "도입": "first",
    "인트로": "first",
    "시작": "first",
    "후렴": "peak",
    "클라이맥스": "peak",
    "드랍": "peak",
    "하이라이트": "peak",
    "아웃트로": "last",
    "엔딩": "last",
    "마지막": "last",
    "브릿지": "bridge",
}
_RELATIVE_WORDS = {
    "직전": -1,
    "그전": -1,
    "바로전": -1,
    "다음": 1,
    "그다음": 1,
    "이후": 1,
}

WINDOW_RADIUS_S = 8


def parse_timestamp_s(text: str) -> int | None:
    if not text:
        return None
    m = _TIMESTAMP_MMSS_RE.search(text)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = _TIMESTAMP_KOR_RE.search(text)
    if m and ("분" in text):
        minutes = int(m.group(1))
        seconds = int(m.group(2)) if m.group(2) else 0
        return minutes * 60 + seconds
    m = _TIMESTAMP_SEC_ONLY_RE.search(text)
    if m:
        return int(m.group(1))
    return None


def parse_structural_reference(text: str) -> str | None:
    if not text:
        return None
    for word, tag in _STRUCTURAL_WORDS.items():
        if word in text:
            return tag
    return None


def parse_relative_reference(text: str) -> int | None:
    if not text:
        return None
    for word, direction in _RELATIVE_WORDS.items():
        if word in text:
            return direction
    return None


def _fmt_mmss(seconds: int) -> str:
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def window_by_timestamp(cache: dict, time_s: int, radius_s: int = WINDOW_RADIUS_S) -> dict:
    raw = cache.get("raw_index", {})
    energy = raw.get("energy_1hz", [])
    brightness = raw.get("brightness_1hz", [])
    n = len(energy)
    lo = max(0, time_s - radius_s)
    hi = min(n - 1, time_s + radius_s) if n else time_s
    perception = cache.get("perception", {})
    sections = perception.get("sections", [])
    containing_idx = next((i for i, s in enumerate(sections) if s["start_s"] <= time_s < s["end_s"]), None)
    containing = sections[containing_idx] if containing_idx is not None else None
    moments = [m for m in perception.get("salient_moments", []) if lo <= m["time_s"] <= hi]
    return {
        "window": f"{_fmt_mmss(lo)}-{_fmt_mmss(hi)}",
        "center_s": time_s,
        "section": containing,
        "section_idx": containing_idx,
        "nearby_salient_moments": moments,
        "energy_trend_in_window": _trend_word(energy[lo : hi + 1]) if energy else "unknown",
        "brightness_trend_in_window": _trend_word(brightness[lo : hi + 1]) if brightness else "unknown",
        "approximate": False,
    }


def _trend_word(values: list[float]) -> str:
    if len(values) < 2:
        return "steady"
    delta = values[-1] - values[0]
    spread = (max(values) - min(values)) + 1e-9
    if abs(delta) < spread * 0.2:
        return "steady"
    return "rising" if delta > 0 else "falling"


def window_by_structural_tag(cache: dict, tag: str) -> dict | None:
    perception = cache.get("perception", {})
    sections = perception.get("sections", [])
    if not sections:
        return None
    if tag == "first":
        section = sections[0]
    elif tag == "last":
        section = sections[-1]
    elif tag == "peak":
        section = max(sections, key=lambda s: {"low": 0, "medium": 1, "high": 2}[s["energy_tier"]])
    elif tag == "bridge" and len(sections) >= 3:
        section = sections[-2]
    else:
        return None
    mid = (section["start_s"] + section["end_s"]) // 2
    result = window_by_timestamp(cache, mid, radius_s=(section["end_s"] - section["start_s"]) // 2 or WINDOW_RADIUS_S)
    result["approximate"] = True
    return result


def resolve_window(cache: dict, user_message: str, *, last_focus_section_idx: int | None = None) -> dict | None:
    ts = parse_timestamp_s(user_message)
    if ts is not None:
        return window_by_timestamp(cache, ts)

    tag = parse_structural_reference(user_message)
    if tag:
        return window_by_structural_tag(cache, tag)

    direction = parse_relative_reference(user_message)
    if direction is not None and last_focus_section_idx is not None:
        sections = cache.get("perception", {}).get("sections", [])
        idx = last_focus_section_idx + direction
        if 0 <= idx < len(sections):
            s = sections[idx]
            mid = (s["start_s"] + s["end_s"]) // 2
            result = window_by_timestamp(cache, mid, radius_s=(s["end_s"] - s["start_s"]) // 2 or WINDOW_RADIUS_S)
            result["approximate"] = True
            return result
    return None
