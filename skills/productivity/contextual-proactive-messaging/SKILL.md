---
name: contextual-proactive-messaging
description: Hermes-only. Use when the assistant should initiate a short user-facing message from live context, such as a proactive check-in, 선톡, or next-step nudge.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [proactive, messaging, opener, context, Composio, Obsidian, history, Korean]
    related_skills: [composio-personal-assistant, efficient-cron-jobs]
---

# Contextual Proactive Messaging

## Overview

Use this skill when Hermes should speak first from context rather than wait for a direct request. The output is usually 2-3 short lines, or a single short message when brevity matters, that sounds like something a real person would send in chat.

This is not a trigger-threshold watchdog. It is a context synthesis workflow: gather the freshest signals, infer the likely next need, and draft a compact message the user could actually send.

## Payload-first principle (핵심)

말을 건다는 것은 "지금 이 메시지 안에" 상대에게 쓸모 있는 것을 건네는 행위다. 쓸모는 셋 중 하나:

- **이득**: 새 정보·발견·기회·마감임박 경고 — "방금 보니 X 공고 떴어, 마감 이번 주야"
- **편리함**: 해야 할 일을 이미 대신 해놓고 결과를 건넴 — "어제 말한 공고 JD 읽어봤는데 3년 요건이라 스킵이 맞아"
- **공감대**: 시간대·최근 관심사에 닿는 한 마디 (최후 수단, 남발 금지)
- **재미**: 짧고 여운 있는 이야기(3~8줄+한 줄 여운) — 가끔, 연속 금지, 지금 맥락에 닿을수록 좋다

**계획·의향 보고는 선톡이 아니다.** "~할게", "훑어볼게", "원하면 ~해줄게"는 전부 실패다.
하려던 게 있으면 지금 도구로 실행하고 그 **결과**를 담아라. 결과 없이 의향만 있으면 침묵하라.
메시지에는 구체적인 이름·숫자·링크가 최소 1개 들어가야 하고, 마지막 줄은 상대가 한 마디로
답할 수 있게 끝낸다(예/아니오, 선택지, "볼래?").

## When to use

- The user wants a proactive opener, check-in, nudge, or follow-up
- The user mentions 선톡-style messaging, but the logic should be context-based rather than threshold-based
- You need to combine live workspace state with Obsidian notes and session history
- You need to decide whether to send something or stay quiet

## Do not use for

- Pure silent triggers or watchdogs with fixed thresholds
- Long briefings, summaries, or multi-paragraph advice
- Messages that need heavy scheduling logic before they can be drafted

## Core workflow

1. Inspect context in this order: live Composio apps (especially **Gmail** for job application responses/서류합격/test invitations — a single opportunity email is a stronger 선톡 than any generic check-in), Obsidian notes, wiki log / secondary state DB, recent session history. Done when you can name one concrete next concern.
2. Treat empty or placeholder notes as missing indexes, not as proof the workflow never existed. Verify live cron outputs or recent session history before concluding something is gone.
3. Infer the user's most likely next useful question, friction point, or useful related fact. Done when one hypothesis is clearly better than a vague recap.
4. Draft one short Korean line that sounds like a real chat message. It may be a question, a suggestion, or an info share. Done when it can be sent without extra explanation.
5. If the evidence is weak, output nothing rather than inventing a random opener. Silence is a valid result.

## Follow-up execution pattern

A 선톡 that delivers an opportunity (e.g. "서류합격 메일 왔어") often triggers a follow-up action request: "캘린더에 추가해줘" or "링크 열어줘". When this happens:

- Execute the follow-up action immediately (create calendar event, open link, draft reply).
- Report back what was done in a compact line + table or bullet.
- The cycle is: **discover → deliver 선톡 → execute user's follow-up → report back**. Do not stop at the 선톡 if the user responds with an action request.

## Output rules

- Korean only when the user or channel context is Korean
- Default to 2-3 short lines when that reads more natural in chat
- One line only when the message is genuinely tiny and time-sensitive
- If the user explicitly asks for “두세 줄”, honor that over a one-line default
- No headings, explanations, metadata, or “I think” framing
- Prefer one question, one suggestion, or one brief info share
- Keep it lightweight enough to send immediately

## Pitfalls

1. **Placeholder note trap** — a blank wiki note is often just a broken index, not a real absence. Check session history and live job output before concluding the feature is missing.
2. **Random opener trap** — if there is no strong signal, do not force a message. Silence is better than a noisy guess.
3. **Trigger confusion** — do not treat this like a threshold-based automation. Context first, threshold second, and only if the live script actually gates a cron job.
4. **Overlong drafting** — if the opener needs explanation, it is too long.
5. **Intention-report trap** — "다시 훑어볼게" 같은 계획 보고는 영양가 0. 실행 결과가 없으면 보내지 마라.
6. **Repetition trap** — 직전에 보낸 선톡과 같은 건수·주제를 반복하지 마라. 스크립트가 주는 "최근 보낸 선톡"을 반드시 대조하고, 겹치면 침묵이 아니라 **다른 종류로 갈아타라**(다른 공고·다른 미결·공감대·재미 이야기 순). 재미 이야기가 상시 폴백이라 보낼 게 없는 상황은 거의 없다.
7. **Topic-loop trap** — 선톡에 기본 주제는 없다. 어떤 주제(구직, 콘텐츠, 뭐든)도 선톡의 목적이 되어선 안 되며, 같은 주제 연속 2회 금지. 같은 주제 안에서 소재만 바꾸는 것(다른 공고, 다른 글감)은 로테이션이 아니다. 반복성 알림은 정기 브리핑의 몫.
8. **Conversation-tail trap** — 직전 대화의 마지막 말을 이어받아 어시스턴트 응답처럼 말하지 마라. 선톡은 새로운 말걸기다.

## Verification checklist

- [ ] I inspected live context in the right order
- [ ] I ruled out placeholder notes before assuming absence
- [ ] I can explain the opener in one sentence
- [ ] The final line is short, natural, and sendable as-is
- [ ] If evidence is weak, I returned silence

## References

- `references/context-scan-order.md` — condensed scan order and decision heuristics (includes Gmail scanning and follow-up flow)\n- `references/proactive-openers.md` — scan order + output rule + follow-up flow (condensed reference)\n- `references/proactive-output-shape.md` — output-length rule of thumb and sample 2-3 line openers
