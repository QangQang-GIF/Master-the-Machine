import { spawn } from 'node:child_process';
import { isAbsolute } from 'node:path';
import { requireThat } from './protocol.mjs';

/** Run the registered root only; stdout is the sole protocol channel. */
export function invoke(registration, request, workspace, onMessage, timeoutMs) {
  requireThat(isAbsolute(registration.command[0]), 'ABSOLUTE_EXECUTABLE_REQUIRED');
  const env = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (['path', 'systemroot', 'windir', 'temp', 'tmp', 'tmpdir', 'lang'].includes(key.toLowerCase())) env[key] = value;
  }
  return new Promise((resolve, reject) => {
    const child = spawn(registration.command[0], registration.command.slice(1), {
      cwd: workspace, env, shell: false, windowsHide: true, stdio: ['pipe', 'pipe', 'ignore']
    });
    let settled = false;
    let buffer = '';
    let size = 0;
    const finish = error => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) { child.kill(); reject(error); } else resolve();
    };
    const timer = setTimeout(() => finish(new Error('AGENT_TIMEOUT')), timeoutMs);
    child.on('error', () => finish(new Error('AGENT_LAUNCH_FAILED')));
    child.stdin.on('error', () => finish(new Error('AGENT_INPUT_FAILED')));
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', chunk => {
      if (settled) return;
      try {
        size += Buffer.byteLength(chunk);
        requireThat(size <= 65_536, 'AGENT_OUTPUT_LIMIT');
        buffer += chunk;
        let split;
        while ((split = buffer.indexOf('\n')) !== -1) {
          const line = buffer.slice(0, split);
          buffer = buffer.slice(split + 1);
          onMessage(JSON.parse(line));
        }
      } catch { finish(new Error('AGENT_PROTOCOL_FAILED')); }
    });
    child.on('close', code => finish(code === 0 && buffer === '' ? null : new Error('AGENT_EXIT_FAILED')));
    child.stdin.end(JSON.stringify(request) + '\n');
  });
}
