#!/usr/bin/env node
import { openLocalAppStore } from '../src/local-app-store.mjs';
import { localAppDirectory } from '../src/local-app.mjs';
import { listMatchRounds } from '../src/match-rounds.mjs';
import { generateKeyPairSync, randomUUID, sign } from 'node:crypto';
import { spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createInterface, emitKeypressEvents } from 'node:readline';
import { run, readJson, register } from '../src/player.mjs';
import { requireThat, shape } from '../src/protocol.mjs';
import { DEFAULT_GAME_MASTER_API_URL, defaultPlayerDataDirectory, initializeMatch, JsonBootstrapConfigStore,
  LegacyCredentialFileStore, validateTeamWorkspaceLayout } from '../src/bootstrap.mjs';
import { discoverTeamRuntime, prepareNativePreparation } from '../src/team-runtime.mjs';
import { startProgressDashboard } from '../src/progress.mjs';
import { verifyTeamRuntime } from '../src/team-install.mjs';
import { initializeMatchInBrowser } from '../src/init-web.mjs';
import { browserOpenRequest, publicInitReceipt, writeMachineFrame } from '../src/machine-output.mjs';
import { createRestartSessionControl, createSessionControl, runSessionControlLoop } from '../src/session-control.mjs';
import { bindRestartSession, loadRestartCheckpoint } from '../src/restart-checkpoint.mjs';
import { createMatchStatusMonitor } from '../src/match-status.mjs';
import { startDashboardService, verifyDashboardServiceCapability } from '../src/dashboard-service.mjs';
import { openSessionJournal, sessionJournalDirectory } from '../src/session-journal.mjs';
import { createHostOutbox } from '../src/host-outbox.mjs';
import { registerCurrentCodexTask } from '../src/codex-session-registry.mjs';
import { launchLocalApp } from '../src/local-app-launcher.mjs';
import { runLocalHostBridge } from '../src/local-host-bridge.mjs';

const args = process.argv.slice(2);
const help = `MTM Player local runtime (Node >=20, Git for demo)
  node scripts/player.mjs init [--reconfigure-game-master] [--clear-challenge] [--stop-existing] [--terminal] [--reliable-control]
  node scripts/player.mjs restart [--reconfigure-game-master] [--reliable-control]
  node scripts/player.mjs watch
  node scripts/player.mjs status [matchId]
  node scripts/player.mjs inspect-runtime --workspace <absolute team workspace>
  node scripts/player.mjs dashboard [--fresh] --team-sdlc-pack-workspace <absolute pack workspace> --match-id <id> --player-session-id <id> --team-id <id> --team-name <name> [--host-telemetry-file <absolute ndjson>] [--price-card <absolute json>]
  node scripts/player.mjs demo
  node scripts/player.mjs register --manifest <absolute candidate.json> --workspace <absolute directory> --out <absolute agent.json>
  node scripts/player.mjs run --config <absolute config.json> --event <absolute event.json> [--retry]
  node scripts/player.mjs status --state <absolute state directory>
Local receipts remain PENDING_GAME_MASTER; no official deadline decision is made.`;

const promptLine = (question, fallback) => new Promise(resolveAnswer => {
  const terminal = createInterface({ input: process.stdin, output: process.stdout });
  const suffix = fallback ? ` [${fallback}]` : '';
  terminal.question(`${question}${suffix}: `, answer => {
    terminal.close();
    resolveAnswer(answer.trim() || fallback || '');
  });
});

