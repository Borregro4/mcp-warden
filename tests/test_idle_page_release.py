"""Opt-in release may touch only this proxy after a clean, complete idle stop."""
import ctypes
import importlib.util
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('test_idle_release', ROOT / 'runtime' / 'idle_stdio.py')
idle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(idle)


class PageReleaseTests(unittest.TestCase):
    def bridge(self, **changes):
        bridge = idle.IdleBridge({'command': sys.executable,
            'idle_session_timeout_seconds': .1, **changes})
        bridge.proc = object()
        bridge.ready = True
        bridge.state = 'active'
        bridge.started_at = bridge.last_activity = time.monotonic() - 1
        return bridge

    def test_option_requires_boolean_and_defaults_off(self):
        self.assertFalse(self.bridge().release_pages_on_idle)
        for invalid in (None, 0, 1, 'true', [], {}):
            with self.subTest(invalid=repr(invalid)), self.assertRaisesRegex(ValueError, 'invalid_configuration'):
                self.bridge(release_idle_pages=invalid)

    def test_release_runs_after_successful_stop_only_when_explicitly_enabled(self):
        for option in (False, True):
            bridge = self.bridge(release_idle_pages=option)
            order = []

            def stopped():
                order.append('stop')
                bridge.proc = None

            def released():
                self.assertIsNone(bridge.proc)
                self.assertEqual(bridge.state, 'sleeping')
                order.append('release')
                return False  # API failure still leaves a usable sleeping session.

            with patch.object(bridge, 'stop_backend', side_effect=stopped), \
                    patch.object(bridge, '_release_own_idle_pages', side_effect=released):
                bridge.poll_idle()
            self.assertEqual(order, ['stop', 'release'] if option else ['stop'])
            self.assertEqual(bridge.state, 'sleeping')
            self.assertFalse(bridge.disconnect)

    def test_pending_rpc_callback_write_fragment_or_queued_event_prevents_release(self):
        for condition in ('rpc', 'callback', 'write', 'partial', 'event', 'handshake', 'replay'):
            with self.subTest(condition=condition):
                bridge = self.bridge(release_idle_pages=True)
                if condition == 'rpc':
                    bridge.client_pending['fixture'] = 1
                elif condition == 'callback':
                    bridge.server_pending['fixture'] = 1
                elif condition == 'write':
                    bridge.writes[1] = time.monotonic()
                elif condition == 'partial':
                    bridge.partial[('client', None)] = time.monotonic()
                elif condition == 'event':
                    bridge._events.put(('client', None, b'fixture'))
                elif condition == 'handshake':
                    bridge.ready = False
                else:
                    bridge.state = 'replaying'
                with patch.object(bridge, 'stop_backend') as stop, \
                        patch.object(bridge, '_release_own_idle_pages') as release:
                    bridge.poll_idle()
                stop.assert_not_called()
                release.assert_not_called()

    def test_uncertain_cancelled_session_and_shutdown_failure_never_release_pages(self):
        bridge = self.bridge(release_idle_pages=True)
        bridge.uncertain = True
        with patch.object(bridge, 'stop_backend'), patch.object(bridge, '_release_own_idle_pages') as release:
            bridge.poll_idle()
        release.assert_not_called()
        bridge = self.bridge(release_idle_pages=True)
        with patch.object(bridge, 'stop_backend', side_effect=OSError('fixture')), \
                patch.object(bridge, '_release_own_idle_pages') as release:
            with self.assertRaises(OSError):
                bridge.poll_idle()
        release.assert_not_called()

    def test_native_api_targets_only_current_process_and_failures_are_best_effort(self):
        kernel = Mock()
        kernel.GetCurrentProcess.return_value = 0xFFFFFFFFFFFFFFFF
        kernel.K32EmptyWorkingSet.return_value = 1
        with patch.object(ctypes, 'WinDLL', return_value=kernel, create=True) as load:
            self.assertTrue(idle.IdleBridge._release_own_idle_pages())
        load.assert_called_once_with('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.assert_called_once_with()
        kernel.K32EmptyWorkingSet.assert_called_once_with(kernel.GetCurrentProcess.return_value)
        kernel.K32EmptyWorkingSet.return_value = 0
        with patch.object(ctypes, 'WinDLL', return_value=kernel, create=True):
            self.assertFalse(idle.IdleBridge._release_own_idle_pages())
        for failure in (OSError('unavailable'), AttributeError('unavailable')):
            with patch.object(ctypes, 'WinDLL', side_effect=failure, create=True):
                self.assertFalse(idle.IdleBridge._release_own_idle_pages())


if __name__ == '__main__':
    unittest.main()
