// These lifecycle regressions use fake owned children: no processes or listeners.
import assert from 'node:assert/strict';
import test from 'node:test';
import { Gateway } from '../runtime/gateway.mjs';

async function flush() {
  for (let i = 0; i < 20; i++) await Promise.resolve();
}

function fixture(t, { transportStalls = false, exited = false } = {}) {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let resolveExit;
  const record = { key: 'owned-session', id: 'http-session', profile: 'fixture',
    context: { pid: 1, created: '1' }, pending: new Map(), callbacks: new Map(),
    child: { exitCode: exited ? 0 : null, signalCode: null,
      stdin: { end() { record.eofCount++; } },
      kill() { record.killCount++; return false; } },
    eofCount: 0, killCount: 0,
    transport: { close: () => transportStalls ? new Promise(() => {}) : Promise.resolve() } };
  record.exited = new Promise(resolve => { resolveExit = resolve; });
  if (exited) resolveExit();
  const gateway = Object.create(Gateway.prototype);
  Object.assign(gateway, { profiles: { fixture: { shutdown_grace_seconds: .5 } },
    sessions: new Map([[record.id, record]]), records: new Map([[record.key, record]]),
    idleCounters: new Map(), active: new Set([record]), events: [] });
  gateway.event = (event, _record, detail = {}) => gateway.events.push({ event, ...detail });
  return { gateway, record, exit() { record.child.exitCode = 0; resolveExit(); } };
}

test('a child stuck after kill has bounded cleanup without freeing its capacity', async t => {
  const h = fixture(t);
  let settled = false;
  const closed = h.gateway.closeRecord(h.record, 'idle_timeout').then(() => { settled = true; });
  await flush();
  t.mock.timers.tick(8000);
  await flush();
  t.mock.timers.tick(4000);
  await flush();
  assert.equal(settled, true, 'cleanup must return after its owned-child budgets');
  await closed;
  assert.equal(h.record.killCount, 1);
  assert.equal(h.gateway.active.has(h.record), true, 'a live child still consumes capacity');
  assert.equal(h.gateway.sessions.size, 0, 'the old HTTP session is no longer usable');
  assert.ok(h.gateway.events.some(e => e.event === 'bridge_cleanup_incomplete'));
  assert.equal(h.gateway.events.some(e => ['bridge_closed', 'session_closed'].includes(e.event)), false);
  h.exit();
  await flush();
  assert.equal(h.gateway.active.size, 0, 'the actual exit reaps the quarantined record');
  assert.equal(h.gateway.events.filter(e => e.event === 'bridge_closed').length, 1);
  assert.equal(h.gateway.events.filter(e => e.event === 'session_closed').length, 1);
  await h.gateway.closeRecord(h.record, 'delete');
  assert.equal(h.record.killCount, 1, 'cleanup remains idempotent after the late exit');
});

test('an exit observed after a due timer avoids a false timeout and unnecessary kill', async t => {
  const h = fixture(t);
  const closing = h.gateway.closeRecord(h.record, 'delete');
  await flush();
  // Timer callback ran, but its continuation has not: the exit can already be known.
  t.mock.timers.tick(8000);
  h.exit();
  await flush();
  await closing;
  assert.equal(h.record.killCount, 0);
  assert.equal(h.gateway.events.some(e => e.event === 'bridge_cleanup_timeout'), false);
  assert.equal(h.gateway.active.size, 0);
});

test('a stalled transport close cannot prevent cleanup of an already exited child', async t => {
  const h = fixture(t, { transportStalls: true, exited: true });
  let settled = false;
  const closing = h.gateway.closeRecord(h.record, 'delete').then(() => { settled = true; });
  await flush();
  t.mock.timers.tick(4000);
  await flush();
  assert.equal(settled, true, 'transport close must have its own bounded budget');
  await closing;
  assert.equal(h.gateway.active.size, 0);
  assert.equal(h.record.killCount, 0);
  assert.ok(h.gateway.events.some(e => e.event === 'transport_close_timeout'));
  assert.equal(h.gateway.events.filter(e => e.event === 'session_closed').length, 1);
});

test('gateway shutdown returns honestly with an unreaped owned child', async t => {
  const h = fixture(t);
  let connectionsClosed = false;
  h.gateway.server = { close(done) { done(); }, closeAllConnections() { connectionsClosed = true; } };
  let settled = false;
  const closing = h.gateway.close().then(() => { settled = true; });
  await flush();
  t.mock.timers.tick(8000);
  await flush();
  t.mock.timers.tick(4000);
  await flush();
  assert.equal(settled, true);
  await closing;
  assert.equal(connectionsClosed, true);
  assert.equal(h.gateway.active.size, 1);
  assert.ok(h.gateway.events.some(e => e.event === 'gateway_cleanup_incomplete'));
  assert.equal(h.gateway.events.some(e => e.event === 'gateway_closed'), false);
  h.exit();
  await flush();
  assert.equal(h.gateway.events.filter(e => e.event === 'gateway_closed').length, 1);
});
