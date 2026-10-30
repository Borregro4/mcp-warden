/** Local integration policy around unmodified Supergateway 4.2.0 transports.
 * HTTP/SSE routing, callbacks, cancellation and stdio parsing stay upstream.
 * The Python owned_bridge retains Windows process handles and backend cleanup.
 */
import { createServer } from 'node:http';
import { createHash, randomUUID, timingSafeEqual } from 'node:crypto';
import { spawn } from 'node:child_process';
import { appendFileSync, existsSync, mkdirSync, readFileSync, realpathSync, statSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { StatefulSession } from 'supergateway/dist/gateways/statefulSession.js';
import { SessionAccessCounter } from 'supergateway/dist/lib/sessionAccessCounter.js';
import { processPeer } from 'supergateway/dist/lib/peer.js';
import { InitializeRequestSchema, InitializeResultSchema, InitializedNotificationSchema,
  JSONRPCMessageSchema } from '@modelcontextprotocol/sdk/types.js';

export const OWNER_ENV_KEYS = new Set(('PATH PATHEXT SHELL COMSPEC SYSTEMROOT WINDIR SYSTEMDRIVE USERNAME USERDOMAIN USERPROFILE '
  + 'HOMEDRIVE HOMEPATH PROGRAMFILES PROGRAMFILES(X86) PROGRAMW6432 PROGRAMDATA LOCALAPPDATA APPDATA '
  + 'TEMP TMP TMPDIR POWERSHELL PWSH CODEX_CA_CERTIFICATE SSL_CERT_FILE REQUESTS_CA_BUNDLE '
  + 'CURL_CA_BUNDLE NODE_EXTRA_CA_CERTS GIT_SSL_CAINFO CARGO_HTTP_CAINFO PIP_CERT BUNDLE_SSL_CA_CERT NPM_CONFIG_CAFILE').split(' '));
const sourcePath = fileURLToPath(import.meta.url);
const quiet = { info() {}, error() {} }; // Upstream debug output can contain complete RPC payloads.
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const decimal = value => typeof value === 'string' && /^[1-9][0-9]*$/.test(value) && BigInt(value) < (1n << 64n);
const canonicalEnv = value => JSON.stringify(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)));

export function idleTimeoutMilliseconds(seconds = 900) {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0
      || seconds > Number.MAX_SAFE_INTEGER / 1000) throw new Error('invalid_idle_timeout');
  return seconds * 1000;
}

export function decodeBase64(value) {
  if (typeof value !== 'string' || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value))
    throw new Error('invalid_context');
  return new TextDecoder('utf-8', { fatal: true }).decode(Buffer.from(value, 'base64'));
}

export function parseContext(headers, allowedRoot = null) {
  const pidText = headers['x-mcp-owner-pid'];
  const created = headers['x-mcp-owner-created'];
  if (!decimal(pidText) || !decimal(created)) throw new Error('invalid_context');
  const pid = Number(pidText);
  if (!Number.isSafeInteger(pid) || pid > 0xffffffff) throw new Error('invalid_context');
  const rawCwd = decodeBase64(headers['x-mcp-cwd-b64']);
  if (!path.isAbsolute(rawCwd)) throw new Error('invalid_context');
  const cwd = realpathSync(rawCwd);
  if (!statSync(cwd).isDirectory()) throw new Error('invalid_context');
  if (allowedRoot !== null) {
    const relative = path.relative(allowedRoot, cwd);
    if (relative === '..' || relative.startsWith('..' + path.sep) || path.isAbsolute(relative))
      throw new Error('invalid_context');
  }
  const env = JSON.parse(decodeBase64(headers['x-mcp-env-b64']));
  if (!object(env) || Object.entries(env).some(([key, value]) =>
    !OWNER_ENV_KEYS.has(key) || typeof value !== 'string' || value.includes('\0')))
    throw new Error('invalid_context');
  return { pid, created, cwd, env, envKey: canonicalEnv(env) };
}

export function validIdentity(identity, context, childPid) {
  return object(identity) && identity.status === 'ready' && identity.owner_pid === context.pid
    && identity.owner_created === context.created && identity.bridge_pid === childPid
    && decimal(identity.bridge_created);
}

export function validInitialize(body) {
  return object(body) && Object.hasOwn(body, 'id') && body.method === 'initialize'
    && JSONRPCMessageSchema.safeParse(body).success && InitializeRequestSchema.safeParse(body).success;
}

