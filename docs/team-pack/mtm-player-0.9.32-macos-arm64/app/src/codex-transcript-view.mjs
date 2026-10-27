import { closeSync, fstatSync, lstatSync, openSync, readSync } from 'node:fs';

const fail = code => { throw Object.assign(new Error(code), { code }); };
const safeId = value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(value);
const redact = value => String(value ?? '')
  .replace(/\b(Bearer\s+)[^\s"']+/gi, '$1[REDACTED]')
  .replace(/\b(?:ghp|github_pat|gm_player|gm_team)_[A-Za-z0-9_\-^\\]{8,}\b/g, '[REDACTED]')
  .replace(/((?:password|secret|apiKeySecret|authorization)["'\s:=]+)[^\s,}"']+/gi, '$1[REDACTED]')
  .slice(0, 4000);

function messageText(payload) {
  if (!Array.isArray(payload?.content)) return null;
  const text = payload.content.map(item => item?.text ?? item?.input_text ?? item?.output_text ?? '').filter(Boolean).join('\n');
  return text ? redact(text) : null;
}

function project(record, codexThreadId) {
  const payload = record?.payload;
  const base = { timestamp: typeof record?.timestamp === 'string' ? record.timestamp : null, codexThreadId };
  if (record?.type === 'response_item' && payload?.type === 'message' && ['user','assistant'].includes(payload.role)) {
    const text = messageText(payload);
    return text ? { ...base, type: 'MESSAGE', role: payload.role.toUpperCase(), text } : null;
  }
  if (record?.type === 'event_msg' && ['user_message','agent_message'].includes(payload?.type)) {
    const text = redact(payload.message ?? payload.text ?? '');
    return text ? { ...base, type: 'MESSAGE', role: payload.type === 'user_message' ? 'USER' : 'ASSISTANT', text } : null;
  }
  if (record?.type === 'response_item' && ['function_call','custom_tool_call'].includes(payload?.type) &&
      typeof payload.name === 'string') return { ...base, type: 'TOOL_CALL', role: 'TOOL', text: redact(payload.name) };
  if (record?.type === 'event_msg' && ['task_started','task_complete','task_aborted','turn_aborted'].includes(payload?.type)) {
    return { ...base, type: 'TASK_STATUS', role: 'SYSTEM', text: payload.type };
  }
  return null;
}

export function readCodexTranscriptView(registry, codexThreadId, { maxBytes = 2 * 1024 * 1024, maxItems = 200 } = {}) {
  if (!safeId(codexThreadId) || !Number.isSafeInteger(maxBytes) || maxBytes < 65536 || maxBytes > 4 * 1024 * 1024 ||
      !Number.isSafeInteger(maxItems) || maxItems < 1 || maxItems > 500) fail('INVALID_TRANSCRIPT_VIEW_REQUEST');
  const registration = registry?.snapshot().registrations.find(item => item.codexThreadId === codexThreadId);
  if (!registration) fail('TRANSCRIPT_TASK_NOT_REGISTERED');
  const source = registry.transcripts().find(item => item.codexThreadId === codexThreadId);
  if (!source) return { schemaVersion: 1, status: 'WAITING_FOR_TRANSCRIPT', task: registration, items: [] };
  if (lstatSync(source.path).isSymbolicLink()) fail('TRANSCRIPT_LINK_NOT_ALLOWED');
  let descriptor;
  try {
    descriptor = openSync(source.path, 'r');
    const stat = fstatSync(descriptor);
    if (!stat.isFile()) fail('INVALID_TRANSCRIPT_FILE');
    const firstBytes = Buffer.alloc(Math.min(1024 * 1024, stat.size));
    const firstCount = readSync(descriptor, firstBytes, 0, firstBytes.length, 0);
    const firstLine = firstBytes.subarray(0, firstCount).toString('utf8').split('\n', 1)[0];
    let metadata;
    try { metadata = JSON.parse(firstLine); } catch { fail('TRANSCRIPT_IDENTITY_UNAVAILABLE'); }
    if (metadata?.type !== 'session_meta' || metadata.payload?.id !== codexThreadId) fail('TRANSCRIPT_IDENTITY_MISMATCH');
    const start = Math.max(0, stat.size - maxBytes);
    const bytes = Buffer.alloc(stat.size - start);
    const count = readSync(descriptor, bytes, 0, bytes.length, start);
    const text = bytes.subarray(0, count).toString('utf8');
    const lines = text.split('\n');
    if (start > 0) lines.shift();
    const items = [];
    for (const line of lines) {
      if (!line || Buffer.byteLength(line) > 1024 * 1024) continue;
      let record;
      try { record = JSON.parse(line); } catch { continue; }
      const item = project(record, codexThreadId);
      if (item) items.push(item);
    }
    return { schemaVersion: 1, status: 'READY', task: registration, truncated: start > 0,
      items: items.slice(-maxItems) };
  } finally { if (descriptor !== undefined) closeSync(descriptor); }
}
