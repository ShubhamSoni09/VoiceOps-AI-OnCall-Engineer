# Goal Roadmap

This roadmap tracks the remaining work toward a production-grade multi-agent AI teammate for live collaborative coding meetings. Each phase must ship with a bug audit, targeted tests, regression tests, and at least one smoke check before the next phase starts.

## Completion Target

VoiceOps is ready when a team can run a meeting where:

- multiple people speak in one shared room,
- the system keeps honest speaker attribution and manual corrections,
- the AI teammate can answer from short and long memory with citations,
- specialist agents can collaborate before proposing code changes,
- code-changing work is approval-first,
- approved changes create local git branches, run tests, and preserve audit metadata,
- returning teammates get a handoff with decisions, tasks, actions, approvals, git state, and open review items.

## Phase Gates

| Phase | Status | Outcome | Quality gate |
| --- | --- | --- | --- |
| 11. Agent review loop | Done | Code, review, test, and git agents can collaborate and request revision. | Multi-agent tests, smoke, backend regression, frontend build. |
| 12. Real agent execution adapters | Done | Test agent validates proposed files in a temp workspace before approval. | Failing patch blocked, smoke, backend regression. |
| 13. Project ontology graph | Done | Room-scoped graph links people, files, memory, actions, branches. | Ontology API tests, backend regression. |
| 14. Long-term memory lifecycle | Done | Approved actions and handoffs archive into deterministic long memory. | Archive idempotency tests, smoke, regression. |
| 15. Speaker validation hardening | Done | Room validation reports unknown labels, confidence, mapping, and verification evidence. | Speaker/live tests, frontend unit/build, speaker calibration E2E, live smoke E2E. |
| 16. Roadmap and cache policy | Done | Make phase order, storage rules, and quality gates explicit. | Docs review, no code behavior regression required. |
| 17. Multi-agent collaboration depth | Done | Agents exchange structured findings, objections, and handoffs across roles. | Agent run trace tests, harness report, timeline audit tests. |
| 18. Cache and persistence hardening | Done | Separate ephemeral cache, durable memory, audit events, and raw external artifacts. | Cache invalidation tests, restart recovery test, store migration test. |
| 19. RAG and ontology integration | Done | Memory Q&A can cite short memory, long memory, ontology nodes, and workspace snippets. | Citation tests, RAG smoke, answer provenance tests. |
| 20. Real speaker demo gate | Done | Real WhisperX path has a strict two-speaker validation gate and visible readiness. | Generated or uploaded two-speaker smoke, live WebSocket smoke, UI readiness check. |
| 21. Multi-user approval closure | Done | Requester, reviewer, approver, branch, tests, and handoff are complete across two clients. | Two-client E2E, branch metadata test, handoff test. |
| 22. Multi-agent autonomy controls | Done | Agent runs expose budget, cancellation, timeout, and recovery state without leaving orphaned work. | Agent cancellation tests, timeout tests, recovery smoke. |
| 23. UI/browser E2E hardening | Done | Browser checks cover meeting-to-approval, memory provenance, speaker readiness, and agent controls without overflow. | Agent controls E2E, full browser E2E suite, frontend unit/build, backend regression. |
| 24. Demo readiness command set | Done | Packaged repeatable quick/local/real-mac operator command profiles and a demo runbook. | Operator script tests, quick smoke command, frontend build, backend regression. |
| 25. Production hardening pass | Done | Added real-mac preflight checks for HF token, ffmpeg, and macOS say with actionable failure output. | Preflight tests, quick smoke command, backend regression. |
| 26. Final multi-agent product review | Done | Added a machine-readable product audit that maps every AI teammate requirement to concrete readiness and harness evidence. | Product audit strong smoke, targeted tests, frontend build, backend regression. |
| 27. GitHub PR adapter | Done | Approved local branches can produce a safe GitHub PR dry-run plan with blockers and exact commands, while real external side effects stay disabled. | API adapter tests, dry-run PR tests, frontend build/unit, product audit strong smoke, backend regression. |
| 28. Production deployment/security review | Done | Added production security readiness gate with STRIDE threats, traceable requirements, mitigations, admin API, CLI script, and configurable CORS/external side-effect settings. | Security readiness tests, CLI smoke, product audit strong smoke, frontend build/unit, backend regression. |
| 29. Real GitHub PR creation adapter | Done | Dry-run PR plans can become real draft GitHub PRs through gated `gh` CLI execution, with base-branch allowlist, disabled-by-default config, failure audit, PR URL metadata, room broadcast, and handoff trace. | Adapter tests, API audit tests, disabled-by-default tests, security gate tests, frontend build, regression. |
| 30. Team onboarding and production runbook packaging | Done | Packaged the system for real team trial usage with production env template, onboarding runbook, account rotation checklist, startup commands, acceptance flow, and onboarding readiness script. | Runbook tests, env template audit, startup smoke, regression. |
| 31. Final acceptance audit | Done | Added a requirement-by-requirement final acceptance audit for the original multi-person AI teammate goal, including product evidence, harnesses, onboarding, and production prerequisites. | Final acceptance audit, product audit, onboarding audit, backend regression, frontend build. |
| 32. External agent provider layer | Done | Added Claude/Codex/Cursor/local provider connection layer with encrypted credential storage, provider status/run APIs, right-rail provider UI, smoke harness, and approval-first patch routing. Cursor/local are CLI-only; Claude/Codex keep API/OAuth read-only where configured and local CLI for code-changing work. | External agent API tests, provider catalogue tests, smoke harness, security readiness, frontend build, regression. |
| 33. Agent selection and model control hardening | Done | Locked the Work Dashboard to expose Claude, Codex, Cursor, local, and auto-routing choices, with provider model lists from the backend catalogue and custom agent naming preserved in readiness copy. | Right rail unit tests, frontend unit/build, external agent API tests, product/final acceptance, backend regression. |