export function normalizeAccept(value, needsJson = true) {
  if (typeof value !== 'string') return null;
  const ranges = value.split(',').map(part => {
    const [type, ...parameters] = part.trim().toLowerCase().split(';').map(item => item.trim());
    const quality = parameters.find(parameter => parameter.startsWith('q='))?.slice(2) ?? '1';
    const q = /^(?:0(?:\.[0-9]{0,3})?|1(?:\.0{0,3})?)$/.test(quality) ? Number(quality) : 0;
    return { type, q };
  });
  const required = needsJson ? ['application/json', 'text/event-stream'] : ['text/event-stream'];
  const explicit = [];
  for (const type of required) {
    let best = -1, quality = 0;
    for (const range of ranges) {
      const specificity = range.type === type ? 2 : range.type === type.split('/')[0] + '/*' ? 1 : range.type === '*/*' ? 0 : -1;
      if (specificity > best) { best = specificity; quality = range.q; }
      else if (specificity === best) quality = Math.max(quality, range.q);
    }
    if (best < 0 || quality <= 0) return null;
    explicit.push(type + ';q=' + quality);
  }
  // The upstream SDK recognizes explicit media types. Preserve the effective
  // RFC wildcard quality while supplying equivalent explicit types to it.
  return explicit.join(', ');
}

function reject(res, status, text) {
  if (res.headersSent) { res.destroy(); return; }
  res.writeHead(status, { 'Content-Type': 'text/plain; charset=utf-8' }).end(text);
}
function json(res, body) {
  res.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(body));
}
function sameContext(record, profile, context) {
  return record.profile === profile && record.context.pid === context.pid
    && record.context.created === context.created && record.context.cwd === context.cwd
    && record.context.envKey === context.envKey;
}
function bodyOf(req, timeoutMs, maximumBytes) {
  return new Promise((resolve, rejectBody) => {
    const chunks = []; let size = 0, settled = false;
    const finish = (error, body) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      req.removeListener('data', onData); req.removeListener('end', onEnd);
      req.removeListener('aborted', onAbort); req.removeListener('error', onAbort);
      if (error) rejectBody(error); else resolve(body);
    };
    const onData = chunk => {
      size += chunk.length;
      if (size > maximumBytes) { finish(new Error('body_too_large')); req.resume(); }
      else chunks.push(chunk);
    };
    const onEnd = () => {
      try { finish(null, JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks)))); }
      catch { finish(new Error('invalid_json')); }
    };
    const onAbort = () => finish(new Error('request_aborted'));
    const timer = setTimeout(() => { finish(new Error('body_timeout')); req.resume(); }, timeoutMs);
    req.on('data', onData); req.once('end', onEnd); req.once('aborted', onAbort); req.once('error', onAbort);
  });
}

export class Gateway {
  constructor(config) {
    this.config = config;
    this.runDir = realpathSync(config.run_dir);
    this.allowedRoot = config.allowed_cwd_root == null ? null : realpathSync(config.allowed_cwd_root);
    this.token = readFileSync(config.token_file, 'utf8').trim();
    if (this.token.length < 32) throw new Error('invalid_configuration');
    if (!Number.isInteger(config.port) || config.port < 1 || config.port > 65535 || !object(config.profiles))
      throw new Error('invalid_configuration');
    for (const field of ['bridge_python', 'owned_bridge', 'bridge'])
      if (typeof config[field] !== 'string' || !path.isAbsolute(config[field]) || !existsSync(config[field]))
        throw new Error('invalid_configuration');
    this.profiles = config.profiles;
    this.timeouts = new Map(Object.entries(this.profiles).map(([name, profile]) => {
      const seconds = Number(profile.handshake_timeout_seconds ?? config.handshake_timeout_seconds ?? 120);
      if (!Number.isFinite(seconds) || seconds <= 0 || !object(profile.manifest)) throw new Error('invalid_configuration');
      return [name, seconds * 1000];
    }));
    this.host = '127.0.0.1:' + config.port;
    this.authDigest = createHash('sha256').update('Bearer ' + this.token).digest();
    this.sourceHash = createHash('sha256').update(readFileSync(sourcePath)).digest('hex');
    this.sessions = new Map(); this.active = new Set(); this.closing = false;
    this.maxSessions = config.max_sessions ?? 32;
    this.maxSessionsPerProfile = config.max_sessions_per_profile ?? 16;
    this.maxRequestBytes = config.max_request_bytes ?? 1048576;
    for (const value of [this.maxSessions, this.maxSessionsPerProfile, this.maxRequestBytes])
      if (!Number.isSafeInteger(value) || value < 1) throw new Error('invalid_configuration');
    this.records = new Map(); this.idleExpiredSessionCount = 0;
    this.idleTimeouts = new Map(Object.entries(this.profiles).map(([name, profile]) =>
      [name, idleTimeoutMilliseconds(profile.idle_session_timeout_seconds ?? config.idle_session_timeout_seconds)]));
    this.idleCounters = new Map([...this.idleTimeouts].map(([name, timeout]) => [name,
      timeout === 0 ? null : new SessionAccessCounter(timeout, key => {
        const record = this.records.get(key);
        if (!record || record.closing) return;
        // The upstream counter holds POSTs, initialization and RPCs in both
        // directions. A passive GET stream alone is not work in progress.
        this.idleExpiredSessionCount++;
        void this.closeRecord(record, 'idle_timeout').catch(() => this.event('idle_cleanup_failed', record));
      }, quiet)]));
  }

