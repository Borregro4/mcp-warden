"""Lazy MCP stdio bridge. Stdlib only; each used connection owns a private backend.

Only initial discovery is cached. Client/backend RPC content is never logged.
See README.md for the manifest contract and cache trust/validity boundaries.
"""
from __future__ import annotations

import argparse
import json
import math
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


class BridgeTransportStop(RuntimeError):
    """Fixed diagnostics, never protocol payloads."""


class BridgeClientClosed(BridgeTransportStop):
    pass


class BridgeEvents:
    """Bounded data plus out-of-band client shutdown/error notification.

    Backend EOF stays ordered behind its data. Client EOF must interrupt a
    blocked output/write even when both data queues are full.
    """
    def __init__(self, stopping):
        self.data = queue.Queue(maxsize=16)
        self.control = queue.Queue(maxsize=8)
        self.stopping = stopping
        self.client_closed = threading.Event()

    def put(self, event):
        source, _ = event
        if source == 'client_eof':
            self.client_closed.set()
        if source == 'client_eof' or source.endswith('_invalid'):
            try:
                self.control.put_nowait(event)
            except queue.Full:
                self.stopping.set()
            return not self.stopping.is_set()
        while not self.stopping.is_set():
            try:
                self.data.put(event, timeout=.05)
                return True
            except queue.Full:
                continue
        return False

    def get(self, timeout=.1):
        try:
            return self.control.get_nowait()
        except queue.Empty:
            return self.data.get(timeout=min(timeout, .05))


def _write_all(stream, raw):
    # Same short-write contract as the bounded lightweight transport.
    view = memoryview(raw)
    while view:
        count = stream.write(view)
        if not isinstance(count, int) or count <= 0 or count > len(view):
            raise OSError('stream_write_failed')
        view = view[count:]
    stream.flush()


def _bounded_seconds(value):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not .1 <= value <= 600):
        raise ValueError('invalid_transport_timeout')
    return float(value)


class BoundedWriter:
    """Stoppable, bounded pipe writes shared by both local transports.

    Use a raw stream: a daemon blocked on a BufferedWriter holds its lock
    during interpreter finalization. The main thread checks EOF and deadlines
    while waiting for room; a blocked OS write never runs on that thread.
    """
    def __init__(self, stream, stopping, on_error, timeout_seconds, capacity=16):
        self.stream = stream
        self.stopping = stopping
        self.on_error = on_error
        self.timeout_seconds = timeout_seconds
        self.queue = queue.Queue(maxsize=capacity)
        self.lock = threading.Lock()
        self.writes = {}
        self.sequence = 0
        self.failed = False
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self.stopping.is_set():
            try:
                ident, raw = self.queue.get(timeout=.05)
            except queue.Empty:
                continue
            if self.stopping.is_set():
                return
            try:
                _write_all(self.stream, raw)
            except (OSError, ValueError):
                self.failed = True
                self.on_error()
                return
            with self.lock:
                self.writes.pop(ident, None)

    def poll(self):
        if self.failed:
            raise BridgeTransportStop('stream_write_failed')
        if self.stopping.is_set():
            raise BridgeTransportStop('transport_stopped')
        with self.lock:
            if self.writes and time.monotonic() - min(self.writes.values()) >= self.timeout_seconds:
                raise BridgeTransportStop('stream_write_timeout')

    def pending(self):
        with self.lock:
            return bool(self.writes)

    def send(self, raw, check=None):
        self.sequence += 1
        ident = self.sequence
        while True:
            if check:
                check()
            self.poll()
            try:
                with self.lock:
                    self.queue.put_nowait((ident, raw))
                    self.writes[ident] = time.monotonic()
                return
            except queue.Full:
                time.sleep(.01)

    def drain(self, check=None):
        while self.pending():
            if check:
                check()
            self.poll()
            time.sleep(.01)


def windows_creation_flags(detached_console=False):
    """Opt out of console attachment only for explicitly qualified backends.

    DETACHED_PROCESS keeps the explicit stdio pipes. It does not remove the
    suspended-before-Job launch or grant a backend independent ownership.
    """
    if type(detached_console) is not bool:
        raise ValueError("Invalid windows_detached_console")
    console_flag = 0x00000008 if detached_console else 0x08000000
    return console_flag | 0x00000004  # CREATE_SUSPENDED; assign Job before resume.


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
    import hashlib
    return hashlib.sha256(encoded(value)).hexdigest()


def file_digest(path):
    import hashlib
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
        self.k.ResumeThread.argtypes = [w.HANDLE]
        self.k.ResumeThread.restype = w.DWORD
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


