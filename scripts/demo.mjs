/** A bounded, real SDK demonstration. Runtime state stays in a fresh external directory. */
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdir, readFile, writeFile, appendFile, realpath } from 'node:fs/promises';
import { writeFileSync } from 'node:fs';
import { createHash, randomUUID } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const IDLE = 2, LONG = 4;
if (process.platform !== 'win32') throw new Error('This demo requires Windows.');
assert.equal(process.argv.length, 4, 'Usage: node scripts/demo.mjs --out FRESH_EXTERNAL_DIRECTORY');
assert.equal(process.argv[2], '--out', 'Explicit --out required');
const out = path.resolve(process.argv[3]);
await mkdir(path.dirname(out), { recursive: true });
const resolvedParent = await realpath(path.dirname(out));
const relative = path.relative(await realpath(ROOT), path.join(resolvedParent, path.basename(out)));
assert.ok(relative.startsWith('..' + path.sep) || path.isAbsolute(relative), 'Output must be outside the source checkout');
await mkdir(out, { recursive: false }); // Never overwrite a previous run.
const state = path.join(out, 'state');
const publicDir = path.join(out, 'public');
await mkdir(publicDir);
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const began = performance.now(), at = () => (performance.now() - began) / 1000;
const checks = {}, identities = new Map();
const sourceHashes = {};
for (const file of ['warden.py', 'runtime/idle_stdio.py', 'runtime/lazy_stdio.py', 'runtime/owned_bridge.py',
  'examples/echo_server.py', 'scripts/soak_metrics.py', 'scripts/demo.mjs', 'package-lock.json'])
  sourceHashes[file] = hash(await readFile(path.join(ROOT, file)));
const pythonResult = spawnSync(process.env.PYTHON ?? 'python', ['-I', '-c', 'import sys;print(sys.executable)'],
  { encoding: 'utf8', windowsHide: true, timeout: 10000 });
