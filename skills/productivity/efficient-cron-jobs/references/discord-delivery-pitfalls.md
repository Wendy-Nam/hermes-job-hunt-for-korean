# Discord delivery + random-window cron notes

## What bit us in this session
- A scheduled job can execute successfully and still not deliver if the delivery target is not resolved at runtime.
- `deliver: origin` is only safe when the current Hermes thread/home channel is actually present.
- Discord text-channel delivery depends on bot/server permissions and the channel wiring being real, not just implied by the prompt text.
- If Hermes needs to read Discord text as context, Discord Developer Portal → Bot → Privileged Gateway Intents → **Message Content Intent** must be enabled.
- `no_agent: true` means script-only delivery: the script's stdout is the whole message. Any diagnostic text printed by the script is what gets sent.
- `no_agent: false` is the mode that lets Hermes consume script output as context and generate a natural-language response.
- For a 30~60 minute random send window, cron should tick frequently and the script should manage the window state.
- When the user asks for the next run, convert UTC to KST before answering.

## Minimal random-window state shape
Store one JSON object per job, for example:
- `last_contact_ts`: latest source timestamp that the window is based on
- `target_ts`: the randomly chosen trigger time
- `notified_for_ts`: the `last_contact_ts` already notified about

Reset the window only when `last_contact_ts` changes.

## Delivery target rule of thumb
Prefer an explicit runtime-resolvable destination for Discord delivery. Avoid assuming that `origin` exists unless the session/channel wiring proves it.
