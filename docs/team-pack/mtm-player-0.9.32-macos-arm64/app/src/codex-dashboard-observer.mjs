import { createCodexTranscriptReader } from './codex-transcript-reader.mjs';
import { discoverCodexTranscripts } from './codex-transcript-discovery.mjs';

export function createCodexDashboardObserver(registry, { now = () => new Date().toISOString(), sessionsDirectory = null, lifecycleState = null } = {}) {
  const readers = new Map(), activity = [], usage = new Map();
  let nextDiscovery = 0, discoveryStatus = 'HOST_ATTACHMENT_ONLY';
  return { poll() {
    let sources;
    try {
      if (sessionsDirectory && Date.now() >= nextDiscovery) {
        nextDiscovery = Date.now() + 10000;
        discoveryStatus = discoverCodexTranscripts(registry, sessionsDirectory).status;
      }
      sources = registry.transcripts();
    }
    catch { return { status: 'REGISTRY_UNAVAILABLE', tasks: [], activity: [...activity], usage: [...usage.values()] }; }
    for (const source of sources.slice(0, 128)) {
      let state = readers.get(source.codexThreadId);
      if (!state) {
        state = { codexThreadId: source.codexThreadId, status: 'WAITING_FOR_TRANSCRIPT' };
        readers.set(source.codexThreadId, state);
        try { state.reader = createCodexTranscriptReader({ ...source, registry,
          since: registry.snapshot().registrations.find(value => value.codexThreadId === source.codexThreadId).registeredAt }); }
        catch { state.status = 'TRANSCRIPT_READER_UNAVAILABLE'; }
      }
      if (!state.reader) continue;
      try {
        const result = state.reader.poll();
        state.status = result.status;
        for (const event of result.events) {
          const observedAt = now();
          activity.push({ ...event, observedAt });
          if (event.type === 'TASK_STATUS') {
            state.taskStatus = event.taskStatus; state.statusAt = event.statusAt;
          }
          if (event.type !== 'TOKEN_USAGE') continue;
          const key = JSON.stringify([event.codexThreadId, event.model]);
          const value = usage.get(key) ?? { codexThreadId: event.codexThreadId, model: event.model,
            input: 0, cached: 0, output: 0 };
          value.input += event.input_tokens; value.cached += event.cached_input_tokens; value.output += event.output_tokens;
          usage.set(key, value);
        }
      } catch { state.status = 'TRANSCRIPT_READ_STOPPED'; state.reader = null; }
    }
    activity.splice(0, Math.max(0, activity.length - 500));
    const registered = registry.snapshot().registrations;
    const host = lifecycleState?.();
    return { status: sources.length ? 'OBSERVING' : 'WAITING_FOR_HOST_TRANSCRIPT', discoveryStatus,
      coverage: 'SINCE_TASK_REGISTRATION',
      tasks: registered.map(item => {
        const reader = readers.get(item.codexThreadId);
        const stopped = item.role === 'ORCHESTRATOR' && item.agentPath &&
          item.agentPath === host?.rootTarget && host?.agentState === 'STOPPED';
        return { codexThreadId: item.codexThreadId, role: item.role, agentPath: item.agentPath,
          taskStatus: stopped ? 'STOPPED' : reader?.reader ? reader.taskStatus ?? 'UNKNOWN' : 'UNKNOWN',
          statusSource: stopped ? 'PLAYER_STOP_ACK' : reader?.taskStatus ? 'LAST_TRANSCRIPT_EVENT' : 'UNAVAILABLE',
          statusAt: reader?.statusAt ?? null,
          status: reader?.status ?? 'NOT_ATTACHED' };
      }),
      activity: [...activity], usage: [...usage.values()].map(value => ({ ...value })) };
  } };
}
