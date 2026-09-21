# Composio MCP Architecture — Token Impact

## Discovery (2026-07-05)

Composio MCP does NOT inject 500+ app-specific tool schemas into every system prompt. The actual architecture:

- **7 meta-tools** (`COMPOSIO_SEARCH_TOOLS`, `COMPOSIO_MULTI_EXECUTE_TOOL`, etc.) are registered in Hermes and appear in the system prompt. These are lightweight.
- **App-specific tools** (GMAIL_FETCH_EMAILS, GOOGLECALENDAR_LIST_EVENTS, etc.) are discovered **dynamically at runtime** via `COMPOSIO_SEARCH_TOOLS` — they never appear in the system prompt.

## What this means for optimization

- `hermes mcp configure composio` shows only the 7 meta-tools — these should all stay enabled
- No app-level filtering is needed or possible through the TUI
- Composio is already token-efficient by design
- The old advice to "filter Composio to 3 apps" was based on a misunderstanding of the architecture

## Verification

```bash
hermes mcp list
# → composio  all  ✓ enabled — correct state, no changes needed
```