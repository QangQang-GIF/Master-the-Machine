import { openSync, closeSync, fstatSync, readSync, lstatSync } from 'node:fs';
import { isAbsolute } from 'node:path';

const fail = code => { throw Object.assign(new Error(code), { code }); };
const label = value => typeof value === 'string' && /^[a-zA-Z0-9_.:-]{1,120}$/.test(value);
const counters = ['input_tokens', 'cached_input_tokens', 'output_tokens'];

/** Read only a host-selected transcript belonging to a registered task.
 * Output is a projection, never a transcript copy. Cursor is process-local.
 */
export function createCodexTranscriptReader({ path, codexThreadId, registry,
  chunkBytes = 65536, maxLineBytes = 1048576, since = null }) {
  const sinceMs = since === null ? null : Date.parse(since);
  if (sinceMs !== null && !Number.isFinite(sinceMs)) fail('INVALID_TRANSCRIPT_WINDOW');
  if (!isAbsolute(path ?? '') || !Number.isSafeInteger(chunkBytes) || chunkBytes < 1 ||
      chunkBytes > 1048576 || !Number.isSafeInteger(maxLineBytes) ||
      maxLineBytes < 1 || maxLineBytes > 4194304) fail('INVALID_TRANSCRIPT_OPTIONS');
  if (!registry?.snapshot().registrations.some(entry => entry.codexThreadId === codexThreadId)) {
    fail('TRANSCRIPT_TASK_NOT_REGISTERED');
  }
  let offset = 0, pending = Buffer.alloc(0), skipping = false, verified = false;
  let fileIdentity = null, model = null, totals = null, lineNumber = 0, stopped = null;
  return Object.freeze({
    poll() {
      if (stopped) fail(stopped);
      const events = [];
      const warning = code => events.push({ type: 'OBSERVATION_WARNING', code, codexThreadId, lineNumber });
      const parse = bytes => {
        lineNumber++;
        let record;
        try { record = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); }
        catch { if (!verified) fail('TRANSCRIPT_IDENTITY_UNAVAILABLE'); warning('INVALID_TRANSCRIPT_RECORD'); return; }
        if (!verified || record?.type === 'session_meta') {
          if (record?.type !== 'session_meta' || record.payload?.id !== codexThreadId) fail('TRANSCRIPT_IDENTITY_MISMATCH');
          verified = true;
          return;
        }
        const payload = record?.payload;
        const timestamp = Date.parse(record?.timestamp);
        const inWindow = sinceMs === null || (Number.isFinite(timestamp) && timestamp >= sinceMs);
        if (record?.type === 'turn_context') {
          model = label(payload?.model) ? payload.model : null;
        } else if (record?.type === 'response_item' &&
          ['function_call', 'custom_tool_call'].includes(payload?.type) && label(payload.name)) {
          if (inWindow) events.push({ type: 'TOOL_CALL', codexThreadId, lineNumber, tool: payload.name });
        } else if (record?.type === 'event_msg' && ['task_started','task_complete','task_aborted','turn_aborted'].includes(payload?.type)) {
          if (inWindow) events.push({ type: 'TASK_STATUS', codexThreadId, lineNumber,
            taskStatus: payload.type === 'task_started' ? 'RUNNING' : payload.type === 'task_complete' ? 'IDLE' : 'STOPPED',
            statusAt: record.timestamp });
        } else if (record?.type === 'event_msg' && payload?.type === 'token_count') {
          const next = payload.info?.total_token_usage;
          if (!next || !counters.every(key => Number.isSafeInteger(next[key]) && next[key] >= 0) ||
              next.cached_input_tokens > next.input_tokens) { warning('INVALID_TOKEN_COUNTER'); return; }
          if (totals && counters.some(key => next[key] < totals[key])) {
            warning('TOKEN_COUNTER_RESET'); totals = { ...next }; return;
          }
          const needsBaseline = sinceMs !== null && totals === null;
          const delta = Object.fromEntries(counters.map(key => [key, next[key] - (totals?.[key] ?? 0)]));
          totals = Object.fromEntries(counters.map(key => [key, next[key]]));
          if (needsBaseline && inWindow) warning('TOKEN_BASELINE_UNAVAILABLE');
          if (inWindow && !needsBaseline && Object.values(delta).some(value => value > 0)) {
            events.push({ type: 'TOKEN_USAGE', codexThreadId, lineNumber, model, ...delta });
          }
        }
      };
      let fd;
      try {
        if (lstatSync(path).isSymbolicLink()) fail('TRANSCRIPT_LINK_NOT_ALLOWED');
        fd = openSync(path, 'r');
        const stat = fstatSync(fd);
        if (!stat.isFile()) fail('INVALID_TRANSCRIPT_FILE');
        const identity = `${stat.dev}:${stat.ino}:${stat.birthtimeMs}`;
        if (fileIdentity && (fileIdentity !== identity || stat.size < offset)) fail('TRANSCRIPT_SOURCE_CHANGED');
        fileIdentity = identity;
        const chunk = Buffer.alloc(Math.min(chunkBytes, stat.size - offset));
        const count = readSync(fd, chunk, 0, chunk.length, offset);
        offset += count;
        const data = Buffer.concat([pending, chunk.subarray(0, count)]);
        pending = Buffer.alloc(0);
        let start = 0;
        for (let index = 0; index < data.length; index++) {
          if (data[index] !== 10) continue;
          if (skipping || index - start > maxLineBytes) {
            if (!verified) fail('TRANSCRIPT_IDENTITY_UNAVAILABLE');
            lineNumber++; warning('TRANSCRIPT_LINE_TOO_LARGE'); skipping = false;
          } else parse(data.subarray(start, index));
          start = index + 1;
        }
        if (data.length - start > maxLineBytes) skipping = true;
        if (!skipping) pending = Buffer.from(data.subarray(start));
        return { status: verified ? 'READING' : 'WAITING_FOR_METADATA', offset, events };
      } catch (error) {
        if (error.code === 'ENOENT' && !fileIdentity) return { status: 'WAITING_FOR_TRANSCRIPT', offset, events: [] };
        stopped = typeof error.code === 'string' && error.code.startsWith('TRANSCRIPT_')
          ? error.code : 'TRANSCRIPT_READ_FAILED';
        fail(stopped);
      } finally { if (fd !== undefined) closeSync(fd); }
    }
  });
}
