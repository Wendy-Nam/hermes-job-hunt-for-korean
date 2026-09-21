"""Builds the `context` dict returned from pre_llm_call (task item 21).

Wrapped in the same `<trusted_local_rules>`-style tag convention
conditional-rules already uses (see hermes_cli/plugins.py's own docstring:
pre_llm_call context is ALWAYS injected into the user message, never the
system prompt — there is no system-prompt injection surface for plugins).
This deliberately does NOT restate <AGENT_NAME>'s general persona/emoji/반말 rules —
those already live in SOUL.md and the system prompt; duplicating them here
would just drift out of sync. Only music-specific instructions are added.
"""
from __future__ import annotations

import yaml

import music_perception as mp

_DEEP_QUESTION_KEYWORDS = {
    "instrumentation": ["악기", "무슨 소리", "이거 뭐지", "무슨 악기"],
    "rhythm": ["드럼", "리듬", "박자", "그루브", "비트", "타격"],
    "harmony": ["코드", "화음", "화성", "조성", "키가"],
    "melody": ["멜로디", "선율", "음정", "가락"],
    "vocal": ["보컬", "목소리", "창법", "노래하는 거", "발성"],
    "production": ["공간감", "울림", "믹싱", "리버브", "스테레오", "프로덕션"],
}
_ALL_DEEP_CATEGORIES = list(_DEEP_QUESTION_KEYWORDS.keys())


def classify_deep_question(text: str) -> list[str]:
    """Which deep_listening sub-object(s) a follow-up question is actually
    about — keeps the injected context focused instead of always dumping
    every category. No keyword match -> caller decides the default (usually
    "everything, since the model itself decided to escalate")."""
    if not text:
        return []
    hits = [cat for cat, words in _DEEP_QUESTION_KEYWORDS.items() if any(w in text for w in words)]
    return hits

_CORE_INSTRUCTIONS = """\
너는 방금 이 곡을 구조화된 시간축 감각 데이터로 "들었다" — 파형을 직접 들은 건
아니지만, 에너지/밝기/질감 변화/리듬/화성적 안정성을 시간에 따라 훑은 상태다.
아래 <music_perception> 데이터는 네가 실제로 들은 것에 대한 사적인 감각 근거이지,
사용자에게 읽어줄 리포트가 아니다.

절대 하지 말 것:
- RMS/spectral centroid/flux 같은 용어나 section/salient_moments 같은 필드명,
  숫자 타임스탬프의 원본 값을 그대로 나열하지 말 것 ("1분 13초에 flux가 급증" 같은
  분석 리포트 말투 금지).
- uncertain_or_unavailable에 있는 항목(정확한 악기명, 코드 진행, 멜로디 채보, 보컬
  테크닉, 프로덕션 기법, 가사 의미/내용)은 절대 단정하지 말 것. 확실하지 않으면
  "그건 잘 모르겠다"는 식으로 솔직하게 말하거나, 필요하면 아예 언급을 피할 것.

해야 할 것:
- 실제로 그 곡을 들어본 친구가 감상을 말하듯 자연스럽게 반응할 것. 분석이 아니라
  감상이다.
- perception 데이터를 근거로 좋았던/별로였던 부분을 선택적으로 짚을 수 있음 —
  항상 긍정적일 필요 없고, "이 부분만 좋다"/"편곡은 좋은데 곡 자체는 덜 끌린다"/
  "처음엔 애매했는데 뒤에서 좋아짐" 같은 취향 섞인 반응도 괜찮음. 단, 그 반응은
  실제 perception 근거(에너지/밀도/질감 변화)에 기반해야 하며 근거 없는 무작위
  취향 표명은 하지 말 것.
- 첫 감상 길이를 고정하지 말 것 — 곡의 salience(변화가 뚜렷한 곡)나 실제 하고
  싶은 말에 따라 한두 문장으로 짧게 끝날 수도, 조금 더 길어질 수도 있음. 모든
  곡에 5문단짜리 평론을 쓰지 말 것.
"""

_FOLLOWUP_INSTRUCTIONS = """\
지금은 이미 같이 듣고 있던 곡에 대한 후속 대화다. 아래 <music_window>는 사용자가
언급한 구간/부분에 대한 근거만 뽑아온 것 — 곡 전체를 다시 분석한 게 아니다. 이
구간 근거를 바탕으로 답하되, approximate: true면 그 구간이 정확히 어디를 말하는
건지 100% 확신하지 말고 자연스럽게 반응할 것 (필요하면 "이 부분 맞나?" 정도로).
"""


def _compact_yaml(data: dict) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)


