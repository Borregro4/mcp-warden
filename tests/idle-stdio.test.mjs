import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { ListRootsRequestSchema, ToolListChangedNotificationSchema } from '@modelcontextprotocol/sdk/types.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const WINDOWS = process.platform === 'win32';
const python = WINDOWS ? spawnSync(process.env.PYTHON ?? 'python', ['-I', '-c', 'import sys;print(sys.executable)'],
  { encoding: 'utf8', windowsHide: true }).stdout.trim() : 'python';

async function waitFor(check, timeout = 5000) {
  const end = performance.now() + timeout;
  do { if (await check()) return; await delay(25); } while (performance.now() < end);
  assert.fail('Timed out waiting for synthetic stdio lifecycle');
}
async function setup(t, changes = {}, rootsHandler = async () => ({ roots: [] })) {
  const directory = await mkdtemp(path.join(tmpdir(), 'warden-idle-stdio-'));
  const ledger = path.join(directory, 'ledger.jsonl');
  const { fixtureChange, fixtureSkipCatalog, ...manifestChanges } = changes;
  const manifest = { command: python, args: ['-I', '-B', '-u', path.join(ROOT, 'tests', 'idle_backend.py'), '--ledger', ledger,
    ...(fixtureChange ? ['--change-on-restart', fixtureChange] : [])],
    idle_session_timeout_seconds: .4, shutdown_grace_seconds: .1, cleanup_scope: 'tree', ...manifestChanges };
  const manifestFile = path.join(directory, 'manifest.json');
  await writeFile(manifestFile, JSON.stringify(manifest));
  const transport = new StdioClientTransport({ command: python,
    args: ['-I', '-B', '-u', path.join(ROOT, 'runtime', 'idle_stdio.py'), '--manifest', manifestFile],
    cwd: directory, stderr: 'pipe' });
  let stderr = '';
  transport.stderr.on('data', data => { stderr = (stderr + data.toString()).slice(-8192); });
  const client = new Client({ name: 'idle-stdio-test', version: '1' }, { capabilities: { roots: {} } });
  client.setRequestHandler(ListRootsRequestSchema, rootsHandler);
  client.setNotificationHandler(ToolListChangedNotificationSchema, () => {});
  t.after(async () => { await client.close(); });
  await client.connect(transport, { timeout: 8000 });
  if (!fixtureSkipCatalog) await client.listTools();
  const rows = async () => {
    try { return (await readFile(ledger, 'utf8')).trim().split('\n').flatMap(line => { try { return [JSON.parse(line)]; } catch { return []; } }); }
    catch (error) { if (error.code === 'ENOENT') return []; throw error; }
  };
  const alive = (pid, created) => {
    const result = spawnSync(python, ['-I', '-B', path.join(ROOT, 'tests', 'idle_process_probe.py'), String(pid), created],
      { encoding: 'utf8', windowsHide: true, timeout: 3000 });
    assert.equal(result.status, 0);
    return JSON.parse(result.stdout).alive;
  };
  return { client, transport, rows, alive, stderr: () => stderr };
}

test('light stdio stateless wakes cleanly across generations without replaying prior calls',
  { skip: !WINDOWS, timeout: 20000 }, async t => {
    const fixture = await setup(t, { restart_policy: 'stateless' });
    const first = await fixture.client.callTool({ name: 'echo', arguments: { value: 'original-once' } });
    for (let i = 0; i < 3; i++) {
      const rows = await fixture.rows();
      const latest = rows.filter(row => row.kind === 'start').at(-1);
      await waitFor(() => !fixture.alive(latest.pid, latest.created));
      const next = await fixture.client.callTool({ name: 'echo', arguments: { value: `wake-once-${i}` } });
      assert.equal(next.structuredContent.value, `wake-once-${i}`);
      assert.notEqual(next.structuredContent.pid, latest.pid);
    }
    const rows = await fixture.rows();
    assert.equal(rows.filter(row => row.kind === 'initialize').length, 4);
    assert.equal(rows.filter(row => row.kind === 'call' && row.value === 'original-once').length, 1);
    for (let i = 0; i < 3; i++) assert.equal(rows.filter(row => row.kind === 'call' && row.value === `wake-once-${i}`).length, 1);
    assert.equal(first.structuredContent.value, 'original-once');
    assert.equal(fixture.stderr(), '');
  });

