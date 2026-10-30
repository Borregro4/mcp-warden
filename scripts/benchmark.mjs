/** Opt-in Windows comparison; synthetic local data, no model or provider calls.
 * Includes all variant-owned supervisor/proxy/connector/backend processes.
 */
import { spawn, spawnSync } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import net from 'node:net';
import { once } from 'node:events';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { performance } from 'node:perf_hooks';
import { setTimeout as delay } from 'node:timers/promises';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { ListRootsRequestSchema } from '@modelcontextprotocol/sdk/types.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const flags = new Map();
for (let i = 2; i < process.argv.length; i += 2) flags.set(process.argv[i], process.argv[i + 1]);
const output = path.resolve(flags.get('--out') ?? 'benchmark-output');
const snooze = flags.get('--snooze') ? path.resolve(flags.get('--snooze')) : null;
const runs = Number(flags.get('--runs') ?? 3);
if (process.platform !== 'win32' || !snooze || !Number.isInteger(runs) || runs < 1 || runs > 5)
  throw new Error('Windows, --snooze PATH and --runs 1..5 are required');
const run = (command, args) => {
  const result = spawnSync(command, args, { encoding: 'utf8', windowsHide: true, timeout: 10000 });
  if (result.status !== 0) throw new Error('benchmark command failed');
  return result.stdout;
};
const python = run(process.env.PYTHON ?? 'python', ['-I', '-c', 'import sys;print(sys.executable)']).trim();
const hash = async file => createHash('sha256').update(await readFile(file)).digest('hex');
const idleSeconds = 2;
const allocationMiB = 32;
const warmCalls = 10;
await mkdir(output, { recursive: true });
const sourceFiles = ['scripts/benchmark.mjs', 'scripts/benchmark_backend.py', 'scripts/benchmark_processes.py',
  'examples/echo_server.py', 'warden.py', 'runtime/gateway.mjs', 'runtime/connect.mjs',
  'runtime/lazy_stdio.py', 'runtime/owned_bridge.py', 'package-lock.json'];
const sourceHashes = Object.fromEntries(await Promise.all(sourceFiles.map(async name => [name, await hash(path.join(ROOT, name))])));
const plan = {
  version: 1, preparedUtc: new Date().toISOString(), platform: 'win32', runsPerVariant: runs,
  variants: ['direct', 'snooze', 'warden'], allocationMiB, sessions: 1, idleSeconds, warmCalls,
  protocolClient: '@modelcontextprotocol/sdk 1.32.0', node: process.version,
  snooze: { version: run(snooze, ['--version']).trim(), binarySha256: await hash(snooze) },
  criteria: {
    sameWorkload: 'Same 32 MiB resident Python fixture, SDK client, one session, ten echo calls and reverse callback.',
    cold: 'Elapsed client connect, tools/list and first echo; includes starting variant processes.',
    warm: 'Ten serial echo round trips, individual latency in milliseconds.',
    longCall: 'Three-second call must complete under two-second idle deadline.',
    idle: 'Resource sample after three seconds without client requests; retain proxy/supervisor cost.',
    wake: 'Attempt echo; if explicitly expired, record failure, reconnect once and call. Include reconnect time.',
    resources: 'Read-only Windows Toolhelp tree from explicitly spawned roots. Sum private bytes and working set; unavailable entries invalidate complete resource claim.',
    cleanup: 'After closing owned transports/supervisor, no matching observed PID+creation identity remains alive.',
    guards: 'Abort variant if <3 GiB system RAM available or managed private bytes >768 MiB.'
  },
  decisionRule: 'Report raw observations and medians; no general winner or subscription/token saving inferred.',
  sourceHashes
};
await writeFile(path.join(output, 'plan.json'), JSON.stringify(plan, null, 2), { flag: 'wx' });

