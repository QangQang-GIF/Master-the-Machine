import { createHash, randomUUID } from 'node:crypto';

const fail = code => { throw Object.assign(new Error(code), { code }); };
const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value) &&
  Object.keys(value).sort().join(',') === [...keys].sort().join(',');
const unresolved = op => !['COMPLETED', 'CANCELLED'].includes(op.state);
const kindOf = frame => frame.stop ? 'STOP_TREE' : frame.spawn ? 'SPAWN' : frame.followup ? 'FOLLOWUP' : null;

/** Retains sensitive host frames in memory; persists only their identity and digest. */
export function createHostOutbox(journal) {
  const frames = new Map();
  let matchStatus = null;
  let generation = journal.attachHost().hostGeneration;
  const allowed = (op, frame) => op.kind === 'STOP_TREE' ||
    (op.kind === 'SPAWN' && frame?.status === 'PREPARATION_READY' &&
      ['PREPARING', 'IN_PROGRESS'].includes(matchStatus)) || matchStatus === 'IN_PROGRESS';
  const find = operationId => {
    const op = journal.snapshot().operations.find(value => value.operationId === operationId);
    if (!op) fail('UNKNOWN_JOURNAL_OPERATION');
    return op;
  };
  const frameFor = op => {
    const frame = frames.get(op.operationId);
    if (!frame) fail('HOST_OPERATION_PAYLOAD_UNAVAILABLE');
    if (hash(frame) !== op.payloadSha256) fail('HOST_OPERATION_INTEGRITY_MISMATCH');
    return structuredClone(frame);
  };
  const view = () => ({ schemaVersion: 1, status: 'HOST_OPERATIONS', hostGeneration: generation,
    matchStatus, operations: journal.snapshot().operations.filter(unresolved).map(op => ({
      ...op, payloadAvailable: frames.has(op.operationId) })) });
  const receipt = () => ({ schemaVersion: 1, status: 'HOST_OPERATION_RECORDED',
    hostGeneration: generation, pendingCount: journal.snapshot().operations.filter(unresolved).length });
  const ensureGeneration = value => {
    if (value !== generation) fail('STALE_HOST_GENERATION');
  };
  return Object.freeze({
    pending: () => journal.snapshot().operations.some(unresolved),
    view,
    observe(status) {
      matchStatus = status;
      // Only an action never handed to a host is safe to cancel automatically.
      for (const op of journal.snapshot().operations.filter(unresolved)) {
        if (op.state === 'PENDING' && frames.has(op.operationId) && !allowed(op, frames.get(op.operationId))) {
          journal.cancel(op.operationId);
        }
      }
    },
    retain(frame) {
      const kind = kindOf(frame);
      if (!kind) return frame;
      const payloadSha256 = hash(frame);
      let op = journal.snapshot().operations.find(value => unresolved(value) && value.payloadSha256 === payloadSha256);
      if (!op) {
        const operationId = `host-${randomUUID()}`;
        journal.enqueue({ operationId, kind, payloadSha256 });
        frames.set(operationId, structuredClone(frame));
        op = find(operationId);
      }
      return { schemaVersion: 1, status: 'HOST_OPERATION_PENDING', originalStatus: frame.status,
        operationId: op.operationId, kind, hostGeneration: generation };
    },
    handle(message) {
      if (exact(message, ['schemaVersion', 'type']) && message.schemaVersion === 1) {
        if (message.type === 'HOST_ATTACH') {
          generation = journal.attachHost().hostGeneration;
          return view();
        }
        if (message.type === 'HOST_OPERATIONS') return view();
      }
      const base = ['schemaVersion', 'type', 'operationId', 'hostGeneration'];
      if (!message || message.schemaVersion !== 1) fail('INVALID_HOST_OPERATION_MESSAGE');
      ensureGeneration(message.hostGeneration);
      const op = find(message.operationId);
      if (exact(message, base) && ['HOST_OPERATION_GET', 'HOST_OPERATION_INSPECT'].includes(message.type)) {
        if (!unresolved(op)) fail('HOST_OPERATION_ALREADY_RESOLVED');
        const frame = frameFor(op);
        if (message.type === 'HOST_OPERATION_INSPECT') {
          return { schemaVersion: 1, status: 'HOST_OPERATION_INSPECTION', execute: false,
            operationId: op.operationId, state: op.state, frame };
        }
        if (!allowed(op, frame)) fail('HOST_OPERATION_OBSOLETE');
        if (journal.snapshot().operations.find(unresolved)?.operationId !== op.operationId) fail('HOST_OPERATION_ORDER_REQUIRED');
        journal.dispatch(op.operationId, generation);
        return { schemaVersion: 1, status: 'HOST_OPERATION_DELIVERY',
          operationId: op.operationId, hostGeneration: generation, frame };
      }
      if (exact(message, [...base, 'result']) && message.type === 'HOST_OPERATION_ACK') {
        journal.acknowledge(op.operationId, generation, message.result);
        journal.complete(op.operationId, generation);
        return receipt();
      }
      if (exact(message, [...base, 'outcome', 'result']) && message.type === 'HOST_OPERATION_RECONCILE') {
        journal.reconcile(op.operationId, generation, message.outcome, message.result);
        if (message.outcome === 'NOT_EXECUTED' && frames.has(op.operationId) && !allowed(op, frames.get(op.operationId))) {
          journal.cancel(op.operationId);
        }
        return receipt();
      }
      fail('INVALID_HOST_OPERATION_MESSAGE');
    }
  });
}
