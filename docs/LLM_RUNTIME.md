# LLM Runtime Contract

VoiceOps treats the model as a user-provided reasoning backend. The product boundary is memory, planning, tools, approvals, audit, and multi-agent coordination.

## Boundary

- LLM providers generate text or structured JSON.
- VoiceOps decides what context is sent, which tools exist, whether an action is read-only or code-changing, and who must approve it.
- Code-changing output is never written directly by a provider. It becomes a `pending_approval` action with diff, proposed files, requester, provider metadata, and test plan.
- Provider credentials are connected through official API keys, OAuth flows, or local CLI auth. VoiceOps must not collect web passwords, scrape cookies, or reuse browser sessions.

## Backend Port

The shared runtime lives under `app.llm`:

- `LLMMessage`: system/user/assistant message.
- `LLMRequest`: messages, purpose, response format, temperature, max tokens, metadata.
- `LLMResponse`: content, provider, model, usage, metadata.
- `LLMRuntime.generate(request)`: single call site for reasoning.

Current adapters:

- `mock`: deterministic offline runtime for tests and local demos.
- `openai`: OpenAI Chat Completions through `OPENAI_API_KEY`.
- `anthropic`: Anthropic Messages API through `ANTHROPIC_API_KEY`.
- `openai_compatible`: OpenAI Chat Completions compatible endpoint for GLM, vLLM, LM Studio, Ollama-compatible gateways, or similar local/open-source runtimes.
- `bedrock`: AWS Bedrock Claude through AWS credentials and `BEDROCK_MODEL_ID`.

## Current Use

VoiceOps routes these model-backed tasks through `LLMRuntime`:

- intent extraction for non-mock providers
- multi-agent planning
- read-only AI teammate reasoning
- memory answer synthesis
- patch proposal generation before human approval

The workspace orchestrator, approval APIs, collaboration memory, and audit log remain outside provider control.

## User Connections

Environment variables provide the deployment default. Individual users can connect their own model credential through:

- `GET /llm/providers`
- `POST /llm/providers/openai/credentials/api-key`
- `POST /llm/providers/anthropic/credentials/api-key`
- `POST /llm/providers/openai_compatible/credentials/api-key`
- `POST /llm/providers/openai/preflight`
- `POST /llm/providers/anthropic/preflight`
- `POST /llm/providers/openai_compatible/preflight`
- `DELETE /llm/providers/openai/credential`
- `DELETE /llm/providers/anthropic/credential`
- `DELETE /llm/providers/openai_compatible/credential`

When a user has a connected model credential, VoiceOps can use that provider and selected model for that user's voice and multi-agent requests. If no user connection exists, it falls back to the environment defaults, then to `mock` in local/test setups.

Example GLM/OpenAI-compatible connection:

```json
{
  "api_key": "",
  "model": "glm-5.2",
  "base_url": "http://127.0.0.1:8000/v1",
  "account_label": "Local GLM"
}
```

If the endpoint requires a token, put it in `api_key`; otherwise leave it empty and VoiceOps uses a local placeholder.

Example Anthropic connection:

```json
{
  "api_key": "sk-ant-...",
  "model": "claude-sonnet-4-5",
  "account_label": "Team Claude"
}
```

## Per-Agent Routing

Humans can choose the LLM per agent role at room scope:

- `GET /agents/rooms/{room_id}/llm-routing`
- `PUT /agents/rooms/{room_id}/llm-routing`

Example:

```json
{
  "role": "code",
  "provider": "openai_compatible",
  "model": "glm-5.2",
  "base_url": "http://127.0.0.1:8000/v1"
}
```

Supported roles: `coordinator`, `meeting`, `memory`, `code`, `review`, `test`, `git`.

Supported route providers: `default`, `mock`, `openai`, `anthropic`, `openai_compatible`, `bedrock`.

`default` means: use the requester's most recently connected valid LLM provider if present, otherwise the environment default. Role overrides are recorded in run metadata and pending action approval metadata.

## Configuration

```bash
LLM_PROVIDER=mock        # mock | openai | anthropic | openai_compatible | bedrock
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o-mini
ANTHROPIC_API_KEY=...
ANTHROPIC_MODEL=claude-sonnet-4-5
OPENAI_COMPATIBLE_API_KEY=
OPENAI_COMPATIBLE_BASE_URL=http://127.0.0.1:8000/v1
OPENAI_COMPATIBLE_MODEL=glm-5.2
LLM_CONNECTION_STORE_PATH=backend/data/llm_connections.json
AGENT_LLM_ROUTES_PATH=backend/data/agent_llm_routes.json
BEDROCK_MODEL_ID=anthropic.claude-3-5-sonnet-20241022-v2:0
BEDROCK_REGION=us-east-1
```

For connected external coding tools, use `/external-agents` provider credentials. Those providers can propose work, but VoiceOps still owns the approval-first action pipeline.
