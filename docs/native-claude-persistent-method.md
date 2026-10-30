# Persistent native Claude workload method

`scripts/native_claude_persistent.py` is an opt-in workload driver. It prepares
and validates a plan without invoking a model unless `--execute` is present.
It is separate from the earlier short, synthetic `native_claude.py` check.
A prepared four-hour plan is not a completed four-hour result.

## Existing implementation and primary-source choice

The earlier native validation already established two turns through the
official Python SDK 0.2.175 and the installed Claude Code executable. Its final
13.641-second observation is retained in
[the original report](native-validation-2026-10-30.md); it is not repeated just
to reconstruct context.

The driver extends that SDK approach because `ClaudeSDKClient` keeps the
native process and conversation alive across `query()` / `receive_response()`
calls. The one-shot `query()` function and independently resumed CLI processes
would measure a different lifecycle. The implementation uses the installed
SDK's public client, options and hook API, rather than rebuilding its control
protocol. Sources checked on 2026-10-30:

- [Official Python API](https://code.claude.com/docs/en/agent-sdk/python).
- [Official client source](https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/client.py).
- [Official native CLI reference](https://code.claude.com/docs/en/cli-reference).
- [MIT license, copyright 2025 Anthropic, PBC](https://github.com/anthropics/claude-agent-sdk-python/blob/main/LICENSE).

Context7 explicitly listed v0.1.37 rather than the installed 0.2.175. The
installed source and current official reference were therefore checked for the
actual APIs used. No SDK or native executable is vendored. The optional SDK
remains in an isolated environment and is not a Warden runtime dependency.

## Fixed workload and evidence

The common `native_workload.py` contract takes private absolute paths for the
workspace and Warden state, a single explicit filesystem profile and fixed
prompts. The official filesystem MCP is restricted to that workspace. Built-in
Claude tools, external MCP settings, project settings and slash commands are
disabled. Saved Claude subscription login is required; direct API credentials
and third-party provider environment selectors are rejected. The executable is
selected explicitly with `--claude`; credentials are never read or copied by
the driver.

A long plan has four user turns scheduled at 0, 4,800, 9,600 and 14,400 seconds
after the first turn starts. Each later turn also waits at least the declared
minimum idle interval after the preceding result. A smoke plan has two turns
and its own short interval. A PreToolUse hook limits each turn to at most 16
allowed MCP calls. A failed turn stops the run; there are no model retries.

The tasks read, reconcile and edit real fixture files through the filesystem
MCP. A turn only passes when observed tool calls have correlated successful
tool replies and the driver independently verifies the expected files. Model
claims such as “PASS” cannot satisfy those checks. SHA-256 identifies the plan,
driver, runtime and backend fingerprint files. Changes during a run fail the
source-stability check.

One SDK client remains connected for the entire schedule. The proxy identity
must remain unchanged, its backend must stop between turns and fresh backend
identities must be observed after wake. Creation timestamps from the existing
Windows sampler distinguish process identities. The selected profile must
explicitly permit stateless restart. Catalog signatures from the client are
recorded at connection and after work; Warden's restart guard revalidates the
backend catalog before releasing a held call. A signature alone does not prove
a fresh backend query, and no discovery polling is performed during idle.

Phases record wall time and monotonic elapsed time, Warden status and the test
process tree's working set, private bytes and CPU seconds. The tree is
validated by process creation timestamps and includes the harness and native
client; Warden status separately describes the proxy/direct backend scope.
SDK tool-use-to-result intervals include client scheduling and are not labeled
backend execution latency. The runtime performance test measures that separate
quantity. Client token usage is retained as reported, without inferring
subscription quota or billed savings.

Raw native messages, stderr, phase observations, plans and state stay outside
the repository. `summary.json` is also private because its source identities
contain local paths. Public results must select and sanitize observations;
never publish raw output, MCP configs, token files or account metadata.

On Windows, a process-local adapter passes `CREATE_NO_WINDOW` through the
official SDK transport. It replaces only that transport module's reference to
AnyIO while the driver runs, restores it afterwards and does not edit the
installed SDK. Cleanup only signals process handles opened by this adapter;
it does not target unrelated clients or applications.

## Deliberate execution

The SDK environment and native executable must already exist. First validate
the fixed plan, then run the approved bounded check:

```powershell
python scripts/native_claude_persistent.py --plan C:/private/claude-plan.json
C:/private/sdk-env/Scripts/python.exe scripts/native_claude_persistent.py `
  --plan C:/private/claude-plan.json --out C:/private/claude-run `
  --claude C:/verified/path/claude.exe --execute
```

The output directory must be new and outside the repository. These examples
are placeholders, not verified local file locations. Unit tests exercise
evidence validation, fixed schedules, failure stopping, the tool budget and
window hiding without invoking Claude or using model quota.
