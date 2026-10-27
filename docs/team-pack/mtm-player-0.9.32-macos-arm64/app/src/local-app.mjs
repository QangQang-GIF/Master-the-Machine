import { browseTeamWorkspace } from './workspace-picker.mjs';
import { codexStandardPriceCard } from './codex-price-card.mjs';
import { createServer } from 'node:http';
import { listCodexModels, selectModelPolicy } from './model-policy.mjs';
import { EventEmitter } from 'node:events';
import { createHash, randomBytes } from 'node:crypto';
import { isAbsolute, join, resolve } from 'node:path';
import { homedir } from 'node:os';
import { existsSync, readFileSync, mkdirSync, statSync, writeFileSync, renameSync } from 'node:fs';
import { openLocalAppStore } from './local-app-store.mjs';
import { roundName, roundRoute, roundSummary, listMatchRounds, archiveRound,
  readArchivedRound, clearRoundMetadata } from './match-rounds.mjs';
import { createLocalAppLifecycle } from './local-app-lifecycle.mjs';
import { initializeMatch, JsonBootstrapConfigStore, LegacyCredentialFileStore, DEFAULT_GAME_MASTER_API_URL,
  validateTeamWorkspaceLayout } from './bootstrap.mjs';
import { verifyTeamRuntime } from './team-install.mjs';
import { discoverTeamRuntime } from './team-runtime.mjs';
import { registerCurrentCodexTask } from './codex-session-registry.mjs';
import { createCodexDashboardObserver } from './codex-dashboard-observer.mjs';
import { discoverCodexTranscripts } from './codex-transcript-discovery.mjs';
import { readCodexTranscriptView } from './codex-transcript-view.mjs';
import { startProgressDashboard, recordPlayerPreparationComplete, dashboardDocument,
  readEnvironmentReadyProgress } from './progress.mjs';
import { fetchMatchStatus, persistMatchStatus } from './match-status.mjs';
import { page } from './init-web.mjs';
import { managementDocument } from './manage-web.mjs';
import { loadRestartCheckpoint, bindRestartSession } from './restart-checkpoint.mjs';
import { queueCodexHostMessage } from './codex-host-notifier.mjs';
import { createCliSupervisor, readCliTranscriptView } from './cli-supervisor.mjs';
import { createAppServerSupervisor } from './codex-app-server.mjs';
import { openDiagnosticJournal } from './diagnostic-journal.mjs';
import { createDashboardStreamingClient } from './dashboard-streaming.mjs';
import { discoverCodexExecutable, resolveCodexExecutable } from './codex-cli.mjs';
import { matchWorkspacePaths } from './workspace-paths.mjs';

const fail = code => { throw Object.assign(new Error(code), { code }); };
const safeCode = error => /^[A-Z][A-Z0-9_]{0,100}$/.test(error?.code ?? '') ? error.code : 'LOCAL_APP_REQUEST_FAILED';
export const localAppDirectory = (dataDirectory, workspace) => join(dataDirectory, 'applications',
  createHash('sha256').update(process.platform === 'win32' ? resolve(workspace).toLowerCase() : resolve(workspace)).digest('hex'));
const version = JSON.parse(readFileSync(new URL('../package.json', import.meta.url))).version;
const terminal = value => ['FINISHED', 'CANCELLED'].includes(value);

