"""Bounded, lightweight stdio lifecycle policy around the existing Bridge.

No daemon, Node process, HTTP listener or persisted discovery cache is used.
Only a clean idle expiry may restart a backend, and only when its profile
explicitly declares restart_policy="stateless". No completed or uncertain tool
request is replayed. WindowsJob, launch, forwarding and handshake replay come
from this repository's lazy_stdio.py.
"""
from __future__ import annotations

import argparse
import importlib.util
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

_spec = importlib.util.spec_from_file_location(
    'warden_existing_bridge', Path(__file__).resolve().with_name('lazy_stdio.py'))
_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)
Bridge = _base.Bridge
encoded = _base.encoded
parse = _base.parse
digest = _base.digest
redirect_diagnostics = _base.redirect_diagnostics


class ProtocolStop(Exception):
    """Fixed diagnostics only; request contents never enter logs."""


class TaggedQueue:
    """A per-reader generation label plus bounded, stoppable backpressure."""
    def __init__(self, destination, generation, stopping):
        self.destination = destination
        self.generation = generation
        self.stopping = stopping

    def put(self, event):
        source, raw = event
        while not self.stopping.is_set():
            try:
                self.destination.put((source, self.generation, raw), timeout=.1)
                return True
            except queue.Full:
                continue
        return False


def _number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('invalid_configuration')
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError('invalid_configuration')
    return float(value)


