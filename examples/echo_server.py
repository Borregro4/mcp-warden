"""Dependency-free MCP example: echo, delayed echo and a client callback.

Writes only MCP messages to stdout. It stores no data and needs no API key.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

_write_lock = threading.Lock()
_callbacks: dict[str, object] = {}
_sequence = 0


def send(message):
    with _write_lock:
        sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
        sys.stdout.flush()


def result(ident, value):
    send({"jsonrpc": "2.0", "id": ident, "result": value})


def tool_result(ident, value):
    data = {"pid": os.getpid(), "value": value}
    result(ident, {"content": [{"type": "text", "text": json.dumps(data)}],
                   "structuredContent": data})


def delayed(ident, seconds, value):
    time.sleep(min(max(float(seconds), 0), 30))
    tool_result(ident, value)


def delayed_callback(callback):
    # Deliver outside the initiating tool response, over the client's GET stream.
    time.sleep(0.1)
    send({"jsonrpc": "2.0", "id": callback, "method": "roots/list", "params": {}})


def handle(message):
    global _sequence
    method, ident = message.get("method"), message.get("id")
    if method is None:
        pending = _callbacks.pop(str(ident), None)
        if pending is not None:
            tool_result(pending, "callback-completed")
        return
    if ident is None:
        return
    if method == "initialize":
        result(ident, {"protocolVersion": message.get("params", {}).get("protocolVersion", "2025-06-18"),
                       "capabilities": {"tools": {}},
                       "serverInfo": {"name": "mcp-warden-example", "version": "0.1.0"}})
    elif method == "ping":
        result(ident, {})
    elif method == "tools/list":
        result(ident, {"tools": [{**tool, "annotations": {
            "readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": False}} for tool in [
            {"name": "echo", "description": "Return a value and this backend PID.",
             "inputSchema": {"type": "object", "properties": {"value": {"type": "string"}}}},
            {"name": "sleep", "description": "Return after a bounded delay (maximum 30 seconds).",
             "inputSchema": {"type": "object", "properties": {"seconds": {"type": "number"}, "value": {"type": "string"}}}},
            {"name": "callback", "description": "Ask this client's roots/list before returning.",
             "inputSchema": {"type": "object", "properties": {}}},
            {"name": "notify_callback", "description": "Return, then ask roots/list over the event stream.",
             "inputSchema": {"type": "object", "properties": {}}},
        ]]})
    elif method == "tools/call":
        params = message.get("params", {})
        arguments = params.get("arguments", {})
        if params.get("name") == "echo":
            tool_result(ident, arguments.get("value", "hello"))
        elif params.get("name") == "sleep":
            threading.Thread(target=delayed, args=(ident, arguments.get("seconds", 1), arguments.get("value", "awake")), daemon=True).start()
        elif params.get("name") == "callback":
            _sequence += 1
            callback = "example-callback-" + str(_sequence)
            _callbacks[callback] = ident
            send({"jsonrpc": "2.0", "id": callback, "method": "roots/list", "params": {}})
        elif params.get("name") == "notify_callback":
            _sequence += 1
            callback = "example-notification-callback-" + str(_sequence)
            tool_result(ident, "callback-starting")
            threading.Thread(target=delayed_callback, args=(callback,), daemon=True).start()
        else:
            send({"jsonrpc": "2.0", "id": ident, "error": {"code": -32602, "message": "Unknown tool"}})
    else:
        send({"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": "Method not found"}})


def main():
    for raw in sys.stdin:
        try:
            message = json.loads(raw)
            if isinstance(message, dict):
                handle(message)
        except (ValueError, TypeError):
            # Invalid input never echoes raw content into diagnostics.
            print("example: invalid message", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
