# Persistent native Codex workload

`scripts/native_codex_persistent.py` drives one real Codex app-server process,
one ephemeral thread and one Warden stdio proxy across a bounded file workload.
The existing signed-in account supplies authentication. It does not read or
copy authentication files, install a client, edit global settings or operate
the desktop GUI. Model execution is opt-in and consumes that account's quota.

This document describes the driver and acceptance criteria. A plan or successful
transport preflight is not a completed model endurance result. The dated short
echo check remains in [the previous report](native-codex-validation-2026-10-30.md).

## Why app-server

Official [OpenAI Docs](https://learn.chatgpt.com/docs/app-server) describe the
stdio JSONL handshake, persistent threads, multiple `turn/start` calls and
authoritative `item/completed`/`turn/completed` events. The installed executable
was checked on 2026-10-30 and reported `codex-cli 0.166.0-alpha.5.1`.
The driver uses that executable's generated schema during development.

The official [TypeScript SDK](https://github.com/openai/codex/blob/rust-v0.166.0-alpha.5.1/sdk/typescript/src/exec.ts)
starts `codex exec` for each run and uses `exec resume` for later turns. It
preserves conversation history but does not preserve the same live MCP client
between turns. App-server matches this test's persistent-connection requirement.
Python's standard library is sufficient for its documented JSONL protocol, so
this driver adds no runtime dependency. Codex's source license is
[Apache-2.0](https://github.com/openai/codex/blob/rust-v0.166.0-alpha.5.1/LICENSE);
the driver calls the public protocol and does not vendor Codex code.

Unlike `codex exec`, this app-server version has no `--ignore-user-config` flag.
Empty table overrides merge with existing tables and do not erase them. The
driver follows the official
[temporary-thread isolation pattern](https://github.com/openai/codex/blob/rust-v0.166.0-alpha.5.1/codex-rs/tui/src/temporary_structured_request.rs):
read effective configuration in memory, retain only MCP server names, disable
those servers explicitly and disable unrelated extensions, built-ins, skills,
hooks, apps and plugins for the test thread. It then enables only the test
connector and its explicit tool allowlist. `config/read` response bodies are
excluded from raw logs, and unrelated configuration values are not persisted.
The connected inventory must contain only the test server before inference.
The [MCP configuration reference](https://developers.openai.com/codex/mcp)
documents `enabled_tools`, `required` and scoped tool approval configuration.

## Fixed plan and evidence

The private JSON plan is fixed before inference and contains:

```json
{
  "version": 1,
  "workspace": "C:/absolute/private/workspace",
  "warden_state": "C:/absolute/private/state",
  "profile": "filesystem",
  "server_name": "warden-work",
  "allowed_tools": ["read_text_file", "read_multiple_files", "write_file"],
  "duration_seconds": 14400,
  "turn_offsets_seconds": [0, 4800, 9600, 14400],
  "minimum_idle_seconds": 900,
  "turn_timeout_seconds": 240,
  "max_mcp_calls_per_turn": 16,
  "turns": [
    {
      "id": "case1",
      "prompt": "Fixed synthetic filesystem task, using the configured MCP only.",
      "required_tool_groups": [["read_text_file", "read_multiple_files"], ["write_file"]],
      "verify": [{"path": "results/case1.json", "json_equals": {"fixed": "expected result"}}]
    }
  ]
}
```

The excerpt shows one case; the four-hour plan must contain four fixed cases
with corresponding offsets. Each required tool group is an OR; all groups
must be satisfied. `required_tools` additionally supports exact required names.
File checks support structural `json_equals`, byte-decoded `text_equals`,
`sha256` and `contains`. Relative paths must remain within the workspace,
including after resolution. Seed files can have unchanged SHA-256 checks.
The connector is derived from the supplied Warden state and profile, which must
explicitly select `stdio`, `stateless` restart and a positive idle deadline.

`criteria.json` records the plan, runtime, backend fingerprint-file, sampler,
helper, driver and executable hashes before any turn. Changed frozen files
abort later inference. The driver makes at most four model turns, never retries
a model automatically, uses `gpt-6.1-sol` with low effort by default, enforces a
per-turn tool-call budget and timeout, and writes raw evidence outside the repo.
An interrupted/failed call or uncertain delivery remains a failure.

Acceptance checks require real successful MCP tool-call events, only permitted
tools, expected file contents, unchanged catalog metadata, the same proxy
PID/creation identity, an observed idle backend exit and a different backend
PID/creation identity after wake. Native read-only handles obtain creation
times and distinguish exited/reused identities from unknown liveness. Driver,
app-server, proxy and direct-backend CPU/working-set observations are separate;
they do not represent the entire machine or prove savings.

Scheduled times are minimum offsets from the first turn. Each later turn also
waits at least `minimum_idle_seconds` after the previous result. No model
requests occur during inactivity. Four-hour acceptance requires the fourth
turn to start at or after 14,400 seconds and all three idle/wake transitions.
The small smoke plan uses two turns, a two-second backend idle deadline and
at least five seconds idle after the first result. Final cleanup requires a
normal app-server exit and zero remaining Warden sessions. Only this driver's
own subprocess handle is eligible for forced termination, which fails cleanup.

## Deliberate execution

```powershell
python scripts/native_codex_persistent.py --plan C:/private/plan.json --out C:/private/new-result
python scripts/native_codex_persistent.py --preflight --codex C:/installed/codex.exe --plan C:/private/plan.json --out C:/private/new-preflight
python scripts/native_codex_persistent.py --preflight --probe-tools --codex C:/installed/codex.exe --plan C:/private/plan.json --out C:/private/new-transport-probe
python scripts/native_codex_persistent.py --execute --codex C:/installed/codex.exe --plan C:/private/plan.json --out C:/private/new-execution
```

Preparation makes no model request. Preflight launches and validates the real
isolated MCP transport, then exits with no model turn. Optional `--probe-tools`
uses `mcpServer/tool/call` to read the actual orders input, write a fresh
`results/probe.json`, read it back and verify its file contents without a model.
It refuses an existing probe output and never writes workload result files.
`--execute` is a separate
explicit choice. Ordinary unit tests use a local fake JSONL peer and synthetic
files; they never invoke a model or read saved login. Neither these checks nor
one filesystem backend establish universal backend compatibility, GUI behavior,
general model quality, subscription savings or invoice savings.
