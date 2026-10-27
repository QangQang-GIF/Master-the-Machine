import { randomUUID } from 'node:crypto';
import { existsSync, lstatSync, mkdirSync, readFileSync, realpathSync, renameSync, rmSync,
  statSync, writeFileSync } from 'node:fs';
import { isAbsolute, join, relative, resolve } from 'node:path';
import { matchWorkspacePaths } from './workspace-paths.mjs';

export class RestartCheckpointError extends Error {
  constructor(code) { super(code); this.code = code; this.name = 'RestartCheckpointError'; }
}

const fail = code => { throw new RestartCheckpointError(code); };
const object = value => value && typeof value === 'object' && !Array.isArray(value);
const exact = (value, keys) => object(value) &&
  JSON.stringify(Object.keys(value).sort()) === JSON.stringify([...keys].sort());
const id = value => typeof value === 'string' &&
  /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(value);
const revision = value => typeof value === 'string' && /^[a-f0-9]{40,64}$/.test(value);
const repository = value => exact(value, ['url', 'branch', 'folder']) &&
  typeof value.url === 'string' && value.url.length > 0 &&
  typeof value.branch === 'string' && value.branch.length > 0 && id(value.folder);
const samePath = (left, right) => resolve(left).toLowerCase() === resolve(right).toLowerCase();
const replaceErrors = new Set(['EPERM', 'EACCES', 'EBUSY']);
const pause = milliseconds => Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, milliseconds);

export function restartCheckpointPath(teamSdlcPackWorkspace, matchId) {
  if (typeof teamSdlcPackWorkspace !== 'string' || !isAbsolute(teamSdlcPackWorkspace) || !id(matchId)) {
    fail('INVALID_RESTART_CHECKPOINT_PATH');
  }
  const root = realpathSync(teamSdlcPackWorkspace);
  if (!statSync(root).isDirectory() || lstatSync(teamSdlcPackWorkspace).isSymbolicLink()) {
    fail('INVALID_RESTART_CHECKPOINT_PATH');
  }
  const logs = join(root, 'logs');
  mkdirSync(logs, { recursive: true, mode: 0o700 });
  if (lstatSync(logs).isSymbolicLink()) fail('INVALID_RESTART_CHECKPOINT_PATH');
  const path = resolve(logs, `${matchId}.player-checkpoint.json`);
  const rel = relative(root, path);
  if (rel.startsWith('..') || isAbsolute(rel) || (existsSync(path) && lstatSync(path).isSymbolicLink())) {
    fail('INVALID_RESTART_CHECKPOINT_PATH');
  }
  return path;
}

function validate(value) {
  const keys = ['schemaVersion', 'status', 'playerSessionId', 'teamUuid', 'teamId', 'teamName',
    'matchId', 'teamWorkspace', 'challengeWorkspace', 'teamSdlcPackWorkspace', 'repository',
    'environmentRevision', 'progressLogFile', 'restartAttempt'];
  if (!exact(value, [...keys, ...['checkoutRoot', 'matchRoot'].filter(key => Object.hasOwn(value, key))]) || value.schemaVersion !== 1 ||
      !['PREPARED', 'GAME_STARTED'].includes(value.status) ||
      !id(value.playerSessionId) || !id(value.teamId) || !id(value.matchId) ||
      typeof value.teamUuid !== 'string' ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value.teamUuid) ||
      typeof value.teamName !== 'string' || value.teamName.length === 0 || value.teamName.length > 1024 ||
      !isAbsolute(value.teamWorkspace) || !isAbsolute(value.challengeWorkspace) ||
      !isAbsolute(value.teamSdlcPackWorkspace) || !isAbsolute(value.progressLogFile) ||
      !repository(value.repository) || !revision(value.environmentRevision) ||
      !Number.isSafeInteger(value.restartAttempt) || value.restartAttempt < 0 ||
      value.restartAttempt > 10_000) fail('INVALID_RESTART_CHECKPOINT');
  try { matchWorkspacePaths(value); } catch { fail('INVALID_RESTART_CHECKPOINT'); }
  return Object.freeze({ ...value, repository: Object.freeze({ ...value.repository }) });
}

