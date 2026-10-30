// No listener, wrapper or backend is started by these checks.
import assert from 'node:assert/strict';
import { realpathSync } from 'node:fs';
import { decodeBase64, parseContext, validIdentity, validInitialize, normalizeAccept, idleTimeoutMilliseconds } from '../runtime/gateway.mjs';
const encode = value => Buffer.from(value).toString('base64');
const cwd = realpathSync(process.cwd());
const created = '134372353234567891'; // Deliberately exceeds Number.MAX_SAFE_INTEGER.
const headers = { 'x-mcp-owner-pid': '4321', 'x-mcp-owner-created': created,
  'x-mcp-cwd-b64': encode(cwd), 'x-mcp-env-b64': encode(JSON.stringify({ PATH: 'private-path', SYSTEMROOT: 'C:\\Windows' })) };
const context = parseContext(headers, cwd);
assert.equal(context.created, created);
assert.equal(context.cwd, cwd);
assert.deepEqual(context.env, { PATH: 'private-path', SYSTEMROOT: 'C:\\Windows' });
assert.throws(() => parseContext({ ...headers, 'x-mcp-owner-created': Number(created) }));
assert.throws(() => parseContext({ ...headers, 'x-mcp-owner-pid': '4294967296' }));
assert.throws(() => parseContext({ ...headers, 'x-mcp-env-b64': encode('{"NODE_OPTIONS":"--require bad"}') }));
assert.throws(() => parseContext({ ...headers, 'x-mcp-env-b64': encode('{"PATH":"a\\u0000b"}') }));
assert.throws(() => decodeBase64('a==='));
assert.throws(() => decodeBase64('/w=='));
const identity = { status: 'ready', owner_pid: 4321, owner_created: created, bridge_pid: 8765, bridge_created: '134372353234567999' };
assert.ok(validIdentity(identity, context, 8765));
assert.equal(validIdentity({ ...identity, owner_created: '134372353234567890' }, context, 8765), false);
assert.equal(validIdentity({ ...identity, bridge_pid: 8766 }, context, 8765), false);
assert.equal(validIdentity({ ...identity, bridge_created: Number(identity.bridge_created) }, context, 8765), false);
const initialize = { jsonrpc: '2.0', id: 0, method: 'initialize', params: {
  protocolVersion: '2025-11-25', capabilities: {}, clientInfo: { name: 'fixture', version: '1' } } };
assert.ok(validInitialize(initialize));
assert.equal(validInitialize({ ...initialize, id: null }), false);
assert.equal(validInitialize({ ...initialize, params: { protocolVersion: '2025-11-25' } }), false);
assert.equal(validInitialize({ ...initialize, method: 'tools/list' }), false);
assert.equal(validInitialize([initialize]), false);
assert.equal(normalizeAccept('*/*'), 'application/json;q=1, text/event-stream;q=1');
assert.equal(normalizeAccept('application/*;q=0.5, text/*;q=0.8'), 'application/json;q=0.5, text/event-stream;q=0.8');
assert.equal(normalizeAccept('*/*;q=1, application/json;q=0'), null);
assert.equal(normalizeAccept('application/json;q=1, text/event-stream;q=0.001'), 'application/json;q=1, text/event-stream;q=0.001');
assert.equal(normalizeAccept('text/*', false), 'text/event-stream;q=1');
assert.equal(normalizeAccept('application/json'), null);
assert.equal(normalizeAccept('application/json;q=2, text/event-stream'), null);
assert.equal(idleTimeoutMilliseconds(), 900000);
assert.equal(idleTimeoutMilliseconds(0), 0);
assert.equal(idleTimeoutMilliseconds(.75), 750);
for (const value of [-1, NaN, Infinity, '900', true, null])
  assert.throws(() => idleTimeoutMilliseconds(value));
console.log(JSON.stringify({ passed: 34, listeners: 0, childProcesses: 0, ownerCreatedPreserved: context.created === created }));
