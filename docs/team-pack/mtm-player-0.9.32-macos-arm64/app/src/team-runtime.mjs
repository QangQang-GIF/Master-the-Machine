import { createHash } from 'node:crypto';
import { existsSync, lstatSync, readFileSync, realpathSync, statSync } from 'node:fs';
import { isAbsolute, join, relative, resolve } from 'node:path';
import { initializeProgressLog } from './progress.mjs';
import { matchWorkspacePaths } from './workspace-paths.mjs';
import { repositoryFromSession, validatePreparationReceipt } from './preparation.mjs';

export class TeamRuntimeError extends Error {
  constructor(code) { super(code); this.code = code; this.name = 'TeamRuntimeError'; }
}

const fail = code => { throw new TeamRuntimeError(code); };
const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value) &&
  JSON.stringify(Object.keys(value).sort()) === JSON.stringify([...keys].sort());
const text = (value, max = 4096) => typeof value === 'string' && value.length > 0 &&
  value.length <= max && !value.includes('\0');
const id = value => text(value, 128) && /^[A-Za-z0-9][A-Za-z0-9_.:-]*$/.test(value);

const rootKeys = ['schemaVersion', 'kind', 'name', 'version', 'protocolVersion', 'provider',
  'entrypoint'];
const entrypointKeys = ['rootAgentType'];
export const requiredPlayerMonitoring = Object.freeze(['agent.lifecycle.read', 'tool.activity.read',
  'process.activity.read', 'filesystem.activity.read', 'network.activity.read',
  'model.usage.read', 'child.stop']);
export const requiredTeamRestrictions = Object.freeze(['workspace.challenge-only',
  'network.allowed-origins-only', 'parent-transcript.denied', 'player-state.denied',
  'descendants.inherit', 'git.assigned-repository-only', 'git.force-push.denied',
  'git.authentication.operator-owned']);

export function validateTeamRuntimeManifest(value) {
  if (!exact(value, rootKeys) || value.schemaVersion !== 1 || value.kind !== 'mtm-team-runtime' ||
      value.name !== 'team-sdlc-pack' || !/^\d+\.\d+\.\d+$/.test(value.version) ||
      value.protocolVersion !== 4 || !['codex', 'claude'].includes(value.provider)) {
    fail('INCOMPATIBLE_TEAM_RUNTIME');
  }
  if (!exact(value.entrypoint, entrypointKeys) ||
      value.entrypoint.rootAgentType !== 'team_orchestrator') fail('INCOMPATIBLE_TEAM_RUNTIME');
  return Object.freeze(value);
}

const nativeObservationCapabilities = Object.freeze(['agent.lifecycle.read',
  'team.progress-log.read', 'child.stop']);

function observationMode(controlPlane) {
  try {
    const validated = validateHostControlPlane(controlPlane);
    return Object.freeze({ source: 'HOST_CONTROL_PLANE',
      capabilities: requiredPlayerMonitoring, restrictionsVerified: false,
      supervisionMode: 'OBSERVE_ONLY',
      hostTelemetryFile: validated.telemetryFile ?? null,
      priceCardFile: validated.priceCardFile ?? null });
  } catch {
    // Stock Codex hosts expose child lifecycle control but do not publish the optional
    // organizer capability manifest. Keep the contest runnable and label the gap.
    return Object.freeze({ source: 'NATIVE_COLLABORATION',
      capabilities: nativeObservationCapabilities, restrictionsVerified: false,
      supervisionMode: 'OBSERVE_ONLY',
      hostTelemetryFile: null, priceCardFile: null });
  }
}

