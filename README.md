# VoiceOps - AI Teammate Console

Run multi-person coding meetings with an always-online AI teammate, speaker-aware memory, approved code actions, and auditable handoffs.

## Architecture

```
Audio/Text → STT → Intent → Normalization → Context → Workspace Orchestrator → sandbox/MCP
```

## Quick start

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# set OPENAI_API_KEY, ANTHROPIC_API_KEY, or an OpenAI-compatible base URL if using a real LLM
# set VOICEOPS_WORKSPACE=sandbox or an absolute local repo path to connect code tools
uvicorn app.main:app --host 127.0.0.1 --port 8001
```

### Dashboards

| UI | URL | Notes |
|----|-----|--------|
| HTML (wired) | http://127.0.0.1:8001/dashboard | Login → voice + sandbox orchestrator |
| React (Vite) | http://localhost:5191 | `cd frontend && npm install && npm run dev` |

Demo login: `priya@voiceops.dev` / `oncall123`

### Real speaker verification

For a real team trial, start with [`docs/TEAM_ONBOARDING.md`](docs/TEAM_ONBOARDING.md) and audit the package:

```bash
cd backend
python scripts/team_onboarding_check.py --json --require-ready
```

For repeatable demo operation, start with the operator command set:

```bash
cd backend
python scripts/demo_operator.py --profile local
python scripts/demo_operator.py --profile local --run --json
```

Profiles and failure handling are documented in [`docs/OPERATOR_RUNBOOK.md`](docs/OPERATOR_RUNBOOK.md).

For local WhisperX/pyannote verification on a Mac, keep live meeting timeouts short but give the offline verification smoke enough room to load diarization models:

```env
SPEAKER_PROVIDER=whisperx
WHISPERX_DEVICE=cpu
WHISPERX_WORKER_MODE=subprocess
SPEAKER_VERIFICATION_TIMEOUT_SECONDS=300
```

Run the generated two-speaker check with:

```bash
cd backend && python scripts/demo_readiness.py --generate-macos-tts --require-real-diarization --timeout 300
```

To prove the live WebSocket path also handles real diarized audio and writes the collaboration timeline, add the live meeting gate:

```bash
cd backend && python scripts/demo_readiness.py --generate-macos-tts --require-real-diarization --require-real-live-meeting --real-live-worker-mode persistent_subprocess --real-live-chunks 2 --timeout 300
```

For the frontend demo readiness harness, the default path stays fast and mock-backed:

```bash
cd frontend && npm run e2e:all -- --json
```

When the MacBook has WhisperX, pyannote assets, `HF_TOKEN`, `say`, and `ffmpeg` available, include the optional real live meeting gate in the same frontend report:

```bash
cd frontend && npm run e2e:all -- --json --real-live --real-live-worker-mode persistent_subprocess --real-live-chunks 2 --real-live-timeout 300
```

To prove the browser microphone path itself can feed real WhisperX and render speaker labels in the React UI, use the heavier browser gate:

```bash
cd frontend && npm run e2e:all -- --json --real-browser-live --real-browser-live-worker-mode persistent_subprocess --real-browser-live-timeout 180
```

These harnesses write a sanitized local evidence file at `backend/data/demo_evidence.json`. The right rail `Demo readiness` panel reads it through `/system/readiness` and shows the latest mock closure, real backend live, and real browser microphone results without storing raw audio.

On a MacBook CPU this is near-real-time, not low-latency realtime: a short generated two-speaker sample currently takes about 20 seconds through the safe subprocess worker. `WHISPERX_WORKER_MODE=persistent_subprocess` starts a long-lived isolated worker so repeated live chunks can reuse loaded models while remaining killable from the backend. `--in-process-warmup` exists on `scripts/smoke_live_meeting_real.py` for manual diagnostics, but it is intentionally not used by readiness by default because native WhisperX/pyannote loading can hang inside the local Mac/conda stack and cannot always be interrupted safely.

The room-level speaker validation API reports unknown labels, low-confidence labels, manual mappings, and real WhisperX verification evidence without storing raw audio. Details are in [`docs/SPEAKER_VALIDATION.md`](docs/SPEAKER_VALIDATION.md).

### RAG meeting memory

VoiceOps keeps a local deterministic RAG index for cited meeting answers, with short-memory and long-memory retrieval traces visible in the React console. Details are in [`docs/RAG_MEMORY.md`](docs/RAG_MEMORY.md).

Run the isolated RAG smoke before a demo:

```bash
cd backend
python scripts/smoke_rag_memory.py
python scripts/smoke_rag_memory.py --json
```

### Multi-agent collaboration

VoiceOps can route a meeting request through deterministic specialist agents: Coordinator, Meeting, Memory, Code, Review, Test, and Git. Details are in [`docs/MULTI_AGENT.md`](docs/MULTI_AGENT.md).

Run the isolated multi-agent closure smoke:

```bash
cd backend
python scripts/smoke_multi_agent.py
python scripts/smoke_multi_agent.py --json
```

### External coding agents

VoiceOps can connect Claude Code, OpenAI Codex, and Cursor as external coding-agent providers through OAuth/API key/local CLI style credentials. External agents can propose diffs, but workspace writes still require VoiceOps approval. Details are in [`docs/EXTERNAL_AGENT_PROVIDERS.md`](docs/EXTERNAL_AGENT_PROVIDERS.md).

### Project ontology

VoiceOps exposes a deterministic room-scoped ontology for people, files, memory, actions, approvals, and git branches. Details are in [`docs/PROJECT_ONTOLOGY.md`](docs/PROJECT_ONTOLOGY.md).

### Long-term memory

Approved actions, meeting decisions, speaker mapping audit events, and handoff summaries can be archived into deterministic room-scoped long-term memory. Details are in [`docs/LONG_TERM_MEMORY.md`](docs/LONG_TERM_MEMORY.md).

### Goal roadmap

The remaining multi-agent teammate work is tracked phase by phase with storage rules, cache policy, event rules, and quality gates in [`docs/GOAL_ROADMAP.md`](docs/GOAL_ROADMAP.md).

Run the final acceptance audit when you need a machine-readable proof against the original multi-person AI teammate goal:

```bash
cd backend
python scripts/final_acceptance_audit.py --json --require-accepted
```

### MCP workspace

Set the same path in `.cursor/mcp.json` and `backend/.env`:

```env
VOICEOPS_WORKSPACE=sandbox
```

## Project layout

```
backend/          FastAPI - auth, voice, console bootstrap, orchestrator
frontend/         React dashboard (from feat/ui-incident-dashboard), wired to API
design/           HTML login + incident dashboard
mcp-server/       MCP tools scoped to VOICEOPS_WORKSPACE
sandbox/          checkout-api demo (missing /health on purpose)
```

## Tests

```bash
cd backend
pytest
```
