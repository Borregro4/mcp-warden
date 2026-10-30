# Primera ejecución en Windows

[English](first-run.md) · [Volver al README](../README.es.md)

## 1. Descarga o clona v0.2.0-alpha.1

Elige una opción. Esta guía usa la
[alfa publicada para Windows](https://github.com/Borregro4/mcp-warden/releases/tag/v0.2.0-alpha.1).

**Sin Git:** descarga **Source code (zip)** de esa versión, extrae todo el
archivo en una carpeta tuya y abre PowerShell en la carpeta extraída que
contiene `warden.py`. Para cambiar de carpeta manualmente, sustituye esta ruta
de ejemplo por la tuya:

```powershell
Set-Location 'C:\ruta\a\la\carpeta\mcp-warden'
```

**Con Git:** desde una carpeta donde todavía no exista `mcp-warden`:

```powershell
git clone --branch v0.2.0-alpha.1 --depth 1 https://github.com/Borregro4/mcp-warden.git mcp-warden
Set-Location .\mcp-warden
```

Git puede avisar que es una etiqueta anotada y que quedaste en detached HEAD.
Es normal al usar esta versión fija; no necesitas cambiar de rama para ejecutarla.

Conserva esa carpeta en su lugar mientras la entrada del cliente la use:
Warden incluye rutas absolutas en la configuración que genera.

## 2. Comprueba los requisitos

Necesitas Windows y Python **3.12 o posterior** en `PATH`, además de
**Codex CLI o Claude Code** instalado y con sesión iniciada. Usa una terminal
normal de PowerShell, sin permisos de administrador. Esta guía stdio no
necesita Node.js, instalación npm, una clave de API para el ejemplo ni un
gateway separado.

```powershell
python --version
python -c "import sys; print(sys.executable)"
python warden.py --version
```

Comprueba que Python indique 3.12+ y Warden `0.2.0-alpha.1`. Conserva la ruta
del ejecutable si tienes varias instalaciones de Python. Para tu cliente,
comprueba `codex --version` o `claude --version`; confirma primero que puedes
abrirlo normalmente con la sesión que ya tienes.

[El registro de validación](../VALIDATION.md) indica las versiones exactas
probadas. No garantiza todos los builds posteriores ni la interfaz de
escritorio.

## 3. Inicializa sólo el servidor sintético incluido

El servidor `example` devuelve un valor y su PID. No conserva estado necesario
entre llamadas ni necesita credenciales. **La opción `stateless` de abajo
corresponde a este ejemplo.** No la copies a un servidor real hasta comprobar
que sea aceptable perder su memoria, cursores y selecciones.

```powershell
python warden.py init --state .warden-state --transport stdio --restart-policy stateless
python warden.py doctor --state .warden-state --transport stdio --json
```

El plazo normal de inactividad es de **900 segundos**. Si quieres hacer una
prueba corta opcional de cierre por inactividad, agrega `--idle-seconds 15`
al comando `init` **antes de ejecutarlo**. Usa una carpeta de estado nueva o
vacía: `init` se niega a sobrescribirla. No ejecutes las dos variantes contra
una misma carpeta inicializada.

`doctor` debe indicar `"ready": true` y todas las comprobaciones en true.
Verifica el runtime y el estado locales; no comprueba tu inicio de sesión
ni una llamada real desde el cliente.

Guarda `.warden-state` bajo tu cuenta y fuera de Git. Contiene configuración
local, un token y diagnósticos de ejecución, incluso en modo stdio.

## 4. Respalda y combina una entrada del cliente

Elige **un** cliente. `configuration` sólo imprime texto: no escribe opciones
ni inicia un servidor. El cliente ejecutará después el transporte `connect`
de Warden. No ejecutes `connect` como comando interactivo: su stdout está
reservado para MCP.

### Codex CLI

```powershell
python warden.py configuration --state .warden-state --profile example --client codex
```

Para probar sólo en este proyecto, usa `.codex/config.toml` dentro de esta
copia del repositorio. La
[documentación oficial de OpenAI](https://learn.chatgpt.com/docs/extend/mcp#configure-with-configtoml)
también admite `~/.codex/config.toml` para el usuario; elige el archivo que
corresponda a tu configuración.

1. Si el archivo elegido existe, cópialo a un respaldo separado antes de editar.
2. Crea su carpeta y el archivo si hacen falta.
3. Agrega la **tabla `[mcp_servers."warden-example"]` impresa**, con su
   `command` y sus `args`. Conserva el resto de las opciones y las tablas.
4. Si ya existe `warden-example`, detén la combinación y elige un nombre de
   servidor libre para esta prueba. No dupliques una tabla TOML ni reemplaces
   un servidor existente. Conserva el comando y los argumentos generados.

Abre una sesión nueva de Codex CLI desde esta carpeta para que lea la entrada.
Atiende cualquier aviso del cliente sobre confianza en el proyecto o permisos
de herramientas.

```powershell
codex
```

### Claude Code

```powershell
python warden.py configuration --state .warden-state --profile example --client claude
```

Para probar sólo en este proyecto, respalda `.mcp.json` en la raíz si existe.
Si no existe, créalo con el JSON impreso. Si existe, agrega únicamente la
propiedad `warden-example` al objeto `mcpServers`; conserva los otros
servidores y las comas válidas del JSON. Si esa clave existe, elige un nombre
de servidor libre.

Inicia Claude Code desde esta carpeta y revisa la aprobación del servidor
del proyecto. Su [documentación oficial de MCP](https://code.claude.com/docs/en/mcp#mcp-installation-scopes)
describe `.mcp.json`; `/mcp` muestra el estado del servidor.

```powershell
claude
```

Las rutas generadas corresponden a tu computadora. Conserva la configuración
de prueba en privado en lugar de subirla al repositorio para compartirla.

## 5. Haz una llamada real a echo

Genera un valor nuevo en PowerShell:

```powershell
$probe = 'warden-' + [guid]::NewGuid().ToString('N')
$probe
```

Pega el valor impreso en este mensaje para tu cliente. Si cambiaste el nombre
de la entrada del servidor, usa ese nombre en lugar de `warden-example`:

> Usa la herramienta `echo` del servidor MCP `warden-example` con `value`
> igual a `PEGA-TU-VALOR-NUEVO`. Devuelve el resultado real de la herramienta,
> incluidos `pid` y `value`. No lo sustituyas por un comando de shell ni por
> un resultado inventado.

Revisa la **llamada completada y su resultado**, además del resumen del
modelo. La prueba funciona si `pid` es un entero positivo y `value` coincide
exactamente con tu cadena nueva. Guarda ambos para tu prueba. El PID concreto
varía.

Con esa sesión del cliente abierta, ejecuta esto en otra terminal PowerShell
desde la misma carpeta:

```powershell
python warden.py status --state .warden-state --transport stdio --json
```

Muestra las identidades validadas de los proxies activos, `sessionCount` y
muestras. El muestreo se actualiza aproximadamente cada dos segundos. Cero
sesiones puede ser normal antes de conectar el cliente o después de cerrarlo.
`"status": "ready"` por sí solo no acredita una llamada MCP exitosa. Las
muestras de memoria cubren únicamente el proxy y el servidor directo.

## 6. Opcional: observa el cierre por inactividad y la reactivación

Si elegiste la prueba de 15 segundos, conserva la misma conexión del cliente
abierta y deja de enviar llamadas. Espera el plazo de inactividad y un
intervalo de muestreo. Usa el comando de estado anterior: mientras el proxy
sigue conectado, `backendPid` puede pasar a `null` cuando el servidor se cierra.

Genera **otro** valor nuevo y pide echo otra vez. Revisa el valor real
devuelto y el PID positivo; consulta el estado de nuevo. Este perfil
sintético stateless puede crear otro servidor sin repetir una llamada
anterior. El tráfico del cliente puede aplazar el vencimiento; si el cliente
se desconecta o reconecta, eso no demuestra reactivación en la misma conexión.
Registra lo que observes.

Si Warden pide **reconectar el cliente**, respeta ese límite. En Claude Code,
selecciona Reconnect para este servidor en `/mcp`. En Codex, abre una sesión
CLI de prueba nueva si no hay un control para reconectar sólo ese servidor.
Reconecta únicamente la conexión o sesión de prueba afectada; no hace falta
reiniciar Windows ni cambiar credenciales.

## 7. Deshaz la prueba

Primero cierra la sesión de prueba o desconecta su conexión con Warden.

- **Codex:** quita únicamente la tabla `[mcp_servers."warden-example"]` que
  agregaste, con su `command` y sus `args`. Si elegiste otro nombre, quita
  esa tabla exacta.
- **Claude:** quita únicamente la propiedad `warden-example` que agregaste
  dentro de `mcpServers` y ajusta la coma contigua si hace falta. Usa el
  nombre elegido si lo cambiaste.

Conserva las otras entradas, opciones y credenciales. Compara con el respaldo;
no restaures todo el archivo encima de cambios posteriores. Puedes dejar una
configuración de prueba vacía. Si hace falta, abre de nuevo el cliente para
que lea el cambio. Comprueba que `status` de stdio indique cero sesiones
activas después de cerrar la conexión; los diagnósticos conservados no son
procesos vivos. Puedes conservar el repositorio y el estado para revisarlos.

## Problemas comunes

| Síntoma | Qué revisar |
| --- | --- |
| Falta `python` o indica otra versión | Usa el ejecutable instalado de Python 3.12+ en esta terminal y vuelve a comprobar `sys.executable`. |
| No encuentra `warden.py` | Cambia a la carpeta extraída o clonada que contiene el archivo. Extrae todo el ZIP, incluidos `runtime` y `examples`. |
| `init requires a new or empty state directory` | Reutiliza el estado ya inicializado o elige una carpeta vacía nueva y usa esa ruta en todos los comandos posteriores. No borres el estado existente para ocultar el error. |
| `doctor` no está listo | Revisa qué comprobación dio false: Windows, Python, archivos completos o ruta de estado. Incluye `--transport stdio` para no exigir dependencias del gateway. |
| No aparecen las herramientas | Comprueba el archivo que lee tu cliente, las rutas y la aprobación del proyecto o servidor. Abre una conexión o sesión de prueba nueva. |
| Claude muestra aprobación pendiente | Abre Claude Code de forma interactiva en este proyecto y revisa el servidor en `/mcp`. |
| `status` indica cero sesiones | Conserva abierta la sesión después de una llamada real a echo; usa la misma ruta de estado y `--transport stdio` en ambas terminales. |
| El servidor no se cierra de inmediato | Revisa el plazo y espera el muestreo. La inicialización, llamadas activas, callbacks u otro tráfico pueden mantenerlo ocupado. |
| Warden pide reconectar | Reconecta la conexión afectada. No reenvíes automáticamente una llamada anterior completada o de resultado incierto. |

Para pedir ayuda, usa la [lista del piloto](pilot.md). Comparte versiones,
transporte, paso que falla y error sin datos sensibles. No publiques la
carpeta de estado, el token, credenciales ni stderr privado sin revisar.

## Referencia

Se conservan abajo las opciones avanzadas y sus límites. Empieza con un
servidor de prueba dedicado antes de conectar uno importante para tu trabajo.

### Política de reinicio del modo ligero

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

### Gateway HTTP opcional

Usa una carpeta de estado distinta de la prueba anterior. El modo HTTP/SSE
opcional agrega la política de ciclo de vida alrededor de
[Supergateway](https://github.com/supercorp-ai/supergateway).

Requisitos: Windows, Python 3.12 y Node.js 22 o posterior en `PATH`. Usa una terminal normal, sin permisos de administrador.

Desde una copia del repositorio:

```powershell
npm ci --ignore-scripts
python warden.py init --state state/gateway
python warden.py doctor --state state/gateway
python warden.py serve --state state/gateway
```

Deja esa terminal abierta. En otra terminal:

```powershell
python warden.py status --state state/gateway
python warden.py configuration --state state/gateway --profile example --client codex
# O genera la entrada JSON para Claude:
python warden.py configuration --state state/gateway --profile example --client claude
```

`init` crea una carpeta local con el token, la configuración del gateway y un servidor de ejemplo. `configuration` imprime fragmentos de configuración: revisa el que corresponda y agrégalo a tu cliente. Ese comando no modifica la configuración existente. `serve` se ejecuta en primer plano; Ctrl+C en esa terminal cierra sus sesiones.

La entrada stdio generada ejecuta:

```powershell
python warden.py connect --state state/gateway --profile example
```

`connect` es un transporte MCP: reserva stdout para el protocolo. Configura tu cliente para ejecutarlo en lugar de usarlo de forma interactiva.

### Conectar un servidor propio

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

En el modo gateway, `python warden.py status --state state/gateway --json` incluye sesiones,
solicitudes pendientes, callbacks y muestras por perfil de memoria de trabajo
en bytes y segundos acumulados de CPU. Las muestras cubren el puente y su
servidor directo, **no todo el árbol de procesos hijos**, el gateway ni las
aplicaciones cliente. Una muestra ausente o antigua no significa consumo cero.
Estas métricas no miden cuotas de suscripción ni consumo de tokens.

Usa `process` cuando el servidor inicie servicios compartidos intencionalmente. Usa `tree` para hijos privados. Reiniciar una sesión pierde la memoria del servidor, sus cursores y selecciones; el propio servidor determina qué pasa con los archivos persistentes.

### Qué protege

- La inicialización, el cuerpo de un POST que sigue llegando y su respuesta pendiente.
- Las solicitudes en ambas direcciones, incluidos los callbacks del servidor al cliente.
- La actividad del servidor que renueva el plazo de inactividad.
- La identidad del propietario mediante PID y hora de creación, para rechazar PID reutilizados.

Un stream pasivo no mantiene viva indefinidamente una sesión gateway sin trabajo. Cancelar no demuestra que el servidor haya terminado. Cuando una sesión gateway vencida devuelve HTTP 404, `connect` pide reconectar y cierra la conexión. El reinicio stateless ligero sigue las condiciones anteriores. La compatibilidad probada se registra en [VALIDATION.md](../VALIDATION.md).

Los clientes stdio modernos pueden enviar `server/discover` antes de inicializar.
Warden responde con un error JSON-RPC asociado al ID correcto y conserva la
conexión para la negociación anterior con `initialize`. Es compatibilidad con
esa consulta previa; no implementa toda la revisión de protocolo 2026-07-28.

### Datos locales y alcance

El gateway escucha en `127.0.0.1`, exige un token local y valida Host y Origin. Guarda la carpeta de estado bajo tu cuenta y fuera de Git. Contiene configuración, token, manifiestos de sesiones y diagnósticos del servidor. Los mensajes de stderr del servidor pueden contener información privada, aunque los eventos del gateway eviten registrar el contenido de las llamadas.

Está pensado para clientes y servidores locales de confianza. Los servidores conservan su propio acceso a archivos, red y credenciales. MCP Warden no termina aplicaciones ajenas por nombre, instala un servicio de Windows ni cambia silenciosamente la configuración global del cliente.

Los manifiestos de ejecución y los diagnósticos se conservan localmente después
de cerrar las sesiones. Esta versión alfa no depura automáticamente ese historial
ni impone cuotas de espacio en disco.
