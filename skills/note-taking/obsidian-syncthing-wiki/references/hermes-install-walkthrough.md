# Hermes Agent Installation Walkthrough (2026-07-05)

> First-hand walkthrough from a real Debian-based Linux server install.
> Full stack: Hermes Agent v0.18.0 → OpenRouter → Discord Gateway → Composio MCP → Syncthing + Obsidian wiki.

## Quick Reference: The Install in 10 Steps

| Step | What | Key Command |
|------|------|-------------|
| 1 | Install deps | `apt install python3 python3-venv curl git jq ffmpeg ripgrep` |
| 2 | Install uv | `curl -fsSL https://astral.sh/uv/install.sh \| bash` |
| 3 | Install Hermes | `curl -fsSL https://hermes-agent.nousresearch.com/install.sh \| bash` |
| 4 | Set API key | `echo 'OPENROUTER_API_KEY=sk-or-...' >> ~/.hermes/.env` |
| 5 | Configure model | `hermes config set model.default deepseek/deepseek-v4-flash` |
| 6 | Set Discord token | `echo 'DISCORD_BOT_TOKEN=MTUy...' >> ~/.hermes/.env` |
| 7 | Set allowed user | `echo 'DISCORD_ALLOWED_USERS=107819...' >> ~/.hermes/.env` |
| 8 | Start gateway | `hermes gateway run` (or `hermes gateway install` for service) |
| 9 | Add Composio MCP | `hermes config set mcp_servers.composio.url "https://connect.composio.dev/mcp"` then edit config.yaml to add `x-consumer-api-key` header |
| 10 | Verify | `hermes doctor && hermes gateway status && hermes mcp list` |

## Common Traps Encountered

### Config lives at `/opt/data/` (not `~/.hermes/`) when HERMES_HOME is set
When the install script uses a custom `HERMES_HOME` (e.g. `/opt/data`):
- Config lives at `/opt/data/config.yaml` 
- `.env` lives at `/opt/data/.env`
- Syncthing home needs `--home=/opt/data/.config/syncthing`

### Discord: Message Content Intent MUST be ON
The #1 cause of a silent Discord bot. In Discord Developer Portal → Bot → Privileged Gateway Intents:
**Message Content Intent** must be checked. Without it, the bot connects but never receives message content.

### Syncthing: Folder path is NOT the folder path you think
If the user sets the wiki folder path on their Mac to `~` (home directory) or `~/Documents`, Syncthing will try to sync **everything** there — 48GB / 500K+ files. The actual server wiki might be only 148MB/4 files. Always verify the folder path is a **dedicated empty directory** (e.g. `~/wiki`).

### Composio MCP: header is `x-consumer-api-key` (not `x-api-key`, not `Authorization: Bearer`)
```yaml
mcp_servers:
  composio:
    url: "https://connect.composio.dev/mcp"
    headers:
      x-consumer-api-key: "ck_xxx..."  # ← 이 헤더가 맞다(실측). x-api-key/Bearer 아님
```

### Redaction hides API keys in output
Hermes redacts credential-like strings by default. `sk-or-...c19b` is the normal display. Don't panic — the actual full key IS sent in API calls, just masked in conversation logs.

### `hermes config set` for simple values, config.yaml edit for complex nested
- Simple: `hermes config set model.default deepseek/deepseek-v4-flash` ✓
- Complex (dicts/lists): need to `hermes config edit` or write config.yaml directly ✓
- The agent CANNOT write to config.yaml via `patch`/`write_file` — blocked by Hermes security

### Gateway needs linger on SSH
Without `sudo loginctl enable-linger $USER`, the gateway stops when the SSH session ends even if installed as a systemd user service.

### Chrome not needed unless using browser tools
`hermes tools disable browser` avoids the "Chrome not found" error if you don't need browser automation.

## Stack Diagram

```
┌─────────────────────────────────────────────────────────┐
│                  Linux Server (VPS)                      │
│                                                         │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ Hermes CLI  │  │ Hermes       │  │ Syncthing     │  │
│  │ (터미널)     │  │ Gateway      │  │ (파일 동기화)   │  │
│  │             │  │ (백그라운드)   │  │ port 8384/    │  │
│  │             │  │              │  │ 22000/21027   │  │
│  └──────┬──────┘  └──────┬───────┘  └──────┬────────┘  │
│         │               │                  │           │
│         ▼               ▼                  ▼           │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ OpenRouter  │  │ Discord Bot  │  │ /opt/data/wiki │  │
│  │ (DeepSeek)  │  │ (서버 메시지)  │  │ (위키 저장소)   │  │
│  └─────────────┘  └──────┬───────┘  └──────┬────────┘  │
│                          │                  │           │
│                    ┌─────┴──────┐    Syncthing sync    │
│                    │ Composio   │           │           │
│                    │ MCP (MCP)  │           ▼           │
│                    │ (Gmail 등)  │    ┌──────────────┐  │
│                    └────────────┘    │ Mac (Obsidian)│  │
│                                      └──────────────┘  │
└─────────────────────────────────────────────────────────┘
```

## Post-Install Checklist

- [ ] `hermes doctor` → all green
- [ ] CLI: `hermes chat -q "hello"` → gets a response
- [ ] Discord: @mention bot in channel → responds
- [ ] Discord: DM the bot → responds
- [ ] Composio: `hermes mcp list` → composio ✓ enabled
- [ ] Gmail: ask bot "read my recent emails" → fetches
- [ ] Google Calendar: ask bot "what's my schedule" → works
- [ ] Syncthing: `http://localhost:8384` → shows connected devices
- [ ] Mac Syncthing: folder synced, files visible in Obsidian
