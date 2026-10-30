/** Standard MCP stdio <-> loopback Streamable HTTP adapter.
 * Forwarding is delegated to the official SDK. No tool call is replayed.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { OWNER_ENV_KEYS } from './gateway.mjs';

export async function connect(options) {
  const state = path.resolve(options.state);
  const config = JSON.parse(readFileSync(path.join(state, 'config.json'), 'utf8').replace(/^\uFEFF/, ''));
  if (!Number.isInteger(config.port) || config.port < 1024 || config.port > 65535
      || !/^[A-Za-z0-9_-]+$/.test(options.profile)
      || !Object.hasOwn(config.profiles, options.profile)
      || !/^[1-9][0-9]*$/.test(options.ownerPid)
      || !/^[1-9][0-9]*$/.test(options.ownerCreated)) throw new Error('invalid_configuration');
  const token = readFileSync(path.join(state, 'token'), 'utf8').trim();
  if (token.length < 32 || token.length > 4096 || /\s/.test(token)) throw new Error('invalid_token');
  const env = {};
  for (const [key, value] of Object.entries(process.env)) {
    const upper = key.toUpperCase();
    if (OWNER_ENV_KEYS.has(upper)) env[upper] = value;
  }
  const encode = value => Buffer.from(value, 'utf8').toString('base64');
  const http = new StreamableHTTPClientTransport(new URL(`http://127.0.0.1:${config.port}/mcp/${options.profile}`), {
    requestInit: { headers: {
      Authorization: 'Bearer ' + token,
      'X-Mcp-Owner-Pid': options.ownerPid,
      'X-Mcp-Owner-Created': options.ownerCreated,
      'X-Mcp-Cwd-B64': encode(process.cwd()),
      'X-Mcp-Env-B64': encode(JSON.stringify(env)),
    } },
    reconnectionOptions: { maxRetries: 0, initialReconnectionDelay: 1000,
      maxReconnectionDelay: 1000, reconnectionDelayGrowFactor: 1 },
  });
  const stdio = new StdioServerTransport();
  let closing = false;
  let closePromise;
  const shutdown = () => {
    if (closePromise) return closePromise;
    closing = true;
    closePromise = (async () => {
      // Stop consuming client messages immediately. A loopback listener can
      // accept DELETE and never answer; shutdown must still release ownership.
      process.stdin.pause();
      await stdio.close().catch(() => {});
      let timer;
      try {
        await Promise.race([
          http.terminateSession().catch(() => {}),
          new Promise(resolve => { timer = setTimeout(resolve, 2000); }),
        ]);
      } finally {
        clearTimeout(timer);
        // SDK close aborts all outstanding fetches, including timed-out DELETE.
        await http.close().catch(() => {});
      }
    })();
    return closePromise;
  };
  // The SDK stdio transport listens to input errors only. Stream write errors
  // such as EPIPE are EventEmitter errors, not send() promise rejections.
  process.stdout.on('error', () => { void shutdown(); });
  http.onerror = () => {}; // SDK errors may contain response payloads; do not log them.
  stdio.onerror = () => { void shutdown(); };
  http.onmessage = message => { if (!closing) void stdio.send(message).catch(() => shutdown()); };
  stdio.onmessage = message => {
    if (closing) return;
    // Modern clients probe stdio before the legacy initialize handshake.
    // This adapter is legacy: a correlated JSON-RPC error permits the
    // documented fallback without forwarding an invalid HTTP initialization.
    if (!http.sessionId && message.method === 'server/discover' && Object.hasOwn(message, 'id')) {
      void stdio.send({ jsonrpc: '2.0', id: message.id,
        error: { code: -32601, message: 'Method not found; use the initialize handshake.' } }).catch(() => shutdown());
      return;
    }
    void http.send(message).catch(async error => {
      const expired = error?.code === 404;
      try {
        if (!closing && !process.stdout.destroyed && Object.hasOwn(message, 'id') && Object.hasOwn(message, 'method')) {
          await stdio.send({ jsonrpc: '2.0', id: message.id, error: {
            code: -32000,
            message: expired ? 'MCP session expired. Reconnect the client; temporary backend state was reset.'
              : 'MCP gateway request failed. No automatic replay was attempted.',
          } }).catch(() => {});
        }
      } finally {
        // After a failed POST the backend may have executed the operation.
        // Close the uncertain session; neither retry nor retain it silently.
        await shutdown();
      }
    }).catch(() => shutdown());
  };
  process.stdin.once('end', () => { void shutdown(); });
  process.stdin.once('close', () => { void shutdown(); });
  process.once('SIGINT', () => { void shutdown(); });
  process.once('SIGTERM', () => { void shutdown(); });
  try {
    await http.start();
    await stdio.start();
  } catch {
    await shutdown();
    throw new Error('connector_start_failed');
  }
  return { shutdown };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const args = process.argv.slice(2);
    const options = {};
    const fields = { '--state': 'state', '--profile': 'profile', '--owner-pid': 'ownerPid', '--owner-created': 'ownerCreated' };
    if (args.length !== 8) throw new Error('invalid_arguments');
    for (let i = 0; i < args.length; i += 2) {
      if (!Object.hasOwn(fields, args[i]) || !args[i + 1]) throw new Error('invalid_arguments');
      options[fields[args[i]]] = args[i + 1];
    }
    await connect(options);
  } catch {
    process.stderr.write('mcp-warden: connector unavailable\n');
    process.exitCode = 2;
  }
}
