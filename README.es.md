# MCP Warden

[English](README.md)

Supervisor local del ciclo de vida de servidores MCP en Windows. El modo ligero usa un proxy stdio de Python y su servidor. El modo gateway opcional agrega sesiones HTTP y límites centrales de admisión. Ambos protegen el trabajo en curso y limpian sus procesos propios.

**Versión alfa.** Esta entrega se enfoca en las sesiones y la propiedad de procesos en Windows. No selecciona modelos de IA, limita cuotas de suscripción ni acredita ahorro de tokens o dinero. Consulta las [pruebas y los límites vigentes](VALIDATION.md) antes de adoptarlo.

## Para qué sirve

Varias sesiones de programación pueden dejar varias copias del mismo servidor MCP ejecutándose. Para cerrarlas correctamente hace falta saber qué sesión inició cada proceso, si hay llamadas o callbacks pendientes y si sus procesos hijos son privados o compartidos.

El proxy ligero administra al servidor directamente. El modo HTTP/SSE opcional agrega esa gestión alrededor de [Supergateway](https://github.com/supercorp-ai/supergateway). Warden comprueba el PID de Windows **y su hora de creación**, protege las operaciones pendientes y permite vencer una sesión HTTP inactiva aunque conserve un stream GET abierto.

## Inicio rápido del modo ligero

Requisitos: Windows y Python 3.12 en `PATH`. Este modo no necesita un proceso
Node, instalación npm, puerto HTTP ni supervisor en primer plano.

El servidor incluido es sintético y no conserva estado necesario entre llamadas.
Para probarlo:

```powershell
python warden.py init --state .warden-state --transport stdio --restart-policy stateless
python warden.py doctor --state .warden-state --transport stdio
python warden.py configuration --state .warden-state --profile example --client codex
# O imprime la configuración para Claude:
python warden.py configuration --state .warden-state --profile example --client claude
```

Agrega la entrada generada al cliente. El cliente inicia `connect` cuando hace
falta. Consulta las identidades de los proxies activos con:

```powershell
python warden.py status --state .warden-state --transport stdio --json
```

Usa `restart_policy: "stateless"` únicamente cuando sea aceptable perder la
memoria del servidor, sus cursores y selecciones. La política predeterminada es
`manual`: una conexión vencida pide reconectar. Puedes conservar un servidor
con estado desactivando el vencimiento con `idle_session_timeout_seconds: 0`.

El reinicio stateless mantiene la conexión stdio, inicializa otro servidor,
comprueba sus instrucciones y el catálogo de herramientas observado previamente,
y envía la solicitud nueva una vez. Cambios de esquemas o permisos declarados,
catálogos paginados, falta de un catálogo de referencia, cancelaciones y resultados
inciertos requieren una conexión nueva. Las llamadas anteriores no se repiten.

Este modo limita los mensajes a 1 MiB y las colas a 16 entradas. Los límites
globales de sesiones pertenecen al modo gateway. Las muestras de estado cubren
el proxy y su servidor directo; no incluyen a todos sus descendientes.

## Gateway HTTP opcional

Requisitos: Windows, Python 3.12 y Node.js 22 o posterior en `PATH`. Usa una terminal normal, sin permisos de administrador.

Desde una copia del repositorio:

```powershell
npm ci --ignore-scripts
python warden.py init --state .warden-state
python warden.py doctor --state .warden-state
python warden.py serve --state .warden-state
```

Deja esa terminal abierta. En otra terminal:

```powershell
python warden.py status --state .warden-state
python warden.py configuration --state .warden-state --profile example --client codex
# O genera la entrada JSON para Claude:
python warden.py configuration --state .warden-state --profile example --client claude
```

`init` crea una carpeta local con el token, la configuración del gateway y un servidor de ejemplo. `configuration` imprime fragmentos de configuración: revisa el que corresponda y agrégalo a tu cliente. Ese comando no modifica la configuración existente. `serve` se ejecuta en primer plano; Ctrl+C en esa terminal cierra sus sesiones.

La entrada stdio generada ejecuta:

```powershell
python warden.py connect --state .warden-state --profile example
```

`connect` es un transporte MCP: reserva stdout para el protocolo. Configura tu cliente para ejecutarlo en lugar de usarlo de forma interactiva.

## Conectar un servidor propio

Edita `config.json` dentro de tu carpeta de estado. Cada perfil tiene un manifiesto para su servidor. Empieza con un servidor de prueba y conserva una copia de la configuración anterior del cliente.

```json
{
  "profiles": {
    "mi-servidor": {
      "transport": "stdio",
      "restart_policy": "manual",
      "idle_session_timeout_seconds": 900,
      "manifest": {
        "command": "C:\\ruta\\absoluta\\servidor.exe",
        "args": [],
        "env": {},
        "cleanup_scope": "tree"
      }
    }
  }
}
```

Es un fragmento de perfil, no la configuración completa. Conserva las rutas y los demás campos que generó `init` y genera la entrada de tu perfil. Las conexiones stdio nuevas leen el perfil actualizado. Si usas `transport: "gateway"`, reinicia el supervisor después de cambiar su configuración.

| Opción | Efecto |
| --- | --- |
| `transport: "stdio"` | Proxy ligero sin supervisor en primer plano. `gateway` conserva la compatibilidad con configuraciones anteriores. |
| `restart_policy: "stateless"` | Autoriza el reinicio tras inactividad limpia en el modo ligero. El valor predeterminado `manual` pide reconectar. |
| `idle_session_timeout_seconds` | Plazo de inactividad por perfil. `0` desactiva el vencimiento. El valor predeterminado es 900 segundos. |
| `cleanup_scope: "tree"` | El árbol privado del servidor queda dentro de un Job Object de Windows y se limpia con su sesión. |
| `cleanup_scope: "process"` | Sólo se limpia el proceso del servidor; sus hijos pueden ser compartidos y seguir ejecutándose. |
| `manifest.env` | Variables que necesita el servidor. Si contienen credenciales, trata la carpeta de estado como información sensible. |
| `max_sessions` | Capacidad global de sesiones, incluidas las que se están iniciando o cerrando. Valor predeterminado: 32. |
| `max_sessions_per_profile` | Capacidad de sesiones por perfil de servidor. Valor predeterminado: 16. |
| `max_request_bytes` | Tamaño máximo del cuerpo JSON entrante. Valor predeterminado: 1 MiB. |

Las opciones de capacidad y tamaño de solicitud van en el nivel principal de
`config.json`. La capacidad controla la admisión de sesiones; no impone un límite
estricto de RAM o CPU. Al alcanzar la capacidad, las operaciones existentes
continúan y las sesiones nuevas reciben HTTP 429.

En el modo gateway, `python warden.py status --state .warden-state --json` incluye sesiones,
solicitudes pendientes, callbacks y muestras por perfil de memoria de trabajo
en bytes y segundos acumulados de CPU. Las muestras cubren el puente y su
servidor directo, **no todo el árbol de procesos hijos**, el gateway ni las
aplicaciones cliente. Una muestra ausente o antigua no significa consumo cero.
Estas métricas no miden cuotas de suscripción ni consumo de tokens.

Usa `process` cuando el servidor inicie servicios compartidos intencionalmente. Usa `tree` para hijos privados. Reiniciar una sesión pierde la memoria del servidor, sus cursores y selecciones; el propio servidor determina qué pasa con los archivos persistentes.

## Qué protege

- La inicialización, el cuerpo de un POST que sigue llegando y su respuesta pendiente.
- Las solicitudes en ambas direcciones, incluidos los callbacks del servidor al cliente.
- La actividad del servidor que renueva el plazo de inactividad.
- La identidad del propietario mediante PID y hora de creación, para rechazar PID reutilizados.

Un stream pasivo no mantiene viva indefinidamente una sesión gateway sin trabajo. Cancelar no demuestra que el servidor haya terminado. Cuando una sesión gateway vencida devuelve HTTP 404, `connect` pide reconectar y cierra la conexión. El reinicio stateless ligero sigue las condiciones anteriores. La compatibilidad probada se registra en [VALIDATION.md](VALIDATION.md).

Los clientes stdio modernos pueden enviar `server/discover` antes de inicializar.
Warden responde con un error JSON-RPC asociado al ID correcto y conserva la
conexión para la negociación anterior con `initialize`. Es compatibilidad con
esa consulta previa; no implementa toda la revisión de protocolo 2026-07-28.

## Datos locales y alcance

El gateway escucha en `127.0.0.1`, exige un token local y valida Host y Origin. Guarda la carpeta de estado bajo tu cuenta y fuera de Git. Contiene configuración, token, manifiestos de sesiones y diagnósticos del servidor. Los mensajes de stderr del servidor pueden contener información privada, aunque los eventos del gateway eviten registrar el contenido de las llamadas.

Está pensado para clientes y servidores locales de confianza. Los servidores conservan su propio acceso a archivos, red y credenciales. MCP Warden no termina aplicaciones ajenas por nombre, instala un servicio de Windows ni cambia silenciosamente la configuración global del cliente.

Los manifiestos de ejecución y los diagnósticos se conservan localmente después
de cerrar las sesiones. Esta versión alfa no depura automáticamente ese historial
ni impone cuotas de espacio en disco.

## Desarrollo

```powershell
npm ci --ignore-scripts
npm test
```

Las pruebas usan servidores sintéticos locales y carpetas temporales. El [workflow de CI](.github/workflows/test.yml) ejecuta la suite en Windows. Tener el archivo del workflow no acredita una ejecución remota exitosa: revisa su resultado y [VALIDATION.md](VALIDATION.md).

Consulta [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), la
[comparación con proyectos relacionados](docs/alternatives.md) y la
[medición de la primera alfa](docs/benchmark-results-2026-10-30.md).
Ese resultado mostró por qué hacía falta reducir el costo del modo stdio;
no acredita superioridad general frente a otros supervisores. La
[comparación del modo ligero](docs/benchmark-lightweight-2026-10-30.md) registra
la mejora de memoria y las métricas donde mcp-snooze sigue adelante.

## Licencia y atribución

MCP Warden usa la licencia MIT. Supergateway y los paquetes MCP fijados conservan sus propias licencias y avisos; consulta [NOTICE](NOTICE) y [licenses/](licenses/). Proyecto independiente, sin afiliación con OpenAI, Anthropic ni los responsables de MCP.
