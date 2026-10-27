import { spawn } from 'node:child_process';
import { createWriteStream, existsSync, lstatSync, mkdirSync, openSync, closeSync, readSync,
  statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { createEventSanitizer, projectCliActivity } from './cli-activity.mjs';

const fail = code => { throw Object.assign(new Error(code), { code }); };
const validSessionId = value => typeof value === 'string' && /^[a-f0-9-]{36}$/.test(value);

export function protocolPrompt(frame, operationKind) {
  const message = frame?.spawn?.message ?? frame?.followup?.message;
  if (typeof message !== 'string' || message.length === 0 || message.length > 1024 * 1024) {
    fail('INVALID_CLI_ORCHESTRATOR_FRAME');
  }
  const header = message.split(/\r?\n/, 1)[0].trim();
  const recovery = /^RESUME_(?:PREPARATION|GAME)_V\d+$/.test(header);
  const preparation = /^PREPARE_GAME_V\d+$/.test(header) ||
    (!/^(?:START_GAME|RESUME_|MATCH_)/.test(header) && operationKind === 'PREPARE');
  const stopping = /^MATCH_(?:PAUSED|ENDED)_V\d+$/.test(header) || operationKind === 'STOP';
  const instruction = stopping ?
    'Handle this stop/pause/end instruction immediately. Cease new work and stop descendants; do not start preparation or backlog work.' :
    recovery ? 'Read and follow .agents/skills/team-recovery/SKILL.md. Recover only the phase specified in this frame. Read progressLogFile first; retain completed valid gates, checkout, playerSessionId, and submission receipts. Rerun only incomplete or invalid preparation gates. Reauthenticate the restarted portal and reconcile server state before submitting. Never clear or reclone an existing checkout during recovery.' :
    preparation ? 'Read and follow .agents/skills/game-preparation/SKILL.md. Complete preparation and report readiness; do not run the active-game workflow before START_GAME.' :
    'Read and follow .agents/skills/team-orchestrator/SKILL.md for active-game work.';
  let guideline = '';
  if (!stopping && /^(?:PREPARE_GAME|RESUME_PREPARATION)_V\d+$/.test(header)) {
    let descriptor;
    try { descriptor = JSON.parse(message.split(/\r?\n/)[1]); } catch { fail('INVALID_CLI_ORCHESTRATOR_FRAME'); }
    const logFile = descriptor.progressLogFile ?? frame.dashboard?.progressLogFile;
    if (typeof descriptor.playerSessionId !== 'string' || typeof logFile !== 'string') {
      fail('INVALID_PREPARATION_PROGRESS_CONTEXT');
    }
    const event = { schemaVersion: 1, playerSessionId: descriptor.playerSessionId,
      type: 'PREPARATION_STATUS', stage: 'GIT_AUTH', status: 'COMPLETED',
      summary: 'Git authentication verified.' };
    guideline = [
      'Player progress reporting contract (implementation-independent):',
      'MUST append one compact JSON object per line to this exact file: ' + JSON.stringify(logFile),
      'Use UTF-8 and a newline. Never overwrite the file. Use any implementation you choose; no specific helper or MCP is required.',
      'Player has already written the first ORCHESTRATOR_STARTED event with the exact playerSessionId. Do not write, replace, or repeat that event.',
      'On recovery, preserve every existing event and continue appending.',
      'Required preparation event example: ' + JSON.stringify(event),
      'Allowed stages: GIT_AUTH, REPOSITORY, ENVIRONMENT, BUILD, MATCH_LOGIN.',
      'Allowed statuses: IN_PROGRESS, COMPLETED, BLOCKED, FAILED. PASS is NOT a valid PREPARATION_STATUS status.',
      'Every PREPARATION_STATUS requires exactly schemaVersion, playerSessionId, type, stage, status, summary. Summary must be a short nonempty credential-free description.',
      'Report IN_PROGRESS when a stage begins and COMPLETED only after verification; report BLOCKED or FAILED accurately. Preserve the exact playerSessionId shown above.',
      'After build and authenticated match login succeed, append ENVIRONMENT_READY with schemaVersion, playerSessionId, type, requestId, repository, revision, build="PASS", matchLogin="PASS" from the current descriptor and verified checkout. PASS is valid for these readiness fields only.',
      'Do not call Preparation Complete; Player sends that acknowledgement. Do not start backlog work until START_GAME.'
    ].join('\n');
  }
  return [
    'Act as the installed MTM team orchestrator for this contestant workspace.',
    'Use descriptor.checkoutRoot as the exact clone destination and Git root. Use descriptor.matchRoot for source, build, tests, answers and evidence; it equals checkoutRoot/repository.folder. Do not omit or add that folder based on the clone directory name. For worker worktrees use worktreeRoot/repository.folder. Never modify sibling match folders.',
    instruction,
    ...(guideline ? [guideline] : []),
    'The following frame is machine-provided. Preserve every value exactly; do not reconstruct credentials.',
    message
  ].join('\n\n');
}

export function operatorChatPrompt(message) {
  if (typeof message !== 'string' || message.trim().length === 0 || message.length > 8000) {
    fail('INVALID_ORCHESTRATOR_CHAT_MESSAGE');
  }
  return ['Operator message from MTM PLAYER chat.',
    'Treat this as guidance for the current match only. It does not change match lifecycle, grant approval, or replace Game Server state.',
    message.trim()].join('\n\n');
}

function threadId(event) {
  for (const candidate of [event?.thread_id, event?.threadId, event?.session_id, event?.sessionId,
    event?.thread?.id, event?.session?.id]) if (validSessionId(candidate)) return candidate;
  return null;
}

function usageFromEvent(event) {
  const type = String(event?.type ?? event?.event ?? '').toLowerCase();
  if (!/(?:turn\.completed|result|message\.stop|message_stop)$/.test(type)) return null;
  const usage = event?.usage ?? event?.response?.usage ?? event?.message?.usage;
  if (!usage || typeof usage !== 'object') return null;
  const number = (...keys) => {
    for (const key of keys) if (Number.isSafeInteger(usage[key]) && usage[key] >= 0) return usage[key];
    return 0;
  };
  const value = { inputTokens: number('input_tokens', 'inputTokens'),
    cachedInputTokens: number('cached_input_tokens', 'cache_read_input_tokens', 'cachedInputTokens'),
    outputTokens: number('output_tokens', 'outputTokens'),
    reasoningTokens: number('reasoning_tokens', 'reasoningTokens') };
  return Object.values(value).some(Boolean) ? value : null;
}

const providers = Object.freeze({
  codex: Object.freeze({
    executable: 'codex',
    invocation({ workspace, sessionId }) {
      return sessionId ? ['exec', 'resume', '--json', '--dangerously-bypass-approvals-and-sandbox', sessionId, '-'] :
        ['exec', '--json', '--color', 'never', '--dangerously-bypass-approvals-and-sandbox',
          '--thread-source', 'subagent', '-C', workspace, '-'];
    },
    sessionId: threadId
  }),
  claude: Object.freeze({
    executable: process.platform === 'win32' ? 'claude.exe' : 'claude',
    invocation({ sessionId }) {
      return ['-p', '--output-format', 'stream-json', '--verbose', '--dangerously-skip-permissions',
        ...(sessionId ? ['--resume', sessionId] : ['--session-id', randomUUID()])];
    },
    sessionId(event, requestedSessionId) {
      return threadId(event) ?? (validSessionId(requestedSessionId) ? requestedSessionId : null);
    }
  })
});

export function cliProvider(name) {
  if (!Object.hasOwn(providers, name)) fail('UNSUPPORTED_CLI_PROVIDER');
  return providers[name];
}

const redact = value => String(value ?? '')
  .replace(/\b(Bearer\s+)[^\s"']+/gi, '$1[REDACTED]')
  .replace(/\b(?:ghp|github_pat|gm_player|gm_team)_[A-Za-z0-9_\-^\\]{8,}\b/g, '[REDACTED]')
  .replace(/((?:password|secret|apiKeySecret|authorization)["'\s:=]+)[^\s,}"']+/gi, '$1[REDACTED]')
  .slice(0, 4000);

function transcriptEvent(event) {
  const type = String(event?.type ?? event?.event ?? 'EVENT').toUpperCase().slice(0, 80);
  const itemType = String(event?.item?.type ?? '').toUpperCase();
  const role = /ASSISTANT|AGENT_MESSAGE/.test(type + itemType) ? 'ASSISTANT' : /USER/.test(type) ? 'USER' :
    /TOOL|COMMAND/.test(type + itemType) ? 'TOOL' : 'SYSTEM';
  const content = event?.item?.text ?? event?.item?.content ?? event?.message?.content ?? event?.result ??
    event?.summary ?? event?.name ?? type;
  const safeText = role === 'USER' ? 'Protocol input omitted.' : role === 'TOOL' ?
    'Tool activity recorded; arguments and output omitted.' : role === 'SYSTEM' ? type :
      redact(typeof content === 'string' ? content : JSON.stringify(content));
  return { timestamp: typeof event?.timestamp === 'string' ? event.timestamp : new Date().toISOString(),
    type: itemType ? `${type}:${itemType}` : type, role, text: safeText };
}

export function readCliTranscriptView(runtime, { maxBytes = 2 * 1024 * 1024, maxItems = 200 } = {}) {
  const path = runtime?.transcriptPath;
  if (!validSessionId(runtime?.sessionId) || typeof path !== 'string') {
    return { schemaVersion: 1, status: 'WAITING_FOR_TRANSCRIPT', task: null, items: [] };
  }
  if (!existsSync(path)) return { schemaVersion: 1, status: 'WAITING_FOR_TRANSCRIPT',
    task: { codexThreadId: runtime.sessionId, role: 'ORCHESTRATOR', agentPath: '/team_orchestrator' }, items: [] };
  if (lstatSync(path).isSymbolicLink() || !statSync(path).isFile()) fail('INVALID_CLI_TRANSCRIPT');
  const size = statSync(path).size;
  const start = Math.max(0, size - maxBytes);
  const bytes = Buffer.alloc(size - start);
  const fd = openSync(path, 'r');
  try { readSync(fd, bytes, 0, bytes.length, start); } finally { closeSync(fd); }
  const lines = bytes.toString('utf8').split(/\r?\n/);
  if (start) lines.shift();
  const items = [];
  for (const line of lines) {
    if (!line || Buffer.byteLength(line) > 1024 * 1024) continue;
    let event; try { event = JSON.parse(line); } catch { continue; }
    const projected = typeof event.text === 'string' && ['ASSISTANT','USER','TOOL','SYSTEM'].includes(event.role) ?
      { timestamp: event.timestamp, type: event.type, role: event.role, text: redact(event.text),
        detail: event.detail && typeof event.detail === 'object' ? {
          name: redact(event.detail.name), phase: redact(event.detail.phase),
          input: redact(event.detail.input), output: redact(event.detail.output),
          exitCode: Number.isInteger(event.detail.exitCode) ? event.detail.exitCode : null } : null } : transcriptEvent(event);
    items.push({ ...projected, codexThreadId: runtime.sessionId });
  }
  return { schemaVersion: 1, status: runtime.status === 'RUNNING' ? 'STREAMING' : 'READY',
    task: { codexThreadId: runtime.sessionId, role: 'ORCHESTRATOR', agentPath: '/team_orchestrator' },
    truncated: start > 0, items: items.slice(-maxItems) };
}

function processAlive(pid) {
  if (!Number.isSafeInteger(pid) || pid < 1) return false;
  try { process.kill(pid, 0); return true; } catch { return false; }
}

function stopTree(pid, spawnImpl = spawn) {
  if (!processAlive(pid)) return Promise.resolve({ treeStopped: true });
  if (process.platform !== 'win32') {
    try { process.kill(-pid, 'SIGTERM'); } catch { try { process.kill(pid, 'SIGTERM'); } catch {} }
    return Promise.resolve({ treeStopped: true });
  }
  return new Promise((resolveStop, reject) => {
    const child = spawnImpl('taskkill.exe', ['/PID', String(pid), '/T', '/F'],
      { windowsHide: true, shell: false, stdio: 'ignore' });
    child.once('error', error => reject(Object.assign(new Error('CLI_PROCESS_STOP_FAILED'),
      { code: 'CLI_PROCESS_STOP_FAILED', cause: error })));
    child.once('exit', code => code === 0 || !processAlive(pid) ? resolveStop({ treeStopped: true }) :
      reject(Object.assign(new Error('CLI_PROCESS_STOP_FAILED'), { code: 'CLI_PROCESS_STOP_FAILED', exitCode: code })));
  });
}

/** Owns exactly one resumable coding-agent CLI session for one match round. */
export function createCliSupervisor({ workspace, directory, runtime, save = () => {},
  onSession = () => {}, onExit = () => {}, provider = 'codex', executable = null }, dependencies = {}) {
  if (typeof workspace !== 'string' || typeof directory !== 'string' || !runtime || typeof runtime !== 'object') {
    fail('INVALID_CLI_SUPERVISOR_CONFIG');
  }
  const spawnImpl = dependencies.spawn ?? spawn;
  const stopImpl = dependencies.stopTree ?? (pid => stopTree(pid, spawnImpl));
  runtime.provider ??= provider;
  if (runtime.provider !== provider) fail('CLI_PROVIDER_IMMUTABLE_FOR_ROUND');
  const driver = dependencies.driver ?? cliProvider(provider);
  executable ??= driver.executable;
  const root = resolve(directory);
  mkdirSync(root, { recursive: true });
  runtime.status ??= 'IDLE';
  if (runtime.status === 'RUNNING' && !processAlive(runtime.pid)) {
    runtime.status = 'INTERRUPTED'; runtime.pid = null; runtime.endedAt = new Date().toISOString(); save();
  }
  let active = null, stopping = false;

  const snapshot = () => ({ provider: runtime.provider, status: runtime.status, sessionId: runtime.sessionId ?? null,
    containment: runtime.containment ?? 'UNKNOWN',
    pid: runtime.pid ?? null, operationId: runtime.operationId ?? null,
    startedAt: runtime.startedAt ?? null, endedAt: runtime.endedAt ?? null,
    exitCode: runtime.exitCode ?? null, transcriptPath: runtime.transcriptPath ?? null,
    errorPath: runtime.errorPath ?? null, lastError: runtime.lastError ?? null,
    lastErrorAt: runtime.lastErrorAt ?? null, operationKind: runtime.operationKind ?? null,
    tokenUsage: runtime.tokenUsage ?? null, modelUsage: runtime.modelUsage ?? [],
    activity: runtime.activity ?? null, activityRecords: runtime.activityRecords ?? [] });
  const reconcile = () => {
    if (runtime.status !== 'RUNNING' || active || processAlive(runtime.pid)) return snapshot();
    const outcome = { operationId: runtime.operationId, kind: runtime.operationKind, exitCode: null };
    runtime.status = 'INTERRUPTED'; runtime.pid = null; runtime.endedAt = new Date().toISOString();
    runtime.lastError = 'CLI_PROCESS_LOST'; runtime.lastErrorAt = runtime.endedAt; save(); onExit(outcome);
    return snapshot();
  };

  const start = (operation, frame, promptOverride = null) => {
    if (active || (runtime.status === 'RUNNING' && processAlive(runtime.pid))) fail('CLI_ORCHESTRATOR_BUSY');
    stopping = false;
    const prompt = promptOverride ?? protocolPrompt(frame, operation.kind);
    const clean = createEventSanitizer(promptOverride === null ? frame?.spawn?.message ?? frame?.followup?.message : undefined);
    const resume = Boolean(runtime.sessionId);
    if (resume && !validSessionId(runtime.sessionId)) fail('INVALID_CLI_SESSION_ID');
    const stamp = `${Date.now()}-${operation.id.replace(/[^A-Za-z0-9_-]/g, '_')}`;
    const transcriptPath = join(root, `${stamp}.ndjson`);
    const errorPath = join(root, `${stamp}.stderr.log`);
    const args = driver.invocation({ workspace, sessionId: runtime.sessionId ?? null });
    const requestedSessionId = args.includes('--session-id') ? args[args.indexOf('--session-id') + 1] : runtime.sessionId;
    const contained = process.platform === 'win32';
    const launchExecutable = contained ? 'pwsh.exe' : executable;
    const launchArguments = contained ? ['-NoLogo', '-NoProfile', '-NonInteractive', '-File',
      fileURLToPath(new URL('../scripts/windows-cli-job.ps1', import.meta.url)),
      '-OwnerPid', String(process.pid), '-Launch', Buffer.from(JSON.stringify({
        executable, cwd: workspace, arguments: args })).toString('base64')] : args;
    const child = spawnImpl(launchExecutable, launchArguments, { cwd: workspace, windowsHide: true, shell: false,
      detached: process.platform !== 'win32', stdio: ['pipe', 'pipe', 'pipe'] });
    const transcript = createWriteStream(transcriptPath, { flags: 'a', mode: 0o600 });
    const errors = createWriteStream(errorPath, { flags: 'a', mode: 0o600 });
    runtime.status = 'RUNNING'; runtime.pid = child.pid ?? null; runtime.operationId = operation.id;
    runtime.operationKind = operation.kind;
    runtime.containment = contained ? 'STARTING' : 'UNAVAILABLE';
    runtime.startedAt = new Date().toISOString(); runtime.endedAt = null; runtime.exitCode = null;
    runtime.lastError = null; runtime.lastErrorAt = null;
    runtime.transcriptPath = transcriptPath; runtime.errorPath = errorPath; save();
    if (operation.kind === 'CHAT') transcript.write(JSON.stringify({ timestamp: new Date().toISOString(),
      type: 'OPERATOR_MESSAGE', role: 'USER', text: clean(frame.followup.message) }) + '\n');
    let buffered = '', settled = false, usageRecorded = false, timer, resolveReadyFn;
    const handleLine = line => {
      let event; try { event = JSON.parse(line); } catch {
        transcript.write(JSON.stringify({ timestamp: new Date().toISOString(), type: 'NON_JSON_OUTPUT',
          role: 'SYSTEM', text: 'Provider emitted non-JSON output; content was discarded.' }) + '\n');
        return;
      }
      const projected = projectCliActivity(event, clean);
      const transcriptItem = transcriptEvent(event);
      transcriptItem.text = clean(transcriptItem.text);
      if (projected.detail) {
        transcriptItem.detail = projected.detail;
        transcriptItem.text = `${projected.detail.name} · ${projected.detail.phase}`;
      }
      transcript.write(JSON.stringify(transcriptItem) + '\n');
      if (projected.records.length) {
        runtime.activityRecords = [...(runtime.activityRecords ?? []), ...projected.records].slice(-500);
        save();
      }
      if (event.type === 'MTM_CONTAINMENT_READY') {
        runtime.containment = 'WINDOWS_JOB_OBJECT'; save(); return;
      }
      if (event.type === 'MTM_CONTAINMENT_FAILED') {
        runtime.containment = 'FAILED'; runtime.lastError = 'WINDOWS_JOB_LAUNCH_FAILED'; save(); return;
      }
      if (contained && runtime.containment !== 'WINDOWS_JOB_OBJECT') return;
      if (event.type === 'turn.started') usageRecorded = false;
      const reportedModel = event.model ?? event.message?.model ?? event.response?.model;
      if (typeof reportedModel === 'string' && /^[A-Za-z0-9_.:/-]{1,128}$/.test(reportedModel)) runtime.observedModel = reportedModel;
      const observed = transcriptEvent(event);
      runtime.activity = { type: observed.type, observedAt: observed.timestamp,
        status: event.type === 'item.started' ? 'IN_PROGRESS' : event.type === 'item.completed' ? 'COMPLETED' : 'OBSERVED' };
      save();
      const usage = usageRecorded ? null : usageFromEvent(event);
      if (usage) {
        usageRecorded = true;
        const previous = runtime.tokenUsage ?? {};
        runtime.tokenUsage = Object.fromEntries(Object.keys(usage).map(key =>
          [key, (Number.isSafeInteger(previous[key]) ? previous[key] : 0) + usage[key]]));
        runtime.modelUsage ??= Object.values(previous).some(value => value > 0) ?
          [{ model: 'Unknown', source: 'CLI_OBSERVED', ...previous }] : [];
        const model = runtime.observedModel ?? 'Unknown';
        let row = runtime.modelUsage.find(value => value.model === model);
        if (!row) { row = { model, source: 'CLI_OBSERVED' }; runtime.modelUsage.push(row); }
        for (const key of Object.keys(usage)) row[key] = (row[key] ?? 0) + usage[key];
        save();
      }
      const id = driver.sessionId(event, requestedSessionId);
      if (id && !runtime.sessionId) {
        runtime.sessionId = id; save(); onSession(id);
      }
      if (!settled && (id || resume)) { settled = true; clearTimeout(timer);
        resolveReadyFn({ target: frame?.spawn?.task_name ?? frame?.followup?.target ?? 'team_orchestrator', sessionId: runtime.sessionId }); }
    };
    const ready = new Promise((resolveReady, rejectReady) => {
      resolveReadyFn = resolveReady;
      timer = setTimeout(() => {
        if (!settled) {
          settled = true; runtime.lastError = 'CLI_SESSION_START_TIMEOUT';
          runtime.lastErrorAt = new Date().toISOString(); save();
          if (child.pid) void stopImpl(child.pid).catch(() => {});
          rejectReady(Object.assign(new Error('CLI_SESSION_START_TIMEOUT'),
            { code: 'CLI_SESSION_START_TIMEOUT' }));
        }
      }, dependencies.startTimeoutMs ?? 30000);
      child.stdout.on('data', chunk => {
        buffered += chunk.toString('utf8');
        const lines = buffered.split(/\r?\n/); buffered = lines.pop() ?? '';
        for (const line of lines) if (line) handleLine(line);
      });
      child.stderr.on('data', chunk => errors.write(JSON.stringify({ timestamp: new Date().toISOString(),
        type: 'STDERR_ACTIVITY', bytes: chunk.length }) + '\n'));
      child.once('error', error => {
        runtime.lastError = 'CLI_SESSION_START_FAILED'; runtime.lastErrorAt = new Date().toISOString(); save();
        if (!settled) { settled = true; clearTimeout(timer); rejectReady(Object.assign(new Error('CLI_SESSION_START_FAILED'),
          { code: 'CLI_SESSION_START_FAILED', cause: error })); }
      });
      child.stdin.once('error', error => {
        runtime.lastError = 'CLI_STDIN_WRITE_FAILED'; runtime.lastErrorAt = new Date().toISOString(); save();
        if (!settled) { settled = true; clearTimeout(timer); rejectReady(Object.assign(new Error('CLI_STDIN_WRITE_FAILED'),
          { code: 'CLI_STDIN_WRITE_FAILED', cause: error })); }
      });
      child.once('exit', code => {
        if (buffered) handleLine(buffered);
        clearTimeout(timer); transcript.end(); errors.end(); active = null;
        runtime.status = stopping ? 'STOPPED' : code === 0 ? 'IDLE' : 'FAILED'; runtime.pid = null; runtime.endedAt = new Date().toISOString();
        runtime.exitCode = code;
        if (code !== 0) { runtime.lastError = runtime.containment === 'FAILED' ? 'WINDOWS_JOB_LAUNCH_FAILED' : 'CLI_PROCESS_EXITED'; runtime.lastErrorAt = runtime.endedAt; }
        save(); if (!stopping) onExit({ operationId: operation.id, kind: operation.kind, exitCode: code });
        if (!settled) { settled = true; rejectReady(Object.assign(new Error('CLI_SESSION_START_FAILED'),
          { code: 'CLI_SESSION_START_FAILED', exitCode: code })); }
      });
    });
    active = { child, ready };
    child.stdin.end(prompt, 'utf8');
    return ready;
  };

  return Object.freeze({
    snapshot, reconcile,
    async dispatch(operation, frame) {
      if (!operation || !['PREPARE', 'GAME'].includes(operation.kind)) fail('INVALID_CLI_OPERATION');
      return start(operation, frame);
    },
    async chat({ id, message }) {
      const prompt = operatorChatPrompt(message);
      if (!runtime.sessionId) fail('ORCHESTRATOR_SESSION_UNAVAILABLE');
      return start({ id, kind: 'CHAT' }, { followup: { target: 'team_orchestrator', message: message.trim() } }, prompt);
    },
    async stop() {
      const pid = runtime.pid;
      stopping = true;
      try { if (pid) await stopImpl(pid); }
      catch (error) { stopping = false; throw error; }
      runtime.status = 'STOPPED'; runtime.pid = null; runtime.operationId = null;
      runtime.endedAt = new Date().toISOString(); save(); active = null;
      return { treeStopped: true };
    }
  });
}

export const _private = Object.freeze({ protocolPrompt, operatorChatPrompt, threadId, usageFromEvent, transcriptEvent, providers });
