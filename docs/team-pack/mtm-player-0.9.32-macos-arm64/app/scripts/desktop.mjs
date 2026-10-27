import { resolve } from 'node:path';
import { spawn } from 'node:child_process';
import { launchLocalApp } from '../src/local-app-launcher.mjs';
import { defaultPlayerDataDirectory } from '../src/bootstrap.mjs';

try {
  const args = process.argv.slice(2);
  if (args.length !== 0 && (args.length !== 2 || args[0] !== '--workspace')) throw Object.assign(new Error(), { code: 'TEAM_WORKSPACE_REQUIRED' });
  const result = await launchLocalApp({ workspace: args[1] ? resolve(args[1]) : undefined,
    dataDirectory: defaultPlayerDataDirectory(), mode: 'ATTACH', codexThreadId: null });
  const url = new URL('/manage', result.url).href;
  const healthResponse = await fetch(new URL('/health', url), { signal: AbortSignal.timeout(3000) });
  if (!healthResponse.ok) throw Object.assign(new Error(), { code: 'MTM_PLAYER_HEALTH_CHECK_FAILED' });
  const browserCommand = process.platform === 'win32' ? 'rundll32.exe' : process.platform === 'darwin' ? '/usr/bin/open' : 'xdg-open';
  const browser = spawn(browserCommand, process.platform === 'win32' ? ['url.dll,FileProtocolHandler', url] : [url],
    { windowsHide: true, stdio: 'ignore', shell: false });
  // The native launcher offers an explicit Open action if the browser association fails.
  await new Promise(done => { browser.once('spawn', done); browser.once('error', done); });
  browser.unref();
  process.stdout.write('MTM_PLAYER_READY\n');
} catch (error) {
  process.stderr.write(/^[A-Z0-9_]+$/.test(error.code ?? '') ? error.code : 'MTM_PLAYER_START_FAILED');
  process.exitCode = 1;
}
