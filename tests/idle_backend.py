"""Synthetic extensions of the public example for stdio lifecycle tests."""
import argparse
import ctypes
from ctypes import wintypes
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

parser = argparse.ArgumentParser()
parser.add_argument('--ledger', required=True)
parser.add_argument('--change-on-restart', choices=['annotations', 'schema', 'instructions', 'pagination'])
args = parser.parse_args()
ledger = Path(args.ledger)
generation = 1
if ledger.exists():
    generation += sum(json.loads(line).get('kind') == 'start'
        for line in ledger.read_text(encoding='utf-8').splitlines())
lock = threading.Lock()
spec = importlib.util.spec_from_file_location('idle_test_echo',
    Path(__file__).resolve().parents[1] / 'examples' / 'echo_server.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def record(kind, **data):
    with lock, ledger.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'kind': kind, 'pid': os.getpid(), **data}) + '\n')


def created(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
            return None
        return str(times[0].dwLowDateTime | times[0].dwHighDateTime << 32)
    finally:
        kernel.CloseHandle(handle)


record('start', created=created(os.getpid()))
original_send, original_handle = module.send, module.handle


def send(message):
    if message.get('method') == 'roots/list':
        record('callback', ident=message['id'])
    if 'result' in message and isinstance(message['result'], dict) and 'tools' in message['result']:
        message['result']['tools'].extend({'name': name, 'inputSchema': {'type': 'object', 'properties': {}}}
            for name in ('same_id_callback', 'notify_for', 'slow_notification', 'stop_reading', 'cancel_wait', 'oversized', 'spawn_child'))
        for tool in message['result']['tools']:
            if tool['name'] == 'echo' and args.change_on_restart == 'annotations':
                tool['annotations'] = {'readOnlyHint': generation == 1}
            if tool['name'] == 'echo' and args.change_on_restart == 'schema' and generation > 1:
                tool['inputSchema']['properties']['value']['type'] = 'number'
        if args.change_on_restart == 'pagination':
            message['result']['nextCursor'] = 'fixture-generation-cursor'
    if ('result' in message and isinstance(message['result'], dict)
            and 'serverInfo' in message['result'] and args.change_on_restart == 'instructions'):
        message['result']['instructions'] = 'Fixture instruction version ' + str(generation)
    original_send(message)


def notifications():
    for _ in range(5):
        time.sleep(.18)
        send({'jsonrpc': '2.0', 'method': 'notifications/tools/list_changed'})
        record('notification')


def slow_notification():
    time.sleep(.1)
    raw = json.dumps({'jsonrpc': '2.0', 'method': 'notifications/tools/list_changed'}) + '\n'
    with module._write_lock:
        sys.stdout.write(raw[:1])
        sys.stdout.flush()
        time.sleep(.85)
        sys.stdout.write(raw[1:])
        sys.stdout.flush()
    record('slow_notification_complete')


def handle(message):
    method, ident = message.get('method'), message.get('id')
    if method == 'initialize':
        record('initialize', ident=ident)
    elif method == 'tools/list':
        record('catalog')
    elif method is None:
        record('client_response', ident=ident)
    elif method == 'notifications/cancelled':
        record('cancelled')
    if method == 'tools/call':
        params = message.get('params', {})
        name, values = params.get('name'), params.get('arguments', {})
        record('call', name=name, value=values.get('value'), ident=ident)
        if name == 'same_id_callback':
            module._callbacks[str(ident)] = ident
            send({'jsonrpc': '2.0', 'id': ident, 'method': 'roots/list', 'params': {}})
            return
        if name == 'notify_for':
            module.tool_result(ident, 'notifying')
            threading.Thread(target=notifications, daemon=True).start()
            return
        if name == 'slow_notification':
            module.tool_result(ident, 'slow-notification-starting')
            threading.Thread(target=slow_notification, daemon=True).start()
            return
        if name == 'stop_reading':
            module.tool_result(ident, 'not-reading')
            time.sleep(3)  # Stops this fixture's input loop, not a production app.
            return
        if name == 'cancel_wait':
            return  # Deliberately does not confirm a final outcome after cancellation.
        if name == 'oversized':
            module.tool_result(ident, 'X' * 6000)
            return
        if name == 'spawn_child':
            child = subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(3)'],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW)
            module.tool_result(ident, {'child': child.pid, 'created': created(child.pid)})
            return
    original_handle(message)


module.send, module.handle = send, handle
raise SystemExit(module.main())
