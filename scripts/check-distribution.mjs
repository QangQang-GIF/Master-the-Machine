#!/usr/bin/env node
/**
 * check-distribution.mjs: Read-only distribution verification script.
 * Verifies registrations/payload parity, identity/version, schema/manifest.
 * Outputs "READY" and exits 0 on success; non-zero on failure.
 */

import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

function parseArgs() {
  const args = process.argv.slice(2);
  let teamWorkspace = process.cwd();
  let packRoot = null;
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--team-workspace' && i + 1 < args.length) {
      teamWorkspace = args[i + 1];
      i++;
    } else if (args[i] === '--pack-root' && i + 1 < args.length) {
      packRoot = args[i + 1];
      i++;
    }
  }
  const wsDir = path.resolve(teamWorkspace);
  let packDir = packRoot ? path.resolve(packRoot) : null;
  if (!packDir) {
    if (fs.existsSync(path.join(wsDir, 'team-sdlc-pack', 'team-runtime.json'))) {
      packDir = path.join(wsDir, 'team-sdlc-pack');
    } else if (fs.existsSync(path.join(wsDir, 'team-runtime.json'))) {
      packDir = wsDir;
    } else if (path.basename(wsDir) === 'team-sdlc-pack') {
      packDir = wsDir;
    } else {
      packDir = path.join(wsDir, 'team-sdlc-pack');
    }
  }
  return { wsDir, packDir };
}

function fail(msg) {
  console.error(`DISTRIBUTION_CHECK_FAILED: ${msg}`);
  process.exit(1);
}

