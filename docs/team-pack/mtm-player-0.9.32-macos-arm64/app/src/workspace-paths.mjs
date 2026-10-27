import { isAbsolute, resolve } from 'node:path';

/** Logical repository paths are independent of the directory used to clone Git. */
export function matchWorkspacePaths(session) {
  const folder = session.repository?.folder ?? session.matchId;
  if (typeof folder !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(folder) || folder === '..') {
    throw Object.assign(new Error('INVALID_REPOSITORY_FOLDER'), { code: 'INVALID_REPOSITORY_FOLDER' });
  }
  const checkoutRoot = session.checkoutRoot ?? resolve(session.challengeWorkspace, session.matchId);
  const matchRoot = resolve(checkoutRoot, folder);
  if (!isAbsolute(checkoutRoot) ||
      (session.checkoutRoot && session.challengeWorkspace &&
        resolve(checkoutRoot) !== resolve(session.challengeWorkspace, session.matchId)) ||
      (session.matchRoot && resolve(session.matchRoot) !== matchRoot)) {
    throw Object.assign(new Error('INVALID_MATCH_ROOT'), { code: 'INVALID_MATCH_ROOT' });
  }
  return { checkoutRoot: resolve(checkoutRoot), matchRoot };
}
