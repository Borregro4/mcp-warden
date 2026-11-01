"""CLI contracts: non-destructive state, private auth, and agent snippets."""
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import queue
import shutil
import tempfile
import threading
import time
import tomllib
import unittest
from unittest.mock import patch
import subprocess
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("warden", ROOT / "warden.py")
warden = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(warden)


class StateContracts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name) / "state with spaces"

    def test_init_refuses_any_existing_data_and_preserves_bytes(self):
        self.state.mkdir()
        existing = self.state / "notes.txt"
        existing.write_bytes(b"user material\x00")
        with self.assertRaises(warden.WardenError):
            warden.initialize(self.state)
        self.assertEqual(existing.read_bytes(), b"user material\x00")
        self.assertFalse((self.state / "token").exists())

    def test_init_uses_explicit_paths_and_cannot_rotate_existing_token(self):
        config = warden.initialize(self.state, port=18742, idle_seconds=0)
        token = (self.state / "token").read_bytes()
        self.assertEqual(config["idle_session_timeout_seconds"], 0)
        self.assertEqual(config["port"], 18742)
        for field in ("token_file", "run_dir", "bridge_python", "owned_bridge", "bridge"):
            self.assertTrue(Path(config[field]).is_absolute(), field)
        self.assertNotIn(token.strip().decode(), json.dumps(config))
        with self.assertRaises(warden.WardenError):
            warden.initialize(self.state)
        self.assertEqual(token, (self.state / "token").read_bytes())

    def test_invalid_init_never_creates_state(self):
        for port, idle in ((0, 900), (65536, 900), (True, 900), (18741, -1), (18741, float("nan"))):
            with self.subTest(port=port, idle=idle), self.assertRaises(warden.WardenError):
                warden.initialize(self.state, port, idle)
            self.assertFalse(self.state.exists())

    def test_generated_codex_and_claude_configs_select_same_connector(self):
        warden.initialize(self.state)
        codex = tomllib.loads(warden.configuration(self.state, "example", "codex"))
        claude = json.loads(warden.configuration(self.state, "example", "claude"))
        codex_entry = codex["mcp_servers"]["warden-example"]
        claude_entry = claude["mcpServers"]["warden-example"]
        self.assertEqual(codex_entry, claude_entry)
        self.assertIn(str(self.state), codex_entry["args"])
        self.assertEqual(codex_entry["args"][-1], "example")
        self.assertNotIn(warden.read_token(self.state), json.dumps(claude_entry))

    def test_codex_configuration_preserves_non_bmp_paths(self):
        unicode_state = self.state / 'state-\U0001f4bb'
        warden.initialize(unicode_state, transport='stdio')
        with patch.object(warden, 'ROOT', self.state / 'source-\U0001f680'), \
                patch.object(warden.sys, 'executable', str(self.state / 'python-\U0001f40d.exe')):
            codex = tomllib.loads(warden.configuration(unicode_state, 'example', 'codex'))
            claude = json.loads(warden.configuration(unicode_state, 'example', 'claude'))
        self.assertEqual(codex['mcp_servers']['warden-example'], claude['mcpServers']['warden-example'])
        self.assertIn(str(unicode_state), codex['mcp_servers']['warden-example']['args'])

    def test_bad_configuration_returns_fixed_error_without_state_data(self):
        self.state.mkdir()
        (self.state / "config.json").write_text('{"private":"secret-value"}', encoding="utf-8")
        output = io.StringIO()
        with redirect_stderr(output):
            result = warden.main(["status", "--state", str(self.state)])
        self.assertEqual(result, 2)
        self.assertNotIn("secret-value", output.getvalue())
        self.assertNotIn(str(self.state), output.getvalue())

    def test_configuration_unknown_profile_does_not_edit_state(self):
        warden.initialize(self.state)
        before = (self.state / "config.json").read_bytes()
        with self.assertRaises(warden.WardenError):
            warden.configuration(self.state, "not-installed", "claude")
        self.assertEqual(before, (self.state / "config.json").read_bytes())

    def test_token_cannot_escape_explicit_state(self):
        config = warden.initialize(self.state)
        config["token_file"] = str(self.state.parent / "other-token")
        (self.state / "config.json").write_text(json.dumps(config), encoding="utf-8")
        with self.assertRaises(warden.WardenError):
            warden.load_config(self.state)

    def test_load_config_resolves_equivalent_state_path_before_token_check(self):
        expected = warden.initialize(self.state)
        # Resolves to the same directory while retaining a distinct lexical
        # representation, as Windows short/long TEMP aliases do in CI.
        alias = self.state / "run" / ".."
        self.assertNotEqual(alias, alias.resolve())
        self.assertEqual(warden.load_config(alias), expected)

    def test_equivalent_state_alias_still_rejects_sibling_token(self):
        config = warden.initialize(self.state)
        sibling = self.state.parent / (self.state.name + "-sibling")
        sibling.mkdir()
        (sibling / "token").write_text("different-local-token", encoding="ascii")
        config["token_file"] = str(sibling / "token")
        (self.state / "config.json").write_text(json.dumps(config), encoding="utf-8")
        with self.assertRaises(warden.WardenError):
            warden.load_config(self.state / "run" / "..")


