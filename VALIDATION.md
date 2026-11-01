# Validation for v0.3.0-alpha.1

This page preserves the v0.3.0-alpha.1 evidence. For the v0.3.1-alpha.1
corrections, reproduced defects and current validation, read the
[2026-11-01 lifecycle audit](docs/audit-2026-11-01.md). The four-hour runs and
performance comparisons below were not repeated for that patch.

Evidence completed on 2026-10-31 JST. Native filesystem workflows, synthetic
performance comparisons and regression tests are separate below. The
[v0.2 record](docs/validation-v0.2.0-alpha.1.md) and
[original alpha record](docs/validation-alpha1.md) remain preserved.

## Native four-hour filesystem sessions

Both runs used the installed native client, an existing login and the official
filesystem MCP backend 2.1.0 restricted to a generated workspace. Four turns
read orders, produce reports, edit a shipping boundary, search adjustments and
audit durable outputs. The data was synthetic; the file operations were real.

| Client | Turns | Successful MCP calls | First-to-fourth turn start | Result |
| --- | ---: | ---: | ---: | --- |
| Codex CLI 0.166.0-alpha.5.1 | 4 | 14 | 14,400.203 s | Passed |
| Claude Code 2.1.309 via Agent SDK 0.2.175 | 4 | 21 | 14,400.031 s | Passed |

Each retained one native session and one proxy PID plus creation identity.
Three actual 900-second idle expirations stopped the prior backend, and each
later turn observed a fresh backend generation. File contents were checked
against predeclared JSON and seed hashes: 18 assertions per client. Final
outputs and all source/backend/config/plan fingerprints were independently
re-read after completion. Cleanup found no live owned backend or proxy and no
live Warden session. No extra model requests were used for this final audit.

Both runs explicitly enabled release_idle_pages with stateless restart. The
normal page-release default is false. They test intermittent work separated
by idle periods, not four hours of constant activity or default-policy native
performance. See the [predeclared method](docs/native-filesystem-method.md),
[Codex result](docs/native-codex-4h-results-2026-10-31.md) and
[Claude result](docs/native-claude-4h-results-2026-10-31.md).

Earlier failed Codex smoke attempts remain in the diagnostic history: one
denied writes under the client approval policy; another observer incorrectly
identified a child process. The driver was corrected before the accepted
two-turn smoke and the single four-hour run. These failures are not erased or
counted as passed product tests. Raw account/client logs remain private.

## Resource comparison

The [six-way comparison](docs/v3-latency-working-set-results-2026-10-31.md)
passed 18/18 repetitions with unchanged current sources and immutable v0.2
baseline. Default v0.3 medians against v0.2 stdio in that same run improved:

- Cold startup: 201.38 to 165.97 ms, 17.58% lower.
- Wake: 82.62 to 66.79 ms, 19.16% lower.
- Idle working set: 30.99 to 26.57 MiB, 14.26% lower.

With explicit page release, idle working set was 10.94 MiB versus 15.23 MiB
for mcp-snooze v0.1.0: 28.17% lower. Default Warden working set remained larger
than mcp-snooze's, and mcp-snooze remained faster cold, warm and on wake.
Page release increased root wake page-fault increments from 13 to 669; it
does not free committed private allocations. A separate local experiment
observed a larger wake-latency penalty. The option stays off by default.
See [its scope and tradeoff](docs/idle-page-release.md).

The report retains raw results, fingerprints, ownership audit and verified
artifact digest. Working-set sums can double-count shared pages. Wake CPU
and page-fault counters cover surviving roots, not the entire stack; zero
observed CPU is not zero work. Three repetitions on one cloud runner do not
establish universal superiority.

## Regression suite and provenance

The [measured candidate CI](https://github.com/Borregro4/mcp-warden/actions/runs/41078627321)
passed **49 Node test entries and 70 Python tests** at
a2bb764c9a9299bd392ad7502d553ad9676bc5af. Node subtests are already included;
internal gateway assertions are not additional tests. Native runs fingerprinted
the same core plus their drivers, private plan and backend artifacts.
Publication incorporates the first-user documentation without changing that core.

Coverage includes protocol leases, callback/request protection, clean idle
wake, metadata revalidation, uncertain outcomes, frame/queue bounds, Windows
PID/creation ownership, suspended assignment through the exact primary thread
handle, failure/handle cleanup, concurrent launch isolation, strict page-release
configuration and native-driver validation. Ordinary CI never invokes a model.

The lightweight runtime uses Python's standard library. npm dependencies stay
pinned for the optional gateway and SDK tests. Native filesystem and Claude
SDK packages are optional test dependencies, not vendored runtime dependencies.
Publication scans explicitly selected files for secret patterns and excludes
raw account logs, tokens, private configuration and backend stderr. A pattern
scan is not a security guarantee.

## Historical endurance

The [v0.2 synthetic hour](docs/soak-stdio-results-2026-10-30.md) passed 9/9
criteria over 3,600.007 seconds; it was not repeated or relabeled as v0.3.
The [earlier gateway attempt](docs/soak-gateway-attempt-2026-10-30.md) missed its
memory objective and stopped early; it remains a failed attempt.

## Limits

Windows is supported; native runs used CPython 3.12.10 and Node.js 24.18.1.
These bounded CLI/SDK workflows do not establish GUI compatibility, multi-day
reliability, absence of leaks, external adoption or performance for every
backend/model. They do not measure token, quota or invoice savings. Idle
sampling is not continuous peak monitoring.

Stateless restart loses backend memory, cursors and selections. It requires
matching initialization/tool metadata and a previously observed unpaginated
catalog. Uncertain outcomes require reconnect; completed calls are never
replayed. Legacy discovery fallback is implemented, not the complete modern
protocol era. Lightweight queues hold 16 entries and frames are limited to
1 MiB. This is not a hard CPU/RAM sandbox or a model router.

Windows Job assignment follows suspended creation and is not atomic with it.
The parent-death gap remains unqualified. Shared descendants require a different
cleanup policy. Status covers the proxy and direct backend, not every descendant
or client app. Backends retain their own file/network access. State inherits
local ACLs and may contain sensitive config, stderr and retained diagnostics;
this alpha does not automatically prune disk history.
