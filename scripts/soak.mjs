/** Opt-in real-time endurance validation; synthetic local MCP, no AI model. */
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { mkdir, readFile, writeFile, appendFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { performance } from 'node:perf_hooks';
import { once } from 'node:events';
import net from 'node:net';
import os from 'node:os';
import { setTimeout as delay } from 'node:timers/promises';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { ListRootsRequestSchema } from '@modelcontextprotocol/sdk/types.js';
import { Gateway, OWNER_ENV_KEYS } from '../runtime/gateway.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const options = { minutes: 60, idle: 900, sample: 30, targetMib: 256, abortMib: 768, minFreeGib: 3 };
for (let i = 2; i < process.argv.length; i += 2) {
  const key = process.argv[i].slice(2);
  if (!['out', ...Object.keys(options)].includes(key) || !process.argv[i + 1]) throw new Error('invalid_arguments');
  options[key] = key === 'out' ? path.resolve(process.argv[i + 1]) : Number(process.argv[i + 1]);
}
if (process.platform !== 'win32' || !options.out || options.minutes < 1 || options.idle < 1 || options.sample < 1)
  throw new Error('Windows and explicit output directory required');
const out = options.out;
await mkdir(path.dirname(out), { recursive: true });
await mkdir(out, { recursive: false });
const hash = value => createHash('sha256').update(value).digest('hex');
const sourceHashes = {};
for (const name of ['runtime/gateway.mjs', 'runtime/connect.mjs', 'runtime/lazy_stdio.py',
  'runtime/owned_bridge.py', 'warden.py', 'examples/echo_server.py', 'scripts/soak.mjs',
  'scripts/soak_metrics.py', 'package-lock.json', 'tests/owner.py'])
  sourceHashes[name] = hash(await readFile(path.join(ROOT, name)));
const criteria = {
  duration: 'Same active backend responds after at least ' + options.minutes * 60 + ' monotonic seconds',
  integrity: 'Every echo equals the sent value; active backend PID stays stable',
  callbacks: 'At least one roots/list callback every five active ticks completes with the same backend PID',
  realIdle: 'An unused session with passive GET expires after ' + options.idle + ' seconds, within 60 seconds grace',
  reconnect: 'A fresh client initializes and completes echo after idle expiration',
  churn: 'Three serial owner exits release their sessions after idle has freed its slot',
  resources: 'Peak sampled owned working set target ' + options.targetMib + ' MiB; abort above ' +
    options.abortMib + ' MiB or free physical memory below ' + options.minFreeGib + ' GiB',
  cleanup: 'Zero sessions and no sampled owned identity remains in the final owned-tree snapshot except the harness',
};
const plan = { version: 1, options, criteria, sourceHashes, createdUtc: new Date().toISOString(),
  sourceCommit: process.env.GITHUB_SHA ?? null, environment: process.env.GITHUB_ACTIONS ? 'GitHub hosted Windows' : 'local Windows',
  scope: 'Synthetic SDK/CLI end-to-end endurance; no model calls, production MCPs or external data',
  clock: 'performance.now monotonic', rootPid: process.pid };
const planText = JSON.stringify(plan, null, 2);
await writeFile(path.join(out, 'plan.json'), planText);
const pythonProbe = spawnSync(process.env.PYTHON ?? 'python', ['-I', '-c', 'import sys; print(sys.executable)'],
  { encoding: 'utf8', windowsHide: true, timeout: 10000 });
assert.equal(pythonProbe.status, 0, 'python_probe_failed');
const python = pythonProbe.stdout.trim();
const clients = [], owners = [], failures = [], checks = {};
let gateway, config, token, port, idleStarted, idleExpiredAt, activePid;
let ticks = 0, callbacks = 0, peak = 0, maxCpuPercent = 0, reconnectDone = false, activeSeconds = null;
let previousSampleTime, previousCpu = new Map();
const observed = new Map();
const began = performance.now();
const report = { startedUtc: new Date().toISOString(), planSha256: hash(planText), checks, failures, sourceHashes,
  runtime: { platform: process.platform, node: process.version, logicalCpus: os.availableParallelism() },
  limits: ['Working-set sum can double-count shared pages', 'Sampling is not a continuous peak measurement',
    'Synthetic endurance is not native Codex/Claude validation or a general savings benchmark',
    'The Windows process table supplies parent edges; no command lines or environment are sampled'] };
const log = async (event, data = {}) => appendFile(path.join(out, 'observations.jsonl'), JSON.stringify({
  event, elapsedSeconds: (performance.now() - began) / 1000, at: new Date().toISOString(), ...data }) + '\n');
function resources() {
  const run = spawnSync(python, ['-I', '-B', path.join(ROOT, 'scripts/soak_metrics.py'), String(process.pid)],
    { encoding: 'utf8', windowsHide: true, timeout: 10000 });
  assert.equal(run.status, 0, 'resource_sampler_failed');
  return JSON.parse(run.stdout);
}
const health = async () => {
  const response = await fetch('http://127.0.0.1:' + port + '/_health', {
    headers: { Authorization: 'Bearer ' + token }, signal: AbortSignal.timeout(5000) });
  assert.equal(response.status, 200);
  return response.json();
};
async function waitFor(predicate, budget = 12000) {
  const end = performance.now() + budget;
  do { if (await predicate()) return; await delay(100); } while (performance.now() < end);
  throw new Error('condition_timeout');
}
function client() {
  const value = new Client({ name: 'mcp-warden-soak', version: '1' }, { capabilities: { roots: {} } });
  value.setRequestHandler(ListRootsRequestSchema, async () => { callbacks++; return { roots: [] }; });
  value.onerror = () => {};
  clients.push(value);
  return value;
}
async function owner() {
  const child = spawn(python, ['-I', '-B', '-u', path.join(ROOT, 'tests/owner.py')],
    { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
  owners.push(child);
  let text = '';
  const identity = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('owner_start_timeout')), 10000);
    child.stdout.on('data', chunk => {
      text += chunk;
      if (text.includes('\n')) { clearTimeout(timer); resolve(JSON.parse(text.split('\n')[0])); }
    });
    child.once('error', () => { clearTimeout(timer); reject(new Error('owner_start_failed')); });
  });
  return { child, identity };
}
async function direct(profile, identity) {
  const env = Object.fromEntries(Object.entries(process.env)
    .filter(([key]) => OWNER_ENV_KEYS.has(key.toUpperCase())).map(([key, value]) => [key.toUpperCase(), value]));
  const encode = value => Buffer.from(value).toString('base64');
  const transport = new StreamableHTTPClientTransport(new URL('http://127.0.0.1:' + port + '/mcp/' + profile), {
    requestInit: { headers: { Authorization: 'Bearer ' + token, 'X-Mcp-Owner-Pid': String(identity.pid),
      'X-Mcp-Owner-Created': identity.created, 'X-Mcp-Cwd-B64': encode(out),
      'X-Mcp-Env-B64': encode(JSON.stringify(env)) } },
    reconnectionOptions: { maxRetries: 0, initialReconnectionDelay: 1000, maxReconnectionDelay: 1000, reconnectionDelayGrowFactor: 1 },
  });
  const value = client();
  await value.connect(transport, { timeout: 10000 });
  return value;
}
async function sample(label) {
  const snapshot = resources(), status = await health(), now = performance.now();
  let cpuDelta = 0;
  for (const value of snapshot.processes) {
    const key = value.pid + ':' + value.created;
    observed.set(key, value);
    if (previousCpu.has(key)) cpuDelta += Math.max(0, value.cpuSeconds - previousCpu.get(key));
  }
  const cpuPercent = previousSampleTime ? cpuDelta / ((now - previousSampleTime) / 1000) /
    os.availableParallelism() * 100 : null;
  if (cpuPercent !== null) maxCpuPercent = Math.max(maxCpuPercent, cpuPercent);
  previousSampleTime = now;
  previousCpu = new Map(snapshot.processes.map(value => [value.pid + ':' + value.created, value.cpuSeconds]));
  peak = Math.max(peak, snapshot.workingSetBytes);
  await log('sample', { label, resources: snapshot, cpuPercent, health: status });
  if (ticks % 10 === 0) console.log(JSON.stringify({ event: 'progress', ticks,
    elapsedSeconds: (now - began) / 1000, sessions: status.sessionCount,
    sampledWorkingSetBytes: snapshot.workingSetBytes, cpuPercent }));
  assert.ok(status.sessionCount <= 2, 'session_capacity_exceeded');
  assert.ok(snapshot.workingSetBytes <= options.abortMib * 1024 ** 2, 'resource_abort_owned_working_set');
  assert.ok(snapshot.availablePhysicalBytes >= options.minFreeGib * 1024 ** 3, 'resource_abort_available_memory');
  return status;
}
try {
  const preflight = resources();
  await writeFile(path.join(out, 'preflight.json'), JSON.stringify(preflight, null, 2));
  assert.ok(preflight.availablePhysicalBytes >= options.minFreeGib * 1024 ** 3, 'resource_preflight_available_memory');
  const reserve = net.createServer();
  reserve.listen(0, '127.0.0.1'); await once(reserve, 'listening');
  port = reserve.address().port; await new Promise(resolve => reserve.close(resolve));
  const state = path.join(out, 'state');
  const init = spawnSync(python, ['-I', '-B', path.join(ROOT, 'warden.py'), 'init', '--state', state,
    '--port', String(port), '--idle-seconds', String(options.idle)], { encoding: 'utf8', windowsHide: true, timeout: 15000 });
  assert.equal(init.status, 0, 'isolated_init_failed');
  config = JSON.parse(await readFile(path.join(state, 'config.json'), 'utf8'));
  const example = config.profiles.example;
  config.profiles = { active: example, idle: structuredClone(example),
    churn: { ...structuredClone(example), idle_session_timeout_seconds: 0 } };
  config.max_sessions = 2; config.max_sessions_per_profile = 1;
  await writeFile(path.join(state, 'config.json'), JSON.stringify(config));
  token = (await readFile(path.join(state, 'token'), 'utf8')).trim();
  gateway = new Gateway(config);
  await gateway.listen();
  const active = client();
  const stdio = new StdioClientTransport({ command: python, args: ['-I', '-B', '-u', path.join(ROOT, 'warden.py'),
    'connect', '--state', state, '--profile', 'active'], cwd: out, stderr: 'pipe' });
  stdio.stderr.on('data', () => {});
  await active.connect(stdio, { timeout: 15000 });
  activePid = (await active.callTool({ name: 'echo', arguments: { value: 'initial' } })).structuredContent.pid;
  const idleOwner = await owner();
  const idle = await direct('idle', idleOwner.identity);
  const idleInitial = await idle.callTool({ name: 'echo', arguments: { value: 'idle-start' } });
  idleStarted = performance.now();
  await log('sessions_started', { activePid, idlePid: idleInitial.structuredContent.pid });
  let next = performance.now();
  while (performance.now() - idleStarted < options.minutes * 60 * 1000) {
    const value = 'tick-' + ticks;
    const echo = await active.callTool({ name: 'echo', arguments: { value } });
    assert.equal(echo.structuredContent.value, value, 'echo_mismatch');
    assert.equal(echo.structuredContent.pid, activePid, 'active_backend_restarted');
    if (ticks % 5 === 0) {
      const callback = await active.callTool({ name: 'callback', arguments: {} });
      assert.equal(callback.structuredContent.value, 'callback-completed');
      assert.equal(callback.structuredContent.pid, activePid);
    }
    const status = await sample('tick-' + ticks);
    if (!idleExpiredAt && status.profiles.idle.sessions === 0) {
      idleExpiredAt = performance.now();
      const seconds = (idleExpiredAt - idleStarted) / 1000;
      assert.ok(seconds >= options.idle - 1 && seconds <= options.idle + 60, 'real_idle_deadline_outside_expected_window');
      checks.realIdle = true;
      await log('real_idle_expired', { elapsedSinceIdleCallSeconds: seconds });
    }
    if (idleExpiredAt && !reconnectDone) {
      const fresh = await direct('idle', idleOwner.identity);
      const response = await fresh.callTool({ name: 'echo', arguments: { value: 'fresh-after-expiry' } });
      assert.equal(response.structuredContent.value, 'fresh-after-expiry');
      await fresh.close();
      reconnectDone = true; checks.reconnect = true;
      await log('fresh_client_after_expiry', { backendPid: response.structuredContent.pid });
      for (let i = 0; i < 3; i++) {
        const held = await owner(), value = await direct('churn', held.identity);
        await value.callTool({ name: 'echo', arguments: { value: 'churn-' + i } });
        held.child.stdin.end();
        await waitFor(async () => (await health()).profiles.churn.sessions === 0);
        await value.close();
      }
      checks.churn = true;
      await log('owner_churn_completed', { count: 3 });
    }
    ticks++;
    next += options.sample * 1000;
    const sleep = Math.max(0, Math.min(next - performance.now(), idleStarted + options.minutes * 60 * 1000 - performance.now()));
    await delay(sleep);
  }
  const finalEcho = await active.callTool({ name: 'echo', arguments: { value: 'final-after-full-duration' } });
  assert.equal(finalEcho.structuredContent.pid, activePid);
  assert.equal(finalEcho.structuredContent.value, 'final-after-full-duration');
  activeSeconds = (performance.now() - idleStarted) / 1000;
  checks.duration = activeSeconds >= options.minutes * 60;
  checks.integrity = true;
  checks.callbacks = callbacks >= Math.max(1, Math.floor(ticks / 5));
  checks.resources = peak <= options.targetMib * 1024 ** 2;
  await log('active_duration_completed', { activeSeconds: (performance.now() - idleStarted) / 1000, ticks });
} catch (error) {
  failures.push({ elapsedSeconds: (performance.now() - began) / 1000, error: error.name, message: String(error.message).slice(0, 300) });
  await log('failure', failures.at(-1));
} finally {
  await Promise.allSettled(clients.map(value => value.close()));
  for (const value of owners) { if (!value.stdin.destroyed) value.stdin.end(); }
  if (gateway) {
    try { await waitFor(async () => (await health()).sessionCount === 0); checks.cleanup = true; }
    catch { checks.cleanup = false; }
    await gateway.close();
  }
  let remaining = [];
  for (let attempt = 0; attempt < 5; attempt++) {
    const after = resources();
    remaining = after.processes.filter(value => value.pid !== process.pid && observed.has(value.pid + ':' + value.created));
    if (remaining.length === 0) break;
    await delay(500);
  }
  report.cleanupRemainingIdentities = remaining;
  checks.cleanup = checks.cleanup === true && remaining.length === 0;
  report.elapsedSeconds = (performance.now() - began) / 1000;
  report.activeSeconds = activeSeconds;
  report.finishedUtc = new Date().toISOString();
  report.activeTicks = ticks; report.callbacks = callbacks;
  report.peakSampledOwnedWorkingSetBytes = peak;
  report.maxSampledCpuPercent = maxCpuPercent;
  report.observedIdentities = observed.size;
  report.allCriteriaMet = Object.keys(criteria).every(key => checks[key] === true) && failures.length === 0;
  await writeFile(path.join(out, 'result.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ allCriteriaMet: report.allCriteriaMet, elapsedSeconds: report.elapsedSeconds,
    checks, failures, peakSampledOwnedWorkingSetBytes: peak }));
  process.exitCode = report.allCriteriaMet ? 0 : 1;
}
