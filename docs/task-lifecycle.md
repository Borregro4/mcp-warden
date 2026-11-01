# Task-bearing sessions retain their backend

MCP Tasks separate the acknowledgment of a request from completion of the
underlying operation. A `tools/call` response can contain a Task in `working`
or `input_required` state while execution continues. Task status notifications
are optional, and the recommended polling interval can exceed Warden's idle
timeout. An empty JSON-RPC request ledger therefore cannot prove clean idle.

The current Warden policy is conservative: **automatic idle expiry is disabled
for the entire connection** if either peer declares `capabilities.tasks` during
initialization, including an empty object. It is also disabled if Warden observes
a request with `params.task`, in either direction, even when negotiation was
omitted. This applies to both manual and stateless restart policies. Ordinary
non-task sessions retain their existing idle and stateless wake behavior.

The proxy exposes a fixed `idle_expiry_blocked_reason` value:

- `mcp_tasks_capability`: either peer advertised the task capability.
- `task_augmented_request`: a task-augmented request was observed without a
  capability already establishing that protection.

These values are sticky for that connection. They contain no task IDs or
request contents. Reconnecting starts a new connection and evaluates negotiation
again; removing a capability while retaining task usage does not bypass the
request guard. Warden still performs normal owned-process cleanup on client
disconnect or transport failure.

This is **not complete Task lifecycle support**. Warden does not infer task
completion from responses, notifications, cancellation, or TTL; it does not
poll, replay, recover, or preserve task results across a backend restart. The
tradeoff is that a task-capable backend remains resident until disconnect,
even when no Task was actually started or all Tasks have completed. This is
preferable to interrupting active work or deleting retrievable task results.

The regression fixture returns `working` immediately, recommends a two-second
polling interval, emits no status notifications, and completes its worker after
500 ms. With a 100 ms idle policy the backend must survive to record completion.
Another regression verifies that an ordinary connection still stops and wakes.

Primary protocol reference: [MCP 2025-11-25 Tasks](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2025-11-25/basic/utilities/tasks.mdx),
especially Creating Tasks, Getting Tasks, and Task Status Notification. Tasks
are experimental in that protocol revision; this guard does not claim support
for every later extension or protocol version.
