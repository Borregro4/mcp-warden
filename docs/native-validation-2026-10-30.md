# Native Claude validation — 2026-10-30 (JST)

These checks used the installed **Claude Code 2.1.309** executable, shipped with
Claude Desktop 4.4212.0.0, and the account's existing Claude subscription login.
They exercised the real native client and model, not a mock of Claude. The
Desktop graphical interface itself was not tested.

All successful checks requested the `sonnet` alias at low effort. The client
reported **claude-sonnet-5-5**, version **2.1.309**, and `apiKeySource: none`.
No credential files were read or copied by the harness. Agent settings and
production MCP configurations were not edited.

## Observed successful checks

| Check | Transport / client driver | Observed result |
| --- | --- | --- |
| One native user turn | Gateway; native `claude -p` | Exactly one `echo`, one two-second `sleep`, and one roots callback completed in order. All three replies contained the expected synthetic values and the same backend PID. Exit 0, no stderr. Total run time: 31.203 seconds, including native startup and model turns. |
| Two native user turns in one client connection | Lightweight stdio, explicit `restart_policy: stateless`, three-second idle; official `claude-agent-sdk==0.2.175` `ClaudeSDKClient` | The first echo returned `native-before-expiry`. The backend became idle and stopped. A second user turn returned `native-after-expiry` from a different backend PID while the proxy owner PID stayed the same. Both turns reported success, six deterministic checks passed, no startup reconnect was requested by the harness, and stderr was empty. Total run time: 16.672 seconds. |
| Final repetition after bounded-writer and Windows ownership corrections | Same lightweight stdio profile, SDK and installed native client; two user turns | Two echo results, the three-second idle stop, a different backend on wake, and the same proxy owner were observed again. Both turns reported success, all six checks passed, exit 0, no startup reconnect, and no stderr. Total run time: 13.641 seconds. |

For both persistent checks, status changed from pending to connected before the
first model request. The backend PIDs were 63880 and 68536; the same proxy PID,
47472, owned both exchanges in the initial check. In the final repetition the
backend PIDs were 9488 and 44292, with proxy PID 71116 unchanged. These are
transient identifiers from the tests,
not machine-independent expected values.

The stateless profile is an explicit choice for this synthetic backend. This
does not establish that arbitrary stateful tools can lose in-memory state
without consequence, or that previous tool calls may safely be replayed.
The restart policy also requires an unpaginated tool catalog observed on the
connection, so its metadata can be revalidated before a held call is released.
A catalog known only to the client's cache is not evidence available to Warden;
without an observed baseline, a fresh client connection is required.

## Evidence identities

Raw output and local state remain private because native diagnostics can contain
paths or account metadata. Only these selected observations and hashes are
published:

| Artifact | SHA-256 |
| --- | --- |
| Basic native stdout | `9f923d13d822b81b75b84ad2a23c8c8eae8d9063c2904edfcd341b2c2050315b` |
| Basic driver snapshot | `cc08ecfb22767facb344f0d5923f7f9fe467cfe7e31696518660b782fcdba404` |
| Initial persistent SDK stdout | `b7c02c9603ccda3930143431d618b014b48332db7cbde89253f1698a50fb7d41` |
| Initial persistent driver snapshot | `6c683ac6a4fa0a9d5d4cc70bad85b9bd09f2ec3ad5734ecd4d35e09f027d896a` |
| Final persistent SDK stdout | `b85fde86ba635c700ea5b20046992a63959cb05174a56515052161021ea375fe` |
| Final persistent driver snapshot | `9a42ee3f1830ad599ed74e5f42e5bf801f6cb426497494c8bb9d99418d84803f` |
| Final `runtime/idle_stdio.py` snapshot | `3abeddced072a694f557b500b484263462f73e9b38f5d8b1be16fc583217664b` |
| Final `runtime/lazy_stdio.py` snapshot | `c8ae30baf15fc9fd28c5142b3b289fcbf3d9d61fba8695552a87bfb49831c3d1` |
| Final `warden.py` snapshot | `5bbb4e244ad463babcd0de6e1d4847a03fe34e1505848524848846c5f09f88e9` |

