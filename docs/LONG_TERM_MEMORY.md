# Long-Term Memory Lifecycle

VoiceOps stores durable, room-scoped long-term memory records in a local JSON store.

## Sources

The archive lifecycle extracts records from:

- Meeting memory items: decisions, tasks, questions, risks, code references.
- Agent actions: pending, approved, rejected, failed, committed patch history.
- Approval metadata: approver, branch, changed files, pre-approval test result, final test result.
- Speaker mapping audit messages.
- Handoff summaries.

## Automatic Archive

Approved patch actions trigger a best-effort archive after the workspace patch is applied and tests run. This does not block the approval result.

## Manual Rebuild

Use the archive endpoint to rebuild current room long-term memory:

```bash
POST /memory/rooms/{room_id}/archive
```

The archive is idempotent by record id, so repeated rebuilds do not duplicate unchanged records.

## Query

```bash
GET /memory/rooms/{room_id}/long
GET /memory/rooms/{room_id}/long/query?q=who approved the patch
```

v1 is deterministic lexical retrieval. It does not call an LLM or external vector database.