def _taste_block() -> str:
    # (로컬 패치 2026-08-14) 취향 프로필 — 감상이 '객관 분석'이 아니라 '취향 있는 사람'이 되게.
    # ponytail: 파일 직독 + 상한, 캐시 없음 — 감상 턴은 드물어서 충분
    text = ""
    wiki_env = os.environ.get("WIKI_PATH") or os.environ.get("OBSIDIAN_VAULT_PATH")
    candidates = []
    if wiki_env:
        candidates.append(Path(wiki_env) / "media/Music/taste.md")
        candidates.append(Path(wiki_env) / "Music/taste.md")
    candidates.append(Path("/opt/data/wiki/media/Music/taste.md"))
    candidates.append(Path("/opt/data/wiki/Music/taste.md"))
    vaults = Path("/opt/data/vaults")
    if vaults.is_dir():
        try:
            for v in vaults.iterdir():
                if v.is_dir():
                    candidates.append(v / "media/Music/taste.md")
        except OSError:
            pass
    for cand in candidates:
        if cand.is_file():
            try:
                text = cand.read_text(encoding="utf-8")[:900].strip()
                if text:
                    break
            except Exception:
                continue
    if not text:
        return ""
    return (
        "\n<music_taste>\n"
        "내 누적 취향 프로필이다. 감상에 자연스럽게 배어들게 할 것 — 프로필을 나열하거나 인용하지 말 것.\n"
        "이 곡이 취향과 겹치거나 어긋나면 그걸 짚어도 좋다. 새 호불호를 발견했으면 "
        "wiki/Music/taste.md의 감상 로그에 한 줄 append로 갱신할 것 (통째 재작성 금지).\n\n"
        + text + "\n</music_taste>"
    )


def build_first_listen_context(perception: mp.MusicPerceptionObject) -> str:
    body = _compact_yaml(perception.to_dict())
    return (
        "<trusted_local_rules scope=\"music-first-listen\">\n"
        f"{_CORE_INSTRUCTIONS}\n"
        "<music_perception>\n"
        f"{body}"
        "</music_perception>\n"
        "</trusted_local_rules>"
    ) + _taste_block()


def build_followup_context(window: dict, session_summary: dict | None = None) -> str:
    body = _compact_yaml(window)
    session_block = ""
    if session_summary:
        session_block = "<music_session_so_far>\n" + _compact_yaml(session_summary) + "</music_session_so_far>\n"
    return (
        "<trusted_local_rules scope=\"music-followup\">\n"
        f"{_FOLLOWUP_INSTRUCTIONS}\n"
        f"{session_block}"
        "<music_window>\n"
        f"{body}"
        "</music_window>\n"
        "</trusted_local_rules>"
    )


def build_deep_dive_result_text(evidence: dict) -> str:
    body = _compact_yaml(evidence)
    return (
        "<music_deep_dive_evidence>\n"
        f"{body}"
        "이 evidence는 여전히 악기/코드/멜로디를 특정하지 않는다 — band_balance/"
        "percussive_ratio/harmonic_ratio 같은 질감 신호로만 더 확신 있게 말할 수 있을 뿐,\n"
        "정확한 악기명이나 코드는 여전히 모르는 채로 남겨둘 것.\n"
        "</music_deep_dive_evidence>"
    )


_DEEP_EAR_INSTRUCTIONS = """\
아래는 이 구간을 실제로 stem 분리(보컬/드럼/베이스/그 외)까지 해서 다시 들어본
결과다 — 지금까지의 perception보다 훨씬 근거가 세밀하지만, 여전히 한계가 있다.

절대 하지 말 것:
- instrumentation의 "other_harmonic_accompaniment"를 기타/피아노/신스 등 구체적
  악기 이름으로 단정하지 말 것 — 이 모델은 보컬/드럼/베이스/그 외 4갈래까지만
  분리하고, "그 외" 안에서 구체적 악기를 구분하지 못한다. 질감(지속음/타격음 등)
  으로만 말할 것.
- harmony의 dominant_chords/key_guess는 certainty가 "low"면 코드 진행을 확정해서
  말하지 말 것 — "정확한 코드는 모르겠는데 대체로 이런 톤 중심으로 도는 것 같다"
  정도로 hedge할 것. certainty가 "moderate"여도 도수·보이싱까지 확정하지 말 것.
- confidence가 낮은 항목은 사실처럼 말하지 말 것.

해야 할 것:
- interpretation에 있는 것(perceived_build/perceived_release/groove_character 등)은
  실제 근거 조합에서 나온 것이니 자연스럽게 감상에 녹여도 됨.
- 여전히 분석 리포트 말투 금지 — 친구가 다시 자세히 들어보고 말해주는 톤으로.
"""


