// Optional real-browser smoke test. Requires Playwright plus an installed browser.
// The production HTTP/UI run against synthetic data in a fresh temporary root.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir, readFile, readdir } from "node:fs/promises";
import { createInterface } from "node:readline";
import { join, resolve } from "node:path";
import { once } from "node:events";

const { chromium } = await import(process.env.WXDECIPHER_PLAYWRIGHT_MODULE || "playwright");
const output = resolve(process.env.WXDECIPHER_BROWSER_OUTPUT || `outputs/verification/wxdecipher-browser-${Date.now()}`);
await mkdir(output, { recursive: true });
const child = spawn(process.env.WXDECIPHER_PYTHON || "python", ["-u", "tests/wxdecipher_browser_fixture.py"],
  { cwd: process.cwd(), windowsHide: true, stdio: ["pipe", "pipe", "pipe"] });
const exit = once(child, "exit");
let diagnostics = "";
child.stderr.on("data", (data) => { diagnostics += data.toString(); });
let browser;
let page;
try {
  const fixture = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`Fixture startup timed out: ${diagnostics}`)), 20000);
    const lines = createInterface({ input: child.stdout });
    lines.once("line", (line) => { clearTimeout(timer); lines.close(); resolve(JSON.parse(line)); });
    child.once("exit", (code) => { clearTimeout(timer); reject(new Error(`Fixture exited ${code}: ${diagnostics}`)); });
  });
  browser = await chromium.launch({ headless: true, ...(process.env.WXDECIPHER_BROWSER_CHANNEL ? { channel: process.env.WXDECIPHER_BROWSER_CHANNEL } : {}) });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1080 }, acceptDownloads: true });
  await context.route("**/*", (route) => new URL(route.request().url()).origin === fixture.url ? route.continue() : route.abort());
  page = await context.newPage();
  const errors = [];
  const requests = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => requests.push({ method: request.method(), path: new URL(request.url()).pathname }));
  await page.goto(fixture.url);
  await page.locator('[data-view="tools"]').click();
  await page.locator('[data-tool="wechat"]').click();
  await page.locator("#wxdecipher-console").waitFor({ state: "visible" });
  assert.equal(await page.locator("#wxdecipher-console .wxdecipher-fields").evaluate((element) => getComputedStyle(element).display), "grid");
  assert.equal(await page.locator("#wxdecipher-run").isDisabled(), true);
  await page.locator("#wechat-account").fill("隔离验证 · 合成账号");
  await page.locator("#wechat-ownership").check();
  await page.locator("#wxdecipher-self-id").fill(fixture.self_id);
  await page.locator("#wxdecipher-snapshot").check();
  await page.locator("#wxdecipher-file-input").setInputFiles(fixture.database);
  await page.locator("#wxdecipher-key").fill("ff".repeat(32));
  await page.locator("#wxdecipher-run").click();
  await page.locator("#wxdecipher-status.error").waitFor();
  assert.match(await page.locator("#wxdecipher-status").innerText(), /校验失败/);
  assert.equal(await page.locator("#wxdecipher-key").inputValue(), "");
  assert.equal((await readdir(join(fixture.root, "data/wechat/decipher"))).length, 0);
  await page.locator("#wechat-tool-panel").screenshot({ path: join(output, "wrong-key.png") });
  await page.locator("#wxdecipher-key").fill(fixture.key);
  await page.locator("#wxdecipher-run").click();
  await page.locator("#wxdecipher-status").filter({ hasText: "导入 3 条新消息" }).waitFor();
  await page.locator("#wechat-conversations .wechat-conversation-card").waitFor();
  assert.match(await page.locator("#wechat-conversations").innerText(), new RegExp(fixture.self_id));
  await page.locator("#wechat-conversations .wechat-conversation-card").click();
  await page.locator("#wechat-messages").filter({ hasText: "报价计划" }).waitFor();
  await page.locator('#wechat-conversations input[type="checkbox"]').check();
  await page.locator("#wechat-export-format").selectOption("jsonl");
  await page.locator("#wechat-export-selected").click();
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: "生成本地文件", exact: true }).click(),
  ]);
  const destination = join(output, "synthetic-export.jsonl");
  await download.saveAs(destination);
  const records = (await readFile(destination, "utf8")).trim().split("\n").map((line) => JSON.parse(line));
  assert.equal(records.length, 3);
  assert.ok(records.every((row) => row.account_id === fixture.self_id));
  assert.equal(await page.locator("#wechat-model-sharing").isChecked(), false);
  assert.equal((await readdir(join(fixture.root, "data/wechat/decipher"))).length, 0);
  assert.equal(requests.filter((request) => request.method === "POST" && /task|scopes|model|sales/.test(request.path)).length, 0);
  assert.equal(requests.filter((request) => request.path.endsWith("/processes")).length, 0, "No process list at startup or manual import");
  await page.locator("#wxdecipher-file-input").setInputFiles([fixture.wal_database, fixture.wal]);
  await page.locator("#wxdecipher-wal-confirm").check();
  await page.locator("#wxdecipher-capture-confirm").check();
  await page.locator("#wxdecipher-process-refresh").click();
  await page.locator("#wxdecipher-status").filter({ hasText: "未列出或读取真实微信" }).waitFor();
  assert.equal(await page.locator("#wxdecipher-process").inputValue(), "");
  await page.locator("#wxdecipher-process").selectOption({ label: "Weixin.exe（合成测试进程） · PID 424242" });
  await page.locator("#wxdecipher-run").click();
  await page.locator("#wxdecipher-status").filter({ hasText: "WAL 恢复" }).waitFor();
  await page.waitForFunction(() => document.getElementById("wxdecipher-console").getAttribute("aria-busy") === "false");
  assert.match(await page.locator("#wxdecipher-status").innerText(), /自动匹配 1/);
  assert.doesNotMatch(await page.locator("#wxdecipher-status").innerText(), /未合并 WAL|本版不恢复/);
  assert.equal(await page.locator("#wxdecipher-capture-confirm").isChecked(), false);
  assert.equal(await page.locator("#wechat-model-sharing").isChecked(), false);
  assert.equal((await readdir(join(fixture.root, "data/wechat/decipher"))).length, 0);
  await page.waitForFunction(() => !document.getElementById("notice").textContent);
  await page.locator("#wxdecipher-console").screenshot({ path: join(output, "wal-capture-success.png") });
  await page.locator("#wxdecipher-media-console > summary").click();
  await page.locator("#wxdecipher-image-key").fill("0123456789abcdef");
  await page.locator("#wxdecipher-media-input").setInputFiles(fixture.media);
  await page.locator("#wxdecipher-media-status").filter({ hasText: "已恢复 1/1" }).waitFor();
  assert.equal(await page.locator("#wxdecipher-image-key").inputValue(), "");
  await page.locator("#wxdecipher-media-results img").waitFor();
  await page.waitForFunction(() => document.querySelector("#wxdecipher-media-results img")?.naturalWidth === 24);
  const [mediaDownload] = await Promise.all([page.waitForEvent("download"), page.locator("#wxdecipher-media-results a").click()]);
  const mediaDestination = join(output, "synthetic-restored.png"); await mediaDownload.saveAs(mediaDestination);
  assert.deepEqual(await readFile(mediaDestination), await readFile(fixture.media_plain));
  await page.locator("#wxdecipher-media-console").screenshot({ path: join(output, "media-restored.png") });
  assert.equal(requests.filter((request) => request.method === "POST" && /task|scopes|model|sales/.test(request.path)).length, 0);
  await page.waitForFunction(() => !document.getElementById("notice").textContent);
  await page.locator("#wechat-tool-panel").screenshot({ path: join(output, "desktop-import.png") });
  await page.locator("#wechat-browser-panel").screenshot({ path: join(output, "desktop-preview.png") });
  await page.setViewportSize({ width: 800, height: 1000 });
  await page.locator("#wechat-tool-panel").screenshot({ path: join(output, "narrow-import.png") });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 2), false);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ status: "passed", initial_export_messages: records.length, wal_and_capture: true, media_download: true, model_requests: 0, screenshots: output }));
} catch (error) {
  if (page) await page.screenshot({ path: join(output, "failure.png"), fullPage: true }).catch(() => {});
  console.error(diagnostics);
  throw error;
} finally {
  if (browser) await browser.close();
  child.stdin.end("quit\n");
  await exit;
}
