# MTM Player compatibility
- `tests/contract`: 86 passed (prep, telemetry, GM contract) with V2 changes.
- Verified: Start Work / submit contract unchanged; new schema fields are optional.
- Known mismatch (pre-existing, unchanged): worker_result.json status enum lacks PARTIAL/DONE, which native uses.
- NOT verified: live Game Master, live Player UI, real Codex runtime.
