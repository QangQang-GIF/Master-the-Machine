import { createHash, randomUUID } from 'node:crypto';
import { closeSync, existsSync, fsyncSync, linkSync, lstatSync, mkdirSync, openSync,
  readFileSync, readdirSync, realpathSync, statSync, unlinkSync, writeFileSync } from 'node:fs';
import { isAbsolute, join } from 'node:path';

const fail = code => { throw Object.assign(new Error(code), { code }); };
const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value) &&
  Object.keys(value).sort().join(',') === [...keys].sort().join(',');
const id = value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(value);
const digest = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const integer = value => Number.isSafeInteger(value) && value >= 0;
const kinds = ['SPAWN', 'FOLLOWUP', 'STOP_TREE', 'PREPARATION_COMPLETE'];
const states = ['PENDING', 'OUTCOME_UNKNOWN', 'ACKNOWLEDGED', 'COMPLETED', 'CANCELLED'];
const resultValid = value => exact(value, ['resultSha256', 'target']) && digest(value.resultSha256) &&
  (value.target === null || (typeof value.target === 'string' &&
    /^\/[A-Za-z0-9_-]+(?:\/[A-Za-z0-9_-]+)*$/.test(value.target) && value.target.length <= 512));
const sameResult = (left, right) => left.resultSha256 === right.resultSha256 && left.target === right.target;

function identityOf(value) {
  if (!exact(value, ['serverOrigin', 'teamId', 'matchId', 'attemptId']) ||
      !id(value.teamId) || !id(value.matchId) || !id(value.attemptId)) fail('INVALID_SESSION_IDENTITY');
  let url;
  try { url = new URL(value.serverOrigin); } catch { fail('INVALID_SESSION_IDENTITY'); }
  if (url.origin !== value.serverOrigin || url.username || url.password ||
      !(url.protocol === 'https:' || (url.protocol === 'http:' &&
        ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)))) fail('INVALID_SESSION_IDENTITY');
  return { serverOrigin: value.serverOrigin, teamId: value.teamId,
    matchId: value.matchId, attemptId: value.attemptId };
}

/** Deterministic directory name; identity is also checked inside every checkpoint. */
export function sessionJournalDirectory(dataDirectory, identity) {
  if (typeof dataDirectory !== 'string' || !isAbsolute(dataDirectory)) fail('INVALID_JOURNAL_DIRECTORY');
  return join(dataDirectory, 'sessions', hash(identityOf(identity)));
}

function validate(snapshot, identity) {
  if (!exact(snapshot, ['schemaVersion', 'generation', 'identity', 'hostGeneration', 'operations']) ||
      snapshot.schemaVersion !== 1 || !integer(snapshot.generation) || snapshot.generation < 1 ||
      !integer(snapshot.hostGeneration) || !Array.isArray(snapshot.operations) ||
      snapshot.operations.length > 1000) fail('CORRUPT_SESSION_JOURNAL');
  if (JSON.stringify(identityOf(snapshot.identity)) !== JSON.stringify(identity)) fail('SESSION_IDENTITY_MISMATCH');
  const seen = new Set();
  for (const op of snapshot.operations) {
    if (!exact(op, ['operationId', 'kind', 'payloadSha256', 'state', 'hostGeneration', 'result']) ||
        !id(op.operationId) || seen.has(op.operationId) || !kinds.includes(op.kind) ||
        !digest(op.payloadSha256) || !states.includes(op.state) ||
        !integer(op.hostGeneration) || op.hostGeneration > snapshot.hostGeneration ||
        (op.result !== null && !resultValid(op.result)) ||
        (['ACKNOWLEDGED', 'COMPLETED'].includes(op.state) && op.result === null) ||
        (op.state === 'PENDING' && (op.result !== null || op.hostGeneration !== 0)) ||
        (op.state === 'CANCELLED' && (op.result !== null || op.hostGeneration !== 0)) ||
        (!['PENDING', 'CANCELLED'].includes(op.state) && op.hostGeneration === 0)) fail('CORRUPT_SESSION_JOURNAL');
    seen.add(op.operationId);
  }
  return snapshot;
}