test('light stdio default stateful expiry returns explicit error and executes no stale call',
  { skip: !WINDOWS, timeout: 10000 }, async t => {
    const fixture = await setup(t);
    await fixture.client.callTool({ name: 'echo', arguments: { value: 'before-expiry' } });
    const start = (await fixture.rows()).find(row => row.kind === 'start');
    await waitFor(() => !fixture.alive(start.pid, start.created));
    await assert.rejects(fixture.client.callTool({ name: 'echo', arguments: { value: 'must-not-execute' } }),
      error => error.code === -32000 && /Reconnect the client/.test(error.message));
    assert.equal((await fixture.rows()).filter(row => row.kind === 'start').length, 1);
    assert.equal((await fixture.rows()).filter(row => row.value === 'must-not-execute').length, 0);
  });

test('light stdio optional own-page release keeps clean stateless wake usable',
  { skip: !WINDOWS, timeout: 10000 }, async t => {
    const fixture = await setup(t, { restart_policy: 'stateless', release_idle_pages: true });
    const first = await fixture.client.callTool({ name: 'echo', arguments: { value: 'before-page-release' } });
    const start = (await fixture.rows()).find(row => row.kind === 'start');
    await waitFor(() => !fixture.alive(start.pid, start.created));
    const next = await fixture.client.callTool({ name: 'echo', arguments: { value: 'after-page-release' } });
    assert.notEqual(next.structuredContent.pid, first.structuredContent.pid);
    assert.equal(next.structuredContent.value, 'after-page-release');
    const rows = await fixture.rows();
    for (const value of ['before-page-release', 'after-page-release'])
      assert.equal(rows.filter(row => row.kind === 'call' && row.value === value).length, 1);
    assert.equal(fixture.stderr(), '');
  });

test('light stdio stateless restart revalidates instructions and approved tool metadata before a new call',
  { skip: !WINDOWS, timeout: 20000 }, async t => {
    for (const fixtureChange of ['annotations', 'schema', 'instructions', 'pagination']) {
      await t.test(fixtureChange, async sub => {
        const fixture = await setup(sub, { restart_policy: 'stateless', fixtureChange });
        await fixture.client.callTool({ name: 'echo', arguments: { value: 'before-change' } });
        const first = (await fixture.rows()).find(row => row.kind === 'start');
        await waitFor(() => !fixture.alive(first.pid, first.created));
        await assert.rejects(fixture.client.callTool({ name: 'echo', arguments: { value: 'held-must-not-execute' } }),
          error => error.code === -32000 && /Reconnect the client/.test(error.message));
        const rows = await fixture.rows();
        assert.equal(rows.filter(row => row.kind === 'start').length, fixtureChange === 'pagination' ? 1 : 2);
        assert.equal(rows.filter(row => row.kind === 'initialize').length, fixtureChange === 'pagination' ? 1 : 2);
        assert.equal(rows.filter(row => row.kind === 'call' && row.value === 'held-must-not-execute').length, 0);
        if (fixtureChange === 'annotations' || fixtureChange === 'schema')
          assert.equal(rows.filter(row => row.kind === 'catalog').length, 2);
      });
    }
  });

test('light stdio stateless auto-wake requires an observed catalog even if the client cached tools',
  { skip: !WINDOWS, timeout: 10000 }, async t => {
    const fixture = await setup(t, { restart_policy: 'stateless', fixtureSkipCatalog: true });
    assert.equal((await fixture.client.callTool({ name: 'echo', arguments: { value: 'initial-call' } })).structuredContent.value,
      'initial-call');
    const start = (await fixture.rows()).find(row => row.kind === 'start');
    await waitFor(() => !fixture.alive(start.pid, start.created));
    await assert.rejects(fixture.client.callTool({ name: 'echo', arguments: { value: 'held-without-discovery' } }),
      error => error.code === -32000 && /requires previously observed tool discovery/.test(error.message));
    const rows = await fixture.rows();
    assert.equal(rows.filter(row => row.kind === 'start').length, 1);
    assert.equal(rows.filter(row => row.kind === 'catalog').length, 0);
    assert.equal(rows.filter(row => row.value === 'held-without-discovery').length, 0);
  });

