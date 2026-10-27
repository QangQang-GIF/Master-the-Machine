import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { readFileSync } from 'node:fs';
import { openLocalAppStore } from './local-app-store.mjs';
import { localAppDirectory } from './local-app.mjs';
import { validateTeamWorkspaceLayout } from './bootstrap.mjs';

export async function launchLocalApp(options) {
  if (!options.workspace) {
    try { const response = await fetch('http://127.0.0.1:54321/health', { signal: AbortSignal.timeout(2000) });
      const health = await response.json(); if (response.ok && health.status === 'LOCAL_APP_READY') return { url: 'http://127.0.0.1:54321/manage', version: health.version };
    } catch {}
  }
  if (options.workspace) {
  const store = openLocalAppStore({ directory: localAppDirectory(options.dataDirectory, options.workspace), workspace: options.workspace });
  const previous = store.load(), lock = store.readLock();
  // Read-only preflight before opening a form. Recovery never clears source.
  if (options.mode === 'INIT' && !previous?.match && !options.clearChallenge) {
    validateTeamWorkspaceLayout(options.workspace);
  }
  if (previous?.port && lock) {
    try {
      const base = `http://127.0.0.1:${previous.port}`;
      const health = await fetch(`${base}/health`, { signal: AbortSignal.timeout(2000) }).then(r => r.json());
      if (health.ownerId !== lock.ownerId) throw new Error('LOCAL_APP_OWNER_MISMATCH');
      const expectedVersion = JSON.parse(readFileSync(new URL('../package.json', import.meta.url))).version;
      if (health.version !== expectedVersion) {
        throw Object.assign(new Error('LOCAL_APP_VERSION_MISMATCH'), {
          code: 'LOCAL_APP_VERSION_MISMATCH', runningVersion: health.version, expectedVersion,
        });
      }
      const controlUrl = `${base}/${previous.controlToken}`;
      if (options.mode === 'INIT') {
        const begin = await fetch(controlUrl, { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ schemaVersion: 1, type: 'APP_BEGIN_INIT', clearChallenge: options.clearChallenge === true,
            stopExisting: options.stopExisting === true, codexThreadId: options.codexThreadId ?? null }),
          signal: AbortSignal.timeout(5000) });
        if (!begin.ok) { const receipt = await begin.json(); throw Object.assign(new Error(receipt.code), { code: receipt.code }); }
      }
      const attached = await fetch(controlUrl, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ schemaVersion: 1, type: 'APP_ATTACH', codexThreadId: options.codexThreadId,
          clearChallenge: options.clearChallenge === true }), signal: AbortSignal.timeout(5000) });
      const attachment = await attached.json();
      if (!attached.ok || attachment.status === 'ERROR') throw new Error('LOCAL_APP_ATTACH_FAILED');
      return { schemaVersion: 1, status: 'LOCAL_APP_READY', url: `${base}${options.mode === 'RESTART' ? '/restart' : '/init'}`,
        controlUrl, reattached: true, version: health.version };
    } catch (error) {
      if (['LOCAL_APP_VERSION_MISMATCH', 'ACTIVE_SESSION_STOP_CONFIRMATION_REQUIRED',
        'STOP_AGENTS_BEFORE_NEW_ROUND'].includes(error.code)) throw error;
      /* a dead owner is reclaimed by the store; a live owner remains a conflict */
    }
  }
  }
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [fileURLToPath(new URL('../scripts/local-app-worker.mjs', import.meta.url))],
      { detached: true, windowsHide: true, stdio: ['ignore', 'ignore', 'ignore', 'ipc'], serialization: 'json' });
    const timer = setTimeout(() => { child.kill(); reject(new Error('LOCAL_APP_START_TIMEOUT')); }, 20000);
    child.once('error', error => { clearTimeout(timer); reject(error); });
    child.once('exit', code => { clearTimeout(timer); reject(new Error(`LOCAL_APP_EXITED_${code}`)); });
    child.once('message', message => {
      clearTimeout(timer);
      if (message.status !== 'LOCAL_APP_READY') { child.kill(); reject(Object.assign(new Error(message.code), { code: message.code })); return; }
      child.disconnect(); child.unref(); resolve(message);
    });
    child.send(options);
  });
}
