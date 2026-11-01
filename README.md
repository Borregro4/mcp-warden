# MCP Warden

[Español](README.es.md)

**Reclaim memory from idle MCP servers on Windows while protecting in-flight tool calls.**

MCP Warden stops owned local MCP backends after clean inactivity. An explicitly
stateless profile can start a fresh backend for the next call. This is a
**Windows alpha**, with bounded Codex CLI and Claude Code checks; review the
[state-loss and compatibility limits](VALIDATION.md#limits) before using a real server.

**[Try the first-run guide →](docs/first-run.md)** ·
[Guía en español](docs/first-run.es.md) ·
[Download v0.3.0-alpha.1](https://github.com/Borregro4/mcp-warden/releases/tag/v0.3.0-alpha.1)

## Start with one test server

The guide walks from download or clone to a real tool result:

1. Check Windows, Python 3.12+, and your installed, signed-in Codex CLI or Claude Code.
2. Create the included synthetic echo profile. Lightweight stdio needs no npm install, Node process, HTTP port, or foreground gateway.
3. Back up your client config and add only the generated Warden entry.
4. Call echo with a new value, inspect its returned PID, and optionally observe idle stop and wake.
5. Remove just that entry to undo the setup.

No installer, service, or automatic client-config changes. The optional
[HTTP gateway and real-backend reference](docs/first-run.md#reference) remain available.

## See the lifecycle

![Synthetic MCP backend: active, idle stop, and wake](assets/demo.gif)

The demo uses a synthetic backend. It illustrates lifecycle behavior; it is
not a claim about savings for your own servers.
[Demo method and scope](docs/demo.md).

## What has been checked

- **Codex CLI 0.166.0-alpha.5.1:** four turns across four hours, 14 real filesystem MCP calls, verified file effects, three idle/wake cycles, and final cleanup.
- **Claude Code 2.1.309 through official Agent SDK 0.2.175:** four turns across four hours, 21 real filesystem MCP calls, verified file effects, three idle/wake cycles, and final cleanup.
- Both kept one native session and proxy while a 900-second idle policy stopped and replaced the backend. These long runs explicitly enabled optional idle page release.

See [VALIDATION.md](VALIDATION.md) for the results, source hashes, failed
smoke diagnostics and test limits. The data was synthetic; the clients, MCP
operations and file changes were real. The demo above remains a recorded v0.2
SDK example.

The [six-way comparison](docs/v3-latency-working-set-results-2026-10-31.md)
measured lower startup, wake latency and idle memory against v0.2. mcp-snooze
still had lower latency. Optional [idle page release](docs/idle-page-release.md)
reduced working set further with extra page faults; it stays off by default.
[Earlier adverse gateway results](docs/benchmark-results-2026-10-30.md) and
[the v0.2 validation](docs/validation-v0.2.0-alpha.1.md) remain available.

## Know the limits

- MCP Tasks can outlive their initial JSON-RPC response. Sessions that advertise
  Tasks or use task-augmented requests retain their backend until disconnect;
  idle expiry is disabled conservatively. This is a safety fallback, not full
  task tracking or task recovery. See [task lifecycle](docs/task-lifecycle.md).
- Stateless restart loses backend memory, cursors, and selections. Use it only when that is acceptable. Default/manual profiles require a client reconnect after expiry; completed calls are never replayed.
- Windows Job cleanup is for owned private children. Shared descendants need a different policy, and the creation-to-Job-assignment gap is not qualified.
- This is a lifecycle supervisor for trusted local programs, with no hard RAM/CPU sandbox, model routing, token savings, or subscription-quota management.
- Status samples cover the proxy and direct backend, not every descendant or the client app. State can contain sensitive configuration and stderr; disk history is not automatically pruned.
- These are bounded CLI/SDK checks, not universal compatibility, multi-day reliability, or desktop GUI validation.

## Help shape the first release

Use Windows with Codex CLI or Claude Code and already have MCP servers?
[Try the pilot and share structured feedback](docs/pilot.md). A successful
first run, a reproducible failure, or a server that must retain state is useful
evidence. Review diagnostics for private data before sharing.

## Development and evidence

```powershell
npm ci --ignore-scripts
npm test
```

Tests use synthetic local backends and temporary state. The
[Windows CI workflow](.github/workflows/test.yml) is separate from its run
results, linked in [VALIDATION.md](VALIDATION.md).

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md),
[related projects](docs/alternatives.md), the [benchmark method](docs/benchmark-method.md),
and the [endurance plan](docs/soak-stdio-method.md) and
[completed result](docs/soak-stdio-results-2026-10-30.md).

## License and attribution

MCP Warden is MIT licensed. Supergateway and the pinned MCP packages retain their own licenses and notices; see [NOTICE](NOTICE) and [licenses/](licenses/). Independent project; no affiliation with OpenAI, Anthropic, or the MCP maintainers.
