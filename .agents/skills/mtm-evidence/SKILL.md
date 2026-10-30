---
name: mtm-evidence
description: Capture, validate, and reference official MTM evidence artifacts and web sessions.
---

# MTM Evidence

- **Storage Location**: Save artifacts under `matchRoot/answers/<key>/evidence/<filename>`. Reference them in `answers/<key>/submit.json` as `evidence/<filename>`.
- **Pre-Change Capture**: Capture requested baseline state before code modifications. Never reconstruct baseline state from modified files.
- **Web Session Mode**: For web UI evidence, invoke:
  `python3 <metadata.project_runtime_path> session -- <browser command>`
  This starts the local dev server on an ephemeral port, verifies readiness, executes capture commands, and cleans up process groups.
- **Authentication**: Authenticate using demo/seed credentials documented in the project. Never use Game Master credentials. Use real headless browser flows; do not bypass authentication.
- **Validation**: Verify that every referenced evidence file exists on disk, is non-empty, and does not contain sensitive secrets or tokens. Reject path traversal (`../`) and absolute paths.
