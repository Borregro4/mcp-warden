"""Probe exactly one synthetic PID and birth time; never kill a process."""
import importlib.util
import json
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location('idle_probe_owned',
    Path(__file__).resolve().parents[1] / 'runtime' / 'owned_bridge.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
try:
    process = module.PinnedProcess(int(sys.argv[1]), sys.argv[2])
except ValueError:
    print(json.dumps({'alive': False}))
else:
    try:
        print(json.dumps({'alive': process.alive()}))
    finally:
        process.close()
