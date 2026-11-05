# V2 Production Readiness Checklist

Legend: [x] verified by automated test in this repo, [~] modelled only, [ ] NOT verified.

- [x] Fail-closed submission (no auto-heal, no forced PARTIAL), tests in test_v2_phase1_safety
- [x] Safe-stop protocol (request -> ack -> snapshot), test_v2_safe_stop, test_v2_gaps
- [x] Emergency flush never fabricates tests; waits for confirmed stop
- [x] Atomic claims / one worker one task / capacity <= 4 normal (test_v2_fault_injection)
- [x] Schemas and checksums, contract suite (86 passed)
- [~] PI wait p95 not worse (model: V2 0s vs V1 ~679s; seeded model, not measurement)
- [~] Heavy median >= 20% faster (model: 24% at 30s/child overhead, 14% at 60s; NOT a measurement)
- [ ] Native child spawn, per-agent model override, stop signalling, Root slot accounting (run docs/V2_RUNTIME_SPIKE.md)
- [ ] Heavy DAG execution (worktrees, merge, QA) - not built
- [ ] Unacked-event replay in Root wait loop; PI slot preemption
- [ ] Shadow scheduler / canary rollout
- [ ] Practice Match end-to-end with V2 flags on
