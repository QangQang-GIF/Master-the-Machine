/**
 * Capture every screenshot a task needs in ONE run: one browser, one login, a hard cutoff.
 *
 * Usage (always through the worker sandbox so PORT matches session-start):
 *   python3 <project_runtime.py> --match-root <match_root> --match-id <id> --worker-id <worker> \
 *     run -- node <pack>/scripts/capture_multi.mjs spec.json [--cutoff-epoch <taskEnvelope.evidence_cutoff_local_epoch>]
 *
 * spec.json:
 * {
 *   "outDir": "../answers/BUG-02/evidence",
 *   "login": true,                              // optional, default true
 *   "shots": [
 *     {"route": "/accounting", "file": "tc-01-vnd-invoice.png", "tab": "Invoices"},
 *     {"route": "/", "file": "mobile-390x844.png", "viewport": {"width": 390, "height": 844}, "auth": false}
 *   ]
 * }
 *
 * Prints a JSON report on stdout: [{file, ok, ms, skipped, error}]. A shot past the cutoff
 * is skipped (never faked); the worker marks that test NOT_RUN. Exit 1 if any shot failed.
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join, resolve } from "node:path";

const args = process.argv.slice(2);
const specPath = args.find((a) => !a.startsWith("--"));
const flag = (name) => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : undefined;
};
if (!specPath) {
  console.error("Usage: node capture_multi.mjs spec.json [--cutoff-epoch <epoch>] [--report <file>]");
  process.exit(2);
}

const spec = JSON.parse(readFileSync(resolve(specPath), "utf8"));
const shots = Array.isArray(spec.shots) ? spec.shots : [];
if (!shots.length || !spec.outDir) {
  console.error("spec.json needs outDir and a non-empty shots array");
  process.exit(2);
}
const cutoffRaw = flag("--cutoff-epoch") ?? process.env.MTM_EVIDENCE_CUTOFF_EPOCH;
const cutoffMs = cutoffRaw ? Number(cutoffRaw) * 1000 : Infinity;
const perShotMs = Number(spec.perShotTimeoutMs ?? 15000);
const baseUrl = spec.baseUrl ?? `http://127.0.0.1:${process.env.PORT || 3000}`;
const outDir = resolve(spec.outDir);
mkdirSync(outDir, { recursive: true });

// Seed account: environment first, then spec, then the project's documented local seed user.
const user = process.env.MTM_EVIDENCE_USER ?? spec.user ?? "morpheusadam95";
const password = process.env.MTM_EVIDENCE_PASSWORD ?? spec.password ?? "change-me-please";

async function loadChromium() {
  try {
    const req = createRequire(join(process.cwd(), "package.json"));
    return req("playwright").chromium;
  } catch {
    return (await import("playwright")).chromium;
  }
}

const report = [];
const chromium = await loadChromium();
const browser = await chromium.launch({ headless: true, args: ["--disable-gpu", "--disable-dev-shm-usage"] });
let storageState;
try {
  if (spec.login !== false && Date.now() < cutoffMs) {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    page.setDefaultTimeout(perShotMs);
    try {
      await page.goto(`${baseUrl}${spec.loginPath ?? "/login"}`, { waitUntil: "domcontentloaded" });
      await page.locator(spec.userSelector ?? "#email").fill(user);
      await page.locator(spec.passwordSelector ?? "#password").fill(password);
      await page.getByRole("button", { name: spec.submitLabel ?? "Sign in" }).click({ force: true });
      await page.waitForURL(new RegExp(spec.afterLoginUrl ?? "dashboard"), { timeout: perShotMs });
      storageState = await ctx.storageState();
    } catch (err) {
      report.push({ file: "(login)", ok: false, error: String(err.message || err).slice(0, 300) });
    } finally {
      await ctx.close();
    }
  }

  for (const shot of shots) {
    const file = String(shot.file || "");
    if (!file || file.includes("/") || file.includes("\\") || file.includes("..")) {
      report.push({ file, ok: false, error: "file must be a plain name inside outDir" });
      continue;
    }
    if (Date.now() >= cutoffMs) {
      report.push({ file, ok: false, skipped: true, error: "evidence cutoff reached" });
      continue;
    }
    const started = Date.now();
    const ctx = await browser.newContext({
      viewport: shot.viewport ?? { width: 1440, height: 900 },
      deviceScaleFactor: 1,
      storageState: shot.auth === false ? undefined : storageState,
    });
    const page = await ctx.newPage();
    page.setDefaultTimeout(Math.min(perShotMs, Math.max(1000, cutoffMs - Date.now())));
    try {
      await page.goto(`${baseUrl}${shot.route || "/"}`, { waitUntil: "domcontentloaded" });
      await page.waitForTimeout(Number(shot.waitMs ?? 800));
      if (shot.tab) {
        const tab = page.locator('[role="tab"]').filter({ hasText: shot.tab }).first();
        await tab.evaluate((el) => el.click());
        await page.waitForTimeout(400);
      }
      await page.screenshot({ path: join(outDir, file), fullPage: shot.fullPage !== false });
      report.push({ file, ok: true, ms: Date.now() - started });
    } catch (err) {
      report.push({ file, ok: false, ms: Date.now() - started, error: String(err.message || err).slice(0, 300) });
    } finally {
      await ctx.close();
    }
  }
} finally {
  await browser.close();
}

const out = JSON.stringify(report, null, 2);
if (flag("--report")) writeFileSync(resolve(flag("--report")), out);
console.log(out);
process.exitCode = report.some((r) => !r.ok && !r.skipped) ? 1 : 0;