/**
 * Durable metadata only, never descriptors/credentials. Dispatch is recorded as
 * outcome-unknown BEFORE the external action. A crash cannot make it safe to retry.
 * Service ownership and credential storage are deliberately separate contracts.
 */
export function openSessionJournal({ directory, identity: suppliedIdentity }) {
  const identity = identityOf(suppliedIdentity);
  if (typeof directory !== 'string' || !isAbsolute(directory)) fail('INVALID_JOURNAL_DIRECTORY');
  mkdirSync(directory, { recursive: true, mode: 0o700 });
  if (lstatSync(directory).isSymbolicLink() || !statSync(directory).isDirectory()) fail('INVALID_JOURNAL_DIRECTORY');
  const root = realpathSync(directory);
  const assertRoot = () => {
    if (lstatSync(directory).isSymbolicLink() || realpathSync(directory) !== root) fail('INVALID_JOURNAL_DIRECTORY');
  };
  const load = () => {
    assertRoot();
    const files = readdirSync(root).filter(name => /^\d{9}\.json$/.test(name)).sort();
    if (!files.length) return null;
    const name = files.at(-1);
    const path = join(root, name);
    if (lstatSync(path).isSymbolicLink() || statSync(path).size > 2 * 1024 * 1024) fail('CORRUPT_SESSION_JOURNAL');
    let record;
    try { record = JSON.parse(readFileSync(path, 'utf8')); } catch { fail('CORRUPT_SESSION_JOURNAL'); }
    if (!exact(record, ['snapshot', 'sha256']) || !digest(record.sha256) ||
        hash(record.snapshot) !== record.sha256 ||
        record.snapshot?.generation !== Number(name.slice(0, 9))) fail('CORRUPT_SESSION_JOURNAL');
    return validate(record.snapshot, identity);
  };
  let current = load() ?? { schemaVersion: 1, generation: 0, identity,
    hostGeneration: 0, operations: [] };
  const snapshot = () => structuredClone(current);
  const publish = next => {
    assertRoot();
    const latest = load();
    if ((latest?.generation ?? 0) !== current.generation) fail('STALE_JOURNAL_WRITER');
    next.generation = current.generation + 1;
    if (next.generation > 999999999) fail('JOURNAL_CAPACITY_EXCEEDED');
    validate(next, identity);
    const temporary = join(root, `.${randomUUID()}.tmp`);
    const destination = join(root, `${String(next.generation).padStart(9, '0')}.json`);
    let descriptor;
    try {
      descriptor = openSync(temporary, 'wx', 0o600);
      writeFileSync(descriptor, JSON.stringify({ snapshot: next, sha256: hash(next) }) + '\n');
      fsyncSync(descriptor);
      closeSync(descriptor); descriptor = undefined;
      // Linking publishes a complete immutable file and refuses an existing name.
      // Unlike rename-over-destination it cannot overwrite a competing writer.
      linkSync(temporary, destination);
    } catch (error) {
      if (error.code === 'EEXIST') fail('STALE_JOURNAL_WRITER');
      fail('SESSION_JOURNAL_WRITE_FAILED');
    } finally {
      if (descriptor !== undefined) closeSync(descriptor);
      try { if (existsSync(temporary)) unlinkSync(temporary); } catch { /* ignored orphan temp */ }
    }
    current = next;
    return snapshot();
  };
  if (current.generation === 0) publish(snapshot());
  const operation = (next, operationId) => {
    const op = next.operations.find(item => item.operationId === operationId);
    if (!op) fail('UNKNOWN_JOURNAL_OPERATION');
    return op;
  };
  const host = generation => {
    if (!integer(generation) || generation === 0 || generation !== current.hostGeneration) fail('STALE_HOST_GENERATION');
  };
  const mutate = change => {
    if (load()?.generation !== current.generation) fail('STALE_JOURNAL_WRITER');
    const next = snapshot();
    return change(next) === false ? snapshot() : publish(next);
  };
  return Object.freeze({
    snapshot,
    attachHost: () => mutate(next => { next.hostGeneration += 1; }),
    enqueue(value) {
      if (!exact(value, ['operationId', 'kind', 'payloadSha256']) || !id(value.operationId) ||
          !kinds.includes(value.kind) || !digest(value.payloadSha256)) fail('INVALID_JOURNAL_OPERATION');
      return mutate(next => {
        const existing = next.operations.find(op => op.operationId === value.operationId);
        if (existing) {
          if (existing.kind !== value.kind || existing.payloadSha256 !== value.payloadSha256) fail('JOURNAL_OPERATION_CONFLICT');
          return false;
        }
        if (next.operations.length >= 1000) fail('JOURNAL_CAPACITY_EXCEEDED');
        next.operations.push({ ...value, state: 'PENDING', hostGeneration: 0, result: null });
      });
    },
    dispatch(operationId, generation) {
      host(generation);
      return mutate(next => {
        const op = operation(next, operationId);
        if (op.state !== 'PENDING') fail('OPERATION_RECONCILIATION_REQUIRED');
        op.state = 'OUTCOME_UNKNOWN'; op.hostGeneration = generation;
      });
    },
    cancel(operationId) {
      return mutate(next => {
        const op = operation(next, operationId);
        if (op.state === 'CANCELLED') return false;
        if (op.state !== 'PENDING') fail('OPERATION_RECONCILIATION_REQUIRED');
        op.state = 'CANCELLED';
      });
    },
    acknowledge(operationId, generation, result) {
      host(generation);
      if (!resultValid(result)) fail('INVALID_JOURNAL_RESULT');
      return mutate(next => {
        const op = operation(next, operationId);
        if (op.hostGeneration !== generation) fail('STALE_HOST_GENERATION');
        if (op.result) {
          if (!sameResult(op.result, result)) fail('JOURNAL_RESULT_CONFLICT');
          return false;
        }
        if (op.state !== 'OUTCOME_UNKNOWN') fail('INVALID_OPERATION_TRANSITION');
        op.result = structuredClone(result); op.state = 'ACKNOWLEDGED';
      });
    },
    complete(operationId, generation) {
      host(generation);
      return mutate(next => {
        const op = operation(next, operationId);
        if (op.hostGeneration !== generation) fail('STALE_HOST_GENERATION');
        if (op.state === 'COMPLETED') return false;
        if (op.state !== 'ACKNOWLEDGED') fail('INVALID_OPERATION_TRANSITION');
        op.state = 'COMPLETED';
      });
    },
    reconcile(operationId, generation, outcome, result = null) {
      host(generation);
      if (!['NOT_EXECUTED', 'COMPLETED'].includes(outcome) ||
          (outcome === 'COMPLETED' ? !resultValid(result) : result !== null)) fail('INVALID_RECONCILIATION');
      return mutate(next => {
        const op = operation(next, operationId);
        if (op.state === 'COMPLETED' && outcome === 'COMPLETED') {
          if (!sameResult(op.result, result)) fail('JOURNAL_RESULT_CONFLICT');
          return false;
        }
        if (op.state === 'PENDING' && outcome === 'NOT_EXECUTED') return false;
        if (!['OUTCOME_UNKNOWN', 'ACKNOWLEDGED'].includes(op.state)) fail('INVALID_OPERATION_TRANSITION');
        if (outcome === 'NOT_EXECUTED') {
          if (op.result) fail('JOURNAL_RESULT_CONFLICT');
          op.state = 'PENDING'; op.hostGeneration = 0;
        } else {
          if (op.result && !sameResult(op.result, result)) fail('JOURNAL_RESULT_CONFLICT');
          op.state = 'COMPLETED'; op.result = structuredClone(result); op.hostGeneration = generation;
        }
      });
    }
  });
}
