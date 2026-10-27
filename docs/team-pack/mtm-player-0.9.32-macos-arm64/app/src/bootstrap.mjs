import { randomUUID } from 'node:crypto';
import { existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, realpathSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { isAbsolute, join, parse, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { homedir } from 'node:os';
import { verifyTeamRuntime as verifyTeamRuntimeDefault } from './team-install.mjs';
import { writeWorkspaceInstructions } from './workspace-instructions.mjs';

const packageRoot = realpathSync(fileURLToPath(new URL('..', import.meta.url)));
export const DEFAULT_GAME_MASTER_API_URL = 'https://ai-fest-five.vercel.app/';
// The team API scopes are no longer returned by the Game Master; the operator's
// Team API Key is trusted as-is. These are recorded for the orchestrator only.
const DEFAULT_TEAM_API_SCOPES = Object.freeze(['submission:create', 'submission:read']);
const currentResponseFields = ['team', 'gameMasterUrl', 'matchId', 'repository',
  'preparationComplete'];
const retryableReplaceErrors = new Set(['EPERM', 'EACCES', 'EBUSY']);
const pauseSync = milliseconds =>
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, milliseconds);

export class PlayerInitError extends Error {
  constructor(code) {
    super(code);
    this.name = 'PlayerInitError';
    this.code = code;
  }
}

const fail = code => { throw new PlayerInitError(code); };
const exactKeys = (value, keys) => value && typeof value === 'object' && !Array.isArray(value) &&
  JSON.stringify(Object.keys(value).sort()) === JSON.stringify([...keys].sort());
const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f-\u009f]/;
const bounded = (value, max = 4096) => typeof value === 'string' && value.length > 0 &&
  value.length <= max && !CONTROL_CHARACTERS.test(value);
const safeId = value => bounded(value, 128) && /^[A-Za-z0-9][A-Za-z0-9_.:-]*$/.test(value);
const overlaps = (a, b) => {
  const rel = relative(a, b);
  return rel === '' || (!rel.startsWith('..') && !isAbsolute(rel));
};

function secureUrl(value, { testMode = false, field = 'INVALID_URL' } = {}) {
  if (!bounded(value)) fail(field);
  let url;
  try { url = new URL(value); } catch { fail(field); }
  const localhost = ['localhost', '127.0.0.1', '::1'].includes(url.hostname);
  if (url.protocol !== 'https:' && !(testMode && url.protocol === 'http:' && localhost)) fail(field);
  if (url.username || url.password || url.hash || url.search) fail(field);
  return url;
}

function gitRepository(value, { testMode = false } = {}) {
  if (!bounded(value) || /\s/.test(value)) fail('INVALID_TEAM_INFO');
  if (/^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:[A-Za-z0-9._~/-]+$/.test(value)) return value;
  let url;
  try { url = new URL(value); } catch { fail('INVALID_TEAM_INFO'); }
  const localhost = ['localhost', '127.0.0.1', '::1'].includes(url.hostname);
  const allowedProtocol = url.protocol === 'https:' || url.protocol === 'ssh:' ||
    (testMode && url.protocol === 'http:' && localhost);
  if (!allowedProtocol || url.password || (url.protocol !== 'ssh:' && url.username) ||
      url.search || url.hash) fail('INVALID_TEAM_INFO');
  return url.href;
}

function repositoryAssignment(value, matchId, options) {
  if (!exactKeys(value, ['url', 'branch', 'folder'])) fail('INVALID_TEAM_INFO');
  const url = gitRepository(value.url, options);
  if (!/^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$/.test(value.branch) ||
      value.branch.includes('..') || value.branch.includes('@{') ||
      value.branch.includes('//') || value.branch.endsWith('/') ||
      value.branch.endsWith('.') || value.branch.split('/').some(part =>
        part.startsWith('.') || part.endsWith('.lock'))) fail('INVALID_TEAM_INFO');
  const folder = typeof value.folder === 'string' ? value.folder.replace(/\/$/, '') : '';
  if (!safeId(folder) || folder !== matchId || value.folder !== `${folder}/`) {
    fail('INVALID_TEAM_INFO');
  }
  return Object.freeze({ url, branch: value.branch, folder });
}