  hold(record, reason) {
    const counter = this.idleCounters.get(record.profile);
    if (!counter || record.closing) return () => {};
    counter.inc(record.key, reason);
    let released = false;
    return () => {
      if (released) return;
      released = true;
      if (!record.closing) counter.dec(record.key, reason);
    };
  }

  beginRpc(record, pending, id) {
    if (!pending.has(id)) pending.set(id, this.hold(record, 'RPC in progress'));
  }

  finishRpc(pending, id) {
    const release = pending.get(id);
    if (release) { pending.delete(id); release(); }
  }

  holdPost(record, res) {
    const release = this.hold(record, 'POST body or response');
    res.once('finish', release); res.once('close', release);
  }

  event(event, record = null, data = {}) {
    const entry = { event, at: Date.now() / 1000, ...data };
    if (record) Object.assign(entry, { session: record.key, profile: record.profile,
      owner_pid: record.context.pid, owner_created: record.context.created,
      ...(record.id ? { http_session: record.id } : {}) });
    appendFileSync(path.join(this.runDir, 'events.jsonl'), JSON.stringify(entry) + '\n');
  }

  authorize(req, res) {
    if (req.headers.host !== this.host) { reject(res, 421, 'Invalid Host'); return false; }
    if (req.headers.origin !== undefined && req.headers.origin !== 'http://' + this.host) {
      reject(res, 403, 'Invalid Origin'); return false;
    }
    const actual = createHash('sha256').update(req.headers.authorization ?? '').digest();
    if (!timingSafeEqual(this.authDigest, actual)) { reject(res, 401, 'Unauthorized'); return false; }
    return true;
  }

  async stopChild(record) {
    if (record.childStop) return record.childStop;
    record.childStop = (async () => {
      const child = record.child;
      if (!child) return;
      child.stdin.end();
      const grace = (Number(this.profiles[record.profile].shutdown_grace_seconds ?? 0.5) + 7) * 1000;
      let timer;
      const exited = await Promise.race([record.exited.then(() => true),
        new Promise(resolve => { timer = setTimeout(() => resolve(false), grace); })]);
      clearTimeout(timer);
      if (!exited) {
        this.event('bridge_cleanup_timeout', record);
        child.kill(); // Exact ChildProcess handle; never enumerate or kill a PID tree.
        await record.exited;
      }
      this.event('bridge_closed', record, { exit_code: child.exitCode });
    })();
    return record.childStop;
  }

  async closeRecord(record, reason, closeTransport = true) {
    if (record.closePromise) return record.closePromise;
    record.closing = true; record.reason = reason; clearTimeout(record.handshakeTimer);
    this.idleCounters.get(record.profile)?.clear(record.key, false, reason);
    this.records.delete(record.key);
    record.pending.clear(); record.callbacks.clear();
    if (record.id) this.sessions.delete(record.id);
    record.closePromise = (async () => {
      if (closeTransport) {
        try { await record.transport?.close(); } catch { /* child cleanup still runs */ }
      }
      await this.stopChild(record);
      this.active.delete(record);
      this.event('session_closed', record, { reason: record.reason });
    })();
    return record.closePromise;
  }

