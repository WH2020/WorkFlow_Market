// Real browser + production UI/HTTP, exclusively synthetic model metadata/config.
// Optional: requires an installed browser and Playwright (no download/install).
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir, readFile } from "node:fs/promises";
import { createInterface } from "node:readline";
import { once } from "node:events";
import { join, resolve } from "node:path";

const { chromium } = await import(process.env.A4M_BROWSER_PLAYWRIGHT || "playwright");
const output = resolve(process.env.A4M_BROWSER_OUTPUT || `outputs/verification/cli-catalog-${Date.now()}`);
await mkdir(output, { recursive: true });
const child = spawn(process.env.A4M_BROWSER_PYTHON || "python", ["-B", "-u", "tests/cli_settings_browser_fixture.py"],
  { cwd: process.cwd(), windowsHide: true, stdio: ["pipe", "pipe", "pipe"] });
const exited = once(child, "exit");
let diagnostics = ""; child.stderr.on("data", (chunk) => { diagnostics = (diagnostics + chunk).slice(-8000); });
let browser;
try {
  const fixture = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`Synthetic UI startup timeout: ${diagnostics}`)), 20000);
    const lines = createInterface({ input: child.stdout });
    lines.once("line", (line) => { clearTimeout(timer); lines.close(); resolve(JSON.parse(line)); });
    child.once("exit", (code) => { clearTimeout(timer); reject(new Error(`Synthetic UI exited ${code}: ${diagnostics}`)); });
  });
  browser = await chromium.launch({ headless: true, channel: process.env.A4M_BROWSER_CHANNEL || "msedge" });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1080 } });
  await context.route("**/*", (route) => new URL(route.request().url()).origin === fixture.url ? route.continue() : route.abort());
  const page = await context.newPage(); const errors = []; const saved = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => { if (new URL(request.url()).pathname === "/api/model-provider-choice") saved.push(request.postDataJSON()); });
  await page.goto(fixture.url);
  await page.locator("#provider-current").filter({ hasText: "Pi 模型执行核心" }).waitFor({ state: "attached" });
  await page.locator('button.sidebar-settings[data-view="settings"]').click();
  await page.locator('input[name="provider-type"][value="codex-cli"]').check();
  await page.locator("#codex-cli-catalog-status").filter({ hasText: "已读取 2 个" }).waitFor();
  assert.equal(await page.locator("#codex-cli-model").inputValue(), "synthetic-sol");
  assert.equal(await page.locator("#codex-cli-thinking").inputValue(), "low");
  assert.equal(await page.locator('#codex-cli-thinking option[value="ultra"]').getAttribute("disabled"), "", await page.locator("#codex-cli-thinking").innerHTML());
  assert.match(await page.locator("#codex-cli-catalog-status").innerText(), /未验证账号权限/u);
  await page.locator("#codex-cli-model").selectOption("synthetic-luna");
  assert.equal(await page.locator("#codex-cli-thinking").inputValue(), "medium");
  await page.locator("#codex-cli-thinking").selectOption("max");
  await page.locator("#model-provider-panel").screenshot({ path: join(output, "codex-selector.png") });
  await page.locator("#save-provider-settings").click();
  await page.locator("#codex-cli-config-status").filter({ hasText: "重启应用后生效" }).waitFor();
  assert.equal(saved.length, 1); assert.equal(saved[0].selected_model, "synthetic-luna"); assert.equal(saved[0].selected_thinking_level, "max");
  assert.equal(typeof saved[0].discovery_id, "string"); assert.equal("cli_reasoning" in saved[0], false);
  assert.equal(await page.locator("#task-thinking").inputValue(), "");
  assert.match(await page.locator("#task-thinking").innerText(), /模型默认：max/u);
  assert.equal(await page.locator('#task-thinking option[value="off"]').count(), 0);
  await page.reload();
  await page.locator('button.sidebar-settings[data-view="settings"]').click();
  await page.locator("#codex-cli-catalog-status").filter({ hasText: "已读取 2 个" }).waitFor();
  assert.equal(await page.locator("#codex-cli-model").inputValue(), "synthetic-luna");
  assert.equal(await page.locator("#codex-cli-thinking").inputValue(), "max");
  await page.locator("#model-provider-panel").screenshot({ path: join(output, "codex-saved-reload.png") });
  const settingsPath = fixture.settings_path;
  const settingsBefore = await readFile(settingsPath);
  await page.route("**/api/coding-assistants/models", (route) => route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "合成目录超时" }) }));
  await page.locator("#refresh-codex-models").click();
  await page.locator("#codex-cli-catalog-status").filter({ hasText: "合成目录超时" }).waitFor();
  assert.equal(await page.locator("#save-provider-settings").isDisabled(), true);
  assert.equal(saved.length, 1); assert.equal(await page.locator("#codex-cli-model").inputValue(), "synthetic-luna");
  assert.deepEqual(await readFile(settingsPath), settingsBefore);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ status: "passed", checks: ["auto-discovery", "model-effort-link", "ultra-disabled", "exact-save", "task-default", "reload-persistence", "failure-preserves-config"], screenshots: output }));
} finally {
  if (browser) await browser.close();
  child.stdin.end("\n");
  const killTimer = setTimeout(() => child.kill(), 10000);
  try { await exited; } finally { clearTimeout(killTimer); }
}
