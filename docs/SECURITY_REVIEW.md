# Production Security Review

Phase 28 adds a repeatable production security gate for running VoiceOps with a real team. It does not enable new external side effects; GitHub PR creation remains dry-run until Phase 29.

## Command

```bash
cd backend
python scripts/security_readiness.py --json
python scripts/security_readiness.py --require-ready --json
python scripts/security_readiness.py --env-file ../voiceops-production-trial/.env --require-ready --json
```

The API exposes the same report for admins:

```http
GET /system/security/readiness
```

## Trust Boundaries

- Browser to FastAPI API: authenticated HTTP/WebSocket traffic.
- FastAPI to local workspace: file reads, proposed patch writes after approval, git commands.
- FastAPI to runtime stores: users, collaboration memory, speaker mappings, agent runs, long memory, RAG cache.
- FastAPI to external providers: Hugging Face/pyannote for diarization, optional OpenAI providers, external coding-agent providers, GitHub PR adapter.

## STRIDE Summary

| ID | Category | Risk | Target | Primary Control |
| --- | --- | --- | --- | --- |
| S-01 | Spoofing | Critical | JWT/session | Non-default strong `JWT_SECRET` |
| S-02 | Spoofing | Critical | User store | Remove seeded demo users |
| S-03 | Spoofing | High | Speaker identity | WhisperX verification plus manual correction |
| T-01 | Tampering | High | Workspace tools | Explicit workspace root and path confinement |
| T-02 | Tampering | High | Runtime stores | SQLite durable stores |
| T-03 | Tampering | High | GitHub adapter | Dry-run only until approved external side effects |
| R-01 | Repudiation | High | Approval audit | Durable collaboration store |
| R-02 | Repudiation | High | External actions | Action id, approver, branch, command audit |
| I-01 | Information Disclosure | High | Browser/API | Explicit production CORS origins |
| I-02 | Information Disclosure | High | Meeting memory | Durable scoped stores and no raw audio storage by default |
| I-03 | Information Disclosure | Medium | Speaker pipeline | Sanitized verification evidence |
| I-04 | Information Disclosure | High | External agent credentials | Encrypted credential store and no token echo |
| E-01 | Elevation | Critical | Auth/API | Strong auth boundary and no demo users |
| E-02 | Elevation | High | Workspace mutation | Least-privilege workspace path |

## Production Blockers

The security gate requires these before production:

- `DEPLOYMENT_ENVIRONMENT=production`
- `JWT_SECRET` is non-default and at least 32 characters.
- `CORS_ALLOWED_ORIGINS` is explicit, not `*`.
- `SEED_DEMO_USERS=false` so production startup cannot create known demo credentials.
- Seeded demo users are removed from `USERS_STORE_PATH`.
- `COLLAB_STORE_BACKEND=sqlite`
- `SPEAKER_STORE_BACKEND=sqlite`
- `VOICEOPS_WORKSPACE` points to the exact repository root.
- `SPEAKER_PROVIDER=whisperx` and `HF_TOKEN` is configured for real speaker attribution.
- `EXTERNAL_AGENT_CREDENTIAL_SECRET` is generated and distinct from `JWT_SECRET` before users connect Claude/Codex/Cursor credentials.
- `GITHUB_PR_CREATION_ENABLED=false` by default. If real PR creation is enabled, `GITHUB_PR_ALLOWED_BASE_BRANCHES`, `gh` authentication, and audit controls must be configured.

## Control Mapping

| Control | Requirement | Threats |
| --- | --- | --- |
| `jwt_secret` | SR-AUTH-01 | S-01, E-01 |
| `default_users` | SR-AUTH-02 | S-02, E-01 |
| `cors_origins` | SR-NET-01 | I-01, E-01 |
| `collab_store` | SR-STORE-01, SR-AUDIT-01 | T-02, R-01, I-02 |
| `speaker_store` | SR-STORE-01, SR-DATA-01 | T-02, I-02 |
| `workspace_boundary` | SR-WORKSPACE-01 | T-01, E-02 |
| `speaker_provider` | SR-SPEAKER-01 | S-03, I-03 |
| `external_agent_credentials` | SR-EXTAGENT-01, SR-CHANGE-01 | I-04, T-04, R-02 |
| `github_side_effects` | SR-CHANGE-01 | T-03, R-02 |

## Current Position

Local demo mode is allowed to remain convenient, but `--require-ready` should be treated as the production gate. If it fails, do not invite a real team into a production deployment or enable external side effects.

## GitHub PR Creation

The GitHub adapter remains dry-run by default. Real creation requires all of the following:

- `GITHUB_PR_CREATION_ENABLED=true`
- `GITHUB_PR_ALLOWED_BASE_BRANCHES` contains the requested base branch, usually `main`
- `GITHUB_PR_CLI_PATH` resolves to an authenticated `gh` CLI
- the action is a completed approved patch
- the approved patch has been committed on its local branch
- the dry-run plan has no blockers

Execution order:

1. `gh auth status`
2. `git push -u origin <head_branch>`
3. `gh pr create --base <base> --head <head> --title <title> --body <body>`

The action audit records the PR URL, PR number when available, creator, timestamp, command labels, exit codes, and truncated command output. Secrets are not accepted through the API body and are not written into action metadata.

## External Agent Providers

Claude Code, OpenAI Codex, and Cursor can be connected through official OAuth, API keys, or local CLI auth. VoiceOps does not collect provider passwords or browser cookies.

Controls:

- Provider credentials are encrypted with `EXTERNAL_AGENT_CREDENTIAL_SECRET`.
- API responses only return redacted token previews.
- Enabled providers are constrained by `EXTERNAL_AGENT_ALLOWED_PROVIDERS`.
- External agent runs create normal `pending_approval` actions for code-changing work.
- Workspace writes still require the existing approve/reject workflow.
- Auto-routed external assignments from voice commands use the same provider recommendation and approval boundary as manual assignments.
- Local CLI external agents run with a minimal environment. The prompt-injection harness asserts that `VOICEOPS_SECRET` is not visible to the external CLI and that forged `VOICEOPS_ACTION_STATUS`, approver, or test-result claims remain untrusted output.

Production must use an external-agent credential secret distinct from `JWT_SECRET`, so auth token rotation and provider-token encryption can be managed independently.

## Prompt Injection Gate

Treat meeting transcript, speaker text, memory items, RAG context, and external agent stdout as untrusted data. The production acceptance path requires:

```bash
cd backend
python scripts/smoke_prompt_injection.py --json
python scripts/product_audit.py --run-harnesses --require-ready --json
```

The prompt-injection harness covers these current-code paths:

- malicious meeting memory used by a RAG answer stays read-only,
- voice patch requests remain preview-first,
- multi-agent patch requests remain preview-first,
- explicit external CLI assignments remain preview-first,
- voice-created auto external assignments remain preview-first,
- external CLI attempts to reveal secrets or forge approval/status metadata are ignored,
- low-privilege users cannot approve patches.
