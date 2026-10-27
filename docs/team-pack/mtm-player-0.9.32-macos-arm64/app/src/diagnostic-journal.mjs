import { appendFileSync, existsSync, lstatSync, readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { join } from 'node:path';

const hash = value => createHash('sha256').update(value).digest('hex');
const code = value => typeof value === 'string' && /^[A-Za-z0-9_.:\/-]{1,256}$/.test(value) ? value : null;
const summary = value => typeof value === 'string' ? value
  .replace(/\x1b\[[0-?]*[ -\/]*[@-~]/g, '').replace(/\[[0-9;]*m/g, '')
  .replace(/\bBearer\s+[^\s"'<>]+/gi, 'Bearer [REDACTED]')
  .replace(/\b(?:ghp|github_pat|gm_live|gm_player|gm_team)_[A-Za-z0-9_\-^\\]+/g, '[REDACTED]')
  .replace(/(["']?(?:password|secret|token|authorization|apiKeySecret|cookie)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}]+)/gi, '$1[REDACTED]')
  .replace(/(https?:\/\/)[^\s/@]+:[^\s/@]+@/gi, '$1[REDACTED]@')
  .replace(/[A-Za-z0-9_\-]{48,}/g, '[REDACTED]')
  .replace(/[\u0000-\u001f\u007f]+/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 2000) : null;

/** Best-effort, credential-free, hash-chained diagnostics outside the team workspace. */
export function openDiagnosticJournal(directory) {
  const path = join(directory, 'diagnostics.ndjson');
  let previousHash = '0'.repeat(64), integrity = 'VERIFIED', records = [], sequence = 0;
  try {
    if (existsSync(path)) {
      const stat = lstatSync(path);
      if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 8 * 1024 * 1024) throw new Error('INVALID_FILE');
      const lines = readFileSync(path, 'utf8').split(/\r?\n/).filter(Boolean);
      for (const line of lines) {
        const record = JSON.parse(line); const { hash: recordedHash, ...body } = record;
        if (body.previousHash !== previousHash || recordedHash !== hash(JSON.stringify(body))) throw new Error('CHAIN_MISMATCH');
        previousHash = recordedHash; sequence = body.sequence; records.push(record);
      }
      records = records.slice(-5000);
    }
  } catch { integrity = 'TAMPERED_OR_CORRUPT'; records = []; }
  const append = (type, details = {}) => {
    try {
      const safeDetails = Object.fromEntries(Object.entries(details).flatMap(([key, value]) => {
        if (!/^[A-Za-z][A-Za-z0-9]{0,63}$/.test(key)) return [];
        if (value === null || typeof value === 'boolean' || Number.isSafeInteger(value)) return [[key, value]];
        const safe = key === 'summary' ? summary(value) : code(value); return safe ? [[key, safe]] : [];
      }));
      const body = { schemaVersion: 1, sequence: ++sequence, timestamp: new Date().toISOString(),
        type: code(type) ?? 'INVALID_DIAGNOSTIC_TYPE', details: safeDetails, previousHash };
      const record = { ...body, hash: hash(JSON.stringify(body)) };
      appendFileSync(path, JSON.stringify(record) + '\n', { encoding: 'utf8', mode: 0o600 });
      previousHash = record.hash; records.push(record); if (records.length > 5000) records = records.slice(-5000);
      return true;
    } catch { integrity = 'WRITE_FAILED'; return false; }
  };
  return Object.freeze({ path, append,
    snapshot(limit = 100) { return { status: integrity, path, head: previousHash,
      records: records.slice(-Math.max(1, Math.min(500, limit))) }; } });
}
