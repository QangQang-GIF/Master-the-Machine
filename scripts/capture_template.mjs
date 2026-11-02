/**
 * Standalone Reliable Playwright UI Evidence Capture Template
 * 
 * Usage:
 *   node capture_template.mjs <evidence_dir> <route> <output_filename> [tab_name]
 * 
 * Example:
 *   node capture_template.mjs ../answers/BUG-02/evidence /accounting tc-01-vnd-invoice.png Invoices
 */

import { mkdirSync } from "node:fs";
import { resolve, join } from "node:path";
import { chromium } from "playwright";

const [evidenceDirArg, routeArg, filenameArg, tabArg] = process.argv.slice(2);

if (!evidenceDirArg || !filenameArg) {
  console.error("Usage: node capture_template.mjs <evidence_dir> <route> <output_filename> [tab_name]");
  process.exit(1);
}

const evidenceDir = resolve(evidenceDirArg);
mkdirSync(evidenceDir, { recursive: true });

const targetFile = join(evidenceDir, filenameArg);
const route = routeArg || "/accounting";
const baseUrl = `http://127.0.0.1:${process.env.PORT || 3000}`;

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1600, height: 1000 },
  deviceScaleFactor: 1,
});
const page = await context.newPage();
page.setDefaultTimeout(5000);

try {
  // 1. Authenticate
  await page.goto(`${baseUrl}/login`, { waitUntil: "domcontentloaded" });
  await page.locator("#email").fill("morpheusadam95");
  await page.locator("#password").fill("change-me-please");
  await page.getByRole("button", { name: "Sign in" }).click({ force: true, timeout: 5000 });
  await page.waitForURL(/dashboard/, { timeout: 10000 });

  // 2. Navigate to destination
  await page.goto(`${baseUrl}${route}`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1000); // Wait for React hydration

  // 3. Switch tab if requested
  if (tabArg) {
    const tabLocator = page.locator('[role="tab"]').filter({ hasText: tabArg }).first();
    await tabLocator.evaluate((el) => el.click());
    await page.waitForFunction(
      (name) => document.querySelector('[role="tab"][data-state="active"]')?.textContent?.trim().includes(name),
      tabArg,
      { timeout: 5000 }
    ).catch(() => {});
    await page.waitForTimeout(500);
  }

  // 4. Capture screenshot
  await page.screenshot({ path: targetFile, fullPage: true });
  console.log(`Successfully captured evidence: ${targetFile}`);
} catch (err) {
  console.error(`UI capture error: ${err.message}`);
  // Diagnostic fallback
  await page.screenshot({ path: join(evidenceDir, "diagnostic-ui.png"), fullPage: true }).catch(() => {});
  process.exitCode = 1;
} finally {
  await browser.close();
}
