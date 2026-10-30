# SDK demo: idle stop and wake

![Verified SDK lifecycle demo](../assets/demo.gif)

This is a terminal-style **replay of an actual Windows execution** of
[`scripts/demo.mjs`](../scripts/demo.mjs), slowed down for reading. It uses MCP
SDK 1.32.0, the real Python CLI and the bundled synthetic
[`echo_server.py`](../examples/echo_server.py). It makes zero model calls.
It is not a recording of Codex, Claude or a desktop application.

The backend's idle deadline is accelerated to **2 seconds** for this demo.
The regular generated default is 900 seconds. The example explicitly opts into
`restart_policy: "stateless"`; use that policy only when losing backend memory
is acceptable. Native application compatibility and longer tests are documented
separately in [VALIDATION.md](../VALIDATION.md).

## Recorded result

Recorded on **2026-10-31 JST** (2026-10-30 15:47 UTC). Runtime baseline:
[`5c95856307e6610c9a1cf2b19a2f6d5cd208deea`](https://github.com/Borregro4/mcp-warden/commit/5c95856307e6610c9a1cf2b19a2f6d5cd208deea),
version `0.2.0-alpha.1`. The new demo harness is identified separately by its
actual file SHA-256 in [plan.json](evidence/demo/plan.json). The runtime files
were unchanged from that baseline. Environment: Windows, Python 3.12.10,
Node v24.18.1 and MCP SDK 1.32.0.

| Check | Observation |
| --- | --- |
| Isolated init | Fresh external state; synthetic `example` profile; stateless policy; 2 s idle deadline. |
| Handshake and catalog | One real SDK connection; `initialize` and `tools/list`; four example tools discovered. |
| Echo integrity | Unique sent values were compared exactly with the returned values. |
| Pending RPC protection | The 4 s `sleep` request was still pending after 2.635 s; backend PID 52432 and its creation identity were still alive. |
| Clean idle expiry | The old backend identity was absent after 2.163 s without calls; proxy PID 34256 and its creation identity remained alive. |
| Same SDK connection | The next echo used the original client and original proxy. |
| New backend generation | The reply came from PID 73652 with a different Windows creation FILETIME; the exact new value matched. |
| Explicit memory scope | Samples contain only the proxy and direct backend: 36.55 MiB before idle; 24.53 MiB with only the proxy. |
| Cleanup | CLI reported zero sessions; direct PID + creation probes found zero remaining owned identities; proxy exited cooperatively with code 0. |

The measured run took **10.04 s**. The new backend answered in **189.4 ms** on
this one execution. These are observations from one host, not a latency promise,
continuous memory peak or performance comparison. Summed working sets can
double-count shared pages.

CLI status is refreshed by the proxy every two seconds. The raw event log keeps
its actual snapshots, including a temporarily stale `backendPid: null` directly
after startup and wake. Exact liveness conclusions use independent Windows
PID **and creation FILETIME** probes, not a stale status snapshot or process name.

## Reproduce the real execution

The demo scripts were added **after** the original `v0.2.0-alpha.1` tag.
Use a checkout of `main` that contains `scripts/demo.mjs` for this optional
SDK reproduction; they are absent from the original release download used
by the first-run guide. The recorded runtime baseline above remains v0.2.
Newer checkouts record their own commit and source hashes in `plan.json`.

From that checkout's root in a normal Windows PowerShell terminal:

```powershell
# Node 22+ and Python 3.12 are needed by this SDK demo.
# Lightweight Warden itself does not need npm or a Node process.
npm ci --ignore-scripts
$DemoRun = Join-Path (Split-Path (Get-Location).Path -Parent) ('warden-demo-' + [guid]::NewGuid().ToString('N'))
node scripts/demo.mjs --out $DemoRun
Get-Content (Join-Path $DemoRun 'public\result.json')
```

The script requires a **new directory outside the checkout** and refuses to
overwrite an earlier run. It starts only its own example processes and closes
the SDK client in `finally`. It does not edit installed MCP client settings,
start a service, change a model or send network requests to a model provider.
The printed JSON contains only the public event fields.

Expected outcome: `passed: true`, nine true checks and a cleanup event with
`sessionCount: 0` and `remainingOwnedIdentities: []`. PIDs, creation times,
hashes of random nonces, RAM samples and elapsed times will vary.

The output layout is deliberately separate:

- `public/`: event log, plan and final result suitable for review.
- `state/`: private generated configuration, token and backend stderr. Keep it
  local and out of Git. No files from this directory are included in the demo.
- `identity-query.json`: local helper input, also outside the repository.

Nothing is deleted automatically. Runtime processes are already stopped by the
script; retain the local folder for inspection or move it to the Recycle Bin
when no longer needed.

## Regenerate the animation

This optional authoring step needs Pillow; it is not a Warden runtime dependency.
Use a Python environment that already has Pillow, or install it in a disposable
authoring environment. The checked-in GIF used Pillow 12.3.0 and the local
Windows Consolas font; no font file is bundled.

```powershell
# Run with the Python executable from your Pillow authoring environment.
python scripts/render-demo.py --evidence "$DemoRun\public" --out "$DemoRun\render" --font "$env:WINDIR\Fonts\consola.ttf"
```

The renderer accepts only a complete passing result and writes a new output
directory. It produces `demo.gif`, `demo-poster.png` and `render.json`. The
committed animation is 900 × 570 pixels, eight frames and 40 seconds per loop;
its final result frame stays visible for nine seconds. The poster is that final
frame. The displayed commands are abbreviated excerpts; the executable source
is the demo script.

For authoring, [Pillow](https://github.com/python-pillow/Pillow) was preferred
over [asciinema/agg](https://github.com/asciinema/agg): Pillow was already
installed locally, and this replay only needs bounded text frames. Pillow's
[MIT-CMU license](https://github.com/python-pillow/Pillow/blob/main/LICENSE)
and GIF encoder source were reviewed; agg's GPLv3 license and additional Rust
toolchain/binary requirements were also checked. No upstream source was copied
and neither library nor font files are distributed by this repository.
The SDK flow follows its
[version 1.32.0 client documentation](https://github.com/modelcontextprotocol/typescript-sdk/blob/1.32.0/docs/client.md)
and the existing Warden SDK tests and soak harness.

## Evidence and privacy

- [Plan and original source-byte hashes](evidence/demo/plan.json)
- [Sanitized measured events](evidence/demo/events.jsonl)
- [Nine criteria and final result](evidence/demo/result.json)
- [Render dimensions, timing and asset hashes](evidence/demo/render.json)

The public files contain actual synthetic PIDs and creation times, not aliases.
Absolute local paths, runtime configuration, token contents, environment
variables and backend stderr are excluded by construction. Random tool values
are replaced with `demo-before-<nonce>`, `demo-long-<nonce>` and
`demo-after-<nonce>`. Their recorded SHA-256 hashes cover the original UTF-8
values, while equality was asserted before redaction. The render input hashes
cover the exact sanitized files committed here, not an unredacted transcript.
No existing chats, model outputs or desktop images were used.
