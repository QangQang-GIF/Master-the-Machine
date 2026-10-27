// Provider events are evidence, not an operating-system audit of every action.
export function createEventSanitizer(protocol = '') {
  const secrets = new Set();
  const visit = value => {
    if (!value || typeof value !== 'object') return;
    for (const [key, item] of Object.entries(value)) {
      if (/secret|password|token|authorization|api.?key|cookie/i.test(key) && typeof item === 'string' && item.length >= 4) secrets.add(item);
      else if (typeof item === 'object') visit(item);
    }
  };
  try { visit(JSON.parse(protocol.slice(protocol.indexOf('{')))); } catch {}
  const clean = value => {
    let text = typeof value === 'string' ? value : JSON.stringify(value ?? null);
    for (const secret of secrets) {
      for (const variant of [secret, JSON.stringify(secret).slice(1,-1), encodeURIComponent(secret)]) text = text.split(variant).join('[REDACTED]');
    }
    return text.replace(/\bBearer\s+[^\s"'<>]+/gi, 'Bearer [REDACTED]')
      .replace(/\b(?:ghp|github_pat|gm_live|gm_player|gm_team)_[A-Za-z0-9_\-^\\]+/g, '[REDACTED]')
      .replace(/(["']?(?:password|secret|token|authorization|apiKeySecret|cookie)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}]+)/gi, '$1[REDACTED]')
      .replace(/(https?:\/\/)[^\s/@]+:[^\s/@]+@/gi, '$1[REDACTED]@')
      .slice(0, 12000);
  };
  return clean;
}

export function projectCliActivity(event, clean = createEventSanitizer()) {
  const item = event.item ?? {};
  const phase = item.status === 'failed' || event.type === 'turn.failed' ? 'FAILED' :
    event.type?.endsWith('.started') ? 'STARTED' : event.type?.endsWith('.completed') ? 'COMPLETED' : 'OBSERVED';
  const timestamp = new Date().toISOString();
  const source = 'CLI_OBSERVED';
  const records = [];
  const add = (category, fields) => records.push({ category, timestamp, source, phase, ...fields });
  let detail = null;
  if (['command_execution','mcp_tool_call'].includes(item.type)) {
    const toolName = item.type === 'command_execution' ? 'shell' : clean([item.server,item.tool].filter(Boolean).join('/'));
    detail = { name: toolName, phase, input: clean(item.command ?? item.arguments ?? {}),
      output: clean(item.aggregated_output ?? item.result ?? item.error ?? ''), exitCode: item.exit_code ?? null };
    add('tool', { toolName, operation: item.type, summary: detail.input });
    if (item.type === 'command_execution') add('process', { processName: 'CLI command', operation: 'COMMAND', summary: detail.input });
  }
  if (item.type === 'file_change') {
    for (const change of (Array.isArray(item.changes) ? item.changes : []).slice(0,100)) {
      add('filesystem', { path: clean(change.path), access: 'WRITE', operation: clean(change.kind ?? 'CHANGE'), summary: 'File change reported by CLI; reads are not monitored.' });
    }
  }
  if (event.type === 'thread.started' || event.type === 'turn.started' || event.type === 'turn.completed' || event.type === 'turn.failed') {
    add('agent', { agentId: 'team_orchestrator', parentAgentId: null, operation: event.type, summary: event.type });
  }
  // Only explicit HTTP telemetry is counted as network evidence. A URL in a
  // command or tool argument does not prove that a request happened.
  const telemetry = (item.result?.structuredContent ?? item.result?.structured_content)?.networkActivity;
  for (const entry of (Array.isArray(telemetry) ? telemetry : []).slice(0,100)) {
    try {
      const url = new URL(entry.url);
      if (!['http:','https:'].includes(url.protocol) || !/^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)$/.test(entry.method)) continue;
      add('network', { source: 'MCP_REPORTED', operation: 'HTTP', method: entry.method,
        origin: url.origin, path: clean(url.pathname), httpStatus: Number.isInteger(entry.httpStatus) ? entry.httpStatus : null,
        summary: 'Explicit MCP HTTP telemetry.' });
    } catch {}
  }
  return { records, detail };
}
