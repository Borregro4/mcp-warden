"""Prepare a private filesystem workload; never calls a model or installs packages."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import uuid

ROOT = Path(__file__).resolve().parents[1]


def prepare(out, filesystem_entry, node, smoke=False, release_idle_pages=False):
    out = Path(out).resolve()
    entry = Path(filesystem_entry).resolve()
    node = Path(node).resolve()
    if out == ROOT or ROOT in out.parents or not entry.is_file() or not node.is_file():
        raise ValueError('private output must be outside repository; explicit existing backend/runtime required')
    out.mkdir(parents=True, exist_ok=False)
    workspace = out / 'workspace'
    (workspace / 'data').mkdir(parents=True)
    (workspace / 'results').mkdir()
    batch = 'batch-' + uuid.uuid4().hex
    orders = {'batch_id': batch, 'orders': [
        {'id': 'A1', 'quantity': 2, 'unit_cents': 1250, 'status': 'paid'},
        {'id': 'A2', 'quantity': 1, 'unit_cents': 499, 'status': 'cancelled'},
        {'id': 'A3', 'quantity': 3, 'unit_cents': 200, 'status': 'paid'},
        {'id': 'A4', 'quantity': 1, 'unit_cents': 850, 'status': 'paid'}]}
    policy = {'free_shipping_threshold_cents': 4000, 'threshold_comparison': 'gt', 'shipping_fee_cents': 500}
    revised_policy = {**policy, 'threshold_comparison': 'ge'}
    adjustments = {'adjustments': [{'order_id': 'A1', 'delta_cents': -250}, {'order_id': 'A4', 'delta_cents': 100}]}

    def save(path, value):
        path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')

    save(workspace / 'data/orders.json', orders)
    save(workspace / 'data/shipping-policy.json', policy)
    save(workspace / 'data/late-adjustments.json', adjustments)
    seed_checks = [{'path': 'data/' + name, 'sha256': hashlib.sha256((workspace/'data'/name).read_bytes()).hexdigest()}
                   for name in ('orders.json', 'late-adjustments.json')]
    baseline = {'batch_id': batch, 'paid_order_count': 3, 'paid_units': 6,
                'subtotal_cents': 3950, 'shipping_cents': 500, 'total_cents': 4450}
    boundary = {'below_threshold_shipping_cents': 500, 'at_threshold_shipping_cents': 0,
                'above_threshold_shipping_cents': 0}
    adjusted = {'batch_id': batch, 'paid_order_ids': ['A1', 'A3', 'A4'],
                'adjustment_cents': -150, 'adjusted_subtotal_cents': 3800,
                'shipping_cents': 500, 'total_cents': 4300}
    final = {'batch_id': batch, 'paid_order_count': 3, 'adjusted_subtotal_cents': 3800,
             'shipping_cents': 500, 'total_cents': 4300, 'free_shipping_boundary': 'inclusive'}
    intro = ('Use only the warden-work MCP filesystem tools for this task. Do not use shell, web, '
             'built-in file tools, delegation or other MCP servers. Read the actual files now; '
             'do not invent tool results or reuse a remembered file instead of reading it. '
             f'The allowed workspace is {workspace}. All money is integer cents. ')
    read = ['read_text_file', 'read_multiple_files']
    turns = [
        {'id': 'paid-orders', 'prompt': intro +
         'Read data/orders.json and data/shipping-policy.json. Exclude cancelled orders. '
         'Calculate paid order count, total paid quantity, subtotal, shipping and total. '
         'Write results/baseline.json with exactly keys batch_id, paid_order_count, paid_units, '
         'subtotal_cents, shipping_cents, total_cents. Read it back and briefly report completion.',
         'required_tool_groups': [read, ['write_file']],
         'verify': [{'path': 'results/baseline.json', 'json_equals': baseline}, *seed_checks]},
        {'id': 'boundary-policy-fix', 'prompt': intro +
         'Read data/shipping-policy.json. The business rule is free shipping AT or above the threshold. '
         'Use edit_file to change only threshold_comparison from gt to ge; preserve all other values. '
         'Write results/boundary-check.json with exactly below_threshold_shipping_cents, '
         'at_threshold_shipping_cents, above_threshold_shipping_cents for subtotals 3999,4000,4001. '
         'Read the edited policy and report back.',
         'required_tool_groups': [read, ['edit_file'], ['write_file']],
         'verify': [{'path': 'data/shipping-policy.json', 'json_equals': revised_policy},
                    {'path': 'results/boundary-check.json', 'json_equals': boundary}, *seed_checks]},
        {'id': 'reconcile-adjustments', 'prompt': intro +
         'Use search_files to find the late adjustments JSON within data. Read the discovered file, '
         'the paid-orders baseline, original orders and current shipping policy. Apply each delta_cents '
         'once to the paid subtotal. Write results/adjusted.json with exactly batch_id, paid_order_ids '
         '(ascending), adjustment_cents, adjusted_subtotal_cents, shipping_cents, total_cents. '
         'Keep all original inputs and earlier result files.',
         'required_tool_groups': [['search_files'], read, ['write_file']],
         'verify': [{'path': 'results/adjusted.json', 'json_equals': adjusted},
                    {'path': 'results/baseline.json', 'json_equals': baseline}, *seed_checks]},
        {'id': 'final-audit', 'prompt': intro +
         'Read original orders, the current policy and all three earlier result files. '
         'Confirm the corrected boundary rule and reconciled total. Write results/final-summary.json '
         'with exactly batch_id, paid_order_count, adjusted_subtotal_cents, shipping_cents, total_cents, '
         'free_shipping_boundary (inclusive or exclusive). Read the summary back; do not modify earlier files.',
         'required_tool_groups': [read, ['write_file']],
         'verify': [{'path': 'results/final-summary.json', 'json_equals': final},
                    {'path': 'results/adjusted.json', 'json_equals': adjusted},
                    {'path': 'results/boundary-check.json', 'json_equals': boundary},
                    {'path': 'results/baseline.json', 'json_equals': baseline},
                    {'path': 'data/shipping-policy.json', 'json_equals': revised_policy}, *seed_checks]}
    ]
    spec = importlib.util.spec_from_file_location('prepare_workload_warden', ROOT / 'warden.py')
    warden = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(warden)
    state = out / 'state'
    config = warden.initialize(state, transport='stdio', restart_policy='stateless', idle_seconds=2 if smoke else 900)
    config['profiles'] = {'filesystem': {'transport': 'stdio', 'restart_policy': 'stateless',
        'manifest': {'command': str(node), 'args': [str(entry), str(workspace)], 'cwd': str(workspace),
                     'env': {}, 'cleanup_scope': 'tree', 'shutdown_grace_seconds': .5,
                     'release_idle_pages': bool(release_idle_pages),
                     'fingerprint_files': [str(entry), str(entry.parent.parent/'package.json'),
                                           str(entry.parents[4]/'package-lock.json')]}}}
    save(state / 'config.json', config)
    plan = {'version': 1, 'workspace': str(workspace), 'warden_state': str(state),
            'server_name': 'warden-work', 'profile': 'filesystem',
            'allowed_tools': ['read_text_file', 'read_multiple_files', 'list_directory', 'list_allowed_directories',
                              'search_files', 'edit_file', 'write_file'],
            'duration_seconds': 0 if smoke else 14400, 'turn_offsets_seconds': [0, 0] if smoke else [0, 4800, 9600, 14400],
            'minimum_idle_seconds': 5 if smoke else 900, 'turn_timeout_seconds': 240,
            'max_mcp_calls_per_turn': 16, 'turns': turns[:2] if smoke else turns,
            'backend': {'name': '@modelcontextprotocol/server-filesystem', 'version': '2.1.0',
                        'entry_sha256': hashlib.sha256(entry.read_bytes()).hexdigest()},
            'release_idle_pages': bool(release_idle_pages),
            'scope': 'Four-hour persistent native client; actual filesystem read/edit/reconciliation tasks; synthetic business data'}
    if smoke:
        plan['turn_offsets_seconds'] = [0, 5]
        plan['duration_seconds'] = 5
    save(out / 'plan.json', plan)
    return out / 'plan.json'


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', required=True, type=Path)
    p.add_argument('--filesystem-entry', required=True, type=Path)
    p.add_argument('--node', default=shutil.which('node'), type=Path)
    p.add_argument('--smoke', action='store_true')
    p.add_argument('--release-idle-pages', action='store_true')
    a = p.parse_args()
    print(prepare(a.out, a.filesystem_entry, a.node, a.smoke, a.release_idle_pages))
