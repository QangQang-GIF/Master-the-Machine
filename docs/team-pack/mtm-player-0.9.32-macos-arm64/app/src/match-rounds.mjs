import { existsSync, lstatSync, mkdirSync, readFileSync, copyFileSync, writeFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { dashboardDocument } from './progress.mjs';

const fail = () => { throw Object.assign(new Error('INVALID_ROUND_PATH'), { code: 'INVALID_ROUND_PATH' }); };
export const roundName = number => `round${String(number).padStart(2, '0')}`;
export const roundRoute = match => `/${match.session.matchId}/${match.roundId ?? 'round01'}`;
export function roundSummary(match, port) {
  const status = ['FINISHED', 'CANCELLED'].includes(match.phase) ? match.phase :
    match.operatorStopped ? 'STOPPED' : match.phase ?? 'WAITING';
  return { matchId: match.session.matchId, roundId: match.roundId ?? 'round01',
    startedAt: match.startedAt ?? null, endedAt: match.endedAt ?? null,
    status, officialStatus: match.officialStatus ?? 'UNKNOWN', agentState: match.agentState ?? 'UNKNOWN',
    cliProvider: match.cliRuntime?.provider ?? null, cliStatus: match.cliRuntime?.status ?? null,
    cliSessionId: match.cliRuntime?.sessionId ?? null,
    tokenUsage: match.cliRuntime?.tokenUsage ?? null,
    lastObservedAt: match.pollHealth?.observedAt ?? null,
    dashboardUrl: `http://127.0.0.1:${port}${roundRoute(match)}` };
}
export function listMatchRounds(state, matchId = state?.match?.session.matchId) {
  if (matchId !== undefined && !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(matchId)) fail();
  const rows = [...(state?.rounds ?? [])].filter(row => row.matchId === matchId);
  if (state?.match && state.match.session.matchId === matchId) {
    const current = roundSummary(state.match, state.port);
    const index = rows.findIndex(row => row.roundId === current.roundId);
    if (index < 0) rows.push(current); else rows[index] = current;
  }
  return { schemaVersion: 1, source: 'LAST_RECORDED_STATE', matchId: matchId ?? null,
    rounds: rows.sort((a, b) => Number(a.roundId.slice(5)) - Number(b.roundId.slice(5))) };
}
export function roundDirectory(root, matchId, roundId) {
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(matchId) || !/^round\d{2,}$/.test(roundId)) fail();
  let directory = root;
  for (const segment of ['rounds', matchId, roundId]) {
    directory = join(directory, segment);
    if (!existsSync(directory)) mkdirSync(directory);
    if (lstatSync(directory).isSymbolicLink() || !lstatSync(directory).isDirectory()) fail();
  }
  return directory;
}
function regular(path) {
  if (existsSync(path) && (lstatSync(path).isSymbolicLink() || !lstatSync(path).isFile())) fail();
}
export function archiveRound(root, match, snapshot) {
  const directory = roundDirectory(root, match.session.matchId, match.roundId);
  for (const [file, content] of [['snapshot.json', JSON.stringify(snapshot)],
    ['dashboard.html', dashboardDocument(snapshot)]]) {
    const path = join(directory, file); regular(path); writeFileSync(path, content, { mode: 0o600 });
  }
  const logs = join(match.session.teamSdlcPackWorkspace, 'logs');
  if (existsSync(logs) && lstatSync(logs).isSymbolicLink()) fail();
  for (const suffix of ['progress.ndjson', 'player-progress.json', 'player-checkpoint.json',
    'match-status.json', 'match-status.ndjson']) {
    const source = join(logs, `${match.session.matchId}.${suffix}`);
    const target = join(directory, suffix); regular(source); regular(target);
    if (existsSync(source)) copyFileSync(source, target);
  }
}
export function readArchivedRound(root, row, file) {
  if (!['snapshot.json', 'dashboard.html'].includes(file)) fail();
  const path = join(roundDirectory(root, row.matchId, row.roundId), file);
  regular(path); return readFileSync(path, 'utf8');
}
export function clearRoundMetadata(session) {
  const logs = join(session.teamSdlcPackWorkspace, 'logs');
  if (existsSync(logs) && lstatSync(logs).isSymbolicLink()) fail();
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(session.matchId)) fail();
  const paths = ['player-checkpoint.json', 'match-status.json', 'match-status.ndjson']
    .map(suffix => join(logs, `${session.matchId}.${suffix}`));
  paths.forEach(regular);
  paths.forEach(path => rmSync(path, { force: true }));
}
