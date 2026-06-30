<p align="center">
  <strong>VoiceOps</strong>
</p>

<p align="center">
  An open-source AI coworking room for human engineering teams and coding agents.
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-black.svg"></a>
  <img alt="Status: technical preview" src="https://img.shields.io/badge/status-technical%20preview-111111.svg">
  <img alt="Local first" src="https://img.shields.io/badge/runtime-local--first-111111.svg">
  <img alt="Approval first" src="https://img.shields.io/badge/code%20changes-approval--first-111111.svg">
</p>

<p align="center">
  <a href="#quick-start">Quick start</a>
  ·
  <a href="#demo-flow">Demo flow</a>
  ·
  <a href="#core-features">Features</a>
  ·
  <a href="#architecture">Architecture</a>
  ·
  <a href="#readiness-and-security">Security</a>
</p>

---

VoiceOps gives a team one shared room for meeting context, memory, code work, and approval-first patches. It is built for the moment where humans discuss code, ask an AI teammate to help, and still want every file change to be visible, reviewable, and auditable before it lands.

VoiceOps is a local-first technical preview. It is ready for demos, local experiments, and feedback. It is not production-ready software yet.

## Product Snapshot

| Surface | What it does |
| --- | --- |
| `Team room` | Invite teammates into the same shared engineering room. |
| `Work` | Show the next useful action instead of a dense dashboard. |
| `Approval` | Review agent patch proposals and click into the diff. |
| `Agent setup` | Connect Claude, Codex, Cursor, or a local coding agent. |
| Command dock | Ask memory questions or request code work from the bottom input. |

The seeded local demo starts with a `main` room, meeting context, room memory, and one pending approval against the `sandbox/` demo repository.

## Why VoiceOps

Coding meetings usually scatter across chat, calls, docs, terminals, and pull requests. AI coding agents make that worse if they work outside the team room and silently mutate files.

VoiceOps takes the opposite path:

| Problem | VoiceOps approach |
| --- | --- |
| Decisions vanish after meetings | Room memory keeps decisions, tasks, risks, and code references. |
| Agents work out of band | Coding agents propose work inside the shared room. |
| Code changes are hard to audit | Every patch waits in `Approval` with requester, files, diff, and metadata. |
| Demo setup is painful | `make dev` seeds a local no-key demo with mock providers. |

```text
Team room + meeting memory + coding agents + approval gate
```

## Quick Start

### Requirements

| Tool | Version |
| --- | --- |
| Python | 3.11+ |
| Node.js | 20+ |
| npm | bundled with Node |

### Run the local demo

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cd frontend && npm install && cd ..
make dev
```

Open:

```text
http://127.0.0.1:5191/
```

Demo login:

```text
email: priya@voiceops.dev
password: oncall123
```

`make dev` runs the FastAPI backend and Vite frontend. It first regenerates a gitignored local demo runtime:

| Generated local artifact | Purpose |
| --- | --- |
| `backend/.env.local` | Local demo environment. |
| `backend/data/collaboration.local.sqlite3` | Seeded room state and approvals. |
| demo users | Local login only. |
| `main` room | Timeline, memory, and one pending approval. |
| mock providers | No-key LLM, speech, speaker, and agent paths. |
| `sandbox/` | Editable demo workspace. |

Reset demo state:

```bash
make demo-reset
```

Run the public-preview check:

```bash
make check
```

## Demo Flow

Use this path for a short video or live demo:

| Step | Action | Shot |
| --- | --- | --- |
| 1 | Sign in with the demo user. | Local technical preview. |
| 2 | Show `Team room`. | Humans join the same room. |
| 3 | Type `what is still open?`. | Memory answers from room context. |
| 4 | Show `Work`. | One next action, not a cockpit. |
| 5 | Open `Approval`. | Pending patch proposal. |
| 6 | Click `Diff preview`. | Human review moment. |
| 7 | Open `Agent setup`. | `Connect Claude or Codex`. |
| 8 | End on approval state. | Agents propose, humans approve. |

Full script: [`docs/PROMO_VIDEO.md`](docs/PROMO_VIDEO.md)

## Core Features

### Team Room

Rooms scope people, the AI teammate, timeline events, handoffs, memory, approvals, and code work. The local demo uses `main`.

### Command Dock

The command dock is the primary interaction surface:

```text
what is still open?
review the repo status
prepare a small patch
```

Voice is supported by the backend, but the open-source demo works well with text only.

### Meeting Memory

VoiceOps stores room decisions, tasks, questions, risks, code references, speaker events, and approval history. It can answer cited memory questions with deterministic local retrieval.

### Approval-First Code Work

Agents can propose diffs, but file writes stay behind a human approval step.

Patch proposals include:

| Field | Why it matters |
| --- | --- |
| requester | Shows who asked for the work. |
| changed files | Shows the affected surface. |
| diff preview | Makes review the core moment. |
| branch metadata | Keeps Git state auditable. |
| verification | Captures test command or evidence. |
| approve/reject | Keeps humans in control. |

### External Coding Agents

VoiceOps models Claude Code, OpenAI Codex, Cursor, and local/open agents as external coding-agent providers.

| Provider path | Use |
| --- | --- |
| OAuth/API | Read-only or provider-backed flows when configured. |
| Local CLI | Code-changing agent work in a temporary workspace. |
| Mock provider | Zero-key local demo. |

Patch mode still creates a pending approval. Providers propose; humans approve.

### Speaker-Aware Workflows

The project includes a speaker validation pipeline for real team trials. The default demo uses mock speech providers. Real speaker verification requires WhisperX/pyannote, `HF_TOKEN`, and local audio tooling.

### Local-First Runtime

The default runtime uses local files and SQLite under `backend/data/`, all gitignored. The demo can be reset at any time.

## Optional Real Integrations

### LLMs

Set one provider before starting the backend:

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5-mini

LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=...
ANTHROPIC_MODEL=claude-sonnet-4-5

LLM_PROVIDER=openai_compatible
OPENAI_COMPATIBLE_BASE_URL=http://127.0.0.1:8000/v1
OPENAI_COMPATIBLE_API_KEY=
OPENAI_COMPATIBLE_MODEL=glm-5.2
```

