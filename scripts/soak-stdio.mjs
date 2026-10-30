/** Real-time endurance for opt-in stateless STDIO, without an HTTP gateway. */
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdir, readFile, writeFile, appendFile } from 'node:fs/promises';
import { writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { performance } from 'node:perf_hooks';
import { setTimeout as delay } from 'node:timers/promises';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { ListRootsRequestSchema } from '@modelcontextprotocol/sdk/types.js';

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
for (const name of ['runtime/idle_stdio.py', 'runtime/lazy_stdio.py', 'runtime/owned_bridge.py',
  'warden.py', 'examples/echo_server.py', 'scripts/soak-stdio.mjs', 'scripts/soak_metrics.py', 'package-lock.json'])
  sourceHashes[name] = hash(await readFile(path.join(ROOT, name)));
const criteria = {
  duration: 'Same active backend responds after at least ' + options.minutes * 60 + ' monotonic seconds',
  integrity: 'Each echo equals the sent tick and active backend PID stays stable',
  callbacks: 'Reverse roots callbacks complete every five active ticks',
  metadata: 'Every connection observes tools/list before its first tool call and contains the required synthetic tools',
  realIdle: 'Idle backend disappears after ' + options.idle + ' seconds, within 60 seconds grace; its Python proxy remains',
  autoWake: 'Same SDK connection and Python proxy wake a fresh backend after clean expiry under explicit stateless policy',
  churn: 'Three serial fresh owner connections close with their backend and proxy identities gone',
  resources: 'Sampled test-tree working set objective ' + options.targetMib + ' MiB; abort above ' + options.abortMib +
    ' MiB or free physical RAM below ' + options.minFreeGib + ' GiB',
  cleanup: 'No active proxies in status; direct PID+creation liveness queries find no observed child identity after shutdown, including orphans',
};
const plan = { version: 1, createdUtc: new Date().toISOString(), sourceCommit: process.env.GITHUB_SHA ?? null,
  options, criteria, sourceHashes, clock: 'performance.now monotonic', rootPid: process.pid,
  environment: process.env.GITHUB_ACTIONS ? 'GitHub hosted Windows' : 'local Windows',
  scope: 'Synthetic SDK -> real CLI STDIO; explicit stateless policy, no model calls or production MCP' };
const planText = JSON.stringify(plan, null, 2);
await writeFile(path.join(out, 'plan.json'), planText);
const pythonProbe = spawnSync(process.env.PYTHON ?? 'python', ['-I', '-c', 'import sys; print(sys.executable)'],
  { encoding: 'utf8', windowsHide: true, timeout: 10000 });
assert.equal(pythonProbe.status, 0, 'python_probe_failed');
const python = pythonProbe.stdout.trim(), state = path.join(out, 'state');
const clients = [], failures = [], checks = {}, observed = new Map();
let activePid, idlePid, idleIdentity, activeOwner, idleOwner, idleStarted, activeSeconds = null;
let ticks = 0, callbacks = 0, peak = 0, autoWoke = false, maxCpuPercent = 0;
let previousTime, previousCpu = new Map();
const began = performance.now();
const report = { startedUtc: new Date().toISOString(), planSha256: hash(planText), sourceHashes, checks, failures,
  runtime: { platform: process.platform, node: process.version, logicalCpus: os.availableParallelism() },
  limits: ['Working-set sum can double-count shared pages', 'Samples are not continuous peaks',
    'Transient sampler/status probes can exit between CPU samples',
    'Synthetic STDIO endurance does not establish native Codex/Claude or general superiority'] };
const log = async (event, data = {}) => appendFile(path.join(out, 'observations.jsonl'), JSON.stringify({
  event, elapsedSeconds: (performance.now() - began) / 1000, at: new Date().toISOString(), ...data }) + '\n');
function resources(identities = null) {
  const args = ['-I', '-B', path.join(ROOT, 'scripts/soak_metrics.py'), String(process.pid)];
  if (identities !== null) {
    const file = path.join(out, 'identity-query.json');
    writeFileSync(file, JSON.stringify(identities.map(value => ({ pid: value.pid, created: value.created }))));
    args.push('--identities', file);
  }
  const result = spawnSync(python, args,
    { encoding: 'utf8', windowsHide: true, timeout: 10000 });
  assert.equal(result.status, 0, 'resource_sampler_failed');
  const data = JSON.parse(result.stdout);
  if (identities !== null) assert.equal(data.knownUnavailable.length, 0, 'known_process_liveness_unavailable');
  return data;
}
function status() {
  const result = spawnSync(python, ['-I', '-B', path.join(ROOT, 'warden.py'), 'status', '--state', state,
    '--transport', 'stdio', '--json'], { encoding: 'utf8', windowsHide: true, timeout: 10000 });
  assert.equal(result.status, 0, 'stdio_status_failed');
  const data = JSON.parse(result.stdout);
  assert.equal(data.status, 'ready'); assert.equal(data.transport, 'stdio');
  return data;
}
async function waitFor(predicate, budget = 12000) {
  const deadline = performance.now() + budget;
  do { if (await predicate()) return; await delay(200); } while (performance.now() < deadline);
  throw new Error('condition_timeout');
}
async function connect() {
  const value = new Client({ name: 'mcp-warden-stdio-soak', version: '1' }, { capabilities: { roots: {} } });
  value.setRequestHandler(ListRootsRequestSchema, async () => { callbacks++; return { roots: [] }; });
  value.onerror = () => {};
  const transport = new StdioClientTransport({ command: python,
    args: ['-I', '-B', '-u', path.join(ROOT, 'warden.py'), 'connect', '--state', state, '--profile', 'example'],
    cwd: out, stderr: 'pipe' });
  transport.stderr.on('data', () => {});
  clients.push(value);
  await value.connect(transport, { timeout: 15000 });
  assert.ok(Number.isInteger(transport.pid) && transport.pid > 0, 'proxy_pid_not_available');
  const catalog = await value.listTools();
  for (const required of ['echo', 'callback'])
    assert.ok(catalog.tools.some(tool => tool.name === required), 'required_fixture_tool_missing');
  await log('client_catalog_observed', { ownerPid: transport.pid, toolCount: catalog.tools.length,
    catalogSha256: hash(JSON.stringify(catalog.tools)) });
  checks.metadata = true;
  return { client: value, ownerPid: transport.pid };
}
async function sample(label) {
  const snapshot = resources(), view = status(), now = performance.now();
  // A low memory sum is not evidence if known live fixture processes were
  // omitted by the sampler. Active identities must remain measurable.
  assert.ok(snapshot.processes.some(value => value.pid === activeOwner), 'active_proxy_missing_from_resource_sample');
  assert.ok(snapshot.processes.some(value => value.pid === activePid), 'active_backend_missing_from_resource_sample');
  if (!autoWoke) assert.ok(snapshot.processes.some(value => value.pid === idleOwner), 'idle_proxy_missing_from_resource_sample');
  let cpuDelta = 0;
  for (const value of snapshot.processes) {
    const key = value.pid + ':' + value.created;
    observed.set(key, value);
    if (previousCpu.has(key)) cpuDelta += Math.max(0, value.cpuSeconds - previousCpu.get(key));
  }
  const cpuPercent = previousTime ? cpuDelta / ((now - previousTime) / 1000) / os.availableParallelism() * 100 : null;
  if (cpuPercent !== null) maxCpuPercent = Math.max(maxCpuPercent, cpuPercent);
  previousTime = now;
  previousCpu = new Map(snapshot.processes.map(value => [value.pid + ':' + value.created, value.cpuSeconds]));
  peak = Math.max(peak, snapshot.workingSetBytes);
  await log('sample', { label, resources: snapshot, cpuPercent, status: view });
  if (ticks % 10 === 0) console.log(JSON.stringify({ event: 'progress', ticks,
    elapsedSeconds: (now - began) / 1000, sampledWorkingSetBytes: snapshot.workingSetBytes,
    proxies: view.sessionCount, cpuPercent }));
  assert.ok(view.sessionCount <= 2, 'unexpected_extra_proxy');
  assert.ok(snapshot.workingSetBytes <= options.abortMib * 1024 ** 2, 'resource_abort_working_set');
  assert.ok(snapshot.availablePhysicalBytes >= options.minFreeGib * 1024 ** 3, 'resource_abort_available_memory');
  return { snapshot, view };
}
try {
  const preflight = resources();
  await writeFile(path.join(out, 'preflight.json'), JSON.stringify(preflight, null, 2));
  assert.ok(preflight.availablePhysicalBytes >= options.minFreeGib * 1024 ** 3, 'resource_preflight_available_memory');
  const init = spawnSync(python, ['-I', '-B', path.join(ROOT, 'warden.py'), 'init', '--state', state,
    '--transport', 'stdio', '--restart-policy', 'stateless', '--idle-seconds', String(options.idle)],
    { encoding: 'utf8', windowsHide: true, timeout: 15000 });
  assert.equal(init.status, 0, 'isolated_stdio_init_failed');
  const active = await connect(); activeOwner = active.ownerPid;
  activePid = (await active.client.callTool({ name: 'echo', arguments: { value: 'active-start' } })).structuredContent.pid;
  const idle = await connect(); idleOwner = idle.ownerPid;
  idlePid = (await idle.client.callTool({ name: 'echo', arguments: { value: 'idle-start' } })).structuredContent.pid;
  idleIdentity = resources().processes.find(value => value.pid === idlePid);
  assert.ok(idleIdentity, 'initial_idle_backend_identity_unavailable');
  idleStarted = performance.now();
  await log('sessions_started', { activePid, idlePid, activeOwner, idleOwner });
  let next = performance.now();
  while (performance.now() - idleStarted < options.minutes * 60 * 1000) {
    const value = 'tick-' + ticks;
    const echo = await active.client.callTool({ name: 'echo', arguments: { value } });
    assert.equal(echo.structuredContent.value, value);
    assert.equal(echo.structuredContent.pid, activePid, 'active_backend_restarted');
    if (ticks % 5 === 0) {
      const answer = await active.client.callTool({ name: 'callback', arguments: {} });
      assert.equal(answer.structuredContent.value, 'callback-completed');
      assert.equal(answer.structuredContent.pid, activePid);
    }
    const current = await sample('tick-' + ticks);
    // Status is sampled every two seconds. Its initial pre-backend snapshot can
    // legitimately have backendPid=null; do not mistake that stale startup
    // record for a completed 15-minute idle interval.
    if (!autoWoke && performance.now() - idleStarted >= (options.idle - 1) * 1000) {
      const idleSample = current.view.samples.find(value => value.ownerPid === idleOwner);
      if (idleSample && idleSample.backendPid === null) {
        const sinceIdle = (performance.now() - idleStarted) / 1000;
        assert.ok(sinceIdle >= options.idle - 1 && sinceIdle <= options.idle + 60, 'idle_deadline_outside_expected_window');
        assert.equal(resources([idleIdentity]).knownAlive.length, 0, 'idle_backend_still_alive');
        assert.ok(current.snapshot.processes.some(value => value.pid === idleOwner), 'idle_proxy_exited');
        checks.realIdle = true;
        await log('real_idle_expired', { elapsedSinceIdleCallSeconds: sinceIdle, idleOwner, oldBackendPid: idlePid });
        const awake = await idle.client.callTool({ name: 'echo', arguments: { value: 'same-client-after-expiry' } });
        assert.equal(awake.structuredContent.value, 'same-client-after-expiry');
        const awakeIdentity = resources().processes.find(value => value.pid === awake.structuredContent.pid);
        assert.ok(awakeIdentity, 'awoken_backend_identity_unavailable');
        assert.notEqual(awakeIdentity.pid + ':' + awakeIdentity.created,
          idleIdentity.pid + ':' + idleIdentity.created, 'backend_identity_not_replaced');
        assert.equal(idle.ownerPid, idleOwner);
        autoWoke = true; checks.autoWake = true;
        await log('same_client_auto_woke', { ownerPid: idleOwner, backendPid: awake.structuredContent.pid });
        await idle.client.close();
        await waitFor(() => !status().samples.some(value => value.ownerPid === idleOwner));
        for (let i = 0; i < 3; i++) {
          const churn = await connect();
          const answer = await churn.client.callTool({ name: 'echo', arguments: { value: 'churn-' + i } });
          const identity = resources().processes.filter(value => value.pid === churn.ownerPid || value.pid === answer.structuredContent.pid);
          assert.equal(identity.length, 2, 'churn_proxy_and_backend_identity_missing');
          assert.equal(new Set(identity.map(value => value.pid + ':' + value.created)).size, 2, 'churn_identities_not_distinct');
          for (const value of identity) observed.set(value.pid + ':' + value.created, value);
          await log('churn_identities_observed', { index: i, identities: identity.map(value => ({ pid: value.pid, created: value.created })) });
          await churn.client.close();
          await waitFor(() => {
            return resources(identity).knownAlive.length === 0;
          });
        }
        checks.churn = true;
        await log('serial_churn_completed', { count: 3 });
      }
    }
    ticks++; next += options.sample * 1000;
    await delay(Math.max(0, Math.min(next - performance.now(), idleStarted + options.minutes * 60 * 1000 - performance.now())));
  }
  const finalEcho = await active.client.callTool({ name: 'echo', arguments: { value: 'final-after-full-duration' } });
  assert.equal(finalEcho.structuredContent.pid, activePid);
  assert.equal(finalEcho.structuredContent.value, 'final-after-full-duration');
  activeSeconds = (performance.now() - idleStarted) / 1000;
  checks.duration = activeSeconds >= options.minutes * 60;
  checks.integrity = true; checks.callbacks = callbacks >= Math.max(1, Math.floor(ticks / 5));
  checks.resources = peak <= options.targetMib * 1024 ** 2;
  await log('active_duration_completed', { activeSeconds, ticks });
} catch (error) {
  failures.push({ elapsedSeconds: (performance.now() - began) / 1000, error: error.name, message: String(error.message).slice(0, 300) });
  await log('failure', failures.at(-1));
} finally {
  await Promise.allSettled(clients.map(value => value.close()));
  try {
    await waitFor(() => status().sessionCount === 0);
    checks.cleanup = true;
  } catch { checks.cleanup = false; }
  let remaining = [];
  for (let attempt = 0; attempt < 5; attempt++) {
    const after = resources([...observed.values()]);
    remaining = after.knownAlive.filter(value => value.pid !== process.pid);
    if (!remaining.length) break;
    await delay(500);
  }
  report.cleanupRemainingIdentities = remaining;
  checks.cleanup = checks.cleanup === true && remaining.length === 0;
  report.elapsedSeconds = (performance.now() - began) / 1000; report.activeSeconds = activeSeconds;
  report.finishedUtc = new Date().toISOString(); report.activeTicks = ticks; report.callbacks = callbacks;
  report.peakSampledOwnedWorkingSetBytes = peak; report.maxSampledCpuPercent = maxCpuPercent;
  report.observedIdentities = observed.size;
  report.allCriteriaMet = Object.keys(criteria).every(key => checks[key] === true) && failures.length === 0;
  await writeFile(path.join(out, 'result.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ allCriteriaMet: report.allCriteriaMet, activeSeconds, elapsedSeconds: report.elapsedSeconds,
    checks, failures, peakSampledOwnedWorkingSetBytes: peak }));
  process.exitCode = report.allCriteriaMet ? 0 : 1;
}