const promptSecret = question => new Promise((resolveAnswer, reject) => {
  if (!process.stdin.isTTY || typeof process.stdin.setRawMode !== 'function') {
    reject(new Error('INTERACTIVE_SECRET_INPUT_REQUIRED'));
    return;
  }
  let value = '';
  const finish = (error) => {
    process.stdin.off('keypress', onKey);
    process.stdin.setRawMode(false);
    process.stdin.pause();
    process.stdout.write('\n');
    if (error) reject(error); else resolveAnswer(value);
  };
  const onKey = (text, key) => {
    if (key?.ctrl && key.name === 'c') return finish(new Error('INPUT_CANCELLED'));
    if (key?.name === 'return' || key?.name === 'enter') return finish();
    if (key?.name === 'backspace') { value = value.slice(0, -1); return; }
    if (text && !key?.ctrl && !key?.meta) value += text;
  };
  process.stdout.write(`${question}: `);
  emitKeypressEvents(process.stdin);
  process.stdin.setRawMode(true);
  process.stdin.resume();
  process.stdin.on('keypress', onKey);
});

function installedRuntimeContext(dataDir) {
  const layout = validateTeamWorkspaceLayout(process.cwd(), { configDir: dataDir });
  const teamRuntime = verifyTeamRuntime(layout);
  return { layout, teamRuntime };
}

async function initializeInteractive(reconfigure, clearChallenge) {
  const dataDir = defaultPlayerDataDirectory();
  const configStore = new JsonBootstrapConfigStore(dataDir);
  const credentialStore = new LegacyCredentialFileStore(join(dataDir, 'credentials'));
  const configured = configStore.load();
  const installed = installedRuntimeContext(dataDir);
  console.log(`Team pack: ${installed.teamRuntime.runtime.teamId} — ${installed.teamRuntime.runtime.teamName} (${installed.teamRuntime.runtime.version})`);
  const teamId = await promptLine('team_id');
  const matchId = await promptLine('match_id');
  const teamWorkspace = await promptLine('team_workspace_root (contains challenge and team-sdlc-pack)');
  requireThat(resolve(teamWorkspace).toLowerCase() === installed.layout.teamWorkspace.toLowerCase(),
    'TEAM_WORKSPACE_MISMATCH');
  let gameMasterApiUrl;
  if (!configured || reconfigure) {
    gameMasterApiUrl = await promptLine('game-master-api-url', configured?.gameMasterApiUrl ?? DEFAULT_GAME_MASTER_API_URL);
  }
  const gameMasterSecretKey = await promptSecret('game-master-secret-key');
  return initializeMatch({ teamId, matchId, teamWorkspace, gameMasterApiUrl,
    gameMasterSecretKey, reconfigure, clearChallenge }, { configStore, credentialStore });
}

function openBrowser(url) {
  const command = process.platform === 'win32' ? 'explorer.exe' : process.platform === 'darwin' ? 'open' : 'xdg-open';
  const child = spawn(command, [url], { detached: true, stdio: 'ignore', windowsHide: true });
  child.once('error', () => {});
  child.unref();
}

async function initializeInBrowser(reconfigure, clearChallenge, mode = 'INIT') {
  await verifyDashboardServiceCapability();
  const dataDir = defaultPlayerDataDirectory();
  const configStore = new JsonBootstrapConfigStore(dataDir);
  const credentialStore = new LegacyCredentialFileStore(join(dataDir, 'credentials'));
  const installed = installedRuntimeContext(dataDir);
  let dashboard = null;
  let activeSession = null;
  let restartCheckpoint = null;
  let hostRegistry = null;
  const session = await initializeMatchInBrowser({ reconfigure, mode,
    expectedTeamWorkspace: installed.layout.teamWorkspace,
    teamPack: { version: installed.teamRuntime.runtime.version,
      teamId: installed.teamRuntime.runtime.teamId,
      teamName: installed.teamRuntime.runtime.teamName } }, {
    configStore,
    credentialStore,
    initializeMatch: (input, dependencies) => initializeMatch({ ...input, clearChallenge,
      preserveChallenge: mode === 'RESTART' }, dependencies),
    defaultApiUrl: DEFAULT_GAME_MASTER_API_URL,
    async onInitialized(sessionValue) {
      if (mode === 'RESTART') {
        const loaded = loadRestartCheckpoint(sessionValue.teamSdlcPackWorkspace,
          sessionValue.matchId);
        activeSession = bindRestartSession(sessionValue, loaded.checkpoint);
        restartCheckpoint = loaded.checkpoint;
      } else activeSession = sessionValue;
      const host = registerCurrentCodexTask(activeSession, { dataDirectory: dataDir });
      activeSession = host.session;
      hostRegistry = host.registry;
      writeMachineFrame(process.stdout, host.receipt);
      dashboard = await startMatchDashboard(activeSession, { fresh: mode !== 'RESTART' });
      return mode === 'RESTART' ? { dashboardUrl: dashboard.url,
        playerSessionId: activeSession.playerSessionId } : { dashboardUrl: dashboard.url };
    },
    onReady(url) {
      writeMachineFrame(process.stdout, browserOpenRequest(url));
      openBrowser(url);
    }
  });
  return { session: activeSession ?? session, dashboard, restartCheckpoint, hostRegistry };
}

