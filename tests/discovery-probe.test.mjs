import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import net from 'node:net';
import { once } from 'node:events';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { Gateway } from '../runtime/gateway.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
for (const mode of ['gateway', 'stdio']) {
  test(`modern discovery probe falls back to legacy initialize on ${mode}`,
    { skip: process.platform !== 'win32', timeout: 12000 }, async t => {
      const python = spawnSync(process.env.PYTHON ?? 'python', ['-I', '-c', 'import sys;print(sys.executable)'],
        { encoding: 'utf8', windowsHide: true }).stdout.trim();
      const directory = await mkdtemp(path.join(tmpdir(), 'warden-discover-'));
      const state = path.join(directory, 'state');
      const reserve = net.createServer().listen(0, '127.0.0.1');
      await once(reserve, 'listening');
      const port = reserve.address().port;
      await new Promise(resolve => reserve.close(resolve));
      const setup = spawnSync(python, ['-I', '-B', path.join(ROOT, 'warden.py'), 'init', '--state', state,
        '--port', String(port), '--transport', mode], { encoding: 'utf8', windowsHide: true });
      assert.equal(setup.status, 0, setup.stderr);
      const gateway = mode === 'gateway' ? new Gateway(JSON.parse(await readFile(path.join(state, 'config.json'), 'utf8'))) : null;
      if (gateway) await gateway.listen();
      const transport = new StdioClientTransport({ command: python,
        args: ['-I', '-B', '-u', path.join(ROOT, 'warden.py'), 'connect', '--state', state, '--profile', 'example'],
        cwd: directory, stderr: 'pipe' });
      transport.stderr.on('data', () => {});
      t.after(async () => { await transport.close(); await gateway?.close(); });
      const pending = new Map();
      transport.onmessage = message => pending.get(message.id)?.(message);
      const request = message => new Promise((resolve, reject) => {
        const timer = setTimeout(() => { pending.delete(message.id); reject(new Error('Probe fixture timeout')); }, 7000);
        pending.set(message.id, answer => { clearTimeout(timer); pending.delete(message.id); resolve(answer); });
        transport.send(message).catch(reject);
      });
      await transport.start();
      const probe = await request({ jsonrpc: '2.0', id: 'server-discover-probe-1', method: 'server/discover',
        params: { _meta: { 'io.modelcontextprotocol/protocolVersion': '2026-07-28' } } });
      assert.equal(probe.id, 'server-discover-probe-1');
      assert.equal(probe.error.code, -32601);
      if (gateway) assert.equal(gateway.active.size, 0);
      const initialized = await request({ jsonrpc: '2.0', id: 1, method: 'initialize',
        params: { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'probe-client', version: '1' } } });
      assert.equal(initialized.result.serverInfo.name, 'mcp-warden-example');
      await transport.send({ jsonrpc: '2.0', method: 'notifications/initialized' });
      const echo = await request({ jsonrpc: '2.0', id: 2, method: 'tools/call',
        params: { name: 'echo', arguments: { value: 'discovery-fallback-worked' } } });
      assert.equal(echo.result.structuredContent.value, 'discovery-fallback-worked');
      assert.ok(echo.result.structuredContent.pid > 0);
    });
}
