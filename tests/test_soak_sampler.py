"""Regression cases for stale parent PIDs and orphan liveness checks."""
import importlib.util
import os
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('soak_sampler', Path(__file__).resolve().parents[1] / 'scripts' / 'soak_metrics.py')
sampler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sampler)


def value(pid, created):
    return {'pid': pid, 'created': str(created), 'workingSetBytes': 1, 'cpuSeconds': 0}


class TreeIdentityTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows handle query')
    def test_real_current_process_identity_is_observed_and_reused_identity_rejected(self):
        reader = sampler.WindowsReader()
        current = reader.query(os.getpid())
        self.assertGreater(current['workingSetBytes'], 0)
        alive, unknown = sampler.select_known([current], reader.query)
        self.assertEqual([v['pid'] for v in alive], [os.getpid()])
        self.assertEqual(unknown, [])
        mismatch = {**current, 'created': str(int(current['created']) + 1)}
        self.assertEqual(sampler.select_known([mismatch], reader.query), ([], []))
        rows, cutoff = reader.snapshot()
        selected, _, _ = sampler.select_tree(rows, os.getpid(), reader.query, cutoff)
        self.assertTrue(all(int(v['created']) >= int(current['created']) for v in selected))

    def test_reused_parent_pid_does_not_adopt_older_process_or_its_children(self):
        values = {10: value(10, 100), 20: value(20, 50), 30: value(30, 60), 40: value(40, 110)}
        selected, rejected, _ = sampler.select_tree([(20, 10), (30, 20), (40, 10)], 10, values.get, 200)
        self.assertEqual({v['pid'] for v in selected}, {10, 40})
        self.assertEqual(rejected, 1)

    def test_pid_created_after_snapshot_is_deferred(self):
        values = {10: value(10, 100), 20: value(20, 201)}
        selected, rejected, _ = sampler.select_tree([(20, 10)], 10, values.get, 200)
        self.assertEqual([v['pid'] for v in selected], [10])
        self.assertEqual(rejected, 1)

    def test_missing_root_is_not_zero_usage(self):
        with self.assertRaises(ValueError):
            sampler.select_tree([], 10, lambda _: None, 200)

    def test_orphan_is_still_found_by_its_recorded_identity(self):
        orphan = value(20, 110)
        alive, unknown = sampler.select_known([orphan], lambda pid: orphan)
        self.assertEqual(alive, [orphan])
        self.assertEqual(unknown, [])

    def test_reused_numeric_pid_is_not_the_recorded_process(self):
        alive, unknown = sampler.select_known([value(20, 110)], lambda _: value(20, 999))
        self.assertEqual(alive, [])
        self.assertEqual(unknown, [])

    def test_access_failure_is_unknown_instead_of_claiming_exit(self):
        def unavailable(_):
            raise OSError('access denied')
        alive, unknown = sampler.select_known([value(20, 110)], unavailable)
        self.assertEqual(alive, [])
        self.assertEqual(len(unknown), 1)


if __name__ == '__main__':
    unittest.main()
