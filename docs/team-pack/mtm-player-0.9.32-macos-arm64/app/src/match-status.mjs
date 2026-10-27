import { appendFileSync, existsSync, lstatSync, mkdirSync, readFileSync, realpathSync,
  renameSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { isAbsolute, join, relative, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';

export class MatchStatusError extends Error {
  constructor(code) { super(code); this.code = code; this.name = 'MatchStatusError'; }
}

const fail = code => { throw new MatchStatusError(code); };
// matchStatus is authorized with the bundled Game Master key, not the Team API Key.
const GAME_MASTER_STATUS_KEY = 'gm_player_1d237308_5yAIqfd1Th8HUB759oOFN6xMH7ngvYD2';
const object = value => value && typeof value === 'object' && !Array.isArray(value);
const exact = (value, keys) => object(value) &&
  JSON.stringify(Object.keys(value).sort()) === JSON.stringify([...keys].sort());
const id = value => typeof value === 'string' &&
  /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(value);
const statusName = value => typeof value === 'string' && /^[A-Z][A-Z0-9_]{0,63}$/.test(value);
const secret = value => typeof value === 'string' && value.length > 0 && value.length <= 16_384 &&
  !/[\r\n\0]/.test(value);

export const MATCH_STATUS_POLL_INTERVAL_MS = 5_000;
export const MATCH_STATUSES = Object.freeze([
  'DRAFT', 'READY', 'PREPARING', 'IN_PROGRESS', 'PAUSED', 'FINISHED', 'CANCELLED'
]);
const terminalStatuses = new Set(['FINISHED', 'CANCELLED']);
const replaceErrors = new Set(['EPERM', 'EACCES', 'EBUSY']);
const pause = milliseconds => Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0,
  milliseconds);

function secureBaseUrl(value, testMode) {
  let url;
  try { url = new URL(value); } catch { fail('INVALID_GAME_MASTER_API_URL'); }
  const loopback = ['127.0.0.1', 'localhost', '::1'].includes(url.hostname);
  if ((url.protocol !== 'https:' && !(testMode && url.protocol === 'http:' && loopback)) ||
      url.username || url.password || url.search || url.hash) fail('INVALID_GAME_MASTER_API_URL');
  return url;
}

function validateSession(session, testMode) {
  if (!object(session) || !Object.isFrozen(session) || !id(session.matchId) ||
      !secret(session.gameMasterPlayerSecret) ||
      typeof session.teamSdlcPackWorkspace !== 'string' ||
      !isAbsolute(session.teamSdlcPackWorkspace)) fail('INVALID_MATCH_STATUS_SESSION');
  return secureBaseUrl(session.gameMasterApiUrl, testMode);
}

function validateDefinitions(value) {
  if (!Array.isArray(value) || value.length < 1 || value.length > 64) {
    fail('INVALID_MATCH_STATUS_RESPONSE');
  }
  const seen = new Set();
  const definitions = value.map(entry => {
    if (!exact(entry, ['status', 'description']) || !statusName(entry.status) ||
        typeof entry.description !== 'string' || entry.description.length < 1 ||
        entry.description.length > 2_000 || /[\0]/.test(entry.description) ||
        seen.has(entry.status)) fail('INVALID_MATCH_STATUS_RESPONSE');
    seen.add(entry.status);
    return Object.freeze({ status: entry.status, description: entry.description });
  });
  return Object.freeze(definitions);
}

function validateResponse(value, expectedMatchId, playerSecret) {
  const keys = object(value) ? Object.keys(value).sort() : [];
  const allowed = [
    ['status'], ['matchId', 'status'], ['status', 'statuses'],
    ['matchId', 'status', 'statuses']
  ].some(expected => JSON.stringify(keys) === JSON.stringify([...expected].sort()));
  if (!allowed || !statusName(value.status) ||
      (Object.hasOwn(value, 'matchId') && value.matchId !== expectedMatchId)) {
    fail('INVALID_MATCH_STATUS_RESPONSE');
  }
  const definitions = Object.hasOwn(value, 'statuses') ? validateDefinitions(value.statuses) :
    Object.freeze([]);
  if (!MATCH_STATUSES.includes(value.status) &&
      !definitions.some(entry => entry.status === value.status)) {
    fail('INVALID_MATCH_STATUS_RESPONSE');
  }
  if (definitions.some(entry => entry.description.includes(playerSecret))) {
    fail('INVALID_MATCH_STATUS_RESPONSE');
  }
  return Object.freeze({ status: value.status, definitions });
}

/** Reads the official state without exposing the Player bootstrap credential. */
export async function fetchMatchStatus(session, {
  fetchImpl = globalThis.fetch, timeoutMs = 10_000, testMode = false,
  now = () => new Date().toISOString()
} = {}) {
  const base = validateSession(session, testMode);
  if (typeof fetchImpl !== 'function' || !Number.isSafeInteger(timeoutMs) ||
      timeoutMs < 1 || timeoutMs > 60_000 || typeof now !== 'function') {
    fail('INVALID_MATCH_STATUS_CLIENT');
  }
  const endpoint = new URL('/api/v1/game-player/matchStatus', base);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let response;
  try {
    response = await fetchImpl(endpoint, { method: 'POST', redirect: 'manual', cache: 'no-store',
      signal: controller.signal, headers: { authorization: `Bearer ${GAME_MASTER_STATUS_KEY}`,
        'content-type': 'application/json', accept: 'application/json' },
      body: JSON.stringify({ matchId: session.matchId }) });
  } catch { fail('MATCH_STATUS_UNAVAILABLE'); }
  finally { clearTimeout(timer); }
  if (response.status >= 300 && response.status < 400) fail('MATCH_STATUS_REDIRECT_REJECTED');
  if (response.status === 401 || response.status === 403) fail('MATCH_STATUS_AUTH_REJECTED');
  if (!response.ok) fail('MATCH_STATUS_REQUEST_FAILED');
  if (!/^application\/json(?:\s*;|$)/i.test(response.headers.get('content-type') ?? '')) {
    fail('INVALID_MATCH_STATUS_RESPONSE');
  }
  const declaredLength = Number(response.headers.get('content-length'));
  if (Number.isFinite(declaredLength) && declaredLength > 64 * 1024) fail('MATCH_STATUS_TOO_LARGE');
  let body;
  try { body = await response.text(); } catch { fail('INVALID_MATCH_STATUS_RESPONSE'); }
  if (Buffer.byteLength(body, 'utf8') > 64 * 1024) fail('MATCH_STATUS_TOO_LARGE');
  let value;
  try { value = JSON.parse(body); } catch { fail('INVALID_MATCH_STATUS_RESPONSE'); }
  const { status, definitions } = validateResponse(value, session.matchId,
    session.gameMasterPlayerSecret);
  const headerRequestId = response.headers.get('x-request-id');
  return Object.freeze({ schemaVersion: 1, type: 'MATCH_STATUS', matchId: session.matchId,
    status, observedAt: now(), requestId: id(headerRequestId) ? headerRequestId : null,
    statusDefinitions: definitions });
}

function validObservation(observation, matchId) {
  if (!object(observation)) return false;
  const keys = Object.keys(observation).sort();
  const base = ['schemaVersion', 'type', 'matchId', 'status', 'observedAt', 'requestId'].sort();
  const extended = [...base, 'statusDefinitions'].sort();
  if (JSON.stringify(keys) !== JSON.stringify(base) &&
      JSON.stringify(keys) !== JSON.stringify(extended)) return false;
  if (observation.schemaVersion !== 1 || observation.type !== 'MATCH_STATUS' ||
      observation.matchId !== matchId || !statusName(observation.status) ||
      typeof observation.observedAt !== 'string' || observation.observedAt.length > 64 ||
      (observation.requestId !== null && !id(observation.requestId))) return false;
  if (!Object.hasOwn(observation, 'statusDefinitions')) {
    return MATCH_STATUSES.includes(observation.status);
  }
  let definitions;
  try { definitions = validateDefinitions(observation.statusDefinitions); }
  catch { return false; }
  return MATCH_STATUSES.includes(observation.status) ||
    definitions.some(entry => entry.status === observation.status);
}

function statusPaths(session) {
  const workspace = realpathSync(session.teamSdlcPackWorkspace);
  if (!statSync(workspace).isDirectory() || lstatSync(session.teamSdlcPackWorkspace).isSymbolicLink()) {
    fail('INVALID_MATCH_STATUS_WORKSPACE');
  }
  const logs = join(workspace, 'logs');
  if (!existsSync(logs)) mkdirSync(logs, { mode: 0o700 });
  if (lstatSync(logs).isSymbolicLink() || !statSync(logs).isDirectory()) {
    fail('INVALID_MATCH_STATUS_WORKSPACE');
  }
  const snapshot = resolve(logs, `${session.matchId}.match-status.json`);
  const history = resolve(logs, `${session.matchId}.match-status.ndjson`);
  for (const path of [snapshot, history]) {
    const rel = relative(workspace, path);
    if (rel.startsWith('..') || isAbsolute(rel) || (existsSync(path) && lstatSync(path).isSymbolicLink())) {
      fail('INVALID_MATCH_STATUS_WORKSPACE');
    }
  }
  return { snapshot, history };
}

export function persistMatchStatus(session, observation) {
  validateSession(session, true);
  if (!validObservation(observation, session.matchId)) {
    fail('INVALID_MATCH_STATUS_OBSERVATION');
  }
  const paths = statusPaths(session);
  // Definitions are retained only long enough for host-side lifecycle classification.
  // Keeping remote prose out of durable telemetry also prevents accidental secret echo.
  const safeObservation = Object.freeze({ schemaVersion: 1, type: 'MATCH_STATUS',
    matchId: observation.matchId, status: observation.status,
    observedAt: observation.observedAt, requestId: observation.requestId });
  const json = `${JSON.stringify(safeObservation)}\n`;
  appendFileSync(paths.history, json, { encoding: 'utf8', mode: 0o600 });
  const temporary = `${paths.snapshot}.${randomUUID()}.tmp`;
  writeFileSync(temporary, json, { encoding: 'utf8', mode: 0o600, flag: 'wx' });
  try {
    for (let attempt = 0; ; attempt += 1) {
      try { renameSync(temporary, paths.snapshot); break; }
      catch (error) {
        if (!replaceErrors.has(error?.code) || attempt === 19) throw error;
        pause(25);
      }
    }
  } catch { rmSync(temporary, { force: true }); fail('MATCH_STATUS_PERSIST_FAILED'); }
  return Object.freeze(paths);
}

export function readPersistedMatchStatus(teamSdlcPackWorkspace, matchId) {
  if (typeof teamSdlcPackWorkspace !== 'string' || !isAbsolute(teamSdlcPackWorkspace) || !id(matchId)) {
    return null;
  }
  const path = join(teamSdlcPackWorkspace, 'logs', `${matchId}.match-status.json`);
  try {
    if (!existsSync(path) || lstatSync(path).isSymbolicLink() || statSync(path).size > 64 * 1024) return null;
    const value = JSON.parse(readFileSync(path, 'utf8'));
    if (!exact(value, ['schemaVersion', 'type', 'matchId', 'status', 'observedAt', 'requestId']) ||
        value.schemaVersion !== 1 || value.type !== 'MATCH_STATUS' || value.matchId !== matchId ||
        !statusName(value.status)) return null;
    return Object.freeze(value);
  } catch { return null; }
}

/** Polls immediately, then every five seconds. Only state changes are emitted and persisted. */
export function createMatchStatusMonitor(session, options = {}) {
  const intervalMs = options.intervalMs ?? MATCH_STATUS_POLL_INTERVAL_MS;
  if (!Number.isSafeInteger(intervalMs) || intervalMs < 10 || intervalMs > 60_000) {
    fail('INVALID_MATCH_STATUS_INTERVAL');
  }
  let timer = null;
  let stopped = false;
  let running = false;
  let previous = null;
  const fetchStatus = options.fetchMatchStatus ?? (value => fetchMatchStatus(value, options));
  const persist = options.persistMatchStatus ?? persistMatchStatus;
  return Object.freeze({
    start({ onObservation, onError }) {
      if (timer || typeof onObservation !== 'function' || typeof onError !== 'function') {
        fail('INVALID_MATCH_STATUS_MONITOR');
      }
      stopped = false;
      const poll = async () => {
        if (stopped || running) return;
        running = true;
        try {
          const observation = await fetchStatus(session);
          const signature = JSON.stringify([observation.status,
            observation.statusDefinitions ?? []]);
          if (signature !== previous) {
            persist(session, observation);
            previous = signature;
            await onObservation(observation);
            if (terminalStatuses.has(observation.status)) stopped = true;
          }
        } catch (error) {
          await onError(error);
        } finally { running = false; }
      };
      void poll();
      timer = setInterval(() => { void poll(); }, intervalMs);
    },
    stop() {
      stopped = true;
      if (timer) clearInterval(timer);
      timer = null;
    }
  });
}
