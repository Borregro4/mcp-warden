# Lightweight Windows comparison observed on 2026-10-30

The lightweight stdio candidate used substantially less managed private memory
than Warden's gateway stack in this workload. It did **not** beat mcp-snooze on
every metric: mcp-snooze retained a smaller idle working set and lower startup,
warm-call and wake latency. This supports a specific measured improvement, not
a universal superiority claim.

This is the current **version-3, identity-validated** comparison. The
[earlier lightweight run on f6](benchmark-lightweight-initial-2026-10-30.md) and
[initial alpha comparison](benchmark-results-2026-10-30.md) remain separate
historical evidence.

## Source and predeclared method

The [comparison run](https://github.com/Borregro4/mcp-warden/actions/runs/41061173880)
passed for commit `1051c2734acc5fcb2302270f8fca4c41fada3c46`.
The [ordinary Windows test run](https://github.com/Borregro4/mcp-warden/actions/runs/41061173924)
passed for the same exact commit: **45 Node test entries and 35 Python tests**,
as counted in its log.

The environment was Windows Server 2025, Node.js 22.23.4 and Python 3.12.10.
The measured interval was 13:12:02–13:14:08 UTC on 2026-10-30.
Only the test harness and attribution checks changed from the f6 candidate;
this comparison does not test a new product core.

The benchmark wrote [plan.json](evidence/benchmark-v3/plan.json) before starting
measured variants. The
[four-variant workload](https://github.com/Borregro4/mcp-warden/blob/f6ae705ce618720875e227fe58b0d9161e9e5d44/docs/benchmark-method.md)
remained fixed. Sampling now reuses the
[canonical ownership selector](https://github.com/Borregro4/mcp-warden/blob/1051c2734acc5fcb2302270f8fca4c41fada3c46/scripts/soak_metrics.py)
through the
[version-3 adapter](https://github.com/Borregro4/mcp-warden/blob/1051c2734acc5fcb2302270f8fca4c41fada3c46/scripts/benchmark_processes.py).

All variants used the same Python fixture with a touched 32 MiB allocation,
one SDK client and one session. Three repetitions per variant rotated their
order; this is not a fully balanced four-order design. Every repetition used a
fresh directory and cache. The idle deadline was accelerated to two seconds.
Source hashes remained unchanged during the run.

## Observed results

All **12 measured repetitions** met the recorded checks: echo content,
reverse callback, a three-second request surviving the two-second idle
deadline, wake behavior, readable samples and cleanup of the observed
PID-plus-creation identities. These are protocol-client measurements; native
Codex/Claude workflows and prolonged stability have their own evidence.

| Observed median | Direct stdio | mcp-snooze v0.1.0 | Warden gateway | Warden lightweight stdio |
| --- | ---: | ---: | ---: | ---: |
| Cold stack startup and first result | 72.90 ms | 105.05 ms | 1164.09 ms | 197.15 ms |
| Warm echo round trip | 0.250 ms | 0.467 ms | 4.723 ms | 0.500 ms |
| Wake result, including reconnect when required | 0.614 ms | 61.90 ms | 657.88 ms | 80.32 ms |
| Active managed private memory | 42.46 MiB | 88.27 MiB | 188.24 MiB | 59.29 MiB |
| Idle managed private memory | 42.46 MiB | 47.03 MiB | 132.46 MiB | 16.77 MiB |
| Idle sum of working sets | 52.61 MiB | 15.29 MiB | 205.97 MiB | 31.41 MiB |
| Observed idle process counts, three runs | 2 / 2 / 2 | 2 / 2 / 2 | 8 / 8 / 8 | 2 / 2 / 2 |
| Explicit first wake errors requiring reconnect | 0 / 3 | 0 / 3 | 3 / 3 | 0 / 3 |

MiB means 1,048,576 bytes. Private memory and working-set bytes are different
Windows metrics. Working-set sums can double-count shared pages. Counts
include the selected descendants of the explicitly spawned variant roots,
including any Windows helpers. Gateway supervisor and connector costs are
included. Warm latency is the upper-middle of 30 samples; other medians use
three observations. CPU snapshots are in the raw evidence, but this report
does not claim normalized CPU utilization or total CPU over time.

Within this run, lightweight stdio used **87.34% less idle private memory than
the gateway variant**, and **64.35% less than mcp-snooze**. These percentages
refer to the private-memory medians above. They do not imply physical RAM
savings across every application. Its working-set sum was larger than
mcp-snooze's and its latencies were higher; those adverse results remain part
of the comparison.

The light profile explicitly declared `restart_policy: stateless` and
discovered tools before sleeping. A clean wake reused the same client
connection, replayed initialization, validated instructions and tool metadata,
and sent the new request once. The gateway variant instead reported an expired
session and required a new client connection, whose error and reconnect time
are retained above. Temporary backend state is lost after reaping. Stateful
profiles do not silently acquire this restart policy.

## Attribution check

A separate soak run exposed a concrete flaw in PID-only tree attribution:
a recycled parent PID can make an unrelated, older process look like a child.
That concern prompted this repeat; it was not treated as a product memory leak.

Version 3 validates each parent/child creation-time edge and rejects processes
created after the snapshot began. Previously observed root birth identities
are checked on later samples. Cleanup checks directly re-query observed
identities, including former orphans. In this run the sampler rejected **six
candidate parent-edge observations** and reported zero unreadable process
candidates. A retrospective audit of the 36 accepted snapshots and 168 row
observations found no invalid accepted time-order edges, root-identity
disagreements or missing recorded parents.

The prior alpha and f6 artifacts were also downloaded and hash-checked, then
their recorded rows were audited. No invalid included edge or root-identity
change was found in their recorded rows. That retrospective check is narrower
than a live PID-reuse guarantee. The
[attribution audit](evidence/benchmark-attribution-audit-2026-10-30.json)
preserves all three scopes and their limits. Recorded JSON is retained for the
[alpha artifact](evidence/benchmark-alpha1/results.json) and
[f6 artifact](evidence/benchmark-f6/results.json), in addition to version 3.

## Limits and evidence

This is a small synthetic comparison on one shared cloud runner. It does not
establish multi-client amortization, browser/JVM/device-server performance,
multi-hour stability, native agent compatibility, subscription savings or
token savings. Three repetitions do not justify broad statistical claims.
Snapshots can miss activity between samples or processes whose identity could
not be proven; unknown candidates are not reported as zero usage.

The workflow uploaded exactly three selected JSON files and checked their text
against generated runtime tokens. It excluded state directories, token files,
backend logs and the third-party binary.

- [Workflow artifact](https://github.com/Borregro4/mcp-warden/actions/runs/41061173880/artifacts/12980555110)
- ZIP SHA-256, also verified after downloading:
  `9434db8b8cfaad4210b1498a3fa46d1b0fc02d68d46c0eb564678fa2ac0363d4`
- [Versioned plan](evidence/benchmark-v3/plan.json)
- [All measured repetitions and resource rows](evidence/benchmark-v3/results.json)
- [Raw summary](evidence/benchmark-v3/summary.json)
- [Summary with run/commit provenance](evidence/benchmark-lightweight-v3-2026-10-30.json)

The artifact has a seven-day retention setting. The selected synthetic JSON
reports are also retained here so the evidence remains inspectable after that
retention period.
