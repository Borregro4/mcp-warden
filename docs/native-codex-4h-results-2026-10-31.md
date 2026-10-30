# Native Codex four-hour filesystem acceptance — 2026-10-31 JST

**Passed:** one persistent native Codex app-server/thread completed four fixed
filesystem tasks and 14 successful MCP calls. The actual first-to-fourth
`turn/start` interval was **14,400.203 seconds**. Three idle backend exits and
three subsequent generations were verified while the same Warden proxy stayed
connected. Expected files, immutable inputs, catalog hashes, frozen sources and
cleanup were independently checked after completion.

The run started on October 30 UTC and finished on October 31 in Japan. This is
a completed run, separate from the earlier short echo acceptance and synthetic
SDK soak tests. The audit started no model, client or backend.

## Recorded conditions and timing

The installed client reported `codex-cli 0.166.0-alpha.5.1`, model
`gpt-6.1-sol`, reasoning effort `low`. The backend was the pinned official
`@modelcontextprotocol/server-filesystem` package, version `2.1.0`, restricted
to the synthetic workspace. Warden used stdio, explicit `stateless` restart,
a 900-second idle timeout and `release_idle_pages: true`.

The client exposed seven allowlisted fixture tools. Only `write_file` and
`edit_file` received scoped `approve` permission. Ten unrelated configured MCP
servers were disabled for the ephemeral test thread; apps, plugins, hooks,
memory and built-in shell/code execution were disabled. Effective configuration
response bodies were excluded from the raw log. Global configuration was not
edited.

| Event | UTC | Japan time |
| --- | --- | --- |
| First turn started | Oct 30, 15:12:08.033 | Oct 31, 00:12:08.033 |
| Fourth turn started | Oct 30, 19:12:08.236 | Oct 31, 04:12:08.236 |
| Fourth turn completed | Oct 30, 19:12:16.923 | Oct 31, 04:12:16.923 |

The recorded workload lasted 14,409.484 seconds; total driver time including
startup and shutdown was 14,410.828 seconds. Four logical model-backed turns
were submitted, without a driver retry in this long run. Tool loops within a
turn can involve multiple model responses; four turns does not mean four
upstream inference requests.

## Actual file work and independent verification

| Stage | Start offset | Successful MCP tools | Verified effect |
| --- | ---: | --- | --- |
| Paid orders | 0.000 s | `read_multiple_files`, `write_file`, `read_text_file` | Three paid orders, six units, subtotal 3,950 cents, shipping 500, total 4,450. |
| Boundary policy | 4,800.187 s | `read_text_file`, `edit_file`, `write_file`, `read_text_file` | Changed the threshold comparison to inclusive; shipping at 3,999/4,000/4,001 cents is 500/0/0. |
| Reconciliation | 9,600.203 s | `search_files`, `read_multiple_files`, `write_file`, `read_text_file` | Located late adjustments; applied −150 cents once; adjusted subtotal 3,800 and total 4,300. |
| Final audit | 14,400.203 s | `read_multiple_files`, `write_file`, `read_text_file` | Re-read prior artifacts and wrote the final inclusive-boundary summary while preserving earlier results. |

All 14 authoritative `item/completed` MCP events were re-read, matched to their
turns and checked for successful status, the correct server, permitted tools
and paths inside the workspace. Four corresponding `turn/completed` events
were found. No unrelated shell, native file-edit, web or delegation item was
observed. The model's final text was not an acceptance criterion.

The audit re-read **18 planned file checks**, including repeated immutable
input hashes and preserved earlier outputs. It also recomputed the business
arithmetic independently from the actual orders, adjustments and final policy,
then compared all four output JSON files. All checks passed. See the structured
[turn and file evidence](evidence/native-codex-4h/turns.json) and
[audit summary](evidence/native-codex-4h/summary.json).

## Persistent identities and idle/wake

The app-server retained PID **28768**, creation FILETIME
**134378467267302281**, across all four recorded phases. The driver retained
PID **76852**, FILETIME **134378467262789773**. One initialization and one
thread creation were observed; all four turn requests targeted the same thread.
Only a SHA-256 of that private thread identifier is published.

Warden's proxy remained PID **34612**, FILETIME **134378467271170085**.
Backend identities came from Warden's published backend PID plus native
creation-time validation, rather than an assumed child process.