export async function startLocalApp({ workspace, dataDirectory, codexThreadId = null, mode = 'ATTACH',
  clearChallenge = false, stopExisting = false, listenPort = 54321 }, dependencies = {}) {
  mkdirSync(dataDirectory, { recursive: true });
  if (!workspace) { try { workspace = JSON.parse(readFileSync(join(dataDirectory, 'player-workspace.json'), 'utf8')).workspace; } catch {} }
  const emptyWorkspace = !workspace;
  workspace ??= dataDirectory;
  let store = openLocalAppStore({ directory: localAppDirectory(dataDirectory, workspace), workspace });
  let diagnostics = openDiagnosticJournal(store.directory);
  let state = store.load() ?? { schemaVersion: 1, port: 0, controlToken: randomBytes(32).toString('hex'),
    workspace: resolve(workspace), match: null };
  if (!Number.isInteger(listenPort) || listenPort < 0 || listenPort > 65535) fail('INVALID_LOCAL_APP_PORT');
  if (listenPort !== 0 || !state.port) state.port = listenPort;
  state.rounds ??= [];
  if (state.shutdownIntent?.id === state.shutdownReceipt?.shutdownId && state.shutdownIntent) {
    if (state.shutdownIntent.resumeOnLaunch && state.match && !dependencies.queueCodexMessage) state.match.operatorStopped = false;
    delete state.shutdownIntent;
  }
  if (state.match) { state.match.roundId ??= 'round01'; state.match.startedAt ??= null; }
  let dashboard = null, streamingClient = null, lifecycle = null, registry = null, cliSupervisor = null, timer = null, closing = false;
  let streamingStatus = Object.freeze({ enabled: false, status: 'DISABLED', lastAttemptAt: null,
    lastSuccessAt: null, lastHttpStatus: null, revision: 0, lastError: null,
    lastSnapshotBytes: null, lastUploadBytes: null });
  let cliAttached = false, cliDispatching = false, chatDispatching = false;
  let modelCatalog = null, pickerOpen = false,
    selectedCodexExecutable = discoverCodexExecutable() ?? state.codexExecutable ?? resolveCodexExecutable();
  let hostThreadId = codexThreadId;
  const changes = new EventEmitter();
  let queue = Promise.resolve(), durabilityError = false;
  const serial = fn => { const result = queue.then(() => { if (durabilityError) fail('LOCAL_APP_STORAGE_UNAVAILABLE'); return fn(); }); queue = result.catch(() => {}); return result; };
  const persist = () => { try { const result = store.save(state); changes.emit('change'); return result; } catch (error) { durabilityError = true; throw error; } };
  const eraseCredentials = () => {
    if (!state.match || state.match.credentialsErased) return;
    state.match.session = { ...state.match.session };
    for (const key of ['password','username','apiKeySecret','gameMasterPlayerSecret']) delete state.match.session[key];
    state.match.preparation = null;
    for (const op of state.match.operations ?? []) if (op.kind !== 'STOP') {
      delete op.frame;
      if (op.state === 'PENDING') op.state = 'CANCELLED';
    }
    state.match.credentialsErased = false;
    try { store.compact(state); state.match.credentialsErased = true; persist(); }
    catch (error) { durabilityError = true; throw error; }
  };
  const init = dependencies.initializeMatch ?? initializeMatch;
  const fetchStatus = dependencies.fetchMatchStatus ?? fetchMatchStatus;
  const queueHostMessage = dependencies.queueCodexMessage ?? queueCodexHostMessage;
  const cliFactory = dependencies.createCliSupervisor ?? (options => options.provider === 'codex' ?
    createAppServerSupervisor(options) : createCliSupervisor(options));
  // Tests and older embedders that explicitly inject the host notifier retain
  // the legacy adapter. Installed Player instances default to the CLI owner.
  const legacyHostAdapter = Boolean(dependencies.queueCodexMessage);
  const configStore = new JsonBootstrapConfigStore(dataDirectory);
  const credentialStore = new LegacyCredentialFileStore(join(dataDirectory, 'credentials'));
  let layout = emptyWorkspace ? null : dependencies.layout ?? validateTeamWorkspaceLayout(workspace, { configDir: dataDirectory });
  let installed = layout ? dependencies.installed ?? verifyTeamRuntime(layout) : null;
  let pack = installed ? { teamId: installed.runtime.teamId, teamName: installed.runtime.teamName, version: installed.runtime.version } : { teamId: '', teamName: '', version: '' };
  let owner = store.acquireLock();
  diagnostics.append('LOCAL_APP_STARTED', { pid: process.pid, ownerId: owner.ownerId, version });
  const publicMatch = () => lifecycle ? { ...lifecycle.publicState(), roundId: state.match.roundId,
    startedAt: state.match.startedAt, cli: cliSupervisor?.snapshot() ?? null,
    chatQueue: (state.match.chatQueue ?? []).map((item, index) => ({ id: item.id, message: item.message,
      createdAt: item.createdAt, position: index + 1 })),
    diagnostics: (() => { const value = diagnostics.snapshot(1); return { status: value.status, head: value.head }; })() } : null;
  const hasLiveHost = match => Boolean(match?.hostSeenAt && Date.now() - match.hostSeenAt < 20000);
  const hasActiveSession = match => Boolean(match && (hasLiveHost(match) || match.agentState !== 'STOPPED' ||
    match.operations?.some(op => ['PENDING', 'DELIVERED'].includes(op.state))));
  const beginNewRound = async message => {
    if (hasActiveSession(state.match)) {
      if (message.stopExisting !== true) fail('ACTIVE_SESSION_STOP_CONFIRMATION_REQUIRED');
      if (legacyHostAdapter) {
        if (!message.codexThreadId || typeof message.codexThreadId !== 'string') fail('CODEX_THREAD_ID_REQUIRED');
        hostThreadId = message.codexThreadId;
        state.match.hostOwnerThreadId = hostThreadId;
      }
      state.match.stopForNewRound = true;
      const attached = await lifecycle.handle({ schemaVersion: 1, type: 'HOST_ATTACH' });
      await lifecycle.handle({ schemaVersion: 1, type: 'APP_STOP', hostGeneration: attached.hostGeneration });
      await dispatchCliIfNeeded();
      if (state.match.agentState === 'STOPPED' &&
          !state.match.operations.some(op => ['PENDING','DELIVERED'].includes(op.state))) {
        state.match.stopForNewRound = false; state.newRoundPending = true;
      }
    } else state.newRoundPending = true;
    clearChallenge = message.clearChallenge === true;
    persist();
    return { status: state.newRoundPending ? 'INIT_FORM_READY' : 'STOPPING_EXISTING_SESSION' };
  };
  const status = () => ({ schemaVersion: 1, status: 'LOCAL_APP_READY', version, ownerId: owner.ownerId,
    initialized: Boolean(state.match), storageStatus: durabilityError ? 'UNAVAILABLE' : 'HEALTHY', match: publicMatch() });
  async function streamingSnapshot() {
    const value = dashboard.snapshot();
    const cli = cliSupervisor?.snapshot();
    const tasks = [];
    if (cli?.sessionId) tasks.push({ sessionId: cli.sessionId, provider: cli.provider,
      role: 'ORCHESTRATOR', agentPath: '/team_orchestrator' });
    for (const agent of cli?.subagents ?? []) if (agent.sessionId &&
        !tasks.some(task => task.sessionId === agent.sessionId)) tasks.push({ sessionId: agent.sessionId,
      provider: cli.provider, role: 'SUBAGENT', agentPath: agent.name });
    const transcripts = await Promise.all(tasks.map(async task => {
      try {
        const transcript = cliSupervisor?.transcript ? await cliSupervisor.transcript(task.sessionId) :
          readCliTranscriptView(state.match.cliRuntime);
        return { ...task, status: transcript.status ?? 'READY', truncated: transcript.truncated === true,
          items: Array.isArray(transcript.items) ? transcript.items.slice(-200) : [] };
      } catch { return { ...task, status: 'UNAVAILABLE', truncated: false, items: [] }; }
    }));
    return { ...value, transcripts };
  }
  const setupMatch = async fresh => {
    const match = state.match;
    if (!match) return;
    streamingClient?.close(); streamingClient = null;
    fresh ||= match.freshPending === true;
    if (fresh) clearRoundMetadata(match.session);
    const host = registerCurrentCodexTask(match.session, { dataDirectory,
      env: hostThreadId ? { CODEX_THREAD_ID: hostThreadId } : {} });
    match.session = host.session; registry = host.registry;
    lifecycle = createLocalAppLifecycle(match, persist, dependencies.lifecycleOptions);
    if (!fresh) lifecycle.recover();
    if (!fresh && !legacyHostAdapter && !terminal(match.officialStatus) &&
        (match.agentState !== 'STOPPED' || match.recoveryRequired ||
          match.operations?.some(op => ['PENDING', 'DELIVERED'].includes(op.state)))) {
      lifecycle.recoverOwnedTransport();
      diagnostics.append('RUNTIME_RECOVERY_PENDING', { matchId: match.session.matchId,
        roundId: match.roundId, phase: match.preparedReceipt ? 'GAME' : 'PREPARATION' });
    }
    match.cliRuntime ??= {};
    const cliProvider = match.cliRuntime?.provider ?? dependencies.cliProvider ??
      process.env.MTM_PLAYER_CLI_PROVIDER ?? 'codex';
    cliSupervisor = legacyHostAdapter ? null : cliFactory({ workspace: state.workspace,
      directory: join(store.directory, 'cli', match.session.matchId, match.roundId),
      runtime: match.cliRuntime, save: persist, provider: cliProvider,
      onSession: cliSessionId => {
        if (cliProvider !== 'codex' || !hostThreadId || !registry) return;
        try { registry.registerAgent({ codexThreadId: cliSessionId, role: 'ORCHESTRATOR', parentThreadId: hostThreadId,
          agentPath: `/${match.rootTarget ?? 'team_orchestrator'}` }); } catch {}
      },
      onExit: outcome => {
        if (outcome.kind === 'CHAT') {
          void serial(async () => { await dispatchQueuedChat(); });
          return;
        }
        if (outcome.exitCode === 0 && outcome.kind === 'PREPARE') return;
        void serial(async () => {
          if (!lifecycle || state.match !== match || match.operations?.some(op => ['PENDING','DELIVERED'].includes(op.state))) return;
          try { await lifecycle.handle({ schemaVersion: 1, type: 'ORCHESTRATOR_EXITED',
            hostGeneration: match.cliHostGeneration }); await dispatchCliIfNeeded(); } catch {}
        });
      } });
    cliAttached = false;
    const cliState = cliSupervisor?.snapshot();
    if (match.agentState !== 'STOPPED' && !match.operations?.some(op => ['PENDING','DELIVERED'].includes(op.state)) &&
        (['INTERRUPTED','FAILED'].includes(cliState?.status) ||
          (cliState?.status === 'IDLE' && match.cliRuntime?.operationKind === 'GAME'))) {
      const attached = await lifecycle.handle({ schemaVersion: 1, type: 'HOST_ATTACH' });
      match.cliHostGeneration = attached.hostGeneration; cliAttached = true;
      await lifecycle.handle({ schemaVersion: 1, type: 'ORCHESTRATOR_EXITED',
        hostGeneration: match.cliHostGeneration });
    }
    if (match.preparedReceipt) recordPlayerPreparationComplete(match.session, match.preparedReceipt);
    dashboard = await startProgressDashboard({ teamSdlcPackWorkspace: match.session.teamSdlcPackWorkspace,
      matchId: match.session.matchId, playerSessionId: match.session.playerSessionId,
      teamId: match.session.teamId, teamName: match.session.teamName, fresh,
      localApplicationState: publicMatch,
      priceCard: codexStandardPriceCard,
      modelPolicy: match.modelPolicy ?? null, modelEvidence: match.modelEvidence ?? [],
      onModelEvidence: evidence => { match.modelEvidence = evidence; persist(); },
      codexObserver: createCodexDashboardObserver(registry, {
        lifecycleState: () => ({ rootTarget: state.match?.rootTarget, agentState: state.match?.agentState }), sessionsDirectory:
        join(process.env.CODEX_HOME || join(homedir(), '.codex'), 'sessions') }) });
    if (match.streamingEnabled !== false && match.session.gameMasterPlayerSecret) {
      const streamingFactory = dependencies.createDashboardStreamingClient ?? createDashboardStreamingClient;
      streamingClient = streamingFactory({ session: match.session, roundId: match.roundId,
        getSnapshot: streamingSnapshot, onStatus: value => { streamingStatus = value; } });
    } else {
      streamingStatus = Object.freeze({ enabled: match.streamingEnabled !== false, status:
        match.streamingEnabled === false ? 'DISABLED' : 'UNAVAILABLE', lastAttemptAt: null,
        lastSuccessAt: null, lastHttpStatus: null, revision: 0,
        lastError: match.streamingEnabled === false ? null : 'CREDENTIAL_UNAVAILABLE',
        lastSnapshotBytes: null, lastUploadBytes: null });
    }
    match.freshPending = false;
    persist();
  };
  const notifyHostIfNeeded = async () => {
    const match = state.match;
    const operation = match?.operations?.find(op => op.state === 'PENDING');
    if (!operation || !hostThreadId) return;
    const previous = match.hostNotification;
    if (previous?.operationId === operation.id && (previous.status === 'QUEUED' ||
        (previous.nextRetryAt ?? 0) > Date.now())) return;
    const attempts = previous?.operationId === operation.id ? (previous.attempts ?? 0) + 1 : 1;
    match.hostNotification = { operationId: operation.id, status: 'QUEUING', attempts,
      updatedAt: new Date().toISOString() };
    persist();
    try {
      await queueHostMessage({ codexThreadId: hostThreadId, workspace: state.workspace,
        operation: { id: operation.id, kind: operation.kind } });
      if (state.match === match && match.hostNotification?.operationId === operation.id) {
        match.hostNotification = { operationId: operation.id, status: 'QUEUED', attempts,
          updatedAt: new Date().toISOString() }; persist();
      }
    } catch {
      if (state.match === match && match.hostNotification?.operationId === operation.id) {
        match.hostNotification = { operationId: operation.id, status: 'RETRY_WAIT', attempts,
          nextRetryAt: Date.now() + 10000, updatedAt: new Date().toISOString() }; persist();
      }
    }
  };
  const dispatchCliIfNeeded = async () => {
    if (legacyHostAdapter) return notifyHostIfNeeded();
    const match = state.match;
    const operation = match?.operations?.find(op => op.state === 'PENDING');
    if (!operation || !cliSupervisor || cliDispatching) return;
    cliDispatching = true;
    diagnostics.append('CLI_OPERATION_DISPATCHING', { operationId: operation.id, kind: operation.kind,
      provider: match.cliRuntime?.provider ?? 'unknown' });
    try {
      if (!cliAttached || match.cliHostGeneration !== match.hostGeneration) {
        const attached = await lifecycle.handle({ schemaVersion: 1, type: 'HOST_ATTACH' });
        match.cliHostGeneration = attached.hostGeneration; cliAttached = true;
      }
      const delivered = await lifecycle.handle({ schemaVersion: 1, type: 'HOST_OPERATION_GET',
        operationId: operation.id, hostGeneration: match.cliHostGeneration });
      const result = operation.kind === 'STOP' ? await cliSupervisor.stop() :
        await cliSupervisor.dispatch(operation, delivered.frame);
      await lifecycle.handle({ schemaVersion: 1, type: 'HOST_OPERATION_ACK', operationId: operation.id,
        hostGeneration: match.cliHostGeneration, result });
      diagnostics.append('CLI_OPERATION_ACKNOWLEDGED', { operationId: operation.id, kind: operation.kind,
        sessionId: result.sessionId ?? null });
      match.cliRuntime.lastError = null; persist();
    } catch (error) {
      match.cliRuntime ??= {};
      match.cliRuntime.lastError = safeCode(error); match.cliRuntime.lastErrorAt = new Date().toISOString();
      const failedRuntime = cliSupervisor?.snapshot?.() ?? {};
      diagnostics.append('CLI_OPERATION_FAILED', { operationId: operation.id, kind: operation.kind,
        code: match.cliRuntime.lastError, exitCode: failedRuntime.lastExitCode,
        signal: failedRuntime.lastExitSignal, summary: failedRuntime.lastStderr });
      const delivered = match.operations?.find(op => op.id === operation.id);
      if (delivered?.state === 'DELIVERED' && !match.cliRuntime.dispatchUncertain) {
        try { await lifecycle.handle({ schemaVersion: 1, type: 'HOST_OPERATION_RECONCILE',
          operationId: operation.id, hostGeneration: match.cliHostGeneration, outcome: 'NOT_EXECUTED' }); } catch {}
      }
      persist();
    } finally { cliDispatching = false; }
  };
  const dispatchQueuedChat = async () => {
    const match = state.match;
    if (!match || !cliSupervisor?.chat || chatDispatching || terminal(match.officialStatus) ||
        match.agentState === 'STOPPED' || match.operatorStopped) return null;
    const cliState = cliSupervisor.snapshot();
    if (['RUNNING', 'WAITING_APPROVAL', 'STARTING'].includes(cliState.status)) return null;
    const queued = match.chatQueue?.shift();
    if (!queued) return null;
    chatDispatching = true; persist();
    try {
      const result = await cliSupervisor.chat(queued);
      diagnostics.append('ORCHESTRATOR_CHAT_SENT', { messageId: queued.id, queuedAt: queued.createdAt,
        sessionId: result.sessionId ?? match.cliRuntime?.sessionId ?? null });
      return result;
    } catch (error) {
      match.chatQueue.unshift(queued); match.cliRuntime ??= {};
      match.cliRuntime.lastError = safeCode(error); match.cliRuntime.lastErrorAt = new Date().toISOString();
      diagnostics.append('ORCHESTRATOR_CHAT_FAILED', { messageId: queued.id, code: match.cliRuntime.lastError });
      persist(); throw error;
    } finally { chatDispatching = false; }
  };
  const submitOrchestratorChat = async input => {
    const match = state.match;
    if (!match || !cliSupervisor?.chat) fail('ORCHESTRATOR_CHAT_UNAVAILABLE');
    if (terminal(match.officialStatus)) fail('MATCH_ALREADY_ENDED');
    if (match.agentState === 'STOPPED' || match.operatorStopped) fail('ORCHESTRATOR_STOPPED');
    const message = input?.message;
    if (typeof message !== 'string' || message.trim().length === 0 || message.length > 8000) {
      fail('INVALID_ORCHESTRATOR_CHAT_MESSAGE');
    }
    if (!match.cliRuntime?.sessionId) fail('ORCHESTRATOR_SESSION_UNAVAILABLE');
    match.chatQueue ??= [];
    if (match.chatQueue.length >= 20) fail('ORCHESTRATOR_CHAT_QUEUE_FULL');
    const item = { id: `chat-${randomBytes(12).toString('hex')}`, message: message.trim(),
      createdAt: new Date().toISOString() };
    match.chatQueue.push(item); persist();
    const cliState = cliSupervisor.snapshot();
    if (chatDispatching || ['RUNNING', 'WAITING_APPROVAL', 'STARTING'].includes(cliState.status)) {
      diagnostics.append('ORCHESTRATOR_CHAT_QUEUED', { messageId: item.id, position: match.chatQueue.length });
      return { status: 'CHAT_QUEUED', messageId: item.id, position: match.chatQueue.length,
        queuedAt: item.createdAt };
    }
    const result = await dispatchQueuedChat();
    return result ?? { status: 'CHAT_QUEUED', messageId: item.id, position: match.chatQueue.length,
      queuedAt: item.createdAt };
  };
  const requestShutdown = async () => {
    state.newRoundPending = false;
    if (!state.shutdownIntent) {
      state.shutdownIntent = { id: randomBytes(16).toString('hex'), requestedAt: new Date().toISOString(),
        resumeOnLaunch: Boolean(state.match && !state.match.operatorStopped && !terminal(state.match.officialStatus)) };
      if (lifecycle) {
        const attached = await lifecycle.handle({ schemaVersion: 1, type: 'HOST_ATTACH' });
        await lifecycle.handle({ schemaVersion: 1, type: 'APP_STOP', hostGeneration: attached.hostGeneration });
      }
      persist(); await dispatchCliIfNeeded();
    }
    if (!legacyHostAdapter && (!state.match || (state.match.agentState === 'STOPPED' &&
        !state.match.operations.some(op => ['PENDING','DELIVERED'].includes(op.state))))) {
      state.shutdownReceipt = { schemaVersion: 1, status: 'LOCAL_APP_STOPPED', shutdownId: state.shutdownIntent.id,
        stoppedAt: new Date().toISOString(), agents: 'STOPPED', mcp: 'STOPPED' };
      persist(); return state.shutdownReceipt;
    }
    return { ...(lifecycle ? lifecycle.publicState() : { hostGeneration: 0, operations: [] }),
      schemaVersion: 1, status: 'HOST_SHUTDOWN_REQUIRED', shutdownId: state.shutdownIntent.id,
      teamWorkspace: state.workspace, rootTarget: state.match?.rootTarget ?? null,
      required: ['STOP_AGENT_TREE', 'STOP_OWNED_MCP', 'APP_SHUTDOWN_ACK'] };
  };
  const tokenUsage = snapshot => {
    if (!Array.isArray(snapshot?.modelUsage) || snapshot.modelUsage.length === 0) return null;
    return snapshot.modelUsage.reduce((total, row) => ({
      inputTokens: total.inputTokens + (Number.isSafeInteger(row.inputTokens) ? row.inputTokens : 0),
      cachedInputTokens: total.cachedInputTokens + (Number.isSafeInteger(row.cachedInputTokens) ? row.cachedInputTokens : 0),
      outputTokens: total.outputTokens + (Number.isSafeInteger(row.outputTokens) ? row.outputTokens : 0),
      reasoningTokens: total.reasoningTokens + (Number.isSafeInteger(row.reasoningTokens) ? row.reasoningTokens : 0)
    }), { inputTokens: 0, cachedInputTokens: 0, outputTokens: 0, reasoningTokens: 0 });
  };
  const managementStatus = async () => {
    const rows = [...state.rounds];
    if (state.match) {
      const current = roundSummary(state.match, state.port);
      const index = rows.findIndex(row => row.matchId === current.matchId && row.roundId === current.roundId);
      if (index < 0) rows.push(current); else rows[index] = current;
    }
    rows.sort((left, right) => {
      const rightStarted = Date.parse(right.startedAt ?? '') || 0;
      const leftStarted = Date.parse(left.startedAt ?? '') || 0;
      return rightStarted - leftStarted;
    });
    let liveSnapshot = null;
    if (dashboard) {
      try { const response = await fetch(dashboard.url + '/snapshot', { signal: AbortSignal.timeout(3000) });
        if (response.ok) liveSnapshot = await response.json(); } catch {}
    }
    const cliState = cliSupervisor?.snapshot() ?? null;
    const transcriptTasks = registry?.snapshot().registrations.map(item => ({ sessionId: item.codexThreadId,
      provider: 'codex', role: item.role, agentPath: item.agentPath })) ?? [];
    if (cliState?.sessionId && !transcriptTasks.some(item => item.sessionId === cliState.sessionId)) {
      transcriptTasks.unshift({ sessionId: cliState.sessionId, provider: cliState.provider,
        role: 'ORCHESTRATOR', agentPath: '/team_orchestrator' });
    }
    for (const agent of cliState?.subagents ?? []) if (!transcriptTasks.some(t => t.sessionId === agent.sessionId)) transcriptTasks.push({ sessionId: agent.sessionId, provider: 'codex', role: 'SUBAGENT', agentPath: agent.name });
    return { schemaVersion: 1, processStatus: closing ? 'STOPPING' : 'RUNNING', port: state.port,
      workspace: state.workspace, selectedWorkspace: installed ? state.workspace : null, shutdownStatus: state.shutdownIntent ? 'REQUESTED' : 'NONE',
      runningTeamWorkspace: state.match?.session && cliState?.pid && ['RUNNING','WAITING_APPROVAL','IDLE','STARTING'].includes(cliState.status) ? state.match.session.teamWorkspace : null,
      activeTeamPack: state.match?.session ? { teamId: state.match.session.teamId, teamName: state.match.session.teamName,
        version: state.match.session.teamRuntime?.runtime?.version ?? null } : null,
      activeMatchId: state.match?.session?.matchId ?? null, activeRoundId: state.match?.roundId ?? null,
      resumeAvailable: Boolean(state.match?.operatorStopped && !terminal(state.match.officialStatus)),
      resumeRequiresAuthentication: Boolean(state.match && (state.match.credentialExpired || state.match.credentialsErased || Date.now() > state.match.credentialExpiresAt)),
      transcriptTasks, cliSupervisor: cliState, diagnostics: diagnostics.snapshot(50), streaming: streamingStatus,
      rounds: rows.map(row => { let snapshot = null;
        const current = state.match?.session?.matchId === row.matchId && state.match?.roundId === row.roundId;
        if (current) snapshot = liveSnapshot;
        else { try { snapshot = JSON.parse(readArchivedRound(store.directory, row, 'snapshot.json')); } catch {} }
        return { ...row,
        durationMs: row.startedAt ? Math.max(0, new Date(row.endedAt ?? Date.now()).getTime() - new Date(row.startedAt).getTime()) : null,
        tokenUsage: current ? (cliState?.tokenUsage ?? tokenUsage(snapshot)) : (row.tokenUsage ?? tokenUsage(snapshot)),
        hostConnection: current ? (legacyHostAdapter ? (hasLiveHost(state.match) ? 'CONNECTED' : 'DISCONNECTED') :
          'LOCAL_APP_OWNED') : 'ARCHIVED',
        pendingOperations: current ?
          state.match.operations?.filter(op => ['PENDING','DELIVERED'].includes(op.state)).length ?? 0 : 0,
        dashboardPath: `/${row.matchId}/${row.roundId}` }; }) };
  };
  const poll = async () => {
    if (closing || state.shutdownIntent || state.newRoundPending || !lifecycle || !state.match?.session || state.match.forgotten) return;
    cliSupervisor?.reconcile?.();
    if (Date.now() > state.match.credentialExpiresAt) {
      state.match.credentialExpired = true; lifecycle.pollFailed();
      state.match.phase = 'REAUTHENTICATION_REQUIRED'; eraseCredentials(); return;
    }
    if (terminal(state.match.officialStatus)) {
      if (state.match.agentState === 'STOPPED' && !state.match.operations.some(op => ['PENDING','DELIVERED'].includes(op.state))) eraseCredentials();
      return;
    }
    try {
      const observation = await fetchStatus(state.match.session);
      if (state.match.lastDiagnosticPollError) {
        diagnostics.append('MATCH_STATUS_POLL_RECOVERED', { matchId: state.match.session.matchId,
          previousCode: state.match.lastDiagnosticPollError });
        state.match.lastDiagnosticPollError = null;
      }
      if (state.match.lastDiagnosticOfficialStatus !== observation.status) {
        diagnostics.append('MATCH_STATUS_CHANGED', { matchId: state.match.session.matchId,
          from: state.match.lastDiagnosticOfficialStatus ?? 'unknown', to: observation.status });
        state.match.lastDiagnosticOfficialStatus = observation.status;
      }
      persistMatchStatus(state.match.session, observation);
      lifecycle.observe(observation);
      if (!state.match.preparedReceipt && state.match.agentState === 'RUNNING' &&
          state.match.operations?.some(op => op.kind === 'PREPARE' && op.state === 'COMPLETED')) {
        const environmentResult = readEnvironmentReadyProgress(state.match.session.teamSdlcPackWorkspace,
          state.match.session.matchId, state.match.session.playerSessionId);
        if (environmentResult) await lifecycle.environmentReady(environmentResult);
      }
      await dispatchCliIfNeeded();
      if (terminal(state.match.officialStatus) && state.match.agentState === 'STOPPED' &&
          (terminalBacklogStatusSynced || terminalBacklogStatusAttempts >= 3 || !state.match.session.gameMasterPlayerSecret) &&
          !state.match.operations.some(op => ['PENDING','DELIVERED'].includes(op.state))) eraseCredentials();
    } catch (error) {
      lifecycle.pollFailed();
      const failure = safeCode(error);
      if (state.match.lastDiagnosticPollError !== failure) {
        state.match.lastDiagnosticPollError = failure;
        diagnostics.append('MATCH_STATUS_POLL_FAILED', { matchId: state.match.session.matchId, code: failure });
      }
    }
  };
  const json = (response, value, code = 200) => { response.writeHead(code, { 'Content-Type': 'application/json' }); response.end(JSON.stringify(value)); };
  const body = async request => {
    let size = 0; const chunks = [];
    for await (const chunk of request) { size += chunk.length; if (size > 65536) fail('LOCAL_APP_BODY_TOO_LARGE'); chunks.push(chunk); }
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  };
  const server = createServer(async (request, response) => {
    response.setHeader('Cache-Control', 'no-store');
    response.setHeader('X-Content-Type-Options', 'nosniff');
    response.setHeader('Referrer-Policy', 'no-referrer');
    const origin = `http://127.0.0.1:${state.port}`;
    if (request.headers.host !== `127.0.0.1:${state.port}`) { response.writeHead(403).end(); return; }
    try {
      if (request.method === 'GET' && request.url === '/health') { json(response, status()); return; }
      if (request.method === 'GET' && request.url === '/manage') {
        const token = randomBytes(16).toString('hex');
        response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Content-Security-Policy':
          `default-src 'none'; img-src data:; style-src 'nonce-${token}'; script-src 'nonce-${token}'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'` });
        response.end(managementDocument({ token, version })); return;
      }
      if (request.method === 'GET' && request.url === '/manage/status') { json(response, await managementStatus()); return; }
      if (request.method === 'POST' && request.url === '/manage/approval') {
        if (request.headers.origin !== origin || request.headers['content-type'] !== 'application/json') fail('INVALID_CONTROL_ORIGIN');
        const input = await body(request);
        if (!cliSupervisor?.decide) fail('APPROVAL_UNAVAILABLE');
        const result = await cliSupervisor.decide(input);
        diagnostics.append('APPROVAL_DECIDED', { requestId: input.id, decision: input.decision });
        json(response, result); return;
      }
      if (request.method === 'GET' && request.url === '/manage/diagnostics') {
        json(response, diagnostics.snapshot(500)); return;
      }
      const transcriptRoute = /^\/manage\/transcript\?threadId=([A-Za-z0-9_.:%-]{1,256})$/.exec(request.url);
      if (request.method === 'GET' && transcriptRoute) {
        const requestedThread = decodeURIComponent(transcriptRoute[1]);
        if (cliSupervisor?.transcript && (state.match?.cliRuntime?.sessionId === requestedThread ||
            state.match?.cliRuntime?.subagents?.some(a => a.sessionId === requestedThread))) {
          json(response, await cliSupervisor.transcript(requestedThread)); return;
        }
        if (!registry) fail('TRANSCRIPT_REGISTRY_UNAVAILABLE');
        if (state.match?.cliRuntime?.sessionId === requestedThread) {
          json(response, readCliTranscriptView(state.match.cliRuntime)); return;
        }
        discoverCodexTranscripts(registry, join(process.env.CODEX_HOME || join(homedir(), '.codex'), 'sessions'));
        json(response, readCodexTranscriptView(registry, requestedThread)); return;
      }
      if (request.method === 'POST' && request.url === '/manage/stop-orchestrator') {
        if (request.headers.origin !== origin || request.headers['content-type'] !== 'application/json') fail('INVALID_CONTROL_ORIGIN');
        await body(request);
        const result = await serial(async () => {
          if (!lifecycle) return { status: 'ORCHESTRATOR_STOPPED' };
          const attached = await lifecycle.handle({ schemaVersion: 1, type: 'HOST_ATTACH' });
          if (!state.match.operatorStopped) await lifecycle.handle({ schemaVersion: 1, type: 'APP_STOP', hostGeneration: attached.hostGeneration });
          await dispatchCliIfNeeded();
          const stopped = state.match.agentState === 'STOPPED';
          diagnostics.append('ORCHESTRATOR_STOP_REQUESTED', { stopped });
          persist();
          return { status: stopped ? 'ORCHESTRATOR_STOPPED' : 'ORCHESTRATOR_STOP_PENDING' };
        });
        json(response, result); return;
      }
      if (request.method === 'POST' && request.url === '/manage/stop') {
        if (request.headers.origin !== origin || request.headers['content-type'] !== 'application/json') fail('INVALID_CONTROL_ORIGIN');
        await body(request); const result = await serial(requestShutdown); json(response, result);
        if (result.status === 'LOCAL_APP_STOPPED') response.once('finish', () => setImmediate(() => { void closeApp(); }));
        return;
      }
      if (request.method === 'POST' && request.url === '/manage/resume') {
        if (request.headers.origin !== origin || request.headers['content-type'] !== 'application/json') fail('INVALID_CONTROL_ORIGIN');
        const input = await body(request);
        const result = await serial(async () => {
          const match = state.match;
          if (!match || !lifecycle) fail('NO_MATCH_TO_RESUME');
          if (input.matchId !== match.session.matchId || input.roundId !== match.roundId) fail('RESUME_ROUND_MISMATCH');
          if (state.shutdownIntent) fail('SHUTDOWN_IN_PROGRESS');
          if (legacyHostAdapter) fail('HOST_RUNTIME_RESUME_REQUIRED');
          if (terminal(match.officialStatus)) fail('MATCH_ALREADY_ENDED');
          if (match.credentialExpired || match.credentialsErased || Date.now() > match.credentialExpiresAt) fail('MATCH_REAUTHENTICATION_REQUIRED');
          if (match.operations.some(op => op.state === 'DELIVERED' || (op.kind === 'STOP' && op.state === 'PENDING'))) fail('STOP_OPERATION_PENDING');
          // Verify the server while the operator-stop latch remains set. Never
          // dispatch work from a stale pre-shutdown match status.
          let observation;
          try { observation = await fetchStatus(match.session); }
          catch { lifecycle.pollFailed(); fail('MATCH_STATUS_UNAVAILABLE'); }
          persistMatchStatus(match.session, observation); lifecycle.observe(observation);
          if (terminal(observation.status)) fail('MATCH_ALREADY_ENDED');
          state.newRoundPending = false;
          const attached = await lifecycle.handle({ schemaVersion: 1, type: 'HOST_ATTACH' });
          await lifecycle.handle({ schemaVersion: 1, type: 'APP_RESUME', hostGeneration: attached.hostGeneration });
          persist(); await dispatchCliIfNeeded();
          diagnostics.append('MATCH_RESUMED', { matchId: match.session.matchId, roundId: match.roundId });
          return { status: 'MATCH_RESUMED', matchId: match.session.matchId, roundId: match.roundId,
            phase: lifecycle.publicState().phase, dashboardUrl: origin + roundRoute(match) };
        });
        json(response, result); return;
      }
      if (request.method === 'POST' && request.url === '/init/stop-existing') {
        if (request.headers.origin !== origin || request.headers['content-type'] !== 'application/json') fail('INVALID_CONTROL_ORIGIN');
        const input = await body(request);
        if (input.stopExisting !== true) fail('ACTIVE_SESSION_STOP_CONFIRMATION_REQUIRED');
        if (legacyHostAdapter) fail('HOST_RUNTIME_STOP_CONFIRMATION_REQUIRED');
        const result = await serial(() => beginNewRound({ stopExisting: true }));
        json(response, result); return;
      }
      if (request.method === 'POST' && request.url === '/manage/browse-workspace') {
        if (request.headers.origin !== origin || request.headers['content-type'] !== 'application/json') fail('INVALID_CONTROL_ORIGIN');
        await body(request);
        if (pickerOpen) fail('FOLDER_PICKER_ALREADY_OPEN');
        pickerOpen = true;
        try { json(response, { workspace: await (dependencies.browseTeamWorkspace ?? browseTeamWorkspace)() }); }
        finally { pickerOpen = false; }
        return;
      }
      if (request.method === 'POST' && request.url === '/init/workspace') {
        if (request.headers.origin !== origin || request.headers['content-type'] !== 'application/json') fail('INVALID_CONTROL_ORIGIN');
        const input = await body(request);
        const result = await serial(async () => {
          if (typeof input.workspace !== 'string' || !input.workspace.trim()) fail('INVALID_TEAM_WORKSPACE');
          const selected = resolve(input.workspace);
          const nextLayout = validateTeamWorkspaceLayout(selected, { configDir: dataDirectory });
          const nextInstalled = verifyTeamRuntime(nextLayout);
          discoverTeamRuntime(selected);
          if (selected.toLowerCase() !== resolve(workspace).toLowerCase()) {
            if (state.match && (state.match.agentState !== 'STOPPED' || state.match.operations?.some(op=>['PENDING','DELIVERED'].includes(op.state)))) fail('ACTIVE_SESSION_STOP_CONFIRMATION_REQUIRED');
            const nextStore = openLocalAppStore({ directory: localAppDirectory(dataDirectory, selected), workspace: selected });
            const nextState = nextStore.load() ?? { schemaVersion: 1, workspace: selected, controlToken: randomBytes(32).toString('hex'), match: null, rounds: [] };
            const nextOwner = nextStore.acquireLock();
            await cliSupervisor?.stop(); streamingClient?.close(); streamingClient = null; await dashboard?.close(); store.releaseLock();
            const port = state.port;
            store = nextStore; owner = nextOwner; state = nextState; state.port = port; workspace = selected;
            diagnostics = openDiagnosticJournal(store.directory);
            dashboard = null; lifecycle = null; registry = null; cliSupervisor = null; cliAttached = false;
            if (state.shutdownIntent?.id === state.shutdownReceipt?.shutdownId) delete state.shutdownIntent;
            if (state.match) await setupMatch(false);
          }
          layout = nextLayout; installed = nextInstalled;
          pack = { teamId: installed.runtime.teamId, teamName: installed.runtime.teamName, version: installed.runtime.version };
          const preferences = join(dataDirectory, 'player-workspace.json');
          writeFileSync(preferences + '.tmp', JSON.stringify({ workspace: selected })); renameSync(preferences + '.tmp', preferences);
          persist(); return { workspace: selected, teamPack: pack, hasSavedSecret: Boolean(state.savedInit?.secret || state.match?.session?.gameMasterPlayerSecret) };
        });
        json(response, result); return;
      }
      const modelRoute = request.url === '/init/models' || request.url === '/restart/models';
      if (modelRoute && ['GET', 'POST'].includes(request.method)) {
        const restartingModels = request.url === '/restart/models';
        let command = selectedCodexExecutable ?? state.match?.cliRuntime?.codexExecutable;
        if (request.method === 'POST') {
          if (request.headers.origin !== origin || !request.headers['content-type']?.startsWith('application/json')) fail('INVALID_INIT_ORIGIN');
          const input = await body(request);
          if (!input || Object.keys(input).sort().join(',') !== 'codexCliPath' || typeof input.codexCliPath !== 'string' ||
              input.codexCliPath.length > 1024 || !isAbsolute(input.codexCliPath)) fail('INVALID_CODEX_CLI_PATH');
          command = resolve(input.codexCliPath);
          if (!existsSync(command) || !statSync(command).isFile()) fail('CODEX_CLI_NOT_FOUND');
        }
        modelCatalog = await (dependencies.listModels ?? listCodexModels)(command ? { command } : undefined);
        if (request.method === 'POST') {
          selectedCodexExecutable = command; state.codexExecutable = command; persist();
        }
        json(response, { ...modelCatalog, codexCliPath: selectedCodexExecutable,
          orchestratorModel: restartingModels ? state.match?.modelPolicy?.orchestratorModel ?? state.match?.modelPolicy?.allowedModels?.[0] : null,
          orchestratorReasoningEffort: restartingModels ? state.match?.modelPolicy?.orchestratorReasoningEffort ?? 'medium' : 'medium',
          selected: restartingModels ? state.match?.modelPolicy?.allowedModels ?? null : null,
          approvalMode: restartingModels ? state.match?.cliRuntime?.approvalMode ?? 'auto_review' : 'auto_review' }); return;
      }
      if (request.method === 'GET' && request.url === '/') { response.writeHead(302, { Location: '/init' }).end(); return; }
      if (request.method === 'GET' && ['/init', '/restart'].includes(request.url)) {
        selectedCodexExecutable = discoverCodexExecutable() ?? state.codexExecutable ?? selectedCodexExecutable;
        const token = randomBytes(16).toString('hex');
        response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Content-Security-Policy':
          `default-src 'none'; img-src data:; style-src 'nonce-${token}'; script-src 'nonce-${token}'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'` });
        response.end(page({ token, defaultApiUrl: configStore.load()?.gameMasterApiUrl ?? DEFAULT_GAME_MASTER_API_URL,
          teamPack: pack, selectedWorkspace: installed ? state.workspace : null,
          hasSavedKey: Boolean(state.savedInit?.secret),
          codexCliPath: selectedCodexExecutable, mode: request.url === '/restart' ? 'RESTART' : 'INIT', localApp: true })); return;
      }
      if (request.method === 'POST' && ['/init/submit', '/restart/submit'].includes(request.url)) {
        if (request.headers.origin !== origin || !request.headers['content-type']?.startsWith('application/json')) fail('INVALID_INIT_ORIGIN');
        const input = await body(request);
        const restarting = request.url === '/restart/submit';
        const result = await serial(async () => {
          if (state.shutdownIntent) fail('SHUTDOWN_IN_PROGRESS');
          if (state.resetPending) fail('RESET_INCOMPLETE_RETRY_RESET');
          const previous = state.match;
          if (previous && !restarting && (previous.agentState !== 'STOPPED' ||
              previous.operations?.some(op => ['PENDING', 'DELIVERED'].includes(op.state)))) fail('STOP_AGENTS_BEFORE_NEW_ROUND');
          if (previous && restarting && previous.agentState !== 'STOPPED') fail('STOP_AGENTS_BEFORE_REAUTHENTICATION');
          const { allowedModels, orchestratorModel, orchestratorReasoningEffort, teamLoginAccount, teamPassword,
            streamingEnabled = restarting ? previous?.streamingEnabled ?? true : true,
            approvalMode = restarting ? previous?.cliRuntime?.approvalMode ?? 'auto_review' : 'auto_review',
            clearChallenge: requestedClearChallenge = false, ...bootstrapInput } = input;
          if (!['auto_review', 'user'].includes(approvalMode)) fail('INVALID_APPROVAL_MODE');
          if (typeof streamingEnabled !== 'boolean') fail('INVALID_STREAMING_MODE');
          if (typeof requestedClearChallenge !== 'boolean') fail('INVALID_CLEAR_CHALLENGE');
          if (teamLoginAccount !== undefined && typeof teamLoginAccount !== 'string') fail('INVALID_WEB_INIT_FORM');
          if (teamPassword !== undefined && typeof teamPassword !== 'string') fail('INVALID_WEB_INIT_FORM');
          if (Object.keys(bootstrapInput).sort().join(',') !== ['teamId','matchId','teamWorkspace','gameMasterApiUrl','gameMasterSecretKey'].sort().join(',')) fail('INVALID_WEB_INIT_FORM');
          // Older direct clients remain readable; the web form always supplies an explicit selection.
          const modelPolicy = allowedModels === undefined ? (restarting ? previous?.modelPolicy ?? null : null) :
            selectModelPolicy(allowedModels, modelCatalog, orchestratorModel, orchestratorReasoningEffort);
          if (orchestratorModel !== undefined && allowedModels === undefined) fail('INVALID_ORCHESTRATOR_MODEL');
          if (restarting && previous?.modelPolicy && (modelPolicy?.orchestratorModel ?? modelPolicy?.allowedModels[0]) !== (previous.modelPolicy.orchestratorModel ?? previous.modelPolicy.allowedModels[0])) fail('ROUND_MODEL_POLICY_IMMUTABLE');
          if (restarting && previous?.modelPolicy && JSON.stringify(modelPolicy?.allowedModels?.slice().sort()) !==
              JSON.stringify(previous.modelPolicy.allowedModels.slice().sort())) fail('ROUND_MODEL_POLICY_IMMUTABLE');
          if (restarting && previous?.modelPolicy && (modelPolicy?.orchestratorReasoningEffort ?? 'medium') !==
              (previous.modelPolicy.orchestratorReasoningEffort ?? 'medium')) fail('ROUND_MODEL_POLICY_IMMUTABLE');
          if (resolve(input.teamWorkspace).toLowerCase() !== resolve(workspace).toLowerCase()) fail('TEAM_WORKSPACE_MISMATCH');
          if (restarting && state.match && (state.match.session.matchId !== input.matchId ||
            state.match.session.teamId.toLowerCase() !== input.teamId.toLowerCase() ||
            new URL(state.match.session.gameMasterApiUrl).origin !== new URL(input.gameMasterApiUrl).origin)) fail('RESTART_IDENTITY_MISMATCH');
          if (previous && !restarting && dashboard) {
            const response = await fetch(dashboard.url + '/snapshot', { signal: AbortSignal.timeout(5000) });
            if (!response.ok) fail('ROUND_ARCHIVE_FAILED');
            archiveRound(store.directory, previous, await response.json());
          }
          if (bootstrapInput.gameMasterSecretKey === '') {
            // Reuse the Team API Key saved for this server; otherwise it is required.
            if (!state.savedInit?.secret || state.savedInit.origin !== new URL(bootstrapInput.gameMasterApiUrl).origin) {
              fail('GAME_MASTER_KEY_REQUIRED');
            }
            bootstrapInput.gameMasterSecretKey = state.savedInit.secret;
          }
          let session = await init({ ...bootstrapInput, teamLoginAccount, teamPassword, reconfigure: true,
            clearChallenge: restarting ? false : (clearChallenge || requestedClearChallenge),
            preserveChallenge: restarting }, { configStore, credentialStore });
          state.savedInit = { secret: bootstrapInput.gameMasterSecretKey, teamId: input.teamId, matchId: input.matchId, origin: new URL(input.gameMasterApiUrl).origin };
          if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(session.matchId)) fail('INVALID_MATCH_ID');
          const manifest = dependencies.manifest ?? discoverTeamRuntime(workspace).manifest;
          const selectedCliProvider = dependencies.cliProvider ?? process.env.MTM_PLAYER_CLI_PROVIDER ?? 'codex';
          const declaredProvider = manifest?.provider ?? 'codex';
          if (declaredProvider !== selectedCliProvider) fail('CLI_PROVIDER_NOT_SUPPORTED_BY_TEAM_PACK');
          const existing = restarting ? state.match : null;
          if (existing) {
            if (existing.cliRuntime?.provider && existing.cliRuntime.provider !== selectedCliProvider) {
              fail('CLI_PROVIDER_IMMUTABLE_FOR_ROUND');
            }
            session = { ...session, playerSessionId: existing.session.playerSessionId };
            // Reauthentication of a saved legacy match keeps its checkout.
            session = { ...session, challengeWorkspace: existing.session.challengeWorkspace,
              ...matchWorkspacePaths(existing.session) };
            // Do not silently replace credentials captured by a delivered host operation.
            if (existing.operations?.some(op => op.state === 'DELIVERED')) fail('HOST_RECONCILIATION_REQUIRED');
            if (existing.agentState === 'RUNNING') fail('STOP_AGENTS_BEFORE_REAUTHENTICATION');
            for (const op of existing.operations ?? []) if (op.state === 'PENDING') op.state = 'CANCELLED';
            state.match = { ...existing, session, manifest, modelPolicy, streamingEnabled, cliRuntime: { ...existing.cliRuntime, approvalMode,
              ...(selectedCodexExecutable ? { codexExecutable: selectedCodexExecutable } : {}) }, credentialExpired: false,
              preparation: null, credentialsErased: false, credentialExpiresAt: Date.now() + 24 * 60 * 60 * 1000 };
          } else {
            const history = listMatchRounds(state, session.matchId).rounds;
            const number = Math.max(0, ...history.map(row => Number(row.roundId.slice(5)))) + 1;
            const candidate = { session, manifest, modelPolicy, modelEvidence: [], streamingEnabled, cliRuntime: { provider: selectedCliProvider, approvalMode,
              ...(selectedCodexExecutable ? { codexExecutable: selectedCodexExecutable } : {}) }, roundId: roundName(number), startedAt: new Date().toISOString(),
              freshPending: !restarting, credentialExpiresAt: Date.now() + 24 * 60 * 60 * 1000 };
            if (restarting) {
              const checkpoint = loadRestartCheckpoint(session.teamSdlcPackWorkspace, session.matchId).checkpoint;
              session = bindRestartSession(session, checkpoint); candidate.session = session;
              candidate.preparedReceipt = { schemaVersion: 1, status: 'PREPARED', playerSessionId: session.playerSessionId,
                teamId: session.teamId, matchId: session.matchId, preparationRequestId: 'checkpoint-recovery',
                environmentRevision: checkpoint.environmentRevision, gameMasterRequestId: null };
            }
            if (previous && !restarting) {
              const entry = { ...roundSummary(previous, state.port), endedAt: new Date().toISOString() };
              state.rounds = state.rounds.filter(row => row.matchId !== entry.matchId || row.roundId !== entry.roundId);
              state.rounds.push(entry);
            }
            state.match = candidate;
          }
          if (dashboard) await dashboard.close();
          clearChallenge = false; state.newRoundPending = false;
          store.compact(state); await setupMatch(!existing && !restarting);
          if (restarting && !legacyHostAdapter) {
            const attached = await lifecycle.handle({ schemaVersion: 1, type: 'HOST_ATTACH' });
            await lifecycle.handle({ schemaVersion: 1, type: 'APP_RESUME', hostGeneration: attached.hostGeneration });
          }
          diagnostics.append('MATCH_INITIALIZED', { matchId: session.matchId, roundId: state.match.roundId,
            provider: selectedCliProvider });
          return { ok: true, status: 'READY', matchId: session.matchId, team: { id: session.teamId, name: session.teamName },
            roundId: state.match.roundId,
            teamRuntime: { version: session.teamRuntime.runtime.version }, dashboardUrl: `${origin}${roundRoute(state.match)}` };
        });
        json(response, result); void serial(poll); return;
      }
      if (request.method === 'POST' && request.url === `/${state.controlToken}`) {
        if (request.headers.origin || request.headers['content-type'] !== 'application/json') fail('INVALID_CONTROL_ORIGIN');
        const message = await body(request);
        if (message.type === 'HOST_WAIT') {
          if (message.schemaVersion !== 1 || !Number.isInteger(message.timeoutMs ?? 55000) ||
              (message.timeoutMs ?? 55000) < 1 || (message.timeoutMs ?? 55000) > 55000) fail('INVALID_HOST_WAIT');
          // Wait outside the mutation queue, otherwise status polling cannot
          // enqueue the operation that this request is waiting to receive.
          const view = () => lifecycle && !state.newRoundPending ? lifecycle.publicState() : { phase: 'AWAITING_INIT', operations: [] };
          const key = value => JSON.stringify([value.phase, value.officialStatus, value.ackState, value.operations]);
          const before = key(view());
          const result = await new Promise(resolveWait => {
            let finished = false;
            const finish = reason => { if (finished) return; finished = true;
              clearTimeout(timer); clearInterval(heartbeat); changes.off('change', check);
              changes.off('shutdown', shuttingDown);
              response.off('close', disconnected); resolveWait({ status: reason, ...view() }); };
            const check = () => {
              if (lifecycle && message.hostGeneration !== state.match.hostGeneration) return finish('STALE_HOST_GENERATION');
              const current = view();
              if (current.operations.length || key(current) !== before) finish('HOST_EVENT_READY');
            };
            const disconnected = () => finish('HOST_WAIT_DISCONNECTED');
            const shuttingDown = () => finish('LOCAL_APP_STOPPED');
            const timer = setTimeout(() => finish('HOST_WAIT_TIMEOUT'), message.timeoutMs ?? 55000);
            const heartbeat = setInterval(() => {
              if (state.match && message.hostGeneration === state.match.hostGeneration) state.match.hostSeenAt = Date.now();
            }, 5000);
            changes.on('change', check); changes.once('shutdown', shuttingDown);
            response.once('close', disconnected); check();
          });
          if (!response.destroyed) json(response, result);
          return;
        }
        const result = await serial(async () => {
          if (message.type === 'APP_SHUTDOWN_REQUEST') {
            if (message.schemaVersion !== 1) fail('INVALID_LOCAL_APP_MESSAGE');
            return requestShutdown();
          }
          if (message.type === 'APP_SHUTDOWN_ACK') {
            if (message.schemaVersion !== 1) fail('INVALID_LOCAL_APP_MESSAGE');
            if (!state.shutdownIntent || message.shutdownId !== state.shutdownIntent.id) fail('UNKNOWN_SHUTDOWN');
            if (message.hostGeneration !== (state.match?.hostGeneration ?? 0)) fail('STALE_HOST_GENERATION');
            if (state.match && (state.match.agentState !== 'STOPPED' ||
                state.match.operations.some(op => ['PENDING','DELIVERED'].includes(op.state)))) fail('STOP_AGENTS_BEFORE_SHUTDOWN');
            if (message.result?.treeStopped !== true || message.result?.mcpStopped !== true) fail('HOST_RUNTIME_STOP_CONFIRMATION_REQUIRED');
            state.shutdownReceipt = { schemaVersion: 1, status: 'LOCAL_APP_STOPPED', shutdownId: state.shutdownIntent.id,
              stoppedAt: new Date().toISOString(), agents: 'STOPPED', mcp: 'STOPPED' };
            persist();
            response.once('finish', () => { setImmediate(() => { void closeApp(); }); });
            return state.shutdownReceipt;
          }
          if (state.shutdownIntent && ['APP_BEGIN_INIT', 'APP_RESUME', 'ENVIRONMENT_READY', 'APP_FORGET'].includes(message.type)) fail('SHUTDOWN_IN_PROGRESS');
          if (message.type === 'APP_BEGIN_INIT') {
            return beginNewRound(message);
          }
          if (message.type === 'APP_ATTACH') {
            if (message.clearChallenge === true) clearChallenge = true;
            if (state.match?.hostOwnerThreadId && message.codexThreadId &&
                message.codexThreadId !== state.match.hostOwnerThreadId) fail('HOST_TASK_REVOKED');
            if (message.codexThreadId) hostThreadId = message.codexThreadId;
            if (state.newRoundPending) return { ...status(), phase: 'AWAITING_INIT', operations: [],
              hostGeneration: state.match?.hostGeneration ?? 0 };
            if (registry && hostThreadId) registry.attachPlayer(hostThreadId);
            return { ...status(), ...(lifecycle ? await lifecycle.handle({ schemaVersion: 1, type: 'HOST_ATTACH' }) : {}) };
          }
          if (message.type === 'APP_RESET') fail('RESET_REMOVED_USE_INIT');
          if (message.type === 'APP_STATUS') return listMatchRounds(state, message.matchId);
          if (state.newRoundPending) fail('INIT_FORM_PENDING');
          if (!lifecycle) return status();
          if (message.type === 'REGISTER_CODEX_AGENT') return registry.registerAgent(message.registration);
          if (message.type === 'REGISTER_CODEX_TRANSCRIPT') return registry.registerTranscript(message.registration);
          if (message.type === 'PLAYER_HOST_INFO') return registry.snapshot();
          if (message.type === 'APP_FORGET') {
            if (state.match.agentState !== 'STOPPED' || state.match.operations.some(op => ['PENDING','DELIVERED'].includes(op.state))) fail('STOP_AGENTS_BEFORE_FORGET');
            streamingClient?.close(); streamingClient = null; await dashboard?.close(); dashboard = null; lifecycle = null; registry = null;
            state.match = null; store.compact(state); return { status: 'FORGOTTEN' };
          }
          if (state.match.credentialExpired && message.type === 'ENVIRONMENT_READY') fail('MATCH_REAUTHENTICATION_REQUIRED');
          const result = await lifecycle.handle(message);
          if (message.type === 'HOST_OPERATION_ACK' && state.match.stopForNewRound &&
              state.match.agentState === 'STOPPED' &&
              !state.match.operations.some(op => ['PENDING','DELIVERED'].includes(op.state))) {
            state.match.stopForNewRound = false; state.newRoundPending = true; persist();
            return { ...status(), phase: 'AWAITING_INIT', operations: [], hostGeneration: state.match.hostGeneration };
          }
          if (terminal(state.match.officialStatus) && state.match.agentState === 'STOPPED' &&
              !state.match.operations.some(op => ['PENDING','DELIVERED'].includes(op.state))) {
            eraseCredentials();
          }
          await dispatchCliIfNeeded();
          return result;
        });
        json(response, result); return;
      }
      const statusRoute = /^\/matches\/([A-Za-z0-9][A-Za-z0-9_-]{0,127})\/rounds$/.exec(request.url);
      if (request.method === 'GET' && statusRoute) { json(response, listMatchRounds(state, statusRoute[1])); return; }
      const alias = /^\/([A-Za-z0-9][A-Za-z0-9_-]{0,127})$/.exec(request.url);
      if (request.method === 'GET' && alias) {
        const latest = listMatchRounds(state, alias[1]).rounds.at(-1);
        if (latest) { response.writeHead(302, { Location: `/${latest.matchId}/${latest.roundId}` }).end(); return; }
      }
      const archivedRoute = /^\/([A-Za-z0-9][A-Za-z0-9_-]{0,127})\/(round\d{2,})(\/snapshot)?$/.exec(request.url);
      const matchPath = state.match ? roundRoute(state.match) : null;
      if (matchPath && request.method === 'POST' && request.url === `${matchPath}/chat`) {
        if (request.headers.origin !== origin || request.headers['content-type'] !== 'application/json') fail('INVALID_CONTROL_ORIGIN');
        const result = await serial(async () => submitOrchestratorChat(await body(request)));
        json(response, result, result.status === 'CHAT_QUEUED' ? 202 : 200); return;
      }
      if (request.method === 'GET' && archivedRoute && `/${archivedRoute[1]}/${archivedRoute[2]}` !== matchPath) {
        const entry = state.rounds.find(row => row.matchId === archivedRoute[1] && row.roundId === archivedRoute[2]);
        if (entry) {
          response.writeHead(200, { 'Content-Type': archivedRoute[3] ? 'application/json' : 'text/html; charset=utf-8',
            'Content-Security-Policy': "default-src 'none'; img-src data:; script-src 'unsafe-inline'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'" });
          response.end(readArchivedRound(store.directory, entry, archivedRoute[3] ? 'snapshot.json' : 'dashboard.html')); return;
        }
      }
      if (matchPath && request.method === 'GET' && request.url === `${matchPath}/team-info`) {
        if ((request.headers.origin && request.headers.origin !== origin) ||
            request.headers['sec-fetch-site'] === 'cross-site') fail('INVALID_CONTROL_ORIGIN');
        const match = state.match;
        if (match.credentialsErased || match.credentialExpired || Date.now() > match.credentialExpiresAt) {
          json(response, { code: 'TEAM_INFO_UNAVAILABLE' }, 410); return;
        }
        const session = match.session;
        json(response, { schemaVersion: 1, playerSessionId: session.playerSessionId,
          requestId: session.teamInfoRequestId, matchId: session.matchId,
          team: { uuid: session.teamUuid, id: session.teamId, name: session.teamName,
            username: session.username, password: session.password },
          apiKey: { secret: session.apiKeySecret, scopes: session.apiKeyScopes ?? [] },
          gameMasterUrl: session.gameMasterUrl, matchPageUrl: session.matchPageUrl,
          repository: session.repository,
          preparationComplete: session.preparationComplete ? { ...session.preparationComplete,
            authorization: `Bearer ${session.apiKeySecret}` } : undefined }); return;
      }
      if (dashboard && request.method === 'GET' && [matchPath, `${matchPath}/snapshot`].includes(request.url)) {
        const suffix = request.url === matchPath ? '' : '/snapshot';
        const upstream = await fetch(dashboard.url + suffix, { signal: AbortSignal.timeout(5000) });
        if (suffix) {
          const value = await upstream.json();
          json(response, { ...value, localApplication: publicMatch() }, upstream.status);
        } else {
          await upstream.body?.cancel();
          response.writeHead(upstream.status, { 'Content-Type': 'text/html; charset=utf-8',
            'Content-Security-Policy': "default-src 'none'; img-src data:; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'" });
          response.end(dashboardDocument(null, !state.match.credentialsErased &&
            !state.match.credentialExpired && Date.now() <= state.match.credentialExpiresAt, '/manage'));
        }
        return;
      }
      response.writeHead(404).end('Match not found');
    } catch (error) {
      const failure = safeCode(error);
      diagnostics.append('LOCAL_HTTP_REQUEST_FAILED', { method: request.method, route: request.url?.split('?')[0], code: failure });
      json(response, { ok: false, status: 'ERROR', code: failure }, 400);
    }
  });
  server.requestTimeout = 15000;
  let closePromise;
  function closeApp() {
    if (closePromise) return closePromise;
    closing = true; clearInterval(timer); changes.emit('shutdown');
    closePromise = (async () => {
      diagnostics.append('LOCAL_APP_STOPPING', { pid: process.pid });
      await queue; streamingClient?.close(); await dashboard?.close();
      await new Promise(done => { server.close(done); server.closeIdleConnections?.(); });
      store.releaseLock();
    })();
    return closePromise;
  }
  try {
    if (mode === 'INIT' && hasActiveSession(state.match) && !stopExisting) {
      fail('ACTIVE_SESSION_STOP_CONFIRMATION_REQUIRED');
    }
    await new Promise((resolveReady, reject) => {
      server.once('error', error => reject(Object.assign(error, {
        code: error.code === 'EADDRINUSE' ? 'LOCAL_APP_PORT_IN_USE' : 'LOCAL_APP_LISTEN_FAILED'
      })));
      server.listen(state.port, '127.0.0.1', resolveReady);
    });
    state.port = server.address().port; persist();
    if (state.match) await setupMatch(false);
    if (mode === 'INIT') {
      if (hasActiveSession(state.match) && stopExisting) {
        await beginNewRound({ stopExisting: true, clearChallenge, codexThreadId });
      } else if (!state.match || !hasActiveSession(state.match)) {
        state.newRoundPending = true; persist();
      }
    }
    let pollQueued = false;
    const schedulePoll = () => {
      if (closing || pollQueued) return;
      pollQueued = true; void serial(poll).finally(() => { pollQueued = false; }).catch(() => {});
    };
    timer = setInterval(schedulePoll, 5000);
    schedulePoll();
  } catch (error) { server.close(); await dashboard?.close(); store.releaseLock(); throw error; }
  const origin = `http://127.0.0.1:${state.port}`;
  return { url: `${origin}${mode === 'INIT' ? '/init' : mode === 'RESTART' ? '/restart' : state.match ? roundRoute(state.match) : '/init'}`,
    controlUrl: `${origin}/${state.controlToken}`, ownerId: owner.ownerId,
    close: closeApp };
}