function verifyDistribution(wsDir, packDir) {
  // 1. team-runtime.json
  const runtimePath = path.join(packDir, 'team-runtime.json');
  if (!fs.existsSync(runtimePath)) {
    fail(`Missing team-runtime.json at ${runtimePath}`);
  }
  let runtime;
  try {
    runtime = JSON.parse(fs.readFileSync(runtimePath, 'utf8'));
  } catch (err) {
    fail(`Invalid JSON in team-runtime.json: ${err.message}`);
  }
  if (runtime.schemaVersion !== 1) fail(`team-runtime.json schemaVersion must be 1, got ${runtime.schemaVersion}`);
  if (runtime.kind !== 'mtm-team-runtime') fail(`team-runtime.json kind must be "mtm-team-runtime", got "${runtime.kind}"`);
  if (runtime.name !== 'team-sdlc-pack') fail(`team-runtime.json name must be "team-sdlc-pack", got "${runtime.name}"`);
  if (runtime.protocolVersion !== 4) fail(`team-runtime.json protocolVersion must be 4, got ${runtime.protocolVersion}`);
  if (runtime.provider !== 'codex') fail(`team-runtime.json provider must be "codex", got "${runtime.provider}"`);
  if (!runtime.entrypoint || runtime.entrypoint.rootAgentType !== 'team_orchestrator') {
    fail(`team-runtime.json entrypoint.rootAgentType must be "team_orchestrator"`);
  }

  // 1b. Parity check with .mtm/team-runtime.json
  const mtmRuntimePath = path.join(wsDir, '.mtm', 'team-runtime.json');
  if (!fs.existsSync(mtmRuntimePath)) {
    fail(`Missing mandatory manifest at ${mtmRuntimePath}`);
  }
  let mtmData;
  try {
    mtmData = JSON.parse(fs.readFileSync(mtmRuntimePath, 'utf8'));
  } catch (err) {
    fail(`Corrupted or invalid JSON in ${mtmRuntimePath}: ${err.message}`);
  }
  for (const field of ['schemaVersion', 'kind', 'name', 'version', 'protocolVersion', 'provider']) {
    if (mtmData[field] !== runtime[field]) {
      fail(`Manifest mismatch in .mtm/team-runtime.json field "${field}": "${mtmData[field]}" vs "${runtime[field]}"`);
    }
  }
  if (JSON.stringify(mtmData.entrypoint) !== JSON.stringify(runtime.entrypoint)) {
    fail(`Manifest mismatch in .mtm/team-runtime.json "entrypoint"`);
  }

  // 2. team-pack-info.json
  const infoPath = path.join(packDir, 'team-pack-info.json');
  if (!fs.existsSync(infoPath)) {
    fail(`Missing team-pack-info.json at ${infoPath}`);
  }
  let info;
  try {
    info = JSON.parse(fs.readFileSync(infoPath, 'utf8'));
  } catch (err) {
    fail(`Invalid JSON in team-pack-info.json: ${err.message}`);
  }
  const infoKeys = Object.keys(info);
  const requiredKeys = ['schemaVersion', 'version', 'teamId', 'teamName'];
  if (infoKeys.length !== 4 || !requiredKeys.every(k => k in info)) {
    fail(`team-pack-info.json must contain exactly 4 keys (${requiredKeys.join(', ')}), got: ${infoKeys.join(', ')}`);
  }
  if (info.schemaVersion !== 1) fail(`team-pack-info.json schemaVersion must be 1`);
  if (info.version !== runtime.version) fail(`Version mismatch: team-runtime (${runtime.version}) vs team-pack-info (${info.version})`);
  if (!info.teamId || typeof info.teamId !== 'string') fail(`Invalid teamId in team-pack-info.json`);
  if (!info.teamName || typeof info.teamName !== 'string') fail(`Invalid teamName in team-pack-info.json`);

  // 2b. Parity check with .mtm/team-pack-info.json
  const mtmInfoPath = path.join(wsDir, '.mtm', 'team-pack-info.json');
  if (!fs.existsSync(mtmInfoPath)) {
    fail(`Missing mandatory manifest at ${mtmInfoPath}`);
  }
  let mtmInfoData;
  try {
    mtmInfoData = JSON.parse(fs.readFileSync(mtmInfoPath, 'utf8'));
  } catch (err) {
    fail(`Corrupted or invalid JSON in ${mtmInfoPath}: ${err.message}`);
  }
  for (const field of ['schemaVersion', 'version', 'teamId', 'teamName']) {
    if (mtmInfoData[field] !== info[field]) {
      fail(`Manifest mismatch in .mtm/team-pack-info.json field "${field}": "${mtmInfoData[field]}" vs "${info[field]}"`);
    }
  }

  // 2c. Root package.json check and files array verification (R3 & R6)
  const wsPkgPath = path.join(wsDir, 'package.json');
  if (!fs.existsSync(wsPkgPath)) {
    fail(`Missing root package.json in TeamWorkspace at ${wsPkgPath}`);
  }
  let pkgData;
  try {
    pkgData = JSON.parse(fs.readFileSync(wsPkgPath, 'utf8'));
  } catch (err) {
    fail(`Invalid root package.json at ${wsPkgPath}: ${err.message}`);
  }

  const REQUIRED_FILES = [
    'agents/',
    'bin/',
    'config/',
    'contracts/',
    'docs/',
    'install.ps1',
    'scripts/check-distribution.mjs',
    'scripts/install.mjs',
    'skills/',
    'src/',
    'README.md',
    'team-pack-info.json',
    'team-runtime.json'
  ];

  if (!Array.isArray(pkgData.files)) {
    fail(`Root package.json at ${wsPkgPath} must contain a 'files' array`);
  }
  if (JSON.stringify(pkgData.files) !== JSON.stringify(REQUIRED_FILES)) {
    fail(`Root package.json 'files' array mismatch.\nExpected: ${JSON.stringify(REQUIRED_FILES)}\nGot: ${JSON.stringify(pkgData.files)}`);
  }
  if (pkgData.name !== runtime.name) {
    fail(`Root package.json 'name' mismatch: "${pkgData.name}" vs runtime "${runtime.name}"`);
  }
  if (pkgData.version !== runtime.version) {
    fail(`Root package.json 'version' mismatch: "${pkgData.version}" vs runtime "${runtime.version}"`);
  }

  // 3. Static Provider Profiles
  const requiredProfiles = [
    'team_orchestrator',
    'team_repo_scout',
    'team_feature_worker',
    'team_quality_checker'
  ];

  for (const prof of requiredProfiles) {
    const candidates = [
      path.join(packDir, 'agents', `${prof}.toml`),
      path.join(packDir, 'agents', `${prof.replace(/_/g, '-')}.toml`),
      path.join(packDir, 'profiles', 'agents', `${prof}.toml`),
      path.join(packDir, 'profiles', 'agents', `${prof.replace(/_/g, '-')}.toml`),
      path.join(packDir, '.codex', 'agents', `${prof}.toml`),
      path.join(wsDir, '.codex', 'agents', `${prof}.toml`),
      path.join(wsDir, 'agents', `${prof}.toml`)
    ];
    const found = candidates.find(p => fs.existsSync(p));
    if (!found) {
      fail(`Missing provider profile for "${prof}". Checked:\n  ${candidates.join('\n  ')}`);
    }
    const content = fs.readFileSync(found, 'utf8');
    const nameMatch = content.match(/^name\s*=\s*["']([^"']+)["']/m);
    if (!nameMatch || nameMatch[1] !== prof) {
      fail(`Profile at ${found} must declare name = "${prof}"`);
    }
  }

  // 4. Progress Event Schema
  const schemaPath = path.join(packDir, 'schemas', 'progress-event.schema.json');
  const wsSchemaPath = path.join(wsDir, 'schemas', 'progress-event.schema.json');
  const targetSchema = fs.existsSync(schemaPath) ? schemaPath : wsSchemaPath;
  if (fs.existsSync(targetSchema)) {
    try {
      JSON.parse(fs.readFileSync(targetSchema, 'utf8'));
    } catch (err) {
      fail(`Invalid schema JSON at ${targetSchema}: ${err.message}`);
    }
  }

  // 5. Root Skill Verification (team-orchestrator/SKILL.md)
  const skillCandidates = [
    path.join(packDir, 'skills', 'team-orchestrator', 'SKILL.md'),
    path.join(packDir, '.agents', 'skills', 'team-orchestrator', 'SKILL.md'),
    path.join(wsDir, '.agents', 'skills', 'team-orchestrator', 'SKILL.md'),
    path.join(wsDir, 'skills', 'team-orchestrator', 'SKILL.md'),
  ];
  const skillFound = skillCandidates.find(p => fs.existsSync(p) && fs.statSync(p).size > 50);
  if (!skillFound) {
    fail(`Missing team-orchestrator/SKILL.md. Checked:\n  ${skillCandidates.join('\n  ')}`);
  }

  console.log('Distribution check passed: ALL_VERIFIED');
  console.log('READY');
}

const { wsDir, packDir } = parseArgs();
verifyDistribution(wsDir, packDir);
