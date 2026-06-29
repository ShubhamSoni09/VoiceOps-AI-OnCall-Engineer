# Operator Runbook

This runbook is the repeatable local command path for proving the VoiceOps meeting-closure demo.

For a real team trial, first complete [`TEAM_ONBOARDING.md`](TEAM_ONBOARDING.md), then run:

```bash
cd backend
python scripts/team_onboarding_check.py --json --require-ready
python scripts/operator_acceptance.py --json --require-accepted
```

To validate a generated production bundle without loading it into the current shell:

```bash
cd backend
python scripts/operator_acceptance.py --env-file ../voiceops-production-trial/.env --json --require-accepted
```

## Command Profiles

Print the command plan without executing anything:

```bash
cd backend
python scripts/demo_operator.py --profile quick
python scripts/demo_operator.py --profile local
python scripts/demo_operator.py --profile real-mac
```

Run the deterministic local closure gates:

```bash
cd backend
python scripts/demo_operator.py --profile local --run --json
python scripts/product_audit.py --run-harnesses --require-ready --json
```

Run a fast diagnostic when you only need current readiness and recorded evidence:

```bash
cd backend
python scripts/demo_operator.py --profile quick --run --json
python scripts/security_readiness.py --json
```

Bootstrap local runtime readiness when the dashboard says workspace/git or durable event store is disconnected:

```bash
cd backend
python scripts/bootstrap_local_runtime.py \
  --env-file .env.local \
  --workspace /absolute/path/to/team/repository \
  --json
python scripts/target_readiness.py --env-file .env.local --json
```

The bootstrap command writes local-only settings, initializes a SQLite collaboration event store when needed, and leaves production secrets untouched. Use `--force` only when replacing an existing local env file intentionally.

Run the real macOS WhisperX path after Hugging Face access is configured:

```bash
cd backend
HF_TOKEN=<token> python scripts/demo_operator.py --profile real-mac --run --timeout 300 --json
```

## What Local Must Prove

- Backend regression passes.
- Frontend unit tests and production build pass.
- Browser E2E passes live meeting, mock microphone, meeting memory, speaker calibration, generated speaker verification, meeting closure, and agent controls.
- Meeting closure creates a pending patch, Bob/Sam approves it, a local branch is created, tests run, and handoff records the requester/approver/files/result.
- Meeting closure also proves the voice-created auto external-agent path: "AI have the best agent fix that" creates an `auto` external assignment, recommends a provider, auto-dispatches when ready, and produces a pending approval without writing the workspace.
- Agent controls show budget/timeout/cancel state and do not overflow on desktop or mobile.
- Product audit maps every original AI teammate requirement to concrete evidence and fails if deterministic current-code harness proof is missing.
- Prompt-injection safety proves malicious meeting memory cannot make internal, multi-agent, explicit external, or auto external routes bypass preview-first approval.

## Real Speaker Prerequisites

The `real-mac` profile expects:

- `HF_TOKEN` with access to the pyannote diarization model.
- `ffmpeg` installed.
- macOS `say` available for generated two-speaker audio.
- Enough time for CPU diarization; GPU/CUDA is faster, but Mac CPU mode is accepted for validation.

`python scripts/demo_operator.py --profile real-mac --run` performs these checks before starting WhisperX. Missing requirements fail fast with the exact missing variable or binary, before any heavy model loading starts.

The profile sets:

- `SPEAKER_PROVIDER=whisperx`
- `WHISPERX_MODEL=tiny`
- mock LLM/STT/TTS providers for deterministic non-speaker parts

## GitHub PR Workflow

Approved patches stay local by default. The GitHub adapter first prepares a safe pull request plan after an approved patch has been committed on its local branch.

Required state:

- Action status is `completed`.
- Action type is `patch`.
- Approval metadata has a local branch.
- The patch has been committed with the console commit control.
- Git remote `origin` points at GitHub.

API:

```bash
POST /collab/rooms/{room_id}/actions/{action_id}/pull-request
```

Example body:

```json
{
  "dry_run": true,
  "base_branch": "main",
  "title": "Fix health endpoint"
}
```

The dry-run response returns `ready`, blockers, the target branch, remote URL, web URL, and the exact `git push` / `gh pr create` commands.

Real PR creation is disabled by default. To enable it for a production-approved environment:

