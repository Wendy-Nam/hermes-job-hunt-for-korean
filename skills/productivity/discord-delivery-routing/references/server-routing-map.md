# Server Discord Routing Map

This reference records the current Discord channel layout used by the user's server.

## Category grouping
- **대화**: `hermes-chat`, `tasks`
- **알림**: `briefing`, `wiki-log`, `reminders`
- **운영**: `ops`

## Channel IDs
- `hermes-chat` — `<YOUR_DISCORD_CHANNEL_ID>`
- `briefing` — `<YOUR_DISCORD_CHANNEL_ID>`
- `wiki-log` — `<YOUR_DISCORD_CHANNEL_ID>`
- `reminders` — `<YOUR_DISCORD_CHANNEL_ID>`
- `tasks` — `<YOUR_DISCORD_CHANNEL_ID>`
- `ops` — `<YOUR_DISCORD_CHANNEL_ID>`

## Usage notes
- Use `hermes send --to discord:hermes-chat "..."` for direct conversation.
- Use `hermes send --list discord` to verify available Discord targets.
- Keep the channel IDs here, not in the main skill body, because they are server-specific and may change.
