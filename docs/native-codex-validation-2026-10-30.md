# Native Codex acceptance — 2026-10-30

The final lightweight test used the installed Codex app's real CLI binary,
`codex-cli 0.166.0-alpha.5.1`, with model `gpt-6.1-sol` and the existing local
login. It completed in **12.703 seconds**, exit 0, with **all seven acceptance
checks passing**. This was a model-backed test, separate from SDK integration
tests. No desktop GUI interaction was tested.

## What was observed

The client discovered the synthetic Warden tools and executed exactly:

1. `echo` with a fresh per-run value.
2. `sleep` for two seconds with a distinct value.
3. `echo` with a final distinct value.

The verifier inspected completed MCP tool-call events, their successful status,
the actual result values and a common positive backend PID. It also checked
exit status, absence of unrelated actions and zero live sessions after exit.
The model's final `PASS` response alone is not evidence of those checks.

The configured idle deadline was 20 seconds. This short single-turn workflow
does **not** prove idle restart in native Codex. The persistent idle/wake test
with real Claude is [recorded separately](native-validation-2026-10-30.md);
longer synthetic SDK endurance has its own plan and result.

The final run used the runtime in candidate
[`f6ae705ce618720875e227fe58b0d9161e9e5d44`](https://github.com/Borregro4/mcp-warden/commit/f6ae705ce618720875e227fe58b0d9161e9e5d44).
Key file hashes before execution were:

| File | SHA-256 |
| --- | --- |
| `runtime/idle_stdio.py` | `3abeddced072a694f557b500b484263462f73e9b38f5d8b1be16fc583217664b` |
| `runtime/lazy_stdio.py` | `604ac275816445cdd7b2208ef7590d55abad1495e8fbdc01b24f3676916a3ee6` |
| `warden.py` | `5bbb4e244ad463babcd0de6e1d4847a03fe34e1505848524848846c5f09f88e9` |
| `scripts/native_codex.py` | `6433345f6e6b1782a818d757df17d08d98e917144d6d4baa9aae5e9db5076f78` |
| Private client event stream | `0d678a1b2513d1ed12d920715573241ed0dd043ba030e73634309487bab0a549` |
| Private result report | `6226923d0393f513dd6f067571c690d20427559ebce0038ebcd10fcb852f0979` |

## Diagnostic history

An earlier successful gateway run used Codex CLI **0.164.0**. It completed the
same three tool calls, values, common PID and cleanup checks in **23.031
seconds**. These elapsed times include model and startup time and are not a
controlled performance comparison between transports or client versions.

Failed diagnostic attempts were retained privately and excluded from passes:

- An older 0.147 executable selected from another installation did not support
  the requested model. The installed app and toolkit binaries were located
  explicitly afterward.
- A startup attempt timed out while local command execution was delayed. It
  completed no successful tool calls. The PC was reported responsive; the
  delay's cause was not established.
- A run reached three MCP approval failures. The synthetic fixture lacked
  read-only annotations needed for this client's automatic approval policy.
  Correct fixture annotations were added; global approval settings were not
  changed.
- A lightweight attempt prohibited the client's deferred-tool discovery
  action and made no successful MCP calls. The prompt now permits
  `tool_search` solely to discover the fixture tools before the three calls.

These changed-condition diagnostics are not a measured failure rate across
identical trials.

## Deliberate reproduction

```powershell
python scripts/native_codex.py --execute `
  --codex C:/absolute/path/to/codex.exe `
  --out ../private-native-codex-results --transport stdio
```

This opt-in test uses the signed-in account's model quota. Ordinary tests and
CI do not run it. It uses an ephemeral read-only session, ignores user config,
disables project-document loading and configures only the synthetic MCP server.
It does not edit global client configuration or copy authentication files.

Raw client output, account-related metadata and state remain outside the repo.
No subscription savings, invoice savings, universal model compatibility or
general superiority is inferred from this check.