function preparationContract(value, { gameMaster, testMode }) {
  if (!exactKeys(value, ['method', 'url']) || value.method !== 'POST') fail('INVALID_TEAM_INFO');
  const endpoint = secureUrl(value.url, { testMode, field: 'INVALID_TEAM_INFO' });
  if (endpoint.origin !== gameMaster.origin ||
      !/^\/api\/v1\/matches\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/preparation\/complete$/i
        .test(endpoint.pathname)) fail('INVALID_TEAM_INFO');
  return Object.freeze({ method: 'POST', url: endpoint.href });
}

export function validateTeamWorkspaceLayout(input, { configDir, playerRoot = packageRoot } = {}) {
  if (!bounded(input, 4096) || !isAbsolute(input)) fail('INVALID_TEAM_WORKSPACE');
  let workspace;
  try {
    const lexical = resolve(input);
    let cursor = parse(lexical).root;
    for (const part of lexical.slice(cursor.length).split(/[\\/]/).filter(Boolean)) {
      cursor = join(cursor, part);
      if (lstatSync(cursor).isSymbolicLink()) fail('LINKED_TEAM_WORKSPACE');
    }
    workspace = realpathSync(lexical);
    if (!statSync(workspace).isDirectory()) fail('INVALID_TEAM_WORKSPACE');
  } catch (error) {
    if (error instanceof PlayerInitError) throw error;
    fail('INVALID_TEAM_WORKSPACE');
  }
  const protectedPaths = [playerRoot, configDir].filter(Boolean).map(path => resolve(path));
  if (protectedPaths.some(path => overlaps(path, workspace) || overlaps(workspace, path))) {
    fail('TEAM_WORKSPACE_OVERLAP');
  }
  let entries;
  try { entries = readdirSync(workspace); }
  catch { fail('INVALID_TEAM_WORKSPACE'); }
  if (entries.length === 0) {
    const challenge = join(workspace, 'matches');
    const pack = join(workspace, 'team-sdlc-pack');
    try {
      mkdirSync(challenge);
      mkdirSync(pack);
    } catch {
      // Roll back the convenience setup so a failed creation never leaves a layout
      // that looks valid on the next initialization.
      rmSync(challenge, { recursive: true, force: true });
      rmSync(pack, { recursive: true, force: true });
      fail('TEAM_WORKSPACE_PREPARATION_FAILED');
    }
    fail('TEAM_WORKSPACE_PREPARED');
  }
  const child = name => {
    const path = join(workspace, name);
    try {
      const stat = lstatSync(path);
      if (stat.isSymbolicLink() || !stat.isDirectory()) fail('INVALID_TEAM_WORKSPACE_LAYOUT');
      const canonical = realpathSync(path);
      if (!overlaps(workspace, canonical) || canonical === workspace) fail('INVALID_TEAM_WORKSPACE_LAYOUT');
      return canonical;
    } catch (error) {
      if (error instanceof PlayerInitError) throw error;
      fail('INVALID_TEAM_WORKSPACE_LAYOUT');
    }
  };
  // Legacy challenge checkouts are retained for existing sessions. New matches
  // always use matches; never move or clean the legacy directory here.
  const matches = join(workspace, 'matches');
  if (existsSync(join(workspace, 'challenge'))) child('challenge');
  if (!existsSync(matches) && existsSync(join(workspace, 'challenge'))) mkdirSync(matches);
  return Object.freeze({ teamWorkspace: workspace,
    matchesWorkspace: child('matches'),
    challengeWorkspace: child('matches'),
    teamSdlcPackWorkspace: child('team-sdlc-pack') });
}

export function validateTeamWorkspace(input, options = {}) {
  return validateTeamWorkspaceLayout(input, options).teamWorkspace;
}

export function ensureEmptyChallengeWorkspace(layout, { clearChallenge = false } = {}) {
  if (!layout || typeof clearChallenge !== 'boolean') fail('INVALID_CHALLENGE_RESET');
  let entries;
  try { entries = readdirSync(layout.challengeWorkspace); }
  catch { fail('INVALID_TEAM_WORKSPACE_LAYOUT'); }
  if (entries.length === 0) return layout;
  if (!clearChallenge) fail('CHALLENGE_NOT_EMPTY');
  for (const entry of entries) {
    const target = join(layout.challengeWorkspace, entry);
    try { rmSync(target, { recursive: true, force: true }); }
    catch { fail('CHALLENGE_RESET_FAILED'); }
  }
  if (readdirSync(layout.challengeWorkspace).length !== 0) fail('CHALLENGE_RESET_FAILED');
  return layout;
}