  async startRecord(profile, context, req, res, initialize) {
    const record = { key: randomUUID(), profile, context, pending: new Map(), callbacks: new Map(), responses: new Set(),
      initialized: false, initializeSucceeded: false, initId: initialize.id, closing: false };
    this.active.add(record);
    this.records.set(record.key, record);
    record.releaseHandshake = this.hold(record, 'initialization');
    const budget = this.timeouts.get(profile);
    record.deadline = Date.now() + budget;
    record.handshakeTimer = setTimeout(() => { void this.closeRecord(record, 'initialize_timeout'); }, budget);
    res.once('close', () => {
      if (!record.initializeSucceeded && !record.closing) void this.closeRecord(record, 'initialize_interrupted');
    });
    try {
      const folder = path.join(this.runDir, 'sessions', record.key);
      mkdirSync(folder, { recursive: true });
      const manifest = path.join(folder, 'manifest.json');
      const identityPath = path.join(folder, 'identity.json');
      const spec = { ...this.profiles[profile].manifest, cwd: context.cwd, stderr_log_dir: path.join(folder, 'stderr') };
      spec.cache_dir ??= path.join(folder, 'cache');
      writeFileSync(manifest, JSON.stringify(spec));
      const child = spawn(this.config.bridge_python, ['-I', '-B', '-u', this.config.owned_bridge,
        '--manifest', manifest, '--owner-pid', String(context.pid), '--owner-created', context.created,
        '--identity-file', identityPath, '--bridge', this.config.bridge],
      { cwd: context.cwd, env: context.env, windowsHide: true, shell: false, stdio: ['pipe', 'pipe', 'pipe'] });
      record.child = child;
      record.exited = new Promise(resolve => {
        child.once('exit', resolve); child.once('error', () => { record.spawnFailed = true; resolve(); });
      });
      void record.exited.then(() => {
        if (!record.peerAttached && !record.closing) void this.closeRecord(record, 'session_start_failed');
      });
      // Silence diagnostics at the boundary: stdio payloads and environment never enter gateway logs.
      child.stderr.on('data', () => {});
      child.stdin.on('error', () => {});
      while (!existsSync(identityPath)) {
        if (record.closing || record.spawnFailed || child.exitCode !== null || child.signalCode !== null
          || Date.now() >= Math.min(record.deadline, record.deadline - budget + 10000)) throw new Error('owner_unavailable');
        await delay(10);
      }
      const identity = JSON.parse(readFileSync(identityPath, 'utf8'));
      if (!validIdentity(identity, context, child.pid) || record.closing || record.spawnFailed
        || child.exitCode !== null || child.signalCode !== null) throw new Error('owner_unavailable');
      this.event('bridge_started', record, { bridge_pid: identity.bridge_pid, bridge_created: identity.bridge_created });
      const registry = {
        register: (id, transport) => {
          record.id = id; record.transport = transport;
          if (!record.closing) this.sessions.set(id, record);
        },
        // Upstream sends failures to pending calls before it closes the transport.
        // Closing it here would race and discard those error responses.
        forget: () => { if (!record.closing) void this.closeRecord(record, record.reason ?? 'transport_closed', false); },
      };
      const session = await StatefulSession.open(registry, res, quiet, null, (_transport, responses) => {
        record.responses = responses;
        const peerStart = processPeer(child, () => this.stopChild(record));
        return owner => {
          record.peerAttached = true;
          return peerStart({ ...owner,
          message: (message, line) => {
            this.hold(record, 'backend activity')();
            if (Object.hasOwn(message, 'id')) {
              if (Object.hasOwn(message, 'method')) this.beginRpc(record, record.callbacks, message.id);
              else this.finishRpc(record.pending, message.id);
            }
            if (message.method === 'notifications/cancelled') this.finishRpc(record.callbacks, message.params?.requestId);
            if (message.id === record.initId && !Object.hasOwn(message, 'method') && !record.initializeReplied) {
              record.initializeReplied = true;
              record.initializeSucceeded = Object.hasOwn(message, 'result') && InitializeResultSchema.safeParse(message.result).success;
              if (!record.initializeSucceeded) {
                record.reason = 'initialize_failed';
                if (!Object.hasOwn(message, 'error')) {
                  message = { jsonrpc: '2.0', id: record.initId, error: { code: -32603, message: 'Invalid initialize result' } };
                  line = JSON.stringify(message);
                }
                res.once('finish', () => { void this.closeRecord(record, 'initialize_failed'); });
              }
            }
            owner.message(message, line);
          },
          exit: (code, signal) => {
            record.reason ??= record.initialized ? 'bridge_eof' : 'initialize_failed';
            owner.exit(code, signal);
          },
          failure: (kind) => owner.failure(kind, new Error('bridge_failure')),
          stderr() {}, nonJson() { owner.failure('process', new Error('invalid_bridge_message')); },
          });
        };
      });
      record.transport = session.transport;
      if (record.closing) { await record.transport.close(); throw new Error('initialize_interrupted'); }
      const upstreamMessage = record.transport.onmessage;
      record.transport.onmessage = (message, extra) => {
        if (Object.hasOwn(message, 'id')) {
          if (Object.hasOwn(message, 'method')) this.beginRpc(record, record.pending, message.id);
          else this.finishRpc(record.callbacks, message.id);
        }
        if (message.method === 'notifications/cancelled') this.finishRpc(record.pending, message.params?.requestId);
        if (record.initializeSucceeded && InitializedNotificationSchema.safeParse(message).success) {
          record.initialized = true; clearTimeout(record.handshakeTimer);
          record.releaseHandshake();
        }
        upstreamMessage(message, extra);
      };
      return record;
    } catch {
      await this.closeRecord(record, record.reason ?? 'session_start_failed');
      throw new Error('session_start_failed');
    }
  }

