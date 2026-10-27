import { startLocalApp } from '../src/local-app.mjs';
let app;
process.once('message', async options => {
  try {
    app = await startLocalApp(options);
    process.send?.({ schemaVersion: 1, status: 'LOCAL_APP_READY', url: app.url, controlUrl: app.controlUrl });
  } catch (error) {
    process.send?.({ status: 'ERROR', code: /^[A-Z0-9_]+$/.test(error.code ?? '') ? error.code : 'LOCAL_APP_START_FAILED' });
    process.exitCode = 1; process.disconnect?.();
  }
});
for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, async () => { await app?.close(); process.exit(0); });
