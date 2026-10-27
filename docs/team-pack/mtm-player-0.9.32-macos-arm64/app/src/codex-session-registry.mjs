import { createHash, randomUUID } from 'node:crypto';
import { closeSync, fsyncSync, linkSync, lstatSync, mkdirSync, openSync, readFileSync,
  readdirSync, realpathSync, statSync, unlinkSync, writeFileSync } from 'node:fs';
import { isAbsolute, join, resolve } from 'node:path';

const fail = code => { throw Object.assign(new Error(code), { code }); };
const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value) &&
  Object.keys(value).sort().join(',') === [...keys].sort().join(',');
const id = value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(value);
const pathId = value => typeof value === 'string' && value.length <= 512 &&
  /^\/[A-Za-z0-9_-]+(?:\/[A-Za-z0-9_-]+)+$/.test(value);
const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const canonicalPath = value => process.platform === 'win32' ? resolve(value).toLowerCase() : resolve(value);

/** Reads the actual task ID, never a collaboration path or a guessed filename. */
export function currentCodexThreadId(env = process.env) {
  const value = env.CODEX_THREAD_ID;
  if (value === undefined || value === '') return null;
  if (!id(value)) fail('INVALID_CODEX_THREAD_ID');
  return value;
}

function sessionIdentity(session) {
  if (!session || !id(session.playerSessionId) || !id(session.teamId) || !id(session.matchId) ||
      typeof session.teamWorkspace !== 'string' || !isAbsolute(session.teamWorkspace)) fail('INVALID_HOST_SESSION');
  let server;
  try { server = new URL(session.gameMasterApiUrl); } catch { fail('INVALID_HOST_SESSION'); }
  if (server.username || server.password || server.hash || server.search ||
      !(server.protocol === 'https:' || (server.protocol === 'http:' &&
        ['localhost', '127.0.0.1', '[::1]'].includes(server.hostname)))) fail('INVALID_HOST_SESSION');
  return { playerSessionId: session.playerSessionId, serverOrigin: server.origin,
    teamId: session.teamId.toUpperCase(), matchId: session.matchId,
    teamWorkspace: canonicalPath(session.teamWorkspace) };
}

