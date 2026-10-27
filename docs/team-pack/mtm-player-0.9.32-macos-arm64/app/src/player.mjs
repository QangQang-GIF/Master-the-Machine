import { existsSync, lstatSync, mkdirSync, readFileSync, realpathSync, renameSync,
  rmdirSync, writeFileSync, openSync, fsyncSync, closeSync } from 'node:fs';
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path';
import { authenticate, manifest, lifecycle, receipt, requireThat, shape } from './protocol.mjs';
import { invoke } from './agent.mjs';

const forbidden = /(^|[\\/])(organizer(?:-pack)?|mtm-master|hidden[-_]?tests?|answer[-_]?keys?|oracle[-_]?patches?|score[-_]?manifests?)([\\/]|$)/i;
function safePath(path) {
  requireThat(typeof path === 'string' && isAbsolute(path) && !forbidden.test(path), 'UNSAFE_PATH');
  // Reject linked components before opening any payload or state file.
  let current = resolve(path);
  while (true) {
    const entry = lstatSync(current, { throwIfNoEntry: false });
    requireThat(!entry?.isSymbolicLink(), 'LINKED_PATH');
    const parent = dirname(current);
    if (parent === current) break;
    current = parent;
  }
  return resolve(path);
}
const inside = (root, path) => {
  const value = relative(root, path);
  return value === '' || (!value.startsWith('..' + sep) && value !== '..' && !isAbsolute(value));
};
export function readJson(path) {
  safePath(path);
  requireThat(lstatSync(path).isFile() && lstatSync(path).size <= 1_048_576, 'INPUT_LIMIT');
  return JSON.parse(readFileSync(path, 'utf8'));
}

