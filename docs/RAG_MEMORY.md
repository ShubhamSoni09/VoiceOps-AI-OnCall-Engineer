# RAG Memory

VoiceOps uses a local deterministic RAG layer so the AI teammate can answer meeting questions with cited project context while staying free/offline by default.

## What Is Indexed

Room-scoped RAG indexes are rebuilt from:

- Meeting memory items: decisions, tasks, questions, risks, code references.
- Timeline messages: speaker-attributed meeting speech and agent replies.
- Agent actions: requester, action type, files changed, approval metadata, git/test audit fields.
- Workspace code: local file tree snippets when `include_code=true`.

Raw audio is not stored in the RAG index. Speaker identity remains represented as room messages, speaker labels, mapped participant names, and confidence metadata.

## Short And Long Memory

RAG answers combine two retrieval tiers:

- `short`: recent room memory, recent timeline messages, and recent agent actions. This keeps “what did we just decide?” and “fix that” style context close to the meeting.
- `long`: persisted room RAG index stored on disk. This finds older decisions, action history, files, and code references outside the short-memory window.
- `ontology`: deterministic project graph nodes such as people, files, actions, branches, and memory items. This gives answers an explicit relationship trail in addition to text similarity.

Every citation includes `metadata.memory_tier` when applicable. The UI displays this as `Short memory`, `Long memory`, or `Ontology`.

## Provider Boundary

The embedding provider is pluggable:

- Default: `RAG_EMBEDDING_PROVIDER=local_sparse`
- Current provider: deterministic sparse local vectors.
- Future providers can implement the same `EmbeddingProvider` port without changing the collaboration timeline or citation contract.

Unsupported providers fail explicitly:

- `/collab/rooms/{room_id}/rag/query` returns `400`
- `/collab/rooms/{room_id}/rag/index` returns `400`
- Voice/live meeting memory answers degrade to a visible “RAG memory is unavailable” response instead of crashing the meeting flow.

## Storage

Runtime paths are configured through backend settings:

- `RAG_INDEX_PATH`: persisted RAG JSON index. Default: `backend/data/rag_index.json`.
- `COLLAB_STORE_PATH`: room messages, memory, actions, agent settings.
- `SPEAKER_STORE_PATH`: speaker mappings and profiles.
- `VOICEOPS_CACHE_PATH`: runtime cache for local readiness/status data.

Tests and smoke scripts override these paths into temporary directories so demo checks do not mutate the real project state.

## Public APIs

Rebuild a room index:

```bash
POST /collab/rooms/{room_id}/rag/index?include_code=true
```

Ask a cited RAG question:

```bash
POST /collab/rooms/{room_id}/rag/query
{
  "question": "what files did Alice mention?",
  "limit": 8,
  "include_code": true
}
```

Ask a provenance-first question that merges RAG plus ontology:

```bash
POST /collab/rooms/{room_id}/provenance/query
{
  "question": "what files did Alice mention and who approved app.py?",
  "limit": 8,
  "include_code": true,
  "include_ontology": true
}
```

This endpoint keeps `/rag/query` compatible while adding ontology citations and a `retrieval.sources` count. Its response mode is `local_provenance`, and `retrieval.rag_provider` preserves the underlying RAG provider used for text/code retrieval.

Response shape:

```json
{
  "answer": "Files mentioned: app.py.",
  "mode": "local_hybrid",
  "citations": [
    {
      "source": "memory",
      "source_id": "mem-...",
      "title": "code_reference from Priya Nair",
      "excerpt": "app.py",
      "actor_name": "Priya Nair",
      "score": 1013,
      "metadata": {
        "memory_tier": "short",
        "kind": "code_reference"
      }
    }
  ],
  "retrieval": {
    "provider": "local_sparse",
    "indexed_documents": 9,
    "indexed_sources": {
      "memory": 4,
      "timeline": 3,
      "action": 1,
      "code": 1
    },
    "short_memory_hits": 6,
    "long_memory_hits": 6,
    "candidate_count": 6,
    "memory_tiers": {
      "short": 6,
      "long": 0,
      "ontology": 1
    },
    "sources": {
      "memory": 3,
      "code": 1,
      "ontology": 1
    }
  }
}
```

## UI Trace

The React console shows:

- RAG answer text.
- Citation rows/chips with source, actor, tier, score, and excerpt.
- Retrieval trace chips: provider, short hits, long hits, indexed docs, candidate count, displayed short/long citation count.
- Provenance trace chips add ontology hits and displayed ontology citation count when the merged endpoint is used.
- RAG index status in the Meeting memory panel.

This is intentionally audit-first: users can see why the AI answered from meeting memory, old project history, or code.

## Smoke Test

Run the deterministic local RAG smoke:

```bash
cd backend
python scripts/smoke_rag_memory.py
python scripts/smoke_rag_memory.py --json
python scripts/smoke_rag_memory.py --keep-workspace
```

The smoke creates a disposable workspace, seeds meeting memory and an action, rebuilds the local RAG index, asks file/action/decision/provenance questions, and verifies citations plus retrieval trace.
