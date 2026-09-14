// Real delivered EXE + WebView2. No fixture backend, fake API, model transport,
// native WeChat process enumeration, or saved developer configuration is used.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { spawn, execFile } from "node:child_process";
import { promisify } from "node:util";
import { once } from "node:events";
import { readFile, mkdir, readdir, writeFile } from "node:fs/promises";
import { resolve, join } from "node:path";

const run = promisify(execFile);
const root = resolve(process.env.AGENT4MARKET_ACCEPTANCE_ROOT);
const normalStart = process.env.AGENT4MARKET_ACCEPTANCE_MODE === "normal";
const replay = process.env.AGENT4MARKET_ACCEPTANCE_MODE === "replay";
const executableSha256 = createHash("sha256").update(await readFile(join(root, "Agent4Market.exe"))).digest("hex");
const fixture = JSON.parse(await readFile(process.env.AGENT4MARKET_ACCEPTANCE_FIXTURE, "utf8"));
assert.equal(resolve(fixture.root), root);
const output = join(root, `outputs/verification/${normalStart ? "native-normal-start" : "native-desktop"}`);
await mkdir(output, { recursive: true });
const { chromium } = await import(process.env.WXDECIPHER_PLAYWRIGHT_MODULE || "playwright");
const safeEnvironmentNames = new Set(["SYSTEMROOT", "WINDIR", "COMSPEC", "SYSTEMDRIVE", "USERPROFILE", "USERNAME", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA", "PATHEXT", "OS", "PROCESSOR_ARCHITECTURE"]);
const env = { ...Object.fromEntries(Object.entries(process.env).filter(([name]) => safeEnvironmentNames.has(name.toUpperCase()))),
  WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: "--remote-debugging-port=19265" };
for (const name of ["PYTHONPATH", "PYTHONHOME", "NODE_PATH", "NODE_OPTIONS", "PI_CODING_AGENT_DIR"])
  delete env[name];
// Test a neutral working directory; source/Codex tools cannot satisfy PATH.
assert.ok(process.env.LOCALAPPDATA, "Windows LOCALAPPDATA is required for installed tool discovery");
env.PATH = ["C:/Windows/System32", "C:/Windows", "C:/Windows/System32/WindowsPowerShell/v1.0",
  "C:/Program Files/Git/cmd", join(process.env.LOCALAPPDATA, "Microsoft", "WinGet", "Links")].join(";");
const app = spawn(join(root, "Agent4Market.exe"), normalStart ? [] : ["--ui-self-test"], {
  cwd: "C:/Windows", env, windowsHide: true, stdio: "ignore" });
const exited = once(app, "exit");
let browser, page;
try {
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    if (app.exitCode !== null) throw new Error(`EXE exited ${app.exitCode}`);
    try { browser = await chromium.connectOverCDP("http://127.0.0.1:19265", { timeout: 1500 }); break; }
    catch { await new Promise((done) => setTimeout(done, 300)); }
  }
  assert.ok(browser, "WebView2 debugging endpoint must become ready");
  const context = browser.contexts()[0];
  await context.route("**/*", (route) => new URL(route.request().url()).origin === "http://127.0.0.1:8765" ? route.continue() : route.abort());
  page = context.pages()[0] || await context.waitForEvent("page", { timeout: 10000 });
  const errors = [], requests = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => requests.push({ method: request.method(), path: new URL(request.url()).pathname }));
  await page.waitForURL("http://127.0.0.1:8765/");
  if (normalStart) {
    let bootstrap;
    const readyDeadline = Date.now() + 45000;
    while (Date.now() < readyDeadline) {
      bootstrap = await page.evaluate(async () => (await fetch("/api/bootstrap")).json());
      if (bootstrap.desktop_runtime.status !== "offline") break;
      await new Promise((done) => setTimeout(done, 500));
    }
    assert.equal(bootstrap.model.configured, false);
    assert.equal(bootstrap.desktop_runtime.status, "idle", JSON.stringify(bootstrap.desktop_runtime));
    assert.ok(bootstrap.desktop_runtime.heartbeat_at);
    assert.equal(bootstrap.tasks.length, 0);
    assert.equal(requests.filter(({ method }) => method === "POST").length, 0);
    await page.screenshot({ path: join(output, "webview2-home.png") });
    const result = { status: "passed", native_exe: join(root, "Agent4Market.exe"), normal_start: true,
      executable_sha256: executableSha256,
      ai_core_status: bootstrap.desktop_runtime.status, ai_core_heartbeat: true, model_configured: false,
      startup_tasks: bootstrap.tasks.length, observed_post_requests: 0, credentials_inherited: false, neutral_cwd: true };
    await writeFile(join(output, "result.json"), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result));
  } else {
  await page.locator('[data-view="tools"]').click();
  await page.locator('[data-tool="wechat"]').click();
  await page.locator("#wxdecipher-console").waitFor({ state: "visible" });
  const bootstrap = await page.evaluate(async () => (await fetch("/api/bootstrap")).json());
  assert.equal(bootstrap.wechat.http_available, true);
  assert.equal(bootstrap.wechat.message_count, replay ? 8 : 0);
  assert.equal(bootstrap.model.configured, false);
  assert.equal(bootstrap.desktop_runtime.status, "offline");
  assert.equal(await page.locator("#wxdecipher-run").isDisabled(), true);
  await page.locator("#wechat-account").fill("EXE 验收 · 合成账号");
  await page.locator("#wechat-ownership").check();
  await page.locator("#wxdecipher-self-id").fill(fixture.self_id);
  await page.locator("#wxdecipher-snapshot").check();
  await page.locator("#wxdecipher-file-input").setInputFiles(fixture.database);
  await page.locator("#wxdecipher-copy").click();
  await page.locator("#wxdecipher-status").filter({ hasText: "复制完成，尚未解析" }).waitFor();
  await page.locator("#wxdecipher-key").fill("ff".repeat(32));
  await page.locator("#wxdecipher-run").click();
  await page.locator("#wxdecipher-status.error").waitFor();
  assert.match(await page.locator("#wxdecipher-status").innerText(), /校验失败/);
  assert.equal(await page.locator("#wxdecipher-key").inputValue(), "");
  await page.locator("#wxdecipher-key").fill(fixture.key);
  await page.locator("#wxdecipher-run").click();
  await page.locator("#wxdecipher-status").filter({ hasText: replay ? "未重复保存或建立重复消息" : "导入 3 条新消息" }).waitFor();
  const threeMessageConversation = page.locator("#wechat-conversations .wechat-conversation-card").filter({ hasText: "保留 3 条" });
  await threeMessageConversation.click();
  await page.locator("#wechat-messages").filter({ hasText: "报价计划" }).waitFor();
  await threeMessageConversation.locator('input[type="checkbox"]').check();
  await page.locator("#wechat-export-format").selectOption("jsonl");
  await page.locator("#wechat-export-selected").click();
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "生成本地文件", exact: true }).click()]);
  const exportPath = join(output, "synthetic-export.jsonl");
  await download.saveAs(exportPath);
  assert.equal((await readFile(exportPath, "utf8")).trim().split("\n").length, 3);
  await page.locator("#wxdecipher-clear-copy").click();
  await page.getByRole("button", { name: "清除副本", exact: true }).click();
  await page.locator("#wxdecipher-status").filter({ hasText: "已清除本批副本" }).waitFor();
  await page.locator("#wxdecipher-file-input").setInputFiles([fixture.wal_database, fixture.wal]);
  await page.locator("#wxdecipher-copy").click();
  await page.locator("#wxdecipher-status").filter({ hasText: "复制完成，尚未解析" }).waitFor();
  await page.locator("#wxdecipher-wal-confirm").check();
  await page.locator("#wxdecipher-key").fill(fixture.key);
  await page.locator("#wxdecipher-run").click();
  await page.locator("#wxdecipher-status").filter({ hasText: "WAL 恢复" }).waitFor();
  await page.waitForFunction(() => document.getElementById("wxdecipher-console").getAttribute("aria-busy") === "false");
  await page.locator("#wxdecipher-media-console > summary").click();
  await page.locator("#wxdecipher-image-key").fill("0123456789abcdef");
  await page.locator("#wxdecipher-media-input").setInputFiles(fixture.media);
  await page.locator("#wxdecipher-media-status").filter({ hasText: "已恢复 1/1" }).waitFor();
  await page.waitForFunction(() => document.querySelector("#wxdecipher-media-results img")?.naturalWidth === 24);
  const [mediaDownload] = await Promise.all([page.waitForEvent("download"), page.locator("#wxdecipher-media-results a").click()]);
  const mediaPath = join(output, "synthetic-restored.png");
  await mediaDownload.saveAs(mediaPath);
  assert.deepEqual(await readFile(mediaPath), await readFile(fixture.media_plain));
  assert.equal(await page.locator("#wechat-model-sharing").isChecked(), false);
  await page.locator("#wxdecipher-clear-copy").click();
  await page.getByRole("button", { name: "清除副本", exact: true }).click();
  await page.locator("#wxdecipher-status").filter({ hasText: "已清除本批副本" }).waitFor();
  assert.equal((await readdir(join(root, "data/wechat/decipher"))).length, 0);
  assert.equal(requests.filter(({ method, path }) => method === "POST" && /task|scopes|model|sales/.test(path)).length, 0);
  assert.equal(requests.filter(({ path }) => path.endsWith("/processes")).length, 0);
  await page.waitForFunction(() => !document.getElementById("notice").textContent);
  await page.screenshot({ path: join(output, "webview2-wechat.png"), fullPage: true });
  assert.deepEqual(errors, []);
  const result = { status: "passed", native_exe: join(root, "Agent4Market.exe"), webview2: true,
    executable_sha256: executableSha256, replay_deduplicated: replay, credentials_inherited: false,
    neutral_cwd: true, source_and_codex_removed_from_path: true, initial_messages: bootstrap.wechat.message_count, exported_messages: 3,
    wrong_key_rejected: true, wal_restored: true, media_download: true, model_requests: 0, wechat_process_requests: 0 };
  await writeFile(join(output, "result.json"), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
  }
} catch (error) {
  if (page) await page.screenshot({ path: join(output, "failure.png"), fullPage: true }).catch(() => {});
  throw error;
} finally {
  if (app.exitCode === null) {
    await run("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe", ["-NoProfile", "-Command", `$owned = Get-Process -Id ${app.pid} -ErrorAction SilentlyContinue; if ($owned) { $owned.CloseMainWindow() }`], { windowsHide: true }).catch(() => {});
    await Promise.race([exited, new Promise((done) => setTimeout(done, 10000))]);
    if (app.exitCode === null) await run("C:/Windows/System32/taskkill.exe", ["/PID", String(app.pid), "/T", "/F"], { windowsHide: true }).catch(() => {});
  }
  if (browser) await browser.close().catch(() => {});
}
