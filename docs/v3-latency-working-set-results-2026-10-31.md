# Warden v0.3 latency and working-set results, 2026-10-31

In this controlled comparison, current Warden stdio improved cold startup,
warm calls, wake latency and idle memory against its immutable v0.2 baseline.
mcp-snooze still had lower latency. Warden's optional page release produced a
smaller idle working set than mcp-snooze, with additional page faults during
wake. The default Warden working set remained larger than mcp-snooze's.

## Verified run and inputs

The [version-4 Windows comparison](https://github.com/Borregro4/mcp-warden/actions/runs/41078627365)
passed **18 of 18 repetitions** at commit
`a2bb764c9a9299bd392ad7502d553ad9676bc5af`.
The [ordinary Windows CI run](https://github.com/Borregro4/mcp-warden/actions/runs/41078627321)
passed on that same commit: **49 Node test entries and 70 Python tests**, as
verified in its completed job log.

The environment was Windows Server 2025, Node.js 22.23.4 and CPython 3.12.10.
Measurement ran from **2026-10-30 15:13:34.863 to 15:16:15.939 UTC**, or
**2026-10-31 00:13:34.863 to 00:16:15.939 JST**. The report date uses JST.

The [predeclared plan](evidence/benchmark-v4/plan.json) fixed six variants,
three rotated repetitions each, one SDK 1.32.0 client, a single session, the
same current Python fixture with a touched 32 MiB allocation, ten warm calls,
a reverse callback, a three-second request and a two-second idle deadline.
Each repetition had a fresh directory and cold cache. Three rotations do not
balance all six order positions.

The old Warden variant used stdio source at immutable commit
`5c95856307e6610c9a1cf2b19a2f6d5cd208deea` (`v0.2.0-alpha.1`), while launching
the same current backend fixture as every other variant. Its four executed
Python files matched their Git blobs before the plan was written, and no npm
dependencies were installed for that baseline. The mcp-snooze variant used
the official Windows release `v0.1.0`; its verified binary SHA-256 was
`43e02bbc1148718c3305e74974d0a57ae032e3e9c366b755d53519ee580971db`.
See the [method](benchmark-method.md) for the upstream ZIP checksum and policy.

Current stdio default and page-release variants used the same product core.
Only the release variant explicitly set `release_idle_pages: true`; the
default remains `false`. All Warden stdio profiles explicitly declared
`restart_policy: stateless`, rather than silently applying restart behavior to
stateful tools.

## Observed medians

| Variant | Cold ms | Warm ms | Wake ms | Active private MiB | Idle private MiB | Idle working set MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Direct stdio | 67.05 | 0.220 | 0.58 | 42.67 | 42.67 | 52.81 |
| mcp-snooze v0.1.0 | 112.25 | 0.452 | 59.03 | 88.16 | 47.10 | 15.23 |
| Warden v0.2 stdio | 201.38 | 0.549 | 82.62 | 58.43 | 16.01 | 30.99 |
| Current Warden gateway | 1126.75 | 4.816 | 680.29 | 182.52 | 126.66 | 196.31 |
| Current Warden stdio, default | 165.97 | 0.518 | 66.79 | 56.42 | 13.79 | 26.57 |
| Current Warden stdio, page release | 164.06 | 0.497 | 66.99 | 56.54 | 13.77 | 10.94 |

MiB means 1,048,576 bytes. Private bytes and resident working-set bytes are
different Windows metrics; working-set sums can count shared pages more than
once. Resources include the selected variant-owned processes and descendants,
including supervisor/connector costs and observed Windows helpers. Idle process
counts were two in every repetition except the gateway variant, which had
eight. Warm latency is the upper-middle of 30 individual samples; the other
medians use three repetitions.

Against v0.2 stdio, current default stdio reduced median cold latency by
**17.58%**, warm latency by **5.79%**, wake latency by **19.16%**, idle private
bytes by **13.84%** and idle working set by **14.26%**. These percentages use
the immutable baseline in this same run, rather than a different historical
runner.

Against mcp-snooze, current default stdio used **70.71% less idle private
memory**, but its idle working set was **74.50% larger**. It was **47.86%
slower cold**, **14.46% slower warm** and **13.14% slower on wake**. The changes
therefore narrow Warden's previous latency and resident-memory costs without
establishing a latency win over mcp-snooze.

With explicit page release, idle working set was **58.84% lower than current
Warden's default** and **28.17% lower than mcp-snooze**. Idle private bytes were
13.77 versus 13.79 MiB without release; this small difference does not support
a claim that page release frees committed allocations. Cold/warm differences
between the two current variants do not establish a page-release startup
benefit: release occurs later, after idle.

The release variant still trailed mcp-snooze by **46.15% cold**, **9.95% warm**
and **13.48% on wake**. No option in this dataset wins every metric.

## Paging cost and counter scope

These counters cover **only explicit roots alive at both idle and awake
samples with the same PID and creation identity**. They exclude new backends,
exited/replaced roots and every descendant. They are not total stack CPU or
pure tool-call CPU; the interval includes wake plus sampling delay. Page faults
include soft faults and are not equivalent to disk reads.

| Variant | Root wake CPU median, ms | Root page-fault increment median |
| --- | ---: | ---: |
| Direct stdio | 0 observed | 0 |
| mcp-snooze | 0 observed | 67 |
| Warden v0.2 stdio | 15.625 | 2803 |
| Current Warden gateway | 0 observed | 0 |
| Current Warden stdio, default | 0 observed | 13 |
| Current Warden stdio, page release | 0 observed | 669 |

Zero observed CPU deltas can reflect coarse accounting and do not prove zero
work. In particular, the gateway counter excludes its Node descendants and
the replaced connector; its zero is not evidence of a cheap gateway wake.
Excluded roots remain recorded in the raw results.

Page release raised the median root page-fault increment from **13 to 669**.
Its wake median was **66.99 versus 66.79 ms**, an observed difference of
**0.20 ms / 0.30%**. Three repetitions do not establish that this latency cost
will remain small on another machine or under memory pressure. The separate
[local experiment](idle-page-release.md) observed a larger wake penalty, so
that adverse evidence remains relevant. Page release is an explicit resident
memory versus paging tradeoff and stays disabled by default.

## Protocol, attribution and retained evidence

All 18 repetitions passed echo checks, reverse callbacks, a three-second
request surviving the shorter idle deadline, wake behavior and cleanup of
previously observed PID-plus-creation identities. The gateway variant returned
its expected expired-session error in all three repetitions; its failed first
request and reconnect are included in wake latency. No other variant required
that explicit reconnect in this workload. Warden's source and CI generation
tests cover revalidation of initial instructions/tool metadata and sending the
new request once. This echo benchmark alone does not provide a durable
side-effect ledger for proving exactly-once execution.

A retrospective audit recomputed all summary medians and root counter deltas
from the selected JSON. It checked **54 snapshots and 228 accepted process
rows**, found no invalid accepted parent-time edges or root-identity
disagreements, and found zero unreadable candidates or rejected roots. The
live sampler recorded zero rejected parent edges in this particular run.
This audit cannot repeat live ownership checks or independently reconstruct
the snapshot-start cutoff from retained rows.

All 14 current source hashes matched the measured commit with its Windows
CRLF checkout representation; all four baseline hashes and Git blobs matched
the immutable baseline. Both source-change lists were empty. The audit and
exact comparison percentages are retained in
[provenance-audit.json](evidence/benchmark-v4/provenance-audit.json).

The downloaded [workflow artifact](https://github.com/Borregro4/mcp-warden/actions/runs/41078627365/artifacts/12989156048)
matched GitHub's reported ZIP SHA-256:
`4d474b3230a72aa389629664bcf21b58f076d81dc4153f1f53ff495d3298832a`.
Only `plan.json`, `results.json` and `summary.json` were present. Those selected
synthetic reports are retained byte-for-byte here, without configuration,
tokens, backend logs, binaries or private agent data:

- [Plan](evidence/benchmark-v4/plan.json), SHA-256
  `8ec4811e3eed9c31ada89d34489f7aec39d3f3ccf0496d35e4c82bdeac9f2871`.
- [All repetitions](evidence/benchmark-v4/results.json), SHA-256
  `bec4dc77762e504d5dedfd4c982170f76c0f5ce91a14c7d5dd46b97137e41b2a`.
- [Raw summary](evidence/benchmark-v4/summary.json), SHA-256
  `a38e886fa02e683869af9dea97e4d59150a6bc4efd723de96314bf75dc8f9065`.

The workflow artifact has seven-day retention. The selected JSON files remain
in the repository so the evidence survives artifact expiration.

This is a small synthetic test on one shared cloud runner. It does not establish
performance for every MCP backend, multi-client amortization, behavior under
system memory pressure, multi-hour stability, native agent compatibility or
provider/token savings. Native Codex/Claude long-session tests are separate
evidence and are not represented as completed by this report.