test('light stdio backend write timeout remains observable when the backend does not read',
  { skip: !WINDOWS, timeout: 10000 }, async t => {
    const fixture = await setup(t, { restart_policy: 'stateless', backend_write_timeout_seconds: .2 });
    await fixture.client.callTool({ name: 'stop_reading', arguments: {} });
    await assert.rejects(fixture.client.callTool({ name: 'echo', arguments: { value: 'blocked-write-' + 'Z'.repeat(128 * 1024) } }),
      error => error.code === -32000 && /backend write timed out/.test(error.message));
    const rows = await fixture.rows();
    assert.equal(rows.filter(row => row.kind === 'start').length, 1);
    assert.equal(rows.some(row => row.name === 'echo'), false);
    assert.equal(fixture.stderr().includes('ZZZ'), false);
  });

test('light stdio client EOF is processed during a blocked backend write',
  { skip: !WINDOWS, timeout: 10000 }, async t => {
    for (const cleanup_scope of ['tree', 'process']) {
      await t.test(cleanup_scope, async sub => {
        const fixture = await setup(sub, { backend_write_timeout_seconds: 5, cleanup_scope });
        await fixture.client.callTool({ name: 'stop_reading', arguments: {} });
        const proxy = fixture.transport._process;
        const outcome = fixture.client.callTool({ name: 'echo', arguments: { value: 'blocked-eof-' + 'Q'.repeat(128 * 1024) } })
          .then(() => ({ completed: true }), () => ({ completed: false }));
        await delay(80);
        const began = performance.now();
        await fixture.client.close();
        assert.ok(performance.now() - began < 1500);
        assert.equal(proxy.exitCode, 0, 'proxy must exit cooperatively before SDK forced kill');
        assert.equal((await outcome).completed, false);
        assert.equal((await fixture.rows()).some(row => row.name === 'echo'), false);
      });
    }
  });

test('light stdio long RPC and equal-ID reverse callback survive idle deadline',
  { skip: !WINDOWS, timeout: 10000 }, async t => {
    let callbacks = 0;
    const fixture = await setup(t, {}, async () => { callbacks++; await delay(850); return { roots: [] }; });
    const first = await fixture.client.callTool({ name: 'echo', arguments: { value: 'same-process' } });
    const long = await fixture.client.callTool({ name: 'sleep', arguments: { seconds: .9, value: 'long' } });
    assert.equal(long.structuredContent.pid, first.structuredContent.pid);
    const callback = await fixture.client.callTool({ name: 'same_id_callback', arguments: {} });
    assert.equal(callback.structuredContent.pid, first.structuredContent.pid);
    assert.equal(callback.structuredContent.value, 'callback-completed');
    assert.equal(callbacks, 1);
    assert.equal((await fixture.rows()).filter(row => row.kind === 'start').length, 1);
  });

test('light stdio keeps numeric and string request IDs distinct',
  { skip: !WINDOWS, timeout: 10000 }, async t => {
    const fixture = await setup(t);
    const first = await fixture.client.callTool({ name: 'echo', arguments: { value: 'before-typed-ids' } });
    const messages = [];
    const receive = fixture.transport.onmessage;
    fixture.transport.onmessage = message => {
      if (!('method' in message) && [1, '1'].some(id => id === message.id)) messages.push(message);
      else receive(message);
    };
    for (const [id, seconds] of [[1, .65], ['1', .5]]) {
      await fixture.transport.send({ jsonrpc: '2.0', id, method: 'tools/call',
        params: { name: 'sleep', arguments: { seconds, value: typeof id + ':' + id } } });
    }
    await waitFor(() => messages.length === 2);
    assert.deepEqual(new Set(messages.map(message => typeof message.id + ':' + message.id)),
      new Set(['number:1', 'string:1']));
    for (const message of messages) {
      assert.equal(message.result.structuredContent.pid, first.structuredContent.pid);
      assert.equal(message.result.structuredContent.value, typeof message.id + ':' + message.id);
    }
    fixture.transport.onmessage = receive;
    assert.equal((await fixture.client.callTool({ name: 'echo', arguments: { value: 'after-typed-ids' } })).structuredContent.pid,
      first.structuredContent.pid);
  });

