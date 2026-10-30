# Related projects and scope

Documentation reviewed on 2026-10-30. These are documented capabilities, not a
head-to-head performance or security evaluation.

| Project | Documented focus | Relationship to MCP Warden |
| --- | --- | --- |
| [Supergateway](https://github.com/supercorp-ai/supergateway) | MCP transport conversion, including stateful HTTP/SSE and session counters. | Runtime dependency. Warden adds local ownership and lifecycle policy around its transport. |
| [mcp-snooze](https://github.com/askmegit/mcp-snooze) | Lazy-start stdio proxy, idle reap, discovery cache and reversible configuration wrapping for Claude Code and Codex. | Direct overlap in idle process reduction. Our [controlled Windows comparison](benchmark-lightweight-2026-10-30.md) records different winners for private memory, working set and latency. Its separate published example benchmark is synthetic and on macOS. |
| [MCP Dashboard](https://github.com/SarutobiSasuke8/mcp-dashboard) | Local RAM/CPU, context-schema estimates and usage observations, with configuration switches, profiles and restore points. | Overlap in management visibility. Warden's first scope is session lifecycle; a dashboard or cost analysis is not part of this alpha. |
| [1MCP](https://github.com/1mcp-app/agent) | Aggregation, presets, filtering, progressive tool discovery and recovery of owned stdio backends. | Broader aggregation. Its README explicitly distinguishes smaller initial schemas from reducing backend connections/processes. |
| [lazy-mcp-wrapper](https://github.com/sleticalboy/lazy-mcp-wrapper) | Lazy startup, cached discovery, a shared daemon and stateful-server isolation. | Another lifecycle approach to evaluate before adding equivalent functionality. |

Warden's hypothesis is that explicit Windows process identity, private Job
Objects, HTTP session leases and a portable client entry can make lifecycle
behavior easier to inspect and reproduce. This is a product hypothesis, not a
proven unique feature or general superiority claim.

The Windows comparison uses the same backend and client workload on one host:
idle and active process counts, memory, cold-start delay, long-running calls,
reverse callbacks, reset behavior and remaining owned identities. Its criteria
were fixed before execution. Only direct stdio and mcp-snooze were measured
against Warden's two modes; the other projects above were not benchmarked.
Subscription quota savings cannot be inferred from RAM reduction.

Contributing a focused improvement upstream may be preferable to maintaining a
duplicate. Warden retains upstream attribution and does not claim to be an MCP,
Codex or Claude standard.