export function discoverTeamRuntime(teamWorkspace) {
  if (!text(teamWorkspace) || !isAbsolute(teamWorkspace) || !existsSync(teamWorkspace)) {
    fail('TEAM_RUNTIME_NOT_FOUND');
  }
  const workspace = realpathSync(teamWorkspace);
  if (!statSync(workspace).isDirectory() || lstatSync(teamWorkspace).isSymbolicLink()) fail('TEAM_RUNTIME_NOT_FOUND');
  const registrationDir = resolve(join(workspace, '.mtm'));
  if (!existsSync(registrationDir) || lstatSync(registrationDir).isSymbolicLink() ||
      !statSync(registrationDir).isDirectory()) fail('TEAM_RUNTIME_NOT_FOUND');
  const manifestPath = resolve(join(registrationDir, 'team-runtime.json'));
  const rel = relative(workspace, manifestPath);
  if (rel.startsWith('..') || isAbsolute(rel) || !existsSync(manifestPath) ||
      lstatSync(manifestPath).isSymbolicLink() || statSync(manifestPath).size > 64 * 1024) fail('TEAM_RUNTIME_NOT_FOUND');
  let value;
  try { value = JSON.parse(readFileSync(manifestPath, 'utf8')); }
  catch { fail('INCOMPATIBLE_TEAM_RUNTIME'); }
  return { manifestPath, manifest: validateTeamRuntimeManifest(value) };
}

export function validateHostControlPlane(value) {
  const base = exact(value, ['schemaVersion', 'playerCapabilities', 'teamRestrictions']);
  const streamed = exact(value, ['schemaVersion', 'playerCapabilities', 'teamRestrictions',
    'telemetryFile', 'priceCardFile']);
  if ((!base && !streamed) ||
      value.schemaVersion !== 1 || !Array.isArray(value.playerCapabilities) ||
      !Array.isArray(value.teamRestrictions) ||
      requiredPlayerMonitoring.some(item => !value.playerCapabilities.includes(item)) ||
      (streamed && (!text(value.telemetryFile) || !isAbsolute(value.telemetryFile) ||
        !text(value.priceCardFile) || !isAbsolute(value.priceCardFile)))) {
    fail('HOST_CONTROL_PLANE_UNAVAILABLE');
  }
  return Object.freeze(value);
}

function validatePlayerSession(session, manifest) {
  validateTeamRuntimeManifest(manifest);
  const sessionKeys = ['status', 'sensitive', 'persistence', 'playerSessionId', 'teamWorkspace',
    'challengeWorkspace', 'teamSdlcPackWorkspace', 'teamRuntime',
    'gameMasterApiUrl', 'gameMasterPlayerSecret', 'schemaVersion', 'matchId', 'teamUuid', 'teamId', 'teamName', 'username',
    'password', 'apiKeySecret', 'apiKeyScopes', 'gameMasterUrl', 'matchPageUrl', 'repository',
    'preparationComplete', 'teamInfoRequestId', 'allowedApiOrigins'];
  if (Object.hasOwn(session ?? {}, 'codexThreadId')) sessionKeys.push('codexThreadId');
  for (const key of ['matchesWorkspace', 'checkoutRoot', 'matchRoot']) {
    if (Object.hasOwn(session ?? {}, key)) sessionKeys.push(key);
  }
  if (!exact(session, sessionKeys) ||
      (Object.hasOwn(session, 'codexThreadId') && session.codexThreadId !== null &&
        !(typeof session.codexThreadId === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(session.codexThreadId))) ||
      session.status !== 'READY' || session.sensitive !== true ||
      session.persistence !== 'PLAYER_CONVERSATION_ONLY' || !id(session.playerSessionId) ||
      !text(session.gameMasterPlayerSecret, 16_384) ||
      !id(session.matchId) || !id(session.teamId) || !id(session.teamInfoRequestId) ||
      !exact(session.teamRuntime, ['action', 'runtime']) || session.teamRuntime.action !== 'VERIFIED' ||
      !exact(session.teamRuntime.runtime,
        ['status', 'name', 'version', 'protocolVersion', 'rootAgentType', 'teamId', 'teamName']) ||
      session.teamRuntime.runtime.status !== 'READY' ||
      session.teamRuntime.runtime.protocolVersion !== manifest.protocolVersion ||
      session.teamRuntime.runtime.rootAgentType !== 'team_orchestrator' ||
      !id(session.teamRuntime.runtime.teamId) || !text(session.teamRuntime.runtime.teamName, 160) ||
      session.teamRuntime.runtime.teamId.toLowerCase() !== session.teamId.toLowerCase()) {
    fail('INVALID_PLAYER_SESSION');
  }
  matchWorkspacePaths(session);
  if (session.matchesWorkspace && resolve(session.matchesWorkspace) !== resolve(session.challengeWorkspace)) fail('INVALID_PLAYER_SESSION');
  return session;
}

