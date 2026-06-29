# Final Acceptance Audit

Phase 31 adds a machine-readable audit for the original multi-person AI teammate target.

## Command

```bash
cd backend
python scripts/final_acceptance_audit.py --json --require-accepted
```

The audit runs deterministic backend harnesses by default. Use `--skip-harnesses` only for a fast diagnostic from recorded evidence:

```bash
cd backend
python scripts/final_acceptance_audit.py --skip-harnesses --json
```

## What It Proves

- Multiple people can speak in one shared room.
- Speaker labels can be distinguished, mapped to teammates, corrected, and shown with uncertainty.
- The AI teammate can answer meeting questions, route commands, propose code changes, and log actions.
- Short memory, long memory, and local RAG answer with citations.
- The project ontology links people, files, decisions, actions, branches, and history.
- Specialist agents collaborate through coordinator, meeting, memory, code, review, test, and git roles.
- Code-changing work is preview-first, approved by a teammate, branched locally, tested, and audited.
- Returning teammates receive handoff lines with requester, approver, branch, files, and test result.
- The React team console has evidence for live transcript, speaker mapping, memory, agent controls, approval controls, and readiness.
- External Claude/Codex/Cursor/local provider layer can connect credentials and return approval-first patch proposals.
- Production trial packaging includes env template, team onboarding, operator runbook, and security gate.

## Deployment Prerequisites

The final acceptance audit separates product/code acceptance from deployment-specific secrets. A local repo can be accepted while `security_readiness.py` still reports required production actions such as rotating `JWT_SECRET` and replacing seeded demo users. Before inviting a real team into a production deployment, run:

```bash
cd backend
# Replace HF_TOKEN/provider secrets, rotate the generated bootstrap admin password,
# and delete ../voiceops-production-trial/bootstrap-admin.txt first.
python scripts/production_trial_acceptance.py --env-file ../voiceops-production-trial/.env --json --require-accepted
```

This strict production-trial command also validates that the generated environment uses `DEPLOYMENT_ENVIRONMENT=production`, keeps `PRODUCTION_STARTUP_SECURITY_GATE=true`, and would pass the same fail-closed startup gate that runs before the FastAPI app is created.

Do not treat final acceptance as a substitute for production secret rotation and account setup.