### Coding Agents

Use `Agent setup` -> `Connected agents`.

| Agent | Connection path |
| --- | --- |
| Claude Code | OAuth/API when configured, or local CLI. |
| OpenAI Codex | OAuth/API when configured, or local CLI. |
| Cursor | Local CLI. |
| Local/open agent | Local CLI command template. |

### GitHub

Private repository and PR flows can use GitHub sign-in, `GITHUB_TOKEN`, or `GH_TOKEN`. Real PR creation is disabled by default and must be explicitly enabled after review.

### Speaker Verification

Real speaker verification requires WhisperX/pyannote, `HF_TOKEN`, local audio tooling, and accepted model terms.

Start here: [`docs/SPEAKER_VALIDATION.md`](docs/SPEAKER_VALIDATION.md)

## Architecture

```mermaid
flowchart LR
  A["Audio or typed command"] --> B["Intent and normalization"]
  B --> C["Room context and memory"]
  C --> D["Workspace orchestrator"]
  D --> E["Agent or workspace tools"]
  E --> F["Pending approval"]
  F --> G["Human approve or reject"]
  G --> H["Audited result"]
```

Runtime layout:

```text
React console
  -> FastAPI backend
  -> room timeline + memory + approvals
  -> workspace orchestrator
  -> sandbox/MCP workspace tools
  -> external coding-agent providers
```

## Project Layout

```text
backend/      FastAPI app, auth, room state, memory, agents, workspace tools
frontend/     React console and local demo UI
docs/         setup, security, demo, provider, and architecture notes
mcp-server/   MCP tools scoped to VOICEOPS_WORKSPACE
sandbox/      demo workspace used by approval-first code actions
design/       older standalone design artifacts
```

## Common Commands

| Command | Purpose |
| --- | --- |
| `make demo-reset` | Reset demo data and seed the main room. |
| `make dev` | Run backend and frontend. |
| `make check` | Run public-preview tests and frontend build. |
| `cd backend && pytest` | Run backend tests. |
| `cd frontend && npm run test:unit` | Run frontend unit tests. |
| `cd frontend && npm run build` | Build frontend assets. |

Manual backend/frontend commands:

```bash
cd backend
python scripts/bootstrap_local_runtime.py --env-file .env.local --workspace ../sandbox --force --replace-event-store --seed-demo-room
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001

cd ../frontend
VITE_API_TARGET=http://127.0.0.1:8001 VITE_DEV_PORT=5191 npm run dev -- --host 127.0.0.1
```

## Readiness And Security

VoiceOps is safe to run as a local technical preview. Do not expose the default demo runtime as a public production service.

Before sharing a repo or recording a demo:

```bash
make demo-reset
make check
```

Before any production-like deployment:

```bash
cd backend
python scripts/security_readiness.py --require-ready --json
```

The default local demo intentionally keeps demo users enabled and uses local development secrets. The production security gate should fail until those are replaced.

| Resource | Link |
| --- | --- |
| Security policy | [`SECURITY.md`](SECURITY.md) |
| Release checklist | [`docs/PUBLIC_RELEASE_CHECKLIST.md`](docs/PUBLIC_RELEASE_CHECKLIST.md) |
| Security review | [`docs/SECURITY_REVIEW.md`](docs/SECURITY_REVIEW.md) |

## Documentation

| Topic | Document |
| --- | --- |
| New clone path | [`docs/GETTING_STARTED.md`](docs/GETTING_STARTED.md) |
| Promo video script | [`docs/PROMO_VIDEO.md`](docs/PROMO_VIDEO.md) |
| External agents | [`docs/EXTERNAL_AGENT_PROVIDERS.md`](docs/EXTERNAL_AGENT_PROVIDERS.md) |
| Speaker validation | [`docs/SPEAKER_VALIDATION.md`](docs/SPEAKER_VALIDATION.md) |
| RAG meeting memory | [`docs/RAG_MEMORY.md`](docs/RAG_MEMORY.md) |
| Multi-agent workflow | [`docs/MULTI_AGENT.md`](docs/MULTI_AGENT.md) |
| Long-term memory | [`docs/LONG_TERM_MEMORY.md`](docs/LONG_TERM_MEMORY.md) |
| Project ontology | [`docs/PROJECT_ONTOLOGY.md`](docs/PROJECT_ONTOLOGY.md) |
| Operator runbook | [`docs/OPERATOR_RUNBOOK.md`](docs/OPERATOR_RUNBOOK.md) |
| Goal roadmap | [`docs/GOAL_ROADMAP.md`](docs/GOAL_ROADMAP.md) |

## What Not To Claim Yet

- Production readiness.
- Unattended code changes.
- Silent workspace mutation by agents.
- Real speaker verification without WhisperX/pyannote setup.
- Real Claude, Codex, Cursor, or GitHub side effects without explicit credentials and enablement.

## License

MIT. See [`LICENSE`](LICENSE).
