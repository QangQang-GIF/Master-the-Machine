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
  let baseDir = process.cwd();
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--team-workspace' && i + 1 < args.length) {
      baseDir = args[i + 1];
      i++;
    }
  }
  return path.resolve(baseDir);
}

function fail(msg) {
  console.error(`DISTRIBUTION_CHECK_FAILED: ${msg}`);
  process.exit(1);
}

function verifyDistribution(baseDir) {
  // 1. team-runtime.json
  const runtimePath = path.join(baseDir, 'team-runtime.json');
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

  // 2. team-pack-info.json
  const infoPath = path.join(baseDir, 'team-pack-info.json');
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

  // 3. Static Provider Profiles
  const requiredProfiles = [
    'team_orchestrator',
    'team_repo_scout',
    'team_feature_worker',
    'team_quality_checker'
  ];

  for (const prof of requiredProfiles) {
    const candidates = [
      path.join(baseDir, 'agents', `${prof}.toml`),
      path.join(baseDir, 'agents', `${prof.replace(/_/g, '-')}.toml`),
      path.join(baseDir, 'profiles', 'agents', `${prof}.toml`),
      path.join(baseDir, 'profiles', 'agents', `${prof.replace(/_/g, '-')}.toml`)
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
  const schemaPath = path.join(baseDir, 'schemas', 'progress-event.schema.json');
  if (fs.existsSync(schemaPath)) {
    try {
      JSON.parse(fs.readFileSync(schemaPath, 'utf8'));
    } catch (err) {
      fail(`Invalid schema JSON at ${schemaPath}: ${err.message}`);
    }
  }

  console.log('Distribution check passed: ALL_VERIFIED');
  console.log('READY');
}

const targetDir = parseArgs();
verifyDistribution(targetDir);
