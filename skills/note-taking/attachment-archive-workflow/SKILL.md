---
name: attachment-archive-workflow
description: Use when user-provided files, chat attachments, or exported artifacts need explicit archival into a raw/ working set or vault note. Distinguishes receipt from backup, verifies the archive location, and keeps notes separate from raw artifacts.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [note-taking, archive, attachments, backup, files]
    related_skills: []
---

# Attachment Archive Workflow

## Overview
Use this skill when a user asks whether a file, attachment, or exported artifact has been backed up, or when the task is to move conversational artifacts into a durable archive. The key idea is simple: **receipt is not archive**. A file that arrived in chat, browser, or tool output is not automatically backed up anywhere durable until you explicitly copy or move it.

This skill keeps the archive boundary clear between working files, raw artifacts, and human-readable notes. Use the raw archive for preserved originals, and use notes for summaries, indexes, or references. A compact session reminder lives in `references/raw-backup-boundary.md`.

## When to Use
- The user asks whether an attachment is already backed up.
- You need to move a received file into a `raw/` archive or similar durable store.
- You want to keep an original artifact and a separate note/summary.
- You need to answer "where is the file stored?" with a verified path or handle.

## Workflow
1. **Identify the source artifact.** Confirm which file or attachment is being archived.
2. **Choose the archive target.** Keep raw artifacts in a dedicated raw/archive area; keep summaries in notes.
3. **Perform an explicit copy or move.** Do not assume incoming attachments are already archived.
4. **Verify the archive.** Re-read or stat the destination so the backup claim is grounded.
5. **Document the relationship.** If useful, add a note that points to the archived original.

Completion criterion: you can name the archive target and verify the artifact is actually present there.

## Common Pitfalls
1. **Confusing receipt with backup**
   - Fix: treat chat receipt as the starting point, not the archive point.
2. **Mixing raw artifacts and summaries**
   - Fix: keep the original file in raw storage and write only the summary in the note.
3. **Answering before verification**
   - Fix: confirm the destination exists before telling the user the backup is done.
4. **Overwriting provenance**
   - Fix: preserve the original filename or record source metadata when it matters.

## Verification Checklist
- [ ] The source file is identified.
- [ ] The archive destination is explicit.
- [ ] The raw artifact exists at the destination.
- [ ] Any summary note links back to the archived original.

## One-Shot Recipe
When a user asks whether an attachment is backed up:
1. Check the original attachment source.
2. Copy or move it into the raw archive if needed.
3. Verify the destination.
4. Answer with the archive location, not a guess.
