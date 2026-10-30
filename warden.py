"""MCP Warden: an explicit, local lifecycle supervisor for MCP backends.

The CLI does not edit agent configuration, install services, or inspect agent logs.
Only the foreground ``serve`` and per-client ``connect`` commands start processes.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import http.client
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
VERSION = "0.1.0-alpha.1"


class WardenError(Exception):
    """A fixed diagnostic safe to display without configuration or credentials."""


def state_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def initialize(state: Path, port: int = 18741, idle_seconds: float = 900) -> dict:
    if type(port) is not int or not 1024 <= port <= 65535:
        raise WardenError("port must be between 1024 and 65535")
    if not isinstance(idle_seconds, (float, int)) or not 0 <= idle_seconds <= 86400:
        raise WardenError("idle seconds must be between 0 and 86400")
    state = state_path(state)
    if state.exists() and (not state.is_dir() or any(state.iterdir())):
        raise WardenError("init requires a new or empty state directory")
    state.mkdir(parents=True, mode=0o700, exist_ok=True)
    (state / "run").mkdir(mode=0o700)
    token = secrets.token_urlsafe(48)
    descriptor = os.open(state / "token", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as output:
        output.write(token + "\n")
    fixture = ROOT / "examples" / "echo_server.py"
    manifest = {
        "command": sys.executable,
        "args": ["-I", "-B", "-u", str(fixture)],
        "env": {},
        "fingerprint_files": [str(fixture)],
        "cleanup_scope": "tree",
        "shutdown_grace_seconds": 0.5,
    }
    config = {
        "port": port,
        "token_file": str(state / "token"),
        "run_dir": str(state / "run"),
        "bridge_python": sys.executable,
        "owned_bridge": str(ROOT / "runtime" / "owned_bridge.py"),
        "bridge": str(ROOT / "runtime" / "lazy_stdio.py"),
        "handshake_timeout_seconds": 120,
        "idle_session_timeout_seconds": idle_seconds,
        "profiles": {"example": {"manifest": manifest, "shutdown_grace_seconds": 0.5}},
    }
    with (state / "config.json").open("x", encoding="utf-8") as output:
        json.dump(config, output, indent=2)
        output.write("\n")
    return config


def load_config(state: Path) -> dict:
    try:
        raw = (state / "config.json").read_bytes()
        if len(raw) > 1024 * 1024:
            raise ValueError()
        config = json.loads(raw.decode("utf-8-sig"))
        port = config["port"]
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError()
        if not isinstance(config["profiles"], dict):
            raise ValueError()
        if Path(config["token_file"]).resolve() != state / "token":
            raise ValueError()
        return config
    except (OSError, ValueError, KeyError, TypeError):
        raise WardenError("invalid or missing state configuration") from None


def read_token(state: Path) -> str:
    try:
        with (state / "token").open("r", encoding="ascii") as source:
            token = source.read(4097).strip()
        if not 32 <= len(token) <= 4096 or any(char.isspace() for char in token):
            raise ValueError()
        return token
    except (OSError, ValueError, UnicodeError):
        raise WardenError("invalid or missing local token") from None


def get_status(state: Path) -> dict:
    config = load_config(state)
    token = read_token(state)
    connection = http.client.HTTPConnection("127.0.0.1", config["port"], timeout=3)
    try:
        connection.request("GET", "/_health", headers={"Authorization": "Bearer " + token})
        response = connection.getresponse()
        raw = response.read(65537)
        if response.status != 200 or len(raw) > 65536:
            raise WardenError("gateway did not return an authenticated health response")
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("status") != "ready":
            raise ValueError()
        return data
    except (OSError, http.client.HTTPException):
        raise WardenError("gateway is not available; run serve first") from None
    except (ValueError, UnicodeError):
        raise WardenError("gateway returned an invalid health response") from None
    finally:
        connection.close()


def node_executable() -> str:
    result = shutil.which("node")
    if not result:
        raise WardenError("Node.js is not on PATH")
    return result


def require_windows() -> None:
    if os.name != "nt":
        raise WardenError("serve and connect require Windows in this alpha")


def own_creation_time() -> int:
    require_windows()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    values = [wintypes.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(kernel.GetCurrentProcess(), *(ctypes.byref(v) for v in values)):
        raise WardenError("could not establish connector process identity")
    return values[0].dwLowDateTime | values[0].dwHighDateTime << 32


def serve(state: Path) -> int:
    require_windows()
    load_config(state)
    read_token(state)
    process = subprocess.Popen(
        [node_executable(), str(ROOT / "runtime" / "gateway.mjs"),
         "--config", str(state / "config.json"), "--stdin-control"],
        stdin=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
        shell=False,
    )
    try:
        return process.wait()
    except KeyboardInterrupt:
        # EOF requests cooperative shutdown. Do not send process-name kills or
        # enumerate unrelated processes. The gateway owns backend cleanup.
        return stop_owned_process(process)
    finally:
        if process.stdin and not process.stdin.closed:
            process.stdin.close()


def stop_owned_process(process: subprocess.Popen) -> int:
    if process.stdin and not process.stdin.closed:
        process.stdin.close()
    try:
        return process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.terminate()
        return process.wait(timeout=5)


def connect(state: Path, profile: str) -> int:
    require_windows()
    config = load_config(state)
    if profile not in config["profiles"]:
        raise WardenError("unknown profile")
    command = [node_executable(), str(ROOT / "runtime" / "connect.mjs"),
               "--state", str(state), "--profile", profile,
               "--owner-pid", str(os.getpid()), "--owner-created", str(own_creation_time())]
    # The backend is owned by the gateway, but this connector Node process is
    # owned by Python. Bind it before it runs so terminating Python cannot leave
    # an idle connector holding the agent's stdio pipes indefinitely.
    module_spec = importlib.util.spec_from_file_location("warden_owned_process", ROOT / "runtime" / "lazy_stdio.py")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    job = module.WindowsJob()
    process = None
    try:
        process = subprocess.Popen(
            command,
            stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
            creationflags=subprocess.CREATE_NO_WINDOW | 0x00000004,  # CREATE_SUSPENDED
            shell=False,
        )
        try:
            job.attach_and_resume(process)
        except BaseException:
            # Assignment failure must not leave an unowned suspended child.
            process.terminate()
            process.wait(timeout=5)
            raise
        try:
            return process.wait()
        except KeyboardInterrupt:
            process.terminate()
            return process.wait(timeout=5)
    finally:
        # The unnamed Job handle is non-inheritable. Windows also closes it if
        # this Python process is forcibly terminated by its MCP client.
        job.close()


def configuration(state: Path, profile: str, client: str) -> str:
    config = load_config(state)
    if profile not in config["profiles"] or re.fullmatch(r"[A-Za-z0-9_-]+", profile) is None:
        raise WardenError("unknown or unsupported profile name")
    command = sys.executable
    args = ["-I", "-B", "-u", str(ROOT / "warden.py"), "connect",
            "--state", str(state), "--profile", profile]
    if client == "claude":
        return json.dumps({"mcpServers": {"warden-" + profile: {"command": command, "args": args}}}, indent=2)
    return "\n".join([
        '[mcp_servers.' + json.dumps("warden-" + profile) + ']',
        "command = " + json.dumps(command),
        "args = " + json.dumps(args),
    ])


def doctor(state: Path | None) -> dict:
    node = shutil.which("node")
    node_supported = False
    if node:
        try:
            result = subprocess.run(
                [node, "--version"], capture_output=True, text=True, timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                check=False,
            )
            match = re.fullmatch(r"v([0-9]+)\.[0-9]+\.[0-9]+\s*", result.stdout)
            node_supported = result.returncode == 0 and match is not None and int(match[1]) >= 22
        except (OSError, subprocess.SubprocessError):
            pass
    checks = {
        "windows_runtime": os.name == "nt",
        "python_311_or_newer": sys.version_info >= (3, 11),
        "node_22_or_newer": node_supported,
        "gateway_source": (ROOT / "runtime" / "gateway.mjs").is_file(),
        "connector_source": (ROOT / "runtime" / "connect.mjs").is_file(),
        "dependencies_installed": (ROOT / "node_modules" / "supergateway" / "package.json").is_file(),
        "sdk_installed": (ROOT / "node_modules" / "@modelcontextprotocol" / "sdk" / "package.json").is_file(),
    }
    if state is not None:
        try:
            load_config(state)
            read_token(state)
            checks["state_valid"] = True
        except WardenError:
            checks["state_valid"] = False
    return {"version": VERSION, "ready": all(checks.values()), "checks": checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local MCP lifecycle management for Windows (alpha).")
    parser.add_argument("--version", action="version", version=VERSION)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "serve", "status", "connect", "configuration", "doctor"):
        sub = commands.add_parser(name)
        sub.add_argument("--state", required=name != "doctor", type=state_path)
        if name == "init":
            sub.add_argument("--port", type=int, default=18741)
            sub.add_argument("--idle-seconds", type=float, default=900)
        if name in {"connect", "configuration"}:
            sub.add_argument("--profile", required=True)
        if name == "configuration":
            sub.add_argument("--client", choices=("codex", "claude"), required=True)
        if name in {"status", "doctor"}:
            sub.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            initialize(args.state, args.port, args.idle_seconds)
            print("State initialized. Review config.json, then run serve with this state directory.")
            return 0
        if args.command == "serve":
            return serve(args.state)
        if args.command == "connect":
            return connect(args.state, args.profile)
        if args.command == "configuration":
            print(configuration(args.state, args.profile, args.client))
            return 0
        data = get_status(args.state) if args.command == "status" else doctor(args.state)
        if args.json:
            print(json.dumps(data, indent=2))
        elif args.command == "doctor":
            for key, passed in data["checks"].items():
                print(("OK   " if passed else "FAIL ") + key)
        else:
            print("Gateway ready; " + str(data.get("sessionCount", 0)) + " active sessions; "
                  + str(data.get("pendingRequestCount", 0)) + " pending requests.")
        return 0 if args.command != "doctor" or data["ready"] else 1
    except WardenError as error:
        print("mcp-warden: " + str(error), file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError):
        print("mcp-warden: operation failed; check local configuration and dependencies", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
