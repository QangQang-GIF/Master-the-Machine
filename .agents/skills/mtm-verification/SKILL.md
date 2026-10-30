---
name: mtm-verification
description: Verify MTM acceptance criteria, MVS coverage, and regressions with real execution logs.
---

# Verification

Follow `mtm-init` and `mtm-worker`.

1. Execute reproduction check and every announced MVS scenario ID. MVS is mandatory.
2. Record exact commands, stdout/stderr, and exit code 0 for PASS.
3. Isolated test runs must never mutate shared fixtures.
4. Distinguish baseline pre-existing failures from new regressions; all new regressions must be fixed.
5. Report `READY_FOR_REVIEW` only when all executed checks pass and required artifacts exist on disk.