/** Health-check a team-provided registration before saving it without overwriting existing setup. */
export async function register({ registration, workspace, output, timeoutMs = 30_000 }) {
  manifest(registration);
  requireThat(Number.isInteger(timeoutMs) && timeoutMs > 0 && timeoutMs <= 3_600_000);
  workspace = realpathSync(safePath(workspace));
  output = safePath(output);
  requireThat(lstatSync(workspace).isDirectory(), 'WORKSPACE_REQUIRED');
  requireThat(!inside(workspace, output), 'REGISTRATION_WORKSPACE_OVERLAP');
  requireThat(!existsSync(output), 'REGISTRATION_EXISTS');
  let healthy = false;
  await invoke(registration, { protocolVersion: 1, type: 'HEALTH' }, workspace, message => {
    shape(message, ['protocolVersion', 'type', 'agent']);
    requireThat(!healthy && message.protocolVersion === 1 && message.type === 'READY' && message.agent === 'mtm-team-agent');
    healthy = true;
  }, timeoutMs);
  requireThat(healthy, 'AGENT_NOT_READY');
  writeFileSync(output, JSON.stringify(registration, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
  return { status: 'READY', agent: registration.name, registrationPath: output };
}

function save(path, state) {
  const temp = safePath(path + '.tmp');
  const serialized = JSON.stringify(state, null, 2) + '\n';
  requireThat(Buffer.byteLength(serialized) <= 1_048_576, 'STATE_LIMIT');
  writeFileSync(temp, serialized, { mode: 0o600 });
  // Windows requires a writable handle for FlushFileBuffers/fsync.
  const fd = openSync(temp, 'r+');
  try { fsyncSync(fd); } finally { closeSync(fd); }
  // Windows scanners can briefly retain a handle after fsync; bounded retries preserve atomic replace semantics.
  for (let attempt = 0; ; attempt++) {
    try { renameSync(temp, path); break; }
    catch (error) {
      if (!['EPERM', 'EACCES'].includes(error?.code) || attempt === 9) throw error;
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 25);
    }
  }
}

/** Consume one signed event. Local receipts remain pending until a future server adapter acknowledges them. */
export async function run({ envelope, registration, trust, teamId, workspaces, stateDir,
  retry = false, timeoutMs = 30_000 }) {
  requireThat(Number.isInteger(timeoutMs) && timeoutMs > 0 && timeoutMs <= 3_600_000);
  manifest(registration);
  const { event, hash } = authenticate(envelope, trust, teamId, workspaces);
  const workspace = realpathSync(safePath(workspaces[event.workspaceRef]));
  requireThat(lstatSync(workspace).isDirectory(), 'WORKSPACE_REQUIRED');
  stateDir = safePath(stateDir);
  requireThat(!inside(workspace, stateDir) && !inside(stateDir, workspace), 'STATE_WORKSPACE_OVERLAP');
  mkdirSync(stateDir, { recursive: true });
  const lock = safePath(resolve(stateDir, '.lock'));
  try { mkdirSync(lock); } catch { throw new Error('STATE_LOCKED'); }
  try {
    const file = safePath(resolve(stateDir, 'state.json'));
    const state = existsSync(file) ? readJson(file) : { protocolVersion: 1, teamId, events: [], outbox: [] };
    shape(state, ['protocolVersion', 'teamId', 'events', 'outbox']);
    requireThat(state.protocolVersion === 1 && Array.isArray(state.events) && Array.isArray(state.outbox), 'INVALID_STATE');
    requireThat(state.teamId === teamId, 'STATE_TEAM_MISMATCH');
    let record = state.events.find(item => item.eventId === event.eventId);
    if (record) {
      requireThat(record.hash === hash, 'EVENT_ID_CONFLICT');
      if (record.status === 'SUBMITTED') {
        const existing = state.outbox.find(item => item.eventId === event.eventId);
        requireThat(existing && existing.status === 'PENDING_GAME_MASTER', 'INVALID_STATE');
        return { replay: true, status: record.status, receipt: existing };
      }
      requireThat(retry, 'RETRY_REQUIRED');
    } else {
      record = { eventId: event.eventId, hash, envelope, status: 'PENDING', attempts: 0, timeline: [] };
      state.events.push(record);
      save(file, state);
    }
    record.attempts++;
    record.status = 'RUNNING';
    save(file, state);
    const append = message => {
      record.timeline.push({ attempt: record.attempts, localObservedAt: new Date().toISOString(), ...message });
      save(file, state);
    };
    try {
      let healthy = false;
      await invoke(registration, { protocolVersion: 1, type: 'HEALTH' }, workspace, message => {
        shape(message, ['protocolVersion', 'type', 'agent']);
        requireThat(!healthy && message.protocolVersion === 1 && message.type === 'READY' && message.agent === 'mtm-team-agent');
        healthy = true;
      }, timeoutMs);
      requireThat(healthy, 'AGENT_NOT_READY');
      append({ type: 'READY' });
      let previous = 'READY';
      let submission;
      await invoke(registration, { protocolVersion: 1, type: 'EVENT', event, envelope, workspace }, workspace, message => {
        previous = lifecycle(message, event, previous);
        // A submission is committed only after clean exit and complete protocol validation.
        if (message.type === 'SUBMITTED') submission = message;
        else append(message);
      }, timeoutMs);
      requireThat(['SUBMITTED', 'FAILED', 'BLOCKED'].includes(previous), 'MISSING_TERMINAL_STATUS');
      record.status = previous;
      if (submission) {
        state.outbox.push(receipt(event, submission));
        record.timeline.push({ attempt: record.attempts, localObservedAt: new Date().toISOString(), ...submission });
      }
      save(file, state);
      return { replay: false, status: record.status, receipt: state.outbox.find(item => item.eventId === event.eventId) ?? null };
    } catch {
      // A failed persistence attempt must not leak a staged submission into the failure save.
      state.outbox = state.outbox.filter(item => item.eventId !== event.eventId);
      record.timeline = record.timeline.filter(item => !(item.attempt === record.attempts && item.type === 'SUBMITTED'));
      record.status = 'FAILED';
      append({ type: 'FAILED', code: 'AGENT_OR_PROTOCOL_FAILURE' });
      throw new Error('AGENT_OR_PROTOCOL_FAILURE');
    }
  } finally { rmdirSync(lock); }
}
