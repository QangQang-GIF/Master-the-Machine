import { createHash } from 'node:crypto';
import { existsSync, lstatSync, readdirSync, rmSync, rmdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { matchWorkspacePaths } from './workspace-paths.mjs';
import { validateTeamWorkspaceLayout, ensureEmptyChallengeWorkspace } from './bootstrap.mjs';

/** Remove only this attempt's Player artifacts; never host-owned transcripts. */
export function clearMatchArtifacts(session, dataDirectory) {
  const layout = validateTeamWorkspaceLayout(session.teamWorkspace, { configDir: dataDirectory });
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(session.matchId)) throw Error('INVALID_MATCH_ID');
  const logs = join(layout.teamSdlcPackWorkspace, 'logs');
  const bindings = join(dataDirectory, 'codex-task-bindings');
  const registry = join(bindings, createHash('sha256').update(JSON.stringify(session.playerSessionId)).digest('hex'));
  for (const folder of [logs, bindings, registry]) {
    if (existsSync(folder) && (lstatSync(folder).isSymbolicLink() || !lstatSync(folder).isDirectory())) {
      throw Object.assign(new Error('UNSAFE_RESET_PATH'), { code: 'UNSAFE_RESET_PATH' });
    }
  }
  const targets = ['progress.ndjson','player-progress.json','dashboard.html','player-checkpoint.json',
    'match-status.json','match-status.ndjson'].map(suffix => join(logs, `${session.matchId}.${suffix}`));
  if (existsSync(registry)) for (const name of readdirSync(registry)) {
    if (!/^(?:\d{9}|transcript-[a-f0-9]{64})\.json$/.test(name)) throw Object.assign(new Error('UNSAFE_RESET_PATH'), { code: 'UNSAFE_RESET_PATH' });
    targets.push(join(registry, name));
  }
  for (const path of targets) if (existsSync(path) && (lstatSync(path).isSymbolicLink() || !lstatSync(path).isFile())) {
    throw Object.assign(new Error('UNSAFE_RESET_PATH'), { code: 'UNSAFE_RESET_PATH' });
  }
  const { checkoutRoot } = matchWorkspacePaths({ ...session,
    challengeWorkspace: session.challengeWorkspace ?? layout.matchesWorkspace });
  if (![resolve(session.teamWorkspace, 'matches', session.matchId),
      resolve(session.teamWorkspace, 'challenge', session.matchId)].includes(checkoutRoot)) {
    throw Object.assign(new Error('UNSAFE_RESET_PATH'), { code: 'UNSAFE_RESET_PATH' });
  }
  if (existsSync(checkoutRoot)) {
    if (lstatSync(checkoutRoot).isSymbolicLink() || !lstatSync(checkoutRoot).isDirectory()) throw Error('UNSAFE_RESET_PATH');
    ensureEmptyChallengeWorkspace({ challengeWorkspace: checkoutRoot }, { clearChallenge: true });
  }
  for (const path of targets) rmSync(path, { force: true });
  if (existsSync(registry)) rmdirSync(registry);
}
