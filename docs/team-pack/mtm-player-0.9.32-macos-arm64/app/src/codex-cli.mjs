import { existsSync, readdirSync, statSync } from 'node:fs';
import { delimiter, join } from 'node:path';

const executableOnPath = (env, platform) => {
  const names = platform === 'win32' ? ['codex.exe'] : ['codex'];
  for (const directory of String(env.PATH ?? '').split(delimiter).map(value => value.trim().replace(/^"|"$/g, '')).filter(Boolean)) {
    for (const name of names) {
      const candidate = join(directory, name);
      if (existsSync(candidate)) return candidate;
    }
  }
  return null;
};

const desktopCandidates = env => {
  const root = env.LOCALAPPDATA ? join(env.LOCALAPPDATA, 'OpenAI', 'Codex', 'bin') : null;
  if (!root || !existsSync(root)) return [];
  const candidates = [join(root, 'codex.exe')];
  try {
    for (const entry of readdirSync(root, { withFileTypes: true })) {
      if (entry.isDirectory()) candidates.push(join(root, entry.name, 'codex.exe'));
    }
  } catch {}
  return candidates.filter(existsSync).map(path => {
    try { return { path, modifiedAt: statSync(path).mtimeMs }; } catch { return { path, modifiedAt: 0 }; }
  }).sort((left, right) => right.modifiedAt - left.modifiedAt);
};

/** Find an installed Codex executable without returning a synthetic fallback. */
export function discoverCodexExecutable({ env = process.env, platform = process.platform } = {}) {
  if (typeof env.CODEX_CLI_PATH === 'string' && env.CODEX_CLI_PATH.trim() &&
      existsSync(env.CODEX_CLI_PATH.trim())) return env.CODEX_CLI_PATH.trim();
  const fromPath = executableOnPath(env, platform);
  if (fromPath) return fromPath;
  if (platform === 'win32') {
    const desktop = desktopCandidates(env)[0];
    if (desktop) return desktop.path;
  }
  return null;
}

/** Resolve the authenticated Codex executable without fixing a release-specific install path. */
export function resolveCodexExecutable(options = {}) {
  const discovered = discoverCodexExecutable(options);
  if (discovered) return discovered;
  const env = options.env ?? process.env;
  if (typeof env.CODEX_CLI_PATH === 'string' && env.CODEX_CLI_PATH.trim()) return env.CODEX_CLI_PATH.trim();
  return 'codex';
}
