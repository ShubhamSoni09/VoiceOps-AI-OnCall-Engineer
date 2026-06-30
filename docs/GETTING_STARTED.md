# Getting Started

This is the first path for a new clone. It keeps the demo local, then shows where real credentials plug in.

## 1. Run the Local Demo

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cd frontend && npm install && cd ..
make dev
```

Open `http://127.0.0.1:5191/` and sign in with:

- email: `priya@voiceops.dev`
- password: `oncall123`

`make dev` resets the local demo, uses mock providers, and seeds the `main` room with meeting context plus one pending approval.

## 2. First Clicks

1. Read the seeded timeline.
2. Open `Work Dashboard` and confirm one approval is waiting.
3. Type `what is still open?` in the command dock.
4. Type `review the repo status`.
5. Approve or reject the seeded patch proposal.

## 3. Connect Real Reasoning

For the local demo, keep the mock model. For real reasoning, set env vars before starting the backend:

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5-mini

# or
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=...
ANTHROPIC_MODEL=claude-sonnet-4-5

# or an OpenAI-compatible local/server endpoint
LLM_PROVIDER=openai_compatible
OPENAI_COMPATIBLE_BASE_URL=http://127.0.0.1:8000/v1
OPENAI_COMPATIBLE_API_KEY=
OPENAI_COMPATIBLE_MODEL=glm-5.2
```

VoiceOps falls back to `mock` in local demo mode when no real model is connected.

## 4. Connect Coding Agents

Use `Connected agents` in the right rail:

- Claude/Codex: use OAuth when official OAuth config is present, or API key for read-only modes.
- Cursor/local: use `Local CLI`; VoiceOps runs the command in a temporary workspace.
- All patch results stay approval-first. Providers propose; teammates approve.

For a private GitHub repository, use `Repository setup`:

1. `Sign in with GitHub`, or provide `GITHUB_TOKEN` / `GH_TOKEN`.
2. Paste a GitHub HTTPS or SSH repository URL.
3. Keep PR creation disabled until local approvals are working.

## 5. Enable Real Speaker Verification

Start with environment setup:

1. Set `SPEAKER_PROVIDER=whisperx` and `HF_TOKEN`.
2. Restart the backend.
3. Run the generated two-speaker readiness command in `README.md`.
4. Map any unknown speaker labels in the Speakers panel.

Detailed install and failure handling are in `docs/SPEAKER_VALIDATION.md`.

## 6. Verify Before Sharing

```bash
make demo-reset
make check
```

For a release or video, use:

- `docs/PUBLIC_RELEASE_CHECKLIST.md`
- `docs/PROMO_VIDEO.md`
- `SECURITY.md`
