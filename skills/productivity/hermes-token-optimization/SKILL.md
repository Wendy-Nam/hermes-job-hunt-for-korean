---
name: hermes-token-optimization
description: "Use when the user wants to reduce Hermes token costs, optimize the prompt footprint, or audit/cut token waste — for personal assistant or light daily use, not for heavy coding sessions. Covers diagnostics, compression tuning, toolset pruning, MCP filter, cron enabled_toolsets, and skill-load strategy."
version: 1.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [hermes, optimization, tokens, cost, personal-assistant, configuration, pruning, model-routing, delegation, profiles]
    related_skills: [efficient-cron-jobs, hermes-agent, composio-personal-assistant]
---

# Hermes Token Optimization

## Overview

Every tool schema, skill description, and SOUL.md character in the system prompt costs tokens on cold starts (new sessions, cron jobs, model changes). Prompt caching makes subsequent turns within the same session much cheaper, but cold starts happen enough to matter. For a personal-assistant / daily-use Hermes profile, the default toolset and skill load are often larger than needed. This skill provides a repeatable diagnostic → prune → verify workflow that cuts token waste without removing any capability the user actually uses.

> **Note on MCP servers:** Don't assume MCP servers are the biggest waste. Composio, for example, only exposes 7 lightweight meta-tools in the prompt — app-specific tools are dynamic. Check before pruning. See `references/composio-mcp-architecture.md`.

## When to Use

- User says "make Hermes faster/more efficient," "reduce token cost," or "optimize my setup"
- User's profile has 10+ skills always-loaded but only uses 3-4 regularly
- MCP servers with 100+ tools are always in the prompt but only 2-3 apps are used
- Cron jobs run with the full default toolset (every tool schema paid for on every tick)
- After adding new tools, skills, or MCP servers — re-audit
- User wants different models for different types of work (chat vs coding, creative vs analytical) — "model role separation"

Don't use this for: one-off coding sessions (those need the full toolset), debugging tool failures, or adding new capabilities. Model role separation is a separate configuration concern — use the dedicated section below.

## Diagnostic Workflow

Run these commands in order to find the biggest waste sources first:

### Step 1: Audit current state

```bash
hermes tools list          # which toolsets are enabled?
hermes mcp list            # which MCP servers and how many tools each?
hermes skills list         # how many skills always-loaded vs disabled?
hermes cron list --all     # any cron jobs burning tokens on every tick?
hermes config path         # confirm config.yaml location
```

### Step 2: Rank waste sources by impact

Waste sources, ordered by typical token impact (largest first):

| Rank | Source | Why it hurts |
|------|--------|-------------|
| 1 | Unnecessary toolsets enabled | `computer_use` on a headless server, `video` when never used — each adds tool schemas. |
| 2 | Weak/missing compression config | Defaults (threshold 0.50, target 0.20) are conservative. Tighter settings save tokens. |
| 3 | Cron jobs with full default toolset | Every tick pays for every tool. Strip to `["web", "terminal", "file"]` or less. |
| 4 | Too many always-loaded skills | Every skill's description block costs tokens even when unloaded. Disable low-frequency skills. |
| 5 | Large SOUL.md | Above ~80% fullness, trim verbose instructions. But don't over-reduce: prompt caching makes cached tokens nearly free in ongoing sessions. |

> **MCP servers are usually NOT a major waste source.** For Composio specifically: only 7 lightweight meta-tools are in the system prompt; 500+ app-specific tools are dynamically discovered at runtime. See `references/composio-mcp-architecture.md`.

### Step 3: Apply fixes in impact order, one at a time

Apply each fix, then verify with a `/reset` session before moving to the next. Fix isolation catches regressions.

## Optimization Techniques

### 1. MCP Servers — Check First, Don't Assume Waste

Before assuming MCP servers are the problem, check the actual tool count:

```bash
hermes mcp list
# Look at the "Tools" column — if it shows "all" for a server,
# the tool count depends on the server's architecture.
```

For **Composio** specifically: only 7 lightweight meta-tools are in the system prompt. The 500+ app-specific tools (Gmail, Calendar, Drive, etc.) are discovered dynamically at runtime via `COMPOSIO_SEARCH_TOOLS` — they never bloat the prompt. No filtering is needed. See `references/composio-mcp-architecture.md`.

For **other MCP servers** that expose many tools statically, use the interactive TUI:
```bash
hermes mcp configure <server-name>
# TUI opens — toggle individual tools on/off
# Note: requires a real terminal (not execute_code or delegate_task)
```

If the MCP server is entirely unused, remove it from `config.yaml`.

### 2. Toolset Pruning