function startMatchDashboard(session, { fresh = true } = {}) {
  const allowedOrigins = new Set(session.allowedApiOrigins);
  try { allowedOrigins.add(new URL(session.repository.url).origin); } catch { /* validated elsewhere */ }
  const dashboardOptions = { teamSdlcPackWorkspace: session.teamSdlcPackWorkspace,
    matchId: session.matchId, playerSessionId: session.playerSessionId,
    teamId: session.teamId, teamName: session.teamName, fresh,
    hostTelemetryFile: process.env.MTM_HOST_TELEMETRY_FILE ?? null,
    auditPolicy: { schemaVersion: 1, mode: 'TASK_PERMISSIONS_OBSERVE_ONLY',
      teamWorkspace: session.teamWorkspace, allowedOrigins: [...allowedOrigins] },
    liveTeamInfo: { schemaVersion: 1, playerSessionId: session.playerSessionId,
      requestId: session.teamInfoRequestId,
      team: { uuid: session.teamUuid, id: session.teamId, name: session.teamName,
        username: session.username, password: session.password },
      apiKey: { secret: session.apiKeySecret, scopes: [...session.apiKeyScopes] },
      gameMasterUrl: session.gameMasterUrl, matchPageUrl: session.matchPageUrl,
      matchId: session.matchId, repository: { ...session.repository },
      preparationComplete: { method: session.preparationComplete.method,
        url: session.preparationComplete.url,
        authorization: `Bearer ${session.apiKeySecret}` } } };
  return startDashboardService({ session, dashboardOptions, hostDataDirectory: defaultPlayerDataDirectory() });
}

function sessionOutbox(session) {
  const identity = { serverOrigin: new URL(session.gameMasterApiUrl).origin,
    teamId: session.teamId, matchId: session.matchId, attemptId: session.playerSessionId };
  return createHostOutbox(openSessionJournal({ identity,
    directory: sessionJournalDirectory(defaultPlayerDataDirectory(), identity) }));
}