  async handle(req, res) {
    if (!this.authorize(req, res)) return;
    const urlPath = req.url.split('?')[0];
    if (req.method === 'GET' && (urlPath === '/_ready' || urlPath === '/_health')) {
      const records = [...this.active];
      json(res, { status: this.closing ? 'closing' : 'ready', gatewaySha256: this.sourceHash,
        ...(urlPath === '/_health' ? { sessionCount: records.length,
          pendingRequestCount: records.reduce((sum, record) => sum + record.pending.size, 0),
          pendingCallbackCount: records.reduce((sum, record) => sum + record.callbacks.size, 0),
          idleExpiredSessionCount: this.idleExpiredSessionCount,
          idleSessionTimeoutSeconds: Object.fromEntries([...this.idleTimeouts].map(([name, ms]) => [name, ms / 1000])),
          initializingSessionCount: records.filter(record => !record.initialized).length,
          limits: { maxSessions: this.maxSessions, maxSessionsPerProfile: this.maxSessionsPerProfile,
            maxRequestBytes: this.maxRequestBytes },
          profiles: Object.fromEntries(Object.keys(this.profiles).map(profile => {
            const own = records.filter(record => record.profile === profile);
            const samples = own.flatMap(record => {
              try {
                const value = JSON.parse(readFileSync(path.join(this.runDir, 'sessions', record.key, 'usage.json'), 'utf8'));
                return [{ bridgePid: record.child?.pid, sampledAt: value.sampled_at,
                  workingSetBytes: value.working_set_bytes, cpuSeconds: value.cpu_seconds,
                  sampledProcesses: value.process_count }];
              } catch { return []; }
            });
            return [profile, { sessions: own.length, pendingRequests: own.reduce((n, r) => n + r.pending.size, 0),
              pendingCallbacks: own.reduce((n, r) => n + r.callbacks.size, 0),
              usageScope: 'bridge_and_direct_backend_only', samples }];
          })) } : {}) });
      return;
    }
    if (this.closing) { reject(res, 503, 'Gateway is shutting down'); return; }
    const match = /^\/mcp\/([A-Za-z0-9_-]+)$/.exec(urlPath);
    const profile = match?.[1];
    if (!profile || !Object.hasOwn(this.profiles, profile)) { reject(res, 404, 'Unknown profile'); return; }
    if (!['GET', 'POST', 'DELETE'].includes(req.method)) { reject(res, 405, 'Method not allowed'); return; }
    let context;
    try { context = parseContext(req.headers, this.allowedRoot); }
    catch { reject(res, 400, 'Invalid owner context'); return; }
    const id = req.headers['mcp-session-id'];
    let record = id ? this.sessions.get(id) : null;
    if (id && (!record || record.closing || !sameContext(record, profile, context))) {
      reject(res, 404, 'Unknown session'); return;
    }
    // Acquire before awaiting a POST body: an idle timer must not overtake a
    // valid request still arriving. Its lease ends once on finish OR close;
    // independent RPC leases survive a dropped response until reply/cancel.
    if (record && req.method === 'POST') this.holdPost(record, res);
    let body;
    if (req.method === 'POST' || req.method === 'GET') {
      const accept = normalizeAccept(req.headers.accept, req.method === 'POST');
      if (accept === null) { reject(res, 406, 'Accept must include required media types'); return; }
      req.headers.accept = accept;
      // @hono/node-server constructs Headers from rawHeaders, not req.headers.
      const raw = [];
      for (let index = 0; index < req.rawHeaders.length; index += 2)
        if (req.rawHeaders[index].toLowerCase() !== 'accept') raw.push(req.rawHeaders[index], req.rawHeaders[index + 1]);
      req.rawHeaders = [...raw, 'Accept', accept];
    }
    if (req.method === 'POST') {
      if ((req.headers['content-type'] ?? '').split(';')[0].trim().toLowerCase() !== 'application/json') {
        reject(res, 415, 'Content-Type must be application/json'); return;
      }
      try { body = await bodyOf(req, this.timeouts.get(profile), this.maxRequestBytes); }
      catch (error) { reject(res, error.message === 'body_timeout' ? 408 : error.message === 'body_too_large' ? 413 : 400, 'Invalid request body'); return; }
    }
    if (!id) {
      if (req.method !== 'POST' || !validInitialize(body)) { reject(res, 400, 'Initialize required'); return; }
      if (this.active.size >= this.maxSessions || [...this.active].filter(r => r.profile === profile).length >= this.maxSessionsPerProfile) {
        reject(res, 429, 'Session capacity reached'); return;
      }
      try { record = await this.startRecord(profile, context, req, res, body); }
      catch { reject(res, 503, 'Session startup failed'); return; }
      this.holdPost(record, res);
    }
    if (record.closing) { reject(res, 404, 'Unknown session'); return; }
    if (res.destroyed) {
      if (!id) await this.closeRecord(record, 'initialize_interrupted');
      return;
    }
    record.responses.add(res);
    const responseEnded = () => {
      record.responses.delete(res);
      if (!id && (!record.initializeSucceeded || res.statusCode !== 200) && !record.closing)
        void this.closeRecord(record, 'initialize_failed');
    };
    res.once('finish', responseEnded); res.once('close', responseEnded);
    if (req.method === 'DELETE') record.reason = 'delete';
    try { await record.transport.handleRequest(req, res, body); }
    catch {
      if (!id) await this.closeRecord(record, 'initialize_interrupted');
      reject(res, 400, 'Transport rejected request');
    }
  }

