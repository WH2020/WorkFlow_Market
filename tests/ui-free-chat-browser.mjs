// Isolated headless fallback when the in-app browser connection is incompatible.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { createInterface } from "node:readline";
import { once } from "node:events";
import { join, resolve } from "node:path";

const { chromium } = await import(process.env.A4M_BROWSER_PLAYWRIGHT || "playwright");
const output = resolve(process.env.A4M_BROWSER_OUTPUT || `outputs/verification/free-chat-${Date.now()}`);
await mkdir(output, { recursive: true });
const child = spawn(process.env.A4M_BROWSER_PYTHON || "python", ["-I", "-B", "-u", "tests/free_chat_browser_fixture.py"],
  { cwd: process.cwd(), windowsHide: true, stdio: ["pipe", "pipe", "pipe"] });
const exited = once(child, "exit");
let diagnostics = "", browser, context;
child.stderr.on("data", (chunk) => { diagnostics = (diagnostics + chunk).slice(-8000); });
const checks = [];
try {
  const fixture = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`Fixture timeout: ${diagnostics}`)), 20000);
    createInterface({ input: child.stdout }).once("line", (line) => { clearTimeout(timer); resolve(JSON.parse(line)); });
    child.once("exit", (code) => { clearTimeout(timer); reject(new Error(`Fixture exit ${code}: ${diagnostics}`)); });
  });
  browser = await chromium.launch({ headless: true, channel: process.env.A4M_BROWSER_CHANNEL || "msedge" });
  context = await browser.newContext({ viewport: { width: 1440, height: 1080 } });
  await context.route("**/*", (route) => new URL(route.request().url()).origin === fixture.url ? route.continue() : route.abort());
  await context.addInitScript(() => localStorage.setItem("agent4market-auto-updates", "false"));
  const page = await context.newPage(), errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const stats = async () => (await context.request.get(`${fixture.url}/fixture/stats`)).json();
  await page.goto(fixture.url);
  await page.locator('button.nav-item[data-view="chat"]').click();
  await page.locator("#chat-status").filter({ hasText: "准备好了" }).waitFor();
  assert.equal(await page.locator(".task-runtime-picker").isVisible(), false);
  assert.equal(await page.locator("#chat-send").isDisabled(), true);
  await page.locator("#chat-input").fill("合成名字：小松");
  await page.locator("#chat-input").press("Shift+Enter");
  assert.equal((await stats()).requests.length, 0);
  await page.locator("#chat-input").press("Enter");
  await page.locator("#chat-status").filter({ hasText: "回复完成" }).waitFor();
  assert.equal(await page.locator("#chat-model").isDisabled(), true);
  await page.locator("#chat-input").fill("我刚才说了什么？");
  await page.locator("#chat-send").click();
  await page.locator("#chat-status").filter({ hasText: "回复完成" }).waitFor();
  assert.match(await page.locator(".free-chat-message.assistant .free-chat-text").last().innerText(), /小松/u);
  assert.equal((await stats()).requests.length, 2);
  await page.locator('button.nav-item[data-view="home"]').click();
  assert.equal(await page.locator(".task-runtime-picker").isVisible(), true);
  await page.locator('button.nav-item[data-view="chat"]').click();
  assert.equal(await page.locator(".free-chat-message").count(), 4);
  await page.screenshot({ path: join(output, "free-chat-desktop.png"), fullPage: true });
  checks.push("entry, keyboard, multi-turn memory, fixed recipient, navigation retention");

  await page.locator("#chat-new").click();
  await page.locator("#chat-status").filter({ hasText: "新对话已准备好" }).waitFor();
  assert.equal(await page.locator(".free-chat-message").count(), 0);
  assert.equal(await page.locator("#chat-model").isDisabled(), false);
  await page.locator("#chat-input").fill("fixture:slow");
  await page.locator("#chat-send").click();
  await page.locator(".free-chat-message.assistant .free-chat-text").filter({ hasText: "测试成功" }).waitFor();
  await page.locator("#chat-stop").click();
  await page.locator("#chat-status").filter({ hasText: "已停止生成" }).waitFor();
  await page.locator("#chat-stop").waitFor({ state: "hidden" });
  await page.locator("#chat-input").fill("合成重试");
  await page.locator("#chat-send").click();
  await page.locator("#chat-status").filter({ hasText: "回复完成" }).waitFor();
  assert.doesNotMatch(JSON.stringify((await stats()).requests.at(-1).body), /fixture:slow/u);
  checks.push("stop, transport close, cancelled history excluded, retry");

  await page.locator("#chat-new").click();
  await page.locator("#chat-status").filter({ hasText: "新对话已准备好" }).waitFor();
  await page.locator("#chat-input").fill("fixture:auth");
  await page.locator("#chat-send").click();
  await page.locator("#chat-status").filter({ hasText: "认证失败" }).waitFor();
  assert.equal(await page.locator("#chat-input").inputValue(), "fixture:auth");
  await page.locator("#chat-input").fill("fixture:html");
  await page.locator("#chat-send").click();
  await page.locator("#chat-status").filter({ hasText: "回复完成" }).waitFor();
  assert.equal(await page.locator("#chat-conversation img, #chat-conversation script").count(), 0);
  assert.match(await page.locator(".free-chat-message.assistant .free-chat-text").last().innerText(), /<img/u);
  assert.doesNotMatch(JSON.stringify((await stats()).requests.at(-1).body), /fixture:auth/u);
  checks.push("auth error redaction, draft restoration, failed history excluded, HTML is text");

  await page.setViewportSize({ width: 980, height: 860 });
  const composer = await page.locator("#chat-form").boundingBox();
  assert.ok(composer && composer.x >= 0 && composer.x + composer.width <= 980);
  const settings = await page.locator(".sidebar-settings").boundingBox();
  assert.ok(settings && settings.y + settings.height <= 860, "settings stay accessible after adding a navigation entry");
  await page.screenshot({ path: join(output, "free-chat-narrow.png"), fullPage: true });
  await page.reload();
  await page.locator('button.nav-item[data-view="chat"]').click();
  await page.locator("#chat-status").filter({ hasText: "准备好了" }).waitFor();
  assert.equal(await page.locator(".free-chat-message").count(), 0);
  const finalStats = await stats();
  assert.equal(finalStats.active, 0);
  assert.ok(finalStats.requests.every((request) => !request.body.tools?.length));
  assert.deepEqual(errors, []);
  checks.push("narrow layout, refresh clears dialogue, no tools, no page errors");
  await writeFile(join(output, "result.json"), JSON.stringify({ checks, modelRequests: finalStats.requests.length, externalRequests: 0 }, null, 2));
  console.log(JSON.stringify({ checks, output }));
} finally {
  if (context) await context.close();
  if (browser) await browser.close();
  child.stdin.end("\n");
  const timer = setTimeout(() => child.kill(), 10000);
  try { await exited; } finally { clearTimeout(timer); }
  if (diagnostics) console.error(diagnostics);
}
