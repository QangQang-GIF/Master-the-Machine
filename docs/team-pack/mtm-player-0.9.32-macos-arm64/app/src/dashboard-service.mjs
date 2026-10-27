import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { requireThat } from './protocol.mjs';

const workerPath = fileURLToPath(new URL('../scripts/dashboard-worker.mjs', import.meta.url));
const serviceError = error => {
  const code = ['EPERM', 'EACCES'].includes(error?.code) ?
    'DASHBOARD_SERVICE_PERMISSION_REQUIRED' : 'DASHBOARD_SERVICE_START_FAILED';
  return Object.assign(new Error(code), { code });
};

/** Fails before the secure form opens when this host cannot create the service. */
export function verifyDashboardServiceCapability({ spawnImpl = spawn } = {}) {
  return new Promise((resolve, reject) => {
    let child;
    try {
      child = spawnImpl(process.execPath, ['-e', 'process.exit(0)'], {
        windowsHide: true, stdio: 'ignore'
      });
    } catch (error) { reject(serviceError(error)); return; }
    child.once('error', error => reject(serviceError(error)));
    child.once('exit', code => code === 0 ? resolve() : reject(serviceError()));
  });
}

/** Starts the live dashboard outside the lifetime of the current Codex command turn. */
export function startDashboardService(options, { timeoutMs = 15_000, spawnImpl = spawn } = {}) {
  requireThat(options && typeof options === 'object' && !Array.isArray(options),
    'INVALID_DASHBOARD_SERVICE');
  let child;
  try {
    child = spawnImpl(process.execPath, [workerPath], {
      detached: true,
      windowsHide: true,
      stdio: ['ignore', 'ignore', 'ignore', 'ipc'],
      serialization: 'json'
    });
  } catch (error) { return Promise.reject(serviceError(error)); }
  return new Promise((resolve, reject) => {
    let settled = false;
    const timer = setTimeout(() => finish(new Error('DASHBOARD_SERVICE_TIMEOUT')), timeoutMs);
    timer.unref?.();
    const finish = (error, message) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      child.off('error', onError);
      child.off('exit', onExit);
      child.off('message', onMessage);
      if (error) {
        try { child.kill(); } catch { /* best effort */ }
        reject(error);
        return;
      }
      child.disconnect();
      child.unref();
      resolve(Object.freeze({ url: message.url, logPath: message.logPath,
        staticDashboardFile: message.staticDashboardFile,
        close: async () => {
          // A live parent closes the service normally. If the command host kills the
          // parent, the detached child remains alive and stops on terminal match state.
          try { process.kill(child.pid, 'SIGTERM'); } catch { /* already stopped */ }
        } }));
    };
    const onError = error => finish(serviceError(error));
    const onExit = () => finish(new Error('DASHBOARD_SERVICE_EXITED'));
    const onMessage = message => {
      if (!message || message.schemaVersion !== 1 || message.status !== 'DASHBOARD_READY' ||
          typeof message.url !== 'string' || typeof message.logPath !== 'string' ||
          typeof message.staticDashboardFile !== 'string') {
        finish(new Error('INVALID_DASHBOARD_SERVICE_RESPONSE'));
        return;
      }
      finish(null, message);
    };
    child.once('error', onError);
    child.once('exit', onExit);
    child.once('message', onMessage);
    child.send({ schemaVersion: 1, type: 'START_DASHBOARD', options });
  });
}
