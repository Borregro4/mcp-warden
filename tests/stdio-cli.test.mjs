import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { ListRootsRequestSchema } from '@modelcontextprotocol/sdk/types.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const python = spawnSync(process.env.PYTHON ?? 'python', ['-I', '-c', 'import sys; print(sys.executable)'],
  { encoding: 'utf8', windowsHide: true }).stdout.trim();

async function prepare(t, policy = 'stateless') {
  const directory = await mkdtemp(path.join(tmpdir(), 'warden-stdio-cli-'));
  const state = path.join(directory, 'state');
  const result = spawnSync(python, ['-I', '-B', path.join(ROOT, 'warden.py'), 'init', '--state', state,
    '--transport', 'stdio', '--restart-policy', policy, '--idle-seconds', '0.5'], { encoding: 'utf8', windowsHide: true });
  assert.equal(result.status, 0, result.stderr);
  const transport = new StdioClientTransport({ command: python,
    args: ['-I', '-B', '-u', path.join(ROOT, 'warden.py'), 'connect', '--state', state, '--profile', 'example'],
    cwd: directory, stderr: 'pipe' });
  let stderr = '';
  transport.stderr.on('data', chunk => { stderr += chunk; });
  const client = new Client({ name: 'warden-lightweight-cli-test', version: '1' }, { capabilities: { roots: {} } });
  let callbacks = 0;
  client.setRequestHandler(ListRootsRequestSchema, async () => { callbacks++; return { roots: [] }; });
  t.after(() => client.close());
  await client.connect(transport, { timeout: 10000 });
  await client.listTools(); // Cache-aware callers expose the approval/schema baseline.
  const status = () => {
    const result = spawnSync(python, ['-I', '-B', path.join(ROOT, 'warden.py'), 'status', '--state', state,
      '--transport', 'stdio', '--json'], { encoding: 'utf8', windowsHide: true, timeout: 5000 });
    assert.equal(result.status, 0, result.stderr);
    return JSON.parse(result.stdout);
  };
  return { client, state, status, callbacks: () => callbacks, stderr: () => stderr, transport };
}

test('lightweight CLI: no HTTP server, live telemetry and stateless resume on the same connection',
  { skip: process.platform !== 'win32', timeout: 20000 }, async t => {
    const h = await prepare(t);
    const before = await h.client.callTool({ name: 'echo', arguments: { value: 'before-light-idle' } });
    const callback = await h.client.callTool({ name: 'callback', arguments: {} });
    assert.equal(callback.structuredContent.value, 'callback-completed');
    assert.equal(h.callbacks(), 1);
    const wait = h.client.callTool({ name: 'sleep', arguments: { seconds: 2.8, value: 'keep-active' } });
    await delay(2200);
    const active = h.status();
    assert.equal(active.transport, 'stdio');
    assert.equal(active.sessionCount, 1);
    assert.equal(active.samples[0].usageScope, 'proxy_and_direct_backend_only');
    assert.equal(active.samples[0].sampledProcesses, 2);
    assert.ok(active.samples[0].workingSetBytes > 0);
    assert.ok(active.samples[0].cpuSeconds >= 0);
    assert.equal((await wait).structuredContent.pid, before.structuredContent.pid);
    const connectorPid = h.transport.pid;
    await delay(1200);
    const after = await h.client.callTool({ name: 'echo', arguments: { value: 'after-light-idle' } });
    assert.equal(after.structuredContent.value, 'after-light-idle');
    assert.notEqual(after.structuredContent.pid, before.structuredContent.pid);
    assert.equal(h.transport.pid, connectorPid);
    assert.equal(h.stderr(), '');
    const config = JSON.parse(await readFile(path.join(h.state, 'config.json'), 'utf8'));
    assert.equal(config.profiles.example.transport, 'stdio');
    await h.client.close();
    assert.equal(h.status().sessionCount, 0);
  });

test('lightweight CLI keeps manual reconnection policy unless stateless was explicitly declared',
  { skip: process.platform !== 'win32', timeout: 10000 }, async t => {
    const h = await prepare(t, 'manual');
    await h.client.callTool({ name: 'echo', arguments: { value: 'stateful-session' } });
    await delay(1200);
    await assert.rejects(h.client.callTool({ name: 'echo', arguments: { value: 'do-not-replay' } }),
      error => error.message.includes('Reconnect the client'));
  });
