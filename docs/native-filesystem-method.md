# Persistent native filesystem acceptance

These opt-in checks use the installed native Codex and Claude clients against
the official filesystem MCP server. They exercise real reads, searches, edits
and durable file outputs on synthetic business data. They do not operate on
production files, constitute a coding-quality comparison, or establish results
merely by preparing a four-hour plan.

## Backend and isolation

The selected optional backend is
[`@modelcontextprotocol/server-filesystem` 2.1.0](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem).
Its published requirement is Node.js 24 or newer. It supports both modern
stdio discovery and legacy initialization. Its CLI allow-list is restricted to
the individual test workspace; native client roots use that same directory.
The project's [license transition](https://github.com/modelcontextprotocol/servers/blob/main/LICENSE)
retains applicable MIT contributions and licenses new code under Apache-2.0.
The package is not bundled or required by Warden's runtime.

Install it separately from the repository's dependencies, without install
scripts or global configuration changes:

```powershell
npm install --prefix ../private-native-backend --ignore-scripts --save-exact @modelcontextprotocol/server-filesystem@2.1.0
python scripts/prepare_native_workload.py --out ../private-client-case `
  --filesystem-entry ../private-native-backend/node_modules/@modelcontextprotocol/server-filesystem/dist/index.js `
  --release-idle-pages
```

The generator creates a new private directory, seed files, expected effects,
and a `filesystem` Warden profile with explicit stateless restart. Output and
plan directories must remain outside the repo. The optional page-release flag
is explicit; omit it for the normal memory policy. Each client gets its own
directory and batch ID. Gold expectations stay outside the MCP allowed root.

Codex uses one isolated app-server process and ephemeral thread. Other MCP
servers and unrelated features are disabled for that process, then checked
before inference. Only `write_file` and `edit_file` of the test server receive
explicit approval; ordinary annotations remain respected for other tools.
No effective global configuration or authentication payload is copied to logs.

Claude uses one official `ClaudeSDKClient`, the existing installed native CLI,
strict MCP configuration, no user setting sources and a tool allow-list/hook.
The SDK is an optional isolated test dependency, not a Warden runtime dependency.
Neither client test changes saved global settings or copies credentials.

## Prior cases and conditions

Both clients receive the same case definitions, with independent file copies:

1. Read orders and shipping policy, exclude cancelled orders, calculate integer
   cent totals and write a baseline JSON report.
2. Selectively change the shipping boundary from strictly greater-than to
   greater-than-or-equal, then write boundary-check results.
3. Find late adjustments by filename search, reconcile them once and retain
   the original orders and earlier baseline.
4. Re-read the earlier artifacts and policy, then write a final audit summary.

File effects are checked independently against predeclared JSON values and
seed SHA-256 hashes. Successful native MCP events, correct server/tool names,
reply status and required tool groups are also checked. A model's completion
statement is insufficient. The tools operate on actual files, while the data
and business case remain synthetic.

The long plan has four turns, scheduled at offsets **0, 4,800, 9,600 and
14,400 seconds** from the first actual turn start. The last turn must begin
after at least four real hours. There is at least a 900-second gap after each
response, and the backend idle policy is 900 seconds. The same native session,
proxy identity and unchanged tool metadata must persist; backend replacement
is checked using PID and Windows creation time, including actual prior exit.

Each turn allows at most 16 MCP calls and has a 240-second deadline. There are
no automatic model retries. Sources, backend entry/package/lock and plans are
fingerprinted before execution and checked later. Native account quota and
provider errors are preserved as failures or blockers, not accepted workflows.

Short smoke checks are separate from the long result. Codex's final smoke
uses a ten-second idle policy and at least fifteen seconds after the first
response, so periodic status can positively identify the actual backend.
Backend observations use Warden's published PID and a native creation-time
query; an arbitrary child process is not a substitute. Earlier diagnostic
failures are retained separately.

## Evidence and completion

The client-specific drivers, CLI options and acceptance checks are described
in [Codex's method](native-codex-persistent.md) and
[Claude's method](native-claude-persistent-method.md). Raw account/client logs,
runtime configuration and tokens stay private. Public results must be
sanitized and distinguish model-backed tasks from SDK-only benchmarks.

A prepared plan, live process or successful first turn is not a completed
four-hour result. Confirm all four stages, actual elapsed time, the idle/wake
receipts, stable source hashes and final cleanup before publishing acceptance.
No outcome here measures billed savings or guarantees multi-day stability.
