# Native Claude four-hour filesystem workload — 2026-10-31 JST

**PASS for this bounded run.** One persistent native Claude session completed
four real filesystem tasks and **21 MCP calls with 21 successful, correlated
replies**. The first and last user-turn starts were **14,400.031 seconds** apart.
The driver recorded **14,418.562 seconds** of monotonic elapsed time. All **14
driver checks** and **20 independent post-run audit checks** passed.

The first recorded connection event was **2026-10-30 15:02:11.280321 UTC**;
the final cleanup sample was **19:02:29.491507 UTC**. In Japan, both occurred
on **October 31, from 00:02 to 04:02 JST**. Durations use the monotonic clock;
the UTC/JST timestamps are the recorded wall-clock observations.

This was Claude Code **2.1.309**, driven through the official Python Agent SDK
**0.2.175**, with observed model **`claude-sonnet-5-5`** and the existing login.
The backend was the official **`@modelcontextprotocol/server-filesystem` 2.1.0**,
restricted to a private synthetic-business workspace. The selected Warden
profile used stdio, explicit `stateless` restart, a **900-second idle timeout**
and **`release_idle_pages: true`**. These memory observations therefore describe
the opted-in page-release profile, not default page-release behavior.

## Work actually completed

| Task | Start, JST | Native turn elapsed | MCP calls | Independent file checks |
| --- | --- | ---: | ---: | ---: |
| Reconcile paid orders and write/read back the baseline | 00:02:12 | 14.672 s | 4 | 3 |
| Correct the shipping boundary and write boundary cases | 01:22:12 | 17.000 s | 4 | 4 |
| Read late adjustments and reconcile the revised total | 02:42:12 | 25.672 s | 6 | 4 |
| Read prior results and write/read back the final audit | 04:02:12 | 16.203 s | 7 | 7 |

The post-run audit reread **18 checks over seven unique files**, independently
of the model's completion text. It also recomputed the business arithmetic:
three paid orders, six units, subtotal **3,950 cents**, initial total **4,450
cents**, adjustments **−150 cents**, revised subtotal **3,800 cents** and final
total **4,300 cents**. At the free-shipping threshold, the corrected inclusive
policy charged **zero** shipping. The original orders and adjustment seed
files retained their expected byte hashes.

Raw native output contains four successful result messages. Every observed
tool call belongs to the fixed MCP allowlist, has a unique call identity and
exactly one successful reply. Each task remained below its limit of 16 calls.
The same hashed native session identity appears in all four results. The audit
did not launch another model request or repeat the four-hour run.

## Persistent owner, idle exits and fresh backends

The live proxy retained **PID 27740**, creation FILETIME
**`134378461321182487`**, throughout the run. The four backend generations were
corroborated by Warden's published backend PID and independently read creation
FILETIME:

| Generation | Backend PID | Creation FILETIME |
| --- | ---: | --- |
| 1 | 25588 | `134378461322364949` |
| 2 | 65056 | `134378509345936438` |
| 3 | 72396 | `134378557433214566` |
| 4 | 62564 | `134378605360307636` |

The result-to-next-turn idle intervals were **4,785.359**, **4,783.000** and
**4,774.328 seconds**, all above 900 seconds. Each interval contains samples
where Warden reports no backend and a fresh handle query confirms that the
preceding backend's exact PID/creation identity has exited. A different
backend identity then handles the next task while the same proxy remains.
Exit times are bounded by approximately 30-second observation intervals;
the report does not assign an exact exit instant between those observations.

The initial `cold` and `cold_before_work` samples had no authoritative backend
PID yet and used the driver's direct-child fallback. **All four tool-result
samples in the first task subsequently published the actual backend identity**
and matched that fallback. A separate read-only handle capture at
**15:09:32 UTC** confirmed that PID 25588 with the recorded FILETIME was
`node.exe`, still alive; the measured child console host had a different PID.

