import { createInterface } from 'node:readline';
import { writeMachineFrame } from './machine-output.mjs';

const fail = code => { throw Object.assign(new Error(code), { code }); };
const object = value => value && typeof value === 'object' && !Array.isArray(value);

function inbox(stream) {
  const queued = [], waiting = [];
  const lines = createInterface({ input: stream, crlfDelay: Infinity });
  lines.on('line', line => {
    let value;
    try { value = JSON.parse(line); } catch { value = { type: 'INVALID_HOST_INPUT' }; }
    const waiter = waiting.shift();
    if (waiter) waiter(value); else queued.push(value);
  });
  return Object.freeze({ next: () => queued.length ? Promise.resolve(queued.shift()) :
    new Promise(resolve => waiting.push(resolve)), close: () => lines.close() });
}

async function post(fetchImpl, controlUrl, message, signal) {
  const response = await fetchImpl(controlUrl, { method: 'POST', redirect: 'manual', signal,
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ schemaVersion: 1, ...message }) });
  let value; try { value = await response.json(); } catch { fail('PLAYER_CONTROL_INVALID_RESPONSE'); }
  if (!response.ok || value?.status === 'ERROR') throw Object.assign(
    new Error(value?.code ?? 'PLAYER_CONTROL_REQUEST_FAILED'), { code: value?.code ?? 'PLAYER_CONTROL_REQUEST_FAILED' });
  return value;
}

function allowedInput(value) {
  if (!object(value) || value.schemaVersion !== 1) return false;
  return ['HOST_OPERATION_ACK', 'HOST_OPERATION_RECONCILE', 'ENVIRONMENT_READY',
    'REGISTER_CODEX_AGENT', 'ORCHESTRATOR_EXITED'].includes(value.type);
}

/**
 * Keeps the Codex task attached to the detached local app. Native collaboration is
 * deliberately executed by the host after it receives DISPATCH_REQUIRED; the bridge
 * transports the exact frame and records only the host's real result.
 */
export async function runLocalHostBridge({ controlUrl, codexThreadId = null,
  input = process.stdin, output = process.stdout, fetchImpl = fetch, waitMs = 55000 }) {
  if (typeof controlUrl !== 'string' || !/^http:\/\/127\.0\.0\.1:\d+\/[a-f0-9]{64}$/.test(controlUrl)) {
    fail('INVALID_PLAYER_CONTROL_URL');
  }
  const messages = inbox(input);
  let pendingInput = messages.next();
  let attached = await post(fetchImpl, controlUrl, { type: 'APP_ATTACH', codexThreadId });
  writeMachineFrame(output, { schemaVersion: 1, status: 'PLAYER_HOST_ATTACHED',
    hostGeneration: attached.hostGeneration ?? 0, phase: attached.phase ?? 'AWAITING_INIT' });
  try {
    for (;;) {
      if (['FINISHED', 'CANCELLED', 'STOPPED', 'OPERATOR_STOPPED'].includes(attached.phase) &&
          !(attached.operations || []).some(op => ['PENDING', 'DELIVERED'].includes(op.state))) return attached;
      const pending = (attached.operations || []).find(op => op.state === 'PENDING');
      if (pending) {
        const delivery = await post(fetchImpl, controlUrl, { type: 'HOST_OPERATION_PEEK',
          hostGeneration: attached.hostGeneration, operationId: pending.id });
        writeMachineFrame(output, { schemaVersion: 1, status: 'HOST_OPERATION_DISPATCH_REQUIRED',
          operationId: delivery.operationId, hostGeneration: attached.hostGeneration,
          controlUrl, frame: delivery.frame });
        return { schemaVersion: 1, status: 'HOST_DISPATCH_EMITTED',
          operationId: delivery.operationId, hostGeneration: attached.hostGeneration };
      }
      const abort = new AbortController();
      const waited = post(fetchImpl, controlUrl, { type: 'HOST_WAIT',
        hostGeneration: attached.hostGeneration ?? 0, timeoutMs: waitMs }, abort.signal)
        .then(value => ({ kind: 'WAIT', value }), error => ({ kind: 'WAIT_ERROR', error }));
      const incoming = pendingInput.then(value => ({ kind: 'INPUT', value }));
      const event = await Promise.race([waited, incoming]);
      if (event.kind === 'INPUT') {
        abort.abort();
        pendingInput = messages.next();
        if (!allowedInput(event.value)) fail('INVALID_HOST_INPUT');
        attached = await post(fetchImpl, controlUrl, event.value);
        writeMachineFrame(output, attached);
      } else {
        if (event.kind === 'WAIT_ERROR') throw event.error;
        attached = event.value;
        if (attached.status === 'STALE_HOST_GENERATION' ||
            (attached.phase !== 'AWAITING_INIT' && !attached.hostGeneration)) {
          attached = await post(fetchImpl, controlUrl, { type: 'APP_ATTACH', codexThreadId });
        } else if (attached.phase !== 'AWAITING_INIT' && attached.hostGeneration === 0) {
          attached = await post(fetchImpl, controlUrl, { type: 'APP_ATTACH', codexThreadId });
        }
        if (attached.status !== 'HOST_WAIT_TIMEOUT') writeMachineFrame(output, attached);
      }
    }
  } finally { messages.close(); }
}
