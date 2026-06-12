# VoiceOps — AI On-Call Engineer

Investigate, fix, and deploy production issues using voice commands.

## Architecture

```
Audio/Text → STT → Intent → Normalization → Context → Workspace Orchestrator → sandbox/MCP
```

## Quick start

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# set OPENAI_API_KEY and VOICEOPS_WORKSPACE=sandbox
uvicorn app.main:app --host 127.0.0.1 --port 8001
```

### Dashboards

| UI | URL | Notes |
|----|-----|--------|
| HTML (wired) | http://127.0.0.1:8001/dashboard | Login → voice + sandbox orchestrator |
| React (Vite) | http://localhost:5191 | `cd frontend && npm install && npm run dev` |

Demo login: `priya@voiceops.dev` / `oncall123`

### MCP workspace

Set the same path in `.cursor/mcp.json` and `backend/.env`:

```env
VOICEOPS_WORKSPACE=sandbox
```

## Project layout

```
backend/          FastAPI — auth, voice, console bootstrap, orchestrator
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