Disable toolsets that have zero utility for the profile's primary use case:

```bash
# Headless server — no desktop GUI
hermes tools disable computer_use

# No video analysis needed
hermes tools disable video
hermes tools disable video_gen

# No X/Twitter integration
hermes tools disable x_search
```

Enable toolsets that improve token efficiency:

```bash
# Context engine helps manage prompt context
hermes tools enable context_engine
```

Toolset changes take effect on `/reset` (new session), not mid-conversation.

### 3. Compression Tuning

Default Hermes compression is conservative. For personal-assistant use (many short conversations), tighter settings save tokens:

```bash
hermes config set compression.enabled true
hermes config set compression.threshold 0.35    # default: 0.50 — start earlier
hermes config set compression.target_ratio 0.15  # default: 0.20 — compress more
```

Threshold 0.35 means compression kicks in when the context window is 35% full (vs 50% default). Target ratio 0.15 means it compresses to 15% (vs 20%). These are safe for most personal-assistant workloads; for heavy coding sessions with long context, revert to defaults.

### 4. Cron Job enabled_toolsets

Every cron job pays the tool-schema cost on every tick. Strip to the minimum the job actually needs:

```python
# For a news/weather brief: only web + terminal + file
cronjob(action='update', job_id='...', enabled_toolsets=["web", "terminal", "file"])

# For a DB-based proactive check: terminal + file + session_search
cronjob(action='update', job_id='...', enabled_toolsets=["terminal", "file", "session_search"])
```

`no_agent=True` jobs (pure script, no LLM) don't benefit from `enabled_toolsets` — they never load the agent at all.

The `efficient-cron-jobs` skill covers script-gate patterns for zero-token silent skips. Combine the two for maximum savings.

### 5. Skill Load Strategy

Skills listed in `<available_skills>` cost tokens even when not loaded. Two approaches:

**Disable low-frequency skills** (remove from prompt):
```
# In config.yaml under skills.disabled: add names
# Or use hermes skills config (interactive)
```

**Load on-demand**: Disabled in config but loadable with `--skills` flag or `/skill` slash command when needed:
```bash
hermes --skills github-code-review "review this PR"
# or mid-session: /skill github-code-review
```

Good candidates for disable-for-now: coding skills (code-review, debugging, PR workflows), creative skills (diagram, ascii-art), and research skills — if the user's primary profile is personal assistant.

### 6. SOUL.md / Project Context Trim

Check SOUL.md size (shown at session start as `USER PROFILE [XX%]`). Above 80% fullness, trim to core signal. But balance is key:

- **Prompt caching**: OpenRouter caches the system prompt after the first turn. Cached SOUL.md tokens cost ~90% less on subsequent turns within the same session.
- **Cold starts still pay full price**: Cron jobs (`/reset`, model changes) always start fresh — SOUL.md tokens are full-price there.
- **Target**: 500-700 chars for personal assistant profiles. Enough for personality and key directives, not so much that cold starts hurt.

Good SOUL.md directive for wiki-heavy workflows:
```markdown
- 위키 적극 활용: 대답 전에 /path/to/wiki에서 관련 노트를 먼저 찾아본다
```

### 7. Model Role Separation

Sometimes the user wants one model for casual conversation and another for coding or analytical work. Hermes supports this at three levels of granularity. Document the user's preference in memory; encode the technique here.

#### Approach A: `/model` Slash Command (Simplest, Manual)

Switch models mid-session without restarting. Works in CLI and (if the gateway supports slash commands) in Discord/Telegram.

```bash
# In any session — switch to a coding model
/model openai-codex/gpt-5.4-mini

# Switch back to chat model
/model deepseek/deepseek-v4-flash
```

**Best for:** Occasional role switching within the same conversation. Zero config, instant.

**Limitation:** Manual — you have to remember to switch. The `/model` command is registered under CLI slash commands; verify it works on the user's gateway platform before recommending.

#### Approach B: Profiles (Full Isolation)

Create separate profiles, each with its own model. Switch via CLI flags or run separate gateway instances per profile.

```bash
# Create a work profile cloned from default
hermes profile create work --clone
hermes profile use work

# Now set the work profile's model to Codex
hermes config set model.default gpt-5.4-mini
hermes config set model.provider openai-codex

# Switch back to chat profile
hermes profile use default

# Or run with a specific profile for one session
hermes -p work
```

For gateway (Discord/Telegram) separation, run a second gateway process on the work profile pointed at a different channel:
```bash
hermes -p work gateway run
```

**Best for:** Complete isolation — different channels, different personalities. Profiles keep separate sessions, skills, memory, and config.

