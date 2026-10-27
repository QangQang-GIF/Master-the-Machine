import { brandHeader, brandStyles } from './brand-ui.mjs';
import { agentControlScript } from './agent-control-ui.mjs';
import { transcriptUiScript } from './transcript-ui.mjs';
import { randomBytes, randomUUID } from 'node:crypto';
import { createServer } from 'node:http';
import { existsSync, lstatSync, mkdirSync, readFileSync, realpathSync, renameSync, rmSync,
  statSync, writeFileSync } from 'node:fs';
import { isAbsolute, join, resolve } from 'node:path';
import { readPersistedMatchStatus } from './match-status.mjs';
import { applyModelPolicy } from './model-policy.mjs';

export class ProgressError extends Error {
  constructor(code) { super(code); this.code = code; this.name = 'ProgressError'; }
}

const fail = code => { throw new ProgressError(code); };
const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value) &&
  JSON.stringify(Object.keys(value).sort()) === JSON.stringify([...keys].sort());
const id = value => typeof value === 'string' && value.length > 0 && value.length <= 128 &&
  /^[A-Za-z0-9][A-Za-z0-9_.:-]*$/.test(value);
const credentialValue = /(?:authorization\s*:\s*bearer\s+\S{8,}|\b(?:password|secret|api[-_ ]?key|access[-_ ]?token)\s*[:=]\s*\S{8,}|\b(?:gm|gh[pousr])_[A-Za-z0-9_-]{12,})/i;
const safeText = (value, max) => typeof value === 'string' && value.length > 0 &&
  value.length <= max && !/[\r\n\0]/.test(value) && !credentialValue.test(value);
const nullableId = value => value === null || id(value);
const nullableRevision = value => value === null ||
  (typeof value === 'string' && /^[a-f0-9]{40,64}$/.test(value));
const usageCount = value => Number.isSafeInteger(value) && value >= 0 && value <= 1_000_000_000_000;
const methods = Object.freeze(['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']);
const telemetryPhases = Object.freeze(['REQUESTED', 'SUCCEEDED', 'FAILED']);
const filesystemAccess = Object.freeze(['READ', 'WRITE', 'DELETE', 'EXECUTE']);
const safeOrigin = value => {
  if (!safeText(value, 256)) return false;
  try {
    const parsed = new URL(value);
    const loopback = parsed.protocol === 'http:' && ['127.0.0.1', 'localhost', '::1'].includes(parsed.hostname);
    return (parsed.protocol === 'https:' || loopback) && parsed.origin === value &&
      parsed.username === '' && parsed.password === '' && parsed.pathname === '/' &&
      parsed.search === '' && parsed.hash === '';
  } catch { return false; }
};
const safePath = value => safeText(value, 512) && value.startsWith('/') &&
  !value.includes('?') && !value.includes('#') && !value.includes('\\');
const safeAbsolutePath = value => safeText(value, 2048) && isAbsolute(value);

export const preparationStages = Object.freeze([
  'GIT_AUTH', 'REPOSITORY', 'ENVIRONMENT', 'BUILD', 'MATCH_LOGIN', 'PREPARE_COMPLETE'
]);
export const gameStartStages = Object.freeze(['BACKLOG_SYNC']);
export const preparationStatuses = Object.freeze(['IN_PROGRESS', 'COMPLETED', 'BLOCKED', 'FAILED']);
export const backlogStatuses = Object.freeze([
  'DISCOVERED', 'IN_PROGRESS', 'COMPLETED', 'SUBMITTED', 'BLOCKED', 'FAILED'
]);
export const testStatuses = Object.freeze(['NOT_RUN', 'RUNNING', 'PASSED', 'FAILED']);
export const apiPhases = Object.freeze(['REQUESTED', 'SUCCEEDED', 'FAILED']);
const count = value => Number.isSafeInteger(value) && value >= 0 && value <= 100_000;

export const emptyPriceCard = Object.freeze({ schemaVersion: 1, id: 'mtm-unpriced-v1',
  currency: 'USD', unitTokens: 1_000_000, models: Object.freeze({}) });

export function validatePriceCard(value) {
  if (!exact(value, ['schemaVersion', 'id', 'currency', 'unitTokens', 'models']) ||
      value.schemaVersion !== 1 || !id(value.id) || !(value.currency === 'CREDITS' || /^[A-Z]{3}$/.test(value.currency)) ||
      !usageCount(value.unitTokens) || value.unitTokens === 0 || !value.models ||
      typeof value.models !== 'object' || Array.isArray(value.models)) fail('INVALID_PRICE_CARD');
  const models = {};
  for (const [model, rates] of Object.entries(value.models)) {
    if (!safeText(model, 128) || !exact(rates, ['input', 'cachedInput', 'output']) ||
        ![rates.input, rates.cachedInput, rates.output].every(rate =>
          typeof rate === 'number' && Number.isFinite(rate) && rate >= 0 && rate <= 1_000_000)) {
      fail('INVALID_PRICE_CARD');
    }
    models[model] = Object.freeze({ ...rates });
  }
  return Object.freeze({ ...value, models: Object.freeze(models) });
}

function canonicalWorkspace(value) {
  if (typeof value !== 'string' || !isAbsolute(value) || !existsSync(value) ||
      lstatSync(value).isSymbolicLink()) fail('INVALID_PROGRESS_WORKSPACE');
  const canonical = realpathSync(value);
  if (!statSync(canonical).isDirectory()) fail('INVALID_PROGRESS_WORKSPACE');
  return canonical;
}

export function progressLogPath(teamSdlcPackWorkspace, matchId) {
  if (!id(matchId)) fail('INVALID_PROGRESS_MATCH');
  return join(canonicalWorkspace(teamSdlcPackWorkspace), 'logs', `${matchId}.progress.ndjson`);
}

export function staticDashboardPath(teamSdlcPackWorkspace, matchId) {
  if (!id(matchId)) fail('INVALID_PROGRESS_MATCH');
  return join(canonicalWorkspace(teamSdlcPackWorkspace), 'logs', `${matchId}.dashboard.html`);
}

function playerProgressPath(teamSdlcPackWorkspace, matchId) {
  if (!id(matchId)) fail('INVALID_PROGRESS_MATCH');
  const workspace = canonicalWorkspace(teamSdlcPackWorkspace);
  const directory = join(workspace, 'logs');
  if (!existsSync(directory)) mkdirSync(directory, { mode: 0o700 });
  if (lstatSync(directory).isSymbolicLink() || !statSync(directory).isDirectory()) {
    fail('UNSAFE_PROGRESS_LOG');
  }
  const path = join(directory, `${matchId}.player-progress.json`);
  if (existsSync(path) && (lstatSync(path).isSymbolicLink() || !statSync(path).isFile())) {
    fail('UNSAFE_PROGRESS_LOG');
  }
  return path;
}

function resetPlayerProgress(teamSdlcPackWorkspace, matchId) {
  const path = playerProgressPath(teamSdlcPackWorkspace, matchId);
  if (existsSync(path)) rmSync(path, { force: true });
}

function validatePlayerPreparationComplete(value, context) {
  if (!exact(value, ['schemaVersion', 'type', 'playerSessionId', 'matchId', 'status',
    'environmentRevision', 'gameMasterRequestId', 'summary']) || value.schemaVersion !== 1 ||
      value.type !== 'PLAYER_PREPARATION_COMPLETE' || value.playerSessionId !== context.playerSessionId ||
      value.matchId !== context.matchId || value.status !== 'COMPLETED' ||
      !nullableRevision(value.environmentRevision) || value.environmentRevision === null ||
      !nullableId(value.gameMasterRequestId) || !safeText(value.summary, 240)) {
    fail('INVALID_PLAYER_PROGRESS');
  }
  return Object.freeze(value);
}

export function recordPlayerPreparationComplete(session, receipt) {
  if (!session || !id(session.playerSessionId) || !id(session.matchId) ||
      !session.teamSdlcPackWorkspace || !receipt || receipt.status !== 'PREPARED') {
    fail('INVALID_PLAYER_PROGRESS');
  }
  const context = { playerSessionId: session.playerSessionId, matchId: session.matchId };
  const value = validatePlayerPreparationComplete({ schemaVersion: 1,
    type: 'PLAYER_PREPARATION_COMPLETE', playerSessionId: session.playerSessionId,
    matchId: session.matchId, status: 'COMPLETED',
    environmentRevision: receipt.environmentRevision,
    gameMasterRequestId: receipt.gameMasterRequestId ?? null,
    summary: 'Game Master accepted preparation.' }, context);
  const path = playerProgressPath(session.teamSdlcPackWorkspace, session.matchId);
  if (existsSync(path)) {
    let existing;
    try { existing = validatePlayerPreparationComplete(JSON.parse(readFileSync(path, 'utf8')), context); }
    catch { fail('INVALID_PLAYER_PROGRESS'); }
    if (JSON.stringify(existing) !== JSON.stringify(value)) fail('PLAYER_PROGRESS_CONFLICT');
    return existing;
  }
  const temporary = `${path}.${randomUUID()}.tmp`;
  try {
    writeFileSync(temporary, `${JSON.stringify(value)}\n`, { encoding: 'utf8', mode: 0o600, flag: 'wx' });
    renameSync(temporary, path);
  } catch (error) {
    rmSync(temporary, { force: true });
    if (error?.code === 'EEXIST' && existsSync(path)) {
      return recordPlayerPreparationComplete(session, receipt);
    }
    fail('PLAYER_PROGRESS_WRITE_FAILED');
  }
  return value;
}

function readPlayerPreparationComplete(context) {
  const path = playerProgressPath(context.teamSdlcPackWorkspace, context.matchId);
  if (!existsSync(path)) return null;
  if (statSync(path).size > 16 * 1024) fail('INVALID_PLAYER_PROGRESS');
  let value;
  try { value = JSON.parse(readFileSync(path, 'utf8')); }
  catch { fail('INVALID_PLAYER_PROGRESS'); }
  return validatePlayerPreparationComplete(value, context);
}

function applyPlayerPreparationComplete(snapshot, value) {
  if (!value) return snapshot;
  const preparation = { ...snapshot.preparation,
    PREPARE_COMPLETE: { status: 'COMPLETED', summary: value.summary, source: 'PLAYER' } };
  const prepared = Object.values(preparation).every(item => item.status === 'COMPLETED');
  return Object.freeze({ ...snapshot, preparation,
    overallStatus: prepared && snapshot.overallStatus === 'PREPARING' ? 'PREPARED' :
      snapshot.overallStatus });
}

export function writeStaticDashboard(path, snapshot) {
  if (typeof path !== 'string' || !isAbsolute(path)) fail('UNSAFE_STATIC_DASHBOARD');
  const directory = resolve(path, '..');
  if (!existsSync(directory) || lstatSync(directory).isSymbolicLink() ||
      !statSync(directory).isDirectory() ||
      (existsSync(path) && (lstatSync(path).isSymbolicLink() || !statSync(path).isFile()))) {
    fail('UNSAFE_STATIC_DASHBOARD');
  }
  writeFileSync(path, dashboardDocument(snapshot), { encoding: 'utf8', flag: 'w', mode: 0o600 });
  return path;
}

export function initializeProgressLog(teamSdlcPackWorkspace, matchId, playerSessionId = null) {
  const workspace = canonicalWorkspace(teamSdlcPackWorkspace);
  const directory = join(workspace, 'logs');
  if (existsSync(directory)) {
    if (lstatSync(directory).isSymbolicLink() || !statSync(directory).isDirectory()) {
      fail('UNSAFE_PROGRESS_LOG');
    }
  } else {
    mkdirSync(directory);
  }
  if (!id(matchId)) fail('INVALID_PROGRESS_MATCH');
  const path = join(directory, `${matchId}.progress.ndjson`);
  if (existsSync(path) && (lstatSync(path).isSymbolicLink() || !statSync(path).isFile())) {
    fail('UNSAFE_PROGRESS_LOG');
  }
  if (playerSessionId !== null && !id(playerSessionId)) fail('INVALID_PROGRESS_IDENTITY');
  const initial = playerSessionId === null ? '' : JSON.stringify({ schemaVersion: 1, playerSessionId,
    type: 'ORCHESTRATOR_STARTED', summary: 'Team orchestrator started.' }) + '\n';
  writeFileSync(path, initial, { encoding: 'utf8', flag: 'w', mode: 0o600 });
  return path;
}

