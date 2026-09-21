# Calendar Integration for Daily Journaling

Composio MCP로 Google Calendar 연동 시 사용하는 정확한 패턴.

## 오늘 일정 조회

```json
{
  "tool_slug": "GOOGLECALENDAR_EVENTS_LIST_ALL_CALENDARS",
  "arguments": {
    "calendar_ids": ["primary"],
    "response_detail": "full",
    "single_events": true,
    "time_min": "YYYY-MM-DDT00:00:00+09:00",
    "time_max": "YYYY-MM-DD(+1)T00:00:00+09:00"
  }
}
```

- `time_min`: 오늘 00:00 KST
- `time_max`: 다음날 00:00 KST (exclusive)

## 일정 추가

```json
{
  "tool_slug": "GOOGLECALENDAR_CREATE_EVENT",
  "arguments": {
    "calendar_id": "primary",
    "summary": "일정 제목",
    "start_datetime": "YYYY-MM-DDTHH:MM:00",
    "timezone": "Asia/Seoul",
    "event_duration_hour": N,
    "event_duration_minutes": N,
    "location": "장소 (optional)",
    "description": "설명 (optional)"
  }
}
```

- `end_datetime`을 직접 지정하거나 `event_duration_hour` + `event_duration_minutes` 사용
- duration 최소값 0 제한, event_duration_minutes는 0-59

## 병렬 실행

조회와 추가가 독립적이면 `COMPOSIO_MULTI_EXECUTE_TOOL`로 병렬:

```json
{
  "tools": [
    { "tool_slug": "GOOGLECALENDAR_EVENTS_LIST_ALL_CALENDARS", "arguments": {...} },
    { "tool_slug": "GOOGLECALENDAR_CREATE_EVENT", "arguments": {...} }
  ],
  "sync_response_to_workbench": false
}
```

## 응답 파싱

- `EVENTS_LIST_ALL_CALENDARS` 응답: `events[].event` 안에 실제 이벤트 데이터
  - `summary`, `start.dateTime`, `end.dateTime`, `location`, `description`, `htmlLink`
- `CREATE_EVENT` 응답: `response_data` 안에 `id`, `htmlLink`, `start`, `end`