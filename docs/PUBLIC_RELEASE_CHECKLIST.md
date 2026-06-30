# Public Release Checklist

Use this before making a public repo, demo video, or tagged technical-preview release.

## Required

- [ ] `git status --short` contains no local `.env`, SQLite, credential, audio, or generated trial bundle files.
- [ ] `make demo-reset` prints `target readiness: ready (100%)`.
- [ ] `make check` passes locally.
- [ ] GitHub Actions CI is enabled and green on the release branch.
- [ ] `LICENSE` is present and the release is marked MIT.
- [ ] `README.md` says this is a technical preview, not production-ready software.
- [ ] Demo login works: `priya@voiceops.dev` / `oncall123`.
- [ ] The seeded `main` room shows meeting timeline, memory, and one pending approval.
- [ ] The promo script in `docs/PROMO_VIDEO.md` matches the current UI.
- [ ] `docs/GETTING_STARTED.md` still matches the current first-run UI.
- [ ] External provider setup clearly states which paths are real OAuth/API/CLI and which remain local demo/mock.
- [ ] Real speaker verification docs cover install, warmup, verification, and failure states.
- [ ] `SECURITY.md` points reporters to the private security path.

## Release Order

1. Clean the repo: remove generated data, private paths, old demo artifacts, and any untracked secrets.
2. Run `make demo-reset && make check`.
3. Record the 45 second X cut from `docs/PROMO_VIDEO.md`.
4. Push the public branch and confirm CI is green.
5. Post the video with the repo link.
6. Pin a follow-up explaining the approval-first safety boundary.

## Do Not Claim Yet

- Production readiness.
- Unattended code changes.
- Real speaker verification without WhisperX/pyannote setup.
- Real Claude Code, Codex, Cursor, or GitHub PR side effects without provider credentials and explicit enablement.
