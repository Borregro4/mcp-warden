"""Deterministic short-write checks; no backend or listener starts."""
import importlib.util
from pathlib import Path
import unittest

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


if __name__ == '__main__':
    unittest.main()