def _rpc_key(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ProtocolStop('invalid_request_id')
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ProtocolStop('invalid_request_id')
        value = int(value)
    return digest(value)  # Numeric 1 equals 1.0, but the string "1" remains distinct.


def _write_all(stream, raw):
    view = memoryview(raw)
    while view:
        count = stream.write(view)
        if not isinstance(count, int) or count <= 0 or count > len(view):
            raise OSError('backend_write_failed')
        view = view[count:]
    stream.flush()


class IdleBridge(Bridge):
    def __init__(self, manifest):
        if not isinstance(manifest, dict) or not isinstance(manifest.get('command'), str):
            raise ValueError('invalid_configuration')
        if not manifest['command'] or '\0' in manifest['command']:
            raise ValueError('invalid_configuration')
        if not isinstance(manifest.get('args', []), list) or any(
                not isinstance(arg, str) or '\0' in arg for arg in manifest.get('args', [])):
            raise ValueError('invalid_configuration')
        environment = manifest.get('env', {})
        if not isinstance(environment, dict) or any(
                not isinstance(k, str) or not k or '\0' in k or '=' in k
                or not isinstance(v, str) or '\0' in v for k, v in environment.items()):
            raise ValueError('invalid_configuration')
        self.spec = dict(manifest)
        self.cleanup_scope = manifest.get('cleanup_scope', 'tree')
        if self.cleanup_scope not in {'tree', 'process'}:
            raise ValueError('invalid_configuration')
        self.policy = manifest.get('restart_policy', 'manual')
        if self.policy not in {'manual', 'stateless'}:
            raise ValueError('invalid_configuration')
        self.idle_seconds = _number(manifest.get('idle_session_timeout_seconds', 900), 0, 86400)
        self.handshake_seconds = _number(manifest.get('handshake_timeout_seconds', 30), .1, 600)
        self.partial_seconds = _number(manifest.get('partial_message_timeout_seconds', 30), .1, 600)
        self.write_seconds = _number(manifest.get('backend_write_timeout_seconds', 30), .1, 600)
        self.spec['shutdown_grace_seconds'] = _number(manifest.get('shutdown_grace_seconds', .5), 0, 5)
        limit = manifest.get('max_rpc_line_bytes', 1024 * 1024)
        if type(limit) is not int or not 1024 <= limit <= 1024 * 1024:
            raise ValueError('invalid_configuration')
        self.max_line_bytes = limit
        self.environment = os.environ.copy()
        self.environment.update(environment)
        self.cwd = manifest.get('cwd') or os.getcwd()
        if not isinstance(self.cwd, str) or not Path(self.cwd).is_dir():
            raise ValueError('invalid_configuration')

        # These are the existing Bridge's protocol fields. Discovery caching is
        # deliberately disabled: there is no disk snapshot or catalog replay.
        self.proc = self.job = None
        self.events = queue.Queue(maxsize=16)
        self._events = self.events
        self.stopping = threading.Event()
        self.partial_lock = threading.Lock()
        self.partial = {}
        self.generation = 0
        self.state = 'new'
        self.init = self.initialized = self.first_result = None
        self.cache = self.cache_path = self.cache_key = self.cache_lock = None
        self.cache_enabled = self.catalog_phase = False
        self.pending = {}
        self.held = []
        self.internal_id = 'warden-init-' + uuid.uuid4().hex
        self.client_pending = {}
        self.server_pending = {}
        self.discovery_requests = {}
        self.tool_snapshot = None
        self.discovery_paginated = False
        self.replay_tools_id = None
        self.replay_initialized = False
        self.last_activity = time.monotonic()
        self.started_at = None
        self.ready = False
        self.replay_tools_id = None
        self.replay_initialized = False
        self.uncertain = False
        self.disconnect = False
        self.write_queue = self.writer_stop = None
        self.writes = {}
        self.write_sequence = 0

    def read_stream(self, stream, destination, source):
        key = (source, destination.generation)
        buffered = bytearray()
        try:
            while not self.stopping.is_set():
                chunk = stream.read(min(8192, self.max_line_bytes + 1))
                if not chunk:
                    if buffered:
                        destination.put((source + '_invalid', 'incomplete_message'))
                    break
                if not buffered:
                    with self.partial_lock:
                        self.partial[key] = time.monotonic()
                buffered.extend(chunk)
                while b'\n' in buffered:
                    end = buffered.index(b'\n') + 1
                    if end > self.max_line_bytes:
                        destination.put((source + '_invalid', 'message_too_large'))
                        return
                    raw = bytes(buffered[:end])
                    del buffered[:end]
                    if not destination.put((source, raw)):
                        return
                    with self.partial_lock:
                        self.partial.pop(key, None)
                        if buffered:
                            self.partial[key] = time.monotonic()
                if len(buffered) > self.max_line_bytes:
                    destination.put((source + '_invalid', 'message_too_large'))
                    return
        except (OSError, ValueError):
            destination.put((source + '_invalid', 'stream_unavailable'))
        finally:
            with self.partial_lock:
                self.partial.pop(key, None)
            destination.put((source + '_eof', None))

    def start(self):
        self.generation += 1
        self.internal_id = 'warden-init-' + uuid.uuid4().hex
        self.started_at = self.last_activity = time.monotonic()
        self.ready = False
        # Bridge.start captures this queue in the reader thread's arguments.
        # Old readers retain their old label even if their EOF arrives later.
        self.events = TaggedQueue(self._events, self.generation, self.stopping)
        try:
            super().start()
        finally:
            self.events = self._events
        process, generation = self.proc, self.generation
        self.write_queue = queue.Queue(maxsize=16)
        self.writer_stop = threading.Event()
        writes, stopped = self.write_queue, self.writer_stop
        destination = TaggedQueue(self._events, generation, self.stopping)

        def write_backend():
            while not stopped.is_set() and not self.stopping.is_set():
                try:
                    ident, raw = writes.get(timeout=.1)
                except queue.Empty:
                    continue
                if stopped.is_set():
                    return
                try:
                    _write_all(process.stdin, raw)
                except (OSError, ValueError):
                    destination.put(('write_failed', ident))
                    return
                if not destination.put(('write_done', ident)):
                    return

        threading.Thread(target=write_backend, daemon=True).start()

    def send(self, raw):
        if self.write_queue is None or self.writer_stop.is_set():
            raise ProtocolStop('backend_unavailable')
        self.write_sequence += 1
        ident = self.write_sequence
        try:
            self.write_queue.put_nowait((ident, raw))
        except queue.Full:
            raise ProtocolStop('backend_write_capacity_reached') from None
        self.writes[ident] = time.monotonic()
        self.last_activity = time.monotonic()
        message = parse(raw)
        if message and message.get('method') == 'notifications/initialized':
            self.ready = True

    def error(self, ident, message, code=-32000):
        self.output(encoded({'jsonrpc': '2.0', 'id': ident,
            'error': {'code': code, 'message': message}}) + b'\n')

    @staticmethod
    def message(raw):
        message = parse(raw)
        if not message or message.get('jsonrpc') != '2.0':
            raise ProtocolStop('invalid_protocol_message')
        if 'id' in message:
            _rpc_key(message['id'])
        if 'method' in message and not isinstance(message['method'], str):
            raise ProtocolStop('invalid_protocol_message')
        if 'method' not in message and 'id' not in message:
            raise ProtocolStop('invalid_protocol_message')
        return message

    def track_request(self, message, pending):
        key = _rpc_key(message['id'])
        if key in pending:
            raise ProtocolStop('duplicate_request_id')
        if len(pending) >= 16:
            return False
        pending[key] = message['id']
        if pending is self.client_pending and message.get('method') == 'tools/list':
            params = message.get('params')
            self.discovery_requests[key] = isinstance(params, dict) and 'cursor' in params
        return True

    def accept_client(self, raw):
        message = self.message(raw)
        method = message.get('method')
        self.last_activity = time.monotonic()
        if self.init is None and method == 'server/discover' and 'id' in message:
            self.error(message['id'], 'Method not found; use the initialize handshake.', -32601)
            return
        if method is None:
            key = _rpc_key(message['id'])
            if key in self.server_pending and self.proc is not None:
                self.send(raw)
                del self.server_pending[key]
            return  # Late/unrecognized callback answers never cross generations.
        if method == 'notifications/cancelled':
            params = message.get('params')
            if isinstance(params, dict) and 'requestId' in params:
                key = _rpc_key(params['requestId'])
                self.client_pending.pop(key, None)
                self.discovery_requests.pop(key, None)
            self.uncertain = True
        if method == 'notifications/initialized' and 'id' not in message:
            self.initialized = raw
            if self.proc is not None:
                self.send(raw)
            return
        if self.init is None:
            if method != 'initialize' or 'id' not in message:
                raise ProtocolStop('initialize_required')
            self.init = message
            self.start()
            self.state = 'direct'
        elif method == 'initialize':
            self.error(message.get('id'), 'MCP session already initialized. Reconnect the client.', -32600)
            self.disconnect = True
            return
        if self.state == 'sleeping':
            if method == 'ping' and 'id' in message:
                self.reply(message, {})
                return
            if 'id' not in message:
                return
            if self.policy != 'stateless' or self.uncertain:
                self.error(message['id'], 'MCP session expired. Reconnect the client; temporary backend state was reset.')
                self.disconnect = True
                return
            if self.discovery_paginated:
                self.error(message['id'], 'MCP paginated discovery requires a fresh client connection. Reconnect the client.')
                self.disconnect = True
                return
            if self.tool_snapshot is None:
                self.error(message['id'], 'MCP restart requires previously observed tool discovery. Reconnect the client and refresh tools.')
                self.disconnect = True
                return
            if not self.track_request(message, self.client_pending):
                self.error(message['id'], 'MCP pending request capacity reached.')
                return
            self.held.append(raw)
            self.activate()  # Replays initialize only; the new request is held once.
            return
        if 'id' in message:
            if not self.track_request(message, self.client_pending):
                self.error(message['id'], 'MCP pending request capacity reached.')
                return
        if self.state == 'replaying':
            if len(self.held) >= 16:
                raise ProtocolStop('pending_capacity_reached')
            self.held.append(raw)
            return
        self.forward(raw, message)

    def accept_backend(self, raw):
        message = self.message(raw)
        self.last_activity = time.monotonic()
        method = message.get('method')
        if method is not None and 'id' in message:
            if not self.track_request(message, self.server_pending):
                raise ProtocolStop('callback_capacity_reached')
        if method == 'notifications/cancelled':
            params = message.get('params')
            if isinstance(params, dict) and 'requestId' in params:
                self.server_pending.pop(_rpc_key(params['requestId']), None)
            self.uncertain = True
        if method is None:
            if self.state == 'replaying' and message['id'] == self.internal_id:
                result = message.get('result')
                if not self.valid_result('initialize', result) or self.first_result is None or any(
                        result.get(field) != self.first_result.get(field)
                        for field in ('protocolVersion', 'capabilities', 'serverInfo', 'instructions')):
                    self.fail_pending('MCP backend initialization changed or failed. Reconnect the client.')
                    raise ProtocolStop('restart_handshake_failed')
                if self.discovery_paginated:
                    self.fail_pending('MCP paginated discovery requires a fresh client connection. Reconnect the client.')
                    raise ProtocolStop('restart_discovery_requires_reconnect')
                if self.tool_snapshot is not None:
                    if self.initialized:
                        self.send(self.initialized)
                        self.replay_initialized = True
                    self.replay_tools_id = 'warden-tools-' + uuid.uuid4().hex
                    self.send(encoded({'jsonrpc': '2.0', 'id': self.replay_tools_id,
                        'method': 'tools/list', 'params': {}}) + b'\n')
                else:
                    self.complete_replay()
                return
            if self.state == 'replaying' and message['id'] == self.replay_tools_id:
                result = message.get('result')
                if (not isinstance(result, dict) or 'nextCursor' in result
                        or result.get('tools') != self.tool_snapshot):
                    self.fail_pending('MCP tool metadata changed or could not be validated. Reconnect the client.')
                    raise ProtocolStop('restart_tool_metadata_changed')
                self.complete_replay()
                return
            else:
                key = _rpc_key(message['id'])
                self.client_pending.pop(key, None)
                paginated_request = self.discovery_requests.pop(key, None)
                result = message.get('result')
                if paginated_request is not None and isinstance(result, dict) and isinstance(result.get('tools'), list):
                    self.discovery_paginated |= paginated_request or 'nextCursor' in result
                    if not self.discovery_paginated:
                        self.tool_snapshot = result['tools']
                if self.init is not None and message['id'] == self.init['id'] and self.first_result is None:
                    if self.valid_result('initialize', message.get('result')):
                        self.first_result = message['result']
        super().accept_backend(raw)

    def complete_replay(self):
        if self.uncertain:
            self.fail_pending('MCP restart outcome is uncertain. Reconnect the client.')
            raise ProtocolStop('restart_outcome_unknown')
        self.state = 'active'
        if self.initialized and not self.replay_initialized:
            self.send(self.initialized)
        held, self.held = self.held, []
        for raw in held:
            self.send(raw)

    def fail_pending(self, text):
        for ident in list(self.client_pending.values()):
            self.error(ident, text)
        self.client_pending.clear()
        self.discovery_requests.clear()
        self.held.clear()

    def stop_backend(self):
        old = self.proc
        if self.writer_stop:
            self.writer_stop.set()
        blocked_process_only = bool(self.writes) and self.cleanup_scope == 'process' and old is not None
        if self.writes and self.job is not None:
            # Closing a pipe while another Windows thread is writing can wait
            # for that write. End the already-owned private Job first so its
            # read ends close and unblock the writer. This path is reached on
            # client shutdown/error, never clean idle (which has no writes).
            self.job.close()
        if blocked_process_only:
            # A process-only profile may have shared descendants holding its
            # handles. Stop only this exact Popen child; do not close a pipe
            # that a daemon writer may still be using or terminate descendants.
            self.unlock()
            if old.poll() is None:
                old.terminate()
                try:
                    old.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    old.kill()
                    old.wait(timeout=3)
        else:
            super().shutdown()
        self.proc = self.job = None
        self.write_queue = self.writer_stop = None
        self.writes.clear()
        self.generation += 1  # Reject every remaining message/EOF from the old reader.
        self.ready = False
        if old and old.stdout and not blocked_process_only:
            old.stdout.close()

    def poll_idle(self):
        now = time.monotonic()
        with self.partial_lock:
            partial = [began for (source, generation), began in self.partial.items()
                if source == 'client' or generation == self.generation]
        if partial and now - min(partial) >= self.partial_seconds:
            self.fail_pending('MCP partial message timed out. Reconnect the client.')
            raise ProtocolStop('partial_message_timeout')
        if self.writes and now - min(self.writes.values()) >= self.write_seconds:
            self.fail_pending('MCP backend write timed out; outcome may be unknown. Reconnect the client.')
            raise ProtocolStop('backend_write_timeout')
        if self.proc is None:
            return
        if (not self.ready or self.state == 'replaying') and now - self.started_at >= self.handshake_seconds:
            self.fail_pending('MCP backend initialization timed out. Reconnect the client.')
            raise ProtocolStop('initialize_timeout')
        if (self.ready and self.idle_seconds > 0 and self.state != 'replaying'
                and not self.client_pending and not self.server_pending
                and not self.writes
                and not partial
                and self._events.empty() and now - self.last_activity >= self.idle_seconds):
            self.stop_backend()
            self.state = 'sleeping'

    def run(self):
        if os.name != 'nt':
            raise ValueError('windows_required')
        destination = TaggedQueue(self._events, None, self.stopping)
        # Bypass the interpreter's standard BufferedReader lock in daemon input
        # reads. Raw pipe reads return available chunks, are bounded here, and
        # do not hold the standard stream's lock during process shutdown.
        client_stream = getattr(sys.stdin.buffer, 'raw', sys.stdin.buffer)
        threading.Thread(target=self.read_stream,
            args=(client_stream, destination, 'client'), daemon=True).start()
        try:
            while not self.disconnect:
                try:
                    source, generation, raw = self._events.get(timeout=.05)
                except queue.Empty:
                    self.poll_idle()
                    continue
                if (source.startswith('backend') or source.startswith('write_')) and generation != self.generation:
                    continue
                if source == 'client':
                    self.accept_client(raw)
                elif source == 'backend':
                    self.accept_backend(raw)
                elif source == 'write_done':
                    self.writes.pop(raw, None)
                    self.last_activity = time.monotonic()
                elif source == 'write_failed':
                    raise ProtocolStop('backend_write_failed')
                elif source == 'client_eof':
                    return 0
                elif source.endswith('_invalid'):
                    self.fail_pending('MCP transport rejected an invalid or oversized message. Reconnect the client.')
                    raise ProtocolStop(str(raw))
                elif source == 'backend_eof':
                    self.fail_pending('MCP backend exited. Outcome of pending work is unknown; reconnect the client.')
                    raise ProtocolStop('backend_exited')
                self.poll_idle()
            return 0
        except (BrokenPipeError, OSError):
            return 2
        except ProtocolStop as error:
            self.fail_pending('MCP transport closed; pending outcomes may be unknown. Reconnect the client.')
            print('mcp-warden: ' + str(error), file=sys.stderr, flush=True)
            return 2
        finally:
            self.stopping.set()
            self.stop_backend()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    args = parser.parse_args()
    try:
        manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8-sig'))
        return IdleBridge(manifest).run()
    except Exception:
        print('mcp-warden: stdio transport unavailable', file=sys.stderr, flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
