import { createHash } from 'node:crypto';
import { gzipSync } from 'node:zlib';

const id = value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(value);

export function createDashboardStreamingClient({ session, roundId, getSnapshot,
  fetchImpl = globalThis.fetch, intervalMs = 1_000, endpointPath = '/api/v1/game-player/dashboardSnapshot',
  onStatus = () => {} }) {
  if (!session || !id(session.matchId) || !id(session.teamId) || !id(session.playerSessionId) ||
      typeof session.gameMasterPlayerSecret !== 'string' || !session.gameMasterPlayerSecret ||
      !id(roundId) || typeof getSnapshot !== 'function' || typeof fetchImpl !== 'function' ||
      !Number.isSafeInteger(intervalMs) || intervalMs < 100 || typeof onStatus !== 'function') throw Error('INVALID_DASHBOARD_STREAMING_CONFIG');
  const endpoint = new URL(endpointPath, session.gameMasterApiUrl);
  let timer = null, stopped = false, sending = false, lastHash = null, revision = 0;
  let state = { enabled: true, status: 'STARTING', lastAttemptAt: null, lastSuccessAt: null,
    lastHttpStatus: null, revision: 0, lastHash: null, lastError: null,
    lastSnapshotBytes: null, lastUploadBytes: null };
  const publish = change => { state = Object.freeze({ ...state, ...change });
    try { onStatus(state); } catch { /* diagnostics cannot interrupt streaming */ } };
  const poll = async () => {
    if (stopped || sending) return;
    sending = true;
    try {
      const dashboard = await getSnapshot();
      const serialized = JSON.stringify(dashboard);
      const contentHash = `sha256:${createHash('sha256').update(serialized).digest('hex')}`;
      if (contentHash === lastHash) return;
      const body = { schemaVersion: 1, type: 'ROUND_DASHBOARD_SNAPSHOT', matchId: session.matchId,
        roundId, playerSessionId: session.playerSessionId, teamId: session.teamId,
        generatedAt: new Date().toISOString(), contentHash, dashboard };
      const requestBody = Buffer.from(JSON.stringify(body));
      const compressedBody = gzipSync(requestBody);
      const attemptedAt = new Date().toISOString();
      publish({ status: revision ? 'UPDATING' : 'CONNECTING', lastAttemptAt: attemptedAt, lastError: null,
        lastSnapshotBytes: Buffer.byteLength(serialized), lastUploadBytes: compressedBody.byteLength });
      const response = await fetchImpl(endpoint, { method: 'POST', redirect: 'manual', cache: 'no-store',
        headers: { authorization: `Bearer ${session.gameMasterPlayerSecret}`,
          'content-type': 'application/json', 'content-encoding': 'gzip', accept: 'application/json' },
        body: compressedBody, signal: AbortSignal.timeout(15_000) });
      if (!response.ok) { publish({ status: 'RETRYING', lastHttpStatus: response.status,
        lastError: `HTTP_${response.status}` }); return; }
      lastHash = contentHash; revision += 1;
      publish({ status: 'CONNECTED', lastSuccessAt: new Date().toISOString(),
        lastHttpStatus: response.status, revision, lastHash, lastError: null });
    } catch { publish({ status: 'RETRYING', lastHttpStatus: null, lastError: 'STREAMING_UNAVAILABLE' }); }
    finally { sending = false; }
  };
  timer = setInterval(() => { void poll(); }, intervalMs); timer.unref?.(); void poll();
  return Object.freeze({ poll, status: () => state,
    close: () => { stopped = true; if (timer) clearInterval(timer); timer = null;
      publish({ status: 'STOPPED' }); } });
}
