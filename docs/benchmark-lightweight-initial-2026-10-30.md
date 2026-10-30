# Initial lightweight Windows comparison observed on 2026-10-30

Historical f6 result. The [current identity-validated comparison](benchmark-lightweight-2026-10-30.md) supersedes it for current measurements. Its selected synthetic [plan](evidence/benchmark-f6/plan.json), [results](evidence/benchmark-f6/results.json) and [summary](evidence/benchmark-f6/summary.json) remain preserved.

The lightweight stdio candidate reduced managed private memory substantially
compared with Warden's gateway stack in this workload. It did **not** beat
mcp-snooze on every metric: mcp-snooze had a lower idle working set and lower
startup, warm-call and wake latency. This result supports a specific resource
improvement, not a universal superiority claim.

This new result preserves the [earlier alpha comparison](benchmark-results-2026-10-30.md),
which found Warden's gateway stack expensive. The two reports are separate:
the table below compares all four variants within one new runner execution.

## Source and method

The [comparison run](https://github.com/Borregro4/mcp-warden/actions/runs/41058954673)
completed successfully for commit
`f6ae705ce618720875e227fe58b0d9161e9e5d44`. The
[ordinary Windows test run](https://github.com/Borregro4/mcp-warden/actions/runs/41058954613)
also passed for that exact commit.

Environment observed in its log: Windows Server 2025, Node.js 22.23.4 and
Python 3.12.10. The measured interval was 12:55:21–12:57:20 UTC on 2026-10-30.

See the [frozen four-variant methodology](https://github.com/Borregro4/mcp-warden/blob/f6ae705ce618720875e227fe58b0d9161e9e5d44/docs/benchmark-method.md).
The benchmark wrote its plan before starting measured variants. All variants
used the same Python fixture, a touched 32 MiB allocation, the same SDK client
and one session. Three repetitions per variant rotated their order; this is not
a fully balanced four-order design. Each repetition had a fresh directory and
cache. The idle deadline was accelerated to two seconds. The source hashes
remained unchanged during the run.

## Observed results

All **12 measured repetitions** met the recorded checks: echo content, callback,
three-second request surviving the two-second idle deadline, wake behavior,
readable process samples and cleanup of the observed PID-plus-creation
identities. These are protocol-client tests, not model-driven application calls.

| Observed median | Direct stdio | mcp-snooze v0.1.0 | Warden gateway | Warden lightweight stdio |
| --- | ---: | ---: | ---: | ---: |
| Cold stack startup and first result | 74.54 ms | 100.85 ms | 1083.63 ms | 200.72 ms |
| Warm echo round trip | 0.225 ms | 0.378 ms | 4.162 ms | 0.515 ms |
| Wake result, including reconnect when required | 0.526 ms | 59.96 ms | 656.47 ms | 81.07 ms |
| Active managed private memory | 42.39 MiB | 88.30 MiB | 188.32 MiB | 58.52 MiB |
| Idle managed private memory | 42.39 MiB | 47.11 MiB | 132.99 MiB | 16.00 MiB |
| Idle sum of working sets | 52.53 MiB | 15.23 MiB | 205.45 MiB | 31.11 MiB |
| Observed idle process counts, three runs | 2 / 2 / 2 | 2 / 2 / 2 | 8 / 8 / 8 | 2 / 2 / 2 |
| Explicit first wake errors requiring reconnect | 0 / 3 | 0 / 3 | 3 / 3 | 0 / 3 |

MiB means 1,048,576 bytes. Private memory and working-set bytes are different
Windows metrics. Working-set sums can double-count shared pages. Counts include
all observed descendants of the explicitly spawned variant roots, including
any Windows helpers. The gateway's supervisor and connectors are included.
Warm latency is the upper-middle sample among 30 calls; other medians use
three observations. CPU snapshots are present in the raw evidence, but this
report does not claim normalized CPU utilization or total CPU over time.

Within this run, lightweight stdio used **87.97% less idle private memory than
the gateway variant**, and **66.03% less than mcp-snooze**. These percentages
refer to the private-memory medians in this table, not physical RAM savings
across every application. The adverse result remains visible: its idle
working-set sum was larger than mcp-snooze's, and its latencies were higher.

The lightweight profile explicitly declared `restart_policy: stateless` and
discovered tools before sleeping. A clean wake reused the same client
connection, replayed initialization, validated instructions and tool metadata,
and then sent the new request once. The gateway variant instead reported an
expired session and required a new client connection; that error and reconnect
time are retained in its results. Temporary backend state is lost after either
backend is reaped. Stateful/manual profiles are a different contract and do not
silently acquire this restart policy.

## Limits and evidence

This is a small synthetic comparison on one cloud runner. It does not establish
multi-client amortization, performance for real browsers/JVMs/device servers,
multi-hour stability, native Codex/Claude compatibility, subscription savings
or token savings. Those require their own evidence. Other work on a shared
runner can affect timing. Three observations per variant do not justify broad
statistical claims.

The workflow uploaded exactly three selected JSON reports: `plan.json`,
`results.json` and `summary.json`. Its upload step checked the selected text
against the generated runtime tokens and did not upload state directories,
token files, backend logs or the third-party binary.

- [Artifact](https://github.com/Borregro4/mcp-warden/actions/runs/41058954673/artifacts/12978457631)
- Artifact ZIP SHA-256: `cae815303cbed1274c7a2ac4e9d79ab278dc1717df34a345648980f55a4692cb`
- [Versioned summary extracted from the workflow log](evidence/benchmark-lightweight-2026-10-30.json)

The artifact has a seven-day retention setting. The tested source commit,
methodology and dated summary remain identifiable after that retention period.
