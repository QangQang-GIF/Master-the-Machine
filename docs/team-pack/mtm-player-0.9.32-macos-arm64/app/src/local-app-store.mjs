import { createHash, randomUUID } from 'node:crypto';
import { closeSync, fsyncSync, linkSync, lstatSync, mkdirSync, openSync, readFileSync,
  readdirSync, realpathSync, unlinkSync, writeFileSync } from 'node:fs';
import { isAbsolute, join } from 'node:path';

const fail = code => { throw Object.assign(new Error(code), { code }); };
const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const identity = value => process.platform === 'win32' ? value.toLowerCase() : value;
const MAX_BYTES = 16 * 1024 * 1024;
function readJson(path) {
  const stat = lstatSync(path);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size > MAX_BYTES) fail('CORRUPT_LOCAL_APP_STORE');
  try { return JSON.parse(readFileSync(path, 'utf8')); } catch { fail('CORRUPT_LOCAL_APP_STORE'); }
}
function publish(path, value) {
  const temporary = `${path}.${randomUUID()}.tmp`;
  let fd;
  try {
    fd = openSync(temporary, 'wx', 0o600);
    writeFileSync(fd, JSON.stringify(value)); fsyncSync(fd); closeSync(fd); fd = undefined;
    linkSync(temporary, path);
  } finally {
    if (fd !== undefined) closeSync(fd);
    try { unlinkSync(temporary); } catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
}

/** Private recovery state. Never serve this directory or serialize load() to a dashboard.
 * On Windows use an organizer-owned user profile directory with its inherited ACL.
 */
export function openLocalAppStore({ directory, workspace }) {
  if (!isAbsolute(directory ?? '') || !isAbsolute(workspace ?? '')) fail('INVALID_LOCAL_APP_DIRECTORY');
  const workspaceIdentity = identity(realpathSync(workspace));
  mkdirSync(directory, { recursive: true, mode: 0o700 });
  if (lstatSync(directory).isSymbolicLink()) fail('INVALID_LOCAL_APP_DIRECTORY');
  const root = realpathSync(directory);
  const privateDirectory = join(root, 'private');
  mkdirSync(privateDirectory, { mode: 0o700, recursive: true });
  if (lstatSync(privateDirectory).isSymbolicLink()) fail('INVALID_LOCAL_APP_DIRECTORY');
  const lockPath = join(root, 'owner.json');
  let owner = null;
  let generation = 0;
  const guard = () => {
    if (lstatSync(directory).isSymbolicLink() || realpathSync(directory) !== root ||
        lstatSync(privateDirectory).isSymbolicLink()) fail('INVALID_LOCAL_APP_DIRECTORY');
  };
  const readLock = () => {
    guard();
    let record;
    try { record = readJson(lockPath); } catch (error) { if (error.code === 'ENOENT') return null; throw error; }
    if (record.schemaVersion !== 1 || !Number.isSafeInteger(record.pid) || record.pid < 1 ||
        typeof record.ownerId !== 'string' || !/^[a-f0-9-]{36}$/.test(record.ownerId)) fail('CORRUPT_LOCAL_APP_LOCK');
    if (record.workspace !== workspaceIdentity) fail('LOCAL_APP_WORKSPACE_MISMATCH');
    return record;
  };
  const loadRecord = () => {
    guard();
    const names = readdirSync(privateDirectory).filter(name => /^\d{9}\.json$/.test(name)).sort();
    if (!names.length) return null;
    const name = names.at(-1);
    const record = readJson(join(privateDirectory, name));
    if (record.schemaVersion !== 1 || record.generation !== Number(name.slice(0, 9)) ||
        !record.state || typeof record.state !== 'object' || Array.isArray(record.state) ||
        record.sha256 !== hash(record.state)) fail('CORRUPT_LOCAL_APP_STORE');
    if (record.workspace !== workspaceIdentity) fail('LOCAL_APP_WORKSPACE_MISMATCH');
    return record;
  };
  const initial = loadRecord(); generation = initial?.generation ?? 0;
  const api = {
    directory: root, workspace: workspaceIdentity, readLock,
    load() { const record = loadRecord(); return record ? structuredClone(record.state) : null; },
    acquireLock() {
      const previous = readLock();
      if (previous) {
        if (owner?.ownerId === previous.ownerId) return structuredClone(owner);
        try { process.kill(previous.pid, 0); fail('LOCAL_APP_ALREADY_RUNNING'); }
        catch (error) { if (error.code !== 'ESRCH') throw error.code === 'EPERM' ? Object.assign(new Error('LOCAL_APP_ALREADY_RUNNING'), { code: 'LOCAL_APP_ALREADY_RUNNING' }) : error; }
        // Exclusive tombstone prevents two stale-lock cleaners deleting a new live owner.
        const reapPath = join(root, `.reaped-${previous.ownerId}`);
        try { publish(reapPath, { pid: process.pid }); }
        catch (error) { if (error.code === 'EEXIST') fail('LOCAL_APP_LOCK_RECOVERY_IN_PROGRESS'); throw error; }
        if (readLock()?.ownerId !== previous.ownerId) fail('LOCAL_APP_LOCK_CHANGED');
        unlinkSync(lockPath);
      }
      const next = { schemaVersion: 1, workspace: workspaceIdentity, pid: process.pid,
        ownerId: randomUUID(), createdAt: new Date().toISOString() };
      try { publish(lockPath, next); }
      catch (error) { if (error.code === 'EEXIST') fail('LOCAL_APP_ALREADY_RUNNING'); throw error; }
      owner = next; return structuredClone(owner);
    },
    releaseLock() {
      if (!owner || readLock()?.ownerId !== owner.ownerId) return false;
      unlinkSync(lockPath); owner = null; return true;
    },
    save(state) {
      if (!owner || readLock()?.ownerId !== owner.ownerId) fail('LOCAL_APP_LOCK_REQUIRED');
      if (!state || typeof state !== 'object' || Array.isArray(state)) fail('INVALID_LOCAL_APP_STATE');
      const copy = JSON.parse(JSON.stringify(state));
      if (Buffer.byteLength(JSON.stringify(copy)) > MAX_BYTES - 2048) fail('LOCAL_APP_STATE_TOO_LARGE');
      if ((loadRecord()?.generation ?? 0) !== generation) fail('STALE_LOCAL_APP_WRITER');
      if (generation >= 999999999) fail('LOCAL_APP_CAPACITY_EXCEEDED');
      const next = generation + 1;
      publish(join(privateDirectory, `${String(next).padStart(9, '0')}.json`), {
        schemaVersion: 1, workspace: workspaceIdentity, generation: next, state: copy, sha256: hash(copy),
      });
      generation = next;
      // Keep at most three committed generations; explicit compact removes all old secrets.
      for (const name of readdirSync(privateDirectory).filter(name => /^\d{9}\.json$/.test(name))) {
        if (Number(name.slice(0, 9)) <= generation - 3) unlinkSync(join(privateDirectory, name));
      }
      return structuredClone(copy);
    },
    compact(state) {
      const saved = api.save(state);
      guard();
      for (const name of readdirSync(privateDirectory)) {
        if ((/^\d{9}\.json$/.test(name) && Number(name.slice(0, 9)) < generation) ||
            /^\d{9}\.json\.[a-f0-9-]{36}\.tmp$/.test(name)) unlinkSync(join(privateDirectory, name));
      }
      return saved;
    },
  };
  return api;
}