const taskName = session =>
  `team_orchestrator_${session.playerSessionId.replace(/[^a-zA-Z0-9]/g, '').slice(0, 12)}`.toLowerCase();
const restartTaskName = (session, attempt) => `${taskName(session)}_r${attempt}`;
const credentialIntegrity = secret => Object.freeze({
  apiKeyLength: Buffer.byteLength(secret, 'utf8'),
  apiKeySha256: createHash('sha256').update(secret, 'utf8').digest('hex')
});

const authorizedApiOperations = Object.freeze([
  Object.freeze({ operation: 'START_WORK', method: 'POST',
    pathTemplate: '/api/v1/matches/{matchUuid}/backlogs/start' }),
  Object.freeze({ operation: 'SUBMIT', method: 'POST',
    pathTemplate: '/api/v1/matches/{matchUuid}/submissions' })
]);

/**
 * Describes expected match activity for monitoring. It does not grant, narrow, or
 * override Codex task permissions. The team root inherits the active task policy.
 */
function hostAuthorizationGrant(session) {
  return Object.freeze({ schemaVersion: 1, type: 'MTM_HOST_AUTHORIZATION',
    authority: 'ORGANIZER_DECLARED_SCOPE', playerSessionId: session.playerSessionId,
    matchId: session.matchId, inheritance: 'ORCHESTRATOR_TREE',
    enforcement: 'TEAM_RESPONSIBILITY', taskPermissions: 'INHERIT',
    credentialUse: 'TEAM_BEARER_TO_GAME_MASTER_ONLY',
    allowedOrigins: Object.freeze([...session.allowedApiOrigins]),
    apiScopes: Object.freeze([...session.apiKeyScopes]),
    apiOperations: authorizedApiOperations,
    gitOperations: Object.freeze(['COMMIT_ASSIGNED_REPOSITORY', 'PUSH_ASSIGNED_BRANCH']) });
}

function preparationHostAuthorizationGrant(session, repository) {
  return Object.freeze({ schemaVersion: 1, type: 'MTM_PREPARATION_HOST_AUTHORIZATION',
    authority: 'ORGANIZER_DECLARED_SCOPE', playerSessionId: session.playerSessionId,
    matchId: session.matchId, inheritance: 'ORCHESTRATOR_TREE',
    enforcement: 'TEAM_RESPONSIBILITY', taskPermissions: 'INHERIT',
    credentialUse: 'HOST_GIT_CREDENTIAL_TO_ASSIGNED_REPOSITORY_ONLY',
    repository: Object.freeze({ ...repository }),
    gitOperations: Object.freeze([
      'READ_ASSIGNED_REPOSITORY', 'CLONE_ASSIGNED_BRANCH', 'FETCH_ASSIGNED_BRANCH'
    ]) });
}