| Backend generation | PID | Creation FILETIME | Before the next turn |
| --- | ---: | --- | --- |
| 1 | 54040 | 134378467272642839 | Native identity no longer alive; expired status had no backend. |
| 2 | 36432 | 134378515347098014 | Native identity no longer alive; expired status had no backend. |
| 3 | 4944 | 134378563344087346 | Native identity no longer alive; expired status had no backend. |
| 4 | 28544 | 134378611313208223 | Exited during final cleanup. |

The raw event gaps after previous turn completion were **4,782.156**,
**4,783.422** and **4,783.234 seconds**. Each exceeded 900 seconds; each
recorded expired state retained the original proxy and showed `backendPid: null`.
Each following phase used a different live backend PID/FILETIME identity.
The 900-second configured deadline and eventual expiry are verified; the exact
timeout latency was not measured by a continuous retained timeline.

All five captured MCP catalog inventories were independently rehashed from
their original responses and matched. All **13** frozen source/configuration/
backend/plan/executable hashes were independently re-read and still matched
the pre-run criteria. Source paths were replaced by safe labels in the public
[lifecycle evidence](evidence/native-codex-4h/lifecycle.json).

## CPU and working-set observations

These are selected phase snapshots, not peaks. CPU values are cumulative for
the named process identity. Each backend row refers to a new generation, so its
CPU counter resets.

| Phase | Driver WS MiB | App-server WS MiB | Proxy WS MiB | Backend WS MiB | Proxy CPU s | Current backend CPU s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 21.148 | 140.961 | 19.066 | 54.539 | 0.171875 | 0.234375 |
| 2 | 17.070 | 132.582 | 7.438 | 56.527 | 8.609375 | 0.218750 |
| 3 | 17.719 | 134.492 | 7.074 | 57.332 | 17.000000 | 0.281250 |
| 4 | 20.121 | 136.141 | 7.059 | 68.062 | 25.343750 | 0.187500 |

At the last phase sample, the driver's cumulative CPU was 5.281250 seconds and
the app-server's was 4.546875 seconds. Late idle proxy working-set samples were
4,612,096, 4,468,736 and 4,493,312 bytes, with no backend present.
Exact byte values and separate role counters are in
[metrics.json](evidence/native-codex-4h/metrics.json).

Working set is not private bytes or total committed memory. These samples do
not cover the entire machine or every possible client descendant. The run has
no controlled unsupervised baseline, so these numbers do not establish net
memory/CPU savings, subscription savings or invoice savings.

The final model-reported cumulative thread usage was 335,031 input tokens
(including 290,688 cached input tokens) and 1,772 output tokens: 336,803 total.
No subscription-credit or price conversion is inferred.

## Cleanup and retained diagnostics

App-server shutdown returned **0**, without forced termination, and Warden
reported **zero remaining sessions**. The post-run audit separately queried
the recorded PID/FILETIME identities of the driver, app-server, proxy and all
four backends; none of those original identities remained alive.

Two changed-condition smoke diagnostics remain counted as failures and are
not included in the long-run pass:

| Run | Model-backed turns | MCP attempts / successful calls | Outcome |
| --- | ---: | ---: | --- |
| Diagnostic 1 | 1 | 2 / 1 | Fixture `auto` approval required interaction for `write_file`; no baseline output was produced. The harness's final backend-phase error obscured that authorization-configuration failure. Cleanup passed. |
| Diagnostic 2 | 1 | 3 / 3 | The file task passed, but the observer incorrectly treated an unidentified persistent direct child as the backend. Its idle-liveness assertion failed. This was an observer failure, not evidence that Warden's published backend failed to expire. Cleanup passed. |
| Final smoke | 2 | 7 / 7 | Scoped write/edit approval and published-backend observation passed, using a 10-second idle deadline and 15-second post-result gap. Cleanup passed. |

Those runs used changed harness conditions and are not an identical-trial
failure-rate estimate. The corrected long run submitted four additional
model-backed turns, with no retry. Diagnostic result hashes and counts are
preserved in [diagnostics.json](evidence/native-codex-4h/diagnostics.json).

## Published evidence and limits

The six JSON artifacts were built using an explicit field allowlist. They
contain relative test-file names, hashes, UTC timing, numeric measurements and
permitted process identities. They omit raw account/configuration/log payloads,
absolute local paths, local usernames, raw conversation/turn/item identifiers,
synthetic batch identifiers, credentials and tool-result text.
[sanitization.json](evidence/native-codex-4h/sanitization.json) records the
publication checks and hashes of the other JSON files.

This establishes the recorded four-hour workflow for one installed native
Codex client and one filesystem backend with synthetic files. It does not
establish desktop GUI behavior, universal backend compatibility, general model
quality, continuous production reliability or economic savings.
