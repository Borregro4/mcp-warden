# STDIO endurance method

This opt-in synthetic test targets the lightweight STDIO mode. It uses the
official SDK as a client of the real Python CLI. No HTTP gateway, AI model,
native agent application or production MCP is used in the workload.

The primary run is **60 real minutes**, with a **900-second idle deadline** and
30-second observation intervals. A separate one-minute smoke run may use an
accelerated idle deadline; it does not count toward the hour-long result.

The harness writes `plan.json` before preflight and workload startup. It records
criteria, duration, resource budgets, commit, and hashes of the runtime, example
backend, dependency lockfile and harness. The result includes the plan hash.

## Workload and prior criteria

1. Two independent SDK stdio connections invoke the Python CLI, configured with
   explicit `restart_policy: stateless`. Each observes `tools/list` before its
   first tool call and starts its own example backend. Tool count and catalog
   hash are recorded, without uploading complete metadata.
2. The active connection echoes a sequence value every 30 seconds and completes
   a reverse roots callback every five ticks. The returned value and active
   backend PID must remain correct. It must still echo after 3,600 monotonic
   seconds.
3. The other connection sends one echo and remains idle. After 900 seconds,
   within 60 seconds of observation grace, its backend must be gone while its
   Python proxy remains alive. Sampled status is corroborated with the actual
   Windows process identity, so initial stale status is not treated as expiry.
4. The **same** SDK connection must wake a fresh backend under the explicit
   stateless policy. Backend identity uses PID and Windows creation time,
   allowing for eventual numeric PID reuse. The proxy remains the same.
5. After the idle connection closes, three new owner connections start and close
   serially. Their proxy and backend identities must disappear. At most two
   proxies are present concurrently.
6. Final shutdown must reach zero live proxies in CLI status and no previously
   observed test-owned child identity when queried directly by PID and creation
   time. This query also checks orphans whose parent is no longer in the tree;
   unavailable liveness is unknown, never assumed to be an exit.

## Resource budgets and evidence

The resource objective is a sampled test-tree working set of at most 256 MiB.
The hosted Windows run aborts above 768 MiB or below 3 GiB of available physical
memory. These are budgets for this test, not universal MCP limits. Missing the
256 MiB objective remains a failed resource criterion even if stability checks
complete.

The Windows sampler includes the Node SDK harness, Python proxies and backend
fixtures. Every parent edge is checked against process creation time to reject
stale PPIDs after PID reuse. It excludes the short-lived sampler itself. Summed working sets can
double-count shared pages. CPU is computed from cumulative process times over
observation intervals and normalized by logical CPU count; short-lived probes
that exit between samples are not fully represented. Thirty-second snapshots
are not continuous peak measurements or proof of an absence of slow leaks.

Artifacts are restricted to plan, preflight, observations and result JSON.
State/configuration, tokens, manifests, catalog caches and backend stderr are
excluded. No native-user session data or credentials are uploaded.

A passing run establishes the recorded synthetic workload on its exact source
hashes and host. Native Codex/Claude tests are separate. Do not mix results from
the earlier gateway version, accelerated tests or different hosts into a claim
of one-hour stability or superiority for the new STDIO code.