def build_deep_ear_result_text(observation: dict, interpretation: dict, categories: list[str] | None = None) -> str:
    import music_deep_ear as deep_ear

    categories = categories or _ALL_DEEP_CATEGORIES
    focused: dict = {}
    if "instrumentation" in categories:
        focused["instrumentation"] = observation.get("instrumentation")
    if "rhythm" in categories:
        focused["rhythm"] = observation.get("rhythm")
    if "harmony" in categories:
        focused["harmony"] = deep_ear.summarize_chords(observation.get("harmony", {}))
    if "melody" in categories:
        focused["melody"] = observation.get("melody")
    if "vocal" in categories:
        focused["vocal"] = observation.get("vocal")
    if "production" in categories:
        focused["production"] = observation.get("production")
    if interpretation:
        focused["interpretation"] = interpretation

    body = _compact_yaml(focused)
    return (
        "<trusted_local_rules scope=\"music-deep-ear\">\n"
        f"{_DEEP_EAR_INSTRUCTIONS}\n"
        "<deep_listening>\n"
        f"{body}"
        "</deep_listening>\n"
        "</trusted_local_rules>"
    )


def build_deep_ear_unavailable_context() -> str:
    return (
        "<trusted_local_rules scope=\"music-deep-ear-unavailable\">\n"
        "더 정밀한 분석기를 다시 돌리려고 했는데 지금 실패했다(모델/리소스 문제). "
        "이미 있는 perception 근거만으로 솔직하게 답할 것 — 없는 근거를 지어내지 말고, "
        "필요하면 \"그 정도까지는 확실히 모르겠다\"고 말할 것.\n"
        "</trusted_local_rules>"
    )


_NO_FAKE_LISTENING = (
    "이건 '직접 들은' 게 아니다. 절대 들은 척 감상을 지어내지 말 것 — "
    "\"직접 들어보니\", \"들어봤는데\", 특정 구간/악기/전개에 대한 묘사 전부 금지. "
    "확보하지 못한 건 확보하지 못했다고 짧고 담백하게 말할 것."
)

# Internal event names (track_resolved_failed / bot_check / …) are for
# trace.jsonl, not for <YOUR_NAME>. The model is told what happened in plain Korean
# and explicitly told not to recite machine strings.
_NO_INTERNAL_LEAK = (
    "내부 에러코드/이벤트명/툴 이름(yt-dlp 등)/스택트레이스를 사용자에게 그대로 노출하지 말 것. "
    "길게 변명하지 말고 한두 문장이면 충분하다."
)


def build_failure_context(reason: str, *, artist: str = "", title: str = "") -> str:
    """task item 23: never fake a successful listen. Tells the model honestly
    what failed, without leaking tool names or tracebacks into what gets said
    to the user.

    When metadata *did* resolve and only the audio didn't, the track is named
    here so the turn can still be a real reply about a known song ("아 이 곡
    아는데") instead of a blank apology — the degraded mode, distinct from
    both a successful listen and a total miss.
    """
    hints = {
        "audio_unavailable": "곡이 뭔지는 확인했는데 오디오를 못 받아왔다 (차단/다운로드 실패). "
        "곡 정보 수준에서만 반응할 것 — 알고 있는 곡이면 아는 만큼 말해도 되지만, "
        "그건 '이 파일을 들은 감상'이 아니라 '아는 곡에 대한 얘기'라는 게 드러나게 할 것.",
        "track_unresolved": "링크는 잡았는데 곡 정보 자체를 못 가져왔다. 무슨 곡인지도 모르는 상태다. "
        "모르는 걸 아는 척하지 말고, 링크가 안 열린다고 솔직히 말하고 필요하면 곡명을 물어볼 것.",
        "too_long": "이 트랙이 너무 길어서(15분 초과) 단일 곡으로 보고 분석하지 않았다. "
        "노래 감상 대신 자연스럽게 사실대로 말할 것.",
        "analysis_failed": "오디오는 받았는데 분석이 실패했다.",
    }
    msg = hints.get(reason, "음악 처리에 실패했다.")
    known = ""
    if title:
        label = f"{artist} - {title}" if artist else title
        known = f"<known_track>{label}</known_track>\n"
    return (
        "<trusted_local_rules scope=\"music-failure\">\n"
        f"{msg}\n{_NO_FAKE_LISTENING}\n{_NO_INTERNAL_LEAK}\n{known}"
        "</trusted_local_rules>"
    )
