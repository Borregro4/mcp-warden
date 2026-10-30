# Contributing

Start with a small, reproducible lifecycle problem. Include the Windows build,
Node and Python versions, the relevant client version, expected behavior and a
sanitized reproduction using a synthetic backend. Do not attach live tokens,
complete client configuration, conversation logs or backend stderr containing
private data.

## Local checks

Use Windows, Node.js 22 or newer, and Python 3.12:

```powershell
npm ci --ignore-scripts
npm test
```

Keep tests isolated from real client settings, production MCP services, user
documents and devices. Temporary state must remain under the test's own
directory. Bind listeners to loopback, choose free ports and clean up processes
the test itself owns.

## Changes worth reviewing carefully

- Preserve request and callback protection while changing idle logic.
- Check PID and creation time together when assigning ownership.
- Keep private process trees separate from intentionally shared descendants.
- Do not replay a potentially completed tool call after a network failure.
- Avoid logging RPC bodies, credentials or raw environment values.
- Check upstream licenses and transport contracts before updating dependencies.

For a client compatibility claim, record the exact tested client version,
configuration, scenario and observed outcome in `VALIDATION.md`. Passing a
protocol fixture does not establish a successful real Claude or Codex run.

Contributions are made under this project's MIT license. Preserve existing
third-party notices. Discuss large architectural changes in an issue before
investing in an implementation.