export function validateProgressEvent(value, playerSessionId) {
  const base = ['schemaVersion', 'playerSessionId', 'type'];
  if (!id(playerSessionId) || value?.schemaVersion !== 1 || value?.playerSessionId !== playerSessionId) {
    fail('INVALID_PROGRESS_EVENT');
  }
  if (value.type === 'ORCHESTRATOR_STARTED' && exact(value, [...base, 'summary']) &&
      safeText(value.summary, 240)) return Object.freeze(value);
  if (value.type === 'ORCHESTRATOR_RESTARTED' &&
      exact(value, [...base, 'attempt', 'phase', 'reason', 'summary']) &&
      Number.isSafeInteger(value.attempt) && value.attempt >= 1 && value.attempt <= 10_000 &&
      ['PREPARATION', 'GAME'].includes(value.phase) && id(value.reason) &&
      safeText(value.summary, 240)) return Object.freeze(value);
  if (value.type === 'PREPARATION_STATUS' && exact(value, [...base, 'stage', 'status', 'summary']) &&
      preparationStages.includes(value.stage) && preparationStatuses.includes(value.status) &&
      safeText(value.summary, 240)) return Object.freeze(value);
  if (value.type === 'ENVIRONMENT_READY' &&
      exact(value, [...base, 'requestId', 'repository', 'revision', 'build', 'matchLogin']) &&
      id(value.requestId) && value.build === 'PASS' && value.matchLogin === 'PASS' &&
      nullableRevision(value.revision) && value.revision !== null &&
      exact(value.repository, ['url', 'branch', 'folder']) &&
      safeText(value.repository.url, 4096) && safeText(value.repository.branch, 255) &&
      /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value.repository.folder)) {
    try {
      const url = new URL(value.repository.url);
      if (url.protocol !== 'https:' || url.username || url.password) fail('INVALID_PROGRESS_EVENT');
    } catch { fail('INVALID_PROGRESS_EVENT'); }
    return Object.freeze({ ...value, repository: Object.freeze({ ...value.repository }) });
  }
  if (value.type === 'GAME_START_STATUS' && exact(value, [...base, 'stage', 'status', 'summary']) &&
      gameStartStages.includes(value.stage) && preparationStatuses.includes(value.status) &&
      safeText(value.summary, 240)) return Object.freeze(value);
  if (value.type === 'BACKLOG_STATUS' &&
      exact(value, [...base, 'backlogId', 'title', 'status', 'summary', 'revision', 'receiptId']) &&
      id(value.backlogId) && safeText(value.title, 160) && backlogStatuses.includes(value.status) &&
      safeText(value.summary, 240) && nullableRevision(value.revision) && nullableId(value.receiptId) &&
      (value.status !== 'SUBMITTED' || (value.revision !== null && value.receiptId !== null))) {
    return Object.freeze(value);
  }
  if (value.type === 'BACKLOG_TESTS' &&
      exact(value, [...base, 'backlogId', 'status', 'required', 'executed', 'passed',
        'failed', 'skipped', 'summary']) && id(value.backlogId) &&
      testStatuses.includes(value.status) && safeText(value.summary, 240) &&
      [value.required, value.executed, value.passed, value.failed, value.skipped].every(count) &&
      value.executed === value.passed + value.failed + value.skipped &&
      (value.status !== 'NOT_RUN' || value.executed === 0) &&
      (value.status !== 'PASSED' || (value.executed > 0 && value.failed === 0 &&
        value.executed >= value.required)) &&
      (value.status !== 'FAILED' || value.failed > 0)) return Object.freeze(value);
  if (value.type === 'GAME_MASTER_API' &&
      exact(value, [...base, 'operation', 'backlogId', 'method', 'origin', 'path', 'phase', 'httpStatus',
        'requestId', 'summary']) && id(value.operation) && nullableId(value.backlogId) &&
      (value.method === null || methods.includes(value.method)) &&
      (value.origin === null || safeOrigin(value.origin)) &&
      (value.path === null || safePath(value.path)) &&
      apiPhases.includes(value.phase) && nullableId(value.requestId) &&
      safeText(value.summary, 240) &&
      (value.httpStatus === null || (Number.isSafeInteger(value.httpStatus) &&
        value.httpStatus >= 100 && value.httpStatus <= 599)) &&
      (value.phase !== 'REQUESTED' || value.httpStatus === null) &&
      (value.phase !== 'SUCCEEDED' || (value.httpStatus >= 200 && value.httpStatus < 300)) &&
      (value.phase !== 'FAILED' || value.httpStatus === null || value.httpStatus >= 400)) {
    return Object.freeze(value);
  }
  if (value.type === 'MODEL_USAGE' &&
      exact(value, [...base, 'model', 'inputTokens', 'cachedInputTokens', 'outputTokens',
        'reasoningTokens', 'summary']) && safeText(value.model, 128) &&
      [value.inputTokens, value.cachedInputTokens, value.outputTokens,
        value.reasoningTokens].every(usageCount) &&
      value.cachedInputTokens <= value.inputTokens && value.reasoningTokens <= value.outputTokens &&
      safeText(value.summary, 240)) return Object.freeze(value);
  if (value.type === 'TOOL_ACTIVITY' &&
      exact(value, [...base, 'toolName', 'operation', 'backlogId', 'phase', 'summary']) &&
      id(value.toolName) && id(value.operation) && nullableId(value.backlogId) &&
      telemetryPhases.includes(value.phase) && safeText(value.summary, 240)) return Object.freeze(value);
  if (value.type === 'NETWORK_ACTIVITY' &&
      exact(value, [...base, 'operation', 'backlogId', 'method', 'origin', 'path', 'phase',
        'httpStatus', 'requestId', 'summary']) && id(value.operation) &&
      nullableId(value.backlogId) && methods.includes(value.method) && safeOrigin(value.origin) &&
      safePath(value.path) && telemetryPhases.includes(value.phase) &&
      (value.httpStatus === null || (Number.isSafeInteger(value.httpStatus) &&
        value.httpStatus >= 100 && value.httpStatus <= 599)) && nullableId(value.requestId) &&
      safeText(value.summary, 240)) return Object.freeze(value);
  if (value.type === 'BLOCKER_OPENED' &&
      exact(value, [...base, 'blockerId', 'backlogId', 'summary']) && id(value.blockerId) &&
      nullableId(value.backlogId) && safeText(value.summary, 240)) return Object.freeze(value);
  if (value.type === 'BLOCKER_RESOLVED' && exact(value, [...base, 'blockerId', 'summary']) &&
      id(value.blockerId) && safeText(value.summary, 240)) return Object.freeze(value);
  if (value.type === 'ORCHESTRATOR_ENDED' && exact(value, [...base, 'status', 'summary']) &&
      ['COMPLETED', 'BLOCKED', 'FAILED'].includes(value.status) && safeText(value.summary, 240)) {
    return Object.freeze(value);
  }
  fail('INVALID_PROGRESS_EVENT');
}

/** Read the latest durable preparation result produced by the active orchestrator. */
export function readEnvironmentReadyProgress(teamSdlcPackWorkspace, matchId, playerSessionId) {
  const workspace = canonicalWorkspace(teamSdlcPackWorkspace);
  if (!id(matchId)) fail('INVALID_PROGRESS_MATCH');
  const path = join(workspace, 'logs', `${matchId}.progress.ndjson`);
  if (!existsSync(path)) return null;
  if (lstatSync(path).isSymbolicLink() || !statSync(path).isFile() || statSync(path).size > 4 * 1024 * 1024) {
    fail('UNSAFE_PROGRESS_LOG');
  }
  let result = null;
  for (const line of readFileSync(path, 'utf8').split(/\r?\n/)) {
    if (!line.trim()) continue;
    if (Buffer.byteLength(line, 'utf8') > 16 * 1024) fail('INVALID_PROGRESS_EVENT');
    let raw;
    try { raw = JSON.parse(line); } catch { continue; }
    if (raw?.type !== 'ENVIRONMENT_READY') continue;
    const event = validateProgressEvent(raw, playerSessionId);
    const candidate = { schemaVersion: 1, status: 'ENVIRONMENT_READY', requestId: event.requestId,
      repository: { ...event.repository }, revision: event.revision, build: event.build,
      matchLogin: event.matchLogin };
    if (result && JSON.stringify(result) !== JSON.stringify(candidate)) fail('ENVIRONMENT_READY_CONFLICT');
    result = Object.freeze(candidate);
  }
  return result;
}

// Team runtime 0.8.4 named lifecycle milestones in prose while its bundled JSON
// schema used the canonical dashboard events. Normalize only those exact bounded
// shapes so existing match logs remain visible without weakening normal validation.
function normalizeLegacyProgressEvent(value) {
  const base = ['schemaVersion', 'playerSessionId', 'type'];
  const status = extra => ({ schemaVersion: value.schemaVersion,
    playerSessionId: value.playerSessionId, type: 'BACKLOG_STATUS',
    backlogId: value.backlogId, title: value.backlogId, summary: value.summary,
    revision: null, receiptId: null, ...extra });
  if (value?.type === 'BACKLOG_STARTED' && exact(value, [...base, 'backlogId', 'summary'])) {
    return [status({ status: 'IN_PROGRESS' })];
  }
  if (value?.type === 'BACKLOG_STATUS' &&
      exact(value, [...base, 'backlogId', 'status', 'summary']) && value.status !== 'SUBMITTED') {
    return [status({ status: value.status })];
  }
  if (value?.type === 'BACKLOG_TESTS' &&
      exact(value, [...base, 'backlogId', 'mandatory', 'executed', 'passed', 'failed',
        'skipped', 'summary'])) {
    const testStatus = value.failed > 0 ? 'FAILED' : value.executed === 0 ? 'NOT_RUN' :
      value.executed < value.mandatory ? 'RUNNING' : 'PASSED';
    return [{ schemaVersion: value.schemaVersion, playerSessionId: value.playerSessionId,
      type: 'BACKLOG_TESTS', backlogId: value.backlogId, status: testStatus,
      required: value.mandatory, executed: value.executed, passed: value.passed,
      failed: value.failed, skipped: value.skipped, summary: value.summary }];
  }
  if (value?.type === 'BACKLOG_BLOCKED' &&
      exact(value, [...base, 'backlogId', 'code', 'summary'])) {
    return [status({ status: 'BLOCKED' }), {
      schemaVersion: value.schemaVersion, playerSessionId: value.playerSessionId,
      type: 'BLOCKER_OPENED', blockerId: value.code, backlogId: value.backlogId,
      summary: value.summary
    }];
  }
  if (value?.type === 'BACKLOG_FAILED' &&
      exact(value, [...base, 'backlogId', 'code', 'summary'])) {
    return [status({ status: 'FAILED' })];
  }
  if (value?.type === 'SUBMISSION_REPORTED' &&
      exact(value, [...base, 'backlogId', 'revision', 'evidenceFiles', 'receiptId',
        'summary']) && nullableRevision(value.revision) && value.revision !== null &&
      id(value.receiptId) && Array.isArray(value.evidenceFiles) &&
      value.evidenceFiles.length <= 32 &&
      value.evidenceFiles.every(item => safeText(item, 512))) {
    return [status({ status: 'SUBMITTED', revision: value.revision,
      receiptId: value.receiptId })];
  }
  if (value?.type === 'GAME_MASTER_API' &&
      exact(value, [...base, 'operation', 'backlogId', 'phase', 'httpStatus',
        'requestId', 'summary'])) {
    return [{ ...value, method: null, origin: null, path: null }];
  }
  return [value];
}

function consoleEntry(value, line, disposition, observedAt) {
  const type = id(value?.type) ? value.type : 'UNKNOWN_EVENT';
  const backlogId = id(value?.backlogId) ? value.backlogId : null;
  const summary = safeText(value?.summary, 240) ? value.summary :
    disposition === 'IGNORED' ? 'Invalid event ignored.' : 'Event recorded.';
  const operation = id(value?.operation) ? value.operation : null;
  const phase = apiPhases.includes(value?.phase) ? value.phase : null;
  const httpStatus = Number.isSafeInteger(value?.httpStatus) ? value.httpStatus : null;
  const requestId = nullableId(value?.requestId) ? value?.requestId ?? null : null;
  return Object.freeze({ line, observedAt, disposition, type, backlogId, operation,
    phase, httpStatus, requestId, summary });
}

const backlogTransitions = Object.freeze({
  // A receipt-backed submission does not require every intermediate report.
  DISCOVERED: ['DISCOVERED', 'IN_PROGRESS', 'SUBMITTED', 'BLOCKED', 'FAILED'],
  IN_PROGRESS: ['IN_PROGRESS', 'COMPLETED', 'SUBMITTED', 'BLOCKED', 'FAILED'],
  COMPLETED: ['COMPLETED', 'IN_PROGRESS', 'SUBMITTED', 'BLOCKED', 'FAILED'],
  BLOCKED: ['BLOCKED', 'IN_PROGRESS', 'SUBMITTED', 'FAILED'],
  FAILED: ['FAILED', 'IN_PROGRESS', 'SUBMITTED'],
  SUBMITTED: ['SUBMITTED']
});

const organizerInterventionIds = new Set([
  'git-auth', 'HOST_APPROVAL_REQUIRED', 'HOST_PERMISSION_DENIED',
  'GIT_AUTHENTICATION_REQUIRED', 'ORGANIZER_INTERVENTION_REQUIRED'
]);

function organizerInterventions(blockers) {
  return blockers.filter(blocker => organizerInterventionIds.has(blocker.id) ||
    /(?:APPROVAL|AUTHENTICATION|ORGANIZER_INTERVENTION)_REQUIRED$/i.test(blocker.id))
    .map(blocker => Object.freeze({ severity: 'HIGH', code: blocker.id,
      backlogId: blocker.backlogId, summary: blocker.summary,
      action: 'Organizer intervention required. Contestant must not operate the machine.' }));
}