/** Creates the first root turn with repository and pre-game portal access, but no team API key. */
export function prepareNativePreparation(session, manifest, host, requestId, options = {}) {
  validatePlayerSession(session, manifest);
  if (!id(requestId)) fail('INVALID_PREPARATION_REQUEST');
  const hostShape = exact(host, ['availableAgentTypes']) ||
    exact(host, ['availableAgentTypes', 'controlPlane']);
  if (!hostShape ||
      !Array.isArray(host.availableAgentTypes) || !host.availableAgentTypes.includes('team_orchestrator')) {
    fail('TEAM_ORCHESTRATOR_NOT_LOADED');
  }
  const observer = observationMode(host.controlPlane);
  let repository;
  try { repository = repositoryFromSession(session, options); }
  catch { fail('PREPARATION_REPOSITORY_UNAVAILABLE'); }
  const progressLogFile = initializeProgressLog(session.teamSdlcPackWorkspace, session.matchId,
    session.playerSessionId);
  const matchOrigin = new URL(session.matchPageUrl).origin;
  const descriptor = Object.freeze({ schemaVersion: 1, type: 'PREPARE_GAME', requestId,
    ...matchWorkspacePaths(session),
    playerSessionId: session.playerSessionId, teamId: session.teamId,
    teamName: session.teamName, matchId: session.matchId,
    progressLogFile, teamSdlcPackWorkspace: session.teamSdlcPackWorkspace,
    username: session.username, password: session.password,
    matchPageUrl: session.matchPageUrl, allowedMatchOrigins: [matchOrigin], repository,
    hostAuthorization: preparationHostAuthorizationGrant(session, repository) });
  const root = taskName(session);
  return Object.freeze({
    spawn: Object.freeze({ task_name: root, agent_type: 'team_orchestrator',
      fork_turns: 'none', message: `PREPARE_GAME_V2\n${JSON.stringify(descriptor)}` }),
    receipt: Object.freeze({ schemaVersion: 1, type: 'PREPARATION_SPAWN_REQUESTED',
      requestId, playerSessionId: session.playerSessionId, teamId: session.teamId,
      rootAgentType: 'team_orchestrator', forkTurns: 'none',
      monitoringSource: observer.source,
      restrictionsVerified: observer.restrictionsVerified,
      supervisionMode: observer.supervisionMode,
      executionPermissions: 'PARENT_TASK_INHERITED' }),
    observer,
    dashboard: Object.freeze({ progressLogFile, matchId: session.matchId,
      teamSdlcPackWorkspace: session.teamSdlcPackWorkspace, playerSessionId: session.playerSessionId,
      teamId: session.teamId, teamName: session.teamName,
      hostTelemetryFile: observer.hostTelemetryFile, priceCardFile: observer.priceCardFile }),
    repository,
    target: root
  });
}

/** Continues the authenticated prepared root and releases active-game API authority. */
export function prepareNativeGameStart(session, manifest, preparationReceipt, requestId) {
  validatePlayerSession(session, manifest);
  if (!id(requestId)) fail('INVALID_GAME_START_REQUEST');
  let preparation;
  try { preparation = validatePreparationReceipt(preparationReceipt, session); }
  catch { fail('INVALID_PREPARATION_RECEIPT'); }
  const descriptor = Object.freeze({
    schemaVersion: 1, type: 'START_GAME', requestId,
    playerSessionId: session.playerSessionId,
    teamUuid: session.teamUuid, teamId: session.teamId, teamName: session.teamName,
    matchId: session.matchId, gameMasterApiUrl: session.gameMasterApiUrl,
    apiKeySecret: session.apiKeySecret, ...credentialIntegrity(session.apiKeySecret),
    apiKeyScopes: session.apiKeyScopes,
    gameMasterUrl: session.gameMasterUrl, matchPageUrl: session.matchPageUrl,
    teamInfoRequestId: session.teamInfoRequestId,
    allowedApiOrigins: session.allowedApiOrigins,
    teamWorkspace: session.teamWorkspace,
    ...matchWorkspacePaths(session), challengeWorkspace: session.challengeWorkspace,
    teamSdlcPackWorkspace: session.teamSdlcPackWorkspace,
    progressLogFile: resolve(session.teamSdlcPackWorkspace, 'logs', `${session.matchId}.progress.ndjson`),
    environmentRevision: preparation.environmentRevision,
    hostAuthorization: hostAuthorizationGrant(session),
    gitPolicy: Object.freeze({ commitAssignedRepository: true,
      pushAssignedRepository: true, forcePush: false, mutateRemote: false,
      authentication: 'ASK_USER' })
  });
  const root = taskName(session);
  return Object.freeze({
    followup: Object.freeze({ target: root,
      message: `START_GAME_V4\n${JSON.stringify(descriptor)}` }),
    receipt: Object.freeze({ schemaVersion: 1, type: 'GAME_START_REQUESTED', requestId,
      preparationRequestId: preparation.preparationRequestId,
      playerSessionId: session.playerSessionId, teamId: session.teamId,
      rootAgentType: 'team_orchestrator', target: root })
  });
}

