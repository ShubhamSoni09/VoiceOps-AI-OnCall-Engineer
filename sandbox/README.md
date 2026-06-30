# VoiceOps sandbox workspace

Small **checkout-api** demo for agent testing:

- `GET /v2/charge` — simulates 500s when a fake DB pool is exhausted
- `GET /metrics` — returns error-rate style metrics
- `GET /health` — health-check endpoint used by pytest

## Setup

```bash
cd sandbox
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pytest
```

## Connect MCP + dashboard

Point both at this folder with the same env var:

| Component | Where |
|-----------|--------|
| Cursor MCP | `.cursor/mcp.json` → `"VOICEOPS_WORKSPACE": "sandbox"` |
| Backend / dashboard | `backend/.env` → `VOICEOPS_WORKSPACE=sandbox` |

Use a relative path from the repo root (`sandbox`) or an absolute path to any other repo.

Restart the backend after changing `.env`, then reload MCP in Cursor (**Settings → MCP → voiceops-workspace**).

## Try agent actions (Cursor)

Example prompts for the Cursor agent (it will use MCP tools: `read_file`, `write_file`, `run_command`, etc.):

1. *"Use workspace_info and list what's in the sandbox."*
2. *"Run pytest in sandbox and tell me what's failing."*
3. *"Inspect the health-check endpoint in sandbox/app.py, then run pytest."*

Allowed shell commands in the workspace: `python`, `pytest`, `pip`, and read-only `git` (`status`, `diff`, `log`, `branch`, `show`).
