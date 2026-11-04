/**
 * Fast Headless Playwright UI Evidence Capture Utility
 * 
 * Features:
 * 1. Persistent auth state caching (`D:\Temp\auth-state.json`) - skips /login after first capture.
 * 2. Ultra-lightweight Chromium flags (no GPU, no sandbox, minimal memory).
 * 3. Fast DOMContentLoaded wait with minimal timeout.
 * 4. Reliable tab selection and full-page capture.
 * 5. Robust multi-path Playwright resolution (works from any working directory).
 * 
 * Usage:
 *   node runner/fast_capture.mjs <evidence_dir> <route> <output_filename> [tab_name]
 * 
 * Example:
 *   node runner/fast_capture.mjs answers/BUG-02/evidence /accounting tc-01-vnd-invoice.png "Invoices"
 */

import { existsSync, mkdirSync } from "node:fs";
import { resolve, join } from "node:path";
import { createRequire } from "node:module";

// Robust Playwright module resolution
let chromium;
try {
  const pw = await import("playwright");
  chromium = pw.chromium;
} catch {
  // Fallback: look in src/node_modules or current directory
  const candidates = [
    resolve(process.cwd(), "src/package.json"),
    resolve(process.cwd(), "package.json"),
    resolve(process.cwd(), "../src/package.json"),
  ];
  let loaded = false;
  for (const pkgPath of candidates) {
    if (existsSync(pkgPath)) {
      try {
        const req = createRequire(pkgPath);
        const pw = req("playwright");
        chromium = pw.chromium;
        loaded = true;
        break;
      } catch {}
    }
  }
  if (!loaded) {
    console.error("[FastCapture] Could not resolve 'playwright' module.");
    process.exit(1);
  }
}

const [evidenceDirArg, routeArg, filenameArg, tabArg] = process.argv.slice(2);

if (!evidenceDirArg || !filenameArg) {
  console.error("Usage: node runner/fast_capture.mjs <evidence_dir> <route> <output_filename> [tab_name]");
  process.exit(1);
}

const evidenceDir = resolve(evidenceDirArg);
mkdirSync(evidenceDir, { recursive: true });

const targetFile = join(evidenceDir, filenameArg);
const route = routeArg || "/dashboard";
const port = process.env.PORT || 3000;
const baseUrl = `http://127.0.0.1:${port}`;
const authStateFile = resolve("D:/Temp/auth-state.json");

const browser = await chromium.launch({
  headless: true,
  args: [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--no-first-run",
    "--no-zygote",
  ],
});

let context;
const hasSavedAuth = existsSync(authStateFile);

if (hasSavedAuth) {
  try {
    context = await browser.newContext({
      storageState: authStateFile,
      viewport: { width: 1440, height: 900 },
      deviceScaleFactor: 1,
    });
  } catch {
    context = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      deviceScaleFactor: 1,
    });
  }
} else {
  context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
  });
}

const page = await context.newPage();
page.setDefaultTimeout(8000);

try {
  // 1. Authenticate if not already authenticated
  let authenticated = false;
  if (hasSavedAuth) {
    try {
      const probe = await page.goto(`${baseUrl}/dashboard`, { waitUntil: "domcontentloaded", timeout: 4000 });
      if (probe && !probe.url().includes("/login")) {
        authenticated = true;
      }
    } catch {
      authenticated = false;
    }
  }

  if (!authenticated) {
    await page.goto(`${baseUrl}/login`, { waitUntil: "domcontentloaded" });
    await page.locator("#email").fill("morpheusadam95");
    await page.locator("#password").fill("change-me-please");
    await page.getByRole("button", { name: "Sign in" }).click({ force: true, timeout: 5000 });
    await page.waitForURL(/dashboard/, { timeout: 8000 });
    try {
      await context.storageState({ path: authStateFile });
    } catch {}
  }

  // 2. Navigate to requested route
  if (route !== "/dashboard") {
    await page.goto(`${baseUrl}${route}`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(600); // Wait for React hydration
  }

  // 3. Switch tab if requested
  if (tabArg) {
    const tabLocator = page.locator('[role="tab"]').filter({ hasText: tabArg }).first();
    await tabLocator.evaluate((el) => el.click()).catch(() => {});
    await page.waitForFunction(
      (name) => document.querySelector('[role="tab"][data-state="active"]')?.textContent?.trim().includes(name),
      tabArg,
      { timeout: 3000 }
    ).catch(() => {});
    await page.waitForTimeout(400);
  }

  // 4. Capture screenshot
  await page.screenshot({ path: targetFile, fullPage: true });
  console.log(`[FastCapture] Successfully captured: ${targetFile}`);
} catch (err) {
  console.error(`[FastCapture] Error: ${err.message}`);
  await page.screenshot({ path: join(evidenceDir, "diagnostic-ui.png"), fullPage: true }).catch(() => {});
  process.exitCode = 1;
} finally {
  await browser.close();
}
