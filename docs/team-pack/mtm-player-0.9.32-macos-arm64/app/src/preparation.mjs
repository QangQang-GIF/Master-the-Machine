const UUID = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
const MAX_RESPONSE_BYTES = 64 * 1024;

export class PlayerPreparationError extends Error {
  constructor(code) { super(code); this.code = code; this.name = 'PlayerPreparationError'; }
}

const fail = code => { throw new PlayerPreparationError(code); };
const object = value => value && typeof value === 'object' && !Array.isArray(value);
const exact = (value, keys) => object(value) &&
  JSON.stringify(Object.keys(value).sort()) === JSON.stringify([...keys].sort());
const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f-\u009f]/;
const text = (value, max = 4096) => typeof value === 'string' && value.length > 0 &&
  value.length <= max && !CONTROL_CHARACTERS.test(value);
const id = value => text(value, 128) && /^[A-Za-z0-9][A-Za-z0-9_.:-]*$/.test(value);

function httpUrl(value, { testMode = false } = {}) {
  if (!text(value)) fail('INVALID_GAME_MASTER_URL');
  let url;
  try { url = new URL(value); } catch { fail('INVALID_GAME_MASTER_URL'); }
  const local = ['localhost', '127.0.0.1', '::1'].includes(url.hostname);
  if (url.protocol !== 'https:' && !(testMode && url.protocol === 'http:' && local)) {
    fail('INVALID_GAME_MASTER_URL');
  }
  if (url.username || url.password || url.search || url.hash) fail('INVALID_GAME_MASTER_URL');
  return url;
}

function gitUrl(value, { testMode = false } = {}) {
  if (!text(value) || /\s/.test(value)) fail('INVALID_PREPARATION_REPOSITORY');
  if (/^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:[A-Za-z0-9._~/-]+$/.test(value)) return value;
  let url;
  try { url = new URL(value); } catch { fail('INVALID_PREPARATION_REPOSITORY'); }
  const local = ['localhost', '127.0.0.1', '::1'].includes(url.hostname);
  if (!['https:', 'ssh:'].includes(url.protocol) &&
      !(testMode && url.protocol === 'http:' && local)) fail('INVALID_PREPARATION_REPOSITORY');
  if (url.password || (url.protocol !== 'ssh:' && url.username) || url.search || url.hash) {
    fail('INVALID_PREPARATION_REPOSITORY');
  }
  return url.href;
}

export function validatePreparationRepository(value, options = {}) {
  if (!exact(value, ['url', 'branch', 'folder'])) fail('INVALID_PREPARATION_REPOSITORY');
  const url = gitUrl(value.url, options);
  if (!/^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$/.test(value.branch) ||
      value.branch.includes('..') || value.branch.includes('@{') ||
      value.branch.includes('//') || value.branch.endsWith('/') ||
      value.branch.endsWith('.') || value.branch.split('/').some(part =>
        part.startsWith('.') || part.endsWith('.lock'))) fail('INVALID_PREPARATION_REPOSITORY');
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value.folder) ||
      value.folder === '.' || value.folder === '..') fail('INVALID_PREPARATION_REPOSITORY');
  return Object.freeze({ url, branch: value.branch, folder: value.folder });
}

export function repositoryFromSession(session, options = {}) {
  if (!object(session) || !id(session.matchId) || !object(session.repository)) {
    fail('PREPARATION_REPOSITORY_UNAVAILABLE');
  }
  const repository = validatePreparationRepository(session.repository, options);
  if (repository.folder !== session.matchId) fail('PREPARATION_REPOSITORY_MISMATCH');
  return repository;
}

export function validateEnvironmentReady(value, { requestId, repository,
  testMode = false } = {}) {
  const baseKeys = ['schemaVersion', 'status', 'requestId', 'repository', 'revision', 'build', 'matchLogin'];
  const legacySession = exact(value, [...baseKeys, 'matchSession']) &&
    exact(value.matchSession, ['status', 'verification']);
  if (!(exact(value, baseKeys) || legacySession) ||
      value.schemaVersion !== 1 || value.status !== 'ENVIRONMENT_READY' ||
      !id(requestId) || value.requestId !== requestId || value.build !== 'PASS' ||
      value.matchLogin !== 'PASS' ||
      !/^[a-f0-9]{40,64}$/.test(value.revision)) fail('INVALID_ENVIRONMENT_RESULT');
  const expected = validatePreparationRepository(repository, { testMode });
  const actual = validatePreparationRepository(value.repository, { testMode });
  if (JSON.stringify(actual) !== JSON.stringify(expected)) fail('PREPARATION_REPOSITORY_MISMATCH');
  const { matchSession: ignored, ...current } = value;
  return Object.freeze({ ...current, repository: actual });
}

