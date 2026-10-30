# Validation for v0.2.0-alpha.1

Evidence recorded on Windows on 2026-10-30. This page separates native model
checks, regression tests, a controlled synthetic comparison and endurance.
The first alpha's original record is [preserved here](docs/validation-alpha1.md).

## Native clients

- **Codex CLI 0.166.0-alpha.5.1**, the binary installed with the local Codex
  app, using `gpt-6.1-sol`: three actual MCP calls, exact output values,
  common backend identity, clean exit and no remaining live session.
  All seven checks passed in 12.703 seconds. An earlier gateway check also
  passed with CLI 0.164.0. [Evidence and diagnostic history](docs/native-codex-validation-2026-10-30.md).
- **Claude Code 2.1.309**, invoked through official Agent SDK 0.2.175 with the
  existing installed executable: two real user turns on the same stdio
  connection, a three-second clean idle stop and a fresh backend on wake.
  Six checks passed, exit 0 and no stderr, in the final 13.641-second run.
  The observed model was `claude-sonnet-5-5`.
  [Evidence, source hashes and diagnostic history](docs/native-validation-2026-10-30.md).

These are bounded CLI workflows using an existing login, not GUI tests or proof
of compatibility with every app build/model. Native Codex persistent idle/wake
was not tested. Native checks are opt-in and excluded from ordinary tests/CI.
Raw account output and credentials are not published.

## Regression suite

The final local `npm test` passed **45 Node test entries and 22 Python tests**.
Node subtests are included in 45; the gateway's 34 assertions are not counted
as additional integration tests. The suite also passed on hosted Windows for
[candidate f6ae705](https://github.com/Borregro4/mcp-warden/actions/runs/41058954613).
Thirteen added measurement-harness regressions passed separately. The updated
[Windows CI run for 1051c27](https://github.com/Borregro4/mcp-warden/actions/runs/41061173924)
passed **45 Node entries and 35 Python tests**. The local run used Python
3.12.10 and Node.js 24.18.1; CI uses Python 3.12 and Node.js 22. Runtime source
remains identical to f6ae705; only harnesses/tests and reports changed afterward.

Coverage includes authenticated HTTP sessions, request/callback leases,
capacity rejection, bounded shutdown, real Windows identity/usage handles,
private Job cleanup, root-only cleanup with shared descendants, abrupt proxy
exit, failed-spawn cleanup, and the real Python CLI with SDK clients.

New lightweight regressions cover clean stateless wake without replay,
manual reconnect by default, tool schema/annotation/instruction changes,
missing or paginated discovery, cancellation, separate string/numeric IDs,
long requests and reverse callbacks, activity/partial frames across the idle
deadline, oversized frames, bounded stalled backend writes, and client EOF
during a blocked write. Pre-initialize `server/discover` receives a correlated
method error so modern clients can fall back to legacy initialization.

## Resource comparison and endurance

The [first-alpha comparison](docs/benchmark-results-2026-10-30.md) showed the
original gateway consumed more memory and added delay against direct MCP and
mcp-snooze. That adverse result motivated the lightweight implementation;
it remains published rather than being replaced by a claim of improvement.

The [four-way Windows comparison](docs/benchmark-lightweight-2026-10-30.md)
passed all 12 repetitions using the hardened PID/creation-time sampler.
Lightweight stdio's median idle private memory was **16.77 MiB**, versus
**132.46 MiB** for Warden gateway and **47.03 MiB** for mcp-snooze. Its idle
working set and latency were higher than mcp-snooze's. See the full report for
the same-host workload, raw evidence and measurement limits.

The [completed STDIO endurance](docs/soak-stdio-results-2026-10-30.md) passed
all nine prior criteria over **3,600.007 monotonic seconds**: 120 active ticks,
24 callbacks, disappearance after a configured 900-second idle interval,
same-connection wake, three serial owner replacements and final cleanup.
Its 120 samples peaked at **153.48 MiB** of test-tree working set against the
prior 256 MiB objective. The native model checks are separate from this
synthetic SDK workload. The [plan](docs/soak-stdio-method.md), raw observations,
source hashes and artifact digest are preserved with the result.
The earlier [gateway endurance attempt](docs/soak-gateway-attempt-2026-10-30.md)
stopped after about 15 minutes and missed its memory objective. It does not
count as a passed hour.

## Publication checks

`npm audit --omit=dev` reported zero known advisories on 2026-10-30. This is a
point-in-time database result, not a security guarantee. Dependencies remain
pinned in `package-lock.json`; the lightweight runtime uses Python's standard
library and does not require npm packages.

Gitleaks 8.30.1 scanned all 83 explicitly selected publication files in two
bounded batches, found no matching secret patterns and verified originals
unchanged. This is pattern-scan coverage, not a security guarantee. No private
native-client logs, runtime tokens, user configs or authentication files are
included. Optional Claude SDK packages are not vendored/runtime dependencies.

## Limits

Windows is the supported target. A single synthetic hour cannot establish
multi-day reliability, production adoption, absence of leaks or superiority
across workloads. Memory samples are not continuous peaks; private bytes and
working sets are different measurements. Reduced RAM does not prove token,
quota or invoice savings.

Stateless restart is explicit opt-in and loses backend memory, cursors and
selections. It requires a previously observed unpaginated tool catalog and
matching initialization/tool metadata. Uncertain outcomes require reconnect;
completed tool calls are never replayed. Legacy fallback is implemented, not
the complete 2026-07-28 protocol era.

Lightweight queues have 16 entries, frames are limited to 1 MiB and blocked
backend writes have a deadline. This is not a hard CPU/RAM sandbox. Gateway
admission limits do not apply to standalone stdio proxies. CLI telemetry covers
the proxy/bridge and direct backend, not every descendant or the client app.

Windows Job assignment follows suspended creation; it is not atomic with
creation. The parent-death gap between these operations is not qualified.
Root-only cleanup allows shared descendants outside Warden's Job; an outer
client/CI Job can still impose its own policy. Backends remain trusted local
programs with their own file/network access.

State inherits Windows directory ACLs and may contain sensitive config,
backend stderr and retained manifests/status. There is no automatic disk
pruning, full dashboard, model routing or subscription quota management.
