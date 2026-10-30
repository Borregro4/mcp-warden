# Lightweight STDIO endurance — 2026-10-30

The recorded synthetic workload passed **all nine predeclared criteria** over
**3,600.007 monotonic seconds** of active operation. This is one real hour on
hosted Windows, separate from native model checks and accelerated idle tests.

- [Completed workflow](https://github.com/Borregro4/mcp-warden/actions/runs/41060689007)
- Tested commit: `8c4801db380ba3b9c1de059d93e0478f996047e6`.
- Runtime Git blobs match candidate `f6ae705` and feature candidate `1051c27`.
  The soak harness and sampler also match `1051c27`. Later report-only edits
  do not change that runtime.
- [Method](soak-stdio-method.md), [pre-execution plan](evidence/soak-stdio-41060689007/soak-stdio-ci/plan.json),
  [result](evidence/soak-stdio-41060689007/soak-stdio-ci/result.json),
  [all observations](evidence/soak-stdio-41060689007/soak-stdio-ci/observations.jsonl).

The workload used official SDK clients against the real Python CLI, a
synthetic backend and explicit `restart_policy: stateless`. It made no model
calls and operated no production tools. Native Codex and Claude evidence is
linked from [VALIDATION.md](../VALIDATION.md).

## Observed results

| Prior criterion or observation | Result |
| --- | --- |
| Active duration, at least 3,600 seconds | 3,600.0065795 seconds; passed |
| UTC receipt interval | 2026-10-30 13:11:13.577–14:11:14.224 |
| Activity | 120 sequential echo ticks plus the final post-hour echo; correct values and stable active backend PID |
| Reverse callbacks | 24 completed |
| Discovery baseline | Every connection observed the required tools before its first call |
| Configured real idle deadline | 900 seconds |
| Idle disappearance observed | 930.191 seconds after the idle call, within the prior 60-second observation grace |
| Same-connection wake | Passed: proxy PID 2560 remained; backend PID 5244 was gone and a new backend PID 1348 returned the correct value |
| Serial owner churn | Three new proxy/backend pairs observed by PID and creation time, then closed and checked directly |
| Resource samples | 120, at 30-second cadence; zero unreadable tree candidates |
| Sampled full test-tree working-set peak | 160,931,840 bytes = 153.477 MiB; below the prior 256 MiB objective |
| Maximum sampled CPU percentage | 0.2214%, normalized by four logical CPUs |
| Final cleanup | Zero active proxies and zero known child identities found alive; no unknown liveness result |
| Failures | None |

The 930-second value is the **observation time**, not a claim that the internal
timer fired at that exact instant. Status is sampled separately, and the
harness corroborated disappearance by directly querying the old PID and
creation time. The same SDK client then issued the new call without opening a
new proxy connection. It completed after initialization and tool metadata
validation. No prior tool call was replayed.

Memory includes the Node SDK harness, Python proxies, backend fixtures and
validated descendants. This differs from the private-memory metric in the
[four-way benchmark](benchmark-lightweight-2026-10-30.md). Working-set sums can
double-count shared pages. CPU intervals can miss short-lived probes; neither
number is a continuous peak or an application-wide Codex/Claude measurement.

A separate smoke test passed 60.016 seconds of active operation with a
five-second idle deadline. It passed all nine criteria, but is not included in
the hour. Its [plan](evidence/soak-stdio-41060689007/soak-stdio-smoke/plan.json),
[result](evidence/soak-stdio-41060689007/soak-stdio-smoke/result.json) and
[observations](evidence/soak-stdio-41060689007/soak-stdio-smoke/observations.jsonl)
are preserved independently.

## Failed or incomplete attempts retained

The [complete attempt log](evidence/soak-attempts-2026-10-30.json) separates
setup failures, incomplete runs, cancellation and the final successful hour.

- The [earlier gateway attempt](soak-gateway-attempt-2026-10-30.md) ended after
  about 15 minutes. Its HTTP harness omitted session DELETE during churn,
  and its original resource/cleanup attribution had narrower coverage. It is
  not a successful hour and does not validate lightweight STDIO.
- [STDIO attempt 41059011420](https://github.com/Borregro4/mcp-warden/actions/runs/41059011420)
  passed its short smoke test, then aborted at startup of the long test. A
  PID-only sampler attributed 136 identities and about 2.9 GiB to its new root,
  including processes created before that root. This was a demonstrated
  measurement defect, not evidence of Warden consuming that memory.
  Artifact `12978705728` preserves the failed attempt.
- [Attempt 41060425549](https://github.com/Borregro4/mcp-warden/actions/runs/41060425549)
  was cancelled while strengthening incomplete-scope handling. It contributes
  no completed endurance time.

Before the completed run, the sampler was changed to validate creation time at
each parent/child edge. Cleanup directly re-queries recorded identities,
including orphans, and treats access failures as unknown rather than zero.
Seven dedicated sampler regressions passed. Churn requires both identities
before checking their later disappearance. The resource criterion rejects
unreadable candidates. The tested core remained unchanged through these
harness corrections.

## Evidence integrity and limits

Downloaded artifact `12983977589` was **21,130 bytes**, matching GitHub's digest:
`2144ae3642669a63635d15c1878ded492f8a31cbd50a078f09cc7cfa34256500`.
It contained exactly the eight allowlisted plan/preflight/observations/result
files for the smoke and long runs. Each result's plan hash was checked, and the
recorded resource peak was recomputed from all samples.

The selected files are retained in [versioned evidence](evidence/soak-stdio-41060689007/provenance.json)
so they remain inspectable after the artifact's 14-day retention period.
State, runtime tokens, configuration, manifests and stderr were excluded.

One synthetic hour does not establish multi-day reliability, production-server
compatibility, absence of every possible leak, or native-model endurance.
Stateless restart loses backend memory/cursors/selections and remains explicit
opt-in. No quota, token or billed-cost saving is inferred from this result.