class _CreateProcessProxy:
    """Keep CPython's launch behavior while owning its exact suspended handles.

    The private-globals interception pattern is adapted from MIT-licensed
    win-safesubprocess; see NOTICE and licenses/win-safesubprocess-MIT.txt.
    No module-global function or native API is patched.
    """
    def __init__(self, native, job):
        self.native, self.job = native, job

    def __getattr__(self, name):
        return getattr(self.native, name)

    def CreateProcess(self, *args, **kwargs):
        # The running CPython implementation supplies flags positionally. Fail
        # before spawning if that private call shape ever changes.
        if len(args) < 6:
            raise RuntimeError('unsupported_process_creation')
        arguments = list(args)
        arguments[5] = int(arguments[5]) | 0x00000004  # CREATE_SUSPENDED
        handles = self.native.CreateProcess(*arguments, **kwargs)
        process, thread, _, _ = handles
        try:
            from ctypes import wintypes
            if not self.job.k.AssignProcessToJobObject(self.job.handle, wintypes.HANDLE(process)):
                raise self.job.c.WinError(self.job.c.get_last_error())
            if self.job.k.ResumeThread(wintypes.HANDLE(thread)) != 1:
                raise RuntimeError('unexpected_suspend_count')
            return handles
        except BaseException:
            # Popen has not received these handles yet. Clean up this exact
            # unpublished child on every setup failure, including interrupts.
            try:
                try:
                    self.native.TerminateProcess(process, 1)
                finally:
                    if self.native.WaitForSingleObject(process, 5000) != 0:
                        raise RuntimeError('unpublished_child_cleanup_failed')
            finally:
                try:
                    self.native.CloseHandle(thread)
                finally:
                    self.native.CloseHandle(process)
            raise


class _OwnedPopen(subprocess.Popen):
    """One launch with a private API proxy; unrelated/concurrent launches stay intact."""
    def __init__(self, *args, job, **kwargs):
        self.warden_job = job
        super().__init__(*args, **kwargs)

    def _execute_child(self, *args, **kwargs):
        from types import FunctionType
        original = subprocess.Popen._execute_child
        if (not isinstance(original, FunctionType) or '_winapi' not in original.__globals__
                or 'CreateProcess' not in original.__code__.co_names):
            raise RuntimeError('unsupported_process_creation')
        namespace = dict(original.__globals__)
        namespace['_winapi'] = _CreateProcessProxy(namespace['_winapi'], self.warden_job)
        execute = FunctionType(original.__code__, namespace, original.__name__,
            original.__defaults__, original.__closure__)
        execute.__kwdefaults__ = original.__kwdefaults__
        return execute(self, *args, **kwargs)


