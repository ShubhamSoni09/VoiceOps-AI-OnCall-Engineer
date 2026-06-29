# External Agent Providers

VoiceOps can now model Claude Code, OpenAI Codex, Cursor, and a generic local/open-source coding agent as external coding-agent providers. This is a provider layer, not a browser-session bridge.

For general AI teammate reasoning, see `docs/LLM_RUNTIME.md`. External coding providers are tool/action adapters; they do not own VoiceOps memory, planning, approval policy, or audit history.

## Supported Connection Modes

- `oauth`: preferred when the provider exposes an official OAuth application and agent scopes.
- `api_key`: practical fallback when users bring provider API keys or personal tokens.
- `local_cli`: local workstation mode where the user has already authenticated a provider CLI.

The `cursor` and `local` providers are intentionally CLI-only. Cursor agent execution depends on a locally authenticated Cursor command, and the generic local/open-source provider is launched by a command such as `local-agent`, an Ollama/vLLM wrapper, or a team script. They do not advertise OAuth or API-key setup because VoiceOps should not pretend those are provider-account coding-agent APIs.

VoiceOps must never collect provider passwords, scrape browser cookies, or reuse web sessions. All provider credentials are stored as encrypted backend payloads and API responses only return redacted previews.

## APIs

List provider status:

```http
GET /external-agents/providers
```

Connect API key:

```http
POST /external-agents/providers/{provider}/credentials/api-key
{
  "api_key": "...",
  "account_label": "Claude Team",
  "scopes": ["claude_code:run"]
}
```

Start OAuth:

```http
POST /external-agents/providers/{provider}/oauth/start
{
  "scopes": ["codex:run"]
}
```

Complete OAuth callback:

```http
POST /external-agents/oauth/callback
{
  "provider": "codex",
  "code": "...",
  "state": "..."
}
```

Run an external agent:

```http
POST /external-agents/rooms/{room_id}/runs
{
  "provider": "cursor",
  "mode": "patch",
  "model": "cursor-default",
  "prompt": "fix the health endpoint"
}
```

List provider/task capabilities:

```http
GET /external-agents/capabilities
```

Ask VoiceOps which external agent should handle a task:

```http
POST /external-agents/recommendations
{
  "task": "explain how app.py works",
  "preferred_provider": "claude",
  "mode": "explain",
  "model": "claude-sonnet-4-6"
}
```

The recommendation API is read-only. It classifies the task as `explain_code`, `find_bug`, `review_patch`, `write_patch`, `run_tests`, `summarize_changes`, or `git_status`, maps that to an external run mode, and returns the best connected provider plus alternatives. It does not execute code and it does not bypass approval.

External agent assignments can also use the same contract:

```http
POST /agents/rooms/{room_id}/assignments
{
  "agent_id": "auto",
  "agent_label": "Auto external agent",
  "agent_kind": "external",
  "task": "fix the health endpoint",
  "mode": "patch"
}
```

When that assignment is dispatched, VoiceOps calls the recommendation service, records the selected provider/mode/model in assignment metadata, then runs the selected provider through the normal external-agent adapter. `agent_id: "auto"` is only routing sugar; code-changing output still becomes a pending approval action and the configured workspace is not written before teammate approval.

Voice meeting commands can create the same assignment directly. For example, when a teammate says "AI have the best agent fix that", VoiceOps resolves recent meeting context, creates an `agent_id: "auto"` external assignment, records the recommendation metadata, and auto-dispatches it only when the recommended provider is ready. The result is still a normal `pending_approval` action with a diff preview. If the recommended provider is blocked or unconfigured, the assignment stays queued with blocker metadata instead of failing silently.

`model` is optional. `GET /external-agents/providers` returns `supported_models` and `default_model` for each provider so the UI can offer exact provider choices. The UI also exposes a `custom model ID` path so teams can use newly released models, enterprise aliases, or self-hosted router names without a code change.

Current presets include:

- Claude: `claude-fable-5`, `claude-mythos-5`, `claude-sonnet-4-6`, `claude-sonnet-4-5`, `claude-opus-4-8`, `claude-opus-4-7`, `claude-opus-4-6`, `claude-opus-4-5`, `claude-haiku-4-5`, `claude-haiku-4-5-20251001`, plus family aliases `claude-sonnet`, `claude-opus`, and `claude-haiku`.
- OpenAI/Codex: `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.4-nano`, `gpt-5`, `gpt-5-mini`, `gpt-5-nano`, plus `gpt-default`.
- Cursor: `cursor-default` and `provider-default`; use `custom model ID` for Cursor workspace-specific model names.
- Local/Open-source: `local-default`, `glm-5.2-local`, `qwen-coder-local`, `deepseek-coder-local`, and `provider-default`; use `custom model ID` for LM Studio, Ollama, vLLM, or team-specific local router names.

