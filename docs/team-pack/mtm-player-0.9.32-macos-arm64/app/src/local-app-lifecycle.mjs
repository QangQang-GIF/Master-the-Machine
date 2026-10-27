import { randomUUID } from 'node:crypto';
import { modelPolicyFrame } from './model-policy.mjs';
import { submissionPolicyFrame } from './submission-policy.mjs';
import { prepareNativePreparation, prepareNativeGameStart, prepareNativeOrchestratorRestart } from './team-runtime.mjs';
import { acknowledgeEnvironmentReady, validateEnvironmentReady, validatePreparationReceipt } from './preparation.mjs';
import { recordPlayerPreparationComplete } from './progress.mjs';

const fail = code => { throw Object.assign(new Error(code), { code }); };
const terminal = status => ['FINISHED', 'CANCELLED'].includes(status);
const unresolved = op => ['PENDING', 'DELIVERED'].includes(op.state);
const deepFreeze = value => { if (value && typeof value === 'object') { Object.values(value).forEach(deepFreeze); Object.freeze(value); } return value; };

/** All calls must be serialized by the application owner. State includes private frames. */
export function createLocalAppLifecycle(state, save, options = {}) {
  const deps = { prepare: prepareNativePreparation, start: prepareNativeGameStart,
    resume: prepareNativeOrchestratorRestart, acknowledge: acknowledgeEnvironmentReady,
    record: recordPlayerPreparationComplete, ...options };
  state.operations ??= []; state.hostGeneration ??= 0; state.attempt ??= 0;
  state.agentState ??= 'STOPPED'; state.phase ??= 'WAITING';
  if (state.preparation?.receipt?.requestId?.startsWith('restart-')) {
    const original = state.operations.find(op => op.kind === 'PREPARE' && op.state === 'COMPLETED' &&
      op.frame?.receipt?.type === 'PREPARATION_SPAWN_REQUESTED' &&
      op.frame.receipt.playerSessionId === state.session?.playerSessionId &&
      JSON.stringify(op.frame.repository) === JSON.stringify(state.preparation.repository));
    if (original) state.preparation.receipt = { ...state.preparation.receipt,
      requestId: original.frame.receipt.requestId };
  }
  // A restarted process never trusts cached server state to authorize a new action.
  let freshStatus = false;
  const session = () => deepFreeze(structuredClone(state.session));
  const commit = () => save(state);
  const pending = () => state.operations.find(unresolved);
  const enqueue = (kind, frame) => {
    frame = submissionPolicyFrame(frame);
    frame = modelPolicyFrame(frame, state.modelPolicy);
    const op = { id: `op-${randomUUID()}`, kind, state: 'PENDING', spawns: Boolean(frame.spawn), frame, createdAt: new Date().toISOString() };
    state.operations.push(op); commit(); return op;
  };
  const permitted = op => op.kind === 'STOP' || (freshStatus &&
    (op.kind === 'PREPARE' ? ['PREPARING', 'IN_PROGRESS'].includes(state.officialStatus) : state.officialStatus === 'IN_PROGRESS'));
  const reconcile = () => {
    if (!freshStatus || !state.session) return;
    if (state.operatorStopped) return;
    const allowed = ['PREPARING', 'IN_PROGRESS'].includes(state.officialStatus);
    for (const op of state.operations.filter(op => op.state === 'PENDING')) {
      if (!permitted(op)) op.state = 'CANCELLED';
    }
    if (!allowed) {
      if (state.agentState !== 'STOPPED' && !state.operations.some(op => unresolved(op) && op.kind === 'STOP')) {
        enqueue('STOP', { schemaVersion: 1, status: terminal(state.officialStatus) ? 'MATCH_ENDED' : 'ORCHESTRATOR_STOP_REQUIRED',
          stop: { target: state.rootTarget, scope: 'TREE', reason: state.officialStatus },
          notification: { target: state.rootTarget, message: `MATCH_${state.officialStatus}_V1\n${JSON.stringify({ matchId: state.session.matchId })}` } });
      }
      state.phase = terminal(state.officialStatus) ? state.officialStatus : 'WAITING'; commit(); return;
    }
    if (state.ackState === 'OUTCOME_UNKNOWN' || state.ackState === 'IN_FLIGHT') { state.phase = 'RECONCILIATION_REQUIRED'; commit(); return; }
    if (pending()) { commit(); return; }
    if (!state.preparedReceipt) {
      state.phase = 'PREPARING';
      if (state.agentState === 'STOPPED') {
        if (!state.preparation) state.preparation = deps.prepare(session(), state.manifest,
          { availableAgentTypes: ['team_orchestrator'] }, `prepare-${randomUUID()}`);
        let frame = state.preparation;
        if (state.recoveryRequired || state.operations.some(op => op.kind === 'PREPARE' && op.state === 'COMPLETED')) {
          state.attempt++;
          frame = deps.resume(session(), state.manifest, { attempt: state.attempt, reason: 'LOCAL_APP_RESUME', phase: 'PREPARATION', environmentRevision: null,
            preparationRequestId: state.preparation.receipt.requestId });
          state.preparation = { ...state.preparation, spawn: frame.spawn,
            target: frame.spawn.task_name };
        }
        state.rootTarget = frame.spawn.task_name;
        enqueue('PREPARE', { schemaVersion: 1, status: 'PREPARATION_READY', ...frame });
      }
    } else if (state.officialStatus === 'PREPARING') state.phase = 'PREPARED_WAITING';
    else if (!state.gameStarted || state.agentState === 'STOPPED') {
      let frame;
      if (state.agentState === 'STOPPED') {
        state.attempt++;
        frame = deps.resume(session(), state.manifest, { attempt: state.attempt,
          reason: 'LOCAL_APP_RESUME', phase: 'GAME', environmentRevision: state.preparedReceipt.environmentRevision });
        state.rootTarget = frame.spawn.task_name;
      } else {
        frame = deps.start(session(), state.manifest, state.preparedReceipt, `start-${randomUUID()}`);
        frame = { ...frame, followup: { ...frame.followup, target: state.rootTarget } };
      }
      enqueue('GAME', { schemaVersion: 1, status: frame.spawn ? 'ORCHESTRATOR_RESTART_READY' : 'GAME_START_READY', ...frame });
      state.phase = 'START_PENDING';
    } else state.phase = 'RUNNING';
    commit();
  };
  const publicState = () => ({ phase: state.phase, matchId: state.session?.matchId ?? state.matchId,
    officialStatus: state.officialStatus ?? 'UNKNOWN', agentState: state.agentState,
    hostConnection: Date.now() - (state.hostSeenAt ?? 0) < 20000 ? 'CONNECTED' : 'HOST_DISCONNECTED',
    hostGeneration: state.hostGeneration, ackState: state.ackState ?? 'NOT_SENT',
    receipt: state.preparedReceipt ? { status: 'PREPARED', matchId: state.preparedReceipt.matchId,
      requestId: state.preparedReceipt.preparationRequestId, gameMasterRequestId: state.preparedReceipt.gameMasterRequestId,
      environmentRevision: state.preparedReceipt.environmentRevision } : null,
    prepared: Boolean(state.preparedReceipt), pollHealth: state.pollHealth ?? null,
    operations: state.operations.filter(unresolved).map(({ id, kind, state: status }) => ({ id, kind, state: status })) });
  const environmentReady = async environmentResult => {
    if (state.preparedReceipt) { deps.record(state.session, state.preparedReceipt); reconcile(); return publicState(); }
    if (state.agentState !== 'RUNNING' || !state.operations.some(op => op.kind === 'PREPARE' && op.state === 'COMPLETED')) fail('PREPARATION_AGENT_NOT_CONFIRMED');
    if (!freshStatus || !['PREPARING', 'IN_PROGRESS'].includes(state.officialStatus)) fail('MATCH_NOT_PREPARING');
    if (state.ackState === 'IN_FLIGHT' || state.ackState === 'OUTCOME_UNKNOWN') fail('PREPARATION_RECONCILIATION_REQUIRED');
    let preparationRequestId = state.preparation?.receipt.requestId;
    // Compatibility for older Player versions which replaced the preparation
    // identity with a restart operation ID. Accept only a Player-issued request
    // from this same session and repository, never an arbitrary log request ID.
    if (environmentResult?.requestId !== preparationRequestId) {
      const original = state.operations.find(op => op.kind === 'PREPARE' &&
        op.state === 'COMPLETED' && op.frame?.receipt?.requestId === environmentResult?.requestId &&
        op.frame?.receipt?.playerSessionId === state.session.playerSessionId &&
        JSON.stringify(op.frame?.repository) === JSON.stringify(state.preparation?.repository));
      if (original) preparationRequestId = original.frame.receipt.requestId;
    }
    validateEnvironmentReady(environmentResult, { requestId: preparationRequestId, repository: state.preparation?.repository });
    state.ackState = 'IN_FLIGHT'; commit();
    try {
      state.preparedReceipt = await deps.acknowledge({ session: session(), preparationRequestId,
        repository: state.preparation.repository, environmentResult });
      state.ackState = 'PREPARED'; commit();
      deps.record(state.session, state.preparedReceipt); reconcile(); return publicState();
    } catch (error) { if (!state.preparedReceipt) state.ackState = 'OUTCOME_UNKNOWN'; commit(); throw error; }
  };
  return {
    publicState,
    environmentReady,
    recover() { if (state.ackState === 'IN_FLIGHT') state.ackState = 'OUTCOME_UNKNOWN'; state.hostSeenAt = 0; commit(); },
    recoverOwnedTransport() {
      // Called only by a replacement Player owner for its supervised transport.
      // An uncertain delivered prompt must be reconciled through a recovery turn,
      // never replayed as a fresh preparation/start request.
      for (const op of state.operations.filter(unresolved)) op.state = 'CANCELLED';
      state.agentState = 'STOPPED';
      state.recoveryRequired = true;
      state.phase = state.operatorStopped ? 'OPERATOR_STOPPED' : 'RECOVERY_PENDING';
      commit();
    },
    observe(observation) {
      if (observation.matchId !== state.session?.matchId) fail('MATCH_ID_MISMATCH');
      if (terminal(state.officialStatus) && observation.status !== state.officialStatus) return publicState();
      state.officialStatus = observation.status; freshStatus = true;
      state.pollHealth = { status: 'HEALTHY', observedAt: observation.observedAt }; reconcile(); return publicState();
    },
    pollFailed() { freshStatus = false; state.pollHealth = { status: 'UNAVAILABLE', observedAt: new Date().toISOString() }; commit(); },
    async handle(message) {
      if (message?.schemaVersion !== 1) fail('INVALID_LOCAL_APP_MESSAGE');
      if (message.type === 'STATUS') return publicState();
      if (message.type === 'HOST_ATTACH') { state.hostGeneration++; state.hostSeenAt = Date.now(); commit(); return publicState(); }
      if (message.hostGeneration !== state.hostGeneration || !state.hostGeneration) fail('STALE_HOST_GENERATION');
      state.hostSeenAt = Date.now();
      if (message.type === 'HOST_OPERATIONS') return publicState();
      if (message.type === 'APP_STOP') {
        state.operatorStopped = true;
        for (const op of state.operations) if (op.state === 'PENDING') op.state = 'CANCELLED';
        if (state.agentState !== 'STOPPED') enqueue('STOP', { schemaVersion: 1, status: 'ORCHESTRATOR_STOP_REQUIRED',
          stop: { target: state.rootTarget, scope: 'TREE', reason: 'OPERATOR_STOP' } });
        state.phase = 'OPERATOR_STOPPED'; commit(); return publicState();
      }
      if (message.type === 'APP_RESUME') { state.operatorStopped = false; commit(); reconcile(); return publicState(); }
      if (['HOST_OPERATION_PEEK', 'HOST_OPERATION_GET', 'HOST_OPERATION_ACK', 'HOST_OPERATION_RECONCILE'].includes(message.type)) {
        const op = state.operations.find(op => op.id === message.operationId);
        if (!op) fail('UNKNOWN_HOST_OPERATION');
        if (message.type === 'HOST_OPERATION_PEEK') {
          if (op.state !== 'PENDING' || !permitted(op)) fail('HOST_OPERATION_NOT_EXECUTABLE');
          if (pending() !== op && op.kind !== 'STOP') fail('HOST_OPERATION_ORDER_REQUIRED');
          return { status: 'HOST_OPERATION_PREVIEW', operationId: op.id, frame: structuredClone(op.frame) };
        }
        if (message.type === 'HOST_OPERATION_GET') {
          if (op.state === 'DELIVERED') fail('HOST_RECONCILIATION_REQUIRED');
          if (op.state !== 'PENDING' || !permitted(op)) fail('HOST_OPERATION_NOT_EXECUTABLE');
          // Stops can overtake uncertain starts so paused/ended matches can be secured.
          if (pending() !== op && op.kind !== 'STOP') fail('HOST_OPERATION_ORDER_REQUIRED');
          op.state = 'DELIVERED'; op.generation = state.hostGeneration;
          if (op.spawns) state.agentState = 'UNKNOWN';
          commit(); return { status: 'HOST_OPERATION_DELIVERY', operationId: op.id, frame: structuredClone(op.frame) };
        }
        if (message.type === 'HOST_OPERATION_RECONCILE' && message.outcome === 'NOT_EXECUTED') {
          if (op.state !== 'DELIVERED') fail('INVALID_RECONCILIATION');
          op.state = permitted(op) ? 'PENDING' : 'CANCELLED';
          if (op.spawns) state.agentState = 'STOPPED';
          commit(); reconcile(); return publicState();
        }
        if (op.state === 'COMPLETED') {
          if (JSON.stringify(op.result) !== JSON.stringify(message.result)) fail('HOST_ACK_CONFLICT');
          return publicState();
        }
        // A one-shot host adapter previews the exact frame, performs the native
        // action, then acknowledges it from a separate command. This avoids
        // leaving a false DELIVERED state when Codex closes command stdin.
        if (op.state === 'PENDING' && message.type === 'HOST_OPERATION_ACK') {
          if (pending() !== op || !permitted(op)) fail('HOST_OPERATION_NOT_EXECUTABLE');
          op.generation = state.hostGeneration;
        } else if (op.state !== 'DELIVERED') fail('HOST_OPERATION_NOT_DELIVERED');
        if (message.type === 'HOST_OPERATION_ACK' && op.generation !== state.hostGeneration) fail('HOST_RECONCILIATION_REQUIRED');
        if (message.type === 'HOST_OPERATION_RECONCILE' && message.outcome !== 'EXECUTED') fail('INVALID_RECONCILIATION');
        if (op.kind === 'STOP') {
          if (message.result?.treeStopped !== true) fail('TREE_STOP_CONFIRMATION_REQUIRED');
          state.agentState = 'STOPPED';
          for (const other of state.operations.filter(unresolved)) if (other.id !== op.id) other.state = 'CANCELLED';
        } else {
          if (typeof message.result?.target !== 'string' || !/^[A-Za-z0-9_\/-]{1,256}$/.test(message.result.target)) fail('INVALID_AGENT_TARGET');
          state.rootTarget = message.result.target; state.agentState = 'RUNNING';
          if (op.kind === 'GAME') state.gameStarted = true;
        }
        op.state = 'COMPLETED'; op.result = structuredClone(message.result);
        if (op.kind !== 'STOP') state.recoveryRequired = false;
        commit(); reconcile(); return publicState();
      }
      if (message.type === 'ENVIRONMENT_READY') {
        return environmentReady(message.environmentResult);
      }
      if (message.type === 'PREPARATION_RECONCILE') {
        if (state.ackState !== 'OUTCOME_UNKNOWN') fail('PREPARATION_RECONCILIATION_NOT_REQUIRED');
        if (message.outcome === 'PREPARED') {
          state.preparedReceipt = validatePreparationReceipt(message.receipt, state.session); state.ackState = 'PREPARED';
          commit(); deps.record(state.session, state.preparedReceipt);
        } else if (message.outcome === 'NOT_ACCEPTED') state.ackState = 'NOT_SENT';
        else fail('INVALID_RECONCILIATION');
        commit(); reconcile(); return publicState();
      }
      if (message.type === 'ORCHESTRATOR_EXITED') {
        if (pending()) fail('HOST_RECONCILIATION_REQUIRED');
        state.agentState = 'STOPPED'; commit(); reconcile(); return publicState();
      }
      fail('UNKNOWN_LOCAL_APP_MESSAGE');
    }
  };
}
