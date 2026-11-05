# Runtime spike (run in a Codex session, ~10 min)
Answer these, record results in docs/V2_UPGRADE_REPORT.md:
1. Spawn 1 child from a Heavy parent. Does it work? Max depth?
2. Spawn with a different model / reasoning effort. Does the child report the model actually used?
3. Can Root send a message to a running worker and can the worker acknowledge (write `.mtm_stopped.json` with instructionId)?
4. How many concurrent agents are allowed? Does Root count? Do nested children count?
5. Spawn 6 agents: what happens to the 6th (error/queue)?
Until 1-5 are answered, keep MTM_V2_HEAVY_DAG and MTM_V2_MODEL_ROUTING off.