## Storage Policy

| Data | Store | Why | Retention |
| --- | --- | --- | --- |
| Live partial transcript | In-memory session cache | Provisional and replaceable. | Drop on socket close or session expiry. |
| Final speaker segments | Collaboration timeline store | Needed for handoff, memory, and audit. | Durable room-scoped JSON or SQLite. |
| Speaker mappings | Speaker store | Manual identity corrections must survive restart. | Durable room-scoped mapping. |
| Raw audio chunks | Do not store by default | Privacy and disk risk. | None in v1. |
| WhisperX verification report | Speaker verification file | Sanitized evidence for readiness. | Replace latest report. |
| Agent run steps | Agent run store | Needed to inspect multi-agent decisions. | Durable room-scoped run history. |
| Proposed patch content | Approval runtime store | Needed to apply approval safely. | Durable until approved, rejected, or expired. |
| Diff preview | Action approval metadata | Safe to show in UI and audit. | Durable action metadata. |
| Git status and branch metadata | Action approval metadata | Needed for handoff and local workflow trace. | Durable action metadata. |
| Short meeting memory | Collaboration memory store | Fast local meeting Q&A. | Durable room-scoped memory. |
| Long memory archive | Long memory store | Cross-session recall. | Durable, idempotent archive records. |
| RAG index | Rebuildable cache | Derived from timeline, memory, docs, and workspace snippets. | Rebuild on source fingerprint change. |
| Ontology graph | Rebuildable projection | Derived from timeline, memory, actions, and git metadata. | Rebuild on query or projection refresh. |
| Demo evidence | Sanitized evidence file | Proves gates for UI readiness. | Replace latest evidence. |

## Event Rules

- Durable collaboration facts should be append-first: message added, memory recorded, action proposed, action approved, action rejected, branch created, test run completed.
- Projections such as ontology and RAG are rebuildable. They should never be the only copy of an important fact.
- Every derived cache entry needs a source fingerprint or timestamp so stale answers can be detected.
- User-facing answers must expose uncertainty for speaker identity and cite source items for memory and code context.

## Bug Audit Checklist

Run this after every phase:

- Boundary check: no framework or storage detail leaks into pure service logic unless the module already follows that pattern.
- Event check: every user-visible action has requester, actor, timestamp, source, and status.
- Identity check: speaker labels and mapped names remain auditable, and confidence uncertainty is not hidden.
- Approval check: code-changing work is pending approval before file writes.
- Persistence check: restart does not lose durable room state, mappings, actions, or long memory.
- Cache check: derived RAG or ontology data can be rebuilt from durable sources.
- UI check: dense panels do not overflow at desktop or mobile widths.
- Test check: targeted tests, backend regression, frontend unit, frontend build, and relevant smoke all pass.

## Next Implementation Order

All roadmap phases are implemented. Remaining production deployment work is environment-specific: rotate production secrets, replace demo users, set explicit CORS origins, and run `python scripts/security_readiness.py --require-ready --json` in the target deployment.