export function writeRestartCheckpoint(session, {
  status = 'GAME_STARTED', environmentRevision, restartAttempt = 0
} = {}) {
  if (!object(session) || !Object.isFrozen(session) || !revision(environmentRevision) ||
      !['PREPARED', 'GAME_STARTED'].includes(status) ||
      !Number.isSafeInteger(restartAttempt) || restartAttempt < 0 || restartAttempt > 10_000) {
    fail('INVALID_RESTART_CHECKPOINT');
  }
  const path = restartCheckpointPath(session.teamSdlcPackWorkspace, session.matchId);
  const value = validate({ schemaVersion: 1, status,
    playerSessionId: session.playerSessionId, teamUuid: session.teamUuid,
    teamId: session.teamId, teamName: session.teamName, matchId: session.matchId,
    teamWorkspace: session.teamWorkspace, challengeWorkspace: session.challengeWorkspace,
    teamSdlcPackWorkspace: session.teamSdlcPackWorkspace,
    repository: { ...session.repository }, environmentRevision,
    progressLogFile: resolve(session.teamSdlcPackWorkspace, 'logs', `${session.matchId}.progress.ndjson`),
    ...matchWorkspacePaths(session),
    restartAttempt });
  const temporary = `${path}.${randomUUID()}.tmp`;
  writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`,
    { encoding: 'utf8', mode: 0o600, flag: 'wx' });
  try {
    for (let attempt = 0; ; attempt += 1) {
      try { renameSync(temporary, path); break; }
      catch (error) {
        if (!replaceErrors.has(error?.code) || attempt === 19) throw error;
        pause(50);
      }
    }
  } catch {
    rmSync(temporary, { force: true });
    fail('RESTART_CHECKPOINT_WRITE_FAILED');
  }
  return Object.freeze({ path, checkpoint: value });
}

export function loadRestartCheckpoint(teamSdlcPackWorkspace, matchId) {
  const path = restartCheckpointPath(teamSdlcPackWorkspace, matchId);
  if (!existsSync(path) || statSync(path).size > 64 * 1024) fail('RESTART_CHECKPOINT_NOT_FOUND');
  let value;
  try { value = JSON.parse(readFileSync(path, 'utf8')); }
  catch { fail('INVALID_RESTART_CHECKPOINT'); }
  return Object.freeze({ path, checkpoint: validate(value) });
}

export function bindRestartSession(session, checkpoint) {
  if (!object(session) || !Object.isFrozen(session)) fail('INVALID_RESTART_SESSION');
  const value = validate(checkpoint);
  if (value.status !== 'GAME_STARTED') fail('RESTART_CHECKPOINT_NOT_STARTED');
  const sameRepository = session.repository.url === value.repository.url &&
    session.repository.branch === value.repository.branch &&
    session.repository.folder === value.repository.folder;
  if (session.teamUuid !== value.teamUuid || session.teamId.toLowerCase() !== value.teamId.toLowerCase() ||
      session.matchId !== value.matchId || !sameRepository ||
      !samePath(session.teamWorkspace, value.teamWorkspace) ||
      !samePath(session.challengeWorkspace, value.challengeWorkspace) ||
      !samePath(session.teamSdlcPackWorkspace, value.teamSdlcPackWorkspace) ||
      !samePath(value.progressLogFile,
        resolve(session.teamSdlcPackWorkspace, 'logs', `${session.matchId}.progress.ndjson`))) {
    fail('RESTART_CHECKPOINT_MISMATCH');
  }
  return Object.freeze({ ...session, playerSessionId: value.playerSessionId, ...matchWorkspacePaths(value) });
}