After this native repetition, the error path for failed process creation was
changed to close its unused Windows Job handle explicitly. The normal spawn
path is unchanged. The release candidate's `runtime/lazy_stdio.py` hash is
`604ac275816445cdd7b2208ef7590d55abad1495e8fbdc01b24f3676916a3ee6`;
the final full regression suite includes the failed-spawn case. The table
above records the actual native-run snapshot, not a later file hash.

The driver evolved during investigation. A snapshot hash identifies what was
run; it is not a promise that the current script has identical bytes.

## Failures preserved during investigation

The basic gateway run included an interrupted initial startup before its
successful connection. Its record contains one `initialize_interrupted`
session and one successful backend start. It is not described as a flawless
first-attempt connection.

Subsequent persistent diagnostics exposed both harness defects and a real
compatibility defect. They are kept separate:

- Early hand-written streaming drivers omitted parts of the native Agent SDK
  control protocol. Those runs produced no completed model turn and do not
  validate persistence. The persistent driver now uses the official SDK.
- A ten-second harness readiness deadline expired just before a gateway
  started. The diagnostic deadline was increased and is explicitly bounded.
- An early SDK driver called reconnect while the server was still pending.
  That cancelled initialization. The final driver waits for connected status
  and does not perform that reconnect.
- With those harness issues removed, the native v2 MCP runtime sent
  `server/discover` before `initialize`. The gateway answered HTTP 400
  `Initialize required`, and the connector closed. An instrumented private
  run captured the method name, response status and fixed error category,
  without logging its bearer token or RPC payloads.
- The compatibility fix returns a correlated JSON-RPC `-32601` response for
  the unsupported pre-initialize discovery probe while keeping the connection
  open for the legacy initialize fallback. It does not claim full support for
  the modern MCP protocol. The final native persistent check passed after
  that correction.

This is diagnostic history with changing conditions, not a measured success
rate across identical trials. No failed diagnostic is counted as a successful
native workflow.

## Reproduce deliberately

The single-turn check needs the installed native Claude executable and its
existing login. The persistent check also imports the optional official Python
SDK. Install it in an isolated environment, not as a required Warden runtime
dependency:

```powershell
python -m venv ../private-claude-test-env
../private-claude-test-env/Scripts/python.exe -m pip install claude-agent-sdk==0.2.175
../private-claude-test-env/Scripts/python.exe scripts/native_claude.py `
  --claude C:/absolute/path/to/claude.exe `
  --output-dir ../private-claude-results `
  --scenario reconnect --transport stdio
```

The output directory must be outside the repository and must not already exist.
The driver uses only the synthetic example server, an explicit MCP config,
restricted mode, no built-in tools, and no session persistence. The persistent
check sets a 90-second MCP startup allowance for that child process. It does not
modify a global startup setting. This is an opt-in model-backed check and is
excluded from ordinary unit tests and CI.

## Scope and limits

These observations prove the selected native client workflows. They do not
prove multi-hour stability, compatibility with every Claude build, performance
superiority, quota reduction, or billed savings. Client-reported token and list
cost estimates are not a subscription-quota or invoice measurement.

Primary references checked during implementation:

- [Claude CLI flags and isolation](https://code.claude.com/docs/en/cli-reference)
- [Official Python Agent SDK client API](https://code.claude.com/docs/en/agent-sdk/python)
- [MCP discovery and legacy negotiation](https://ts.sdk.modelcontextprotocol.io/v2/protocol-versions)
- [Agent SDK source and MIT license](https://github.com/anthropics/claude-agent-sdk-python)

The optional SDK's license is MIT, copyright 2025 Anthropic, PBC. Its package
and bundled CLI are not vendored into this repository; the test explicitly
selects the already installed native executable through `cli_path`.
