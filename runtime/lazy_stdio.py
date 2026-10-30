"""Lazy MCP stdio bridge. Stdlib only; each used connection owns a private backend.

Only initial discovery is cached. Client/backend RPC content is never logged.
See README.md for the manifest contract and cache trust/validity boundaries.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid

VERSION = 1
LIST_KEYS = {"tools/list": "tools", "resources/list": "resources",
             "resources/templates/list": "resourceTemplates", "prompts/list": "prompts"}


def redirect_diagnostics(manifest, manifest_path):
    """Release the parent's stderr pipe while preserving diagnostics in one local file.

    Runs before any backend starts. The temporary backup of fd 2 is closed before
    returning, so the parent sees EOF even while this bridge remains alive. Native
    Windows children also need the process standard handle updated, not only fd 2.
    Failure leaves the original stderr usable and does not change MCP behavior.
    """
    logfile_fd = backup_fd = None
    changed = False
    kernel = None
    try:
        directory = Path(manifest.get("stderr_log_dir") or
                         Path(__file__).resolve().parent / "candidate-data" / "stderr")
        if not directory.is_absolute():
            directory = Path(manifest_path).resolve().parent / directory
        directory.mkdir(parents=True, exist_ok=True)
        logfile = directory / ("stderr-" + str(os.getpid()) + "-" + uuid.uuid4().hex + ".log")
        logfile_fd = os.open(logfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                             os.O_APPEND | getattr(os, "O_BINARY", 0), 0o600)
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            import msvcrt
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.SetStdHandle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
            kernel.SetStdHandle.restype = wintypes.BOOL
        sys.stderr.flush()
        backup_fd = os.dup(2)
        os.dup2(logfile_fd, 2)
        changed = True
        if kernel is not None and not kernel.SetStdHandle(-12, msvcrt.get_osfhandle(2)):
            raise ctypes.WinError(ctypes.get_last_error())
        return logfile
    except (OSError, ValueError, TypeError, AttributeError) as error:
        if changed:
            os.dup2(backup_fd, 2)
            if kernel is not None:
                kernel.SetStdHandle(-12, msvcrt.get_osfhandle(2))
        # Never print paths, backend arguments, RPC content, or exception text.
        print("lazy-mcp: diagnostic file unavailable (" + type(error).__name__ +
              "); continuing with original stderr", file=sys.stderr, flush=True)
        return None
    finally:
        for descriptor in (backup_fd, logfile_fd):
            if descriptor is not None:
                os.close(descriptor)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def file_digest(path):
    # Same whole-file SHA-256 semantics, bounded-buffer reads even for large runtimes.
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def parse(raw):
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except (ValueError, UnicodeError):
        return None


class CacheLock:
    """OS-owned one-byte lock; a crash releases ownership without stale PID logic."""
    def __init__(self, path):
        self.file = open(path, "a+b")
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        self.owned = False

    def acquire(self):
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.owned = True
            return True
        except OSError:
            return False

    def close(self):
        if self.file.closed:
            return
        if self.owned:
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        self.file.close()


class WindowsJob:
    """Own a suspended child before it runs; optionally let descendants leave this Job.

    Silent breakaway applies only to this Job. An outer client/CI Job can still
    contain descendants according to its own policy.
    """
    def __init__(self, include_descendants=True):
        import ctypes as c
        from ctypes import wintypes as w
        self.c = c
        self.k = c.WinDLL("kernel32", use_last_error=True)
        class BASIC(c.Structure):
            _fields_ = [("PerProcessUserTimeLimit", c.c_int64), ("PerJobUserTimeLimit", c.c_int64),
                        ("LimitFlags", w.DWORD), ("MinimumWorkingSetSize", c.c_size_t),
                        ("MaximumWorkingSetSize", c.c_size_t), ("ActiveProcessLimit", w.DWORD),
                        ("Affinity", c.c_size_t), ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]
        class IO(c.Structure):
            _fields_ = [(name, c.c_uint64) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        class EXT(c.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO), ("ProcessMemoryLimit", c.c_size_t),
                        ("JobMemoryLimit", c.c_size_t), ("PeakProcessMemoryUsed", c.c_size_t), ("PeakJobMemoryUsed", c.c_size_t)]
        self.k.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
        self.k.CreateJobObjectW.restype = w.HANDLE
        self.k.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
        self.k.SetInformationJobObject.restype = w.BOOL
        self.k.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.k.AssignProcessToJobObject.restype = w.BOOL
        self.k.CloseHandle.argtypes = [w.HANDLE]
        self.handle = self.k.CreateJobObjectW(None, None)
        if not self.handle:
            raise c.WinError(c.get_last_error())
        info = EXT()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not include_descendants:
            info.BasicLimitInformation.LimitFlags |= 0x1000  # JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK
        if not self.k.SetInformationJobObject(self.handle, 9, c.byref(info), c.sizeof(info)):
            self.close()
            raise c.WinError(c.get_last_error())

    def attach_and_resume(self, process):
        c = self.c
        from ctypes import wintypes as w
        if not self.k.AssignProcessToJobObject(self.handle, w.HANDLE(int(process._handle))):
            raise c.WinError(c.get_last_error())
        class THREAD(c.Structure):
            _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ThreadID", w.DWORD),
                        ("th32OwnerProcessID", w.DWORD), ("tpBasePri", w.LONG), ("tpDeltaPri", w.LONG), ("dwFlags", w.DWORD)]
        self.k.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
        self.k.CreateToolhelp32Snapshot.restype = w.HANDLE
        self.k.Thread32First.argtypes = [w.HANDLE, c.POINTER(THREAD)]
        self.k.Thread32Next.argtypes = [w.HANDLE, c.POINTER(THREAD)]
        self.k.OpenThread.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self.k.OpenThread.restype = w.HANDLE
        self.k.ResumeThread.argtypes = [w.HANDLE]
        self.k.ResumeThread.restype = w.DWORD
        snapshot = self.k.CreateToolhelp32Snapshot(4, 0)
        if snapshot == c.c_void_p(-1).value:
            raise c.WinError(c.get_last_error())
        resumed = False
        try:
            entry = THREAD()
            entry.dwSize = c.sizeof(entry)
            found = self.k.Thread32First(snapshot, c.byref(entry))
            while found:
                if entry.th32OwnerProcessID == process.pid:
                    handle = self.k.OpenThread(2, False, entry.th32ThreadID)
                    if not handle:
                        raise c.WinError(c.get_last_error())
                    try:
                        if self.k.ResumeThread(handle) == 0xFFFFFFFF:
                            raise c.WinError(c.get_last_error())
                        resumed = True
                    finally:
                        self.k.CloseHandle(handle)
                found = self.k.Thread32Next(snapshot, c.byref(entry))
            if not resumed:
                raise RuntimeError("No suspended backend thread found")
        finally:
            self.k.CloseHandle(snapshot)

    def close(self):
        if self.handle:
            self.k.CloseHandle(self.handle)
            self.handle = None


class Bridge:
    def __init__(self, manifest):
        self.spec = manifest
        self.cleanup_scope = manifest.get("cleanup_scope", "tree")
        if self.cleanup_scope not in {"tree", "process"}:
            raise ValueError("Invalid cleanup scope")
        self.environment = os.environ.copy()
        self.environment.update({str(k): str(v) for k, v in manifest.get("env", {}).items()})
        self.cwd = manifest.get("cwd") or os.getcwd()
        self.proc = None
        self.job = None
        self.events = queue.Queue()
        self.state = "new"
        self.init = None
        self.initialized = None
        self.cache = None
        self.cache_path = None
        self.cache_key = None
        self.cache_lock = None
        self.cache_enabled = True
        self.catalog_phase = True
        self.pending = {}
        self.held = []
        self.internal_id = "lazy-init-" + uuid.uuid4().hex
        self.wait_until = 0
        self.cache_ttl = float(manifest.get("cache_ttl_seconds", 86400))
        self.static_lists = set(manifest.get("static_discovery_methods", []))
        try:
            files = {str(Path(p).resolve()): file_digest(p)
                     for p in manifest.get("fingerprint_files", [])}
            files[str(Path(__file__).resolve())] = file_digest(__file__)
            names = manifest.get("fingerprint_env")
            env_key = self.environment if names is None else {
                name: self.environment.get(name) for name in set(names) | set(manifest.get("env", {}))}
            self.base_key = digest({"version": VERSION, "command": manifest["command"],
                                    "args": manifest.get("args", []), "files": files,
                                    "env_hash": digest(env_key), "static_lists": sorted(self.static_lists),
                                    "cwd": None if manifest.get("catalog_cwd_independent", False) else self.cwd})
            self.cache_dir = Path(manifest["cache_dir"])
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except (OSError, ValueError, TypeError):
            self.cache_enabled = False
            self.log("Discovery cache unavailable; using direct backend")

    def log(self, message):
        print("lazy-mcp: " + message, file=sys.stderr, flush=True)

    @staticmethod
    def read_stream(stream, destination, source):
        try:
            for raw in iter(stream.readline, b""):
                destination.put((source, raw))
        finally:
            destination.put((source + "_eof", None))

    def output(self, raw):
        sys.stdout.buffer.write(raw)
        sys.stdout.buffer.flush()

    def reply(self, request, result):
        self.output(encoded({"jsonrpc": "2.0", "id": request["id"], "result": result}) + b"\n")

    def send(self, raw):
        self.proc.stdin.write(raw)
        self.proc.stdin.flush()

    def start(self):
        args = [self.spec["command"], *self.spec.get("args", [])]
        kwargs = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr.buffer,
                      cwd=self.cwd, env=self.environment, bufsize=0)
        if os.name == "nt":
            # Fail closed only for unsafe launch mechanics; never resume an unowned suspended child.
            # Process scope retains crash cleanup for the root, while shared daemons
            # automatically leave this Job when they are created.
            self.job = WindowsJob(include_descendants=self.cleanup_scope == "tree")
            try:
                self.proc = subprocess.Popen(args, creationflags=0x08000000 | 0x00000004, **kwargs)
                self.job.attach_and_resume(self.proc)
            except BaseException:
                try:
                    if self.proc is not None:
                        self.proc.kill()
                        self.proc.wait(timeout=5)
                finally:
                    self.job.close()
                raise
        else:
            self.proc = subprocess.Popen(args, start_new_session=self.cleanup_scope == "tree", **kwargs)
        threading.Thread(target=self.read_stream, args=(self.proc.stdout, self.events, "backend"), daemon=True).start()

    def request_key(self, message):
        return digest({"method": message["method"], "params": message.get("params")})

    def valid_result(self, method, result):
        if not isinstance(result, dict):
            return False
        if method == "initialize":
            return (isinstance(result.get("protocolVersion"), str) and isinstance(result.get("capabilities"), dict)
                    and isinstance(result.get("serverInfo"), dict))
        key = LIST_KEYS.get(method)
        # Pagination cursors may belong to one backend instance; never replay them into another.
        if not key or "nextCursor" in result or not isinstance(result.get(key), list):
            return False
        if method == "tools/list":
            return all(isinstance(tool, dict) and isinstance(tool.get("name"), str)
                       and isinstance(tool.get("inputSchema"), dict) for tool in result[key])
        return method in self.static_lists or (not result[key] and not result.get("nextCursor"))

    def load_cache(self):
        try:
            document = json.loads(self.cache_path.read_text(encoding="utf-8"))
            age = time.time() - document["created_at"]
            if (document.get("invalidated") or document["version"] != VERSION or document["key"] != self.cache_key or
                    not 0 <= age <= self.cache_ttl or not self.valid_result("initialize", document["initialize"])):
                return None
            responses = document["responses"]
            if not isinstance(responses, dict):
                return None
            if not any(v.get("method") == "tools/list" for v in responses.values()):
                return None  # Do not release stampede waiters on an incomplete warm-up.
            for entry in responses.values():
                if not self.valid_result(entry["method"], entry["result"]):
                    return None
            return document
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None

    def save_cache(self):
        if not self.cache_enabled or not self.cache or "initialize" not in self.cache:
            return
        try:
            temporary = self.cache_path.with_name(self.cache_path.name + "." + uuid.uuid4().hex + ".tmp")
            temporary.write_bytes(encoded(self.cache))
            os.replace(temporary, self.cache_path)
        except OSError:
            self.log("Could not write discovery cache; backend remains available")

    def unlock(self):
        if self.cache_lock:
            self.cache_lock.close()
            self.cache_lock = None

    def prepare_cache(self, init):
        if not self.cache_enabled or self.cache_ttl <= 0:
            return False
        self.cache_key = digest({"base": self.base_key, "initialize_params_hash": digest(init.get("params"))})
        self.cache_path = self.cache_dir / (self.cache_key + ".json")
        self.cache = self.load_cache()
        if self.cache:
            return True
        try:
            self.cache_lock = CacheLock(self.cache_path.with_suffix(".lock"))
            if not self.cache_lock.acquire():
                self.state = "waiting"
                self.wait_until = time.monotonic() + float(self.spec.get("cache_wait_seconds", 15))
                return False
        except OSError:
            self.unlock()
        self.new_cache()
        return False

    def new_cache(self):
        self.cache = {"version": VERSION, "key": self.cache_key, "created_at": time.time(), "responses": {}}

    def activate(self):
        self.catalog_phase = False
        self.start()
        self.state = "replaying"
        replay = dict(self.init)
        replay["id"] = self.internal_id
        self.send(encoded(replay) + b"\n")

    def forward(self, raw, message):
        if (self.catalog_phase and message and "method" in message and "id" not in message
                and message["method"] != "notifications/initialized"):
            self.catalog_phase = False
            self.unlock()
        if self.catalog_phase and message and "id" in message and "method" in message:
            method = message["method"]
            if method == "initialize" or method in LIST_KEYS:
                if isinstance(message.get("params"), dict) and "cursor" in message["params"]:
                    self.catalog_phase = False
                    self.unlock()
                else:
                    self.pending[digest(message["id"])] = (method, self.request_key(message))
            elif method != "ping":
                self.catalog_phase = False
                self.unlock()
        self.send(raw)

    def accept_client(self, raw):
        message = parse(raw)
        method = message.get("method") if message else None
        if self.state == "new":
            if method == "initialize" and "id" in message:
                self.init = message
                if self.prepare_cache(message):
                    self.state = "deferred"
                    self.reply(message, self.cache["initialize"])
                    return
                if self.state == "waiting":
                    return
            self.start()
            self.state = "direct"
            self.forward(raw, message)
            return
        if self.state == "waiting":
            self.held.append(raw)
            return
        if self.state == "deferred":
            if method == "notifications/initialized" and "id" not in message:
                self.initialized = raw
                return
            if method == "ping" and "id" in message:
                self.reply(message, {})
                return
            if message and "id" in message and method in LIST_KEYS:
                cached = self.cache["responses"].get(self.request_key(message))
                if cached:
                    self.reply(message, cached["result"])
                    return
            self.held.append(raw)
            self.activate()
            return
        if self.state == "replaying":
            if message and "method" not in message and "id" in message:
                self.send(raw)  # Client answers to backend-initiated requests must not wait on initialize.
            else:
                self.held.append(raw)
            return
        self.forward(raw, message)

    def accept_backend(self, raw):
        message = parse(raw)
        if (self.catalog_phase and message and message.get("method") in
                {"notifications/tools/list_changed", "notifications/resources/list_changed", "notifications/prompts/list_changed"}):
            # A spontaneously changing discovery snapshot is not safe to reuse.
            # Changes after actual session use are forwarded, but never rewrite the initial cache.
            if self.cache is not None:
                self.cache["invalidated"] = True
                self.save_cache()
            self.catalog_phase = False
            self.unlock()
        if self.state == "replaying" and message and message.get("id") == self.internal_id and "method" not in message:
            if "error" in message:
                for pending in self.held:
                    request = parse(pending)
                    if request and "id" in request and "method" in request:
                        self.output(encoded({"jsonrpc": "2.0", "id": request["id"], "error": message["error"]}) + b"\n")
                raise RuntimeError("Backend rejected deferred initialization")
            self.state = "active"
            if self.initialized:
                self.send(self.initialized)
            held, self.held = self.held, []
            for pending in held:
                self.send(pending)
            return
        if message and "id" in message and "method" not in message:
            entry = self.pending.pop(digest(message["id"]), None)
            if entry and self.catalog_phase and self.cache_enabled and self.cache is not None:
                method, key = entry
                result = message.get("result")
                if isinstance(result, dict) and "nextCursor" in result:
                    self.catalog_phase = False
                    self.unlock()
                if self.valid_result(method, result):
                    if method == "initialize":
                        self.cache["initialize"] = result
                    else:
                        self.cache["responses"][key] = {"method": method, "result": result}
                    self.save_cache()
                    if method == "tools/list":
                        self.unlock()
        self.output(raw)

    def poll_wait(self):
        if self.state != "waiting":
            return
        document = self.load_cache()
        if document:
            self.cache = document
            self.unlock()
            self.state = "deferred"
            self.reply(self.init, document["initialize"])
        elif time.monotonic() >= self.wait_until or self.cache_lock.acquire():
            self.new_cache()
            self.start()
            self.state = "direct"
            self.forward(encoded(self.init) + b"\n", self.init)
        else:
            return
        held, self.held = self.held, []
        for raw in held:
            self.accept_client(raw)

    def shutdown(self):
        self.unlock()
        if not self.proc:
            return
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=float(self.spec.get("shutdown_grace_seconds", 6)))
        except subprocess.TimeoutExpired:
            pass
        finally:
            if self.job:
                self.job.close()  # Descendant ownership follows the selected cleanup scope.
            elif os.name != "nt" and self.cleanup_scope == "tree":
                import signal
                try:
                    os.killpg(self.proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            if self.proc.poll() is None:
                if self.cleanup_scope == "process":
                    self.proc.terminate()  # Root process only: never taskkill /T, killpg, or child enumeration.
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=3)

    def run(self):
        threading.Thread(target=self.read_stream, args=(sys.stdin.buffer, self.events, "client"), daemon=True).start()
        try:
            while True:
                try:
                    source, raw = self.events.get(timeout=0.1)
                except queue.Empty:
                    self.poll_wait()
                    continue
                if source == "client":
                    self.accept_client(raw)
                elif source == "backend":
                    self.accept_backend(raw)
                elif source == "client_eof":
                    return 0
                elif source == "backend_eof":
                    try:
                        return self.proc.wait(timeout=3) or 0
                    except subprocess.TimeoutExpired:
                        return 1  # finally closes only the owned tree; no command-bearing traceback.
                self.poll_wait()
        finally:
            self.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    try:
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8-sig"))
        if not isinstance(manifest.get("command"), str) or not isinstance(manifest.get("args", []), list):
            raise ValueError("Invalid command manifest")
        redirect_diagnostics(manifest, args.manifest)
        return Bridge(manifest).run()
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        # Do not include exception text: child arguments, RPC content, or credentials may be embedded.
        print("lazy-mcp: launch/transport failed (" + type(error).__name__ + "); original backend files unchanged", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