class Bridge:
    def __init__(self, manifest):
        self.spec = manifest
        self.cleanup_scope = manifest.get("cleanup_scope", "tree")
        if self.cleanup_scope not in {"tree", "process"}:
            raise ValueError("Invalid cleanup scope")
        self.windows_detached_console = manifest.get("windows_detached_console", False)
        windows_creation_flags(self.windows_detached_console)  # Strict bool, on every platform.
        self.environment = os.environ.copy()
        self.environment.update({str(k): str(v) for k, v in manifest.get("env", {}).items()})
        self.cwd = manifest.get("cwd") or os.getcwd()
        self.proc = None
        self.job = None
        self.stopping = threading.Event()
        self.events = BridgeEvents(self.stopping)
        limit = manifest.get('max_rpc_line_bytes', 1024 * 1024)
        if type(limit) is not int or not 1024 <= limit <= 8 * 1024 * 1024:
            raise ValueError('invalid_rpc_line_limit')
        self.max_line_bytes = limit
        self.write_seconds = _bounded_seconds(manifest.get('backend_write_timeout_seconds', 30))
        self.partial_seconds = _bounded_seconds(manifest.get('partial_message_timeout_seconds', 30))
        self.io_lock = threading.Lock()
        self.partial = {}
        self.io_writers = {}
        self.io_managed = False
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
                                    "windows_detached_console": self.windows_detached_console,
                                    "env_hash": digest(env_key), "static_lists": sorted(self.static_lists),
                                    "cwd": None if manifest.get("catalog_cwd_independent", False) else self.cwd})
            self.cache_dir = Path(manifest["cache_dir"])
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except (OSError, ValueError, TypeError):
            self.cache_enabled = False
            self.log("Discovery cache unavailable; using direct backend")

    def log(self, message):
        print("lazy-mcp: " + message, file=sys.stderr, flush=True)

    def read_stream(self, stream, destination, source):
        buffered = bytearray()
        try:
            while not self.stopping.is_set():
                chunk = stream.read(min(8192, self.max_line_bytes + 1))
                if not chunk:
                    if buffered:
                        destination.put((source + '_invalid', 'incomplete_message'))
                    break
                buffered.extend(chunk)
                while b'\n' in buffered:
                    end = buffered.index(b'\n') + 1
                    if end > self.max_line_bytes:
                        destination.put((source + '_invalid', 'message_too_large'))
                        return
                    raw = bytes(buffered[:end])
                    del buffered[:end]
                    # A complete frame waiting for queue capacity is
                    # backpressure, not a stalled partial protocol message.
                    with self.io_lock:
                        self.partial.pop(source, None)
                    if not destination.put((source, raw)):
                        return
                if len(buffered) > self.max_line_bytes:
                    destination.put((source + '_invalid', 'message_too_large'))
                    return
                if buffered:
                    with self.io_lock:
                        self.partial.setdefault(source, time.monotonic())
        except (OSError, ValueError):
            destination.put((source + '_invalid', 'stream_unavailable'))
        finally:
            with self.io_lock:
                self.partial.pop(source, None)
            destination.put((source + "_eof", None))

    def output(self, raw):
        if getattr(self, 'io_managed', False):
            self._queue_write('client', raw)
            return
        sys.stdout.buffer.write(raw)
        sys.stdout.buffer.flush()

    def reply(self, request, result):
        self.output(encoded({"jsonrpc": "2.0", "id": request["id"], "result": result}) + b"\n")

    def send(self, raw):
        if self.io_managed:
            self._queue_write('backend', raw)
            return
        self.proc.stdin.write(raw)
        self.proc.stdin.flush()

    def _start_writer(self, name, stream):
        self.io_writers[name] = BoundedWriter(stream, self.stopping,
            lambda: self.events.put((name + '_invalid', 'stream_write_failed')), self.write_seconds)

    def _poll_transport(self):
        if self.events.client_closed.is_set():
            raise BridgeClientClosed('client_closed')
        if self.stopping.is_set():
            raise BridgeTransportStop('transport_stopped')
        for writer in self.io_writers.values():
            writer.poll()
        now = time.monotonic()
        with self.io_lock:
            if self.partial and now - min(self.partial.values()) >= self.partial_seconds:
                raise BridgeTransportStop('partial_message_timeout')

    def _queue_write(self, name, raw):
        if len(raw) > self.max_line_bytes:
            raise BridgeTransportStop('message_too_large')
        writer = self.io_writers.get(name)
        if writer is None:
            raise BridgeTransportStop('stream_unavailable')
        writer.send(raw, self._poll_transport)

    def _drain_output(self):
        self.io_writers['client'].drain(self._poll_transport)

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
                self.proc = _OwnedPopen(args, job=self.job,
                    creationflags=windows_creation_flags(self.windows_detached_console), **kwargs)
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
        if getattr(self, 'io_managed', False):
            self._start_writer('backend', self.proc.stdin)
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
                    if len(self.pending) >= 16:
                        raise BridgeTransportStop('discovery_capacity_reached')
                    self.pending[digest(message["id"])] = (method, self.request_key(message))
            elif method != "ping":
                self.catalog_phase = False
                self.unlock()
        self.send(raw)

    def hold_message(self, raw):
        if len(self.held) >= 16 or sum(map(len, self.held)) + len(raw) > max(4 * 1024 * 1024, self.max_line_bytes):
            raise BridgeTransportStop('pending_capacity_reached')
        self.held.append(raw)

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
            self.hold_message(raw)
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
            self.hold_message(raw)
            self.activate()
            return
        if self.state == "replaying":
            if message and "method" not in message and "id" in message:
                self.send(raw)  # Client answers to backend-initiated requests must not wait on initialize.
            else:
                self.hold_message(raw)
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
                        if key not in self.cache['responses'] and len(self.cache['responses']) >= 16:
                            self.catalog_phase = False
                            self.unlock()
                            self.output(raw)
                            return
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
        if getattr(self, 'io_managed', False):
            self.stopping.set()
        if not self.proc:
            return
        backend_writer = getattr(self, 'io_writers', {}).get('backend')
        blocked_write = backend_writer is not None and backend_writer.pending()
        if blocked_write and self.job:
            self.job.close()  # Unblock an owned write before touching its pipe.
        if blocked_write and self.cleanup_scope == 'process':
            # Shared descendants may retain the pipe. End only the pinned root
            # and leave daemon readers/writers for process exit to release.
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=3)
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
        self.io_managed = True
        self._start_writer('client', getattr(sys.stdout.buffer, 'raw', sys.stdout.buffer))
        client_stream = getattr(sys.stdin.buffer, 'raw', sys.stdin.buffer)
        threading.Thread(target=self.read_stream, args=(client_stream, self.events, "client"), daemon=True).start()
        try:
            while True:
                self._poll_transport()
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
                elif source.endswith('_invalid'):
                    raise BridgeTransportStop(str(raw))
                elif source == "backend_eof":
                    self._drain_output()
                    try:
                        return self.proc.wait(timeout=3) or 0
                    except subprocess.TimeoutExpired:
                        return 1  # finally closes only the owned tree; no command-bearing traceback.
                self.poll_wait()
        except BridgeClientClosed:
            return 0
        except BridgeTransportStop as error:
            self.log(str(error))
            return 2
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
