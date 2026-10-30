import test from 'node:test';
import assert from 'node:assert/strict';
import { stableRootDeltas } from '../scripts/benchmark_deltas.mjs';

const row = (pid, created, cpuSeconds, pageFaultCount) => ({ pid, created, cpuSeconds, pageFaultCount });
const sample = (sampledAt, processes) => ({ sampledAt, roots: [10], processes });

test('wake deltas include only an explicit root with the same creation identity', () => {
  const result = stableRootDeltas(sample(1, [row(10, '100', 1, 30)]),
    sample(2, [row(10, '100', 1.25, 45), row(11, '200', 9, 999)]));
  assert.equal(result.cpuSeconds, .25);
  assert.equal(result.pageFaultCount, 15);
  assert.equal(result.intervalSeconds, 1);
  assert.equal(result.roots.length, 1);
  assert.deepEqual(result.invalidRoots, []);
});

test('PID reuse and departed roots are excluded rather than reported as zero wake cost', () => {
  for (const processes of [[], [row(10, '200', 10, 100)]]) {
    const result = stableRootDeltas(sample(1, [row(10, '100', 1, 30)]), sample(2, processes));
    assert.equal(result.cpuSeconds, null);
    assert.equal(result.pageFaultCount, null);
    assert.equal(result.excludedRoots.length, 1);
  }
});

test('missing or regressed page-fault/CPU counters remain unknown', () => {
  for (const end of [row(10, '100', .5, 40), row(10, '100', 2, 29), row(10, '100', 2, undefined)]) {
    const result = stableRootDeltas(sample(1, [row(10, '100', 1, 30)]), sample(2, [end]));
    assert.equal(result.cpuSeconds, null);
    assert.equal(result.pageFaultCount, null);
    assert.equal(result.invalidRoots.length, 1);
  }
});
