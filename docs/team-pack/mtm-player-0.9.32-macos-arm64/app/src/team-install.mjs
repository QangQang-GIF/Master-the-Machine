import { existsSync, lstatSync, readFileSync, readdirSync, realpathSync, statSync } from 'node:fs';
import { dirname, isAbsolute, join, relative, resolve } from 'node:path';
import { discoverTeamRuntime } from './team-runtime.mjs';

export class TeamRuntimeSetupError extends Error {
  constructor(code) { super(code); this.code = code; this.name = 'TeamRuntimeSetupError'; }
}

const fail = code => { throw new TeamRuntimeSetupError(code); };
const legacyPackageEntries = Object.freeze(['agents/', 'bin/', 'contracts/', 'docs/', 'install.ps1',
  'scripts/install.mjs', 'skills/', 'src/', 'README.md', 'team-pack-info.json', 'team-runtime.json']);
const packageEntriesWithoutChecker = Object.freeze(['agents/', 'bin/', 'config/', 'contracts/', 'docs/', 'install.ps1',
  'scripts/install.mjs', 'skills/', 'src/', 'README.md', 'team-pack-info.json', 'team-runtime.json']);
const packageEntries = Object.freeze(['agents/', 'bin/', 'config/', 'contracts/', 'docs/', 'install.ps1',
  'scripts/check-distribution.mjs', 'scripts/install.mjs', 'skills/', 'src/', 'README.md',
  'team-pack-info.json', 'team-runtime.json']);
const packageEntrySets = Object.freeze([packageEntries, packageEntriesWithoutChecker, legacyPackageEntries]);
const requiredRegistrationFiles = Object.freeze([
  ['agents/team-orchestrator.toml', '.codex/agents/team-orchestrator.toml'],
  ['team-pack-info.json', '.mtm/team-pack-info.json'],
  ['team-runtime.json', '.mtm/team-runtime.json']
]);

function regularFile(path, code = 'INVALID_TEAM_PACK') {
  if (!existsSync(path)) fail(code);
  const stat = lstatSync(path);
  if (stat.isSymbolicLink() || !stat.isFile()) fail(code);
  return stat;
}

function directory(path, code = 'INVALID_TEAM_PACK') {
  if (!existsSync(path)) fail(code);
  const stat = lstatSync(path);
  if (stat.isSymbolicLink() || !stat.isDirectory()) fail(code);
  return realpathSync(path);
}

function safeTarget(root, target) {
  const absolute = resolve(root, target);
  const rel = relative(root, absolute);
  if (rel.startsWith('..') || isAbsolute(rel)) fail('UNSAFE_TEAM_PACK_PATH');
  let cursor = dirname(absolute);
  while (cursor !== root) {
    if (existsSync(cursor) && lstatSync(cursor).isSymbolicLink()) fail('UNSAFE_TEAM_PACK_PATH');
    const parent = dirname(cursor);
    if (parent === cursor) fail('UNSAFE_TEAM_PACK_PATH');
    cursor = parent;
  }
  return absolute;
}

function filesBelow(root, entry) {
  const start = safeTarget(root, entry);
  const stat = lstatSync(start);
  if (stat.isSymbolicLink()) fail('INVALID_TEAM_PACK');
  if (stat.isFile()) return [entry.replaceAll('\\', '/')];
  if (!stat.isDirectory()) fail('INVALID_TEAM_PACK');
  const files = [];
  const visit = (absolute, rel) => {
    for (const name of readdirSync(absolute)) {
      const child = join(absolute, name);
      const childRel = join(rel, name).replaceAll('\\', '/');
      const childStat = lstatSync(child);
      if (childStat.isSymbolicLink()) fail('INVALID_TEAM_PACK');
      if (childStat.isDirectory()) visit(child, childRel);
      else if (childStat.isFile()) files.push(childRel);
      else fail('INVALID_TEAM_PACK');
    }
  };
  visit(start, entry.replace(/[\\/]$/, ''));
  return files;
}