export function reduceProgressEvents(events, { playerSessionId, teamId, teamName }) {
  if (!id(playerSessionId) || !id(teamId) || !safeText(teamName, 160)) fail('INVALID_PROGRESS_IDENTITY');
  const preparation = Object.fromEntries(preparationStages.map(stage =>
    [stage, { status: 'PENDING', summary: 'Waiting for team report.' }]));
  const gameStart = Object.fromEntries(gameStartStages.map(stage =>
    [stage, { status: 'PENDING', summary: 'Waiting for game start.' }]));
  const backlogs = new Map();
  const apiByBacklog = new Map();
  const apiActivity = [];
  const toolActivity = [];
  const networkActivity = [];
  const usageByModel = new Map();
  const blockers = new Map();
  let started = false;
  let restartCount = 0;
  let ended = null;
  let currentBacklogId = null;
  const normalizedEvents = events.flatMap(normalizeLegacyProgressEvent);
  for (const raw of normalizedEvents) {
    const event = validateProgressEvent(raw, playerSessionId);
    if (ended && event.type !== 'ORCHESTRATOR_RESTARTED') fail('INVALID_PROGRESS_TRANSITION');
    if (event.type === 'ORCHESTRATOR_STARTED') {
      if (started) fail('INVALID_PROGRESS_TRANSITION');
      started = true;
    } else if (event.type === 'ORCHESTRATOR_RESTARTED') {
      if (!started || event.attempt !== restartCount + 1) fail('INVALID_PROGRESS_TRANSITION');
      restartCount = event.attempt;
      ended = null;
    } else if (!started) {
      fail('INVALID_PROGRESS_TRANSITION');
    } else if (event.type === 'PREPARATION_STATUS') {
      preparation[event.stage] = { status: event.status, summary: event.summary };
    } else if (event.type === 'GAME_START_STATUS') {
      gameStart[event.stage] = { status: event.status, summary: event.summary };
    } else if (event.type === 'BACKLOG_STATUS') {
      const previous = backlogs.get(event.backlogId);
      // Discovery is idempotent and may race a worker starting or submitting work.
      if (previous && event.status === 'DISCOVERED') continue;
      if (previous && !backlogTransitions[previous.status].includes(event.status)) {
        fail('INVALID_PROGRESS_TRANSITION');
      }
      const next = { id: event.backlogId, title: event.title, status: event.status,
        summary: event.summary, revision: event.revision, receiptId: event.receiptId,
        tests: previous?.tests ?? null, api: apiByBacklog.get(event.backlogId) ??
          previous?.api ?? null };
      backlogs.set(event.backlogId, next);
      if (event.status === 'SUBMITTED') {
        for (const [blockerId, blocker] of blockers) {
          if (blocker.backlogId === event.backlogId) blockers.delete(blockerId);
        }
      }
      if (event.status === 'IN_PROGRESS') currentBacklogId = event.backlogId;
      else if (currentBacklogId === event.backlogId) currentBacklogId = null;
    } else if (event.type === 'BACKLOG_TESTS') {
      const backlog = backlogs.get(event.backlogId);
      if (!backlog) fail('INVALID_PROGRESS_TRANSITION');
      backlog.tests = { status: event.status, required: event.required,
        executed: event.executed, passed: event.passed, failed: event.failed,
        skipped: event.skipped, summary: event.summary };
    } else if (event.type === 'GAME_MASTER_API') {
      const activity = { operation: event.operation, backlogId: event.backlogId,
        method: event.method, origin: event.origin, path: event.path,
        phase: event.phase, httpStatus: event.httpStatus, requestId: event.requestId,
        summary: event.summary };
      apiActivity.push(activity);
      if (event.backlogId !== null) {
        const previous = apiByBacklog.get(event.backlogId);
        const summary = { count: (previous?.count ?? 0) + 1,
          lastOperation: event.operation, lastPhase: event.phase,
          httpStatus: event.httpStatus, requestId: event.requestId,
          summary: event.summary };
        apiByBacklog.set(event.backlogId, summary);
        const backlog = backlogs.get(event.backlogId);
        if (backlog) backlog.api = summary;
      }
    } else if (event.type === 'MODEL_USAGE') {
      const previous = usageByModel.get(event.model) ?? { model: event.model,
        source: 'TEAM_REPORTED', inputTokens: 0, cachedInputTokens: 0,
        outputTokens: 0, reasoningTokens: 0, reports: 0 };
      previous.inputTokens += event.inputTokens;
      previous.cachedInputTokens += event.cachedInputTokens;
      previous.outputTokens += event.outputTokens;
      previous.reasoningTokens += event.reasoningTokens;
      previous.reports += 1;
      usageByModel.set(event.model, previous);
    } else if (event.type === 'TOOL_ACTIVITY') {
      toolActivity.push({ source: 'TEAM_REPORTED', toolName: event.toolName,
        operation: event.operation, backlogId: event.backlogId, phase: event.phase,
        summary: event.summary });
    } else if (event.type === 'NETWORK_ACTIVITY') {
      networkActivity.push({ source: 'TEAM_REPORTED', operation: event.operation,
        backlogId: event.backlogId, method: event.method, origin: event.origin,
        path: event.path, phase: event.phase, httpStatus: event.httpStatus,
        requestId: event.requestId, summary: event.summary });
    } else if (event.type === 'BLOCKER_OPENED') {
      if (blockers.has(event.blockerId)) fail('INVALID_PROGRESS_TRANSITION');
      blockers.set(event.blockerId, { id: event.blockerId, backlogId: event.backlogId,
        summary: event.summary });
    } else if (event.type === 'BLOCKER_RESOLVED') {
      if (!blockers.delete(event.blockerId)) fail('INVALID_PROGRESS_TRANSITION');
    } else if (event.type === 'ORCHESTRATOR_ENDED') {
      ended = { status: event.status, summary: event.summary };
    }
  }
  const prepValues = Object.values(preparation);
  const startValues = Object.values(gameStart);
  if (ended?.status === 'COMPLETED' && blockers.size) fail('INVALID_PROGRESS_TRANSITION');
  const phaseFailure = [...prepValues, ...startValues].some(item => item.status === 'FAILED') ? 'FAILED' :
    [...prepValues, ...startValues].some(item => item.status === 'BLOCKED') ? 'BLOCKED' : null;
  const prepared = prepValues.every(item => item.status === 'COMPLETED');
  const gameStarted = startValues.every(item => item.status === 'COMPLETED');
  const startActive = startValues.some(item => item.status !== 'PENDING');
  const overallStatus = ended?.status ?? phaseFailure ?? (blockers.size ? 'BLOCKED' :
    gameStarted ? 'PLAYING' : startActive ? 'STARTING' : prepared ? 'PREPARED' :
      started ? 'PREPARING' : 'WAITING');
  const blockerValues = [...blockers.values()];
  const interventions = organizerInterventions(blockerValues);
  return Object.freeze({ schemaVersion: 1, source: 'TEAM_REPORTED', teamId, teamName,
    playerSessionId, overallStatus, summary: ended?.summary ?? null, currentBacklogId,
    preparation, gameStart, backlogs: [...backlogs.values()], blockers: blockerValues,
    apiActivity: Object.freeze(apiActivity),
    modelUsage: Object.freeze([...usageByModel.values()].map(value => Object.freeze(value))),
    toolActivity: Object.freeze(toolActivity), networkActivity: Object.freeze(networkActivity),
    observability: Object.freeze({ modelUsage: usageByModel.size ? 'TEAM_REPORTED' : 'UNAVAILABLE',
      toolActivity: toolActivity.length ? 'TEAM_REPORTED' : 'UNAVAILABLE',
      networkActivity: networkActivity.length ? 'TEAM_REPORTED' : 'UNAVAILABLE' }),
    organizerInterventionRequired: interventions.length > 0,
    organizerInterventions: Object.freeze(interventions), restartCount,
    eventCount: events.length });
}

function observabilityOnly(event) {
  return ['MODEL_USAGE', 'TOOL_ACTIVITY', 'NETWORK_ACTIVITY'].includes(event.type);
}

function validateHostObservabilityEvent(value, playerSessionId) {
  try {
    const event = validateProgressEvent(value, playerSessionId);
    if (observabilityOnly(event)) return event;
  } catch { /* host-only event types are validated below */ }
  const base = ['schemaVersion', 'playerSessionId', 'type'];
  if (value?.schemaVersion !== 1 || value?.playerSessionId !== playerSessionId) {
    fail('INVALID_HOST_TELEMETRY_EVENT');
  }
  if (value.type === 'AGENT_ACTIVITY' &&
      exact(value, [...base, 'agentId', 'parentAgentId', 'operation', 'phase', 'summary']) &&
      id(value.agentId) && nullableId(value.parentAgentId) && id(value.operation) &&
      telemetryPhases.includes(value.phase) && safeText(value.summary, 240)) return Object.freeze(value);
  if (value.type === 'PROCESS_ACTIVITY' &&
      exact(value, [...base, 'processName', 'operation', 'phase', 'summary']) &&
      safeText(value.processName, 260) && id(value.operation) &&
      telemetryPhases.includes(value.phase) && safeText(value.summary, 240)) return Object.freeze(value);
  if (value.type === 'FILESYSTEM_ACTIVITY' &&
      exact(value, [...base, 'operation', 'path', 'access', 'phase', 'summary']) &&
      id(value.operation) && safeAbsolutePath(value.path) && filesystemAccess.includes(value.access) &&
      telemetryPhases.includes(value.phase) && safeText(value.summary, 240)) return Object.freeze(value);
  if (value.type === 'POLICY_SIGNAL' &&
      exact(value, [...base, 'category', 'operation', 'resource', 'summary']) &&
      ['FILESYSTEM', 'NETWORK', 'GIT', 'PLAYER_INTEGRITY', 'ORGANIZER_ASSET'].includes(value.category) &&
      id(value.operation) && safeText(value.resource, 2048) && safeText(value.summary, 240)) {
    return Object.freeze(value);
  }
  fail('INVALID_HOST_TELEMETRY_EVENT');
}

export function readHostObservabilityLog(path, playerSessionId) {
  if (path === null || path === undefined) return Object.freeze({ events: Object.freeze([]),
    invalidEventCount: 0, available: false });
  if (typeof path !== 'string' || !isAbsolute(path) || !existsSync(path) ||
      lstatSync(path).isSymbolicLink() || !statSync(path).isFile() || statSync(path).size > 1024 * 1024) {
    fail('UNSAFE_HOST_TELEMETRY');
  }
  const content = readFileSync(path, 'utf8');
  const complete = content.endsWith('\n') ? content : content.slice(0, content.lastIndexOf('\n') + 1);
  const events = [];
  let invalidEventCount = 0;
  for (const line of complete.split('\n')) {
    if (!line) continue;
    try {
      if (Buffer.byteLength(line, 'utf8') > 4096) throw new Error('INVALID');
      const event = validateHostObservabilityEvent(JSON.parse(line), playerSessionId);
      events.push(event);
    } catch { invalidEventCount += 1; }
  }
  return Object.freeze({ events: Object.freeze(events), invalidEventCount, available: true });
}

function aggregateUsage(events, source) {
  const aggregate = new Map();
  for (const event of events.filter(item => item.type === 'MODEL_USAGE')) {
    const previous = aggregate.get(event.model) ?? { model: event.model, source,
      inputTokens: 0, cachedInputTokens: 0, outputTokens: 0, reasoningTokens: 0, reports: 0 };
    previous.inputTokens += event.inputTokens;
    previous.cachedInputTokens += event.cachedInputTokens;
    previous.outputTokens += event.outputTokens;
    previous.reasoningTokens += event.reasoningTokens;
    previous.reports += 1;
    aggregate.set(event.model, previous);
  }
  return [...aggregate.values()];
}

function priceUsage(items, priceCard) {
  const card = validatePriceCard(priceCard ?? emptyPriceCard);
  return { priceCard: Object.freeze({ schemaVersion: card.schemaVersion, id: card.id,
    currency: card.currency, unitTokens: card.unitTokens }),
  items: Object.freeze(items.map(item => {
    const rate = card.models[item.model];
    // Provider reasoning tokens are normally a subset of output tokens and are shown
    // separately without billing them twice.
    const nominalCredit = rate ? ((item.inputTokens - item.cachedInputTokens) * rate.input +
      item.cachedInputTokens * rate.cachedInput + item.outputTokens * rate.output) /
      card.unitTokens : null;
    return Object.freeze({ ...item, nominalCredit,
      pricingStatus: rate ? 'CALCULATED' : 'RATE_UNAVAILABLE' });
  })) };
}

function validateAuditPolicy(value) {
  if (value === null || value === undefined) return null;
  if (!exact(value, ['schemaVersion', 'mode', 'teamWorkspace', 'allowedOrigins']) ||
      value.schemaVersion !== 1 || value.mode !== 'TASK_PERMISSIONS_OBSERVE_ONLY' ||
      !safeAbsolutePath(value.teamWorkspace) || !Array.isArray(value.allowedOrigins) ||
      value.allowedOrigins.length === 0 || value.allowedOrigins.length > 16 ||
      value.allowedOrigins.some(origin => !safeOrigin(origin)) ||
      new Set(value.allowedOrigins).size !== value.allowedOrigins.length) fail('INVALID_AUDIT_POLICY');
  return Object.freeze({ ...value, teamWorkspace: resolve(value.teamWorkspace),
    allowedOrigins: Object.freeze([...value.allowedOrigins]) });
}

function within(root, candidate) {
  const base = resolve(root).toLowerCase();
  const path = resolve(candidate).toLowerCase();
  return path === base || path.startsWith(`${base}${process.platform === 'win32' ? '\\' : '/'}`);
}

function suspectedViolations(snapshot, host, policy) {
  const audit = validateAuditPolicy(policy);
  if (!audit) return Object.freeze([]);
  const results = [];
  const add = (code, source, operation, resource, summary) => results.push(Object.freeze({
    id: `suspected-${results.length + 1}`, status: 'SUSPECTED', code, source,
    operation, resource, summary
  }));
  const network = host.events.some(event => event.type === 'NETWORK_ACTIVITY') ?
    host.events.filter(event => event.type === 'NETWORK_ACTIVITY').map(event => ({ ...event,
      source: 'HOST_OBSERVED' })) : [...snapshot.apiActivity, ...snapshot.networkActivity];
  for (const event of network) {
    if (event.origin && !audit.allowedOrigins.includes(event.origin)) {
      add('UNEXPECTED_NETWORK_ORIGIN', event.source ?? 'TEAM_REPORTED', event.operation,
        `${event.origin}${event.path ?? ''}`, 'Observed network activity outside the declared match origins.');
    }
  }
  const tools = host.events.some(event => event.type === 'TOOL_ACTIVITY') ?
    host.events.filter(event => event.type === 'TOOL_ACTIVITY').map(event => ({ ...event,
      source: 'HOST_OBSERVED' })) : snapshot.toolActivity;
  for (const event of tools) {
    if (/(?:FORCE[_-]?PUSH|REMOTE[_-]?(?:SET|ADD|REMOVE)|CREDENTIAL[_-]?(?:READ|EXPORT))/i.test(event.operation)) {
      add('RISKY_GIT_OR_CREDENTIAL_OPERATION', event.source ?? 'TEAM_REPORTED', event.operation,
        event.toolName ?? 'tool', 'Observed an operation that requires organizer review after the match.');
    }
  }
  for (const event of host.events.filter(item => item.type === 'FILESYSTEM_ACTIVITY')) {
    if (!within(audit.teamWorkspace, event.path)) {
      add('OUTSIDE_TEAM_WORKSPACE_ACCESS', 'HOST_OBSERVED', event.operation, event.path,
        'Observed filesystem activity outside the registered team workspace.');
    } else if (event.access !== 'READ' && /[\\/](?:\.mtm|team-sdlc-pack[\\/]runtime)(?:[\\/]|$)/i.test(event.path)) {
      add('TEAM_RUNTIME_INTEGRITY_ACCESS', 'HOST_OBSERVED', event.operation, event.path,
        'Observed a write-like operation against installed runtime or registration files.');
    }
  }
  for (const event of host.events.filter(item => item.type === 'POLICY_SIGNAL')) {
    add(`HOST_${event.category}_SIGNAL`, 'HOST_OBSERVED', event.operation, event.resource,
      event.summary);
  }
  if (host.available && host.invalidEventCount > 0) {
    add('HOST_TELEMETRY_GAP', 'HOST_OBSERVED', 'INVALID_RECORDS',
      `${host.invalidEventCount} record(s)`, 'Some organizer telemetry records were invalid and excluded.');
  }
  return Object.freeze(results);
}

