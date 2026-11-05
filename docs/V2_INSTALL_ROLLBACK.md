# V2 Install / Rollback

## Install (default OFF)
1. `git checkout feat/adaptive-hierarchical-orchestrator-v2`
2. `python scripts/install.py` (writes 5 slots = 4 normal + 1 PI-only reserve; reserved borrow disabled).
3. V2 stays OFF until `MTM_ORCH_V2_ENABLED=1`. Enable one flag at a time, in this order:
   `MTM_V2_LIGHTWEIGHT_EVENTS` -> `MTM_V2_DEADLINE_CONTROL` -> `MTM_V2_ADAPTIVE_SCHEDULER` -> `MTM_V2_MODEL_ROUTING` -> `MTM_V2_HEAVY_DAG`.
   Run one Practice Match per step and compare with the baseline before adding the next.
4. Note: safety fixes (fail-closed submit, no forced PARTIAL, safe-stop, emergency flush) are always on; they are not behind flags.

## Rollback
- Fast: unset `MTM_ORCH_V2_ENABLED` (all V2 flags off). Safety fixes remain.
- Full: `git checkout feat/five-worker-lanes-dispatch` (baseline `f4d8058`) and rerun `python scripts/install.py`.
- Active matches: do not switch mid-match; use `request_stop` + `confirm_stopped` on every running worker first.
