# Reproducible Windows resource comparison (version 4)

This comparison uses synthetic local data and an official SDK protocol client.
It makes no model calls, reads no agent conversations, and measures no provider
quota or token cost. Run it separately from a long soak or other benchmarks.

## Variants and provenance

1. Direct stdio backend, with no lifecycle wrapper.
2. [mcp-snooze v0.1.0](https://github.com/askmegit/mcp-snooze/releases/tag/v0.1.0),
   using the upstream Windows amd64 release binary.
3. MCP Warden `v0.2.0-alpha.1` stdio at immutable commit
   `5c95856307e6610c9a1cf2b19a2f6d5cd208deea`, with its explicit stateless policy.
   Its source is archived into a separate directory; no npm dependencies are
   installed there. Its four executed Python source files must match that Git
   commit's exact blobs before the plan is written.
4. Current MCP Warden gateway mode, including its foreground supervisor and Node stdio
   connector.
5. Current MCP Warden lightweight stdio mode, using one Python adapter, an explicit
   stateless restart policy and its default page-release policy (`false`).
   No gateway, Node connector or HTTP listener starts.
6. Current MCP Warden lightweight stdio with the same profile and explicit
   `release_idle_pages: true`. This only releases the proxy's own resident
   working-set pages after clean idle; it does not free committed allocations.
   See [the paging tradeoff](idle-page-release.md).

The mcp-snooze Windows release ZIP has GitHub-reported SHA-256
`1694342e4d7f4cf64cfdd17e65edc10280a7727be438bd9328ff8824b0442880`.
The archive downloaded during preparation matched that digest. Its
[release license](https://github.com/askmegit/mcp-snooze/blob/v0.1.0/LICENSE)
is MIT, copyright 2026 askmegit. The binary is not vendored in this repository.
Its version and binary hash are recorded in every benchmark plan.

The [upstream Windows implementation](https://github.com/askmegit/mcp-snooze/blob/v0.1.0/internal/proxy/proxy_windows.go)
uses `taskkill /T` on its own child PID. Its current README describes Windows
black-box lifecycle testing as unqualified. That is an upstream qualification
limit, not evidence that the binary fails this workload.

## Fixed workload

Each variant receives the same `scripts/benchmark_backend.py`, Python runtime,
SDK 1.32.0 client, and one session. The backend touches a 32 MiB allocation in
addition to its normal Python memory. The immutable Warden source launches the
**current same fixture** using absolute paths, rather than its historical
fixture. Three repetitions rotate variant order; all orders are fixed in the
plan before measured processes start. Three rotations do not balance all six
positions or establish statistical significance.
Every repetition uses a new working directory and a cold cache.

The recorded stages are:

- Cold startup: process stack startup, client initialization, tool discovery and
  first echo. Offline generation of Warden configuration is excluded.
- Ten warm serial echo calls, with individual round-trip latency.
- A reverse `roots/list` callback.
- A three-second tool call under a two-second idle deadline.
- Active process resources, followed by three seconds with no client requests
  and a second resource sample.
- Wake echo. If Warden explicitly reports an expired session, that failed call
  and one explicit client reconnect are included in its wake latency.
- Idle-to-awake cumulative CPU and page-fault deltas for explicit roots present
  at both sample boundaries with the same PID and creation identity. New
  backends, exited/replaced roots and all descendants are excluded from these
  delta totals. Their managed memory remains included in the stage samples.
- Shutdown and a PID-plus-creation-time check for previously observed processes.

The gateway variant explicitly expires and requires reconnection. The light
stdio variant must resume on the same connection after revalidating initial
instructions and previously discovered tool metadata; a changed catalog fails
closed. Its stateless profile declaration is part of this fixture, not a safe
default for arbitrary stateful tools. mcp-snooze restarts its own backend under
its documented policy. A low-memory result does not erase a wake error or state
reset; the raw results preserve both.

## Measurements and limits

`benchmark_processes.py` reads the Windows process table and queries handles for
the explicitly spawned supervisor/proxy/connector roots and their descendants.
It does not inspect process command lines or environments and never kills a
process. The client harness itself is excluded. Warden's shared supervisor is
included in the gateway variant, so this single-session case does not demonstrate multi-session
amortization.

The version-4 comparison retains the version-3 ownership selector using
`soak_metrics.select_tree` and `WindowsReader`.
Every selected parent/child edge requires a child creation time at least as new
as its parent's, and no child created after the snapshot began is adopted.
Previously observed root creation identities are checked on later samples;
an explicitly newly spawned SDK child may establish a new identity if Windows
reused a PID. Merely matching a historical parent PID is insufficient. This
avoids attributing unrelated processes to a newly reused PID. The same known
identities are supplied for the final cleanup check, including former orphans.

Private bytes and working-set bytes are reported separately. Working-set sums
can double-count shared pages. CPU seconds are cumulative snapshots of the
processes alive at that stage, not CPU utilization or total work including
already exited processes. Unreadable processes make a complete-resource claim
invalid for that repetition.

The benchmark adapter also reads `PageFaultCount` from the same native memory
counter structure and identity-checked handle. This is total page faults, not
hard page faults or disk reads. `wakeStableRoots` records per-root CPU seconds
and page-fault increments between idle and awake samples, with excluded or
invalid identities retained explicitly. No matched root produces `null`, not
zero. The interval includes wake and measurement delay; it is not pure RPC CPU
or total stack work. Coarse observed CPU accounting may produce zero deltas.

The benchmark stops a variant below 3 GiB available system RAM or above 768 MiB
of observed managed private memory. Those are fixture guards, not product
resource guarantees. Other activity on the machine can affect latency and
memory. A short synthetic comparison cannot establish general superiority,
multi-hour stability, native client compatibility, or subscription savings.

## Run

Download and verify the official upstream binary into a private test directory.
Do not run `wrap` or edit live agent configurations for this comparison.
Fetch the immutable Warden commit and archive it into a separate absolute
directory. Disable `core.autocrlf` for the archive command itself, without
changing Git configuration, so the extracted files retain their exact blobs.
The supplied baseline directory is rejected if any executed Python file differs
from that commit. Its Git objects must also be available in this checkout for
the verification.

```powershell
node scripts/benchmark.mjs --snooze C:\absolute\test\mcp-snooze.exe --baseline C:\absolute\warden-v02-archive --out C:\absolute\new-results --runs 3
```

The output directory must be new: `plan.json` is created exclusively before
starting any measured variant. It fixes inputs, criteria, source hashes,
versions, variant orders, current and baseline source hashes, baseline Git
blobs, immutable commit and binary hash. `results.json` keeps all attempts and errors;
`summary.json` reports medians alongside planned and passing counts. Changes
to either source tree during a run are detected and invalidate a single-version claim.

The Windows workflow performs that public HTTPS fetch with depth 1 and no
persisted checkout credentials, verifies the exact commit, and uses `git archive`
under `RUNNER_TEMP`. It retains the upstream ZIP digest check and uploads only
the selected synthetic JSON reports after checking them against generated
runtime tokens. Source archives, binaries, configuration, tokens and stderr
files are excluded from the uploaded artifact.

No benchmark result is asserted by this methodology file. Publish a result only
after inspecting its artifacts and explaining the actual errors and tradeoffs.
