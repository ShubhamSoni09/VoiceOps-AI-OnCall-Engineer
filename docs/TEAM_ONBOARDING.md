# Team Onboarding

This is the runbook for bringing a real team into a VoiceOps production trial. Use it after the local deterministic harnesses are green.

## 1. Prerequisites

- macOS or Linux host with Python 3.11+, Node 18+, Git, and ffmpeg.
- A clean team repository path for `VOICEOPS_WORKSPACE`.
- Hugging Face token with access to the pyannote diarization model if real multi-speaker attribution is enabled.
- Optional GitHub CLI authentication only if real PR creation is intentionally enabled.

## 2. Configure Environment

Preferred production-trial setup:

```bash
cd backend
python scripts/prepare_production_trial.py \
  --output-dir ../voiceops-production-trial \
  --frontend-origin https://voiceops.example.com \
  --workspace /absolute/path/to/team/repository \
  --admin-email admin@example.com \
  --admin-name "Team Admin"
```

The script generates a private `.env`, a non-demo `users.json`, and a one-time bootstrap admin password. Keep the generated directory outside git and delete the bootstrap password file after the first login and password rotation.

Manual fallback:

```bash
cd backend
cp .env.production.example .env
```

Edit `backend/.env`:

- Replace `JWT_SECRET` with a generated 32+ character value.
- Set `CORS_ALLOWED_ORIGINS` to the exact frontend origin.
- Set `VOICEOPS_WORKSPACE` to the exact repository root.
- Keep `COLLAB_STORE_BACKEND=sqlite` and `SPEAKER_STORE_BACKEND=sqlite`.
- Set `SPEAKER_PROVIDER=whisperx` and `HF_TOKEN` for real speaker trials.
- Keep `GITHUB_PR_CREATION_ENABLED=false` until local branch approval is stable.

## 3. Rotate Accounts

Before inviting teammates:

- Remove seeded demo users from `USERS_STORE_PATH`, or use `scripts/prepare_production_trial.py` to generate a non-demo user store.
- Set `SEED_DEMO_USERS=false` before running with `DEPLOYMENT_ENVIRONMENT=production`.
- Create named teammate accounts with least-privilege permissions.
- Verify each teammate can log in and appears as the correct participant.
- Confirm speaker labels remain auditable through manual mappings.

## 4. Start Services

Backend:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Open the React console at the printed Vite URL. In local development this is usually `http://127.0.0.1:5174/` or `http://localhost:5173/`.

## 5. Acceptance Flow

Run the package check:

```bash
cd backend
python scripts/team_onboarding_check.py --json --require-ready
```

Run deterministic local proof:

```bash
cd backend
python scripts/operator_acceptance.py --json --require-accepted
python scripts/demo_operator.py --profile local --run --json
python scripts/product_audit.py --run-harnesses --require-ready --json
```

Run production security gate:

```bash
cd backend
python scripts/operator_acceptance.py --env-file ../voiceops-production-trial/.env --json --require-accepted
# After first login, rotate the bootstrap admin password and delete
# ../voiceops-production-trial/bootstrap-admin.txt before strict acceptance.
python scripts/production_cutover_check.py --env-file ../voiceops-production-trial/.env --json --require-ready
python scripts/production_trial_acceptance.py --env-file ../voiceops-production-trial/.env --json --require-accepted
```

`production_cutover_check.py` is the final account/secret gate. It verifies the temporary bootstrap password file has been deleted, generated secrets are distinct, `HF_TOKEN` has been replaced, seeded demo users are absent, and the production startup gate would pass. `production_trial_acceptance.py` is the strict production-trial gate. It verifies the generated env targets `DEPLOYMENT_ENVIRONMENT=production`, keeps `PRODUCTION_STARTUP_SECURITY_GATE=true`, runs the same startup fail-closed check used by the FastAPI app, and then runs deployment hardening plus final product acceptance.

Run real Mac speaker validation when using live diarization:

```bash
cd backend
HF_TOKEN=<token> python scripts/demo_operator.py --profile real-mac --run --timeout 300 --json
```

## 6. Trial Script

Use this sequence for the first team trial:

1. Alice/Priya and Bob/Sam join the same room.
2. Start Live meeting and speak in alternating turns.
3. Map unknown speaker labels to teammates in the Speakers panel.
4. Ask the AI teammate: `what is still open?`
5. Say or type: `AI fix that`.
6. Confirm a pending patch appears without changing files.
7. Bob/Sam approves the patch.
8. Verify local branch, changed files, test result, approver, and requester in Agent actions.
9. Ask for handoff and confirm decisions, open items, actions, branch, and tests are included.

## 7. Failure Handling

- If onboarding check fails, fix the missing file, command, or template variable first.
- If security readiness fails, do not invite a real team into the deployment.
- If cutover check fails, rotate the bootstrap admin password, delete `bootstrap-admin.txt`, replace placeholder provider secrets, and rerun it.
- If real speaker validation fails, use mock speaker mode for the collaboration demo and keep manual speaker mapping visible.
- If approval fails, inspect the action audit before retrying; rejected and failed actions must remain in handoff.
- If GitHub PR creation fails, keep the approved local branch and use the dry-run PR plan.

## 8. Storage Safety

- Raw audio is not stored by default.
- Speaker mappings, collaboration timeline, approvals, and long memory are durable.
- RAG and ontology are rebuildable projections and should not be the only copy of critical facts.
- Proposed patch content is backend runtime data; the UI displays diff and audit metadata.
