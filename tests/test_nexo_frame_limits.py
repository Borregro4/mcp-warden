"""Bounded opt-in RPC frames for Nexo; synthetic bytes, no processes or service.

Nexo memory_original returns a CallToolResult whose own JSON is capped at six
MiB. Its SDK returns CallToolResult directly (without TextContent/structured
duplication), then serializes the JSON-RPC envelope once. This fixture includes
the result's duplicated small metadata and escaped filename inside that cap.
"""
from __future__ import annotations

import gc
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("nexo_frame_idle_stdio", ROOT / "runtime" / "idle_stdio.py")
idle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(idle)
MIB = 1024 * 1024


class Sink:
    generation = 1

    def __init__(self):
        self.events = []

    def put(self, event):
        self.events.append(event)
        return True


def bridge(limit=None):
    manifest = {"command": sys.executable, "cwd": str(ROOT)}
    if limit is not None:
        manifest["max_rpc_line_bytes"] = limit
    return idle.IdleBridge(manifest)


def exact_frame(size):
    prefix = b'{"jsonrpc":"2.0","id":1,"result":{"text":"'
    suffix = b'"}}\n'
    return prefix + b"A" * (size - len(prefix) - len(suffix)) + suffix


def read_events(instance, raw, source):
    sink = Sink()
    instance.read_stream(io.BytesIO(raw), sink, source)
    if instance.partial:
        raise AssertionError("reader left a partial-frame marker behind")
    return sink.events


class NexoFrameLimitTests(unittest.TestCase):
    def tearDown(self):
        gc.collect()

    def test_launch_policy_is_initialized_and_strictly_boolean(self):
        self.assertIs(bridge().windows_detached_console, False)
        self.assertIs(idle.IdleBridge({'command': sys.executable, 'windows_detached_console': True}).windows_detached_console, True)
        for invalid in (1, 'true', None):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    idle.IdleBridge({'command': sys.executable, 'windows_detached_console': invalid})

    def test_default_rejects_one_point_two_mib_in_both_directions(self):
        raw = exact_frame(6 * MIB // 5)
        for source in ("client", "backend"):
            with self.subTest(source=source):
                instance = bridge()
                self.assertEqual(instance.max_line_bytes, MIB)
                events = read_events(instance, raw, source)
                self.assertEqual(events, [(source + "_invalid", "message_too_large"),
                                          (source + "_eof", None)])

    def test_opt_in_forwards_one_point_two_mib_unchanged_in_both_directions(self):
        raw = exact_frame(6 * MIB // 5)
        for source in ("client", "backend"):
            with self.subTest(source=source):
                events = read_events(bridge(8 * MIB), raw, source)
                self.assertEqual(events, [(source, raw), (source + "_eof", None)])

    def test_six_mib_nexo_result_plus_wire_envelope_fits_opt_in(self):
        metadata = {"source_id": "synthetic", "filename": 'quote"-slash\\-ñ.bin',
                    "mime": "application/octet-stream", "inline": True}
        result = {"content": [{"type": "text", "text": json.dumps(metadata, ensure_ascii=False)},
                              {"type": "resource", "resource": {
                                  "uri": "memory://originals/synthetic", "mimeType": metadata["mime"], "blob": ""}}],
                  "structuredContent": metadata}
        encoded = lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        empty_size = len(encoded(result))
        # Valid base64 has a multiple-of-four length. Approach the result cap
        # within three bytes, rather than inventing an invalid original blob.
        result["content"][1]["resource"]["blob"] = "A" * ((6 * MIB - empty_size) // 4 * 4)
        result_json = encoded(result)
        self.assertLessEqual(len(result_json), 6 * MIB)
        self.assertGreaterEqual(len(result_json), 6 * MIB - 3)
        raw = b'{"jsonrpc":"2.0","id":"nexo-frame-fixture","result":' + result_json + b'}\n'
        self.assertGreater(len(raw), 6 * MIB)
        self.assertLess(len(raw), 8 * MIB)
        del result, result_json
        events = read_events(bridge(8 * MIB), raw, "backend")
        self.assertEqual(events, [("backend", raw), ("backend_eof", None)])

    def test_opt_in_rejects_frames_above_ceiling_with_or_without_newline(self):
        for source, complete in (("client", True), ("backend", False)):
            with self.subTest(source=source, complete=complete):
                raw = exact_frame(8 * MIB + 1) if complete else b"A" * (8 * MIB + 1)
                events = read_events(bridge(8 * MIB), raw, source)
                self.assertEqual(events, [(source + "_invalid", "message_too_large"),
                                          (source + "_eof", None)])
                del raw, events

    def test_limit_validation_retains_default_and_bounds_opt_in(self):
        self.assertEqual(bridge().max_line_bytes, MIB)
        self.assertEqual(bridge(8 * MIB).max_line_bytes, 8 * MIB)
        for invalid in (True, False, 1023, 8 * MIB + 1, float(8 * MIB), "8388608", None):
            with self.subTest(invalid=invalid):
                manifest = {"command": sys.executable, "cwd": str(ROOT), "max_rpc_line_bytes": invalid}
                with self.assertRaises(ValueError):
                    idle.IdleBridge(manifest)


if __name__ == "__main__":
    unittest.main()
