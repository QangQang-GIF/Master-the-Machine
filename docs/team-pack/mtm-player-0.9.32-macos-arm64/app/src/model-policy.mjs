import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { resolveCodexExecutable } from './codex-cli.mjs';

const fail = code => { throw Object.assign(new Error(code), { code }); };
const modelId = value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/.test(value);

/** Read-only host catalog query. Never starts a thread or model turn. */
export function listCodexModels({ spawnImpl = spawn, command = resolveCodexExecutable(), timeoutMs = 30000 } = {}) {
  return new Promise((resolve, reject) => {
    let child, lines, timer, settled = false, bytes = 0, sequence = 2;
    const models = new Map(), cursors = new Set();
    const finish = (error, value) => {
      if (settled) return; settled = true; clearTimeout(timer); lines?.close();
      child?.stdin?.end(); child?.kill(); error ? reject(error) : resolve(value);
    };
    const error = code => finish(Object.assign(new Error(code), { code }));
    try { child = spawnImpl(command, ['app-server'], { windowsHide: true, shell: false, stdio: ['pipe', 'pipe', 'ignore'] }); }
    catch (cause) { error(cause?.code === 'ENOENT' ? 'CODEX_CLI_NOT_FOUND' : 'MODEL_CATALOG_UNAVAILABLE'); return; }
    child.on('error', cause => error(cause?.code === 'ENOENT' ? 'CODEX_CLI_NOT_FOUND' : 'MODEL_CATALOG_UNAVAILABLE'));
    child.on('exit', () => { if (!settled) error('MODEL_CATALOG_UNAVAILABLE'); });
    child.stdin.on('error', () => error('MODEL_CATALOG_UNAVAILABLE'));
    const send = value => child.stdin.write(JSON.stringify(value) + '\n');
    lines = createInterface({ input: child.stdout, crlfDelay: Infinity });
    timer = setTimeout(() => error('MODEL_CATALOG_TIMEOUT'), timeoutMs);
    lines.on('line', line => {
      if (settled) return;
      bytes += Buffer.byteLength(line); if (bytes > 4 * 1024 * 1024) { error('MODEL_CATALOG_PROTOCOL_ERROR'); return; }
      let value; try { value = JSON.parse(line); } catch { error('MODEL_CATALOG_PROTOCOL_ERROR'); return; }
      if (value.id === 1) {
        if (value.error) { error('MODEL_CATALOG_INITIALIZE_FAILED'); return; }
        send({ method: 'initialized', params: {} });
        send({ id: sequence, method: 'model/list', params: { includeHidden: false, limit: 100 } });
      } else if (value.id === sequence) {
        if (value.error || !Array.isArray(value.result?.data)) { error('MODEL_CATALOG_PROTOCOL_ERROR'); return; }
        for (const item of value.result.data) {
          const id = item.model || item.id;
          if (modelId(id) && !item.hidden) models.set(id, { id, name: String(item.displayName || id).slice(0,160) });
        }
        const cursor = value.result.nextCursor;
        if (cursor) {
          if (cursors.has(cursor) || cursors.size >= 10) { error('MODEL_CATALOG_PROTOCOL_ERROR'); return; }
          cursors.add(cursor); sequence++;
          send({ id: sequence, method: 'model/list', params: { includeHidden: false, limit: 100, cursor } });
        } else if (!models.size) error('MODEL_CATALOG_EMPTY');
        else finish(null, { source: 'CODEX_HOST_MODEL_LIST', fetchedAt: new Date().toISOString(), models: [...models.values()] });
      }
    });
    send({ id: 1, method: 'initialize', params: { clientInfo: { name: 'mtm-player-model-catalog', version: '1.0.0' }, capabilities: {} } });
  });
}

export function selectModelPolicy(selected, catalog, orchestratorModel = selected?.[0],
  orchestratorReasoningEffort = 'medium') {
  if (!Array.isArray(selected) || !selected.length || selected.length > 200 ||
      selected.some(id => !modelId(id)) || new Set(selected).size !== selected.length) fail('INVALID_ALLOWED_MODELS');
  if (!catalog?.models?.length) fail('MODEL_CATALOG_UNAVAILABLE');
  if (selected.some(id => !catalog.models.some(model => model.id === id))) fail('UNKNOWN_ALLOWED_MODEL');
  if (!selected.includes(orchestratorModel)) fail('INVALID_ORCHESTRATOR_MODEL');
  if (!['low', 'medium', 'high'].includes(orchestratorReasoningEffort)) fail('INVALID_REASONING_EFFORT');
  return { schemaVersion: 2, orchestratorModel, orchestratorReasoningEffort,
    allowedModels: [...selected], enforcement: 'OBSERVE_ONLY',
    catalogSource: catalog.source, catalogFetchedAt: catalog.fetchedAt };
}

export function modelPolicyFrame(frame, policy) {
  if (!policy) return frame;
  const result = structuredClone(frame);
  const instruction = '\n\nMTM_MODEL_POLICY_V1\n' + JSON.stringify({ ...policy,
    instruction: 'MTM Player has already requested orchestratorModel for this root and independently observes the provider-reported model. Treat orchestratorModel as the selected root model; do not infer another root model from task, UI, profile, or system text. Use only allowedModels for descendants and pass this rule to every worker. OBSERVE_ONLY never blocks preparation or game work. Report MODEL_UNAVAILABLE only when the provider explicitly rejects an allowed model selection. Player records suspected violations; only the organizer decides penalties. This policy does not grant host permissions.' });
  if (result.spawn) {
    result.spawn.message += instruction;
    result.spawn.model = policy.orchestratorModel ?? policy.allowedModels[0];
    result.spawn.reasoningEffort = policy.orchestratorReasoningEffort ?? 'medium';
  }
  if (result.followup) {
    result.followup.message += instruction;
    result.followup.model = policy.orchestratorModel ?? policy.allowedModels[0];
    result.followup.reasoningEffort = policy.orchestratorReasoningEffort ?? 'medium';
  }
  return result;
}

/** Team-only observations; the organizer Player task is not a contestant model use. */
export function applyModelPolicy(snapshot, policy, evidence = new Map(), now = new Date().toISOString()) {
  if (!policy) return snapshot;
  const roles = new Map((snapshot.codexObservation?.tasks || []).map(task => [task.codexThreadId, task.role]));
  const rows = [
    ...(snapshot.modelUsage || []).map(row => ({ model: row.model, task: 'team', source: row.source || 'TEAM_REPORTED' })),
    ...(snapshot.codexObservation?.usage || []).filter(row => ['ORCHESTRATOR', 'SUBAGENT'].includes(roles.get(row.codexThreadId)))
      .map(row => ({ model: row.model, task: row.codexThreadId, source: 'HOST_OBSERVED' }))
  ];
  for (const row of rows) {
    if (!modelId(row.model) || row.model === 'Unknown' || policy.allowedModels.includes(row.model)) continue;
    const key = JSON.stringify([row.source, row.task, row.model]);
    if (!evidence.has(key)) evidence.set(key, { code: 'MODEL_NOT_ALLOWED', source: row.source,
      operation: 'MODEL_USAGE', resource: row.model, model: row.model, taskId: row.task,
      observedAt: now, summary: `Model ${row.model} used by ${row.task}; first observed ${now}. Organizer review required.` });
  }
  return { ...snapshot, modelPolicy: policy, modelViolations: [...evidence.values()],
    suspectedViolations: [...(snapshot.suspectedViolations || []), ...evidence.values()] };
}
