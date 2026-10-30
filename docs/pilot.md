# First-user pilot

We are looking for an initial **five volunteers** who already use **Windows,
Codex CLI or Claude Code, and a local MCP backend**. The immediate question is
whether a new person can install Warden from the guide and understand its
lifecycle behavior. Participation and all follow-up are voluntary.

Start with the [first-run guide](first-run.md), using the included synthetic
backend before trying a trusted backend of your own. Use the public
**v0.3.0-alpha.1** release for a comparable first attempt. Do not change a
production workflow just to take part. There is no separate pilot signup,
email requirement, automatic telemetry or scheduled reminder.

## A small, reproducible attempt

1. When possible, record Warden's version, Windows build, client/version and
   backend/version. Start a timer, or estimate time spent if you prefer.
2. Follow the guide in a fresh local state directory. Use the same synthetic
   example and lifecycle checks described there. Record any personal help you
   used; using a coding assistant to solve a blocked step counts as help.
3. Note whether the first call, idle behavior, wake/reconnect and final cleanup
   matched the guide. Stop at a blocked step; a failure report is useful.
4. Optionally submit [first-run feedback](https://github.com/Borregro4/mcp-warden/issues/new?template=first-run.yml).
   An existing GitHub account is needed to open an issue; running the example
   privately does not require reporting. Use the
   [bug form](https://github.com/Borregro4/mcp-warden/issues/new?template=bug.yml)
   for a reproducible defect or the
   [compatibility form](https://github.com/Borregro4/mcp-warden/issues/new?template=compatibility.yml)
   for one exact client/backend scenario.

Only the client, installation outcome and last completed or failed guide step
are required to submit first-run feedback. Versions, Windows build, backend
details and setup duration are useful but optional; leave them blank or say
“unknown” / “not measured” when unavailable. If possible, describe expected vs
observed behavior. If the guide changed during a retry, mention that revision;
a repaired retry does not erase the initial failure. English and Spanish are
both welcome.

## Optional memory observation

Use ordinary Windows process measurements only if you want to. Record **Private
bytes** and **Working set** separately, with units, time after the last call,
backend, policy and processes included. Compare the same backend/workload run
directly and through Warden if practical. Otherwise say “not measured.”
Do not substitute one metric for the other or infer subscription savings.

Warden's status scope is the proxy/bridge and direct backend; it does not cover
every descendant or the client app. The
[published comparison](benchmark-lightweight-2026-10-30.md) has a controlled
synthetic workload and its own process attribution. A participant's snapshot
is a separate observation, not a reproduction of that benchmark.

## Optional day-1 and day-7 comments

You can add either update to your original issue without creating a new report:

```text
Day 1 or Day 7:
Still using / stopped / have not tried again:
Client and backend (and any version change):
What worked or the step that blocked me:
One reason to keep using it or stop:
Memory observation, if measured (Private bytes and Working set separately):
```

No reply is required. A missing update means **unknown**, not retained or lost.
Stopping because the tool is unnecessary is useful feedback too. No one will be
contacted privately or enrolled in reminders through this pilot.

## Proposed checkpoints, not results

The initial cohort is five volunteers. A later cumulative checkpoint could be
**10 distinct people attempting the guide, 5 completing it without individual
help, and 3 explicitly reporting continued use at day 7**. These are proposed
learning goals, not achieved counts or promises. Reaching ten people would
require extending beyond the first cohort.

Only count voluntarily reported attempts, and keep distinct people separate
from retries. Record helped and unassisted completions separately. A day-7
report requires seven elapsed days from that person's first attempt and an
explicit statement of use; a star, download, issue, CI run or maintainer test
does not count as adoption. With such a small, self-selected sample, report
counts and missing follow-up rather than a population retention rate.

Maintain any summary manually from public issue links and reported facts.
There is no tracking code, automatic collection or collection of private
accounts/configuration. No external participant outcomes are reported by
this document.

## What has and has not been qualified

The [current validation record](../VALIDATION.md) contains a four-hour native
filesystem session for each client: four turns each, 14 actual Codex MCP calls
and 21 Claude MCP calls, with verified file effects, idle/wake and cleanup.
Both explicitly enabled optional idle page release. The older synthetic
one-hour result belongs to v0.2. These bounded checks do not establish
seven-day reliability or compatibility with every client/backend.

Stateless restart discards backend memory, cursors and selections. Disable
idle expiry when that state must survive; manual reconnect makes the reset
boundary explicit but does not preserve backend memory. This alpha is
not a security sandbox and does not claim token, quota or bill savings.
Read [SECURITY.md](../SECURITY.md) before attaching a real backend.

## Privacy

All GitHub issues and comments here are public. Share versions and a minimal
sanitized reproduction only. Do not post email addresses, credentials, private
paths, session identifiers, full client configuration, conversation logs or
raw backend stderr. Screenshots are optional and must be checked for private
content. Vulnerabilities follow [the security reporting process](../SECURITY.md),
including a request-only issue if a private report action is unavailable.

## En español

Buscamos primero **cinco voluntarios** que ya usen Windows, Codex CLI o Claude
Code y MCP. Sigue la [guía de primer uso](first-run.md) con el ejemplo sintético
de **v0.3.0-alpha.1**. Si puedes, anota versiones y tiempo de instalación;
son útiles pero opcionales al enviar. El formulario sólo exige cliente,
resultado y último paso completado o fallido. Puedes responder en español
y dejar lo demás vacío o indicar “desconocido” / “no medido”; no necesitas
compartir correo, configuración completa ni conversaciones.

Las respuestas al día siguiente y al día 7 son opcionales. Si no respondes,
el resultado queda desconocido. Las metas de 10 intentos, 5 instalaciones sin
ayuda y 3 personas que sigan usándolo al día 7 son propuestas para una etapa
posterior; este documento no las presenta como resultados. Si mides memoria,
separa bytes privados de conjunto de trabajo y conserva las unidades y el
alcance. No hay telemetría ni recordatorios automáticos.
