# VoiceOps — AI On-Call Engineer

Investigate, fix, and deploy production issues using voice commands.

## Architecture

The **Voice Processor** (Voice Agent Core) pipeline:

```
Audio/Text → Speech-to-Text → Intent Extraction → Command Normalization → Context Enrichment
                                                                              ↓
                                                                   Agent Orchestrator (next)
```

| Stage | Description | Default |
|-------|-------------|---------|
| STT | Whisper or AWS Transcribe | Whisper (`base`) |
| Intent | LLM or rule-based mock | Mock (local dev) |
| Normalization | Standard command schema for orchestrator | Built-in |
| Context | Session memory + incident enrichment | JSON file store |

## Quick start

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload
```

Open http://localhost:8000/docs for the API.

## Voice API

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/voice/process-audio` | Upload audio (push-to-talk) |
| POST | `/voice/process-text` | Process text directly (dev/testing) |
| DELETE | `/voice/sessions/{id}` | Clear session memory |
| GET | `/voice/health` | Voice module status |

### Example: process text

```bash
curl -X POST http://localhost:8000/voice/process-text \
  -H "Content-Type: application/json" \
  -d "{\"text\": \"Why is the API failing in prod?\", \"session_id\": \"demo\"}"
```

### Example: process audio

```bash
curl -X POST http://localhost:8000/voice/process-audio \
  -F "audio=@recording.wav" \
  -F "session_id=demo"
```

## Configuration

See `backend/.env.example`. Key settings:

- `STT_PROVIDER` — `whisper` or `aws`
- `LLM_PROVIDER` — `mock` (local) or `bedrock` (AWS Claude)
- `WHISPER_MODEL` — `tiny`, `base`, `small`, etc.

## Project layout

```
backend/
├── app/
│   ├── main.py                 # FastAPI entry
│   └── voice_agent/
│       ├── pipeline.py         # End-to-end voice pipeline
│       ├── router.py           # HTTP routes
│       ├── stt/                # Whisper + AWS Transcribe
│       ├── intent/             # LLM intent extraction
│       ├── normalization/      # Command normalizer
│       └── context/            # Memory + enrichment
└── tests/
```

## Tests

```bash
cd backend
pytest
```

## Next steps

- Wire **Agent Orchestrator** to consume `NormalizedCommand` output
- Add React + OpenUI push-to-talk frontend
- Connect Composio MCP integrations (GitHub, Render, Slack)
