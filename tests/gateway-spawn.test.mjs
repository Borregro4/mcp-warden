// No processes or listeners. Real detached ownership is covered by the Windows fixture.
import assert from 'node:assert/strict';
import test from 'node:test';
import { ownedBridgeSpawnOptions } from '../runtime/gateway.mjs';

test('Windows detached bridge launch is explicit and retains owned stdio pipes', () => {
  const standard = ownedBridgeSpawnOptions(undefined, 'win32');
  assert.equal(standard.detached, false);
  const optedIn = ownedBridgeSpawnOptions(true, 'win32');
  assert.equal(optedIn.detached, true);
  assert.deepEqual(optedIn.stdio, ['pipe', 'pipe', 'pipe']);
  assert.equal(optedIn.shell, false);
  assert.equal(optedIn.windowsHide, true);
  assert.equal(ownedBridgeSpawnOptions(false, 'win32').detached, false);
  assert.equal(ownedBridgeSpawnOptions(true, 'linux').detached, false);
  assert.equal(ownedBridgeSpawnOptions(true, 'darwin').detached, false);
});

test('Windows detached bridge launch rejects non-boolean configuration', () => {
  for (const value of [null, 0, 1, 'true', {}, []])
    assert.throws(() => ownedBridgeSpawnOptions(value), /invalid_windows_detached_bridge/);
});