/** Append-only, nonsecret host/task mapping. It grants no authority over a task. */
export function openCodexSessionRegistry({ dataDirectory, session, now = () => new Date().toISOString() }) {
  const identity = sessionIdentity(session);
  if (typeof dataDirectory !== 'string' || !isAbsolute(dataDirectory)) fail('INVALID_HOST_REGISTRY_DIRECTORY');
  // Key on Player identity, then check all binding fields inside the record. A
  // changed team/server/workspace cannot silently adopt this session's history.
  const directory = join(dataDirectory, 'codex-task-bindings', hash(identity.playerSessionId));
  mkdirSync(directory, { recursive: true, mode: 0o700 });
  if (lstatSync(directory).isSymbolicLink()) fail('INVALID_HOST_REGISTRY_DIRECTORY');
  const root = realpathSync(directory);

  const replay = () => {
    if (lstatSync(directory).isSymbolicLink() || realpathSync(directory) !== root) fail('INVALID_HOST_REGISTRY_DIRECTORY');
    const files = readdirSync(root).filter(name => /^\d{9}\.json$/.test(name)).sort();
    if (files.length > 10000) fail('HOST_REGISTRY_CAPACITY_EXCEEDED');
    const registrations = new Map();
    const attachments = [];
    let activeCodexThreadId = null;
    for (let index = 0; index < files.length; index++) {
      if (Number(files[index].slice(0, 9)) !== index + 1) fail('CORRUPT_HOST_REGISTRY');
      const path = join(root, files[index]);
      if (lstatSync(path).isSymbolicLink() || statSync(path).size > 65536) fail('CORRUPT_HOST_REGISTRY');
      let record;
      try { record = JSON.parse(readFileSync(path, 'utf8')); } catch { fail('CORRUPT_HOST_REGISTRY'); }
      if (!exact(record, ['event', 'sha256']) || hash(record.event) !== record.sha256 ||
          !exact(record.event, ['schemaVersion', 'sequence', 'identity', 'registration']) ||
          record.event.schemaVersion !== 1 || record.event.sequence !== index + 1) fail('CORRUPT_HOST_REGISTRY');
      if (JSON.stringify(record.event.identity) !== JSON.stringify(identity)) fail('HOST_SESSION_IDENTITY_MISMATCH');
      const value = record.event.registration;
      validateRegistration(value, registrations, activeCodexThreadId);
      const previous = registrations.get(value.codexThreadId);
      if (previous && !sameBinding(previous, value)) fail('HOST_THREAD_BINDING_CONFLICT');
      if (!previous) registrations.set(value.codexThreadId, value);
      if (value.role === 'PLAYER') {
        activeCodexThreadId = value.codexThreadId;
        attachments.push({ codexThreadId: value.codexThreadId, attachedAt: value.registeredAt });
      }
    }
    return { count: files.length, registrations, attachments, activeCodexThreadId };
  };
  const snapshot = state => ({ schemaVersion: 1, status: state.activeCodexThreadId ? 'REGISTERED' : 'UNAVAILABLE',
    ...(!state.activeCodexThreadId ? { code: 'CODEX_THREAD_ID_UNAVAILABLE' } : {}),
    identity: { ...identity }, activeCodexThreadId: state.activeCodexThreadId,
    registrations: [...state.registrations.values()].map(value => ({ ...value })),
    attachments: state.attachments.map(value => ({ ...value })) });
  const register = value => {
    const state = replay();
    validateRegistration(value, state.registrations, state.activeCodexThreadId);
    const previous = state.registrations.get(value.codexThreadId);
    if (previous && !sameBinding(previous, value)) fail('HOST_THREAD_BINDING_CONFLICT');
    if (previous && (value.role !== 'PLAYER' || state.activeCodexThreadId === value.codexThreadId)) return snapshot(state);
    if (state.count >= 10000) fail('HOST_REGISTRY_CAPACITY_EXCEEDED');
    const event = { schemaVersion: 1, sequence: state.count + 1, identity, registration: value };
    const temporary = join(root, `.${randomUUID()}.tmp`);
    let descriptor;
    try {
      descriptor = openSync(temporary, 'wx', 0o600);
      writeFileSync(descriptor, JSON.stringify({ event, sha256: hash(event) }) + '\n');
      fsyncSync(descriptor); closeSync(descriptor); descriptor = undefined;
      linkSync(temporary, join(root, `${String(event.sequence).padStart(9, '0')}.json`));
    } catch (error) {
      if (error.code === 'EEXIST') fail('HOST_REGISTRY_WRITE_CONFLICT');
      fail('HOST_REGISTRY_WRITE_FAILED');
    } finally {
      if (descriptor !== undefined) closeSync(descriptor);
      try { unlinkSync(temporary); } catch { /* unpublished temp is safe to ignore */ }
    }
    return snapshot(replay());
  };
  replay();
  return Object.freeze({
    snapshot: () => snapshot(replay()),
    registerTranscript({ codexThreadId, path } = {}) {
      if (!replay().registrations.has(codexThreadId)) fail('TRANSCRIPT_TASK_NOT_REGISTERED');
      if (typeof path !== 'string' || !isAbsolute(path)) fail('INVALID_TRANSCRIPT_PATH');
      const file = join(root, `transcript-${hash(codexThreadId)}.json`);
      const value = { codexThreadId, path: resolve(path) };
      const temporary = join(root, `.${randomUUID()}.tmp`);
      try {
        writeFileSync(temporary, JSON.stringify(value), { flag: 'wx', mode: 0o600, flush: true });
        linkSync(temporary, file);
      }
      catch (error) {
        if (error.code !== 'EEXIST') fail('TRANSCRIPT_REGISTRATION_FAILED');
        if (readFileSync(file, 'utf8') !== JSON.stringify(value)) fail('TRANSCRIPT_BINDING_CONFLICT');
      }
      finally { try { unlinkSync(temporary); } catch { /* unpublished temp */ } }
      return { codexThreadId, status: 'TRANSCRIPT_REGISTERED' };
    },
    transcripts() {
      const registered = replay().registrations;
      return readdirSync(root).filter(name => /^transcript-[a-f0-9]{64}\.json$/.test(name)).map(name => {
        const file = join(root, name);
        if (lstatSync(file).isSymbolicLink() || statSync(file).size > 16384) fail('INVALID_TRANSCRIPT_BINDING');
        const value = JSON.parse(readFileSync(file, 'utf8'));
        if (!exact(value, ['codexThreadId', 'path']) || !registered.has(value.codexThreadId) ||
            typeof value.path !== 'string' || !isAbsolute(value.path) ||
            name !== `transcript-${hash(value.codexThreadId)}.json`) fail('INVALID_TRANSCRIPT_BINDING');
        return value;
      });
    },
    attachPlayer(codexThreadId) {
      if (!id(codexThreadId)) fail('INVALID_CODEX_THREAD_ID');
      return register({ codexThreadId, role: 'PLAYER', parentThreadId: null, agentPath: null, registeredAt: now() });
    },
    registerAgent(value) {
      if (!exact(value, ['codexThreadId', 'role', 'parentThreadId', 'agentPath']) ||
          !['ORCHESTRATOR', 'SUBAGENT'].includes(value.role)) fail('INVALID_CODEX_AGENT_REGISTRATION');
      return register({ ...value, registeredAt: now() });
    }
  });
}

