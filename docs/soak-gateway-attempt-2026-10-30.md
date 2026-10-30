# Gateway endurance attempt, 2026-10-30

This is a **failed/incomplete attempt**, retained as evidence. It is not a
one-hour validation and does not validate the later lightweight STDIO mode.

Source: [Windows workflow run 41053728227](https://github.com/Borregro4/mcp-warden/actions/runs/41053728227),
commit `3d1dd3c9726930835362d7c9ec4355ba6dd06e54`, based on the original gateway
runtime from `e67fb884`. Artifact ID: `12977050186`.

The downloaded ZIP matched its GitHub artifact digest:
`45d174ac3c461b1d3a3c0e786270791b1b9d5217f3c944d775372b294ffd3023`.
The plan file matched the hash stored in the result.

| Observation | Result |
| --- | --- |
| Planned duration | 3,600 seconds |
| Observed total duration | 902.675 seconds |
| Actual idle expiry observation | 900.089 seconds after the idle tool call |
| Fresh connection after expiry | Passed |
| Final cleanup reported by the original harness | Zero remaining identities in its current tree; orphan verification was incomplete |
| Resource samples | 31 |
| Peak sampled full test-tree working set | 292,483,072 bytes (278.93 MiB) |
| Last sampled working set | 142,262,272 bytes (135.67 MiB) |
| Maximum sampled CPU, normalized by 4 logical CPUs | 0.365% |
| Prior working-set objective | 256 MiB; exceeded |

The loop stopped during serial owner churn because the test harness closed a
fresh direct HTTP SDK client without sending MCP session DELETE. SDK transport
close alone aborts that client's streams; it does not terminate the gateway
session. That session occupied the second slot, so the next initialization
correctly received HTTP 429, `Session capacity reached`.

The harness was corrected to explicitly terminate its direct HTTP session and
wait for cleanup before starting another. This attempt was not relabeled or
silently rerun as a success. Its hour-long duration, full churn and overall
acceptance criteria remain unmet. The measured startup working set also exceeded
the original objective, independently of the harness error.

Working-set sums can double-count shared pages, and these are discrete samples,
not continuous peak/CPU measurements. No comparative savings or native-agent
compatibility can be inferred from this run.

Later review found that the original sampler used parent PIDs without checking
creation times at each edge, and cleanup inspected only the current tree. That
could misattribute an older process after parent-PID reuse or miss an orphan
whose parent already exited. A separate STDIO attempt demonstrated that
misattribution, so this run's resource and cleanup figures remain qualified.
No older-than-root rows were found in these 31 gateway samples, but that alone
does not retrospectively validate every parent edge or prove orphan absence.
The sampler was corrected and regression-tested before the next endurance run.
