import { acknowledgeEnvironmentReady } from './preparation.mjs';
import { prepareNativeGameStart } from './team-runtime.mjs';

export class ActiveMatchSessionError extends Error {
  constructor(code) { super(code); this.code = code; this.name = 'ActiveMatchSessionError'; }
}

const fail = code => { throw new ActiveMatchSessionError(code); };
const object = value => value && typeof value === 'object' && !Array.isArray(value);
const id = value => typeof value === 'string' &&
  /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(value);

/**
 * Owns the sensitive bootstrap result for one match. The returned object deliberately
 * exposes no session, endpoint, credential, descriptor, or authorization property.
 */
export function createActiveMatchSession({ session, manifest, preparation }, {
  acknowledge = acknowledgeEnvironmentReady,
  prepareGameStart = prepareNativeGameStart,
  continueOrchestrator,
  onPrepared = async () => {},
  acknowledgementOptions = {}
} = {}) {
  if (!object(session) || !Object.isFrozen(session) ||
      !object(session.preparationComplete) || !Object.isFrozen(session.preparationComplete) ||
      !object(manifest) || !object(preparation) || !object(preparation.receipt) ||
      !id(preparation.receipt.requestId) || !object(preparation.repository) ||
      !id(preparation.target) || typeof acknowledge !== 'function' ||
      typeof prepareGameStart !== 'function' || typeof continueOrchestrator !== 'function' ||
      typeof onPrepared !== 'function') {
    fail('INVALID_ACTIVE_MATCH_SESSION');
  }
  const retainedPreparation = session.preparationComplete;
  const retainedEndpoint = retainedPreparation.url;
  const preparationRequestId = preparation.receipt.requestId;
  let rootTarget = preparation.target;
  let preparedReceipt = null;
  let continuation = null;
  let publicReceipt = null;
  let startRequestId = null;
  let inFlight = null;
  let checkpointSaved = false;

  const assertRetainedContract = () => {
    if (session.preparationComplete !== retainedPreparation ||
        retainedPreparation.url !== retainedEndpoint) fail('ACTIVE_SESSION_CONTRACT_CHANGED');
  };

  return Object.freeze({
    async acknowledgeAndContinue({ environmentResult, requestId }) {
      if (!id(requestId)) fail('INVALID_GAME_START_REQUEST');
      if (startRequestId !== null && startRequestId !== requestId) {
        fail('ACTIVE_SESSION_REQUEST_MISMATCH');
      }
      startRequestId = requestId;
      assertRetainedContract();
      if (publicReceipt) return publicReceipt;
      if (!inFlight) {
        inFlight = (async () => {
          if (!preparedReceipt) {
            preparedReceipt = await acknowledge({ session,
              preparationRequestId, repository: preparation.repository, environmentResult },
            acknowledgementOptions);
          }
          if (!checkpointSaved) {
            await onPrepared(preparedReceipt);
            checkpointSaved = true;
          }
          if (!continuation) {
            continuation = prepareGameStart(session, manifest, preparedReceipt, requestId);
            if (!object(continuation.followup) || !id(rootTarget)) {
              continuation = null;
              fail('ORCHESTRATOR_TARGET_CHANGED');
            }
            if (continuation.followup.target !== rootTarget) {
              continuation = Object.freeze({ ...continuation,
                followup: Object.freeze({ ...continuation.followup, target: rootTarget }) });
            }
          }
          await continueOrchestrator(continuation.followup);
          publicReceipt = Object.freeze({ schemaVersion: 1, status: 'PREPARED',
            requestId: preparationRequestId,
            gameMasterRequestId: preparedReceipt.gameMasterRequestId,
            matchId: preparedReceipt.matchId,
            environmentRevision: preparedReceipt.environmentRevision });
          return publicReceipt;
        })();
        inFlight.finally(() => { inFlight = null; }).catch(() => {});
      }
      return inFlight;
    },
    status() {
      return Object.freeze({ schemaVersion: 1,
        status: publicReceipt || preparedReceipt ? 'PREPARED' : 'PREPARING',
        requestId: preparationRequestId, matchId: session.matchId });
    },
    replaceRootTarget(target) {
      if (!id(target)) fail('INVALID_ORCHESTRATOR_TARGET');
      rootTarget = target;
      continuation = null;
    }
  });
}
