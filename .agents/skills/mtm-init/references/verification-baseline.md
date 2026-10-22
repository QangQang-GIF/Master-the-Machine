# Verification Baseline

1. **Before Claiming READY_FOR_REVIEW**:
   - The test asserting the bug fix or feature must pass cleanly with exit code 0.
   - Any regression suite relevant to the module must also pass.
   - Run `git diff` to ensure no accidental debug logs, extra files, or unintended changes exist.

2. **MVS (Minimum Viable Solution)**:
   - When the Game Master announces MVS criteria, the exact named tests MUST be verified and confirmed passing.