Across the entire run, **16 of 21** tool-result samples already had Warden's
authoritative backend identity. **Five early samples after wake** arrived
before the two-second status refresh: one in task 2 and four in task 4. Their
fallback identities match the later authoritative PID/FILETIME within the
same task. Thus **all four backend generations are corroborated**. This does
not establish that a unique-direct-child heuristic is generally reliable.

After disconnect, Warden reported **zero sessions**, all four known backend
identities and the proxy were absent, and the sampled test tree contained only
the harness finishing its report. A later independent handle readback found
**all 14 recorded test-tree identities absent**, including the native client
and the harness. An unrelated process reusing a numeric PID would not satisfy
the recorded creation identity.

## Metadata and source identity

The client's catalog signature remained unchanged at connection and after
each task. The pinned Warden restart path reinitializes a fresh backend and
compares its initialization metadata and tool catalog before releasing the
held call. Successful post-wake calls and the unchanged source snapshot support
that guarded workflow; this run did not add a separate protocol trace or
counter proving each individual catalog exchange. A cached client signature
alone is not such a trace.

All **11 recorded runtime/config/backend/plan source hashes**, plus the
separate driver hash, matched fresh reads during the closing audit. The
completed native-output and phase-log hashes also matched the final summary.
Native stderr was **zero bytes**. The driver snapshot SHA-256 was
`fc7fafde2416e901e54526db485d0891eb9568862f98b04e7ee8ae281bf6b429`.
Source-role hashes are published without private local paths or configuration
contents in the [sanitized run summary](evidence/native-claude-4h/run-summary.json).

## Observed memory and CPU

The audit used **510 process/phase samples**. It identified **392 confirmed
backend-absent samples**, excluding startup and post-wake samples whose status
had not refreshed. Windows working-set counters describe resident memory;
they do not establish committed-memory release or a before/after saving.

| Measured scope, backend confirmed absent | Minimum | Median | Maximum |
| --- | ---: | ---: | ---: |
| Warden proxy working set | 4.164 MiB | **4.281 MiB** | 4.746 MiB |
| Whole test tree working set | 219.512 MiB | **252.344 MiB** | 305.750 MiB |

The whole test tree includes the Python driver, native Claude client and
creation-validated descendants. It is not the proxy alone or the entire
desktop. In 110 backend-present samples, including the wait before idle
expiry, the median proxy working set was **7.961 MiB** and the median whole
test tree was **336.357 MiB**. These are observations from different phases,
not a controlled estimate of savings caused by page release.

During the three observed backend-absent windows, the pinned proxy's CPU-time
counter increased by **6.484375**, **6.312500** and **5.984375 seconds** over
**3,884.093**, **3,882.032** and **3,873.343 seconds**, respectively. Their combined
increase was **18.781250 CPU seconds across 11,639.468 observed seconds**.
Those counters exclude the backend, model client, harness and console hosts;
they do not cover unobserved edges between samples. SDK tool-use-to-reply
intervals include client scheduling and timestamp quantization and are not
labeled isolated backend execution latency.

## Evidence and limits

- [Sanitized run summary, checks and source hashes](evidence/native-claude-4h/run-summary.json).
- [Correlated calls, file readbacks and recomputed business values](evidence/native-claude-4h/calls-and-files.json).
- [Lifecycle observations, process identities and scoped metrics](evidence/native-claude-4h/lifecycle-metrics.json).
- [Method, SDK references and deliberate execution](native-claude-persistent-method.md).
- [Earlier native checks and diagnostic history](native-validation-2026-10-30.md).

The public JSON was built from explicit allowed fields. Raw account metadata,
prompts, arguments, result bodies, absolute personal paths, configuration,
environment values, tokens and backend diagnostics remain private. Original
session, tool-call and fixture identifiers are replaced by hashed labels;
process PID/creation identities are retained for the lifecycle evidence.

This validates one four-hour native CLI/SDK filesystem workflow with deliberate
stateless restart. It does not establish multi-day production stability,
Desktop GUI behavior, safe loss of arbitrary MCP in-memory state, lower
subscription-quota consumption, billed savings or broad performance
superiority. Earlier diagnostics remain separate from this completed run.
