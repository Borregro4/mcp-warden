# Validation for v0.1.0-alpha.1

Observed on Windows on 2026-10-30 (JST), with Python 3.12.10 and Node.js
24.18.1. This is synthetic local integration evidence, not a general savings
benchmark or acceptance by external users.

## Reproduce

```powershell
npm ci --ignore-scripts
npm test
npm audit --omit=dev
```

The local protocol run passed **13 Node test entries**. After the path-alias
regression fix, the Python suite passed **17 tests**.
The pure gateway entry contains 34 assertions; it is included in the 13,
not another 34 independent integration tests.

Covered behavior:

- Authentication, Host validation, malformed JSON and fixed diagnostic errors.
- Expiration of an idle session with an open passive GET event stream.
- Requests, arriving POST bodies, and reverse callbacks lasting longer than
  the configured idle interval. A standalone callback is tested with no
  client request pending.
- Separate backend processes for separate sessions; disabling idle expiry.
- Session capacity rejection before another bridge starts, and request body
  rejection before initialization.
- Session cleanup when its pinned owner process exits.
- Standard SDK client -> Python CLI -> Node stdio/HTTP adapter -> gateway ->
  Python backend: discovery, echo and reverse callback.
- Actual Windows working-set and cumulative CPU samples for the bridge and
  direct backend. No usage numbers are injected into those tests.
- An expired request receives a fixed error and is not replayed into a new
  backend. The gateway event record confirms no second bridge was started.
- Windows Job Object cleanup of a private root and descendant, checked with
  process handles. Connector ownership is tested by forcibly ending its
  Python parent after Job assignment.
- Bounded connector shutdown when DELETE does not answer, broken stdout,
  and explicit Windows stdio inheritance without a visible child window.
- Init does not overwrite an existing state directory; client configuration
  snippets parse as TOML/JSON; status requires authenticated loopback health.

The tests caught a real Windows pipe inheritance problem and connector shutdown
issues during preparation. The final tests include those cases.

The first remote Windows CI run passed the 13 Node entries but failed two Python
cases because the state comparison used a resolved token path and an unresolved
state alias. `load_config` now resolves its state argument first. A regression
test failed before this change and passed afterward; another case confirms that
a token in a sibling directory is still rejected.

## Dependency and publication checks

`npm audit --omit=dev` returned zero known advisories on 2026-10-30 after pinning
MCP node 2.1.2 and Hono node-server 2.1.5. This is a point-in-time database
result, not a security guarantee. Transitive packages and overrides are fixed
in `package-lock.json`. License copies are in `licenses/`.

A local Gitleaks scan of the selected publication files found no matching
secret patterns. Runtime tokens, manifests, private logs, personal configs and
the earlier local evaluation history are not part of the publication.

## Compatibility and limits

- Codex CLI 0.164.0 accepted a temporary stdio configuration through `mcp get`.
  That checks configuration parsing only. A live model tool call through
  Codex Desktop/CLI or Claude has **not** been validated for this release.
- The official SDK client was tested end to end. A generated Claude snippet
  is not proof of a native Claude session. Client reconnection after expiry
  depends on the client; temporary backend state is lost.
- Accelerated idle tests use 0.6-second deadlines. No multi-hour soak or
  production 15-minute idle comparison is claimed.
- Private-tree Job cleanup is tested. Preservation of shared descendants
  under `cleanup_scope: process` has not been independently requalified in
  this public package.
- Process creation is suspended before Job assignment. Assignment is not
  atomic with creation; killing the parent in that interval is not tested.
- Telemetry excludes descendant processes beyond the direct backend, the
  gateway and client apps. It is sampled, best effort and not a CPU percentage.
- Capacity limits reject new sessions. They do not cap CPU/RAM for a backend
  already running. Backends are trusted local programs, not sandboxed tools.
- State files inherit Windows directory ACLs. Store them under a private user
  directory; POSIX `0600` is not a Windows ACL guarantee.
- Runtime manifests, backend stderr and gateway events remain on disk.
  Automatic disk pruning, full application dashboards, model routing and
  subscription quota management are not implemented.
- Windows is the runtime target. Other operating systems skip ownership
  integration tests and are not supported by this alpha.

The Windows CI workflow runs Node.js 22 and Python 3.12. Consult the actual
workflow result for remote validation; this document's local results do not
stand in for it.
