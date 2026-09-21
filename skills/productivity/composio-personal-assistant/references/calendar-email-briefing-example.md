# Calendar + Email Briefing Example

Example transcript (Korean-speaking user, Asia/Seoul timezone). **All names, companies, schedules and amounts below are fictional** — the point is the tool sequence and output format.

## User Request

`[USER] 내 구글캘린더 읽어와서 이번 주 일정과 다음주 일정 브리핑좀.`

## Tools Used

1. `COMPOSIO_SEARCH_TOOLS` — discover tools, create session_id
2. `GOOGLECALENDAR_GET_CURRENT_DATE_TIME` — anchor: 2026-07-05T17:11:36+09:00 (Sunday)
3. `GOOGLECALENDAR_EVENTS_LIST_ALL_CALENDARS` × 2 (parallel) — this week & next week
4. `GMAIL_FETCH_EMAILS` — 20 recent emails
5. `GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID` × 3 (parallel) — drill into specific threads

## Date Window Calculation

- Today (Sunday): 2026-07-05
- This week: Mon 7/6 → Sun 7/12
  - time_min: 2026-07-06T00:00:00+09:00
  - time_max: 2026-07-13T00:00:00+09:00
- Next week: Mon 7/13 → Sun 7/19
  - time_min: 2026-07-13T00:00:00+09:00
  - time_max: 2026-07-20T00:00:00+09:00

## Calendar Summary View (Example)

### This week
| 요일 | 시간 | 일정 |
| 월 7/6 | 15:00~16:00 | [면접] 대면 - 알파테크 기술영업 |
| 화 7/7 | 10:30~11:30 | [면접] 대면 - 브라보소프트 강남지사 (6층) |
| 수 7/8 | 14:00~15:00 | [면접] 화상 - 찰리랩스 1차 인터뷰 |
| 일 7/12 | 21:30~22:30 | 스터디 모임 |

### Next week
| 금 7/17 | 종일 | 제헌절 (공휴일) |
| 금 7/17 | 21:30~22:30 | 월간 코칭 세션 |
| 일 7/19 | 21:30~22:30 | 스터디 모임 |

## Email Categories (Example)

### 1. 면접/채용 (Job Search)
- **알파테크 면접** (7/3) — 일정 확정 스레드
- **델타컴퍼니** 서류 결과 — 불합격 신호
- **에코스타트업** 잡보드 이력서 열람 (7/4)
- **커리어 코치** 면접 스크립트 피드백 (지원동기/강점/가치관)

### 2. AI/IT 트렌드
- AI 뉴스레터 · 이벤트 안내 등

### 3. 코칭/정산
- **커리어 코칭 정산** — 코치(coach@example.com)
  - 7/1: 1h41m + 7/4: 2h4m = 총 3h45m = 예시 금액

## Thread Analysis Technique

To determine how much coaching happened, examine the **References** and **In-Reply-To** headers:

```python
# From GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID response
# Count References to get thread depth
refs = message['payload']['headers']
ref_count = len([h for h in refs if h['name'] == 'References'][0]['value'].split('\n'))
```

In this session: 49 reference message-IDs in one email = ~25 round trips in a single thread.