/**
 * Creates a fresh root in the same live Player process. Sensitive restart data stays
 * inside the spawn message and is never returned in the public receipt.
 */
export function prepareNativeOrchestratorRestart(session, manifest, {
  attempt, reason, phase, environmentRevision = null, preparationRequestId = null
}) {
  validatePlayerSession(session, manifest);
  if (!Number.isSafeInteger(attempt) || attempt < 1 || attempt > 10_000 || !id(reason) ||
      !['PREPARATION', 'GAME'].includes(phase) ||
      (phase === 'GAME' && (typeof environmentRevision !== 'string' ||
        !/^[a-f0-9]{40,64}$/.test(environmentRevision))) ||
      (phase === 'PREPARATION' && environmentRevision !== null)) {
    fail('INVALID_ORCHESTRATOR_RESTART');
  }
  if (preparationRequestId !== null && !id(preparationRequestId)) fail('INVALID_ORCHESTRATOR_RESTART');
  const requestId = phase === 'PREPARATION' && preparationRequestId ? preparationRequestId :
    `restart-${session.playerSessionId}-${attempt}`;
  const base = {
    schemaVersion: 1, type: phase === 'GAME' ? 'RESUME_GAME' : 'RESUME_PREPARATION',
    requestId, playerSessionId: session.playerSessionId, restartAttempt: attempt,
    reason, teamUuid: session.teamUuid, teamId: session.teamId, teamName: session.teamName,
    matchId: session.matchId, username: session.username, password: session.password,
    gameMasterUrl: session.gameMasterUrl, matchPageUrl: session.matchPageUrl,
    allowedMatchOrigins: [new URL(session.matchPageUrl).origin],
    repository: repositoryFromSession(session), teamWorkspace: session.teamWorkspace,
    ...matchWorkspacePaths(session), challengeWorkspace: session.challengeWorkspace,
    teamSdlcPackWorkspace: session.teamSdlcPackWorkspace,
    progressLogFile: resolve(session.teamSdlcPackWorkspace, 'logs', `${session.matchId}.progress.ndjson`),
    environmentRevision
  };
  const descriptor = phase === 'GAME' ? Object.freeze({ ...base,
    gameMasterApiUrl: session.gameMasterApiUrl, apiKeySecret: session.apiKeySecret,
    ...credentialIntegrity(session.apiKeySecret),
    apiKeyScopes: session.apiKeyScopes, teamInfoRequestId: session.teamInfoRequestId,
    allowedApiOrigins: session.allowedApiOrigins,
    hostAuthorization: hostAuthorizationGrant(session),
    gitPolicy: Object.freeze({ commitAssignedRepository: true,
      pushAssignedRepository: true, forcePush: false, mutateRemote: false,
      authentication: 'ASK_USER' }) }) : Object.freeze(base);
  const root = restartTaskName(session, attempt);
  return Object.freeze({
    spawn: Object.freeze({ task_name: root, agent_type: 'team_orchestrator',
      fork_turns: 'none', message: `${phase === 'GAME' ? 'RESUME_GAME_V1' :
        'RESUME_PREPARATION_V1'}\n${JSON.stringify(descriptor)}` }),
    receipt: Object.freeze({ schemaVersion: 1, type: 'ORCHESTRATOR_RESTART_REQUESTED',
      requestId, playerSessionId: session.playerSessionId, matchId: session.matchId,
      phase, attempt, reason, rootAgentType: 'team_orchestrator', target: root })
  });
}