Exact provider IDs should be preferred when supported. The shorter family names are VoiceOps routing aliases/presets.

## Approval Boundary

External providers can only create a proposal in VoiceOps. For code-changing work, the result is converted to a normal `pending_approval` action with:

- provider id and provider run id,
- selected model,
- diff preview,
- proposed files stored server-side,
- test command,
- requester/audit metadata.

The workspace is not written until a teammate approves the action through the existing approval API. This keeps Claude/Codex/Cursor/local agents from bypassing the team workflow.

## Runtime Boundary

External agent execution now goes through an adapter boundary:

- Patch mode remains `approval_first`; v1 produces a pending proposal and never lets a provider directly write the configured workspace.
- Read-only modes use the mock adapter by default for deterministic local demos and tests.
- Provider API read-only execution can be enabled with `EXTERNAL_AGENT_API_EXECUTION_ENABLED=true`.
  - Claude API key/OAuth credentials call Anthropic Messages API at `POST /v1/messages`.
  - OpenAI/Codex API key/OAuth credentials call OpenAI Responses API at `POST /v1/responses`.
  - Provider HTTP errors are returned as failed external runs with audit metadata; raw tokens are never returned.
- Local CLI read-only execution can be enabled with `EXTERNAL_AGENT_CLI_EXECUTION_ENABLED=true`. In that mode, the configured CLI command runs inside an isolated temporary workspace with `shell=False`, command allowlist validation, timeout handling, and runtime audit metadata.
- The CLI receives context through environment variables: `VOICEOPS_AGENT_PROVIDER`, `VOICEOPS_AGENT_MODEL`, `VOICEOPS_AGENT_MODE`, `VOICEOPS_AGENT_PROMPT`, and `VOICEOPS_PROVIDER_RUN_ID`.

The runtime boundary is intentionally conservative. Real provider-specific CLI/API adapters should plug into this boundary instead of writing directly to the project workspace.

## OAuth Reality

The current implementation has the OAuth resource model and state validation in place for providers that expose OAuth. If a provider token URL is not configured, local development uses a clearly labeled `mock_exchange` credential for integration testing. Production use requires official provider OAuth apps, allowed scopes, and configured token endpoints.

Cursor and local/open-source agents are CLI-only in VoiceOps. To get the real agent behavior, users authenticate those tools outside VoiceOps, then register the local command. VoiceOps wraps that command with sandboxed execution, preview-first diffs, approval, tests, git metadata, and audit logs.

## Security Controls

- `EXTERNAL_AGENT_CREDENTIAL_SECRET` encrypts provider credentials at rest.
- `EXTERNAL_AGENT_ALLOWED_PROVIDERS` limits enabled providers.
- Runtime credential files live under `backend/data` and are gitignored.
- Responses never return raw tokens.
- External runs publish room audit events and create normal approval-first actions.
- Auto-routed voice assignments keep `voice_transcript`, `resolved_context`, recommended provider/model/mode, confidence, and blockers in assignment metadata.
- Meeting text and memory are treated as untrusted input. Even if a transcript says to ignore approval, approve itself, reveal secrets, or write files directly, the external adapter can only produce a pending proposal.
- Provider credential management requires `credentials:manage_own`.
- External agent execution requires `agent:run`.
- Patch approval, rejection, commit, and PR preparation require `agent:approve`.

## Verification

Run these from `backend` when changing external-agent routing or provider adapters:

```bash
python scripts/smoke_external_coding_agent.py --provider claude --json
python scripts/smoke_external_coding_agent.py --provider codex --json
python scripts/smoke_external_coding_agent.py --provider local --json
python scripts/smoke_meeting_closure.py --json
python scripts/smoke_prompt_injection.py --json
python scripts/product_audit.py --run-harnesses --require-ready --json
```

The meeting closure harness proves that voice-created `auto` external assignments can produce a pending approval visible to Bob/Sam. The prompt-injection harness proves that malicious meeting memory cannot make an auto-routed external agent write the workspace, leak service secrets, or forge approval/test status.

## Frontend

The right rail `Connected agents` panel shows Claude Code, OpenAI Codex, Cursor, and local/open-source connection status. It can start OAuth for providers that support it, register local CLI mode, request a provider recommendation, and submit a patch prompt to the selected connected provider. The Work Dashboard assignment queue shows auto-route metadata such as recommended provider, model, confidence, and readiness. The resulting patch appears in the normal Agent actions approval queue.
