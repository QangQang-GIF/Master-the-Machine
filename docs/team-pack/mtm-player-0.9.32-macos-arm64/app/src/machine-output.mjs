const FRAME_PREFIX = 'MTM_PLAYER_JSON_V2';
const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f-\u009f]/;

export class PlayerOutputError extends Error {
  constructor(code) { super(code); this.code = code; this.name = 'PlayerOutputError'; }
}

const fail = code => { throw new PlayerOutputError(code); };
const object = value => value && typeof value === 'object' && !Array.isArray(value);
const text = (value, max = 4096) => typeof value === 'string' && value.length > 0 &&
  value.length <= max && !CONTROL_CHARACTERS.test(value);

/** Returns the only init data that may cross an operator or terminal boundary. */
export function publicInitReceipt(session) {
  if (!object(session) || session.status !== 'READY' || !text(session.playerSessionId, 128) ||
      !text(session.teamId, 128) || !text(session.teamName, 1024) || !text(session.matchId, 128) ||
      !object(session.teamRuntime?.runtime) || !text(session.teamRuntime.runtime.version, 64) ||
      !text(session.teamRuntime.runtime.rootAgentType, 128)) fail('INVALID_PLAYER_SESSION');
  if (Object.hasOwn(session, 'codexThreadId') && session.codexThreadId !== null &&
      (typeof session.codexThreadId !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(session.codexThreadId))) fail('INVALID_PLAYER_SESSION');
  return Object.freeze({ schemaVersion: 1, status: 'READY',
    playerSessionId: session.playerSessionId, teamId: session.teamId,
    ...(Object.hasOwn(session, 'codexThreadId') ? { codexThreadId: session.codexThreadId } : {}),
    teamName: session.teamName, matchId: session.matchId,
    teamRuntime: Object.freeze({ version: session.teamRuntime.runtime.version,
      rootAgentType: session.teamRuntime.runtime.rootAgentType }) });
}

/** Returns a host action that opens the one-time init URL in Codex's browser panel. */
export function browserOpenRequest(url) {
  if (typeof url !== 'string' ||
      !/^http:\/\/127\.0\.0\.1:\d+\/(?:[a-f0-9]{48}|init|restart)$/.test(url)) {
    fail('INVALID_PLAYER_BROWSER_URL');
  }
  return Object.freeze({ schemaVersion: 1, status: 'WAITING_FOR_INPUT', url,
    openInCodex: Object.freeze({ placement: 'right',
      target: Object.freeze({ type: 'browser', url }) }) });
}

/**
 * Frames JSON as one UTF-8 record with a verified byte length. The host adapter
 * parses it directly from a non-TTY stdout pipe without reconstructing the payload.
 */
export function encodeMachineFrame(value) {
  const json = JSON.stringify(value);
  const bytes = Buffer.from(json, 'utf8');
  return `${FRAME_PREFIX}:${bytes.length}:${json}\n`;
}

export function decodeMachineFrame(frame) {
  if (typeof frame !== 'string' || CONTROL_CHARACTERS.test(frame.replace(/\n$/, ''))) {
    fail('INVALID_MACHINE_FRAME');
  }
  const match = new RegExp(`^${FRAME_PREFIX}:(\\d+):(\\{.*\\})\\n?$`).exec(frame);
  if (!match) fail('INVALID_MACHINE_FRAME');
  const expected = Number(match[1]);
  const bytes = Buffer.from(match[2], 'utf8');
  if (!Number.isSafeInteger(expected) || expected < 2 || bytes.length !== expected) {
    fail('INVALID_MACHINE_FRAME');
  }
  let value;
  try { value = JSON.parse(bytes.toString('utf8')); } catch { fail('INVALID_MACHINE_FRAME'); }
  return value;
}

export function writeMachineFrame(stream, value) {
  if (!stream || typeof stream.write !== 'function') fail('INVALID_OUTPUT_STREAM');
  stream.write(encodeMachineFrame(value));
}