class HealthContract(unittest.TestCase):
    def test_status_sends_token_only_as_authorization_header(self):
        observed = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                observed.append((self.path, self.headers.get("Authorization")))
                body = json.dumps({"status": "ready", "sessionCount": 3, "pendingRequestCount": 1}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                state = Path(temporary) / "state"
                warden.initialize(state, server.server_port)
                token = warden.read_token(state)
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    result = warden.main(["status", "--state", str(state)])
                self.assertEqual(result, 0)
                self.assertEqual(observed, [("/_health", "Bearer " + token)])
                self.assertNotIn(token, stdout.getvalue())
                self.assertIn("3 active sessions", stdout.getvalue())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_doctor_does_not_initialize_or_spawn_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing"
            with patch.object(warden.subprocess, "Popen", side_effect=AssertionError("unexpected runtime spawn")), \
                 patch.object(warden.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout="v22.1.0\n")):
                data = warden.doctor(missing)
            self.assertFalse(data["checks"]["state_valid"])
            self.assertFalse(missing.exists())

    def test_doctor_rejects_missing_required_runtime_sources(self):
        required = {
            'stdio': ('idle_stdio.py', 'lazy_stdio.py', 'owned_bridge.py'),
            'gateway': ('gateway.mjs', 'connect.mjs', 'lazy_stdio.py', 'owned_bridge.py'),
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'runtime').mkdir()
            for package in ('supergateway', '@modelcontextprotocol/sdk'):
                package_path = root / 'node_modules' / package
                package_path.mkdir(parents=True)
                (package_path / 'package.json').write_text('{}')
            for name in set(sum(required.values(), ())):
                (root / 'runtime' / name).write_text('')
            with patch.object(warden, 'ROOT', root), \
                    patch.object(shutil, 'which', return_value='node.exe'), \
                    patch.object(warden.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout='v22.1.0\n')):
                for transport, filenames in required.items():
                    checks = warden.doctor(None, transport)['checks']
                    self.assertTrue(all(value for key, value in checks.items() if key != 'windows_runtime'))
                    for filename in filenames:
                        with self.subTest(transport=transport, missing=filename):
                            path = root / 'runtime' / filename
                            path.unlink()
                            self.assertFalse(warden.doctor(None, transport)['ready'])
                            path.write_text('')

    def test_doctor_validates_stdio_profile_without_starting_backend(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / 'state'
            config = warden.initialize(state, transport='stdio')
            path = state / 'config.json'
            valid_profile = config['profiles']['example']
            invalid_profiles = [
                {'transport': 'stdio'},
                {'transport': 'stdio', 'manifest': {}},
                {'transport': 'stdio', 'manifest': {'command': warden.sys.executable, 'args': 'not-a-list'}},
                {'transport': 'stdio', 'manifest': {'command': warden.sys.executable, 'env': []}},
                {'transport': 'stdio', 'idle_session_timeout_seconds': -1, 'manifest': valid_profile['manifest']},
                {'transport': 'stdio', 'manifest': {'command': str(state / 'absent.exe')}},
            ]
            with patch.object(warden.subprocess, 'Popen', side_effect=AssertionError('unexpected backend spawn')):
                before = sorted(str(item.relative_to(state)) for item in state.rglob('*'))
                self.assertTrue(warden.doctor(state, 'stdio')['checks']['profiles_valid'])
                self.assertEqual(before, sorted(str(item.relative_to(state)) for item in state.rglob('*')))
                for profile in invalid_profiles:
                    with self.subTest(profile=profile):
                        config['profiles']['example'] = profile
                        path.write_text(json.dumps(config), encoding='utf-8')
                        data = warden.doctor(state, 'stdio')
                        self.assertFalse(data['ready'])
                        self.assertFalse(data['checks']['profiles_valid'])

    def test_doctor_accepts_relative_command_resolved_by_client_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / 'state'
            config = warden.initialize(state, transport='stdio')
            config['profiles']['example']['manifest'] = {
                'command': 'custom-backend', 'args': [], 'env': {'PATH': 'client-specific-path'}}
            (state / 'config.json').write_text(json.dumps(config), encoding='utf-8')
            with patch.object(warden.subprocess, 'Popen', side_effect=AssertionError('unexpected backend spawn')):
                self.assertTrue(warden.doctor(state, 'stdio')['checks']['profiles_valid'])

    def test_doctor_checks_gateway_runtime_paths_without_resolving_relative_backend(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / 'state'
            config = warden.initialize(state)
            config['profiles']['example']['manifest'] = {
                'command': 'npx', 'args': ['a-client-specific-package'], 'env': {'PATH': 'client-specific-path'}}
            config_path = state / 'config.json'
            config_path.write_text(json.dumps(config), encoding='utf-8')
            with patch.object(shutil, 'which', return_value='node.exe'), \
                    patch.object(warden.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout='v22.1.0\n')), \
                    patch.object(warden.subprocess, 'Popen', side_effect=AssertionError('unexpected backend spawn')):
                initial = warden.doctor(state, 'gateway')['checks']
                self.assertTrue(initial['profiles_valid'])
                self.assertTrue(initial['configured_runtime_paths'])
                config['owned_bridge'] = str(state / 'absent-bridge.py')
                config_path.write_text(json.dumps(config), encoding='utf-8')
                data = warden.doctor(state, 'gateway')
                self.assertFalse(data['ready'])
                self.assertFalse(data['checks']['configured_runtime_paths'])

    def test_connect_rejects_missing_manifest_with_fixed_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / 'state'
            config = warden.initialize(state, transport='stdio')
            config['profiles']['example'] = {'transport': 'stdio', 'private': 'must-not-appear'}
            (state / 'config.json').write_text(json.dumps(config), encoding='utf-8')
            stderr = io.StringIO()
            with patch.object(warden, 'require_windows'), redirect_stderr(stderr):
                result = warden.main(['connect', '--state', str(state), '--profile', 'example'])
            self.assertEqual(result, 2)
            self.assertNotIn('Traceback', stderr.getvalue())
            self.assertNotIn('must-not-appear', stderr.getvalue())

    def test_stdio_status_preserves_idle_block_reason(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / 'state'
            warden.initialize(state, transport='stdio')
            folder = state / 'run' / 'stdio' / 'fixture'
            folder.mkdir(parents=True)
            sample = {'active': True, 'ownerPid': 123, 'ownerCreated': '456',
                      'idleExpiryBlockedReason': 'mcp_tasks_capability'}
            (folder / 'status.json').write_text(json.dumps(sample), encoding='utf-8')
            owner = SimpleNamespace(alive=lambda: True, close=lambda: None)
            metrics = SimpleNamespace(PinnedProcess=lambda *args: owner)
            with patch.object(warden, 'require_windows'), patch.object(warden, 'runtime_module', return_value=metrics):
                status = warden.get_stdio_status(state)
            self.assertEqual(status['samples'][0]['idleExpiryBlockedReason'], 'mcp_tasks_capability')

    def test_stdio_sampler_records_idle_block_reason(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / 'state'
            config = warden.initialize(state, transport='stdio')
            bridge_module = SimpleNamespace(
                IdleBridge=lambda manifest: SimpleNamespace(proc=None, idle_expiry_blocked_reason='mcp_tasks_capability', run=lambda: 0),
                redirect_diagnostics=lambda *args: None)
            owner = SimpleNamespace(pid=123, created=456, usage=lambda: (100, 0.0), close=lambda: None)
            metrics_module = SimpleNamespace(PinnedProcess=lambda *args: owner)
            with patch.object(warden, 'runtime_module', side_effect=[bridge_module, metrics_module]):
                self.assertEqual(warden.connect_stdio(state, 'example', config), 0)
            samples = list((state / 'run' / 'stdio').glob('*/status.json'))
            self.assertEqual(len(samples), 1)
            self.assertEqual(json.loads(samples[0].read_text())['idleExpiryBlockedReason'], 'mcp_tasks_capability')


@unittest.skipUnless(shutil.which("node") and (ROOT / "node_modules" / "@modelcontextprotocol" / "sdk").is_dir(),
                     "connector tests require npm ci and Node.js")
class ConnectorLifecycle(unittest.TestCase):
    def run_connector(self, *, stalled_delete=False, broken_stdout=False, python_cli=False):
        deletion_seen = threading.Event()
        release_delete = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                body = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Mcp-Session-Id", "synthetic-session")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_DELETE(self):
                deletion_seen.set()
                if stalled_delete:
                    release_delete.wait(10)
                try:
                    self.send_response(200)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                except OSError:
                    pass

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        process = None
        try:
            with tempfile.TemporaryDirectory() as temporary:
                state = Path(temporary) / "state"
                warden.initialize(state, server.server_port)
                command = ([warden.sys.executable, "-I", "-B", "-u", str(ROOT / "warden.py"),
                            "connect", "--state", str(state), "--profile", "example"] if python_cli else
                           [shutil.which("node"), str(ROOT / "runtime" / "connect.mjs"),
                            "--state", str(state), "--profile", "example", "--owner-pid", "1", "--owner-created", "1"])
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                response = queue.Queue()
                if broken_stdout:
                    process.stdout.close()
                else:
                    reader = threading.Thread(target=lambda: response.put(process.stdout.readline()), daemon=True)
                    reader.start()
                process.stdin.write(b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n')
                process.stdin.flush()
                if not broken_stdout:
                    self.assertEqual(json.loads(response.get(timeout=5))["id"], 1)
                    process.stdin.close()
                    process.stdin = None
                began = time.monotonic()
                self.assertEqual(process.wait(timeout=6), 0)
                self.assertLess(time.monotonic() - began, 5)
                self.assertTrue(deletion_seen.wait(1))
                self.assertEqual(process.stderr.read(), b"")
        finally:
            release_delete.set()
            if process:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=3)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream and not stream.closed:
                        stream.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_client_eof_is_bounded_when_delete_never_responds(self):
        self.run_connector(stalled_delete=True)

    def test_broken_stdout_closes_without_unhandled_epipe(self):
        self.run_connector(broken_stdout=True)

    @unittest.skipUnless(os.name == "nt", "Windows CLI connector test")
    def test_python_cli_forwards_its_mcp_stdio_pipes(self):
        self.run_connector(python_cli=True)

    @unittest.skipUnless(os.name == "nt", "Windows Job ownership test")
    def test_killing_python_connector_also_closes_owned_node(self):
        # Instrument only this test child's Popen to learn its own Node PID.
        # No global process scan and no unrelated process is terminated.
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            ledger = Path(temporary) / "node-pid"
            warden.initialize(state)
            script = "\n".join([
                "import importlib.util, subprocess, pathlib",
                "s=importlib.util.spec_from_file_location('warden', " + repr(str(ROOT / "warden.py")) + ")",
                "m=importlib.util.module_from_spec(s); s.loader.exec_module(m)",
                "original=subprocess.Popen",
                "class Capture(original):",
                "    def __init__(self, *args, **kwargs):",
                "        super().__init__(*args, **kwargs)",
                "        pathlib.Path(" + repr(str(ledger)) + ").write_text(str(self.pid))",
                "subprocess.Popen=Capture",
                "m.connect(pathlib.Path(" + repr(str(state)) + "), 'example')",
            ])
            parent = subprocess.Popen([warden.sys.executable, "-I", "-B", "-c", script],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      creationflags=subprocess.CREATE_NO_WINDOW)
            node = None
            try:
                deadline = time.monotonic() + 5
                while not ledger.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(ledger.exists(), "connector did not launch Node")
                process_spec = importlib.util.spec_from_file_location("owned_bridge", ROOT / "runtime" / "owned_bridge.py")
                process_module = importlib.util.module_from_spec(process_spec)
                process_spec.loader.exec_module(process_module)
                node = process_module.PinnedProcess(int(ledger.read_text()))
                time.sleep(0.2)  # allow assignment/resume to complete
                self.assertTrue(node.alive())
                parent.terminate()
                parent.wait(timeout=3)
                deadline = time.monotonic() + 3
                while node.alive() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertFalse(node.alive(), "Node survived its owning Python connector")
            finally:
                if node:
                    node.close()
                if parent.poll() is None:
                    parent.terminate()
                    parent.wait(timeout=3)
                for stream in (parent.stdin, parent.stdout, parent.stderr):
                    if stream:
                        stream.close()


if __name__ == "__main__":
    unittest.main()
