---
name: mtm-verification
description: Verify MTM acceptance criteria, complete MVS coverage, and regressions using real execution evidence.
---

# Verification

- Check the received template and every announced scenario ID against executed results. A shorter self-declared test list cannot replace required coverage. Run focused checks first, then relevant negative/boundary and regression checks; MVS is a minimum.
- Save command, environment context needed to reproduce, stdout/stderr, and exit code. A tool timeout, missing runner, or unrun check is not PASS. Avoid relying on pipeline exit codes that conceal test failures.
- Isolate worker test resources so concurrent runs cannot alter each other's fixtures or state. Use provided local loaders and task-permitted generated inputs; report unavailable mandatory inputs accurately.
- Classify failures against the recorded baseline. Demonstrably unrelated pre-existing failures do not prevent submission of a verified item; regressions or failed required scenarios must be resolved and remain visible.
- `READY_FOR_REVIEW` requires complete acceptance/MVS coverage, passing relevant checks, and real required artifacts. Report exact unresolved failures otherwise. Root integration must recheck affected behavior because independently passing patches can conflict; the root then pushes and submits promptly.
