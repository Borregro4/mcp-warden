import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtemp, readFile } from 'node:fs/promises';
import path from 'node:path';
import { tmpdir } from 'node:os';
import net from 'node:net';
import { once } from 'node:events';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { ListRootsRequestSchema } from '@modelcontextprotocol/sdk/types.js';
import { Gateway } from '../runtime/gateway.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

async function freePort() {
  const server = net.createServer();
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

async function waitFor(predicate, timeoutMs = 8000) {
  const end = Date.now() + timeoutMs;
  do {
    if (await predicate()) return;
    await delay(30);
  } while (Date.now() < end);
  assert.fail('Timed out waiting for connector lifecycle condition');
}

test('Windows CLI connector forwards SDK MCP, reports usage, and never replays an expired call',
  { skip: process.platform !== 'win32', timeout: 25000 }, async t => {
    const python = spawnSync(process.env.PYTHON ?? 'python', ['-I', '-c', 'import sys; print(sys.executable)'],
      { encoding: 'utf8', windowsHide: true });
    assert.equal(python.status, 0, 'Python fixture runtime must be available');
    const state = await mkdtemp(path.join(tmpdir(), 'mcp-warden-connect-'));
    const port = await freePort();
    const init = spawnSync(python.stdout.trim(), ['-I', '-B', path.join(ROOT, 'warden.py'), 'init',
      '--state', state, '--port', String(port), '--idle-seconds', '0.6'],
    { encoding: 'utf8', windowsHide: true });
    assert.equal(init.status, 0, init.stderr);
    const token = (await readFile(path.join(state, 'token'), 'utf8')).trim();
    const config = JSON.parse(await readFile(path.join(state, 'config.json'), 'utf8'));
    const gateway = new Gateway(config);
    await gateway.listen();
    const transport = new StdioClientTransport({ command: python.stdout.trim(),
      args: ['-I', '-B', '-u', path.join(ROOT, 'warden.py'), 'connect', '--state', state, '--profile', 'example'],
      cwd: state, stderr: 'pipe' });
    let stderr = '';
    transport.stderr.on('data', data => { stderr += data.toString('utf8'); });
    const client = new Client({ name: 'mcp-warden-sdk-fixture', version: '1' }, { capabilities: { roots: {} } });
    let callbacks = 0;
    client.setRequestHandler(ListRootsRequestSchema, async () => {
      callbacks++;
      return { roots: [] };
    });
    t.after(async () => {
      await client.close();
      await gateway.close();
    });
    try {
      await client.connect(transport, { timeout: 8000 });
    } catch (error) {
      assert.equal(stderr, '', 'connector startup diagnostics: ' + stderr);
      throw error;
    }
    const tools = await client.listTools();
    assert.ok(tools.tools.some(tool => tool.name === 'echo'));
    const echo = await client.callTool({ name: 'echo', arguments: { value: 'through-sdk-stdio-http' } });
    assert.equal(echo.structuredContent.value, 'through-sdk-stdio-http');
    const callback = await client.callTool({ name: 'callback', arguments: {} });
    assert.equal(callback.structuredContent.value, 'callback-completed');
    assert.equal(callbacks, 1);
    assert.equal(callback.structuredContent.pid, echo.structuredContent.pid);

    const health = async () => {
      const response = await fetch(`http://127.0.0.1:${port}/_health`, { headers: { Authorization: 'Bearer ' + token } });
      assert.equal(response.status, 200);
      return response.json();
    };
    // Keep actual work in flight past the sampler's interval. No synthetic
    // metrics are injected: the Windows adapter observes its pinned handles.
    const long = client.callTool({ name: 'sleep', arguments: { seconds: 3, value: 'sampled' } });
    await waitFor(async () => (await health()).profiles.example.samples.some(sample => sample.sampledProcesses === 2));
    const measured = (await health()).profiles.example;
    assert.equal(measured.usageScope, 'bridge_and_direct_backend_only');
    const sample = measured.samples.find(value => value.sampledProcesses === 2);
    assert.ok(sample.workingSetBytes > 0);
    assert.ok(Number.isFinite(sample.cpuSeconds) && sample.cpuSeconds >= 0);
    assert.ok(Number.isFinite(sample.sampledAt) && sample.sampledAt > 0);
    assert.equal((await long).structuredContent.value, 'sampled');

    await waitFor(async () => (await health()).idleExpiredSessionCount === 1);
    await waitFor(async () => (await health()).sessionCount === 0);
    await assert.rejects(client.callTool({ name: 'echo', arguments: { value: 'must-not-be-replayed' } }),
      error => error.code === -32000 && error.message.includes('MCP session expired. Reconnect the client; temporary backend state was reset.'));
    const events = (await readFile(path.join(state, 'run', 'events.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse);
    assert.equal(events.filter(event => event.event === 'bridge_started').length, 1);
    assert.equal(stderr.includes(token), false);
    assert.equal(stderr.includes('must-not-be-replayed'), false);
    assert.equal(JSON.stringify(events).includes(token), false);
    assert.equal(JSON.stringify(events).includes('must-not-be-replayed'), false);
  });
