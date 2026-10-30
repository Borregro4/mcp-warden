# Optional idle page release on Windows

The stdio manifest accepts `"release_idle_pages": true`. The default is
`false`; only a JSON boolean is accepted. This option applies to the Python
proxy's own working set after its backend has successfully stopped for clean
idle. It does not run during a pending request, reverse callback, write,
partial frame, restart handshake, cancellation with uncertain outcome, transport
error or shutdown failure.

The proxy calls Windows `K32EmptyWorkingSet(GetCurrentProcess())` once per clean
idle stop. It does not open another process or trim any backend, agent, shared
daemon or unrelated application. The API is best effort: failure leaves the
connection and its existing manual/stateless restart policy unchanged.

This releases resident working-set pages, **not committed private allocations**.
Windows may keep removed pages in its transition cache, and it can trim working
sets automatically under memory pressure. Referencing removed pages causes
page faults; waking can therefore cost additional CPU and latency. A smaller
working-set counter alone is not evidence of the same reduction in system RAM
or committed memory.

The preliminary local rotated experiment on 2026-10-30 used twelve paired runs of
the same synthetic 32 MiB backend, with fresh proxy processes and accelerated
0.4-second idle. Idle working-set medians were 24.11 MiB without release and
9.54 MiB with release. Wake medians were 144.17 ms and 172.87 ms; proxy-only page
fault increments were 17.5 and 989.5. Observed proxy CPU increments during wake
were 15.625 ms in both groups, with coarse observed accounting. Private-byte medians were
11.04 and 12.09 MiB. The experimental implementation used `psapi.EmptyWorkingSet`,
which Microsoft documents as a wrapper for `K32EmptyWorkingSet`; the product
uses the direct kernel export and avoids loading the additional wrapper DLL.

These are small local raw-JSON-RPC measurements, not the hosted four-variant
comparison or native long-session results. They justify an explicit option
with a paging tradeoff, not a default claim of free memory savings. Independent
native tests and hosted comparisons are tracked separately.

Sources verified on 2026-10-30:

- [Microsoft: EmptyWorkingSet](https://learn.microsoft.com/en-us/windows/win32/api/psapi/nf-psapi-emptyworkingset)
- [Microsoft: working sets and page faults](https://learn.microsoft.com/en-us/windows/win32/memory/working-set)