async function readPreparedResponse(response) {
  if (response.status >= 300 && response.status < 400) fail('GAME_MASTER_REDIRECT_REJECTED');
  if (response.status === 401 || response.status === 403) fail('TEAM_AUTH_REJECTED');
  if (!response.ok) fail('PREPARATION_ACKNOWLEDGEMENT_FAILED');
  const declared = Number(response.headers.get('content-length'));
  if (Number.isFinite(declared) && declared > MAX_RESPONSE_BYTES) fail('GAME_MASTER_RESPONSE_TOO_LARGE');
  if (!/^application\/json(?:\s*;|$)/i.test(response.headers.get('content-type') ?? '')) {
    fail('INVALID_GAME_MASTER_RESPONSE');
  }
  const body = await response.text().catch(() => fail('INVALID_GAME_MASTER_RESPONSE'));
  if (Buffer.byteLength(body) > MAX_RESPONSE_BYTES) fail('GAME_MASTER_RESPONSE_TOO_LARGE');
  let value;
  try { value = JSON.parse(body); } catch { fail('INVALID_GAME_MASTER_RESPONSE'); }
  if (!object(value) || value.status !== 'PREPARED') fail('PREPARATION_NOT_ACKNOWLEDGED');
  return response.headers.get('x-request-id');
}

export async function acknowledgeEnvironmentReady({ session, preparationRequestId, repository,
  environmentResult }, { fetchImpl = globalThis.fetch, timeoutMs = 30_000,
  testMode = false } = {}) {
  if (!object(session) || !id(session.playerSessionId) || !id(session.teamId) ||
      !id(session.matchId) || !text(session.apiKeySecret, 16_384) ||
      !object(session.preparationComplete) || session.preparationComplete.method !== 'POST' ||
      !text(session.preparationComplete.url) || !Array.isArray(session.allowedApiOrigins) ||
      typeof fetchImpl !== 'function' || !Number.isInteger(timeoutMs) ||
      timeoutMs < 1 || timeoutMs > 120_000) fail('INVALID_PLAYER_SESSION');
  const ready = validateEnvironmentReady(environmentResult,
    { requestId: preparationRequestId, repository, testMode });
  const endpoint = httpUrl(session.preparationComplete.url, { testMode });
  const allowed = new Set(session.allowedApiOrigins.map(value =>
    httpUrl(`${value}/`, { testMode }).origin));
  if (!allowed.has(endpoint.origin) ||
      !new RegExp(`^/api/v1/matches/${UUID}/preparation/complete$`, 'i').test(endpoint.pathname)) {
    fail('PREPARATION_ENDPOINT_NOT_ALLOWED');
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let gameMasterRequestId;
  try {
    const response = await fetchImpl(endpoint, { method: 'POST', redirect: 'manual',
      cache: 'no-store', signal: controller.signal,
      headers: { authorization: `Bearer ${session.apiKeySecret}`,
        accept: 'application/json', 'content-type': 'application/json' },
      body: '{}' });
    gameMasterRequestId = await readPreparedResponse(response);
  } catch (error) {
    if (error instanceof PlayerPreparationError) throw error;
    fail('GAME_MASTER_UNAVAILABLE');
  } finally { clearTimeout(timer); }
  if (gameMasterRequestId !== null && !id(gameMasterRequestId)) fail('INVALID_GAME_MASTER_RESPONSE');
  return Object.freeze({ schemaVersion: 1, status: 'PREPARED',
    playerSessionId: session.playerSessionId, teamId: session.teamId,
    matchId: session.matchId, preparationRequestId: ready.requestId,
    environmentRevision: ready.revision, gameMasterRequestId });
}

export function validatePreparationReceipt(value, session) {
  if (!exact(value, ['schemaVersion', 'status', 'playerSessionId', 'teamId', 'matchId',
    'preparationRequestId', 'environmentRevision', 'gameMasterRequestId']) ||
      value.schemaVersion !== 1 || value.status !== 'PREPARED' ||
      !id(value.playerSessionId) || !id(value.teamId) || !id(value.matchId) ||
      !id(value.preparationRequestId) || !/^[a-f0-9]{40,64}$/.test(value.environmentRevision) ||
      !(value.gameMasterRequestId === null || id(value.gameMasterRequestId)) ||
      value.playerSessionId !== session?.playerSessionId || value.teamId !== session?.teamId ||
      value.matchId !== session?.matchId) fail('INVALID_PREPARATION_RECEIPT');
  return Object.freeze({ ...value });
}
