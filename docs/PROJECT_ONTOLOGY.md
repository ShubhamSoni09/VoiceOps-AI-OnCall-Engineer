# Project Ontology

VoiceOps exposes a deterministic room-scoped project ontology as a rebuildable read model.

## Sources

- Room participants
- Timeline messages
- Meeting memory items
- Agent actions
- Approval metadata
- Git branch metadata
- File references found in text, memory, actions, and diffs

## Node Kinds

- `person`
- `file`
- `action`
- `branch`
- `memory:decision`
- `memory:task`
- `memory:question`
- `memory:risk`
- `memory:code_reference`

## Edge Relations

- `mentioned_file`
- `recorded_memory`
- `mentions_file`
- `requested_action`
- `approved_action`
- `created_branch`
- `touches_file`

## APIs

- `GET /ontology/rooms/{room_id}`
- `GET /ontology/rooms/{room_id}/query?q=what files are in context`

The ontology is local and deterministic. It does not call an LLM or external graph service in v1.
