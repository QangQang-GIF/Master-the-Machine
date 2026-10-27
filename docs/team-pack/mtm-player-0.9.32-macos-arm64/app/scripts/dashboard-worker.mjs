#!/usr/bin/env node
import { startProgressDashboard } from '../src/progress.mjs';
import { createMatchStatusMonitor } from '../src/match-status.mjs';
import { openCodexSessionRegistry } from '../src/codex-session-registry.mjs';
import { createCodexDashboardObserver } from '../src/codex-dashboard-observer.mjs';
import { homedir } from 'node:os';
import { join } from 'node:path';

let dashboard = null;
let monitor = null;
let started = false;

async function stop() {
  monitor?.stop();
  if (dashboard) await dashboard.close().catch(() => {});
  process.exit(0);
}

process.once('SIGINT', stop);
process.once('SIGTERM', stop);
process.once('message', async message => {
  if (started || !message || message.schemaVersion !== 1 ||
      message.type !== 'START_DASHBOARD' || !message.options) process.exit(2);
  started = true;
  try {
    const { session: rawSession, dashboardOptions, hostDataDirectory } = message.options;
    const session = Object.freeze(rawSession);
    let codexObserver = null;
    if (hostDataDirectory) {
      try { codexObserver = createCodexDashboardObserver(openCodexSessionRegistry({ dataDirectory: hostDataDirectory, session }),
        { sessionsDirectory: join(process.env.CODEX_HOME || join(homedir(), '.codex'), 'sessions') }); }
      catch { codexObserver = { poll: () => ({ status: 'REGISTRY_UNAVAILABLE', tasks: [], activity: [], usage: [] }) }; }
    }
    dashboard = await startProgressDashboard({ ...dashboardOptions, codexObserver });
    monitor = createMatchStatusMonitor(session);
    monitor.start({
      onObservation(observation) {
        if (['FINISHED', 'CANCELLED'].includes(observation.status)) {
          // Refresh and preserve the static dashboard before releasing credentials.
          setTimeout(stop, 1_000).unref();
        }
      },
      onError() { /* The dashboard exposes the last persisted observation. */ }
    });
    process.send?.({ schemaVersion: 1, status: 'DASHBOARD_READY', url: dashboard.url,
      logPath: dashboard.logPath, staticDashboardFile: dashboard.staticDashboardFile });
  } catch {
    process.exit(1);
  }
});

// Do not retain an orphan that never received its in-memory session descriptor.
setTimeout(() => { if (!started) process.exit(2); }, 15_000).unref();