test('light stdio rejects fractional MCP request IDs before backend execution',
  { skip: !WINDOWS, timeout: 5000 }, async t => {
    const fixture = await setup(t);
    let resolveClosed;
    const closed = new Promise(resolve => { resolveClosed = resolve; });
    const previous = fixture.transport.onclose;
    fixture.transport.onclose = () => { previous?.(); resolveClosed(); };
    await fixture.transport.send({ jsonrpc: '2.0', id: 1.5, method: 'tools/call',
      params: { name: 'echo', arguments: { value: 'fraction-must-not-execute' } } });
    await closed;
    assert.equal((await fixture.rows()).some(row => row.value === 'fraction-must-not-execute'), false);
    assert.match(fixture.stderr(), /invalid_request_id/);
  });

test('light stdio standalone callback holds backend with no client RPC outstanding',
  { skip: !WINDOWS, timeout: 10000 }, async t => {
    let started, answered;
    const callbackStarted = new Promise(resolve => { started = resolve; });
    const callbackAnswered = new Promise(resolve => { answered = resolve; });
    const fixture = await setup(t, {}, async () => { started(); await delay(850); answered(); return { roots: [] }; });
    const first = await fixture.client.callTool({ name: 'notify_callback', arguments: {} });
    await callbackStarted;
    const start = (await fixture.rows()).find(row => row.kind === 'start');
    await delay(600);
    assert.equal(fixture.alive(start.pid, start.created), true);
    await callbackAnswered;
    const next = await fixture.client.callTool({ name: 'echo', arguments: { value: 'after-callback' } });
    assert.equal(next.structuredContent.pid, first.structuredContent.pid);
    await waitFor(async () => (await fixture.rows()).some(row => row.kind === 'client_response'));
    assert.equal((await fixture.rows()).filter(row => row.kind === 'callback').length, 1);
  });

test('light stdio backend notifications refresh idle activity',
  { skip: !WINDOWS, timeout: 10000 }, async t => {
    const fixture = await setup(t);
    const first = await fixture.client.callTool({ name: 'notify_for', arguments: {} });
    await waitFor(async () => (await fixture.rows()).filter(row => row.kind === 'notification').length === 5);
    const next = await fixture.client.callTool({ name: 'echo', arguments: { value: 'after-notices' } });
    assert.equal(next.structuredContent.pid, first.structuredContent.pid);
  });

test('light stdio partially arriving client frame holds the existing backend past idle',
  { skip: !WINDOWS, timeout: 10000 }, async t => {
    const fixture = await setup(t, { partial_message_timeout_seconds: 2 });
    const first = await fixture.client.callTool({ name: 'echo', arguments: { value: 'before-slow-frame' } });
    const send = fixture.transport.send.bind(fixture.transport);
    fixture.transport.send = async message => {
      if (message.method !== 'tools/call') return send(message);
      const raw = Buffer.from(JSON.stringify(message) + '\n');
      const pipe = fixture.transport._process.stdin; // Test-only split of SDK serialization.
      await new Promise((resolve, reject) => pipe.write(raw.subarray(0, 1), error => error ? reject(error) : resolve()));
      await delay(850);
      await new Promise((resolve, reject) => pipe.write(raw.subarray(1), error => error ? reject(error) : resolve()));
    };
    const next = await fixture.client.callTool({ name: 'echo', arguments: { value: 'after-slow-frame' } });
    assert.equal(next.structuredContent.pid, first.structuredContent.pid);
    assert.equal(next.structuredContent.value, 'after-slow-frame');
  });

test('light stdio partially arriving backend notification holds its generation past idle',
  { skip: !WINDOWS, timeout: 10000 }, async t => {
    const fixture = await setup(t, { partial_message_timeout_seconds: 2 });
    const first = await fixture.client.callTool({ name: 'slow_notification', arguments: {} });
    await waitFor(async () => (await fixture.rows()).some(row => row.kind === 'slow_notification_complete'));
    const next = await fixture.client.callTool({ name: 'echo', arguments: { value: 'after-slow-notice' } });
    assert.equal(next.structuredContent.pid, first.structuredContent.pid);
  });