function sameBinding(left, right) {
  return ['codexThreadId', 'role', 'parentThreadId', 'agentPath'].every(key => left[key] === right[key]);
}

function validateRegistration(value, registrations, activeThread) {
  if (!exact(value, ['codexThreadId', 'role', 'parentThreadId', 'agentPath', 'registeredAt']) ||
      !id(value.codexThreadId) || typeof value.registeredAt !== 'string' ||
      !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value.registeredAt) ||
      !Number.isFinite(Date.parse(value.registeredAt))) fail('INVALID_CODEX_AGENT_REGISTRATION');
  if (value.role === 'PLAYER') {
    if (value.parentThreadId !== null || value.agentPath !== null) fail('INVALID_CODEX_AGENT_REGISTRATION');
    return;
  }
  if (!['ORCHESTRATOR', 'SUBAGENT'].includes(value.role) || !id(value.parentThreadId) ||
      value.codexThreadId === value.parentThreadId || !pathId(value.agentPath)) fail('INVALID_CODEX_AGENT_REGISTRATION');
  const parent = registrations.get(value.parentThreadId);
  if (!parent) fail('CODEX_AGENT_PARENT_NOT_REGISTERED');
  if (value.role === 'ORCHESTRATOR' ? parent.role !== 'PLAYER' :
    (parent.role === 'PLAYER' || !value.agentPath.startsWith(`${parent.agentPath}/`))) fail('INVALID_CODEX_AGENT_PARENT');
  let ancestor = parent;
  while (ancestor.role !== 'PLAYER') ancestor = registrations.get(ancestor.parentThreadId);
  if (ancestor.codexThreadId !== activeThread) fail('CODEX_AGENT_PARENT_NOT_ACTIVE');
  if ([...registrations.values()].some(entry => entry.codexThreadId !== value.codexThreadId &&
      entry.agentPath === value.agentPath && entry.parentThreadId === value.parentThreadId)) fail('CODEX_AGENT_PATH_CONFLICT');
}

/** Called after fresh authentication/restart binding, before starting observation. */
export function registerCurrentCodexTask(session, { dataDirectory, env = process.env, now } = {}) {
  const codexThreadId = currentCodexThreadId(env);
  const registry = openCodexSessionRegistry({ dataDirectory, session, now });
  const registration = codexThreadId ? registry.attachPlayer(codexThreadId) : registry.snapshot();
  return { session: Object.freeze({ ...session, codexThreadId }), registry,
    receipt: { schemaVersion: 1, status: 'PLAYER_HOST_REGISTRATION',
      registrationStatus: codexThreadId ? 'REGISTERED' : 'UNAVAILABLE',
      ...(!codexThreadId ? { code: 'CODEX_THREAD_ID_UNAVAILABLE' } : {}),
      playerSessionId: session.playerSessionId, codexThreadId } };
}
