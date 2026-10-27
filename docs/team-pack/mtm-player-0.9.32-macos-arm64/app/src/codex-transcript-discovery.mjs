import { readdirSync, lstatSync } from 'node:fs';
import { join } from 'node:path';

/** Enumerate names only; transcript contents are read only after task registration. */
export function discoverCodexTranscripts(registry, sessionsDirectory) {
  const registrations = registry.snapshot().registrations;
  const attached = new Set(registry.transcripts().map(value => value.codexThreadId));
  const wanted = registrations.filter(value => !attached.has(value.codexThreadId));
  if (!wanted.length) return { status: 'ATTACHED' };
  const matches = new Map(wanted.map(value => [value.codexThreadId, []]));
  const queue = [{ path: sessionsDirectory, depth: 0 }];
  let visited = 0;
  while (queue.length) {
    const current = queue.shift();
    let entries;
    try {
      if (lstatSync(current.path).isSymbolicLink()) continue;
      entries = readdirSync(current.path, { withFileTypes: true });
    } catch { return { status: 'DISCOVERY_UNAVAILABLE' }; }
    for (const entry of entries) {
      if (++visited > 20000) return { status: 'DISCOVERY_LIMIT_REACHED' };
      if (entry.isSymbolicLink()) continue;
      const path = join(current.path, entry.name);
      if (entry.isDirectory() && current.depth < 3 && /^\d{2,4}$/.test(entry.name)) {
        queue.push({ path, depth: current.depth + 1 });
      } else if (entry.isFile() && entry.name.startsWith('rollout-')) {
        for (const [id, paths] of matches) if (entry.name.endsWith(`-${id}.jsonl`)) paths.push(path);
      }
    }
  }
  let ambiguous = false;
  for (const [codexThreadId, paths] of matches) {
    if (paths.length === 1) registry.registerTranscript({ codexThreadId, path: paths[0] });
    if (paths.length > 1) ambiguous = true;
  }
  return { status: ambiguous ? 'DISCOVERY_AMBIGUOUS' : 'DISCOVERY_COMPLETE' };
}
