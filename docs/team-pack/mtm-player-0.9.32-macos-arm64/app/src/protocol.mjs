import { createHash, createPublicKey, verify } from 'node:crypto';

export function requireThat(condition, code = 'INVALID_PROTOCOL') {
  if (!condition) throw new Error(code);
}

export function shape(value, keys) {
  requireThat(value && typeof value === 'object' && !Array.isArray(value));
  requireThat(Object.keys(value).length === keys.length && keys.every(k => Object.hasOwn(value, k)));
}

const id = value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(value);
const short = (value, max) => typeof value === 'string' && value.length > 0 && value.length <= max;
export const digest = value => createHash('sha256').update(value).digest('hex');
const base64 = value => {
  requireThat(short(value, 100_000));
  const bytes = Buffer.from(value, 'base64');
  requireThat(bytes.toString('base64') === value);
  return bytes;
};

export function authenticate(envelope, trust, teamId, workspaces) {
  shape(envelope, ['protocolVersion', 'keyId', 'payload', 'signature']);
  requireThat(envelope.protocolVersion === 1 && id(envelope.keyId));
  requireThat(Object.hasOwn(trust, envelope.keyId), 'UNTRUSTED_KEY');
  const bytes = base64(envelope.payload);
  const signature = base64(envelope.signature);
  const key = createPublicKey(trust[envelope.keyId]);
  requireThat(key.asymmetricKeyType === 'ed25519' && signature.length === 64 &&
    verify(null, bytes, key, signature), 'INVALID_SIGNATURE');
  const event = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
  shape(event, ['protocolVersion', 'type', 'eventId', 'runId', 'ticketId', 'teamId',
    'workspaceRef', 'issuedAt', 'deadline', 'ticket']);
  requireThat(event.protocolVersion === 1 && event.type === 'TICKET');
  for (const k of ['eventId', 'runId', 'ticketId', 'teamId', 'workspaceRef']) requireThat(id(event[k]));
  requireThat(event.teamId === teamId, 'WRONG_TEAM');
  requireThat(Object.hasOwn(workspaces, event.workspaceRef), 'UNKNOWN_WORKSPACE');
  for (const k of ['issuedAt', 'deadline']) {
    requireThat(typeof event[k] === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/.test(event[k]));
    requireThat(Number.isFinite(Date.parse(event[k])) && new Date(event[k]).toISOString() === event[k]);
  }
  requireThat(event.deadline >= event.issuedAt);
  shape(event.ticket, ['title', 'instructions']);
  requireThat(short(event.ticket.title, 256) && short(event.ticket.instructions, 16_384));
  return { event, hash: digest(bytes) };
}

export function manifest(value) {
  shape(value, ['protocolVersion', 'name', 'command', 'capabilities']);
  requireThat(value.protocolVersion === 1 && value.name === 'mtm-team-agent');
  requireThat(Array.isArray(value.command) && value.command.length > 0 && value.command.length <= 64);
  requireThat(value.command.every(arg => typeof arg === 'string' && arg.length <= 4096 && !arg.includes('\0')));
  requireThat(Array.isArray(value.capabilities) && value.capabilities.length <= 64 && value.capabilities.every(id));
  return value;
}

export function lifecycle(message, event, previous) {
  const keys = ['protocolVersion', 'type', 'eventId', 'runId', 'ticketId'];
  if (message?.type === 'PROGRESS') keys.push('percent');
  if (['BLOCKED', 'FAILED'].includes(message?.type)) keys.push('code');
  if (message?.type === 'SUBMITTED') keys.push('revision', 'evidence');
  shape(message, keys);
  requireThat(message.protocolVersion === 1 && ['eventId', 'runId', 'ticketId'].every(k => message[k] === event[k]));
  requireThat(previous === 'READY' ? message.type === 'STARTED' :
    ['STARTED', 'PROGRESS'].includes(previous) && ['PROGRESS', 'BLOCKED', 'FAILED', 'SUBMITTED'].includes(message.type), 'INVALID_TRANSITION');
  if (message.type === 'PROGRESS') requireThat(Number.isInteger(message.percent) && message.percent >= 0 && message.percent <= 100);
  if (['BLOCKED', 'FAILED'].includes(message.type)) requireThat(id(message.code));
  if (message.type === 'SUBMITTED') {
    requireThat(typeof message.revision === 'string' && /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(message.revision));
    requireThat(Array.isArray(message.evidence) && message.evidence.length <= 32);
    const seen = new Set();
    for (const item of message.evidence) {
      shape(item, ['path', 'sha256']);
      requireThat(short(item.path, 512) && item.path.split('/').every(part =>
        /^[A-Za-z0-9_-][A-Za-z0-9_.-]*$/.test(part) && !part.endsWith('.')));
      requireThat(!seen.has(item.path.toLowerCase()));
      seen.add(item.path.toLowerCase());
      requireThat(typeof item.sha256 === 'string' && /^[a-f0-9]{64}$/.test(item.sha256));
    }
  }
  return message.type;
}

export function receipt(event, submission) {
  const identity = { protocolVersion: 1, eventId: event.eventId, runId: event.runId,
    ticketId: event.ticketId, teamId: event.teamId, revision: submission.revision,
    evidence: submission.evidence.map(({ path, sha256 }) => ({ path, sha256 })) };
  return { ...identity, receiptId: digest(JSON.stringify(identity)), status: 'PENDING_GAME_MASTER',
    localRecordedAt: new Date().toISOString() };
}
