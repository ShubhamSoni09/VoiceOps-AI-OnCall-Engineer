/**
 * A hardcoded OpenUI Lang program. This is exactly the shape an LLM would stream
 * once a key is wired up; here it stands in so the OpenUI <Renderer> works with
 * zero backend. Validated against the live parser (errors: [], unresolved: []).
 *
 * Syntax: `identifier = Component(arg1, arg2, ...)`, positional args, references
 * resolved after parse. See frontend/src/openui/library.jsx for the components.
 */
export const INCIDENT_BRIEFING = `root = Briefing("checkout-api 500s on /v2/charge", [summary, metrics, findings, note])
summary = Summary("Deploy a3f9c2 raised request concurrency to 64 but left the DB pool at 20. The pool is exhausted, 64 requests are queued, and every one times out at 5s.")
metrics = MetricGrid([m1, m2, m3, m4])
m1 = Metric("Error rate", "4.7%", "bad")
m2 = Metric("p99 latency", "1,840ms", "warn")
m3 = Metric("Throughput", "312 req/s", "ok")
m4 = Metric("Error budget", "87.3%", "warn")
findings = Findings([f1, f2, f3])
f1 = Finding("cause", "Connection pool too small for the new concurrency limit")
f2 = Finding("commit", "Introduced by a3f9c2 (raise worker concurrency to 64) by m.bell")
f3 = Finding("fix", "Raise db.pool.max 20 to 80, add a 2s acquire timeout")
note = Callout("warn", "Proposed fix is in PR #482. The deploy preview needs your approval.")`
