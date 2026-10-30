"""Telemetry must survive a backend exiting between observations."""
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('warden_telemetry_regression', ROOT / 'warden.py')
warden = importlib.util.module_from_spec(spec)
spec.loader.exec_module(warden)


class TelemetryRegression(unittest.TestCase):
    def test_backend_snapshot_survives_exit_between_attribute_reads(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            failures = []

            class ChangingBridge:
                """The backend was observed, then stop_backend cleared proc."""
                reads = 0

                def __init__(self, manifest):
                    pass

                @property
                def proc(self):
                    self.reads += 1
                    return SimpleNamespace(poll=lambda: 0) if self.reads == 1 else None

                def run(self):
                    deadline = time.monotonic() + .5
                    while time.monotonic() < deadline:
                        files = list((state / 'run' / 'stdio').glob('*/status.json'))
                        if files:
                            self.snapshot = json.loads(files[0].read_text())
                            return 0 if self.snapshot['active'] else 1
                        time.sleep(.01)
                    return 1

            class Owner:
                pid = 1234
                created = 5678

                def __init__(self, pid):
                    pass

                def usage(self):
                    return 100, 0.0

                def close(self):
                    pass

            bridge_module = SimpleNamespace(IdleBridge=ChangingBridge, redirect_diagnostics=lambda *a: None)
            metrics_module = SimpleNamespace(PinnedProcess=Owner)
            with mock.patch.object(warden, 'runtime_module', side_effect=[bridge_module, metrics_module]), \
                    mock.patch.object(threading, 'excepthook', side_effect=lambda args: failures.append(args.exc_type.__name__)):
                result = warden.connect_stdio(state, 'fixture', {'profiles': {'fixture': {'manifest': {}}}})
            self.assertEqual(failures, [])
            self.assertEqual(result, 0, 'a disappearing backend must not stop future telemetry samples')


if __name__ == '__main__':
    unittest.main()
