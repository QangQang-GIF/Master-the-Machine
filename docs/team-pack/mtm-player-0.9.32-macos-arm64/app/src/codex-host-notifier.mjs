import { spawn } from 'node:child_process';

const fail = code => { throw Object.assign(new Error(code), { code }); };
const validThread = value => typeof value === 'string' && /^[a-f0-9-]{36}$/.test(value);

export function hostWakeMessage(operation) {
  if (!operation || !/^op-[a-f0-9-]{36}$/.test(operation.id) ||
      !['PREPARE', 'GAME', 'STOP'].includes(operation.kind)) fail('INVALID_HOST_OPERATION');
  const prompt = operation.kind === 'PREPARE' ? 'Initialization completed. Spawn the orchestrator to perform preparation.' :
    operation.kind === 'GAME' ? 'The match is in progress. Continue or restart the orchestrator to start game work.' :
      'The match state requires the orchestrator tree to stop.';
  return `MTM_PLAYER_HOST_EVENT_V1\n${JSON.stringify({ schemaVersion: 1,
    operationId: operation.id, kind: operation.kind,
    prompt,
    instruction: 'Run the installed MTM Player watch command for this workspace. Dispatch the exact pending frame and acknowledge the real host result. Do not run init or create a new round.' })}`;
}

export function queueCodexHostMessage({ codexThreadId, workspace, operation }, dependencies = {}) {
  if (!validThread(codexThreadId)) fail('INVALID_CODEX_THREAD_ID');
  const executable = dependencies.executable ?? 'codex';
  const spawnImpl = dependencies.spawn ?? spawn;
  const message = hostWakeMessage(operation);
  return new Promise((resolve, reject) => {
    const child = spawnImpl(executable, ['queue', '--thread', codexThreadId, '--message', message, '-C', workspace],
      { cwd: workspace, windowsHide: true, shell: false, stdio: ['ignore', 'pipe', 'pipe'] });
    let stderr = '', stdout = '';
    child.stdout?.on('data', chunk => { if (stdout.length < 8192) stdout += chunk; });
    child.stderr?.on('data', chunk => { if (stderr.length < 8192) stderr += chunk; });
    child.once('error', error => reject(Object.assign(new Error('CODEX_HOST_QUEUE_FAILED'),
      { code: 'CODEX_HOST_QUEUE_FAILED', cause: error })));
    child.once('exit', code => code === 0 ? resolve({ status: 'QUEUED' }) :
      reject(Object.assign(new Error('CODEX_HOST_QUEUE_FAILED'), { code: 'CODEX_HOST_QUEUE_FAILED', exitCode: code })));
  });
}
