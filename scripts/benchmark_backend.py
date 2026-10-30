"""Synthetic benchmark backend: shared example protocol plus resident allocation.

Only its explicitly requested private memory differs from the example backend.
This file does not read configuration, credentials, projects or network data.
"""
import argparse
import importlib.util
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--mb', type=int, default=32)
args = parser.parse_args()
if not 1 <= args.mb <= 128:
    parser.error('mb must be in 1..128')
resident = bytearray(args.mb * 1024 * 1024)
for offset in range(0, len(resident), 4096):
    resident[offset] = 1
spec = importlib.util.spec_from_file_location('benchmark_echo',
    Path(__file__).resolve().parents[1] / 'examples' / 'echo_server.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
raise SystemExit(module.main())
