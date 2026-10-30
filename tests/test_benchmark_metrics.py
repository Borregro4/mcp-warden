"""Validate benchmark attribution before comparing resource totals."""
import importlib.util
import os
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('test_benchmark_metrics', ROOT / 'scripts' / 'benchmark_processes.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def row(pid, parent, created, private=10):
    return {'pid': pid, 'parent': parent, 'created': str(created), 'privateBytes': private,
        'workingSetBytes': private * 2, 'cpuSeconds': 0}


class FakeReader:
    def __init__(self, rows, cutoff=200, unavailable=()):
        self.rows = {value['pid']: value for value in rows}
        self.cutoff = cutoff
        self.unavailable = unavailable

    def snapshot(self):
        return [(r['pid'], r['parent']) for r in self.rows.values()], self.cutoff

    def query(self, pid):
        if pid in self.unavailable:
            raise OSError('fixture_unavailable')
        return self.rows.get(pid)

    def available(self):
        return 8 * 1024**3


class BenchmarkAttributionTests(unittest.TestCase):
    def test_old_child_under_reused_parent_pid_is_not_adopted(self):
        reader = FakeReader([row(10, 0, 100), row(11, 10, 150), row(12, 10, 90, 999999), row(13, 12, 160, 999999)])
        result = module.collect(reader, [10], {10: '100'})
        self.assertEqual({r['pid'] for r in result['processes']}, {10, 11})
        self.assertEqual(result['privateBytes'], 20)
        self.assertEqual(result['rejectedParentEdges'], 1)

    def test_expected_root_creation_rejects_reused_root_pid(self):
        result = module.collect(FakeReader([row(10, 0, 110, 999999)]), [10], {10: '100'})
        self.assertEqual(result['processCount'], 0)
        self.assertEqual(len(result['rejectedRootIdentities']), 1)

    def test_process_created_after_snapshot_start_is_rejected(self):
        result = module.collect(FakeReader([row(10, 0, 100), row(11, 10, 210, 999999)]), [10], {})
        self.assertEqual(result['privateBytes'], 10)
        self.assertEqual(result['rejectedParentEdges'], 1)

    def test_overlapping_explicit_roots_are_counted_once(self):
        result = module.collect(FakeReader([row(10, 0, 100), row(11, 10, 150)]), [10, 11], {})
        self.assertEqual(result['processCount'], 2)
        self.assertEqual(result['privateBytes'], 20)

    def test_unreadable_candidate_is_unknown_not_zero(self):
        result = module.collect(FakeReader([row(10, 0, 100), row(11, 10, 150)], unavailable=[11]), [10], {})
        self.assertEqual(result['unavailable'], [11])

    @unittest.skipUnless(os.name == 'nt', 'Real Windows private-byte query')
    def test_real_windows_query_extends_canonical_identity(self):
        reader = module.BenchmarkReader()
        value = reader.query(os.getpid())
        self.assertGreater(value['privateBytes'], 0)
        self.assertGreater(value['workingSetBytes'], 0)
        result = module.collect(reader, [os.getpid()], {os.getpid(): value['created']})
        self.assertTrue(any(r['pid'] == os.getpid() and r['created'] == value['created'] for r in result['processes']))


if __name__ == '__main__':
    unittest.main()
