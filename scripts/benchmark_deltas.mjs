/** Counters for the same explicit root identity at both sample boundaries only. */
export function stableRootDeltas(before, after) {
  const roots = [], excludedRoots = [], invalidRoots = [];
  for (const pid of new Set(before.roots)) {
    const start = before.processes.find(row => row.pid === pid);
    const end = after.processes.find(row => row.pid === pid);
    if (!start || !end || start.created !== end.created) {
      excludedRoots.push({ pid, reason: !start || !end ? 'not_alive_at_both_samples' : 'creation_identity_changed' });
      continue;
    }
    const cpuSeconds = end.cpuSeconds - start.cpuSeconds;
    const pageFaultCount = end.pageFaultCount - start.pageFaultCount;
    if (![cpuSeconds, pageFaultCount].every(value => Number.isFinite(value) && value >= 0)) {
      invalidRoots.push({ pid, created: start.created, reason: 'counter_missing_or_regressed' });
      continue;
    }
    roots.push({ pid, created: start.created, cpuSeconds, pageFaultCount });
  }
  return { scope: 'same_explicit_root_pid_and_creation_only',
    intervalSeconds: after.sampledAt - before.sampledAt, roots, excludedRoots, invalidRoots,
    cpuSeconds: roots.length ? roots.reduce((sum, row) => sum + row.cpuSeconds, 0) : null,
    pageFaultCount: roots.length ? roots.reduce((sum, row) => sum + row.pageFaultCount, 0) : null };
}
