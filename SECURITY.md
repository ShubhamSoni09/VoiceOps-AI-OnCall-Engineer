# Security Policy

VoiceOps is a local-first technical preview. Do not run it as a production service until the production security gate passes.

## Reporting

Please report security issues privately to the project maintainers before opening a public issue. Include the affected commit, reproduction steps, and whether credentials, workspace files, or meeting data can be exposed or changed.

## Production Gate

Before inviting a real team or enabling external side effects, run:

```bash
cd backend
python scripts/security_readiness.py --require-ready --json
```

The full review is in [docs/SECURITY_REVIEW.md](docs/SECURITY_REVIEW.md).
