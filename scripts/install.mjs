#!/usr/bin/env node
import { spawnSync } from 'node:child_process';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const installPy = resolve(__dirname, 'install.py');
const py = process.platform === 'win32' ? 'python' : 'python3';
const res = spawnSync(py, [installPy, ...process.argv.slice(2)], {
  stdio: 'inherit',
  env: process.env,
});
process.exit(res.status ?? 0);
