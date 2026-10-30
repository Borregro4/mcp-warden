# First run on Windows

[Español](first-run.es.md) · [Back to README](../README.md)

## 1. Download or clone v0.3.0-alpha.1

Choose one route. This walkthrough is pinned to the
[published Windows alpha](https://github.com/Borregro4/mcp-warden/releases/tag/v0.3.0-alpha.1).

**Without Git:** download **Source code (zip)** from that release, extract the
whole archive to a folder you own, and open PowerShell in the extracted folder
containing `warden.py`. To change folders manually, replace this example path:

```powershell
Set-Location 'C:\path\to\extracted\mcp-warden'
```

**With Git:** from a parent folder where `mcp-warden` does not already exist:

```powershell
git clone --branch v0.3.0-alpha.1 --depth 1 https://github.com/Borregro4/mcp-warden.git mcp-warden
Set-Location .\mcp-warden
```

Git may report an annotated tag and a detached HEAD. That is expected when
using this pinned release; no branch change is needed to run it.

Keep this folder in place while your client entry uses it. Warden prints
absolute paths into the entry.

## 2. Check the prerequisites

Use Windows and Python **3.12 or newer** on `PATH`, plus an installed,
signed-in **Codex CLI or Claude Code**. Use a normal user PowerShell terminal.
This stdio walkthrough needs no Node.js, npm install, administrator access,
API key for the example, or separate gateway.

```powershell
python --version
python -c "import sys; print(sys.executable)"
python warden.py --version
```

Check that Python reports 3.12+ and Warden reports `0.3.0-alpha.1`. Record
the executable path if you have multiple Python installations. For your chosen
client, check `codex --version` or `claude --version`; first confirm that you
can open that client normally using its existing login.

[The validation record](../VALIDATION.md) lists the exact tested client
versions. It does not promise compatibility with every newer build or the
desktop GUI.

## 3. Initialize only the included synthetic server

The included `example` backend echoes a value and its PID. It stores no
necessary state between calls and needs no credentials. **The `stateless`
option below is for this example.** Do not copy it to a real server until
losing its memory, cursors, and selections is acceptable.

```powershell
python warden.py init --state .warden-state --transport stdio --restart-policy stateless
python warden.py doctor --state .warden-state --transport stdio --json
```

The normal idle deadline is **900 seconds**. If you want a short optional
idle-stop test, add `--idle-seconds 15` to the `init` command **before running it**.
Use a new or empty state folder; `init` deliberately refuses to overwrite one.
Do not run both variants against the same initialized folder.

`doctor` should report `"ready": true` with all checks true. It checks the
local runtime and state, not your client login or an actual tool invocation.

Keep `.warden-state` under your account and out of Git. It contains local
configuration, a token, and runtime diagnostics, even in stdio mode.

## 4. Back up and merge one client entry

Choose **one** client below. `configuration` only prints text; it does not
write settings or start a backend. The client later launches Warden's
`connect` transport itself. Do not run `connect` as an interactive command:
its stdout is reserved for MCP.

### Codex CLI

```powershell
python warden.py configuration --state .warden-state --profile example --client codex
```

For a project-only test, use `.codex/config.toml` inside this checkout.
[Official OpenAI documentation](https://learn.chatgpt.com/docs/extend/mcp#configure-with-configtoml)
also supports the user config at `~/.codex/config.toml`; choose the file
appropriate to your client setup.

1. If your chosen file exists, copy it to a separate backup before editing.
2. Create its parent folder and the file if needed.
3. Add the **printed `[mcp_servers."warden-example"]` table** with its `command`
   and `args`. Preserve all other settings and server tables.
4. If `warden-example` already exists, stop the merge and choose an unused
   server name for this test; do not duplicate a TOML table or overwrite an
   existing server. Keep the generated command and args unchanged.

Open a new Codex CLI session from this checkout so it reads the entry.
Follow any project-trust or tool-approval prompt shown by the client.

```powershell
codex
```

### Claude Code

```powershell
python warden.py configuration --state .warden-state --profile example --client claude
```

For this project-only test, back up `.mcp.json` at the checkout root if it
exists. If absent, create it with the printed JSON. Otherwise merge only
the printed `warden-example` property into its existing `mcpServers` object;
preserve other servers and valid JSON commas. Choose an unused server name
if that key already exists.

Start Claude Code from this folder and review its project-server approval.
Its [official MCP documentation](https://code.claude.com/docs/en/mcp#mcp-installation-scopes)
describes `.mcp.json`; `/mcp` shows server status.

```powershell
claude
```

The generated paths are local to your machine. Keep the test config private
rather than committing it to share with others.

## 5. Make one actual echo call

Generate a fresh test value in PowerShell:

```powershell
$probe = 'warden-' + [guid]::NewGuid().ToString('N')
$probe
```

Paste the printed value into this prompt in your chosen client. If you renamed
the server entry, use that chosen name instead of `warden-example`:

> Use the `echo` tool from the `warden-example` MCP server with `value` set to
> `PASTE-YOUR-FRESH-VALUE`. Return the actual tool result, including `pid` and
> `value`. Do not substitute a shell command or a guessed result.

Review the **completed tool call and its result**, not just the model's
summary. Success means a positive integer `pid` and a `value` exactly equal
to your fresh string. Save both for your own test. The exact PID varies.

While that client session is still open, run this in a second PowerShell
terminal in the same checkout:

```powershell
python warden.py status --state .warden-state --transport stdio --json
```

It reports live validated proxy identities, `sessionCount`, and samples.
The sampler updates about every two seconds. Before the client connects or
after it exits, zero sessions can be normal. `"status": "ready"` alone is
not evidence of a successful MCP call. Memory samples cover the proxy and
direct backend only.

## 6. Optional: observe idle stop and wake

If you chose the 15-second idle test, keep the same client connection open
and stop sending tool calls. Allow the idle interval and a sampling interval.
Use the status command above: while the proxy remains connected,
`backendPid` can become `null` when the backend has stopped.

Generate a **different** fresh value and ask for echo again. Check its actual
returned value and positive PID, then inspect status again. This synthetic
stateless profile can create a fresh backend without replaying an old call.
Client traffic can postpone expiry; a client that disconnects or reconnects
does not demonstrate same-connection wake. Record what you actually observe.

If Warden requests a **client reconnect**, follow that boundary. For Claude
Code, select Reconnect for this server in `/mcp`. For Codex, start a fresh
test CLI session if no per-server reconnect control is available. Reconnect
only the affected test connection/session; there is no need to restart Windows
or change credentials.

## 7. Undo the test

Close the test client session or disconnect its Warden connection first.

- **Codex:** remove only the `[mcp_servers."warden-example"]` table you added,
  including its generated `command` and `args`. If you used a different
  server name, remove that exact table.
- **Claude:** remove only the `warden-example` property you added inside
  `mcpServers`, repairing its surrounding comma if necessary. Use your
  chosen name if you renamed it.

Keep other entries, settings, and credentials. Use the backup to compare;
do not restore the whole file over changes made since the backup. An empty
test config can stay in place. Reopen the test client if needed to read the
change. Confirm that Warden's stdio `status` shows zero live sessions after
the connection exits; retained diagnostics are not live processes.
You may keep the checkout and state for inspection.

## Common problems

| Symptom | Next check |
| --- | --- |
| `python` missing or wrong version | Make the installed Python 3.12+ executable available in this terminal; check `sys.executable` again. |
| `warden.py` not found | Change to the extracted/cloned folder containing that file. Extract the whole archive, including `runtime` and `examples`. |
| `init requires a new or empty state directory` | Reuse the existing initialized state, or choose a new empty folder and use its path in every subsequent command. Do not delete an existing state to silence the error. |
| `doctor` is not ready | Read the false check; verify Windows, Python, full source, and the same state path. Include `--transport stdio` so gateway dependencies are not required. |
| Tools do not appear | Confirm the entry was merged into the config the chosen client reads, paths still exist, and the project/server was approved. Start a fresh test connection/session. |
| Claude shows pending approval | Open Claude Code interactively in this project and review the server from `/mcp`. |
| `status` shows zero sessions | Keep the client session open after an actual echo call; confirm both terminals use the same state path and `--transport stdio`. |
| Idle stop does not happen immediately | Check the configured deadline and wait for the sampler. Initialization, active calls, callbacks, or other traffic can keep the backend busy. |
| Warden asks to reconnect | Reconnect the affected client connection. Do not resend a completed or uncertain old call automatically. |

Need help? Use the [pilot checklist](pilot.md). Share versions, transport,
the failing step, and a redacted error. Do not post your state folder, token,
credentials, or raw private backend stderr.

## Reference

The sections below preserve the advanced configuration and safety details.
Start with a dedicated test backend before adding one that matters to your work.

### Lightweight restart policy

Declare `restart_policy: "stateless"` only when losing backend memory, cursors
and selections is acceptable. The default policy is `manual`: an expired
connection reports that a client reconnect is required. For stateful backends,
you can disable idle expiry with `idle_session_timeout_seconds: 0`.

Stateless wake keeps the stdio connection open, reinitializes the backend,
validates its initialization instructions and previously observed tool metadata,
then sends the new request once. Changed schemas or approval annotations,
paginated discovery, missing discovery baseline, cancellation or uncertain
outcomes require a fresh client connection. Old tool calls are never replayed.

This mode bounds input frames to 1 MiB and queues to 16 entries. It does not
provide the gateway's global/per-profile session admission limits. Status
samples cover the proxy and direct backend; they do not include every descendant.

### Optional HTTP gateway

Use a separate state folder from the walkthrough above. The optional HTTP/SSE
mode adds lifecycle policy around [Supergateway](https://github.com/supercorp-ai/supergateway).

Requirements: Windows, Python 3.12, and Node.js 22 or newer on `PATH`. Run in a normal user terminal.

From a source checkout:

```powershell
npm ci --ignore-scripts
python warden.py init --state state/gateway
python warden.py doctor --state state/gateway
python warden.py serve --state state/gateway
```

Leave that terminal open. In a second terminal:

```powershell
python warden.py status --state state/gateway
python warden.py configuration --state state/gateway --profile example --client codex
# Or generate the JSON entry for Claude:
python warden.py configuration --state state/gateway --profile example --client claude
```

`init` creates a local state directory with a bearer token, gateway configuration, and an example backend. `configuration` prints client configuration snippets; review and add the relevant snippet to your client. It does not edit existing client settings. `serve` runs in the foreground; use Ctrl+C in that terminal to shut down its sessions.

The generated stdio entry invokes:

```powershell
python warden.py connect --state state/gateway --profile example
```

`connect` is an MCP transport, so its stdout is reserved for protocol messages. Configure a client to invoke it instead of running it interactively.

### Configure a real backend

Edit `config.json` in your state directory. Each named profile contains a backend manifest. Start with a dedicated test server and keep a copy of your existing client settings.

```json
{
  "profiles": {
    "my-server": {
      "transport": "stdio",
      "restart_policy": "manual",
      "idle_session_timeout_seconds": 900,
      "manifest": {
        "command": "C:\\absolute\\path\\to\\server.exe",
        "args": [],
        "env": {},
        "cleanup_scope": "tree"
      }
    }
  }
}
```

This is a profile fragment, not a complete configuration. Preserve the paths and other fields generated by `init`, then generate the entry for your profile. New stdio connections read the updated profile. If using `transport: "gateway"`, restart the foreground supervisor after changing its configuration.

| Setting | Meaning |
| --- | --- |
| `transport: "stdio"` | Lightweight proxy; no foreground supervisor needed. `gateway` remains the compatibility default for older configs. |
| `restart_policy: "stateless"` | Explicit opt-in to clean idle restart in lightweight mode. Default `manual` preserves an explicit reconnect boundary. |
| `idle_session_timeout_seconds` | Idle deadline per profile. `0` disables idle expiry. The default is 900 seconds. |
| `cleanup_scope: "tree"` | Private backend process tree belongs to a Windows Job Object and is cleaned up with the session. |
| `cleanup_scope: "process"` | Clean up only the backend process; descendants may be shared and may remain running. |
| `manifest.env` | Explicit environment values needed by that backend. Treat the state directory as sensitive when this contains credentials. |
| `max_sessions` | Global session capacity, including startup and cleanup. Default: 32. |
| `max_sessions_per_profile` | Session capacity per backend profile. Default: 16. |
| `max_request_bytes` | Maximum incoming JSON request body. Default: 1 MiB. |

The capacity and request-size settings belong at the top level of `config.json`.
Capacity is admission control, not a RAM or CPU hard limit. Existing work continues
when capacity is reached; new sessions receive HTTP 429.

In gateway mode, `python warden.py status --state state/gateway --json` includes sessions, pending
requests, callbacks and per-profile samples of working-set bytes and cumulative
CPU seconds. Samples cover the bridge and its direct backend, **not the entire
descendant tree**, the gateway or client apps. Missing or older samples are not
zero usage. These metrics do not measure subscription quotas or token consumption.

Choose `process` for a backend that deliberately launches shared services. Use `tree` only for private children. A session reset loses backend memory, cursors, and selections; persistent files are governed by the backend itself.

### What is protected

- Initialization, an arriving POST body, and its unfinished response.
- Pending requests in both directions, including server-to-client callbacks.
- Backend activity that refreshes the idle deadline.
- Windows owner identity, checked using PID and creation time to reject PID reuse.

A passive event stream does not keep an otherwise idle gateway session alive indefinitely. Cancellation does not prove that a backend has stopped working. When an expired gateway session returns HTTP 404, `connect` reports a fixed MCP error requesting reconnection and closes the connection. Lightweight stateless restart follows the stricter rules above. Actual application compatibility is recorded in [VALIDATION.md](../VALIDATION.md).

Modern stdio clients may probe `server/discover` before initialization. Warden
returns a correlated JSON-RPC method error and keeps the connection open for
the documented legacy `initialize` fallback. This is compatibility with that
probe, not an implementation of the complete 2026-07-28 protocol era.

### Local data and boundaries

The gateway binds to `127.0.0.1`, requires a local bearer token, and validates HTTP Host and Origin. Keep the state directory under your account and out of Git. It contains configuration, a token, session manifests, and backend diagnostic files. Backend stderr may contain private data even though gateway events avoid RPC payloads.

This is a supervisor for trusted local clients and backends, not a sandbox against malicious tools. Backends retain their own filesystem, network, and credential access. MCP Warden does not kill unrelated apps by process name, install a Windows service, or silently change global client configuration.

Runtime manifests and diagnostic files are retained locally after sessions end.
This alpha does not automatically prune disk history or enforce disk quotas.