async function main() {
  if (args[0] === 'reset') throw Object.assign(new Error('RESET_REMOVED_USE_INIT'), { code: 'RESET_REMOVED_USE_INIT' });
  if (args[0] === 'status' && !args.includes('--state')) {
    requireThat(args.length <= 2, 'INVALID_ARGUMENTS');
    const workspace = process.cwd();
    const store = openLocalAppStore({ directory: localAppDirectory(defaultPlayerDataDirectory(), workspace), workspace });
    const state = store.load();
    console.log(JSON.stringify(listMatchRounds(state, args[1]), null, 2));
    return;
  }
  if (args[0] === 'watch') {
    requireThat(args.length === 1, 'INVALID_ARGUMENTS');
    const result = await launchLocalApp({ workspace: process.cwd(), dataDirectory: defaultPlayerDataDirectory(),
      codexThreadId: process.env.CODEX_THREAD_ID ?? null, mode: 'ATTACH' });
    writeMachineFrame(process.stdout, result);
    await runLocalHostBridge({ controlUrl: result.controlUrl,
      codexThreadId: process.env.CODEX_THREAD_ID ?? null });
    return;
  }
  if (['init', 'restart'].includes(args[0]) && !args.includes('--legacy')) {
    requireThat(args.slice(1).every(value => ['--clear-challenge', '--reconfigure-game-master', '--stop-existing'].includes(value)), 'INVALID_ARGUMENTS');
    const result = await launchLocalApp({ workspace: process.cwd(), dataDirectory: defaultPlayerDataDirectory(),
      codexThreadId: process.env.CODEX_THREAD_ID ?? null, mode: args[0] === 'restart' ? 'RESTART' : 'INIT',
      clearChallenge: args.includes('--clear-challenge'), stopExisting: args.includes('--stop-existing') });
    writeMachineFrame(process.stdout, result);
    writeMachineFrame(process.stdout, browserOpenRequest(result.url));
    openBrowser(result.url);
    return;
  }
  if (args.includes('--legacy')) args.splice(args.indexOf('--legacy'), 1);
  if (args[0] === 'control') {
    requireThat(args.length === 5 && args[1] === '--url' && args[3] === '--message-file' &&
      /^http:\/\/127\.0\.0\.1:\d+\/[a-f0-9]{64}$/.test(args[2]), 'INVALID_ARGUMENTS');
    const message = readJson(args[4]);
    const body = JSON.stringify(message);
    requireThat(Buffer.byteLength(body) <= 65536, 'SESSION_CONTROL_MESSAGE_TOO_LARGE');
    const response = await fetch(args[2], { method: 'POST', redirect: 'manual',
      headers: { 'Content-Type': 'application/json' }, body,
      signal: AbortSignal.timeout(60000) });
    requireThat(response.ok, 'PLAYER_CONTROL_REQUEST_FAILED');
    writeMachineFrame(process.stdout, await response.json());
    return;
  }
  if (args.length === 0 || (args.length === 1 && ['--help', 'help'].includes(args[0]))) return console.log(help);
  if (args[0] === 'init') {
    const options = new Set(args.slice(1));
    requireThat(options.size === args.length - 1 &&
      [...options].every(option => ['--reconfigure-game-master', '--clear-challenge', '--terminal', '--reliable-control'].includes(option)), 'INVALID_ARGUMENTS');
    const reconfigure = options.has('--reconfigure-game-master');
    const clearChallenge = options.has('--clear-challenge');
    let session; let dashboard; let hostRegistry;
    if (options.has('--terminal')) {
      session = await initializeInteractive(reconfigure, clearChallenge);
      const host = registerCurrentCodexTask(session, { dataDirectory: defaultPlayerDataDirectory() });
      session = host.session; hostRegistry = host.registry;
      writeMachineFrame(process.stdout, host.receipt);
      dashboard = await startMatchDashboard(session);
      openBrowser(dashboard.url);
    } else ({ session, dashboard, hostRegistry } = await initializeInBrowser(reconfigure, clearChallenge));
    writeMachineFrame(process.stdout, publicInitReceipt(session));
    const { manifest } = discoverTeamRuntime(session.teamWorkspace);
    const preparation = prepareNativePreparation(session, manifest,
      { availableAgentTypes: ['team_orchestrator'] }, `prepare-${randomUUID()}`);
    const control = createSessionControl({ session, manifest, preparation });
    const matchStatusMonitor = createMatchStatusMonitor(session);
    await runSessionControlLoop(control, { matchStatusMonitor, reconnectable: true, hostRegistry,
      hostOutbox: options.has('--reliable-control') ? sessionOutbox(session) : null,
      onEnd: () => dashboard.close() });
    return;
  }
  if (args[0] === 'restart') {
    const options = new Set(args.slice(1));
    requireThat(options.size === args.length - 1 &&
      [...options].every(option => ['--reconfigure-game-master', '--reliable-control'].includes(option)), 'INVALID_ARGUMENTS');
    const { session, dashboard, restartCheckpoint, hostRegistry } = await initializeInBrowser(
      options.has('--reconfigure-game-master'), false, 'RESTART');
    const { manifest } = discoverTeamRuntime(session.teamWorkspace);
    writeMachineFrame(process.stdout, { schemaVersion: 1, status: 'RESTARTED',
      matchId: session.matchId, teamId: session.teamId,
      playerSessionId: session.playerSessionId, codexThreadId: session.codexThreadId,
      restartAttempt: restartCheckpoint.restartAttempt + 1 });
    const control = createRestartSessionControl({ session, manifest,
      checkpoint: restartCheckpoint });
    const matchStatusMonitor = createMatchStatusMonitor(session);
    await runSessionControlLoop(control, { matchStatusMonitor, reconnectable: true, hostRegistry,
      hostOutbox: options.has('--reliable-control') ? sessionOutbox(session) : null,
      onEnd: () => dashboard.close() });
    return;
  }
  if (args[0] === 'inspect-runtime') {
    requireThat(args.length === 3 && args[1] === '--workspace', 'INVALID_ARGUMENTS');
    const result = discoverTeamRuntime(args[2]);
    console.log(JSON.stringify({ status: 'READY', manifestPath: result.manifestPath,
      runtime: { name: result.manifest.name, version: result.manifest.version,
        protocolVersion: result.manifest.protocolVersion,
        rootAgentType: result.manifest.entrypoint.rootAgentType,
        provider: result.manifest.provider,
        executionPermissions: 'PARENT_TASK_INHERITED',
        supervisionMode: 'OBSERVE_ONLY' } }, null, 2));
    return;
  }
  if (args[0] === 'dashboard') {
    const values = {};
    const required = new Set(['--team-sdlc-pack-workspace', '--match-id', '--player-session-id', '--team-id', '--team-name']);
    const allowed = new Set([...required, '--host-telemetry-file', '--price-card']);
    let fresh = false;
    for (let index = 1; index < args.length;) {
      if (args[index] === '--fresh') {
        requireThat(!fresh, 'INVALID_ARGUMENTS');
        fresh = true; index += 1; continue;
      }
      requireThat(allowed.has(args[index]) && args[index + 1] && !Object.hasOwn(values, args[index]),
        'INVALID_ARGUMENTS');
      values[args[index]] = args[index + 1];
      index += 2;
    }
    requireThat([...required].every(key => Object.hasOwn(values, key)), 'INVALID_ARGUMENTS');
    const dashboard = await startProgressDashboard({ teamSdlcPackWorkspace: values['--team-sdlc-pack-workspace'],
      matchId: values['--match-id'],
      playerSessionId: values['--player-session-id'], teamId: values['--team-id'],
      teamName: values['--team-name'], fresh,
      hostTelemetryFile: values['--host-telemetry-file'] ?? null,
      priceCard: values['--price-card'] ? readJson(values['--price-card']) : undefined });
    console.log(JSON.stringify({ status: 'DASHBOARD_READY', url: dashboard.url,
      progressLogFile: dashboard.logPath, staticDashboardFile: dashboard.staticDashboardFile }));
    openBrowser(dashboard.url);
    const stop = async () => { await dashboard.close(); process.exit(0); };
    process.once('SIGINT', stop); process.once('SIGTERM', stop);
    return;
  }
  if (args[0] === 'demo') {
    requireThat(args.length === 1, 'INVALID_ARGUMENTS');
    const root = mkdtempSync(join(tmpdir(), 'mtm-player-demo-'));
    const workspace = join(root, 'workspace');
    mkdirSync(workspace);
    // This local fixture acts as a mock issuer. Its ephemeral signing key is never stored.
    const { publicKey, privateKey } = generateKeyPairSync('ed25519');
    const event = { protocolVersion: 1, type: 'TICKET', eventId: 'event-local-1', runId: 'run-local-1',
      ticketId: 'ticket-local-1', teamId: 'team-local', workspaceRef: 'sample',
      issuedAt: '2026-09-11T00:00:00.000Z', deadline: '2026-09-11T00:10:00.000Z',
      ticket: { title: 'Public local sample', instructions: 'Create evidence.txt and commit the result.' } };
    const bytes = Buffer.from(JSON.stringify(event));
    const envelope = { protocolVersion: 1, keyId: 'local-demo', payload: bytes.toString('base64'),
      signature: sign(null, bytes, privateKey).toString('base64') };
    const registration = { protocolVersion: 1, name: 'mtm-team-agent',
      command: [process.execPath, fileURLToPath(new URL('../examples/team-agent.mjs', import.meta.url))],
      capabilities: ['tickets-v1', 'git', 'evidence'] };
    const agentPath = join(root, 'agent.json');
    const config = { protocolVersion: 1, teamId: 'team-local', agentPath,
      trust: { 'local-demo': publicKey.export({ type: 'spki', format: 'pem' }) },
      workspaces: { sample: workspace }, stateDir: join(root, 'state'), timeoutMs: 30_000 };
    for (const [name, value] of Object.entries({ 'event.json': envelope, 'agent.json': registration, 'config.json': config })) {
      writeFileSync(join(root, name), JSON.stringify(value, null, 2) + '\n');
    }
    const result = await run({ ...config, envelope, registration });
    console.log(JSON.stringify({ root, ...result }, null, 2));
    return;
  }
  const options = {};
  for (let i = 1; i < args.length; i++) {
    const key = args[i];
    requireThat(!Object.hasOwn(options, key), 'DUPLICATE_ARGUMENT');
    if (key === '--retry') options[key] = true;
    else {
      requireThat(['--config', '--event', '--state', '--manifest', '--workspace', '--out'].includes(key) && args[i + 1], 'INVALID_ARGUMENTS');
      options[key] = args[++i];
    }
  }
  if (args[0] === 'register') {
    shape(options, ['--manifest', '--workspace', '--out']);
    console.log(JSON.stringify(await register({ registration: readJson(options['--manifest']),
      workspace: options['--workspace'], output: options['--out'] }), null, 2));
  } else if (args[0] === 'status') {
    shape(options, ['--state']);
    const state = readJson(resolve(options['--state'], 'state.json'));
    console.log(JSON.stringify({ events: state.events.map(({ eventId, status, attempts, timeline }) =>
      ({ eventId, status, attempts, lastLifecycle: timeline.at(-1)?.type ?? null })),
      receipts: state.outbox }, null, 2));
  } else if (args[0] === 'run') {
    shape(options, Object.hasOwn(options, '--retry') ? ['--config', '--event', '--retry'] : ['--config', '--event']);
    const config = readJson(options['--config']);
    shape(config, ['protocolVersion', 'teamId', 'agentPath', 'trust', 'workspaces', 'stateDir', 'timeoutMs']);
    requireThat(config.protocolVersion === 1);
    console.log(JSON.stringify(await run({ ...config, envelope: readJson(options['--event']),
      registration: readJson(config.agentPath), retry: options['--retry'] === true }), null, 2));
  } else throw new Error('INVALID_COMMAND');
}

main().catch(error => {
  // Do not reflect ticket content, child output or filesystem errors into telemetry.
  const code = typeof error?.code === 'string' && /^[A-Z0-9_]+$/.test(error.code) ? error.code :
    /^[A-Z0-9_]+$/.test(error?.message ?? '') ? error.message : 'PLAYER_FAILURE';
  if (code === 'TEAM_WORKSPACE_PREPARED' || code === 'TEAM_RUNTIME_NOT_INSTALLED') {
    console.error('Hãy copy team-sdlc-pack-<version>.tgz và install.ps1 vào thư mục team-sdlc-pack, chạy installer, rồi mở task thi tại team workspace.');
    process.exitCode = 1;
    return;
  }
  console.error(`MTM Player failed: ${code}`);
  process.exitCode = 1;
});