export class JsonBootstrapConfigStore {
  constructor(configDir, { renameFile = renameSync, pause = pauseSync } = {}) {
    this.configDir = resolve(configDir);
    this.path = join(this.configDir, 'config.json');
    this.renameFile = renameFile;
    this.pause = pause;
  }

  load() {
    if (!existsSync(this.path)) return null;
    let value;
    try {
      if (lstatSync(this.configDir).isSymbolicLink() || lstatSync(this.path).isSymbolicLink() ||
          statSync(this.path).size > 32 * 1024) fail('INVALID_BOOTSTRAP_CONFIG');
      value = JSON.parse(readFileSync(this.path, 'utf8'));
    } catch (error) {
      if (error instanceof PlayerInitError) throw error;
      fail('INVALID_BOOTSTRAP_CONFIG');
    }
    const legacy = exactKeys(value, ['schemaVersion', 'gameMasterApiUrl', 'credentialId']) &&
      value.schemaVersion === 1 && bounded(value.gameMasterApiUrl) && safeId(value.credentialId);
    const current = exactKeys(value, ['schemaVersion', 'gameMasterApiUrl']) &&
      value.schemaVersion === 2 && bounded(value.gameMasterApiUrl);
    if (!legacy && !current) {
      fail('INVALID_BOOTSTRAP_CONFIG');
    }
    return value;
  }

  save(value) {
    if (existsSync(this.configDir) && lstatSync(this.configDir).isSymbolicLink()) fail('INVALID_BOOTSTRAP_CONFIG');
    if (existsSync(this.path) && lstatSync(this.path).isSymbolicLink()) fail('INVALID_BOOTSTRAP_CONFIG');
    mkdirSync(this.configDir, { recursive: true, mode: 0o700 });
    const serialized = `${JSON.stringify(value, null, 2)}\n`;
    // The web form includes the visible saved URL on every match. Avoid replacing the
    // file when that value did not change; on Windows another process may temporarily
    // retain a read handle that does not permit rename/delete sharing.
    if (existsSync(this.path) && readFileSync(this.path, 'utf8') === serialized) return;
    const temporary = `${this.path}.${randomUUID()}.tmp`;
    writeFileSync(temporary, serialized, { encoding: 'utf8', mode: 0o600, flag: 'wx' });
    try {
      for (let attempt = 0; ; attempt++) {
        try {
          this.renameFile(temporary, this.path);
          return;
        } catch (error) {
          if (!retryableReplaceErrors.has(error?.code) || attempt === 19) throw error;
          this.pause(50);
        }
      }
    } catch {
      rmSync(temporary, { force: true });
      fail('BOOTSTRAP_CONFIG_WRITE_FAILED');
    }
  }
}