function inspectPackage(rootInput) {
  const root = directory(rootInput);
  let manifest;
  try {
    regularFile(join(root, 'package.json'));
    manifest = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
  } catch (error) {
    if (error instanceof TeamRuntimeSetupError) throw error;
    fail('INVALID_TEAM_PACK');
  }
  const lifecycle = ['preinstall', 'install', 'postinstall', 'prepare'];
  const entries = packageEntrySets.find(candidate =>
    JSON.stringify(manifest.files) === JSON.stringify(candidate));
  if (manifest.name !== 'team-sdlc-pack' || !/^\d+\.\d+\.\d+$/.test(manifest.version) ||
      !entries ||
      ['dependencies', 'optionalDependencies', 'peerDependencies', 'bundledDependencies']
        .some(key => manifest[key] && Object.keys(manifest[key]).length > 0) ||
      lifecycle.some(key => typeof manifest.scripts?.[key] === 'string')) fail('INVALID_TEAM_PACK');
  let runtimeManifest;
  try {
    regularFile(join(root, 'team-runtime.json'));
    runtimeManifest = JSON.parse(readFileSync(join(root, 'team-runtime.json'), 'utf8'));
  } catch (error) {
    if (error instanceof TeamRuntimeSetupError) throw error;
    fail('INVALID_TEAM_PACK');
  }
  if (runtimeManifest?.kind !== 'mtm-team-runtime' || runtimeManifest?.name !== manifest.name ||
      runtimeManifest?.version !== manifest.version || runtimeManifest?.protocolVersion !== 4 ||
      runtimeManifest?.entrypoint?.rootAgentType !== 'team_orchestrator') fail('INVALID_TEAM_PACK');
  let teamPack;
  try {
    regularFile(join(root, 'team-pack-info.json'));
    teamPack = JSON.parse(readFileSync(join(root, 'team-pack-info.json'), 'utf8'));
  } catch (error) {
    if (error instanceof TeamRuntimeSetupError) throw error;
    fail('INVALID_TEAM_PACK_INFO');
  }
  if (!exactTeamPackInfo(teamPack) || teamPack.version !== manifest.version) {
    fail('INVALID_TEAM_PACK_INFO');
  }
  const files = Object.freeze(['package.json', ...entries.flatMap(entry => filesBelow(root, entry))]);
  return Object.freeze({ root, manifest, runtimeManifest, teamPack, files });
}

function exactTeamPackInfo(value) {
  return value && typeof value === 'object' && !Array.isArray(value) &&
    JSON.stringify(Object.keys(value).sort()) ===
      JSON.stringify(['schemaVersion', 'teamId', 'teamName', 'version'].sort()) &&
    value.schemaVersion === 1 && /^\d+\.\d+\.\d+$/.test(value.version) &&
    /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(value.teamId) &&
    typeof value.teamName === 'string' && value.teamName.trim() === value.teamName &&
    value.teamName.length > 0 && value.teamName.length <= 160 && !/[\0\r\n]/.test(value.teamName);
}

function verifyInstalled(layout, referenceRoot) {
  const runtime = inspectPackage(join(layout.teamSdlcPackWorkspace, 'runtime'));
  if (referenceRoot) {
    const reference = inspectPackage(referenceRoot);
    if (runtime.manifest.version !== reference.manifest.version ||
        JSON.stringify(runtime.files) !== JSON.stringify(reference.files)) fail('INSTALLED_RUNTIME_TAMPERED');
    for (const path of runtime.files) {
      if (readFileSync(resolve(runtime.root, path), 'utf8') !==
          readFileSync(resolve(reference.root, path), 'utf8')) fail('INSTALLED_RUNTIME_TAMPERED');
    }
  }
  for (const [from, to] of requiredRegistrationFiles) {
    const target = safeTarget(layout.teamWorkspace, to);
    regularFile(target, 'TEAM_RUNTIME_NOT_INSTALLED');
    const actual = readFileSync(target, 'utf8');
    const expected = readFileSync(resolve(runtime.root, from), 'utf8');
    if (actual !== expected) fail('INSTALLED_RUNTIME_TAMPERED');
  }
  const discovered = discoverTeamRuntime(layout.teamWorkspace);
  if (discovered.manifest.version !== runtime.manifest.version) fail('INSTALLED_RUNTIME_TAMPERED');
  return Object.freeze({ status: 'READY', name: discovered.manifest.name,
    version: discovered.manifest.version, protocolVersion: discovered.manifest.protocolVersion,
    rootAgentType: discovered.manifest.entrypoint.rootAgentType,
    teamId: runtime.teamPack.teamId, teamName: runtime.teamPack.teamName });
}

/** Statically verify a team runtime that was installed before the Player task opened. */
export function verifyTeamRuntime(layout) {
  const registration = join(layout.teamWorkspace, '.mtm', 'team-runtime.json');
  if (!existsSync(registration)) fail('TEAM_RUNTIME_NOT_INSTALLED');
  const packageRoot = join(layout.teamSdlcPackWorkspace, 'node_modules', 'team-sdlc-pack');
  return Object.freeze({ action: 'VERIFIED',
    runtime: verifyInstalled(layout, existsSync(packageRoot) ? packageRoot : undefined) });
}
