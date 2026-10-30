# Reproducible Windows resource comparison

This comparison uses synthetic local data and an official SDK protocol client.
It makes no model calls, reads no agent conversations, and measures no provider
quota or token cost. Run it separately from a long soak or other benchmarks.

## Variants and provenance

1. Direct stdio backend, with no lifecycle wrapper.
2. [mcp-snooze v0.1.0](https://github.com/askmegit/mcp-snooze/releases/tag/v0.1.0),
   using the upstream Windows amd64 release binary.
3. MCP Warden gateway mode, including its foreground supervisor and Node stdio
   connector.
4. MCP Warden lightweight stdio mode, using one Python adapter and an explicit
   stateless restart policy. No gateway, Node connector or HTTP listener starts.

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
addition to its normal Python memory. Three repetitions rotate variant order.
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

Private bytes and working-set bytes are reported separately. Working-set sums
can double-count shared pages. CPU seconds are cumulative snapshots of the
processes alive at that stage, not CPU utilization or total work including
already exited processes. Unreadable processes make a complete-resource claim
invalid for that repetition.

The benchmark stops a variant below 3 GiB available system RAM or above 768 MiB
of observed managed private memory. Those are fixture guards, not product
resource guarantees. Other activity on the machine can affect latency and
memory. A short synthetic comparison cannot establish general superiority,
multi-hour stability, native client compatibility, or subscription savings.

## Run

Download and verify the official upstream binary into a private test directory.
Do not run `wrap` or edit live agent configurations for this comparison.

```powershell
node scripts/benchmark.mjs --snooze C:\absolute\test\mcp-snooze.exe --out C:\absolute\new-results --runs 3
```

The output directory must be new: `plan.json` is created exclusively before
starting any measured variant. It fixes inputs, criteria, source hashes,
versions and binary hash. `results.json` keeps all attempts and errors;
`summary.json` reports medians alongside planned and passing counts. Changes
to the source during a run are detected and invalidate a single-version claim.

No benchmark result is asserted by this methodology file. Publish a result only
after inspecting its artifacts and explaining the actual errors and tradeoffs.