```bash
GITHUB_PR_CREATION_ENABLED=true
GITHUB_PR_ALLOWED_BASE_BRANCHES=main
GITHUB_PR_CREATE_DRAFT=true
```

Then call the same endpoint with:

```json
{
  "dry_run": false,
  "base_branch": "main",
  "title": "Fix health endpoint"
}
```

The backend runs `gh auth status`, pushes the local action branch, creates a draft PR, writes the PR URL/number and command audit into the action, and broadcasts the room update. Keep using dry-run if `gh` is not authenticated or the branch/base policy is not ready.

## Production Security Gate

Before a real team uses a production deployment, run:

```bash
cd backend
python scripts/prepare_production_trial.py \
  --output-dir ../voiceops-production-trial \
  --frontend-origin https://voiceops.example.com \
  --workspace /absolute/path/to/team/repository \
  --admin-email admin@example.com \
  --admin-name "Team Admin"
# Replace HF_TOKEN/provider secrets, start once, rotate the bootstrap admin
# password, then delete ../voiceops-production-trial/bootstrap-admin.txt.
python scripts/production_cutover_check.py --env-file ../voiceops-production-trial/.env --json --require-ready
python scripts/production_trial_acceptance.py --env-file ../voiceops-production-trial/.env --json --require-accepted
```

The cutover check is the final human-account gate. It fails if the temporary bootstrap password file still exists, `HF_TOKEN` is still a placeholder, seeded demo users are present, generated secrets are weak or reused, or the startup gate would block production. The acceptance gate then initializes the SQLite event store when needed, checks strong JWT configuration, explicit CORS origins, removal of seeded demo users, SQLite stores, explicit workspace boundary, WhisperX/Hugging Face readiness, disabled GitHub external side effects, the production startup fail-closed gate, and final product acceptance.

`prepare_production_trial.py` also writes `bundle-manifest.json` next to the generated `.env`. Keep it with the bundle. The acceptance script treats it as required evidence and fails if the env, workspace, file permissions, startup gate, or seeded-user setting drift from the generated bundle.

## Failure Handling

- If `demo_readiness_full` fails, inspect the failed gate detail in the JSON result first.
- If `production_cutover_check.py --env-file ../voiceops-production-trial/.env --json --require-ready` fails, fix the listed account/secret blocker before inviting teammates.
- If `production_trial_acceptance.py --env-file ../voiceops-production-trial/.env --json --require-accepted` fails, inspect the failed stage first; each stage includes next steps.
- If `product_audit.py --run-harnesses --require-ready` fails, inspect the blocked item; each item includes the exact command that proves that requirement.
- If `prompt_injection_safety` fails, run `python scripts/smoke_prompt_injection.py --json` and inspect the missing invariant. The auto external path must show `auto_external_preview_first`, `auto_external_env_sanitized`, and `auto_external_status_claims_ignored`.
- If `approval_git_tests` fails on external auto-agent evidence, run `python scripts/smoke_meeting_closure.py --json` and inspect `external_assignment_id`, `external_action_id`, `external_provider`, and `external_auto_dispatched`.
- If `security_readiness.py --env-file ../voiceops-production-trial/.env --require-ready` fails, fix the reported control before enabling real team production usage.
- To isolate production-trial product proof, run `product_audit.py --env-file ../voiceops-production-trial/.env --run-harnesses --require-ready --json`.
- To isolate production-trial security config, run `security_readiness.py --env-file ../voiceops-production-trial/.env --require-ready --json`.
- If `init_collab_event_store.py` reports that the SQLite file already exists, inspect it first; use `--replace` only for a disposable trial bundle.
- If browser E2E fails, rerun from `frontend` with `npm run e2e:all -- --json`.
- If real diarization fails, rerun `python scripts/smoke_whisperx_provider.py --generate-macos-tts --record-status --require-multiple-speakers --json`.
- If strict target readiness is blocked, run `python scripts/target_readiness.py --json` and follow each milestone command.
- If target readiness reports `VOICEOPS_WORKSPACE is not configured` or `json backend`, run `python scripts/bootstrap_local_runtime.py --env-file .env.local --workspace /absolute/path/to/team/repository --json`, then rerun target readiness with `--env-file .env.local`.

## Storage Safety

The deterministic browser and backend harnesses use disposable temp workspaces. Durable local runtime evidence is limited to sanitized readiness files under `backend/data`, plus the configured SQLite event store when running the app directly.