  async listen() {
    this.server = createServer((req, res) => {
      void this.handle(req, res).catch(() => reject(res, 500, 'Gateway request failed'));
    });
    this.server.requestTimeout = 0; // Calls are never timed out by session idleness.
    await new Promise((resolve, rejectListen) => {
      this.server.once('error', rejectListen);
      this.server.listen(this.config.port, '127.0.0.1', resolve);
    });
    this.event('gateway_started', null, { pid: process.pid });
    if (this.config.stop_file) this.stopTimer = setInterval(() => {
      if (existsSync(this.config.stop_file)) void this.close();
    }, 100);
  }

  async close() {
    if (this.closePromise) return this.closePromise;
    this.closing = true; clearInterval(this.stopTimer);
    this.closePromise = (async () => {
      const serverClosed = new Promise(resolve => this.server ? this.server.close(resolve) : resolve());
      await Promise.all([...this.active].map(record => this.closeRecord(record, 'gateway_shutdown')));
      this.server?.closeAllConnections();
      await serverClosed;
      this.event('gateway_closed');
    })();
    return this.closePromise;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === sourcePath) {
  try {
    const stdinControl = process.argv.length === 5 && process.argv[4] === '--stdin-control';
    if ((!stdinControl && process.argv.length !== 4) || process.argv[2] !== '--config') throw new Error('invalid_arguments');
    const config = JSON.parse(readFileSync(process.argv[3], 'utf8').replace(/^\uFEFF/, ''));
    const gateway = new Gateway(config);
    process.once('SIGINT', () => { void gateway.close(); });
    process.once('SIGTERM', () => { void gateway.close(); });
    await gateway.listen();
    if (stdinControl) {
      process.stdin.once('end', () => { void gateway.close(); });
      process.stdin.resume();
    }
  } catch {
    process.stderr.write('supergateway-adapter: startup failed\n');
    process.exitCode = 2;
  }
}