test('light stdio incomplete frame has a bounded timeout even before initialization',
  { skip: !WINDOWS, timeout: 5000 }, async t => {
    const directory = await mkdtemp(path.join(tmpdir(), 'warden-partial-frame-'));
    const manifestFile = path.join(directory, 'manifest.json');
    const ledger = path.join(directory, 'ledger.jsonl');
    await writeFile(manifestFile, JSON.stringify({ command: python,
      args: ['-I', '-B', '-u', path.join(ROOT, 'tests', 'idle_backend.py'), '--ledger', ledger],
      partial_message_timeout_seconds: .2 }));
    const child = spawn(python, ['-I', '-B', '-u', path.join(ROOT, 'runtime', 'idle_stdio.py'), '--manifest', manifestFile],
      { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
    t.after(() => { if (child.exitCode === null) child.kill(); });
    let stderr = '';
    child.stderr.on('data', chunk => { stderr += chunk.toString(); });
    child.stdin.on('error', () => {});
    child.stdin.write('{');
    const [code] = await once(child, 'exit');
    assert.equal(code, 2);
    assert.match(stderr, /partial_message_timeout/);
    assert.equal(stderr.includes('Fatal Python'), false);
    await assert.rejects(readFile(ledger, 'utf8'), error => error.code === 'ENOENT');
  });

test('light stdio cancelled unknown work is never automatically resumed even with stateless policy',
  { skip: !WINDOWS, timeout: 10000 }, async t => {
    const fixture = await setup(t, { restart_policy: 'stateless' });
    const abort = new AbortController();
    const pending = fixture.client.callTool({ name: 'cancel_wait', arguments: { value: 'cancelled-once' } }, undefined, { signal: abort.signal });
    const rejected = assert.rejects(pending);
    await waitFor(async () => (await fixture.rows()).some(row => row.name === 'cancel_wait'));
    abort.abort();
    await rejected;
    await waitFor(async () => (await fixture.rows()).some(row => row.kind === 'cancelled'));
    const start = (await fixture.rows()).find(row => row.kind === 'start');
    await waitFor(() => !fixture.alive(start.pid, start.created));
    await assert.rejects(fixture.client.callTool({ name: 'echo', arguments: { value: 'after-unknown' } }), /Reconnect the client/);
    const rows = await fixture.rows();
    assert.equal(rows.filter(row => row.name === 'cancel_wait').length, 1);
    assert.equal(rows.filter(row => row.value === 'after-unknown').length, 0);
  });

test('light stdio rejects oversized backend and client frames without echoing them',
  { skip: !WINDOWS, timeout: 10000 }, async t => {
    await t.test('backend frame', async sub => {
      const fixture = await setup(sub, { max_rpc_line_bytes: 4096 });
      await assert.rejects(fixture.client.callTool({ name: 'oversized', arguments: {} }));
      assert.equal(fixture.stderr().includes('XXX'), false);
    });
    await t.test('client frame', async sub => {
      const fixture = await setup(sub, { max_rpc_line_bytes: 4096 });
      await assert.rejects(fixture.client.callTool({ name: 'echo', arguments: { value: 'Y'.repeat(6000) } }));
      assert.equal((await fixture.rows()).some(row => row.name === 'echo'), false);
      assert.equal(fixture.stderr().includes('YYY'), false);
    });
  });

test('light stdio process-only scope preserves shared child while tree scope cleans it',
  { skip: !WINDOWS, timeout: 15000 }, async t => {
    for (const scope of ['tree', 'process']) {
      await t.test(scope, async sub => {
        const fixture = await setup(sub, { cleanup_scope: scope, idle_session_timeout_seconds: 0 });
        const response = await fixture.client.callTool({ name: 'spawn_child', arguments: {} });
        const child = response.structuredContent.value;
        assert.equal(fixture.alive(child.child, child.created), true);
        await fixture.client.close();
        if (scope === 'tree') await waitFor(() => !fixture.alive(child.child, child.created), 1500);
        else assert.equal(fixture.alive(child.child, child.created), true);
        // The synthetic shared child self-exits after three seconds. No PID kill.
        await waitFor(() => !fixture.alive(child.child, child.created), 4500);
      });
    }
  });
