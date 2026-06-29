# Multi-Agent Collaboration Layer

VoiceOps now has a deterministic multi-agent layer for proving team-style AI collaboration without paid LLM calls.

## Agents

- `coordinator`: routes the request and decides whether the run is read-only, memory-only, or patch closure.
- `meeting`: resolves recent meeting context such as "fix that".
- `memory`: attaches room memory/RAG context.
- `code`: uses the existing workspace orchestrator to create read-only answers or pending patch proposals.
- `review`: checks that code-changing work remains approval-first, has a diff, and can request a revision before human approval.
- `test`: applies proposed files in a disposable temporary workspace and runs the validation command before a human approval action is created.
- `git`: defers branch creation until human approval.

## APIs

- `POST /agents/rooms/{room_id}/runs`
- `GET /agents/rooms/{room_id}/runs`
- `GET /agents/rooms/{room_id}/runs/{run_id}`

Patch runs create normal pending `AgentAction` records only after Review Agent accepts the proposal and Test Agent validates the proposed files in a temporary workspace. If Review Agent requests a revision, Code Agent updates the proposed files and diff first; the real workspace is still not written until human approval. Approval still happens through:

- `POST /collab/rooms/{room_id}/actions/{action_id}/approve`
- `POST /collab/rooms/{room_id}/actions/{action_id}/reject`

## Run Trace

Each `AgentRun` records three layers:

- `steps`: what each specialist agent did.
- `findings`: warnings, blocks, and failed validation details.
- `exchanges`: handoffs between agents, such as Meeting to Memory, Memory to Code, Code to Review, Review to Code, Review to Test, and Test to Git.

The React right rail shows the latest specialist roles and the most recent exchanges so a teammate can see whether the AI acted as a coordinated team or stopped early.

## Smoke

```bash
cd backend
python scripts/smoke_multi_agent.py
python scripts/smoke_multi_agent.py --json
python scripts/smoke_multi_agent.py --keep-workspace
```

The smoke harness creates a temp repo, records a meeting task, starts a multi-agent run, verifies the Code -> Review -> Code revision loop, approves the pending patch as Bob/Sam, then checks branch creation, test result, and handoff lines.