assert.equal(pythonResult.status, 0, 'Python unavailable');
const python = pythonResult.stdout.trim();
const runPython = args => {
  const result = spawnSync(python, ['-I', '-B', ...args], { encoding: 'utf8', windowsHide: true, timeout: 15000 });
  assert.equal(result.status, 0, 'Demo helper failed (raw diagnostics remain private)');
  return result.stdout;
};
const sdk = JSON.parse(await readFile(path.join(ROOT, 'node_modules/@modelcontextprotocol/sdk/package.json'), 'utf8'));
assert.equal(sdk.version, '1.32.0', 'Use the SDK pinned by this checkout');
const git = spawnSync('git', ['rev-parse', 'HEAD'], { cwd: ROOT, encoding: 'utf8', windowsHide: true });
assert.equal(git.status, 0, 'Source commit unavailable');
const plan = {
  schemaVersion: 1, recordedUtc: new Date().toISOString(), sourceCommit: git.stdout.trim(), sourceHashes,
  runtime: { platform: process.platform, node: process.version, sdk: sdk.version,
    python: runPython(['-c', 'import platform; print(platform.python_version())']).trim() },
  clock: 'performance.now() monotonic seconds; UTC labels are wall clock',
  scenario: 'SDK demo · synthetic backend · accelerated idle', idleSeconds: IDLE, longRpcSeconds: LONG,
  profile: 'example', restartPolicy: 'stateless', connectionCount: 1,
  publication: 'Only public/ is suitable for review; state/ contains local configuration, token and stderr. Nonces are replaced with labels; their SHA-256 hashes cover the original UTF-8 values.',
  limits: ['Synthetic backend only; no model calls or native app compatibility claim.',
    'Working-set samples cover the proxy and direct backend; shared pages can be counted twice.',
    'CLI status is sampled by the proxy every two seconds and can lag direct PID+FILETIME probes.',
    'The animation is an editorial replay of these real observations, not a desktop recording.'],
};
await writeFile(path.join(publicDir, 'plan.json'), JSON.stringify(plan, null, 2) + '\n');
async function log(event, data = {}) {
  const row = { event, elapsedSeconds: at(), at: new Date().toISOString(), ...data };
  await appendFile(path.join(publicDir, 'events.jsonl'), JSON.stringify(row) + '\n');
  console.log(JSON.stringify(row));
}
function status() {
  const value = JSON.parse(runPython([path.join(ROOT, 'warden.py'), 'status', '--state', state, '--transport', 'stdio', '--json']));
  assert.equal(value.status, 'ready');
  assert.equal(value.transport, 'stdio');
  return value;
}
function measure() {
  const query = path.join(out, 'identity-query.json');
  writeFileSync(query, JSON.stringify([...identities.values()]));
  const sample = JSON.parse(runPython([path.join(ROOT, 'scripts/soak_metrics.py'), String(process.pid), '--identities', query]));
  assert.equal(sample.unavailableTreeCandidates, 0, 'Incomplete owned tree sample');
  assert.equal(sample.knownUnavailable.length, 0, 'Known identity liveness unavailable');
  return sample;
}
function remember(sample, pid) {
  const identity = sample.processes.find(row => row.pid === pid);
  assert.ok(identity, 'Owned process identity missing');
  identities.set(identity.pid + ':' + identity.created, { pid: identity.pid, created: identity.created });
  return identity;
}
function matching(sample, identity) {
  return sample.knownAlive.some(row => row.pid === identity.pid && row.created === identity.created);
}
function memory(sample, pids) {
  const rows = sample.processes.filter(row => pids.includes(row.pid));
  return { scope: 'proxy_and_direct_backend_only', workingSetBytes: rows.reduce((n, row) => n + row.workingSetBytes, 0),
    sampledProcesses: rows.length, processes: rows };
}
async function waitFor(check, timeout = 10000) {
  const deadline = performance.now() + timeout;
  do { if (await check()) return; await delay(100); } while (performance.now() < deadline);
  throw new Error('Bounded demo condition timed out');
}
const client = new Client({ name: 'mcp-warden-sdk-demo', version: '1' });
let transport, proxyProcess, proxyIdentity, firstIdentity, secondIdentity, stderrBytes = 0, failed = null;
try {
  runPython([path.join(ROOT, 'warden.py'), 'init', '--state', state, '--transport', 'stdio',
    '--restart-policy', 'stateless', '--idle-seconds', String(IDLE)]);
  // Verify the actual generated example; never publish this machine's config or paths.
  const config = JSON.parse(await readFile(path.join(state, 'config.json'), 'utf8'));
  assert.deepEqual(config.profiles.example.manifest.env, {});
  assert.deepEqual(config.profiles.example.manifest.args, ['-I', '-B', '-u', path.join(ROOT, 'examples/echo_server.py')]);
  const initial = status(); assert.equal(initial.sessionCount, 0);
  checks.isolatedInit = true;
  await log('state_initialized', { transport: 'stdio', profile: 'example', idleSeconds: IDLE,
    restartPolicy: 'stateless', exampleSourceSha256: sourceHashes['examples/echo_server.py'], status: initial });
  transport = new StdioClientTransport({ command: python,
    args: ['-I', '-B', '-u', path.join(ROOT, 'warden.py'), 'connect', '--state', state, '--profile', 'example'],
    cwd: out, stderr: 'pipe' });
  transport.stderr.on('data', data => { stderrBytes += data.length; });
  await client.connect(transport, { timeout: 15000 });
  proxyProcess = transport._process; // Capture this SDK-owned process for its cooperative exit code.
  const proxyPid = transport.pid;
  assert.ok(Number.isInteger(proxyPid));
  const tools = (await client.listTools()).tools.map(tool => tool.name);
  for (const required of ['echo', 'sleep']) assert.ok(tools.includes(required));
  checks.handshakeAndCatalog = true;
  await log('sdk_connected', { proxyPid, tools, connectionCount: 1, sdk: sdk.version });
  const beforeNonce = 'demo-before-' + randomUUID();
  const before = (await client.callTool({ name: 'echo', arguments: { value: beforeNonce } })).structuredContent;
  assert.equal(before.value, beforeNonce);
  let sample = measure();
  proxyIdentity = remember(sample, proxyPid); firstIdentity = remember(sample, before.pid);
  checks.echoIntegrity = true;
  await log('echo_before', { valueLabel: 'demo-before-<nonce>', valueSha256: hash(before.value), exactValueMatch: true,
    backend: firstIdentity, proxy: proxyIdentity,
    memory: memory(sample, [proxyPid, before.pid]), status: status() });
  const longNonce = 'demo-long-' + randomUUID();
  const longStart = performance.now(); let longDone = false;
  const pending = client.callTool({ name: 'sleep', arguments: { seconds: LONG, value: longNonce } })
    .then(value => { longDone = true; return value.structuredContent; });
  await log('long_rpc_started', { seconds: LONG, idleSeconds: IDLE, backendPid: before.pid });
  await delay(2500);
  sample = measure();
  assert.equal(longDone, false, 'Long RPC finished before the pending-protection observation');
  assert.equal(matching(sample, firstIdentity), true, 'Backend vanished while RPC was pending');
  await log('long_rpc_protected', { pending: true, sinceRequestSeconds: (performance.now() - longStart) / 1000,
    backend: firstIdentity, sameBackendIdentity: true, status: status() });
  const long = await pending;
  assert.equal(long.value, longNonce); assert.equal(long.pid, before.pid);
  const longSeconds = (performance.now() - longStart) / 1000;
  assert.ok(longSeconds >= LONG);
  checks.pendingProtection = true;
  await log('long_rpc_completed', { valueLabel: 'demo-long-<nonce>', valueSha256: hash(long.value), exactValueMatch: true,
    backendPid: long.pid, durationSeconds: longSeconds });
  const idleStart = performance.now();
  await log('idle_wait_started', { idleSeconds: IDLE, backendPid: before.pid });
  await waitFor(() => { sample = measure(); return !matching(sample, firstIdentity); });
  const idleSeconds = (performance.now() - idleStart) / 1000;
  assert.ok(idleSeconds >= IDLE - .2 && idleSeconds < 10, 'Idle expiry outside bounded window');
  assert.equal(matching(sample, proxyIdentity), true);
  let idleStatus;
  await waitFor(() => {
    idleStatus = status();
    return idleStatus.sessionCount === 1 && idleStatus.samples[0].backendPid === null;
  });
  sample = measure(); assert.equal(matching(sample, firstIdentity), false);
  assert.equal(matching(sample, proxyIdentity), true);
  checks.cleanIdle = true;
  await log('idle_expired', { sinceLastResponseSeconds: idleSeconds, oldBackend: firstIdentity,
    oldIdentityAlive: false, proxyIdentityAlive: true, memory: memory(sample, [proxyPid]), status: idleStatus });
  const afterNonce = 'demo-after-' + randomUUID(), wakeStart = performance.now();
  const after = (await client.callTool({ name: 'echo', arguments: { value: afterNonce } })).structuredContent;
  const wakeSeconds = (performance.now() - wakeStart) / 1000;
  assert.equal(after.value, afterNonce); assert.equal(transport.pid, proxyPid);
  sample = measure(); secondIdentity = remember(sample, after.pid);
  assert.notEqual(secondIdentity.pid, firstIdentity.pid);
  assert.notEqual(secondIdentity.created, firstIdentity.created);
  assert.equal(matching(sample, proxyIdentity), true);
  checks.sameSdkConnection = true; checks.newBackendGeneration = true;
  const awakeMemory = memory(sample, [proxyPid, after.pid]);
  assert.equal(awakeMemory.sampledProcesses, 2); checks.memoryScope = true;
  await log('echo_after_wake', { valueLabel: 'demo-after-<nonce>', valueSha256: hash(after.value), exactValueMatch: true, wakeSeconds,
    connectionCount: 1, sameProxyIdentity: true, backend: secondIdentity, proxy: proxyIdentity,
    memory: awakeMemory, status: status() });
} catch (error) {
  failed = error;
  await log('demo_failed', { code: 'demo_assertion_or_operation_failed' });
} finally {
  await client.close();
  if (proxyIdentity) {
    await waitFor(() => measure().knownAlive.length === 0);
    const finalStatus = status(); assert.equal(finalStatus.sessionCount, 0);
    const final = measure(); assert.equal(final.knownAlive.length, 0);
    assert.equal(proxyProcess.exitCode, 0, 'Proxy did not exit cooperatively');
    checks.cleanup = true;
    await log('cleanup', { remainingOwnedIdentities: final.knownAlive, status: finalStatus,
      proxyExitCode: proxyProcess.exitCode, stderrBytes, ownedIdentitiesChecked: [...identities.values()] });
  }
}
assert.equal(stderrBytes, 0, 'Unexpected diagnostics; inspect private run locally');
const result = { passed: !failed && Object.keys(checks).length === 9 && Object.values(checks).every(Boolean),
  checks, checkCount: Object.keys(checks).length, elapsedSeconds: at(), sourceCommit: plan.sourceCommit,
  observedBackends: [firstIdentity, secondIdentity].filter(Boolean).map(({pid, created}) => ({pid, created})),
  scope: plan.scenario, modelCalls: 0, connectionCount: 1 };
await writeFile(path.join(publicDir, 'result.json'), JSON.stringify(result, null, 2) + '\n');
console.log(JSON.stringify({ event: 'result', ...result }));
if (failed) throw failed;
assert.equal(result.passed, true, 'Demo criteria incomplete');