async function freePort() {
  const server = net.createServer(); server.listen(0, '127.0.0.1');
  await once(server, 'listening'); const port = server.address().port;
  await new Promise(resolve => server.close(resolve)); return port;
}
async function sample(roots) {
  const value = JSON.parse(run(python, ['-I', '-B', path.join(ROOT, 'scripts', 'benchmark_processes.py'),
    '--roots', ...(roots.length ? roots : [0]).map(String)]));
  if (value.availableSystemBytes < 3 * 1024 ** 3 || value.privateBytes > 768 * 1024 ** 2) {
    const error = new Error('benchmark resource guard');
    error.observation = value;
    throw error;
  }
  return value;
}
async function waitReady(port, token) {
  const deadline = performance.now() + 10000;
  while (performance.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/_health`, {
        headers: { Authorization: 'Bearer ' + token }, signal: AbortSignal.timeout(500) });
      if (response.ok && (await response.json()).status === 'ready') return;
    } catch {}
    await delay(50);
  }
  throw new Error('gateway readiness timeout');
}
const results = [];
async function variant(name, repetition) {
  const directory = path.join(output, `${repetition}-${name}`);
  await mkdir(directory);
  const roots = [];
  const clients = [];
  const identities = new Map();
  const item = { variant: name, repetition, measurements: {}, errors: [], expectedFirstWakeError: false };
  let supervisor;
  let spec;
  const began = performance.now();
  let coldStarted = began;
  const observe = async label => {
    const value = await sample(roots);
    item.measurements[label] = value;
    for (const row of value.processes) identities.set(row.pid, row.created);
    return value;
  };
  const connectClient = async () => {
    const client = new Client({ name: 'warden-comparison', version: '1' }, { capabilities: { roots: {} } });
    client.setRequestHandler(ListRootsRequestSchema, async () => ({ roots: [] }));
    const transport = new StdioClientTransport({ ...spec, cwd: directory, stderr: 'pipe' });
    let stderrBytes = 0;
    transport.stderr.on('data', chunk => { stderrBytes += chunk.length; });
    clients.push(client);
    await client.connect(transport, { timeout: 10000 });
    roots.push(transport.pid);
    return { client, transport, diagnosticSize: () => stderrBytes };
  };
  try {
    if (name === 'warden') {
      const state = path.join(directory, 'state');
      const port = await freePort();
      run(python, ['-I', '-B', path.join(ROOT, 'warden.py'), 'init', '--state', state,
        '--port', String(port), '--idle-seconds', String(idleSeconds)]);
      const configFile = path.join(state, 'config.json');
      const config = JSON.parse(await readFile(configFile, 'utf8'));
      config.profiles.example.manifest.args = ['-I', '-B', '-u', path.join(ROOT, 'scripts', 'benchmark_backend.py'), '--mb', String(allocationMiB)];
      config.profiles.example.manifest.fingerprint_files = [path.join(ROOT, 'scripts', 'benchmark_backend.py'), path.join(ROOT, 'examples', 'echo_server.py')];
      await writeFile(configFile, JSON.stringify(config));
      const token = (await readFile(path.join(state, 'token'), 'utf8')).trim();
      coldStarted = performance.now(); // Offline state preparation is not startup latency.
      supervisor = spawn(python, ['-I', '-B', path.join(ROOT, 'warden.py'), 'serve', '--state', state],
        { windowsHide: true, stdio: ['pipe', 'ignore', 'pipe'] });
      supervisor.stderr.on('data', () => {});
      roots.push(supervisor.pid);
      await waitReady(port, token);
      spec = { command: python, args: ['-I', '-B', '-u', path.join(ROOT, 'warden.py'), 'connect', '--state', state, '--profile', 'example'] };
    } else {
      const backend = ['-I', '-B', '-u', path.join(ROOT, 'scripts', 'benchmark_backend.py'), '--mb', String(allocationMiB)];
      spec = name === 'direct' ? { command: python, args: backend } : {
        command: snooze, args: ['--idle', String(idleSeconds), '--cache-dir', path.join(directory, 'cache'), '--', python, ...backend] };
    }
    let session = await connectClient();
    await session.client.listTools();
    const cold = await session.client.callTool({ name: 'echo', arguments: { value: 'cold' } });
    if (cold.structuredContent?.value !== 'cold') throw new Error('cold content mismatch');
    item.coldMs = performance.now() - coldStarted;
    item.warmMs = [];
    for (let i = 0; i < warmCalls; i++) {
      const sent = performance.now();
      const response = await session.client.callTool({ name: 'echo', arguments: { value: `warm-${i}` } });
      if (response.structuredContent?.value !== `warm-${i}`) throw new Error('warm content mismatch');
      item.warmMs.push(performance.now() - sent);
    }
    const callback = await session.client.callTool({ name: 'callback', arguments: {} });
    item.callbackPassed = callback.structuredContent?.value === 'callback-completed';
    const long = await session.client.callTool({ name: 'sleep', arguments: { seconds: 3, value: 'long' } });
    item.longCallPassed = long.structuredContent?.value === 'long'
      && long.structuredContent?.pid === cold.structuredContent?.pid;
    await observe('active');
    await delay((idleSeconds + 1) * 1000);
    await observe('idle');
    const wake = performance.now();
    try {
      const response = await session.client.callTool({ name: 'echo', arguments: { value: 'wake' } });
      item.wakePassed = response.structuredContent?.value === 'wake';
    } catch (error) {
      if (name !== 'warden' || error.code !== -32000 || !error.message.includes('session expired')) throw error;
      item.expectedFirstWakeError = true;
      await session.client.close();
      session = await connectClient();
      await session.client.listTools();
      const response = await session.client.callTool({ name: 'echo', arguments: { value: 'wake' } });
      item.wakePassed = response.structuredContent?.value === 'wake';
    }
    item.wakeMs = performance.now() - wake;
    await observe('awake');
    item.diagnosticBytes = session.diagnosticSize();
  } catch (error) {
    item.errors.push({ code: error.code ?? null, message: String(error.message).slice(0, 200) });
  } finally {
    for (const client of clients) await client.close().catch(() => {});
    if (supervisor && supervisor.exitCode === null) {
      supervisor.stdin.end();
      await Promise.race([once(supervisor, 'exit'), delay(10000)]);
      if (supervisor.exitCode === null) { supervisor.kill(); await once(supervisor, 'exit'); }
    }
    await delay(300);
    const after = await sample([...identities.keys()]);
    item.cleanupRemaining = after.processes.filter(row => identities.get(row.pid) === row.created);
    item.elapsedMs = performance.now() - began;
    item.passed = item.errors.length === 0 && item.callbackPassed && item.longCallPassed && item.wakePassed
      && item.cleanupRemaining.length === 0 && Object.values(item.measurements).every(v => v.unavailable.length === 0);
  }
  results.push(item);
  await writeFile(path.join(output, 'results.json'), JSON.stringify({ plan, results }, null, 2));
  console.log(JSON.stringify({ variant: name, repetition, passed: item.passed, errors: item.errors,
    coldMs: item.coldMs, idlePrivateBytes: item.measurements.idle?.privateBytes, wakeMs: item.wakeMs }));
}
try { await sample([]); }
catch (error) {
  await writeFile(path.join(output, 'environment-abort.json'), JSON.stringify({
    reason: 'resource_guard_before_start', observation: error.observation ?? null,
    measuredVariantsStarted: 0, atUtc: new Date().toISOString()
  }, null, 2));
  throw error;
}
for (let repetition = 1; repetition <= runs; repetition++) {
  const variants = [...plan.variants.slice((repetition - 1) % 3), ...plan.variants.slice(0, (repetition - 1) % 3)];
  for (const name of variants) await variant(name, repetition);
}
const endingHashes = Object.fromEntries(await Promise.all(sourceFiles.map(async name => [name, await hash(path.join(ROOT, name))])));
const median = values => { const sorted = [...values].sort((a, b) => a - b); return sorted.length ? sorted[Math.floor(sorted.length / 2)] : null; };
const summary = {
  preparedUtc: plan.preparedUtc, finishedUtc: new Date().toISOString(), runsPerVariant: runs,
  sourceChangedDuringRun: sourceFiles.filter(name => sourceHashes[name] !== endingHashes[name]),
  summaries: plan.variants.map(name => {
    const rows = results.filter(r => r.variant === name), ok = rows.filter(r => r.passed);
    return { variant: name, planned: rows.length, passed: ok.length,
      medianColdMs: median(ok.map(r => r.coldMs)), medianWarmMs: median(ok.flatMap(r => r.warmMs)),
      medianWakeMs: median(ok.map(r => r.wakeMs)),
      medianActivePrivateBytes: median(ok.map(r => r.measurements.active.privateBytes)),
      medianIdlePrivateBytes: median(ok.map(r => r.measurements.idle.privateBytes)),
      medianIdleWorkingSetBytes: median(ok.map(r => r.measurements.idle.workingSetBytes)),
      idleProcessCounts: ok.map(r => r.measurements.idle.processCount),
      explicitFirstWakeErrors: rows.filter(r => r.expectedFirstWakeError).length,
      errors: rows.flatMap(r => r.errors) };
  }),
  limits: ['Single synthetic session with 32 MiB resident allocation, three runs; not representative of all MCP servers.',
    'Snapshot memory includes all observed variant-owned descendants; working-set sums double-count shared pages.',
    'Only direct baseline and mcp-snooze v0.1.0 compared. No quota, model or token cost is measured.',
    'Warden currently requires explicit client reconnection after expiry; that failure and reconnect are included in wake time.']
};
await writeFile(path.join(output, 'summary.json'), JSON.stringify(summary, null, 2));
console.log(JSON.stringify(summary));
if (results.some(r => !r.passed) || summary.sourceChangedDuringRun.length) process.exitCode = 1;