export function applyObservedTelemetry(snapshot, host, priceCard = emptyPriceCard, auditPolicy = null) {
  const hostEvents = host?.events ?? [];
  const hostUsage = aggregateUsage(hostEvents, 'HOST_OBSERVED');
  const hostTools = hostEvents.filter(event => event.type === 'TOOL_ACTIVITY').map(event => ({
    source: 'HOST_OBSERVED', toolName: event.toolName, operation: event.operation,
    backlogId: event.backlogId, phase: event.phase, summary: event.summary }));
  const hostNetwork = hostEvents.filter(event => event.type === 'NETWORK_ACTIVITY').map(event => ({
    source: 'HOST_OBSERVED', operation: event.operation, backlogId: event.backlogId,
    method: event.method, origin: event.origin, path: event.path, phase: event.phase,
    httpStatus: event.httpStatus, requestId: event.requestId, summary: event.summary }));
  const usage = hostUsage.length ? hostUsage : snapshot.modelUsage;
  const priced = priceUsage(usage, priceCard);
  const agentActivity = hostEvents.filter(event => event.type === 'AGENT_ACTIVITY').map(event =>
    Object.freeze({ source: 'HOST_OBSERVED', agentId: event.agentId,
      parentAgentId: event.parentAgentId, operation: event.operation, phase: event.phase,
      summary: event.summary }));
  const processActivity = hostEvents.filter(event => event.type === 'PROCESS_ACTIVITY').map(event =>
    Object.freeze({ source: 'HOST_OBSERVED', processName: event.processName,
      operation: event.operation, phase: event.phase, summary: event.summary }));
  const filesystemActivity = hostEvents.filter(event => event.type === 'FILESYSTEM_ACTIVITY').map(event =>
    Object.freeze({ source: 'HOST_OBSERVED', path: event.path, access: event.access,
      operation: event.operation, phase: event.phase, summary: event.summary }));
  const violations = suspectedViolations(snapshot, host, auditPolicy);
  return Object.freeze({ ...snapshot, modelUsage: priced.items,
    toolActivity: Object.freeze(hostTools.length ? hostTools : snapshot.toolActivity),
    networkActivity: Object.freeze(hostNetwork.length ? hostNetwork : snapshot.networkActivity),
    agentActivity: Object.freeze(agentActivity), processActivity: Object.freeze(processActivity),
    filesystemActivity: Object.freeze(filesystemActivity),
    suspectedViolations: violations, suspectedViolationCount: violations.length,
    observability: Object.freeze({
      modelUsage: hostUsage.length ? 'HOST_OBSERVED' : snapshot.observability.modelUsage,
      toolActivity: hostTools.length ? 'HOST_OBSERVED' : snapshot.observability.toolActivity,
      networkActivity: hostNetwork.length ? 'HOST_OBSERVED' : snapshot.observability.networkActivity,
      hostTelemetry: host?.available ? 'CONNECTED' : 'UNAVAILABLE',
      invalidHostEventCount: host?.invalidEventCount ?? 0
    }), priceCard: priced.priceCard });
}

// Keep timing in Player memory so the team root never emits timer heartbeats or treats
// a local clock as official Game Master time.
export function addObservedWorkTime(snapshot, timingState, nowMs = Date.now()) {
  if (!(timingState instanceof Map) || !Number.isFinite(nowMs)) fail('INVALID_PROGRESS_TIMING');
  const backlogs = snapshot.backlogs.map(backlog => {
    let timing = timingState.get(backlog.id);
    if (!timing) {
      timing = { accumulatedMs: 0, activeSinceMs: null, lastStatus: null };
      timingState.set(backlog.id, timing);
    }
    if (backlog.status === 'IN_PROGRESS' && timing.lastStatus !== 'IN_PROGRESS') {
      timing.activeSinceMs = nowMs;
    } else if (backlog.status !== 'IN_PROGRESS' && timing.lastStatus === 'IN_PROGRESS' &&
        timing.activeSinceMs !== null) {
      timing.accumulatedMs += Math.max(0, nowMs - timing.activeSinceMs);
      timing.activeSinceMs = null;
    }
    timing.lastStatus = backlog.status;
    const activeMs = timing.activeSinceMs === null ? 0 : Math.max(0, nowMs - timing.activeSinceMs);
    return { ...backlog, observedActiveSeconds: Math.floor((timing.accumulatedMs + activeMs) / 1000),
      timingStatus: timing.activeSinceMs === null ? 'PAUSED' : 'RUNNING' };
  });
  return Object.freeze({ ...snapshot, timingSource: 'PLAYER_OBSERVED', backlogs });
}

export function readProgressLog(path, context, { observedAtByLine = null,
  now = () => new Date().toISOString(), hostTelemetryFile = null,
  priceCard = emptyPriceCard, auditPolicy = null } = {}) {
  if (!existsSync(path)) return applyObservedTelemetry(applyPlayerPreparationComplete(Object.freeze({
    ...reduceProgressEvents([], context), normalizedEventCount: 0, invalidEventCount: 0,
    eventConsole: Object.freeze([]) }), readPlayerPreparationComplete(context)), readHostObservabilityLog(hostTelemetryFile,
    context.playerSessionId), priceCard, auditPolicy);
  const expected = progressLogPath(context.teamSdlcPackWorkspace, context.matchId);
  if (resolve(path) !== resolve(expected)) fail('UNSAFE_PROGRESS_LOG');
  const directory = resolve(path, '..');
  if (!existsSync(directory) || lstatSync(directory).isSymbolicLink() ||
      !statSync(directory).isDirectory() || lstatSync(path).isSymbolicLink() ||
      !statSync(path).isFile()) fail('UNSAFE_PROGRESS_LOG');
  const size = statSync(path).size;
  if (size > 256 * 1024) fail('PROGRESS_LOG_TOO_LARGE');
  const content = readFileSync(path, 'utf8');
  // A concurrent append can expose a partial final line. Ignore only that line.
  // Completed team-authored events are isolated so one invalid report cannot hide
  // the rest of the observable match progress.
  const complete = content.endsWith('\n') ? content : content.slice(0, content.lastIndexOf('\n') + 1);
  const events = [];
  const eventConsole = [];
  let rawEventCount = 0;
  let normalizedEventCount = 0;
  let invalidEventCount = 0;
  const observed = lineNumber => {
    if (!(observedAtByLine instanceof Map)) return null;
    if (!observedAtByLine.has(lineNumber)) observedAtByLine.set(lineNumber, now());
    return observedAtByLine.get(lineNumber);
  };
  let lineNumber = 0;
  for (const line of complete.split('\n')) {
    lineNumber += 1;
    if (!line) continue;
    const observedAt = observed(lineNumber);
    if (Buffer.byteLength(line, 'utf8') > 4096) {
      invalidEventCount += 1;
      eventConsole.push(consoleEntry(null, lineNumber, 'IGNORED', observedAt));
      continue;
    }
    let raw;
    try { raw = JSON.parse(line); } catch {
      invalidEventCount += 1;
      eventConsole.push(consoleEntry(null, lineNumber, 'IGNORED', observedAt));
      continue;
    }
    const normalized = normalizeLegacyProgressEvent(raw);
    const disposition = normalized.length !== 1 || normalized[0] !== raw ? 'NORMALIZED' : 'ACCEPTED';
    try {
      // Replaying accepted events with the candidate keeps transition validation
      // strict while allowing one bad team-authored line to be ignored in isolation.
      reduceProgressEvents([...events, ...normalized], context);
      events.push(...normalized);
      rawEventCount += 1;
      if (disposition === 'NORMALIZED') normalizedEventCount += 1;
      eventConsole.push(consoleEntry(raw, lineNumber, disposition, observedAt));
    } catch (error) {
      if (!(error instanceof ProgressError) ||
          !['INVALID_PROGRESS_EVENT', 'INVALID_PROGRESS_TRANSITION'].includes(error.code)) throw error;
      invalidEventCount += 1;
      eventConsole.push(consoleEntry(raw, lineNumber, 'IGNORED', observedAt));
    }
  }
  const snapshot = applyPlayerPreparationComplete(Object.freeze({
    ...reduceProgressEvents(events, context), eventCount: rawEventCount,
    normalizedEventCount, invalidEventCount, eventConsole: Object.freeze(eventConsole) }),
  readPlayerPreparationComplete(context));
  return applyObservedTelemetry(snapshot,
  readHostObservabilityLog(hostTelemetryFile, context.playerSessionId), priceCard, auditPolicy);
}

function validateLiveTeamInfo(value, context) {
  const privateText = (candidate, max) => typeof candidate === 'string' &&
    candidate.length > 0 && candidate.length <= max && !/[\r\n\0]/.test(candidate);
  if (!exact(value, ['schemaVersion', 'playerSessionId', 'requestId', 'team', 'apiKey',
    'gameMasterUrl', 'matchPageUrl', 'matchId', 'repository', 'preparationComplete']) ||
      value.schemaVersion !== 1 || value.playerSessionId !== context.playerSessionId ||
      value.matchId !== context.matchId || !id(value.requestId) ||
      !exact(value.team, ['uuid', 'id', 'name', 'username', 'password']) ||
      value.team.id !== context.teamId || value.team.name !== context.teamName ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value.team.uuid) ||
      !privateText(value.team.name, 1024) || !privateText(value.team.username, 1024) ||
      !privateText(value.team.password, 16_384) || !exact(value.apiKey, ['secret', 'scopes']) ||
      !privateText(value.apiKey.secret, 16_384) || !Array.isArray(value.apiKey.scopes) ||
      value.apiKey.scopes.length === 0 || value.apiKey.scopes.length > 32 ||
      value.apiKey.scopes.some(scope => !safeText(scope, 128)) ||
      !exact(value.repository, ['url', 'branch', 'folder']) ||
      !exact(value.preparationComplete, ['method', 'url', 'authorization']) ||
      value.preparationComplete.method !== 'POST' ||
      value.preparationComplete.authorization !== `Bearer ${value.apiKey.secret}` ||
      ![value.gameMasterUrl, value.matchPageUrl, value.repository.url,
        value.preparationComplete.url].every(url => safeText(url, 4096)) ||
      Buffer.byteLength(JSON.stringify(value)) > 64 * 1024) fail('INVALID_LIVE_TEAM_INFO');
  return Object.freeze(structuredClone(value));
}

export function applyCliUsage(snapshot, cli, priceCard = emptyPriceCard) {
  if (!cli?.tokenUsage) return snapshot;
  const valid = value => ['inputTokens','cachedInputTokens','outputTokens','reasoningTokens'].every(key =>
    Number.isSafeInteger(value?.[key]) && value[key] >= 0) && value.cachedInputTokens <= value.inputTokens;
  const rows = Array.isArray(cli.modelUsage) && cli.modelUsage.length ? cli.modelUsage :
    [{ model: 'Unknown', ...cli.tokenUsage }];
  if (!rows.every(valid)) return snapshot;
  const priced = priceUsage(rows.map(row => ({ ...row, source: 'CLI_OBSERVED' })), priceCard);
  return { ...snapshot, modelUsage: priced.items, priceCard: priced.priceCard,
    observability: { ...snapshot.observability, modelUsage: 'CLI_OBSERVED' } };
}

export function applyCliActivity(snapshot, cli) {
  if (!cli) return snapshot;
  const records = cli.activityRecords ?? [];
  const select = category => records.filter(row => row.category === category);
  return { ...snapshot,
    agentActivity: [...snapshot.agentActivity, ...select('agent')],
    processActivity: [...snapshot.processActivity, { source: 'PLAYER_OBSERVED', processName: cli.provider + ' CLI',
      operation: 'LIFECYCLE', phase: cli.status, summary: cli.pid ? 'PID ' + cli.pid : 'No active PID reported' }, ...select('process')],
    toolActivity: [...snapshot.toolActivity, ...select('tool')],
    filesystemActivity: [...snapshot.filesystemActivity, ...select('filesystem')],
    networkActivity: [...snapshot.networkActivity, ...select('network')],
    monitoring: { agent: 'CLI lifecycle events', process: 'Owned CLI and reported commands only',
      tool: 'CLI tool events only', filesystem: 'Reported file changes only; reads unmonitored',
      network: snapshot.networkActivity.length || select('network').length || snapshot.apiActivity?.length ?
        'Reported HTTP events only; not all network traffic' : 'Monitoring unavailable — no HTTP telemetry received' } };
}

function localApplicationSnapshot(provider) {
  if (provider === null) return null;
  try {
    const value = provider();
    const label = input => typeof input === 'string' && /^[A-Z][A-Z_]{0,63}$/.test(input) ? input : 'UNKNOWN';
    if (!value || typeof value !== 'object') throw Error('Invalid state');
    return { phase: label(value.phase), officialStatus: label(value.officialStatus),
      roundId: /^round\d{2,}$/.test(value.roundId ?? '') ? value.roundId : null,
      hostConnection: label(value.hostConnection), agentState: label(value.agentState), ackState: label(value.ackState),
      cli: value.cli && typeof value.cli === 'object' ? {
        provider: ['codex','claude'].includes(value.cli.provider) ? value.cli.provider : null,
        status: label(value.cli.status),
        sessionId: typeof value.cli.sessionId === 'string' && /^[A-Za-z0-9_.:-]{1,128}$/.test(value.cli.sessionId) ? value.cli.sessionId : null,
        pid: Number.isSafeInteger(value.cli.pid) && value.cli.pid > 0 ? value.cli.pid : null,
        tokenUsage: value.cli.tokenUsage ?? null, modelUsage: value.cli.modelUsage ?? [],
        activity: value.cli.activity ?? null, activityRecords: value.cli.activityRecords ?? [],
        subagents: value.cli.subagents ?? [], approvals: value.cli.approvals ?? []
      } : null,
      diagnostics: value.diagnostics && typeof value.diagnostics === 'object' ? {
        status: ['VERIFIED','TAMPERED_OR_CORRUPT','WRITE_FAILED'].includes(value.diagnostics.status) ?
          value.diagnostics.status : 'UNKNOWN',
        head: typeof value.diagnostics.head === 'string' && /^[a-f0-9]{64}$/.test(value.diagnostics.head) ?
          value.diagnostics.head : null
      } : null,
      pollHealth: { status: label(value.pollHealth?.status),
        observedAt: typeof value.pollHealth?.observedAt === 'string' &&
          /^\d{4}-\d{2}-\d{2}T[\d:.]+Z$/.test(value.pollHealth.observedAt) ? value.pollHealth.observedAt.slice(0,32) : null },
      operations: (Array.isArray(value.operations) ? value.operations : []).slice(-100).map(op => ({
        id: id(op?.id) && safeText(op.id,128) ? op.id : 'UNKNOWN', kind: label(op?.kind), state: label(op?.state) })) };
  } catch { return { phase: 'UNAVAILABLE', officialStatus: 'UNKNOWN', hostConnection: 'UNKNOWN',
    agentState: 'UNKNOWN', ackState: 'UNKNOWN', pollHealth: { status: 'UNAVAILABLE', observedAt: null }, operations: [] }; }
}

