# Windows comparison observed on 2026-10-30

This result does **not** support a claim that MCP Warden currently uses fewer
resources or has lower latency than the alternatives in this workload.

The [Windows comparison run](https://github.com/Borregro4/mcp-warden/actions/runs/41053233941)
completed successfully on Windows Server 2025, Node.js 22.23.4 and Python 3.12.10.
Its tested source commit is `cf6887f6f3190fe388dcf5818f8b88d1d689169d`;
Warden's runtime in that branch is the alpha runtime from
`e67fb8848c07bfdd129b3666726ff8edc6e21ad9`.
The [ordinary Windows test run](https://github.com/Borregro4/mcp-warden/actions/runs/41053233807)
also passed on this source.

## Fixed scope and observed outcomes

See [the frozen three-variant methodology](https://github.com/Borregro4/mcp-warden/blob/cf6887f6f3190fe388dcf5818f8b88d1d689169d/docs/benchmark-method.md). Three repetitions per variant ran
serially on the same runner, with rotated variant order. All variants used one
SDK client and the same Python backend with a touched 32 MiB resident allocation.
A new cache and working directory were used for every repetition. The idle
deadline was accelerated to two seconds.

All **9 measured repetitions** met the recorded checks: echo content, reverse
callback, three-second request surviving the idle deadline, wake behavior with
explicit reconnection when required, readable resource samples, and cleanup of
the observed PID-plus-creation identities. Source hashes did not change during
the run. This says nothing about days of stability or native model workflows.

| Observed median | Direct stdio | mcp-snooze v0.1.0 | MCP Warden alpha |
| --- | ---: | ---: | ---: |
| Cold stack startup and first result | 70.15 ms | 110.06 ms | 1196.22 ms |
| Warm echo round trip | 0.249 ms | 0.429 ms | 4.925 ms |
| Wake result, including explicit reconnect if needed | 0.530 ms | 57.34 ms | 723.82 ms |
| Active managed private memory | 42.64 MiB | 88.30 MiB | 187.31 MiB |
| Idle managed private memory | 42.64 MiB | 47.12 MiB | 131.95 MiB |
| Idle sum of working sets | 52.80 MiB | 15.18 MiB | 204.43 MiB |
| Observed idle managed process counts, three runs | 2 / 2 / 2 | 2 / 2 / 2 | 8 / 8 / 8 |
| Explicit first wake errors requiring reconnect | 0 / 3 | 0 / 3 | 3 / 3 |

MiB means 1,048,576 bytes. Private memory and working sets are different Windows
metrics; working-set sums can double-count shared pages. Process counts include
all observed descendants of the explicitly spawned variant roots, including
Windows helper processes. Warden's supervisor and connector are included.
Warm latency uses the upper-middle value of the 30 recorded warm samples;
the other medians use three observations.

## What this changes

Warden released backend memory after idle expiry, but the remaining supervisor
and connector stack cost more in this single-session case than the direct
backend or mcp-snooze. mcp-snooze also paid an active-process overhead; its idle
working set was lower than the direct backend in this run. Warden's protocol
latency was higher, although a few milliseconds of warm transport overhead
should not be presented as a measured application-level user-experience delay.

Warden explicitly reported an expired session and required client reconnection
on all three wake attempts. That behavior is preserved in the latency and
error counts. mcp-snooze restarted its backend without an application-visible
error in these three attempts. Both lifecycle approaches lose temporary backend
state after a reap; the fixture does not establish correctness for arbitrary
stateful servers.

These measurements prioritize reducing the resident connector/supervisor stack
and improving explicit recovery before making resource-efficiency claims.
Native HTTP clients, multiple simultaneous clients, heavier backends and
different operating environments require their own same-workload comparisons.
No general superiority, subscription saving or token saving follows from this
small synthetic result.

## Evidence

The run uploaded exactly three selected JSON report files: `plan.json`,
`results.json` and `summary.json`. It did not upload runtime state, bearer-token
files, backend logs or the third-party binary.

- [Artifact](https://github.com/Borregro4/mcp-warden/actions/runs/41053233941/artifacts/12975337522)
- Artifact ZIP SHA-256: `161015e4f6cbaa7df956b7bf980b6969119070ac2a04fa09aab2e88de66748a2`
- mcp-snooze upstream release archive digest was checked before execution.
- The summary and per-repetition status lines are also present in the workflow log.

Artifacts have a seven-day retention setting. The tested commit, methodology and
this dated result remain identifiable after that retention period.
