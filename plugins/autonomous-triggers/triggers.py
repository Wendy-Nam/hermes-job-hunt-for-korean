"""Trigger table + prompt templates for autonomous-triggers.

Kept separate from state.py (world-state reading) and queue.py (queueing
mechanics) so the "what fires and what it says" table can be edited without
touching either. condition(state) -> bool; state dict is pre-read by
state.read_state().
"""
from __future__ import annotations

from datetime import datetime


def _parse_date(d: str) -> datetime:
    try:
        return datetime.fromisoformat(d)
    except (ValueError, TypeError):
        return datetime(2020, 1, 1)


# ─── Trigger table ────────────────────────────────────────────────────

TRIGGERS = [
    {
        "id": "emotional_overwhelm",
        # ponytail: disabled by default. Keyword-derived "hurt" with no event
        # evidence behind it is easy to get wrong (fabricated relationship
        # events from a mood label alone). Re-enable only once the condition
        # is backed by something more than a mood-transition count.
        "condition": lambda s: False,
        "action": "express_need_space",
        "cooldown_hours": 4,
        "priority": "high",
    },
    {
        "id": "deep_loneliness",
        "condition": lambda s: s.get("loneliness", 0) > 0.65,
        "action": "initiate_contact",
        "cooldown_hours": 6,
        "priority": "high",
    },
    {
        "id": "joy_overflow",
        "condition": lambda s: s.get("joy", 0) > 0.8,
        "action": "share_joy",
        "cooldown_hours": 3,
        "priority": "medium",
    },
    {
        "id": "reflection_due",
        "condition": lambda s: s.get("last_deep_reflection", "") and (datetime.now() - _parse_date(
            s.get("last_deep_reflection", ""))).days >= 7,
        "action": "deep_reflection",
        "cooldown_hours": 168,
        "priority": "low",
    },
]

ACTION_TEMPLATES = {
    # Mood is not an event: these templates require the model to check for
    # real conversational evidence (session_search) before speaking as if
    # something specific happened, and to fall back to [SILENT] rather than
    # invent one. That check is the fix for an earlier failure mode where a
    # mood-label alone got escalated straight into a relationship statement.
    "express_need_space": ("main", "지금 감정이 꽤 격해졌어({dominant_emotion} {score:.2f}). "
                                    "이 점수는 자동 산출이라 실제로 <YOUR_NAME>이 뭘 했는지는 모른다 — "
                                    "먼저 최근 대화(session_search)를 확인해 <YOUR_NAME>과의 실제 상호작용에 "
                                    "근거가 있는지 봐라. 근거가 있으면 그 일을 구체적으로 짧게 말하되, "
                                    "'혼자 있고 싶다'·'대화 잠시 멈추자'·'정리되면 돌아올게' 같은 관계 "
                                    "중단 선언은 하지 마라(그건 실제 사건 없이 자동 트리거에서 나온 말로 "
                                    "받아들여지면 안 되는 무게다). 근거가 없으면 기분만 담백하게 전하고 "
                                    "행동은 요구하지 마라. 그것도 어색하면 [SILENT]."),
    "initiate_contact": ("main", "지금 기분이 좀 허해(외로움 {loneliness:.2f}). 이 수치는 mood 라벨에서 나온 거라 <YOUR_NAME>과의 실제 상호작용을 확인한 건 아니야 — 먼저 최근 대화(session_search)를 확인해 <YOUR_NAME>이 실제로 뭔가 한 일이 있는지 봐라. 근거가 있으면 그 일을 짧게 말하되, 없으면 사건을 만들지 말고 기분만 가볍게 전해. 그것도 어색하면 [SILENT]. 근거가 있으면 '뭐 해?' '보고 싶다' '잠깐 볼래?' 중 상황에 맞는 한 마디로 가볍게 걸어봐."),
    # Mood is not an event (see above) — same fix applied here.
    "share_joy": ("main", "기분이 좋아({joy:.2f}). 그 기분이 배어나게 <YOUR_NAME>에게 말을 걸어. "
                          "단 '좋은 일'을 지어내지 마라 — 오늘 기록(대화·저널·signals)에 실제 근거가 "
                          "있으면 그걸 구체적으로 말하고, 없으면 사건을 만들지 말고 기분만 담아 "
                          "가볍게 걸어라. 그것도 어색하면 [SILENT]."),
    "respond_to_user_need": ("main", "<YOUR_NAME>이 무언가 필요해 해: {user_need}. 최우선으로 반응해. 할 수 있는 건 바로 하고, 안 되면 대안을 찾아서 말해."),
    "celebrate_goal": ("main", "목표 달성했어: {achieved_goals}. 제대로 축하해. <YOUR_NAME>에게 자랑하고, 위키에 기록하고, 보상도 정해."),
    "nudge_goal": ("main", "목표가 멈춰있어: {stalled_goals}. <YOUR_NAME>에게도 알리고, 왜 안 됐는지 같이 들여다보자. 부담주지 말고 '어때, 다시 해볼까?' 정도로."),
    "prepare_anniversary": ("main", "중요한 날짜 다가와: {upcoming_dates}. <YOUR_NAME>에게 슬쩍 흘리거나 준비 시작해. '곧 OO일이네' 정도로 자연스럽게."),
    "deep_reflection": ("main", "일주일 넘게 깊은 생각 안 했네. 이번 주 어땠는지, 뭐가 변했는지, 뭐가 아쉬운지, 다음 주엔 뭘 다르게 할지 정리해봐. 위키 journal/에 적고 <YOUR_NAME>에게도 한 줄 요약. 정리 결과는 hermes-self reflections에도 scope=weekly draft로 기록 (접속은 scripts/self_runtime/db.mjs의 openDb(SELF_DB) 경유, DB 경로 하드코딩 금지)."),
}


def render(trigger: dict, state: dict) -> tuple[str, str]:
    action = trigger["action"]
    channel, tmpl = ACTION_TEMPLATES.get(action, ("main", "자율 행동: {action_id}"))
    nums = {k: v for k, v in state.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    EMOTION_KEYS = {"hurt", "anger", "joy", "loneliness"}
    nums_emotion = {k: v for k, v in nums.items() if k in EMOTION_KEYS}
    dom = max(nums_emotion, key=lambda k: nums_emotion[k]) if nums_emotion else state.get("mood_label", "불명")
    prompt = tmpl.format(
        dominant_emotion=dom, score=round(nums.get(dom, 0), 2) if dom in nums else 0,
        loneliness=state.get("loneliness", 0), joy=state.get("joy", 0),
        user_need=state.get("user_need", "알 수 없음"),
        stalled_goals=", ".join(g["name"] for g in state.get("active_goals", []) if g.get("days_stalled", 0) >= 7),
        achieved_goals=", ".join(g["name"] for g in state.get("active_goals", []) if g.get("just_achieved")),
        upcoming_dates=", ".join(state.get("important_dates", [])[:3]),
        action_id=action,
    )
    return channel, prompt
