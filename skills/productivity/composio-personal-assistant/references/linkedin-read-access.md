# LinkedIn Read Access via Composio

Session note: Composio's LinkedIn toolkit was active and authenticated in this environment.

## What worked
- `LINKEDIN_GET_MY_INFO` succeeded on the active account.
- The response returned a stable member id (`id`) that can be reused as `person_id`.
- `LINKEDIN_GET_PERSON` with that id may still be permission-limited and return sparse data like `id: private`.

## Practical workflow
1. Search tools with a fresh Composio session.
2. Confirm the LinkedIn toolkit has an active connection.
3. Call `LINKEDIN_GET_MY_INFO` first.
4. Reuse the returned member id for `LINKEDIN_GET_PERSON`.
5. Treat sparse or private responses as a permissions result, not a tool failure.

## Pitfalls
- Do not assume profile details are available just because self-info works.
- If only `private` returns, report the limitation plainly.
- For company/admin-scoped checks, expect some calls to fail with 403 if the scope is missing.

## Notes
- Use this only for authenticated LinkedIn context already connected through Composio.
- Keep user-facing reporting short: what succeeded, what was blocked, and what the next usable step is.
