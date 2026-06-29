/**
 * A hardcoded OpenUI Lang program. This is exactly the shape an LLM would stream
 * once a key is wired up; here it stands in so the OpenUI <Renderer> works with
 * zero backend. Validated against the live parser (errors: [], unresolved: []).
 *
 * Syntax: `identifier = Component(arg1, arg2, ...)`, positional args, references
 * resolved after parse. See frontend/src/openui/library.jsx for the components.
 */
export const WORKSPACE_BRIEFING = `root = Briefing("Meeting closure for app.py", [summary, metrics, findings, note])
summary = Summary("Priya asked the agent to keep the patch small, Bob is waiting to review the proposed app.py change, and the latest handoff needs the test result before the task can close.")
metrics = MetricGrid([m1, m2, m3, m4])
m1 = Metric("Open tasks", "2", "warn")
m2 = Metric("Pending patches", "1", "warn")
m3 = Metric("Mapped speakers", "2", "ok")
m4 = Metric("Tests run", "1", "ok")
findings = Findings([f1, f2, f3])
f1 = Finding("cause", "The open task is the unresolved app.py health check fix from the meeting memory.")
f2 = Finding("commit", "No commit has been created yet; approval should create a local branch first.")
f3 = Finding("fix", "Approve the pending patch, run the configured tests, then update handoff with the result.")
note = Callout("warn", "Patch output must stay pending until a teammate approves it.")`

export const INCIDENT_BRIEFING = WORKSPACE_BRIEFING
