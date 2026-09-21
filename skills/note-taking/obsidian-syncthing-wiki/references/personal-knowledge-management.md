# Personal Knowledge Management — Full Setup Reference

Comprehensive reference for a Korean-language personal daily-life assistant wiki, synced via Syncthing, edited in Obsidian.

## SCHEMA.md Template (for personal knowledge management)

```markdown
# Wiki Schema

## Domain
일상관리 비서 · 업무자동화 · 개인 지식 베이스

## Conventions
- File names: lowercase-kebab-case.md (한글 주제는 영문 슬러그)
- Every page starts with YAML frontmatter
- Use [[wikilinks]] to link between pages (최소 2개 아웃바운드 링크)
- private: true for PII pages

## Frontmatter
```yaml
---
title: 페이지 제목 (한글)
created: YYYY-MM-DD
updated: YYYY-MM-DD
type: entity | concept | routine | project | log | reference | query
tags: [from taxonomy]
private: false
---
```

## Tag Taxonomy
- 생활관리: 일상, 루틴, 습관, 건강, 운동, 식단, 수면, 취미
- 업무/생산성: 업무, 프로젝트, 자동화, todo, 회의, 아이디어, 목표
- 학습/지식: 학습, 기술, AI, 프로그래밍, 도서, 강의, 영어
- 개인정보: 개인정보, 계정, 설정, 재정, 의료
- 메타: 템플릿, 체크리스트, 비교, 타임라인, 회고
```

## Directory Layout

```
wiki/
├── profile/          # 나에 대한 것(프로필·이력)
├── journal/<YYYY-MM>/   # 일일 노트: YYYY-MM-DD.md
├── areas/            # 지속 관리 영역: health.md · finance.md 등 (폴더 아닌 페이지)
├── projects/         # 진행 중인 일·태스크 노트 (interview/ life/ 하위)
├── concepts/         # 지식·개념
├── people/           # 인물
├── automation/job-hunting/             # 구직: Rules.md(판정 기준) · postings/(공고 1건=노트 1개) · resume/
├── hermes/           # 에이전트 운영 문서(runbooks.md · decisions.md)
├── inbox/            # 미처리 캡처
├── raw/              # 불변 원본(articles/ transcripts/ clippings/)
├── _archive/         # 폐기·대체분 (tasks/ notes/)
├── index.md · SCHEMA.md · log.md
```

## Accepting a Remote Device (REST API Procedure)

When the user's local machine adds your server but no connection establishes yet,
the server needs to add the remote device back:

```bash
# 1. Get API key
APIKEY=$(grep -oP '(?<=<apikey>)[^<]+' "$STHOME/config.xml")

# 2. Check current state (no connections = pending)
curl -s -H "X-API-Key: $APIKEY" http://127.0.0.1:8384/rest/system/connections

# 3. Fetch config
curl -s -H "X-API-Key: $APIKEY" http://127.0.0.1:8384/rest/system/config -o /tmp/st-cfg.json

# 4. Ask user for their Mac's Device ID, then add it
python3 << 'PYEOF'
import json
REMOTE_ID = "USER-PROVIDED-DEVICE-ID"

with open("/tmp/st-cfg.json") as f:
    cfg = json.load(f)

cfg["devices"].append({
    "deviceID": REMOTE_ID,
    "name": "macbook",
    "compression": "metadata",
    "addresses": ["dynamic"],
    "introducers": [],
})

for folder in cfg["folders"]:
    if folder["id"] == "wiki":
        folder["devices"].append({"deviceID": REMOTE_ID})

with open("/tmp/st-cfg.json", "w") as f:
    json.dump(cfg, f, indent=2)
PYEOF

# 5. Push
curl -s -X PUT -H "X-API-Key: $APIKEY" -H "Content-Type: application/json" \
  -d @/tmp/st-cfg.json http://127.0.0.1:8384/rest/system/config

# 6. Verify connection
curl -s -H "X-API-Key: $APIKEY" http://127.0.0.1:8384/rest/system/connections
```

## .env Alternative When User Blocks Shell Writes

```bash
# Instead of: echo 'KEY=val' >> .env  (may be blocked)
hermes config set WIKI_PATH /path/to/wiki
hermes config set OBSIDIAN_VAULT_PATH /path/to/wiki
```

These write to config.yaml. The agent's `.env` reader also sources config.yaml
keys, so the wiki tools find the path. Restart gateway for env to take effect.

| Action | Command |
|--------|---------|
| Start | `~/.local/bin/syncthing --no-browser --home=$HOME/.config/syncthing` |
| Check device ID | `grep -oP '(?<=id=")[^"]+(?=")' ~/.config/syncthing/config.xml \| head -1` |
| Get API key | `grep -oP '(?<=<apikey>)[^<]+' ~/.config/syncthing/config.xml` |
| REST API status | `curl -s -H "X-API-Key: $KEY" http://127.0.0.1:8384/rest/system/connections` |
| Folder status | `curl -s -H "X-API-Key: $KEY" http://127.0.0.1:8384/rest/db/status?folder=wiki` |

Note: Port 8384 = GUI, 22000 = TCP/QUIC sync, 21027 = discovery broadcast.

## Composition: Hermes Gateway + Composio MCP + Syncthing + Wiki

Full stack from this session:

| Component | Role | Status Signal |
|-----------|------|--------------|
| Hermes Gateway | Discord bot, handles messages | `hermes gateway status`, PID in ps |
| Composio MCP | External app tool integration | `hermes mcp list` → composio ✓ enabled |
| Syncthing | Bi-directional file sync | `http://127.0.0.1:8384`, REST API |
| llm-wiki (wiki/) | Markdown knowledge base | Synced via Syncthing, edited in Obsidian |

Discord bot token goes in `DISCORD_BOT_TOKEN` in .env.
Composio MCP uses `x-consumer-api-key:` header (NOT `x-api-key` or `Authorization: Bearer`).
Syncthing config lives at `$HERMES_HOME/.config/syncthing/config.xml` when HERMES_HOME is set.

When setting up a personal wiki for a Korean-speaking user:
- SCHEMA.md and content should be bilingual (tag names in Korean, file slugs in English)
- Daily notes in Korean
- Hermes should respond in Korean when operating within this wiki context
- The wiki structure SCHEMA.md should be the authoritative reference the agent reads on every session start