export function dashboardDocument(initialSnapshot = null, liveTeamInfoAvailable = false, managementPath = null) {
const serializedSnapshot = initialSnapshot === null ? 'null' :
  JSON.stringify(initialSnapshot).replaceAll('<', '\\u003c').replaceAll('>', '\\u003e');
return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MTM PLAYER Progress</title><style>
:root{color-scheme:light dark;--bg:#eef3f8;--card:#fff;--ink:#0d1b2a;--muted:#607086;--line:#d8e1ea;--blue:#0b4f8a;--green:#147d54;--amber:#a45b00;--red:#b42318}
@media(prefers-color-scheme:dark){:root{--bg:#101820;--card:#17232f;--ink:#f4f7fb;--muted:#a8b6c6;--line:#324252;--blue:#69b7ff;--green:#62d5a4;--amber:#ffbd66;--red:#ff8a80}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,sans-serif}.wrap{max-width:1100px;margin:auto;padding:28px}.manage-link{display:inline-block;margin-bottom:12px;color:var(--blue);font-weight:700;text-decoration:none}.manage-link:hover{text-decoration:underline}.top{display:flex;justify-content:space-between;gap:20px;align-items:end;margin-bottom:20px}h1{font-size:28px;margin:0}.sub,.meta{color:var(--muted)}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px;box-shadow:0 8px 24px #0000000b}.wide{grid-column:1/-1}.critical{border:3px solid var(--red);background:color-mix(in srgb,var(--red) 9%,var(--card));box-shadow:0 0 0 4px color-mix(in srgb,var(--red) 14%,transparent)}.critical h2{color:var(--red);font-size:20px}.critical .row{border-color:color-mix(in srgb,var(--red) 30%,var(--line))}h2{font-size:17px;margin:0 0 14px}.steps,.blockers{display:grid;gap:9px}.row{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:10px 0;border-top:1px solid var(--line)}.row:first-child{border-top:0}.badge{white-space:nowrap;border-radius:999px;padding:3px 9px;background:var(--bg);font-size:12px;font-weight:700}.COMPLETED,.SUBMITTED,.RUNNING,.PREPARED,.PLAYING,.ACCEPTED{color:var(--green)}.IN_PROGRESS,.PREPARING,.STARTING,.NORMALIZED{color:var(--blue)}.BLOCKED,.PENDING,.WAITING{color:var(--amber)}.FAILED,.IGNORED,.HIGH{color:var(--red)}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:10px;border-top:1px solid var(--line);vertical-align:top}th{border-top:0;color:var(--muted);font-size:12px;text-transform:uppercase}.status-cell{white-space:nowrap}.status-cell .badge{display:inline-flex;align-items:center;line-height:1.2;padding:3px 8px}.empty{color:var(--muted);padding:8px 0}.notice{border-left:4px solid var(--amber);padding-left:12px}.error{color:var(--red)}.activity-scroll{max-height:320px;overflow:auto;overscroll-behavior:contain}.activity-scroll thead{position:sticky;top:0;background:var(--card);z-index:1}.console{background:#08111b;color:#cbd5e1;border-radius:10px;padding:14px;min-height:120px;max-height:320px;overflow:auto;white-space:pre-wrap;font:12px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace}@media(max-width:760px){.grid{grid-template-columns:1fr}.top{display:block}table,thead,tbody,tr,th,td{display:block}thead{display:none}td{border:0;padding:4px 0}tr{display:block;border-top:1px solid var(--line);padding:10px 0}}
.PAUSED,.DRAFT,.READY,.UNKNOWN{color:var(--amber)}.FINISHED{color:var(--green)}.CANCELLED{color:var(--red)}button{border:0;border-radius:9px;background:var(--blue);color:#fff;padding:8px 12px;font-weight:700;cursor:pointer}button:disabled{opacity:.55;cursor:not-allowed}.team-info{margin-top:12px}.suspected{border:2px solid var(--amber);background:color-mix(in srgb,var(--amber) 7%,var(--card))}.suspected h2{color:var(--amber)}
.card{min-width:0}.card>summary{cursor:pointer;font-weight:700;font-size:17px}.card[open]>summary{margin-bottom:14px}.card>summary:focus-visible{outline:2px solid var(--blue);outline-offset:5px}.panel-meta{font-size:12px;font-weight:400;color:var(--muted);margin-left:8px}.overview{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.overview-card{--status-color:var(--muted);min-width:0;padding:16px;border:1px solid color-mix(in srgb,var(--status-color) 40%,var(--line));border-left:4px solid var(--status-color);border-radius:12px;background:color-mix(in srgb,var(--status-color) 10%,var(--card));display:flex;flex-direction:column;align-items:flex-start;gap:10px}.overview-card.CLEAR,.overview-card.COMPLETED{--status-color:var(--green)}.overview-card.BLOCKED,.overview-card.FAILED{--status-color:var(--red)}.overview-card.WAITING{--status-color:var(--amber)}.overview-card.IN_PROGRESS,.overview-card.PREPARING{--status-color:var(--blue)}.overview-card strong{color:var(--ink)}.overview-card .meta{width:100%;overflow-wrap:anywhere}.overview-card .badge{margin-top:auto;max-width:100%;white-space:normal;overflow-wrap:anywhere}.overview-card.BLOCKED .badge{color:var(--red)}@media(max-width:820px){.overview{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:520px){.overview{grid-template-columns:minmax(0,1fr)}.wrap{padding:14px}.card{padding:14px}}.intervention{padding:14px;border-radius:10px;margin-bottom:12px}.CLEAR{color:var(--green)}.record-table{font:12px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace}.record-table th,.record-table td{padding:6px 8px}.compact-value{white-space:nowrap}.copy-id{padding:0;background:transparent;color:var(--ink);font:inherit;text-align:left;border-radius:3px}.activity-scroll{min-width:0}.record-table{display:table}.record-table thead{display:table-header-group}.record-table tbody{display:table-row-group}.record-table tr{display:table-row}.record-table th,.record-table td{display:table-cell}.overview .meta{overflow-wrap:anywhere}[hidden]{display:none!important}
.card.preparation-completed{border-color:color-mix(in srgb,var(--green) 45%,var(--line));background:color-mix(in srgb,var(--green) 10%,var(--card))}.card.preparation-completed>summary{color:var(--green)}
.backlog-table{display:table;min-width:760px;font-size:12px;line-height:1.5}.backlog-table thead{display:table-header-group}.backlog-table tbody{display:table-row-group}.backlog-table tr{display:table-row}.backlog-table th,.backlog-table td{display:table-cell;padding:8px}.backlog-table th:first-child,.backlog-table td:first-child{width:1%;min-width:100px;white-space:nowrap}.backlog-table th:nth-child(6),.backlog-table td:nth-child(6){width:1%;min-width:125px;white-space:nowrap}.backlog-table td:first-child{font-size:14px;font-weight:600}.backlog-table .status-cell .badge{white-space:nowrap}
.backlog-row{--row-color:var(--muted);background:color-mix(in srgb,var(--row-color) 10%,var(--card));color:var(--ink)}.backlog-row.IN_PROGRESS{--row-color:var(--blue)}.backlog-row.DISCOVERED,.backlog-row.BLOCKED,.backlog-row.PENDING,.backlog-row.WAITING{--row-color:var(--amber)}.backlog-row.SUBMITTED,.backlog-row.COMPLETED,.backlog-row.ACCEPTED{--row-color:var(--green)}.backlog-row.FAILED{--row-color:var(--red)}.backlog-row td:first-child{border-left:3px solid var(--row-color)}
.backlog-row.UNFINISHED{--row-color:var(--red)}.badge.UNFINISHED{color:#ff9aa7;background:#3e1d29}.backlog-last-status{display:block;margin-top:5px;color:var(--muted);font-size:10px;white-space:nowrap}
.overview-card.UNFINISHED{--status-color:var(--red)}.overview-card.UNFINISHED .badge{color:#ff9aa7;background:#3e1d29}
.card.records-alert{border-color:color-mix(in srgb,var(--red) 45%,var(--line));background:color-mix(in srgb,var(--red) 10%,var(--card))}.card.records-alert>summary{color:var(--red)}
@keyframes backlog-status-pulse{0%,100%{opacity:1}50%{opacity:.55}}.backlog-table .backlog-row.IN_PROGRESS td:first-child,.backlog-table .status-cell .badge.IN_PROGRESS{animation:backlog-status-pulse 1.8s ease-in-out infinite}@media(prefers-reduced-motion:reduce){.backlog-table .backlog-row.IN_PROGRESS td:first-child,.backlog-table .status-cell .badge.IN_PROGRESS{animation:none;outline:1px solid currentColor}}
.record-table tr.model-not-allowed{background:color-mix(in srgb,var(--red) 12%,var(--card));color:var(--red)}
.transcript-controls{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:10px}.transcript-controls select{max-width:100%;padding:7px}.transcript-entry{padding:8px 0;border-bottom:1px solid #263849;white-space:pre-wrap;overflow-wrap:anywhere}.transcript-entry:last-child{border-bottom:0}.transcript-role{color:#70b7ff;font-weight:700}.transcript-time{color:#8ca0b5;margin-right:8px}
.transcript-entry{white-space:normal}.transcript-entry pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:260px;overflow:auto}.transcript-entry p{margin:6px 0}.transcript-entry summary{cursor:pointer}
.chat-history{min-height:180px;margin-bottom:12px}.chat-form{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;align-items:end}.chat-form textarea{width:100%;min-height:72px;max-height:180px;resize:vertical;border:1px solid var(--line);border-radius:9px;background:var(--bg);color:var(--ink);padding:10px;font:inherit}.chat-form textarea:focus{outline:2px solid var(--blue);outline-offset:1px}.chat-status{min-height:22px;margin:8px 0 0}.chat-message-user .transcript-role{color:#8cecff}.chat-message-assistant .transcript-role{color:#80edb6}.chat-pending{margin:9px 0;padding:10px 12px;border:1px dashed #568095;border-radius:9px;background:#102432;color:var(--ink)}.chat-pending-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:6px}.chat-pending-author{color:#8cecff;font-weight:700}.chat-pending-badge{color:#ffd166;font-size:.78rem;font-weight:800;letter-spacing:.04em;text-transform:uppercase}.chat-pending-badge::before{content:'●';display:inline-block;margin-right:6px;animation:chat-pending-pulse 1.2s ease-in-out infinite}.chat-pending-text{white-space:pre-wrap;overflow-wrap:anywhere}.chat-pending-time{color:var(--muted);font-size:.78rem;margin-left:8px}@keyframes chat-pending-pulse{50%{opacity:.25}}@media(prefers-reduced-motion:reduce){.chat-pending-badge::before{animation:none}}@media(max-width:620px){.chat-form{grid-template-columns:1fr}.chat-form button{justify-self:end}}
${brandStyles}
.view-tabs{display:flex;gap:4px;overflow-x:auto;border-bottom:1px solid #315467;margin:0 0 16px;padding:0;scrollbar-width:thin}.view-tabs [role=tab]{flex:0 0 auto;border-radius:7px 7px 0 0;border:1px solid transparent;border-bottom:3px solid transparent;background:transparent;color:#95aabe;padding:10px 16px;font:600 13px/1.4 system-ui,sans-serif;white-space:nowrap}.view-tabs [role=tab]:hover{background:#102733;color:#e8f1fa}.view-tabs [role=tab][aria-selected=true]{background:#153b49;color:#8cecff;border-color:#315467;border-bottom-color:#48d9ee}.view-tabs [role=tab]:focus-visible{outline:2px solid #8cecff;outline-offset:-4px}
.round-brand-status{flex:0 0 auto;display:flex;flex-direction:column;align-items:flex-start;gap:6px}.round-brand-status .live-indicator{margin-left:0}
.live-indicator{display:inline-flex;align-items:center;gap:6px;width:118px;min-width:118px;height:22px;white-space:nowrap;vertical-align:middle;font-size:11px;color:var(--muted);margin-left:8px}.live-indicator::before{content:"";width:6px;height:6px;flex:0 0 6px;border-radius:50%;background:currentColor}.live-indicator.live{color:var(--green)}.live-indicator.updating{color:var(--blue)}.live-indicator.reconnecting{color:var(--amber)}.live-indicator.snapshot{color:var(--muted)}
</style></head><body><main class="wrap"><div class="page-brand-heading"><div class="round-brand-status">${brandHeader}<span id="refresh" class="live-indicator" role="status">Connecting…</span></div><div class="page-heading"><h1>MTM PLAYER</h1><div id="team" class="sub">Waiting for data…</div></div></div><div class="top">${managementPath ? `<a class="manage-link" href="${managementPath}">← Back to management</a>` : '<span></span>'}<div><span id="match-state" class="badge WAITING">MATCH UNKNOWN</span> <span id="orchestrator-state" class="badge">ORCHESTRATOR: UNKNOWN</span> </div></div>
<p class="notice">Progress is reported by team-orchestrator. Game Master is the official source for results and time. Work duration below is observed by Player and resets when the dashboard restarts.</p>
<div class="grid"><details id="panel-overview" class="card wide"  open><summary>Match overview <span id="summary-overview" class="panel-meta"></span></summary><div id="match-overview" class="overview"></div></details>
<section id="approval-controls" class="card wide"></section>
<details id="panel-playing" class="card wide"  open><summary>Backlog Status <span id="summary-playing" class="panel-meta"></span></summary><div id="backlogs" class="activity-scroll"></div></details>
<details id="panel-preparation" class="card wide"  open><summary>Preparation <span id="summary-preparation" class="panel-meta"></span></summary><div id="preparation" class="steps"></div></details>
<details id="panel-local-player-application" class="card wide" ><summary>Runtime diagnostics <span id="summary-local-player-application" class="panel-meta"></span></summary><div id="local-application" hidden><div id="local-application-state" class="activity-scroll"></div><div id="local-application-operations" class="activity-scroll"></div></div></details>
<details id="panel-codex-task-observation" class="card wide" ><summary>Codex task observation <span id="summary-codex-task-observation" class="panel-meta"></span></summary><div id="codex-status" class="meta"></div><div id="codex-tasks" class="activity-scroll"></div><div id="codex-usage" class="activity-scroll"></div><div id="codex-activity" class="activity-scroll"></div></details>
<details id="panel-live-transcript" class="card wide" ><summary>Orchestrator activity <span id="summary-live-transcript" class="panel-meta"></span></summary><div id="agent-controls"></div><div id="orchestrator-current" class="meta"></div><label class="meta"><input id="transcript-follow" type="checkbox" checked> Auto-scroll</label><div class="transcript-controls"><select id="transcript-task"></select><span id="transcript-status" class="meta"></span></div><div id="live-transcript" class="console"></div></details>
<details id="panel-agent-amp-process-activity" class="card wide" ><summary>Agent &amp; process activity <span id="summary-agent-amp-process-activity" class="panel-meta"></span></summary><div id="agent-process-activity" class="activity-scroll"></div></details>
<details id="panel-network-amp-api-activity" class="card wide" ><summary>Network &amp; API activity <span id="summary-network-amp-api-activity" class="panel-meta"></span></summary><div id="network-activity" class="activity-scroll"></div></details>
<details id="panel-model-usage-amp-nominal-credit" class="card wide" ><summary>Model usage &amp; estimated credits <span id="summary-model-usage-amp-nominal-credit" class="panel-meta"></span></summary><div id="allowed-models" class="meta"></div><div id="usage-meta" class="meta"></div><div id="model-usage" class="activity-scroll"></div></details>
<details id="panel-tool-activity" class="card wide" ><summary>Tool activity <span id="summary-tool-activity" class="panel-meta"></span></summary><div id="tool-activity" class="activity-scroll"></div></details>
<details id="panel-filesystem-activity" class="card wide" ><summary>Filesystem activity <span id="summary-filesystem-activity" class="panel-meta"></span></summary><div id="filesystem-activity" class="activity-scroll"></div></details>
<details id="panel-blockers" class="card wide"  open><summary>Open blockers <span id="summary-blockers" class="panel-meta"></span></summary><div id="organizer-intervention" class="critical intervention" hidden><strong>ORGANIZER INTERVENTION REQUIRED</strong><div class="notice">The host blocked an allowed operation that the orchestrator cannot complete. Contestants must not operate the machine.</div><div id="organizer-interventions" class="blockers"></div></div><div id="blockers" class="blockers"></div></details>
<details id="panel-orchestrator-console" class="card wide"  open><summary>Orchestrator console <span id="summary-orchestrator-console" class="panel-meta"></span></summary><label class="meta"><input id="console-follow" type="checkbox" checked> Follow latest</label><div class="meta">Player observation time, not official Game Master time.</div><pre id="console" class="console"></pre></details>
<details id="panel-suspected-violations" class="card wide" ><summary>Suspected Violations <span id="summary-suspected-violations" class="panel-meta"></span></summary><div class="meta">Evidence only. The organizer decides after the match.</div><div id="suspected-violations" class="activity-scroll"></div></details>
<details id="panel-team-info-session" class="card wide" ><summary>Team info session <span id="summary-team-info-session" class="panel-meta"></span></summary><div class="meta">Full data is loaded only when requested by the organizer and exists only in the active Player session.</div><button id="team-info-toggle"${liveTeamInfoAvailable ? '' : ' disabled'}>${liveTeamInfoAvailable ? 'Show full details' : 'Unavailable in static dashboard'}</button><pre id="team-info" class="console team-info" hidden></pre></details></div></main>
<script>'use strict';
${transcriptUiScript}
${agentControlScript}
const names={GIT_AUTH:'Git authentication',REPOSITORY:'Source checkout',ENVIRONMENT:'Environment setup',BUILD:'Source build',PREPARE_COMPLETE:'Preparation Complete',MATCH_LOGIN:'Match page login',BACKLOG_SYNC:'Backlog sync'};
const el=id=>document.getElementById(id); const text=(tag,value,cls)=>{const n=document.createElement(tag);n.textContent=value;if(cls)n.className=cls;return n};
const duration=s=>{const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),v=s%60;return(h?h+'h ':'')+(m||h?String(m).padStart(h?2:1,'0')+'m ':'')+String(v).padStart(m||h?2:1,'0')+'s'};
const tests=t=>!t?'Not reported':t.status+' · '+t.passed+'/'+t.executed+' passed · '+t.failed+' failed · '+t.skipped+' skipped · '+t.required+' required';
const api=a=>!a?'Not called':a.lastOperation+' · '+a.lastPhase+(a.httpStatus?' · HTTP '+a.httpStatus:'')+(a.requestId?' · '+a.requestId:'')+' · '+a.count+' event(s)';
const number=v=>new Intl.NumberFormat('en-US').format(v);const money=(v,c)=>v===null?'N/A':c==='CREDITS'?new Intl.NumberFormat('en-US',{maximumFractionDigits:0}).format(v)+' credits':new Intl.NumberFormat('en-US',{style:'currency',currency:c,maximumFractionDigits:6}).format(v);
const uuidPattern=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
let displayedIds=[];let currentModelFindings=[];
function shortId(value){let n=8;while(n<value.length-4&&displayedIds.some(other=>other!==value&&other.slice(0,n)===value.slice(0,n)&&other.slice(-4)===value.slice(-4)))n+=4;return n>=value.length-4?value:value.slice(0,n)+'…'+value.slice(-4)}
function shortTime(value){const d=new Date(value);if(!Number.isFinite(d.getTime()))return value;const now=new Date();return d.toDateString()===now.toDateString()?d.toLocaleTimeString('en-GB',{hour12:false}):d.toLocaleDateString('en-GB',{day:'2-digit',month:'short',...(d.getFullYear()!==now.getFullYear()?{year:'numeric'}:{})})+' '+d.toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit',hour12:false})}
function recordCell(value){const raw=String(value),cell=text('td','');if(uuidPattern.test(raw)){const button=text('button',shortId(raw),'copy-id compact-value');button.title=raw+' — Copy full ID';button.onclick=async()=>{try{await navigator.clipboard.writeText(raw);button.title=raw+' — Copied'}catch{button.textContent=raw;button.title='Copy unavailable; select the full ID'}};cell.append(button)}else if(/^[0-9]{4}-[0-9]{2}-[0-9]{2}T/.test(raw)){cell.textContent=shortTime(raw);cell.title=raw;cell.className='compact-value'}else cell.textContent=raw;return cell}
function table(headers,rows){if(!rows.length)return text('div','No observed data.','empty');const t=document.createElement('table'),h=document.createElement('thead'),hr=document.createElement('tr');t.className='record-table';headers.forEach(v=>hr.append(text('th',v)));h.append(hr);const b=document.createElement('tbody');rows.forEach(values=>{const tr=document.createElement('tr');values.forEach(v=>tr.append(recordCell(v)));const mi=headers.indexOf('Model'),ti=headers.indexOf('Task');if(mi>=0&&currentModelFindings.some(v=>v.model===values[mi]&&(ti<0||v.taskId===values[ti]))){tr.className='model-not-allowed';tr.title='MODEL NOT ALLOWED';tr.children[mi].append(text('span',' MODEL NOT ALLOWED','badge FAILED'))}b.append(tr)});t.append(h,b);return t}
function row(label,item){const n=text('div','', 'row');const a=document.createElement('div');a.append(text('strong',label));a.append(text('div',item.summary,'meta'));n.append(a,text('span',item.status,'badge '+item.status));return n}
let tabGroupSequence=0;
function tabGroup(parent,groups){
 const prefix='view-tabs-'+(++tabGroupSequence),bar=document.createElement('div');
 bar.className='view-tabs';bar.setAttribute('role','tablist');bar.setAttribute('aria-label',parent.querySelector('summary')?.textContent||'Activity views');
 function select(index){groups.forEach(([,content],i)=>content.hidden=i!==index);buttons.forEach((button,i)=>{button.setAttribute('aria-selected',String(i===index));button.tabIndex=i===index?0:-1})}
 const buttons=groups.map(([label,node],index)=>{
  const button=text('button',label);button.type='button';button.id=prefix+'-tab-'+index;button.setAttribute('role','tab');
  if(!node.id)node.id=prefix+'-panel-'+index;
  button.setAttribute('aria-controls',node.id);node.setAttribute('role','tabpanel');node.setAttribute('aria-labelledby',button.id);node.tabIndex=0;
  button.onclick=()=>select(index);
  button.onkeydown=event=>{let next;if(event.key==='ArrowRight')next=(index+1)%groups.length;else if(event.key==='ArrowLeft')next=(index+groups.length-1)%groups.length;else if(event.key==='Home')next=0;else if(event.key==='End')next=groups.length-1;else return;event.preventDefault();select(next);buttons[next].focus()};
  bar.append(button);return button;
 });select(0);parent.append(bar,...groups.map(([,node])=>node));
}
const activityPanel=el('panel-live-transcript'),runtimePanel=el('panel-local-player-application');
el('panel-overview').after(el('approval-controls'));el('approval-controls').after(activityPanel);activityPanel.open=true;
const transcriptPane=document.createElement('div');while(activityPanel.children.length>1)transcriptPane.append(activityPanel.children[1]);
const eventsPanel=el('panel-orchestrator-console');eventsPanel.open=true;
const toolPane=document.createElement('div');const cliTools=document.createElement('div');cliTools.id='cli-tool-activity';cliTools.className='activity-scroll';toolPane.append(cliTools,el('tool-activity'));el('panel-tool-activity').hidden=true;
const chatPane=document.createElement('div'),chatHistory=document.createElement('div'),chatForm=document.createElement('form'),chatInput=document.createElement('textarea'),chatSend=text('button','Send'),chatStatus=text('p','Waiting for an Orchestrator session.','meta chat-status');
chatHistory.id='orchestrator-chat-history';chatHistory.className='console chat-history';chatInput.id='orchestrator-chat-input';chatInput.name='message';chatInput.maxLength=8000;chatInput.placeholder='Send guidance to the Team Orchestrator…';chatInput.setAttribute('aria-label','Message to Team Orchestrator');chatSend.type='submit';chatSend.id='orchestrator-chat-send';chatForm.id='orchestrator-chat-form';chatForm.className='chat-form';chatForm.append(chatInput,chatSend);chatStatus.id='orchestrator-chat-status';chatPane.append(chatHistory,chatForm,chatStatus);
tabGroup(activityPanel,[['Transcript',transcriptPane],['Chat',chatPane],['Tool calls',toolPane],['Progress events',eventsPanel]]);
const processPanel=el('panel-agent-amp-process-activity');runtimePanel.append(processPanel);
const detailsPanel=document.createElement('details');detailsPanel.className='card wide';detailsPanel.append(text('summary','Activity details'));
const networkPanel=el('panel-network-amp-api-activity'),filesPanel=el('panel-filesystem-activity');networkPanel.open=true;filesPanel.open=true;
tabGroup(detailsPanel,[['Network & API',networkPanel],['Filesystem',filesPanel]]);
const container=el('panel-overview').parentElement;
container.append(el('panel-model-usage-amp-nominal-credit'),detailsPanel,runtimePanel,el('panel-codex-task-observation'),el('panel-blockers'),el('panel-suspected-violations'),el('panel-team-info-session'));
const panelIds=["overview","blockers","playing","preparation","codex-task-observation","live-transcript","agent-amp-process-activity","suspected-violations","network-amp-api-activity","model-usage-amp-nominal-credit","tool-activity","filesystem-activity","team-info-session","local-player-application","orchestrator-console"];const chosenPanels=new Set();
for(const id of panelIds){const panel=el('panel-'+id);try{const saved=localStorage.getItem('mtm-dashboard:'+location.pathname+':'+id);if(saved!==null){panel.open=saved==='open';chosenPanels.add(id)}}catch{}const summary=panel.firstElementChild;if(summary)summary.onclick=()=>{chosenPanels.add(id)};panel.ontoggle=()=>{if(!chosenPanels.has(id))return;try{localStorage.setItem('mtm-dashboard:'+location.pathname+':'+id,panel.open?'open':'closed')}catch{}}}
function defaultPanel(id,open){const panel=el('panel-'+id);if(!chosenPanels.has(id))panel.open=open}
function overviewCard(label,item){const card=text('div','','overview-card '+item.status);card.append(text('strong',label),text('div',item.summary,'meta'),text('span',item.status,'badge '+item.status));return card}
let hadBacklogs=false;
function overview(s){displayedIds=[...new Set(JSON.stringify(s).match(/[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}/gi)||[])];const prep=Object.values(s.preparation),done=prep.filter(v=>v.status==='COMPLETED').length,complete=prep.length>0&&done===prep.length,ended=['FINISHED','CANCELLED'].includes(s.matchStatus?.status);const active=s.backlogs.filter(v=>['BLOCKED','IN_PROGRESS'].includes(v.status)),unfinished=ended?s.backlogs.filter(v=>!['SUBMITTED','COMPLETED','ACCEPTED'].includes(v.status)):[];const blocked=Boolean(s.blockers.length||s.organizerInterventionRequired||(!ended&&active.some(v=>v.status==='BLOCKED'))||[...prep,...Object.values(s.gameStart)].some(v=>v.status==='BLOCKED'));const playing=s.matchStatus?.status==='IN_PROGRESS'||s.backlogs.length>0;el('panel-preparation').className='card wide'+(complete?' preparation-completed':'');defaultPanel('preparation',!complete);if(s.backlogs.length&&!hadBacklogs)el('panel-playing').open=true;hadBacklogs=s.backlogs.length>0;el('match-overview').replaceChildren(overviewCard('Blockers',{status:blocked?'BLOCKED':'CLEAR',summary:blocked?(s.organizerInterventionRequired?'Organizer intervention required.':'Review blocked work below.'):'No blockers reported.'}),overviewCard('Preparation',{status:complete?'COMPLETED':prep.some(v=>v.status==='BLOCKED')?'BLOCKED':'PREPARING',summary:done+'/'+prep.length+' steps completed'}),overviewCard('Current work',{status:unfinished.length?'UNFINISHED':active.some(v=>v.status==='BLOCKED')?'BLOCKED':active.length?'IN_PROGRESS':'WAITING',summary:unfinished.length?unfinished.length+' backlog(s) unfinished when the match ended.':active.length?active.map(v=>v.id+' — '+v.title).join(' · '):'No active backlogs reported.'}));el('summary-overview').textContent=blocked?'BLOCKED':complete?'Preparation complete':'Preparing';el('summary-overview').className='panel-meta '+(blocked?'FAILED':'');el('summary-blockers').textContent=blocked?'Needs attention':'No open blockers';el('summary-blockers').className='panel-meta '+(blocked?'FAILED':'');el('summary-preparation').textContent=(complete?'COMPLETED · ':'')+done+'/'+prep.length;el('summary-playing').textContent=ended?unfinished.length+' unfinished · '+s.backlogs.filter(v=>v.status==='SUBMITTED').length+' submitted':!playing?'Wait for game start':active.length+' active · '+s.backlogs.filter(v=>v.status==='SUBMITTED').length+' submitted · '+s.backlogs.filter(v=>['PENDING','DISCOVERED'].includes(v.status)).length+' pending';el('summary-suspected-violations').textContent=String(s.suspectedViolations.length);const tasks=s.codexObservation?.tasks||[];el('summary-codex-task-observation').textContent=tasks.filter(v=>v.taskStatus==='RUNNING').length+' running · '+tasks.filter(v=>v.taskStatus==='STOPPED').length+' stopped';}
let teamInfoLoaded=false;async function toggleTeamInfo(){const box=el('team-info'),button=el('team-info-toggle');if(teamInfoLoaded){box.hidden=!box.hidden;button.textContent=box.hidden?'Show full details':'Hide details';return}button.disabled=true;button.textContent='Loading…';try{const r=await fetch(location.pathname+'/team-info',{cache:'no-store'});const value=await r.json();if(!r.ok)throw Error(value.code||'TEAM_INFO_UNAVAILABLE');box.textContent=JSON.stringify(value,null,2);box.hidden=false;teamInfoLoaded=true;button.textContent='Hide details'}catch(e){box.textContent=e.message;box.hidden=false;button.textContent='Retry'}finally{button.disabled=false}}el('team-info-toggle').onclick=toggleTeamInfo;
const liveTranscriptAvailable=${managementPath ? 'true' : 'false'};let transcriptTask='';let transcriptLoading=false;let latestTranscriptTasks=[];let embeddedTranscripts=[];let chatAvailable=false;let pendingChatMessages=[];let latestChatTranscriptItems=[];
function renderPendingChat(item){const node=text('div','','chat-pending'),head=text('div','','chat-pending-head'),identity=text('div','','');identity.append(text('span','You','chat-pending-author'),text('span',item.createdAt?' · '+shortTime(item.createdAt):'','chat-pending-time'));head.append(identity,text('span','Queued'+(item.position?' · #'+item.position:''),'chat-pending-badge'));node.append(head,text('div',item.message||'','chat-pending-text'));return node}
function renderChatHistory(items){latestChatTranscriptItems=items||[];const filtered=latestChatTranscriptItems.filter(item=>item.role==='USER'||item.role==='ASSISTANT'),entries=filtered.map(item=>{const node=renderTranscriptEntry(item);node.classList.add(item.role==='USER'?'chat-message-user':'chat-message-assistant');return node});entries.push(...pendingChatMessages.map(renderPendingChat));chatHistory.replaceChildren(...entries);if(!entries.length)chatHistory.textContent='No chat messages yet.';chatHistory.scrollTop=chatHistory.scrollHeight}
async function loadLiveTranscript(tasks){
  latestTranscriptTasks=tasks;const select=el('transcript-task'),box=el('live-transcript'),status=el('transcript-status');
  const next=tasks.some(v=>v.codexThreadId===transcriptTask)?transcriptTask:(tasks.find(v=>v.role==='ORCHESTRATOR')||tasks[0])?.codexThreadId||'';
  if(transcriptTask!==next||(select.options?.length??0)!==tasks.length){transcriptTask=next;select.replaceChildren(...tasks.map(v=>{const option=document.createElement('option');option.value=v.codexThreadId;option.textContent=v.role+' · '+(v.agentPath||v.codexThreadId);return option}));select.value=transcriptTask}
  if(!transcriptTask){select.disabled=true;status.textContent='No registered tasks';box.textContent='No transcript available.';el('summary-live-transcript').textContent='No data';defaultPanel('live-transcript',true);return}
  if(!liveTranscriptAvailable){
    const value=embeddedTranscripts.find(v=>v.sessionId===transcriptTask);
    select.disabled=false;
    if(!value){status.textContent='Unavailable in snapshot';box.textContent='Transcript was not included in this snapshot.';el('summary-live-transcript').textContent='Unavailable';defaultPanel('live-transcript',true);return}
    el('cli-tool-activity').replaceChildren(table(['Time','CLI event','Details'],(value.items||[]).filter(item=>item.role==='TOOL').map(item=>[item.timestamp,item.type,item.text])));
    status.textContent=(value.status||'READY')+(value.truncated?' · latest entries':'')+' · embedded snapshot';
    el('summary-live-transcript').textContent=(value.items||[]).length+' record(s)';
    box.replaceChildren(...(value.items||[]).map(renderTranscriptEntry));renderChatHistory(value.items||[]);if(!(value.items||[]).length)box.textContent='No transcript entries available.';return;
  }
  if(transcriptLoading)return;transcriptLoading=true;try{const r=await fetch('/manage/transcript?threadId='+encodeURIComponent(transcriptTask),{cache:'no-store'}),value=await r.json();if(!r.ok)throw Error(value.code||'TRANSCRIPT_UNAVAILABLE');el('cli-tool-activity').replaceChildren(table(['Time','CLI event','Details'],(value.items||[]).filter(item=>item.role==='TOOL').map(item=>[item.timestamp,item.type,item.text])));status.textContent=value.status+(value.truncated?' · latest entries':'');el('summary-live-transcript').textContent=(value.items||[]).length+' record(s)';box.replaceChildren(...(value.items||[]).map(renderTranscriptEntry));renderChatHistory(value.items||[]);if(!(value.items||[]).length)box.textContent='No transcript entries available.';if(el('transcript-follow').checked)box.scrollTop=box.scrollHeight}catch(error){status.textContent=error.message;box.textContent='Transcript unavailable.';el('summary-live-transcript').textContent='Unavailable'}finally{transcriptLoading=false}
}
el('transcript-task').onchange=()=>{transcriptTask=el('transcript-task').value;void loadLiveTranscript(latestTranscriptTasks)};
chatForm.onsubmit=async event=>{event.preventDefault();const message=chatInput.value.trim();if(!message||!chatAvailable)return;chatSend.disabled=true;chatInput.disabled=true;chatStatus.textContent='Sending…';try{const r=await fetch(location.pathname+'/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message})}),value=await r.json();if(!r.ok)throw Error(value.code||'ORCHESTRATOR_CHAT_FAILED');chatInput.value='';if(value.status==='CHAT_QUEUED'){pendingChatMessages=[...pendingChatMessages.filter(item=>item.id!==value.messageId),{id:value.messageId,message,createdAt:value.queuedAt||new Date().toISOString(),position:value.position}];renderChatHistory(latestChatTranscriptItems)}chatStatus.textContent=value.status==='CHAT_QUEUED'?'Queued — it will be sent after the current turn.':'Sent to Team Orchestrator.';setTimeout(()=>void loadLiveTranscript(latestTranscriptTasks),400)}catch(error){chatStatus.textContent=error.message}finally{chatInput.disabled=!chatAvailable;chatSend.disabled=!chatAvailable}};
function render(s){embeddedTranscripts=Array.isArray(s.transcripts)?s.transcripts:[];currentModelFindings=s.modelViolations||[];overview(s);el('allowed-models').textContent=s.modelPolicy?'Allowed models: '+s.modelPolicy.allowedModels.join(', ')+' · Observe only; organizer decides penalties.':'Model policy not configured for this round.';if(currentModelFindings.length){el('summary-overview').textContent+=' · MODEL NOT ALLOWED';el('summary-overview').className='panel-meta FAILED';el('summary-model-usage-amp-nominal-credit').className='panel-meta FAILED'}el('panel-blockers').className='card wide'+((s.blockers.length||s.organizerInterventionRequired||s.organizerInterventions.length)?' records-alert':'');el('panel-suspected-violations').className='card wide'+(s.suspectedViolations.length?' records-alert':'');const counts={'codex-task-observation':(s.codexObservation?.tasks?.length||0)+(s.codexObservation?.usage?.length||0)+(s.codexObservation?.activity?.length||0),'agent-amp-process-activity':s.agentActivity.length+s.processActivity.length,'model-usage-amp-nominal-credit':s.modelUsage.length,'tool-activity':s.toolActivity.length,'filesystem-activity':s.filesystemActivity.length,'orchestrator-console':s.eventConsole.length};for(const [id,count] of Object.entries(counts)){if(!count){el('summary-'+id).textContent='No data';defaultPanel(id,false);}else if(id!=='codex-task-observation')el('summary-'+id).textContent=count+' record(s)'}el('summary-suspected-violations').textContent=s.suspectedViolations.length?s.suspectedViolations.length+' record(s)':'No data';if(!s.suspectedViolations.length)defaultPanel('suspected-violations',false);if(!s.blockers.length&&!s.organizerInterventionRequired)defaultPanel('blockers',false);if(!s.backlogs.length)defaultPanel('playing',false);el('team').textContent=s.teamId+' — '+s.teamName;const ms=s.matchStatus?.status||'UNKNOWN';el('match-state').textContent='MATCH '+ms;el('match-state').className='badge '+ms;el('preparation').replaceChildren(...Object.entries(s.preparation).map(([k,v])=>row(names[k]||k,v)));
const la=s.localApplication;pendingChatMessages=Array.isArray(la?.chatQueue)?la.chatQueue:[];renderChatHistory(latestChatTranscriptItems);el('local-application').hidden=!la;el('panel-local-player-application').hidden=!la;if(la){if(['FINISHED','CANCELLED','STOPPED','OPERATOR_STOPPED'].includes(la.phase))defaultPanel('local-player-application',false);el('summary-local-player-application').textContent=(la.roundId||'')+' · '+la.phase+' · '+((la.operations||[]).length?(la.operations||[]).length+' pending operations':'No pending operations');el('local-application-state').replaceChildren(table(['Round','Phase','Official match','Agent','CLI provider','CLI process','CLI session','Diagnostic integrity','Preparation acknowledgement','Polling','Last poll'],[[la.roundId||'—',la.phase,la.officialStatus,la.agentState,la.cli?.provider||'—',la.cli?.status||'—',la.cli?.sessionId||'—',la.diagnostics?.status||'UNKNOWN',la.ackState,la.pollHealth?.status||'UNKNOWN',la.pollHealth?.observedAt||'—']]));el('local-application-operations').replaceChildren(...((la.operations||[]).length?[table(['Pending operation','Kind','State'],la.operations.slice(-100).map(v=>[v.id,v.kind,v.state]))]:[]))}
const co=s.codexObservation||{status:'UNAVAILABLE',tasks:[],usage:[],activity:[]};el('codex-status').textContent=co.status+' · Since task registration; estimated usage, separate from team reports. Cached input is included in input. Observation time is not official match time.';el('codex-tasks').replaceChildren(table(['Task','Role','Agent','Task status','Status evidence','Updated','Observation status'],co.tasks.map(v=>[v.codexThreadId,v.role,v.agentPath||'—',v.taskStatus||'UNKNOWN',v.statusSource||'UNAVAILABLE',v.statusAt||'—',v.status])));el('codex-usage').replaceChildren(table(['Task','Model','Input','Cached input','Output'],co.usage.map(v=>[v.codexThreadId,v.model||'Unknown',number(v.input),number(v.cached),number(v.output)])));el('codex-activity').replaceChildren(table(['Observed at','Task','Line','Type','Tool / diagnostic'],co.activity.map(v=>[v.observedAt,v.codexThreadId,v.lineNumber,v.type,v.tool||v.code||'Token usage'])));
const cli=la?.cli;const orchStopped=cli?.status==='STOPPED'||la?.agentState==='STOPPED'||la?.phase==='OPERATOR_STOPPED';const orchState=orchStopped?'STOPPED':cli?.status||la?.agentState||'UNKNOWN';el('orchestrator-state').textContent='ORCHESTRATOR: '+orchState;el('orchestrator-state').style.color=orchState==='RUNNING'?'#80edb6':'#ff9aa7';el('orchestrator-state').style.background=orchState==='RUNNING'?'#123d31':'#3e1d29';renderAgentControls(cli||{},el('agent-controls'),el('approval-controls'));el('panel-codex-task-observation').hidden=Boolean(cli);defaultPanel('live-transcript',true);
chatAvailable=Boolean(liveTranscriptAvailable&&cli?.sessionId&&!orchStopped&&!['FINISHED','CANCELLED'].includes(ms));chatInput.disabled=!chatAvailable;chatSend.disabled=!chatAvailable;if(!chatAvailable)chatStatus.textContent=!liveTranscriptAvailable?'Chat is unavailable in a static snapshot.':!cli?.sessionId?'Waiting for an Orchestrator session.':orchStopped?'Orchestrator is stopped.':(['FINISHED','CANCELLED'].includes(ms)?'The match has ended.':'Chat is unavailable.');else if(pendingChatMessages.length)chatStatus.textContent=pendingChatMessages.length+' message'+(pendingChatMessages.length===1?' is':'s are')+' queued.';else if(!chatStatus.textContent||chatStatus.textContent.startsWith('Waiting')||chatStatus.textContent.startsWith('Orchestrator')||chatStatus.textContent.startsWith('The match')||chatStatus.textContent.includes('queued'))chatStatus.textContent=cli.status==='RUNNING'||cli.status==='WAITING_APPROVAL'?'Messages are queued while the current turn is busy.':'Ready.';
const observed=cli?.activity,age=observed?.observedAt?Math.max(0,Math.floor((Date.now()-Date.parse(observed.observedAt))/1000)):null;
el('orchestrator-current').textContent=(orchStopped?'Orchestrator is stopped. ':'')+(cli?(cli.provider+' · '+cli.status+' · Session '+(cli.sessionId?shortId(cli.sessionId):'not started')+' · '+(observed?('Last event: '+observed.type+' · '+observed.status+' · '+age+'s ago'):'Waiting for activity data')):'Waiting for activity data');
const snapshotTasks=embeddedTranscripts.length?embeddedTranscripts.map(v=>({codexThreadId:v.sessionId,role:v.role,agentPath:v.agentPath})):cli?.sessionId?[{codexThreadId:cli.sessionId,role:'ORCHESTRATOR',agentPath:cli.provider+' CLI'}]:(co.tasks||[]);void loadLiveTranscript(snapshotTasks);
if(s.blockers.length||s.organizerInterventionRequired)defaultPanel('blockers',true);if(s.suspectedViolations.length)defaultPanel('suspected-violations',true);
el('usage-meta').textContent='Source: '+s.observability.modelUsage+' · Cached input is included in input. CLI usage updates at turn completion; unknown models are not priced. · Price card: '+s.priceCard.id+(s.priceCard.currency==='CREDITS'?' · OpenAI Business/Enterprise/Edu Standard rates; Fast-mode surcharge excluded. Estimate only, not billed credits.':'')+' · Reasoning tokens are a subset of output and are not counted twice.';el('model-usage').replaceChildren(table(['Model','Source','Input','Cached input','Output','Reasoning',s.priceCard.currency==='CREDITS'?'Estimated credits':'Nominal credit'],s.modelUsage.map(v=>[v.model,v.source,number(v.inputTokens),number(v.cachedInputTokens),number(v.outputTokens),number(v.reasoningTokens),money(v.nominalCredit,s.priceCard.currency)])));
const agents=[...s.agentActivity.map(v=>['Agent',v.agentId,v.parentAgentId||'—',v.operation,v.phase,v.source,v.summary]),...s.processActivity.map(v=>['Process',v.processName,'—',v.operation,v.phase,v.source,v.summary])];el('agent-process-activity').replaceChildren(table(['Type','Identifier','Parent','Operation','Phase','Source','Summary'],agents));
el('tool-activity').title=s.monitoring?.tool||'Monitoring unavailable';el('filesystem-activity').title=s.monitoring?.filesystem||'Monitoring unavailable';el('agent-process-activity').title=s.monitoring?.process||'Monitoring unavailable';el('tool-activity').replaceChildren(table(['Tool','Operation','Backlog','Phase','Source','Summary'],s.toolActivity.map(v=>[v.toolName,v.operation,v.backlogId||'—',v.phase,v.source,v.summary])));
el('filesystem-activity').replaceChildren(table(['Access','Path','Operation','Phase','Source','Summary'],s.filesystemActivity.map(v=>[v.access,v.path,v.operation,v.phase,v.source,v.summary])));
const network=[...s.apiActivity.map(v=>({source:'TEAM_REPORTED',...v})),...s.networkActivity];const latestApi=network.at(-1);if(!network.length)defaultPanel('network-amp-api-activity',false);el('summary-network-amp-api-activity').textContent=latestApi?latestApi.operation+' · '+latestApi.phase+(latestApi.httpStatus?' · HTTP '+latestApi.httpStatus:''):'No data';el('network-activity').replaceChildren(table(['Method','Origin','Path','Operation','Backlog','Phase','HTTP','Source'],network.map(v=>[v.method||'—',v.origin||'—',v.path||'—',v.operation,v.backlogId||'—',v.phase,v.httpStatus||'—',v.source])));
for(const [node,key,count] of [['tool-activity','tool',s.toolActivity.length],['filesystem-activity','filesystem',s.filesystemActivity.length],['network-activity','network',network.length]]){const note=text('p',s.monitoring?.[key]||'Monitoring unavailable','meta');el(node).prepend(note);if(!count)el('summary-'+({tool:'tool-activity',filesystem:'filesystem-activity',network:'network-amp-api-activity'}[key])).textContent=s.monitoring?.[key]?.startsWith('Monitoring unavailable')?'Monitoring unavailable':s.monitoring?.[key]?'No activity recorded':'Monitoring unavailable'}el('suspected-violations').replaceChildren(table(['Code','Source','Operation','Resource','Evidence'],s.suspectedViolations.map(v=>[v.code,v.source,v.operation,v.resource,v.summary])));
const intervention=el('organizer-intervention');intervention.hidden=!s.organizerInterventionRequired;const alerts=s.organizerInterventions.map(v=>row((v.backlogId?v.backlogId+' · ':'')+v.code,{status:v.severity,summary:v.summary+' '+v.action}));el('organizer-interventions').replaceChildren(...alerts);
const consoleLines=s.eventConsole.map(v=>'['+(v.observedAt?shortTime(v.observedAt):'—')+'] #'+v.line+' '+v.disposition+' '+v.type+(v.operation?' '+v.operation:'')+(v.phase?' '+v.phase:'')+(v.backlogId?' '+v.backlogId:'')+(v.httpStatus?' HTTP '+v.httpStatus:'')+(v.requestId?' '+v.requestId:'')+' — '+v.summary);el('console').textContent=consoleLines.length?consoleLines.join('\\n'):'No events received.';
if(el('console-follow').checked)el('console').scrollTop=el('console').scrollHeight;
const bs=s.blockers.map(b=>row(b.backlogId?b.backlogId+' · '+b.id:b.id,{status:'BLOCKED',summary:b.summary}));el('blockers').replaceChildren(...(bs.length?bs:[text('div','No open blockers.','empty')]));
if(!s.backlogs.length){el('backlogs').replaceChildren(text('div',s.matchStatus?.status==='IN_PROGRESS'?'Waiting for backlogs.':['FINISHED','CANCELLED'].includes(s.matchStatus?.status)?'No backlogs received.':'Wait for game start','empty'));return}const backlogTable=document.createElement('table'),head=document.createElement('thead'),hr=document.createElement('tr'),ended=['FINISHED','CANCELLED'].includes(s.matchStatus?.status);['ID','Backlog','Tests','Game Master API','Work duration','Status','Update'].forEach(v=>hr.append(text('th',v)));head.append(hr);const body=document.createElement('tbody');[...s.backlogs].sort((a,b)=>({BLOCKED:0,IN_PROGRESS:1,DISCOVERED:2,PENDING:2,SUBMITTED:3}[a.status]??2)-({BLOCKED:0,IN_PROGRESS:1,DISCOVERED:2,PENDING:2,SUBMITTED:3}[b.status]??2)).forEach(b=>{const tr=document.createElement('tr'),status=text('td','','status-cell'),unfinished=ended&&!['SUBMITTED','COMPLETED','ACCEPTED'].includes(b.status),displayStatus=unfinished?'UNFINISHED':b.status;tr.className='backlog-row '+displayStatus;status.append(text('span',displayStatus,'badge '+displayStatus));if(unfinished)status.append(text('small','Last reported: '+b.status,'backlog-last-status'));tr.append(text('td',b.id),text('td',b.title),text('td',tests(b.tests)),text('td',api(b.api)),text('td',duration(b.observedActiveSeconds)),status,text('td',unfinished?'Match ended before submission. '+b.summary:b.summary));body.append(tr)});backlogTable.className='backlog-table';backlogTable.append(head,body);el('backlogs').replaceChildren(backlogTable)}
async function poll(){const slowUpdate=setTimeout(()=>{if(el('refresh').className!=='live-indicator reconnecting'){el('refresh').textContent='Updating…';el('refresh').className='live-indicator updating';}},1500);try{const r=await fetch(location.pathname+'/snapshot',{cache:'no-store',signal:AbortSignal.timeout(10000)});const s=await r.json();if(!r.ok)throw Error(s.code||'DASHBOARD_ERROR');render(s);el('refresh').textContent='Live';el('refresh').className='live-indicator live';el('refresh').title='Last successful update: '+new Date().toLocaleTimeString()}catch(e){el('refresh').textContent='Reconnecting…';el('refresh').className='live-indicator reconnecting';el('refresh').title=e.message}finally{clearTimeout(slowUpdate)}setTimeout(poll,1000)}const initialSnapshot=${serializedSnapshot};if(initialSnapshot){render(initialSnapshot);el('refresh').textContent='Snapshot';el('refresh').className='live-indicator snapshot'}else poll();
</script></body></html>`;
}

export async function startProgressDashboard({ teamSdlcPackWorkspace, matchId, playerSessionId, teamId, teamName,
  fresh = false, hostTelemetryFile = null, priceCard = emptyPriceCard, liveTeamInfo = null,
  auditPolicy = null, codexObserver = null, listenPort = 0, routePath = null, localApplicationState = null,
  modelPolicy = null, modelEvidence = [], onModelEvidence = () => {} }) {
  if (typeof fresh !== 'boolean') fail('INVALID_PROGRESS_MODE');
  if (!Number.isInteger(listenPort) || listenPort < 0 || listenPort > 65535) fail('INVALID_DASHBOARD_PORT');
  // Readable routes identify a match; they are not a secret-bearing capability.
  if (routePath !== null && (!id(matchId) || routePath !== `/${matchId}` ||
      !/^\/[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(routePath))) fail('INVALID_DASHBOARD_ROUTE');
  const validatedPriceCard = validatePriceCard(priceCard);
  if (fresh) {
    initializeProgressLog(teamSdlcPackWorkspace, matchId);
    resetPlayerProgress(teamSdlcPackWorkspace, matchId);
  }
  const logPath = progressLogPath(teamSdlcPackWorkspace, matchId);
  const dashboardPath = staticDashboardPath(teamSdlcPackWorkspace, matchId);
  const route = routePath ?? `/${randomBytes(24).toString('hex')}`;
  const context = { teamSdlcPackWorkspace, matchId, playerSessionId, teamId, teamName };
  reduceProgressEvents([], context);
  const validatedLiveTeamInfo = liveTeamInfo === null ? null : validateLiveTeamInfo(liveTeamInfo, context);
  const exposeLiveTeamInfo = routePath === null && validatedLiveTeamInfo !== null;
  const timingState = new Map();
  const observedAtByLine = new Map();
  let snapshot;
  const modelFindings = new Map(modelEvidence.map(item => [JSON.stringify([item.source, item.taskId, item.model]), item]));
  let snapshotError = null;
  const refresh = () => {
    try {
      const progress = addObservedWorkTime(readProgressLog(logPath, context, { observedAtByLine,
        hostTelemetryFile, priceCard: validatedPriceCard, auditPolicy }), timingState);
      let codexObservation = { status: 'UNAVAILABLE', tasks: [], activity: [], usage: [] };
      try { if (codexObserver) codexObservation = codexObserver.poll(); }
      catch { codexObservation.status = 'OBSERVER_UNAVAILABLE'; }
      const findingCount = modelFindings.size;
      const localApplication = localApplicationSnapshot(localApplicationState);
      let next = applyModelPolicy({ ...applyCliActivity(applyCliUsage(progress, localApplication?.cli, validatedPriceCard), localApplication?.cli), codexObservation, localApplication,
        matchStatus: readPersistedMatchStatus(teamSdlcPackWorkspace, matchId) }, modelPolicy, modelFindings);
      if (localApplication?.diagnostics && localApplication.diagnostics.status !== 'VERIFIED') {
        const violation = { id: 'suspected-player-diagnostics-integrity', status: 'SUSPECTED',
          code: 'PLAYER_DIAGNOSTIC_INTEGRITY', source: 'LOCAL_APP', operation: 'PLAYER_STATE',
          resource: 'LOCAL_DIAGNOSTIC_JOURNAL', summary: `Diagnostic journal status: ${localApplication.diagnostics.status}` };
        next = { ...next, suspectedViolations: [...next.suspectedViolations, violation],
          suspectedViolationCount: next.suspectedViolationCount + 1 };
      }
      snapshot = Object.freeze(next);
      if (modelFindings.size !== findingCount) onModelEvidence([...modelFindings.values()]);
      writeStaticDashboard(dashboardPath, snapshot);
      snapshotError = null;
    } catch (error) {
      snapshotError = error instanceof ProgressError ? error : new ProgressError('PROGRESS_READ_FAILED');
    }
  };
  refresh();
  const refreshTimer = setInterval(refresh, 500);
  const server = createServer((request, response) => {
    const headers = { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
      'Referrer-Policy': 'no-referrer', 'Cross-Origin-Resource-Policy': 'same-origin' };
    if (request.method === 'GET' && request.url === route) {
      response.writeHead(200, { ...headers, 'Content-Type': 'text/html; charset=utf-8',
        'Content-Security-Policy': "default-src 'none'; img-src data:; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'" });
      response.end(dashboardDocument(null, exposeLiveTeamInfo)); return;
    }
    if (request.method === 'GET' && request.url === `${route}/snapshot`) {
      refresh();
      if (!snapshotError) {
        response.writeHead(200, { ...headers, 'Content-Type': 'application/json; charset=utf-8' });
        response.end(JSON.stringify(snapshot));
      } else {
        const code = snapshotError.code;
        response.writeHead(422, { ...headers, 'Content-Type': 'application/json; charset=utf-8' });
        response.end(JSON.stringify({ code }));
      }
      return;
    }
    if (request.method === 'GET' && request.url === `${route}/team-info` && exposeLiveTeamInfo) {
      response.writeHead(200, { ...headers, 'Content-Type': 'application/json; charset=utf-8' });
      response.end(JSON.stringify(validatedLiveTeamInfo)); return;
    }
    response.writeHead(404, { ...headers, 'Content-Type': 'text/plain; charset=utf-8' }); response.end('Not found');
  });
  await new Promise((resolveReady, reject) => {
    server.once('error', error => { clearInterval(refreshTimer); reject(error); });
    server.listen(listenPort, '127.0.0.1', resolveReady);
  });
  const { port } = server.address();
  return Object.freeze({ url: `http://127.0.0.1:${port}${route}`, logPath,
    staticDashboardFile: dashboardPath,
    snapshot: () => { refresh(); if (snapshotError) throw snapshotError; return structuredClone(snapshot); },
    close: () => { refresh(); clearInterval(refreshTimer); return new Promise((resolveClose, reject) =>
      server.close(error => error ? reject(error) : resolveClose())); } });
}
