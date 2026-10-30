import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import net from 'node:net';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { randomBytes } from 'node:crypto';
import { mkdtemp, mkdir, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { Gateway, OWNER_ENV_KEYS } from '../runtime/gateway.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const IDLE_SECONDS = 0.6;

async function waitFor(predicate, timeoutMs = 7000) {
  const end = Date.now() + timeoutMs;
  do {
    if (await predicate()) return;
    await delay(25);
  } while (Date.now() < end);
  assert.fail('Timed out waiting for a lifecycle condition');
}

async function freePort() {
  const server = net.createServer();
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

async function createOwner() {
  const child = spawn(process.env.PYTHON ?? 'python', ['-I', '-B', '-u', path.join(ROOT, 'tests', 'owner.py')],
    { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
  let raw = '';
  const identity = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Owner fixture startup timeout')), 5000);
    child.once('error', reject);
    child.stdout.on('data', chunk => {
      raw += chunk;
      if (raw.includes('\n')) {
        clearTimeout(timer);
        try { resolve(JSON.parse(raw.split('\n')[0])); } catch (error) { reject(error); }
      }
    });
    child.once('exit', () => { clearTimeout(timer); if (!raw) reject(new Error('Owner fixture exited before readiness')); });
  });
  return { child, identity };
}

function exchange(port, headers, body, options = {}) {
  return new Promise((resolve, reject) => {
    const encoded = options.raw ?? (body == null ? null : JSON.stringify(body));
    const req = http.request({ hostname: '127.0.0.1', port, method: options.method ?? 'POST',
      path: options.path ?? '/mcp/example', headers: {
        ...headers, ...(encoded == null ? {} : { 'Content-Length': Buffer.byteLength(encoded) }), ...options.headers,
      } }, res => {
      const chunks = [];
      let pending = '';
      const messages = [];
      res.on('data', chunk => {
        chunks.push(chunk);
        pending += chunk.toString('utf8');
        while (pending.includes('\n')) {
          const split = pending.indexOf('\n');
          const line = pending.slice(0, split).trimEnd();
          pending = pending.slice(split + 1);
          if (line.startsWith('data:')) {
            const message = JSON.parse(line.slice(5).trim());
            messages.push(message);
            options.onMessage?.(message);
          }
        }
      });
      res.once('error', reject);
      res.once('end', () => {
        clearTimeout(timer);
        const text = Buffer.concat(chunks).toString('utf8');
        if ((res.headers['content-type'] ?? '').includes('application/json') && text) {
          try { messages.push(JSON.parse(text)); } catch { /* assertions inspect raw rejection body */ }
        }
        resolve({ status: res.statusCode, headers: res.headers, messages, text });
      });
    });
    const timer = setTimeout(() => req.destroy(new Error('HTTP fixture timeout')), options.timeoutMs ?? 7000);
    req.once('error', error => { clearTimeout(timer); reject(error); });
    req.end(encoded);
  });
}

class Client {
  constructor(harness, profile = 'example') {
    this.harness = harness;
    this.profile = profile;
    this.id = 0;
    this.session = null;
    this.callbackDelay = 0;
    this.callbackSeen = false;
    this.callbackTasks = [];
  }
  headers() {
    return { ...this.harness.headers, ...(this.session ? { 'Mcp-Session-Id': this.session } : {}) };
  }
  async post(body) {
    const reply = await exchange(this.harness.port, this.headers(), body, {
      path: '/mcp/' + this.profile,
      onMessage: message => this.onMessage(message),
    });
    if (reply.headers['mcp-session-id']) this.session = reply.headers['mcp-session-id'];
    return reply;
  }
  onMessage(message) {
    if (message.method === 'roots/list') {
      this.callbackSeen = true;
      this.callbackTasks.push((async () => {
        await delay(this.callbackDelay);
        const answer = await this.post({ jsonrpc: '2.0', id: message.id, result: { roots: [] } });
        assert.equal(answer.status, 202);
      })());
    }
  }
  async initialize() {
    const reply = await this.post({ jsonrpc: '2.0', id: ++this.id, method: 'initialize',
      params: { protocolVersion: '2025-06-18', capabilities: { roots: {} },
        clientInfo: { name: 'mcp-warden-test', version: '1' } } });
    assert.equal(reply.status, 200, reply.text);
    assert.ok(this.session);
    assert.ok(reply.messages.find(message => message.id === this.id)?.result);
    assert.equal((await this.post({ jsonrpc: '2.0', method: 'notifications/initialized' })).status, 202);
    return this;
  }
  async call(name, args = {}) {
    const id = ++this.id;
    const reply = await this.post({ jsonrpc: '2.0', id, method: 'tools/call', params: { name, arguments: args } });
    assert.equal(reply.status, 200, reply.text);
    const message = reply.messages.find(item => item.id === id && !item.method);
    assert.ok(message?.result, JSON.stringify(reply.messages));
    return message.result.structuredContent;
  }
  async openGet() {
    await new Promise((resolve, reject) => {
      this.get = http.request({ hostname: '127.0.0.1', port: this.harness.port, path: '/mcp/' + this.profile,
        method: 'GET', headers: { ...this.headers(), Accept: 'text/event-stream' } }, res => {
        this.getResponse = res;
        let pending = '';
        res.on('data', chunk => {
          pending += chunk.toString('utf8');
          while (pending.includes('\n')) {
            const split = pending.indexOf('\n');
            const line = pending.slice(0, split).trimEnd();
            pending = pending.slice(split + 1);
            if (line.startsWith('data:')) this.onMessage(JSON.parse(line.slice(5).trim()));
          }
        });
        assert.equal(res.statusCode, 200);
        resolve();
      });
      this.get.once('error', reject);
      this.get.end();
    });
  }
  closeGet() { this.get?.destroy(); this.getResponse?.destroy(); }
}

async function harness(t, idleSeconds = IDLE_SECONDS, overrides = {}) {
  const directory = await mkdtemp(path.join(tmpdir(), 'mcp-warden-lifecycle-'));
  const run = path.join(directory, 'run');
  await mkdir(run);
  const token = randomBytes(32).toString('hex');
  const tokenFile = path.join(directory, 'token');
  await writeFile(tokenFile, token, { mode: 0o600 });
  const owner = await createOwner();
  const port = await freePort();
  const manifest = { command: owner.identity.python, args: ['-I', '-B', '-u', path.join(ROOT, 'examples', 'echo_server.py')],
    env: {}, fingerprint_env: [], cache_ttl_seconds: 0, shutdown_grace_seconds: 0.2 };
  const gateway = new Gateway({ port, token_file: tokenFile, run_dir: run, allowed_cwd_root: directory,
    bridge_python: owner.identity.python, owned_bridge: path.join(ROOT, 'runtime', 'owned_bridge.py'),
    bridge: path.join(ROOT, 'runtime', 'lazy_stdio.py'), idle_session_timeout_seconds: idleSeconds,
    handshake_timeout_seconds: 8,
    profiles: { example: { manifest, shutdown_grace_seconds: 0.2 },
      disabled: { manifest, shutdown_grace_seconds: 0.2, idle_session_timeout_seconds: 0 } }, ...overrides });
  await gateway.listen();
  const headers = { Authorization: 'Bearer ' + token, Accept: 'application/json, text/event-stream',
    'Content-Type': 'application/json', 'Mcp-Owner-Unused': 'not-authoritative',
    'X-Mcp-Owner-Pid': String(owner.identity.pid), 'X-Mcp-Owner-Created': owner.identity.created,
    'X-Mcp-Cwd-B64': Buffer.from(directory).toString('base64'),
    'X-Mcp-Env-B64': Buffer.from(JSON.stringify(Object.fromEntries(Object.entries(process.env)
      .filter(([key]) => OWNER_ENV_KEYS.has(key.toUpperCase())).map(([key, value]) => [key.toUpperCase(), value])))).toString('base64') };
  const clients = [];
  t.after(async () => {
    for (const client of clients) client.closeGet();
    await gateway.close();
    owner.child.stdin.end();
    if (owner.child.exitCode === null) await once(owner.child, 'exit');
    // Fixtures and token stay in the system temp directory for failure diagnosis;
    // no recursive deletion and no artifacts enter the release archive.
  });
  const value = { directory, run, port, token, owner, gateway, headers,
    client: async profile => { const client = new Client(value, profile); clients.push(client); return client.initialize(); },
    health: async () => {
      const reply = await exchange(port, { Authorization: 'Bearer ' + token }, null, { method: 'GET', path: '/_health' });
      assert.equal(reply.status, 200);
      return JSON.parse(reply.text);
    },
    events: async () => (await readFile(path.join(run, 'events.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse),
  };
  return value;
}

test('Windows MCP session lifecycle', { skip: process.platform !== 'win32', timeout: 65000 }, async t => {
  await t.test('authentication, Host and malformed JSON fail without echoing payloads', async t => {
    const h = await harness(t);
    const marker = 'private-fixture-content-do-not-log';
    assert.equal((await exchange(h.port, { Authorization: 'Bearer wrong' }, null, { method: 'GET', path: '/_health' })).status, 401);
    assert.equal((await exchange(h.port, h.headers, null, { method: 'GET', path: '/_health', headers: { Host: 'example.invalid' } })).status, 421);
    const bad = await exchange(h.port, h.headers, null, { raw: '{"' + marker });
    assert.equal(bad.status, 400);
    assert.equal(bad.text.includes(marker), false);
    assert.equal((await h.health()).sessionCount, 0);
    assert.equal(JSON.stringify(await h.events()).includes(marker), false);
    assert.equal(JSON.stringify(await h.events()).includes(h.token), false);
  });

  await t.test('idle session expires even while its GET event stream is open', async t => {
    const h = await harness(t);
    const c = await h.client();
    await c.call('echo');
    await c.openGet();
    await waitFor(async () => (await h.health()).idleExpiredSessionCount === 1);
    await waitFor(async () => (await h.health()).sessionCount === 0);
    assert.equal((await c.post({ jsonrpc: '2.0', id: 'stale', method: 'tools/list' })).status, 404);
    assert.ok((await h.events()).some(event => event.event === 'session_closed' && event.reason === 'idle_timeout'));
  });

  await t.test('RPC longer than idle timeout retains its backend until the reply', async t => {
    const h = await harness(t);
    const c = await h.client();
    const before = await c.call('echo', { value: 'before' });
    const pending = c.call('sleep', { seconds: IDLE_SECONDS * 2.4, value: 'after' });
    await delay(IDLE_SECONDS * 1500);
    assert.equal((await h.health()).pendingRequestCount, 1);
    assert.equal((await h.health()).idleExpiredSessionCount, 0);
    const after = await pending;
    assert.equal(after.pid, before.pid);
    assert.equal(after.value, 'after');
    await waitFor(async () => (await h.health()).sessionCount === 0);
  });

  await t.test('pending server callback survives the idle timeout', async t => {
    const h = await harness(t);
    const c = await h.client();
    const before = await c.call('echo');
    c.callbackDelay = IDLE_SECONDS * 2400;
    const pending = c.call('callback');
    await waitFor(() => c.callbackSeen);
    await delay(IDLE_SECONDS * 1300);
    assert.equal((await h.health()).pendingCallbackCount, 1);
    assert.equal((await h.health()).idleExpiredSessionCount, 0);
    const after = await pending;
    await Promise.all(c.callbackTasks);
    assert.equal(after.pid, before.pid);
    assert.equal(after.value, 'callback-completed');
    await waitFor(async () => (await h.health()).sessionCount === 0);
  });

  await t.test('server callback holds the session with no client RPC in flight', async t => {
    const h = await harness(t);
    const c = await h.client();
    await c.openGet();
    c.callbackDelay = IDLE_SECONDS * 2400;
    const before = await c.call('notify_callback');
    await waitFor(() => c.callbackSeen);
    await delay(IDLE_SECONDS * 1300);
    const health = await h.health();
    assert.equal(health.pendingRequestCount, 0);
    assert.equal(health.pendingCallbackCount, 1);
    assert.equal(health.idleExpiredSessionCount, 0);
    await Promise.all(c.callbackTasks);
    assert.equal((await c.call('echo')).pid, before.pid);
    await waitFor(async () => (await h.health()).sessionCount === 0);
  });

  await t.test('a slowly arriving POST body keeps its existing session alive', async t => {
    const h = await harness(t);
    const c = await h.client();
    const before = await c.call('echo');
    const body = JSON.stringify({ jsonrpc: '2.0', id: 'slow-body', method: 'tools/call', params: { name: 'echo' } });
    const result = new Promise((resolve, reject) => {
      const request = http.request({ hostname: '127.0.0.1', port: h.port, method: 'POST', path: '/mcp/example',
        headers: { ...c.headers(), 'Content-Length': Buffer.byteLength(body) } }, response => {
        const chunks = [];
        response.on('data', chunk => chunks.push(chunk));
        response.once('end', () => resolve({ status: response.statusCode, text: Buffer.concat(chunks).toString('utf8') }));
      });
      request.once('error', reject);
      request.write(body.slice(0, 1));
      setTimeout(() => request.end(body.slice(1)), IDLE_SECONDS * 1900);
    });
    const response = await result;
    assert.equal(response.status, 200, response.text);
    assert.equal((await c.call('echo')).pid, before.pid);
  });

  await t.test('capacity limits reject a new session before starting another backend', async t => {
    const h = await harness(t, 0, { max_sessions: 1, max_sessions_per_profile: 1 });
    const c = await h.client();
    await c.call('echo');
    const rejected = await exchange(h.port, h.headers, { jsonrpc: '2.0', id: 'capacity', method: 'initialize',
      params: { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'capacity', version: '1' } } });
    assert.equal(rejected.status, 429, rejected.text);
    assert.equal((await h.health()).sessionCount, 1);
    assert.equal((await h.events()).filter(event => event.event === 'bridge_started').length, 1);
  });

  await t.test('oversized request bodies fail before creating a session', async t => {
    const h = await harness(t, IDLE_SECONDS, { max_request_bytes: 1024 });
    const rejected = await exchange(h.port, h.headers, null, { raw: JSON.stringify({ payload: 'x'.repeat(2048) }) });
    assert.equal(rejected.status, 413, rejected.text);
    assert.equal((await h.health()).sessionCount, 0);
    assert.equal((await h.events()).filter(event => event.event === 'bridge_started').length, 0);
  });

  await t.test('separate sessions have separate backends and can disable idle expiry', async t => {
    const h = await harness(t);
    const a = await h.client('disabled');
    const b = await h.client('disabled');
    const first = await a.call('echo', { value: 'a' });
    const second = await b.call('echo', { value: 'b' });
    assert.notEqual(first.pid, second.pid);
    assert.notEqual(a.session, b.session);
    await delay(IDLE_SECONDS * 1400);
    assert.equal((await a.call('echo')).pid, first.pid);
    assert.equal((await b.call('echo')).pid, second.pid);
    assert.equal((await h.health()).idleExpiredSessionCount, 0);
  });

  await t.test('owner process exit closes its session even when idle expiry is disabled', async t => {
    const h = await harness(t);
    const c = await h.client('disabled');
    await c.call('echo');
    const exited = once(h.owner.child, 'exit');
    h.owner.child.stdin.end();
    await exited;
    await waitFor(async () => (await h.health()).sessionCount === 0);
    assert.equal((await c.post({ jsonrpc: '2.0', id: 'orphan', method: 'tools/list' })).status, 404);
  });
});

test('configured initialization deadline allows an owner bridge taking over ten seconds',
  { skip: process.platform !== 'win32', timeout: 25000 }, async t => {
    const h = await harness(t, IDLE_SECONDS, { handshake_timeout_seconds: 18 });
    const wrapper = path.join(h.directory, 'slow-owner.py');
    await writeFile(wrapper, 'import time,runpy\ntime.sleep(10.2)\nrunpy.run_path('
      + JSON.stringify(path.join(ROOT, 'runtime', 'owned_bridge.py')) + ',run_name="__main__")\n');
    h.gateway.config.owned_bridge = wrapper;
    const response = await exchange(h.port, h.headers, { jsonrpc: '2.0', id: 'slow-owner', method: 'initialize',
      params: { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'slow-owner', version: '1' } } },
      { timeoutMs: 20000 });
    assert.equal(response.status, 200, response.text);
    assert.ok(response.messages.some(message => message.id === 'slow-owner' && message.result));
    assert.equal((await h.events()).filter(event => event.event === 'bridge_started').length, 1);
  });
