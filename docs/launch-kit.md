# First-user launch kit

These are **drafts for review**, not published posts or evidence of users.
The intended first cohort is five Windows volunteers using Codex CLI or Claude
Code with MCP. See the [pilot](pilot.md) and [first-run guide](first-run.md).
No outside account, subreddit, posting identity or audience has been authorized
for execution by this kit. Do not send posts, comments, moderator messages or
DMs from these drafts without that authorization.

## r/mcp showcase draft (English)

**MCP Warden: looking for 5 Windows testers for a first installation**

I maintain MCP Warden, an MIT-licensed Windows alpha for managing local MCP
backend lifetimes: protect work in progress, stop owned idle processes, and
reconnect or restart according to an explicit policy.

The recorded demo runs a 4-second tool call with a 2-second idle deadline:
the pending call stays alive, the backend stops after clean inactivity, and
the next call starts a new backend through the same SDK connection. This uses
the bundled synthetic server, not a recording of either AI client.

I'm looking for 5 people already using Codex CLI or Claude Code with MCP to
try the first-run guide and tell me where setup fails or needs explanation.
The first-run stdio example needs Python, without npm or a separate gateway.
Stateless restart loses backend memory; it is an explicit choice for this
example. Keep idle expiry disabled if that state must survive.

Repo and demo: [Borregro4/mcp-warden](https://github.com/Borregro4/mcp-warden)
· [First-run guide](https://github.com/Borregro4/mcp-warden/blob/main/docs/first-run.md)

This is v0.3.0-alpha.1. Codex and Claude each completed a four-hour native
filesystem session with four turns and verified files, both with optional
idle page release. The older synthetic hour belongs to v0.2. This does not establish multi-day reliability
or token/quota savings. [First-run feedback](https://github.com/Borregro4/mcp-warden/issues/new?template=first-run.yml)
has three required fields: client, outcome and the last completed or blocked
step. No email or full configuration. Where does the guide lose you?

## Borrador breve en español

**MCP Warden: busco 5 personas en Windows para probar la primera instalación**

Comparto MCP Warden, una herramienta de código abierto en alfa para manejar
la vida de servidores MCP locales: protege solicitudes en curso, detiene
procesos propios cuando quedan inactivos y permite reconectar o reiniciar
según una política explícita.

Busco 5 personas que ya usen Codex CLI o Claude Code con MCP para seguir la
guía y decirme qué paso falla o necesita explicación. Incluye un servidor
sintético para probar antes de conectar uno propio.

Repositorio y guía: [Borregro4/mcp-warden](https://github.com/Borregro4/mcp-warden)

Es v0.3.0-alpha.1: Codex y Claude completaron una sesión nativa de cuatro
horas cada uno, con cuatro turnos y archivos verificados; ambos activaron
la liberación opcional de páginas. La hora sintética anterior corresponde
a v0.2. Eso todavía no demuestra estabilidad de varios días
ni ahorro de tokens o cuota. El feedback puede ser un issue público breve,
sin correo, configuración completa ni conversaciones. Las actualizaciones
al día siguiente y al día 7 son opcionales.

## Community rules checked on 2026-10-31 (JST)

Public pages were read without signing into Reddit. Search/indexed page
snapshots can lag changes; recheck the live rules and the active thread before
any authorized post. Community rules describe possible channels, not the
user's authorization or a guarantee that a moderator will accept a post.

| Space | Primary rule evidence | Suitable channel and conditions | Decision |
| --- | --- | --- | --- |
| [r/ClaudeCode](https://www.reddit.com/r/ClaudeCode/) | The public sidebar's rules 4–6 prohibit repeated/disguised promotion and referral links, direct simple project sharing to the weekly showcase, and require substantial context for standalone posts. The [showcase thread inspected](https://www.reddit.com/r/ClaudeCode/comments/1xfu9ul/weekly_showcase_thread_what_are_you_building_with/) expressly accepts project links and asks how Claude Code was involved. | A comment in the **currently pinned weekly showcase** is the proposed channel. Disclose authorship, explain the actual Claude workflow and a concrete lesson. Do not repost the same project repeatedly. | Suitable after authorization and adaptation; the generic short draft alone does not yet explain Claude's role. |
| [r/mcp](https://www.reddit.com/r/mcp/) | The sidebar permits disclosed self-promotion, requires the **showcase** tag for sharing one's work, bans astroturfing and promotional AI slop, and rejects waitlists or announcements of future services. | A factual **showcase** post linking the already available release, with authorship disclosed and a concrete lifecycle example. Request feedback on the usable alpha, not signup for a future service. | Suitable after authorization and a human review of the final text; alpha status and measured limits must stay explicit. |

The `/about/rules/` URLs for ClaudeCode and OpenaiCodex returned a login shell
without rule text in this retrieval. The full public subreddit sidebars supplied
the rules above. [r/OpenaiCodex](https://www.reddit.com/r/OpenaiCodex/) exposes a
**Showcase / Highlight** flair, but the visible rules did not establish its
self-promotion policy. It is not treated as an approved distribution channel.
No Discord login or posting-channel permission was verified.

## Channel-specific preparation

For the ClaudeCode showcase, an accurate addition supported by the
[published native record](native-validation-2026-10-30.md) is:

> Claude Code was the real client in a bounded validation: two user turns via
> the official Agent SDK launching the installed CLI. One lesson was to keep
> that evidence separate from the synthetic endurance test and from untested
> multi-day use.

This describes tested client use. Do not claim Claude wrote the project or
invent a development story. If a future showcase requires development with
Claude rather than client validation, establish that fact or skip that channel.

For r/mcp, keep the authorship disclosure in the opening, use **showcase**,
and add a concrete synthetic idle/wake example from the guide. The maintainer
should review and personalize the draft; do not bulk-paste the same message
across communities or manufacture testimonials. Do not claim a general MCP
security boundary, guaranteed compatibility or lower subscription usage.

## Before an authorized external post

Confirm the posting account, exact destination and final text with the user.
Verify that the release, first-run guide, issue chooser and security link are
publicly reachable after the repository change is published. Recheck current
community rules and any account eligibility; neither karma requirements nor
permission to DM were established by this review. If a moderator's approval
is needed, prepare a request and obtain authorization before sending it.

Keep the pilot's status honest: a click, star or maintainer-run test is not an
external install. Report only voluntarily supplied results, including failed
attempts and missing follow-up. The pilot adds no automated tracking.

## Para publicar después, en español

Los textos de arriba quedan preparados; no se enviaron. Los canales propuestos
son el hilo semanal vigente de r/ClaudeCode y una publicación `showcase` en
r/mcp, sujetos a sus reglas actuales y a que se autoricen cuenta, destino y
texto. La autoría debe quedar clara. Para ClaudeCode hace falta explicar el
uso real de Claude sin inventar cómo se desarrolló el proyecto. No hay permiso
confirmado de autopromoción para r/OpenaiCodex ni para canales de Discord.
