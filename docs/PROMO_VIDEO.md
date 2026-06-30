# Promo video script

## Positioning

VoiceOps is an open-source AI coworking room for human engineering teams and coding agents. It remembers the room, answers context questions, and routes Claude/Codex/Cursor/local code work through approval-first patches.

Do not claim production readiness, full autonomy, or unattended code changes. Say "open-source technical preview" or "local demo".

## X launch angle

Hook:
"I built an open-source team room where humans, Codex, and Claude can cowork on code without giving agents direct write access."

Show, do not explain:

1. Humans join the same room.
2. The room remembers meeting context.
3. A teammate asks for code work.
4. Claude/Codex appears as a connected coding agent.
5. The patch lands as pending approval.
6. A human approves or rejects.

One-line positioning:
"Slack for the meeting, memory for the context, approval gate for the code."

## 60-90 second cut

1. Problem, 5 seconds
   "In coding meetings, decisions, context, and follow-up patches get scattered."

2. Command dock, 10 seconds
   Show the main console and the bottom command dock.
   Say: "VoiceOps gives the team one place to ask memory, request review, or start code work."

3. Memory, 15 seconds
   Type: "what did we decide?"
   Show a cited memory answer or recent meeting context.

4. Code workflow, 20 seconds
   Type: "review the repo status" or "prepare a small patch".
   Show that the AI teammate can inspect context and queue work.

5. Approval-first safety, 15 seconds
   Show the approval area or explain that code changes wait for human approval.
   Say: "The agent can propose work, but writes stay auditable and approval-first."

6. Close, 10 seconds
   Say: "VoiceOps is an open-source technical preview for meeting-native AI engineering work."

## 45 second X cut

1. Start on the product, 3 seconds
   Say: "This is VoiceOps: an open-source room for human teams and coding agents."

2. Show team room, 7 seconds
   Click `Copy invite`.
   Say: "Humans join the same room. The AI teammate stays in the room too."

3. Ask memory, 10 seconds
   Type: `what is still open?`
   Say: "The room remembers decisions and open work."

4. Show coding agents, 8 seconds
   Open `Agent setup` -> `Coding agents`.
   Say: "Claude, Codex, Cursor, or local agents connect here."

5. Show approval, 12 seconds
   Open the waiting patch.
   Say: "Agents propose patches. Humans approve before anything writes."

6. Close, 5 seconds
   Say: "Local-first, approval-first, open source."

## X post copy

```text
I built VoiceOps: an open-source AI coworking room for engineering teams.

Humans join one room.
The room remembers meeting context.
Claude/Codex/Cursor can propose code work.
Patches stay approval-first.

Local demo, technical preview.
Repo: <link>
```

Thread follow-up:

```text
The important design choice: coding agents do not get silent write access.

They create proposals with diffs, metadata, and audit trail.
Humans approve/reject in the same room where the decision happened.
```

## Capture notes

- Use the React UI at `http://127.0.0.1:5191/`.
- Keep the browser width wide enough to show the main feed and command dock.
- Hide unrelated setup panes unless they are the point of the shot.
- Use demo credentials only.
- Do not show `.env`, tokens, local SQLite contents, or private repository paths.
- Record in light mode unless dark mode looks better after one test shot.
- Keep zoom at 100%; crop later instead of zooming the browser.
- Use a 16:9 master and crop a second 4:5 or 1:1 cut for X.

## Exact Demo Path

Reset before recording:

```bash
make demo-reset
make dev
```

Use these on-screen tasks:

1. Sign in with `priya@voiceops.dev` / `oncall123`.
2. Show the seeded timeline and pending approval.
3. Type `what is still open?`.
4. Type `review the repo status`.
5. Show Work and the waiting Approval patch.
6. Open Agent setup briefly and show `Connect Claude or Codex`.
7. Say real WhisperX speaker verification uses local setup and the readiness command in `README.md`.
8. End on the approval-first patch state.

Do not approve the patch during the main video unless the point of the cut is the approval workflow.
