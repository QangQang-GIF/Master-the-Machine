import { resolve } from 'node:path';
import { matchWorkspacePaths } from './workspace-paths.mjs';

const activeGameHeader = /^(?:START_GAME|RESUME_GAME)_V\d+$/;
const portable = value => value.replaceAll('\\', '/');

function descriptorFromMessage(message) {
  if (typeof message !== 'string') return null;
  const [header, json] = message.split('\n', 2);
  if (!activeGameHeader.test(header) || !json) return null;
  try {
    const descriptor = JSON.parse(json);
    return descriptor && typeof descriptor === 'object' ? descriptor : null;
  } catch { return null; }
}

function policyFor(descriptor) {
  const paths = matchWorkspacePaths(descriptor);
  const repositoryRoot = portable(paths.matchRoot);
  return Object.freeze({
    schemaVersion: 1,
    policyId: 'MTM_SUBMISSION_POLICY_V1',
    scope: 'ACTIVE_GAME_BACKLOG_SUBMISSIONS',
    repositoryRoot,
    checkoutRoot: portable(paths.checkoutRoot), matchRoot: repositoryRoot,
    startWorkTemplate: `${repositoryRoot}/challenge/<BACKLOG-ID>-submit.json`,
    canonicalSubmitFile: `${repositoryRoot}/answers/<BACKLOG-ID>/submit.json`,
    canonicalEvidenceDirectory: `${repositoryRoot}/answers/<BACKLOG-ID>/evidence/`,
    evidenceReferenceFormat: 'evidence/<filename>',
    forbiddenEvidenceDirectories: Object.freeze([
      `${repositoryRoot}/evidence/`,
      `${repositoryRoot}/challenge/evidence/`
    ]),
    instruction: 'A submit template returned by Start Work is source material only. For each backlog, materialize answers/<BACKLOG-ID>/submit.json and store every evidence file under answers/<BACKLOG-ID>/evidence/. In submit.json, use paths relative to the backlog answer directory, such as evidence/<filename>. Never commit backlog evidence to repository-root evidence/ or challenge/evidence/. Validate these paths before commit and before submission. The team may implement this rule with MCP, a helper, or its own orchestration code.'
  });
}

/** Adds the organizer-owned submission contract only to active-game instructions. */
export function submissionPolicyFrame(frame) {
  const result = structuredClone(frame);
  for (const key of ['spawn', 'followup']) {
    const descriptor = descriptorFromMessage(result[key]?.message);
    if (!descriptor || typeof descriptor.challengeWorkspace !== 'string' ||
        typeof descriptor.matchId !== 'string') continue;
    result[key].message += `\n\nMTM_SUBMISSION_POLICY_V1\n${JSON.stringify(policyFor(descriptor))}`;
  }
  return result;
}
