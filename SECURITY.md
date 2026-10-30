# Security

MCP Warden is an alpha supervisor for trusted local clients and backends. It is
not a sandbox for untrusted tools or a boundary between Windows users.

## Boundaries

- The HTTP listener is loopback-only, bearer-authenticated and checks Host and
  Origin. Do not expose it through a public tunnel or reverse proxy.
- Treat the state directory as private. It can contain credentials in backend
  manifests, a gateway token, local paths, cached tool schemas and backend stderr.
- Backends can use their existing filesystem, network and credential access.
- `tree` cleanup covers private owned children. `process` cleanup intentionally
  allows descendants to survive; choose it only for shared-service backends.
- Expiration destroys session memory. A cancellation notice is not proof that a
  backend operation has stopped. Review the backend's own cancellation behavior.

## Reporting a vulnerability

For a report containing sensitive information, use GitHub's **Report a
vulnerability** action on this repository if available. If it is unavailable,
open an issue containing only a request for a private reporting channel. Do not
post credentials, live session identifiers, user configuration or exploit details
that expose other users.

There is no guaranteed response time or long-term support commitment for this
alpha. Fixes and affected versions will be documented with their evidence and
remaining limits.
