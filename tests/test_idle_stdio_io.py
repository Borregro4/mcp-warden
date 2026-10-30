"""Deterministic short-write checks; no backend or listener starts."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('test_idle_io', ROOT / 'runtime' / 'idle_stdio.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class WriteAllTests(unittest.TestCase):
    def test_partial_writes_are_completed_once_before_flush(self):
        class Partial:
            def __init__(self):
                self.data = bytearray()
                self.calls = self.flushes = 0

            def write(self, value):
                self.calls += 1
                count = min(2, len(value))
                self.data.extend(value[:count])
                return count

            def flush(self):
                self.flushes += 1

        stream = Partial()
        module._write_all(stream, b'abcdefg')
        self.assertEqual(stream.data, b'abcdefg')
        self.assertEqual(stream.calls, 4)
        self.assertEqual(stream.flushes, 1)

    def test_nonprogress_write_fails_without_flush_or_loop(self):
        for result in (0, -1, None):
            with self.subTest(result=result):
                class Stalled:
                    def write(self, value):
                        return result

                    def flush(self):
                        raise AssertionError('failed write must not flush')
                with self.assertRaisesRegex(OSError, 'backend_write_failed'):
                    module._write_all(Stalled(), b'payload')


class ResponsePathTests(unittest.TestCase):
    def test_rpc_ids_normalize_integral_numbers_but_keep_strings_distinct(self):
        self.assertEqual(module._rpc_key(1), module._rpc_key(1.0))
        self.assertNotEqual(module._rpc_key(1), module._rpc_key('1'))
        for invalid in (True, False, None, 1.5, float('inf'), float('nan'), [], {}):
            with self.subTest(invalid=repr(invalid)), self.assertRaises(module.ProtocolStop):
                module._rpc_key(invalid)

    def test_uncached_transport_outputs_once_without_reentering_catalog_handler(self):
        bridge = module.IdleBridge({'command': sys.executable})
        self.assertFalse(bridge.cache_enabled)
        self.assertFalse(bridge.catalog_phase)
        bridge.init = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize'}
        response = module.encoded({'jsonrpc': '2.0', 'id': 1, 'result': {
            'protocolVersion': '2025-11-25', 'capabilities': {}, 'serverInfo': {'name': 'fixture'}}}) + b'\n'
        bridge.track_request(bridge.init, bridge.client_pending)
        with patch.object(module.Bridge, 'accept_backend', side_effect=AssertionError('catalog path must stay disabled')), \
                patch.object(bridge, 'output') as output:
            bridge.accept_backend(response)
        output.assert_called_once_with(response)
        self.assertFalse(bridge.client_pending)
        self.assertEqual(bridge.first_result['serverInfo'], {'name': 'fixture'})


if __name__ == '__main__':
    unittest.main()
