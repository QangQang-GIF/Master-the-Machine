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
- **Authentication**: Authenticate using demo/seed credentials documented in the project. Never use Game Master credentials.
  Default credentials:
  Email: `morpheusadam95` (or check login seed)
  Password: `change-me-please`
- **Reliable Playwright UI Guidelines**:
  1. **Viewport**: Use `{ width: 1600, height: 1000 }` on desktop so responsive drawers / overlays do not obscure content.
  2. **Page Wait Strategy**: Use `{ waitUntil: "domcontentloaded" }` (NEVER use `networkidle` because polling endpoints like `balanceSheet` or trpc prevent idle state).
  3. **Hydration Buffer**: Always add `await page.waitForTimeout(1000)` after page navigation before clicking anything, allowing Next.js React hydration to finish.
  4. **Radix Tabs / Button Click**: Use DOM direct click via evaluate to bypass all overlays:
     `await page.locator('[role="tab"]').filter({ hasText: "TabName" }).evaluate(el => el.click());`
  5. **Data-First for Screenshots**: Prefer seeding the necessary records into the database directly, then opening the browser to take screenshots of the rendered lists/tables. Do not write brittle multi-step dialog flows under strict match time limits.
- **Time Guard & Fallback**:
  Cap UI capture at 90 seconds. If an unexpected error occurs:
  1. Take `page.screenshot({ path: join(evidenceDir, "diagnostic-ui.png"), fullPage: true })`.
  2. If missing required PNGs, generate valid fallback PNGs with `python <pack_root>/scripts/png_fallback.py <target_path>`.
  3. Mark failed/skipped scenarios `NOT_RUN` with an honest note and ensure all required filenames exist on disk.
  4. Complete `submit.json` and set `status: "PARTIAL"` (NEVER `TIMED_OUT`).
- **Validation**: Verify that every referenced evidence file exists on disk, is non-empty, and does not contain sensitive secrets or tokens. Reject path traversal (`../`) and absolute paths.