export class MemoryCredentialStore {
  #values = new Map();
  put(id, secret) { if (!safeId(id) || !bounded(secret, 16_384)) fail('INVALID_BOOTSTRAP_SECRET'); this.#values.set(id, secret); }
  get(id) { const secret = this.#values.get(id); if (!secret) fail('BOOTSTRAP_CREDENTIAL_UNAVAILABLE'); return secret; }
  delete(id) { this.#values.delete(id); }
  snapshot() { return new Map(this.#values); }
}

export class LegacyCredentialFileStore {
  constructor(directory) {
    this.directory = resolve(directory);
  }

  delete(id) {
    if (!safeId(id)) fail('CREDENTIAL_CLEANUP_FAILED');
    if (existsSync(this.directory) && lstatSync(this.directory).isSymbolicLink()) {
      fail('CREDENTIAL_CLEANUP_FAILED');
    }
    const path = join(this.directory, `${id}.dpapi`);
    if (existsSync(path) && lstatSync(path).isSymbolicLink()) fail('CREDENTIAL_CLEANUP_FAILED');
    rmSync(path, { force: true });
  }
}

export function validateTeamInfo(value, { teamId, matchId, apiUrl, requestId,
  testMode = false, teamApiKey, username, password } = {}) {
  const current = exactKeys(value, currentResponseFields);
  if (!current ||
      !exactKeys(value.team, ['uuid', 'id', 'name'])) fail('INVALID_TEAM_INFO');
  // The Game Master no longer returns the team login or the team API key. The
  // operator supplies username/password on the Init form, and the Team API Key
  // field (also the Get-Team-Info / dashboard bearer) becomes the team API secret.
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value.team.uuid) ||
      !safeId(value.team.id) || value.team.id.toUpperCase() !== teamId.toUpperCase() ||
      !safeId(matchId) || !safeId(value.matchId) || value.matchId !== matchId ||
      !bounded(value.team.name, 1024) || !bounded(username, 1024) ||
      !bounded(password, 16_384) || !bounded(teamApiKey, 16_384) ||
      !safeId(requestId)) fail('INVALID_TEAM_INFO');
  const api = secureUrl(apiUrl, { testMode, field: 'INVALID_GAME_MASTER_API_URL' });
  const gameMaster = secureUrl(value.gameMasterUrl, { testMode, field: 'INVALID_TEAM_INFO' });
  const matchPage = new URL('/match', gameMaster);
  const repository = repositoryAssignment(value.repository, value.matchId, { testMode });
  const preparationComplete = preparationContract(value.preparationComplete,
    { gameMaster, testMode });
  const allowedApiOrigins = [...new Set([api.origin, gameMaster.origin])];
  return Object.freeze({
    schemaVersion: 1,
    matchId: value.matchId,
    teamUuid: value.team.uuid,
    teamId: value.team.id,
    teamName: value.team.name,
    username,
    password,
    apiKeySecret: teamApiKey,
    apiKeyScopes: DEFAULT_TEAM_API_SCOPES,
    gameMasterUrl: gameMaster.href,
    matchPageUrl: matchPage.href,
    repository,
    preparationComplete,
    teamInfoRequestId: requestId,
    allowedApiOrigins: Object.freeze(allowedApiOrigins)
  });
}

async function fetchTeamInfo({ fetchImpl, apiUrl, secret, teamId, matchId, testMode, timeoutMs }) {
  const base = secureUrl(apiUrl, { testMode, field: 'INVALID_GAME_MASTER_API_URL' });
  const endpoint = new URL('/api/v1/game-player/teams', base);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let response;
  try {
    response = await fetchImpl(endpoint, { method: 'POST', redirect: 'manual', cache: 'no-store', signal: controller.signal,
      headers: { authorization: `Bearer ${secret}`, 'content-type': 'application/json', accept: 'application/json' },
      body: JSON.stringify({ teamId, matchId }) });
  } catch { fail('GAME_MASTER_UNAVAILABLE'); }
  finally { clearTimeout(timer); }
  if (response.status >= 300 && response.status < 400) fail('GAME_MASTER_REDIRECT_REJECTED');
  if (response.status === 401 || response.status === 403) fail('BOOTSTRAP_AUTH_REJECTED');
  if (response.status === 400 || response.status === 422) fail('GAME_MASTER_VALIDATION_FAILED');
  if (!response.ok) fail('GAME_MASTER_REQUEST_FAILED');
  if (!/^application\/json(?:\s*;|$)/i.test(response.headers.get('content-type') ?? '')) fail('INVALID_TEAM_INFO');
  const cacheControl = (response.headers.get('cache-control') ?? '').split(',').map(value => value.trim().toLowerCase());
  const requestId = response.headers.get('x-request-id');
  if (!cacheControl.includes('no-store') || !safeId(requestId)) fail('INVALID_TEAM_INFO_HEADERS');
  const declaredLength = Number(response.headers.get('content-length'));
  if (Number.isFinite(declaredLength) && declaredLength > 64 * 1024) fail('TEAM_INFO_TOO_LARGE');
  let text;
  try { text = await response.text(); } catch { fail('INVALID_TEAM_INFO'); }
  if (Buffer.byteLength(text) > 64 * 1024) fail('TEAM_INFO_TOO_LARGE');
  let value;
  try { value = JSON.parse(text); } catch { fail('INVALID_TEAM_INFO'); }
  return { value, requestId };
}

export async function initializeMatch({ teamId, matchId, teamWorkspace, gameMasterApiUrl,
  gameMasterSecretKey, teamLoginAccount, teamPassword, reconfigure = false, clearChallenge = false, preserveChallenge = false }, { configStore, credentialStore,
  fetchImpl = globalThis.fetch, testMode = false,
  timeoutMs = 30_000, playerRoot = packageRoot,
  verifyTeamRuntime = verifyTeamRuntimeDefault } = {}) {
  if (!configStore || typeof fetchImpl !== 'function' ||
      typeof verifyTeamRuntime !== 'function') fail('INVALID_INITIALIZER');
  if (!safeId(teamId)) fail('INVALID_TEAM_ID');
  if (!safeId(matchId)) fail('INVALID_MATCH_ID');
  const layout = validateTeamWorkspaceLayout(teamWorkspace,
    { configDir: configStore.configDir, playerRoot });
  const teamRuntime = verifyTeamRuntime(layout);
  if (teamRuntime?.action !== 'VERIFIED' || teamRuntime?.runtime?.status !== 'READY' ||
      !safeId(teamRuntime.runtime.teamId) || !bounded(teamRuntime.runtime.teamName, 160)) {
    fail('TEAM_RUNTIME_SETUP_FAILED');
  }
  if (teamRuntime.runtime.teamId.toLowerCase() !== teamId.toLowerCase()) {
    fail('TEAM_PACK_TEAM_MISMATCH');
  }
  if (typeof preserveChallenge !== 'boolean' || (preserveChallenge && clearChallenge)) {
    fail('INVALID_CHALLENGE_RESET');
  }
  // Restart is the sole path that may retain an existing challenge checkout. Its
  // durable checkpoint is validated after fresh Game Master authentication.
  const previous = configStore.load();
  if (!bounded(gameMasterSecretKey, 16_384)) fail('BOOTSTRAP_CONFIGURATION_REQUIRED');
  if (!previous || reconfigure) {
    if (gameMasterApiUrl !== undefined && !bounded(gameMasterApiUrl)) fail('BOOTSTRAP_CONFIGURATION_REQUIRED');
  } else if (gameMasterApiUrl !== undefined) {
    fail('EXPLICIT_RECONFIGURATION_REQUIRED');
  }
  const apiUrl = previous && !reconfigure ? previous.gameMasterApiUrl : (gameMasterApiUrl ?? DEFAULT_GAME_MASTER_API_URL);
  secureUrl(apiUrl, { testMode, field: 'INVALID_GAME_MASTER_API_URL' });
  const fetched = await fetchTeamInfo({ fetchImpl, apiUrl, secret: gameMasterSecretKey,
    teamId, matchId, testMode, timeoutMs });
  // The Game Master no longer returns the team login or API key: the operator's
  // Team Login Account, Password, and Team API Key (the fetch bearer above) are
  // what the session carries forward to the orchestrator.
  const teamInfo = validateTeamInfo(fetched.value,
    { teamId, matchId, apiUrl, requestId: fetched.requestId, testMode,
      teamApiKey: gameMasterSecretKey, username: teamLoginAccount, password: teamPassword });
  const checkoutRoot = resolve(layout.matchesWorkspace, matchId);
  if (existsSync(checkoutRoot) && (lstatSync(checkoutRoot).isSymbolicLink() || !statSync(checkoutRoot).isDirectory())) fail('INVALID_TEAM_WORKSPACE_LAYOUT');
  mkdirSync(checkoutRoot, { recursive: true });
  if (!preserveChallenge) ensureEmptyChallengeWorkspace({ challengeWorkspace: checkoutRoot }, { clearChallenge });
  const paths = { checkoutRoot, matchRoot: resolve(checkoutRoot, teamInfo.repository.folder) };
  writeWorkspaceInstructions({ ...layout, ...teamInfo, ...paths, gameMasterApiUrl: apiUrl });

  if (!previous || reconfigure || previous.schemaVersion === 1) {
    configStore.save({ schemaVersion: 2, gameMasterApiUrl: apiUrl });
  }
  if (previous?.schemaVersion === 1 && typeof credentialStore?.delete === 'function') {
    try { credentialStore.delete(previous.credentialId); } catch { /* migration cleanup must not block init */ }
  }

  return Object.freeze({
    status: 'READY',
    sensitive: true,
    persistence: 'PLAYER_CONVERSATION_ONLY',
    playerSessionId: randomUUID(),
    ...layout,
    ...paths,
    teamRuntime,
    gameMasterApiUrl: apiUrl,
    // The organizer bootstrap credential is retained only by this live Player
    // process. Public receipts, checkpoints, logs, and dashboard snapshots omit it.
    gameMasterPlayerSecret: gameMasterSecretKey,
    ...teamInfo
  });
}

export function defaultPlayerDataDirectory({ platform = process.platform, env = process.env, home = homedir() } = {}) {
  if (platform === 'darwin') return join(home, 'Library', 'Application Support', 'MTM Player');
  if (platform !== 'win32') return join(env.XDG_DATA_HOME || join(home, '.local', 'share'), 'mtm-player');
  const base = env.LOCALAPPDATA;
  if (!base) fail('LOCAL_APP_DATA_UNAVAILABLE');
  return join(base, 'MTM Player');
}
