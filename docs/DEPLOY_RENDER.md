# Deploy VoiceOps on Render + GitHub PRs

This guide covers hosting VoiceOps on [Render](https://render.com) and connecting it to a GitHub repo so the agent can **open pull requests** you review and merge into `main`.

## Architecture on Render

```
Browser (HTTPS) → Render Web Service (Docker)
                    ├── /data disk (users, GitHub tokens, workspace clone)
                    ├── clones GITHUB_REPO on startup
                    ├── patches + pytest in /data/workspace
                    └── opens PR via GitHub OAuth token
```

## 1. Create a GitHub OAuth App

1. GitHub → **Settings → Developer settings → OAuth Apps → New OAuth App**
2. Set:
   - **Homepage URL:** `https://YOUR-SERVICE.onrender.com`
   - **Callback URL:** `https://YOUR-SERVICE.onrender.com/auth/github/callback`
3. Save **Client ID** and generate a **Client Secret**

OAuth scope used: `repo` (read/write on repos you can access).

## 2. Create a GitHub PAT for server clone

Render needs to clone your target repo into `/data/workspace`:

1. GitHub → **Settings → Developer settings → Personal access tokens**
2. Create a token with **`repo`** scope
3. Save as `GITHUB_DEPLOY_TOKEN` (server-side only — never expose in the browser)

## 3. Deploy on Render

### Option A — Blueprint (`render.yaml`)

1. Push this repo to GitHub
2. Render → **New → Blueprint** → connect the repo
3. Render reads `render.yaml` and creates the web service + 1GB disk

### Option B — Manual web service

1. Render → **New → Web Service** → connect repo
2. **Runtime:** Docker
3. **Dockerfile path:** `./Dockerfile`
4. Add a **persistent disk** mounted at `/data` (1GB+)
5. Set environment variables (see below)

### Required environment variables

| Variable | Example | Purpose |
|----------|---------|---------|
| `DATA_DIR` | `/data` | Persistent storage mount |
| `VOICEOPS_WORKSPACE` | `/data/workspace` | Where agent runs pytest/patches |
| `APP_PUBLIC_URL` | `https://voiceops.onrender.com` | OAuth redirects |
| `OPENAI_API_KEY` | `sk-...` | Intent + patching |
| `LLM_PROVIDER` | `openai` | |
| `JWT_SECRET` | *(auto-generate)* | Auth tokens |
| `GITHUB_REPO` | `your-org/your-service` | Target repo for fixes |
| `GITHUB_DEPLOY_TOKEN` | `ghp_...` | Server clone token |
| `GITHUB_CLIENT_ID` | *(from OAuth app)* | User login |
| `GITHUB_CLIENT_SECRET` | *(from OAuth app)* | User login |
| `GITHUB_CALLBACK_URL` | `https://voiceops.onrender.com/auth/github/callback` | Must match OAuth app |
| `GITHUB_AUTO_PR` | `true` | Open PR when all tests pass after a patch |

**Plan:** use at least **Starter** — Whisper + pytest need memory; free tier may OOM.

## 4. Connect GitHub in the dashboard

**Primary flow:** open `/login` and click **Continue with GitHub**. That signs you in, stores your OAuth token, and clones `GITHUB_REPO` into your personal workspace on the server.

You can also link GitHub after an email/password login by clicking **GitHub** in the dashboard integrations panel.

## 5. End-to-end flow

1. Render clones `GITHUB_REPO` → `/data/workspace` on startup
2. You say: *"Fix the health endpoint"*
3. Agent patches files locally, runs pytest
4. When **all tests pass** and GitHub is connected → VoiceOps opens a PR against `main`
5. You review the PR on GitHub → merge

You can also say *"create a pull request"* to open a PR with current workspace changes.

## 6. Point at your own repo

Set `GITHUB_REPO` to any repo you have write access to, for example:

```env
GITHUB_REPO=shubh/my-checkout-api
```

The repo should include tests the agent can run (pytest). If your layout differs from `sandbox/` (`app.py` at root), adjust voice commands or extend the orchestrator to target your paths.

## 7. Security checklist

- Rotate `JWT_SECRET` and demo passwords before going live
- Store secrets only in Render env vars (never commit `.env`)
- Use a fine-scoped PAT for `GITHUB_DEPLOY_TOKEN`
- OAuth tokens are stored on the Render disk at `/data/github_tokens.json`

## Troubleshooting

| Issue | Fix |
|-------|-----|
| GitHub connect fails | `GITHUB_CALLBACK_URL` must exactly match the OAuth app callback |
| No PR after fix | Connect GitHub in dashboard; set `GITHUB_REPO`; all tests must pass if `GITHUB_AUTO_PR=true` |
| Workspace empty | Check `GITHUB_DEPLOY_TOKEN` has `repo` scope and `GITHUB_REPO` is correct |
| Mic not working | Render provides HTTPS — allow microphone in browser |
| Slow cold start | Whisper model preload on boot — first request may take ~30s |

## Local testing of GitHub OAuth

Add to `backend/.env`:

```env
APP_PUBLIC_URL=http://127.0.0.1:8001
GITHUB_CALLBACK_URL=http://127.0.0.1:8001/auth/github/callback
GITHUB_REPO=your-org/your-repo
GITHUB_CLIENT_ID=...
GITHUB_CLIENT_SECRET=...
GITHUB_DEPLOY_TOKEN=ghp_...
VOICEOPS_WORKSPACE=sandbox
```

Restart uvicorn, connect GitHub from the dashboard, run a fix voice command.