**Limitation:** Two gateway processes = two long-running daemons. The work profile starts with empty skills/memory unless cloned.

#### Approach C: Delegation Model Override (Automatic Subagent Routing)

Set `delegation.*` config to a different model. When the main agent spawns a subagent via `delegate_task`, the subagent runs on the delegation model while the main session stays on the chat model.

```bash
# Subagents run on Codex; main session stays on DeepSeek
hermes config set delegation.provider openai-codex
hermes config set delegation.model gpt-5.4-mini
```

Verified with `hermes config show` — the delegation section appears once set.

In practice: you keep chatting on DeepSeek, and when you say "write a Python script to parse this CSV and email the results," the subagent spawned to do the actual implementation runs on Codex.

**Best for:** Background coding tasks where you don't need to watch the model work. The delegation config also supports `base_url`, `api_key`, `reasoning_effort`, and `max_iterations` — tune these for the delegate model's strengths.

**Limitation:** Only works through `delegate_task` — direct in-session coding still uses the main model. For that, combine with Approach A.

#### Quick Decision Table

| Need | Recommended approach |
|---|---|
| Manual switch mid-conversation | A — `/model` |
| Separate Discord channels for chat vs work | B — Profiles |
| Subagents auto-use a different model | C — Delegation override |
| "I want all three" | A + C (compatible) |
| Two gateways on the same machine | B — separate profiles |

#### Common Pitfalls

- **`/model` may not work on all gateway platforms** — it's a CLI slash command. Test on the user's platform before relying on it for Discord/Telegram.
- **Profiles start empty** — `--clone` copies config but not session history. Skills and memory are profile-isolated by default though the profile creation wizard can prompt for clone options.
- **Delegation doesn't affect direct turns** — only `delegate_task` subagents use the delegation model. The main conversation loop always uses `model.default`.
- **Cold start cost per profile** — each profile has its own skills, memory, and session DB. Switching profiles is a full cold start (no prompt cache carryover).
- **MOA (Mixture of Agents) interacts** — if MOA is enabled with reference models from multiple providers, the aggregator model (set separately) may override the main model's output. When debugging model routing, check if MOA is active.

## Verification

After applying changes, start a fresh session (`/reset` or new `hermes` invocation) and confirm:

- [ ] `hermes tools list` shows only needed toolsets enabled
- [ ] `hermes mcp list` shows reduced tool count per server
- [ ] `hermes config get compression` reflects new thresholds
- [ ] Cron jobs show `enabled_toolsets` in `cronjob(action='list')` output
- [ ] A test conversation works correctly with the reduced toolset
- [ ] No "tool not found" errors for workflows the user actually uses

## Common Pitfalls

1. **Disabling a toolset the user actually needs** — before disabling, check recent sessions with `session_search` or ask the user which capabilities they use daily.
2. **Compression too aggressive for long sessions** — threshold 0.35 is fine for short daily chats but can prematurely compress context in coding sessions. If the user reports "Hermes forgot what we were doing," raise threshold back to 0.40-0.45.
3. **MCP configure requires a real terminal** — it's an interactive TUI. Can't run from `execute_code`, `delegate_task`, or non-TTY surfaces. The user must run it themselves in a terminal.
4. **Toolset changes need /reset** — they don't take effect mid-conversation. Remind the user to start a new session.
5. **context_engine behavior is under-documented** — enable it, test, and disable if it causes issues. Its exact toolset is not publicly documented as of 2026-07.
6. **Composio MCP misconception** — do NOT tell users to "filter Composio to 3 apps." Composio's 7 meta-tools are lightweight; app tools are dynamic. The `hermes mcp configure composio` TUI only shows the meta-tools (all should stay enabled). See `references/composio-mcp-architecture.md`.
7. **Don't prune what you don't understand** — if unsure about a toolset's function, check `hermes tools list` description or leave it enabled.
8. **STT uses native provider, not MCP** — Hermes has native Groq Whisper support. Setting `GROQ_API_KEY` in `.env` + `stt.provider: groq` is direct, free-tier, and avoids Composio MCP round-trips (which would add latency + tokens + failure points for every voice message). Don't route STT through Composio when native is available.
9. **MOA with mixed providers changes effective model** — if MOA is enabled with reference models from different providers, the aggregator model (set in `moa.aggregator`) may override the main output. Changing `model.default` won't fully change behavior if MOA is active — check `hermes config show` for MOA settings.
10. **`/model` command availability** — the `/model` slash command is registered in the CLI commands; its availability on a given gateway platform depends on whether that platform's adapter processes slash commands. Test before recommending. If unavailable, fall back to Approach B (profiles).