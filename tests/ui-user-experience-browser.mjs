// Production UI/HTTP with disposable dossiers and a local synthetic model only.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { once } from "node:events";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve, join } from "node:path";

const { chromium } = await import(process.env.A4M_BROWSER_PLAYWRIGHT || "playwright");
const output = resolve(process.env.A4M_BROWSER_OUTPUT || `outputs/verification/user-experience-${Date.now()}`);
await mkdir(output, { recursive: true });
const child = spawn(process.env.A4M_BROWSER_PYTHON || "python", ["-I", "-B", "-u", "tests/free_chat_browser_fixture.py"],
  { windowsHide: true, stdio: ["pipe", "pipe", "pipe"] });
const exited = once(child, "exit"); let diagnostics = "", browser, context, page;
child.stderr.on("data", chunk => { diagnostics = (diagnostics + chunk).slice(-6000); });
try {
  const fixture = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(Error("Fixture timeout: " + diagnostics)), 20000);
    createInterface({ input: child.stdout }).once("line", line => { clearTimeout(timer); resolve(JSON.parse(line)); });
    child.once("exit", () => { clearTimeout(timer); reject(Error(diagnostics)); });
  });
  browser = await chromium.launch({ headless: true, channel: process.env.A4M_BROWSER_CHANNEL || "msedge" });
  context = await browser.newContext({ viewport: { width: 1366, height: 768 } });
  await context.route("**/*", route => new URL(route.request().url()).origin === fixture.url ? route.continue() : route.abort());
  await context.addInitScript(() => localStorage.setItem("agent4market-auto-updates", "false"));
  page = await context.newPage(); const errors = [], checks = [], layouts = [];
  page.setDefaultTimeout(10000);
  page.on("pageerror", error => errors.push(error.message));
  let taskRequests = 0;
  await context.route("**/api/task-requests", route => { taskRequests++; return route.abort(); });
  const stats = async () => (await context.request.get(fixture.url + "/fixture/stats")).json();
  const send = async text => { await page.locator("#chat-input").fill(text); await page.locator("#chat-send").click(); };
  const done = () => page.locator("#chat-status").filter({ hasText: "回复完成" }).waitFor({ timeout: 30000 });
  const fresh = async () => {
    await page.locator('#sidebar nav [data-view="chat"]').click();
    await page.locator("#chat-new").click();
    await page.locator("#chat-status").filter({ hasText: "新对话已准备好" }).waitFor();
  };
  await page.goto(fixture.url);
  await page.locator("#chat-status").filter({ hasText: "准备好了" }).waitFor();
  for (const [width, height] of [[1366, 768], [1280, 720], [390, 844]]) {
    await page.setViewportSize({ width, height });
    const layout = await page.evaluate(() => ({ width: innerWidth, height: innerHeight,
      overflow: document.documentElement.scrollWidth > innerWidth,
      controls: ["chat-input", "chat-send"].map(id => {
        const rect = document.getElementById(id).getBoundingClientRect();
        return { id, top: rect.top, bottom: rect.bottom, visible: rect.top >= 0 && rect.bottom <= innerHeight };
      }) }));
    assert.equal(layout.overflow, false);
    for (const control of layout.controls) assert.equal(control.visible, true, JSON.stringify(layout));
    layouts.push(layout); await page.screenshot({ path: join(output, `first-screen-${width}x${height}.png`) });
  }
  checks.push("input and send visible without scrolling at 1366x768, 1280x720 and 390x844");
  await page.setViewportSize({ width: 1366, height: 768 });
  await page.getByRole("button", { name: "复盘客户进展", exact: true }).click();
  assert.equal(await page.locator("#chat-input").inputValue(), "复盘本周客户进展");
  assert.equal((await stats()).requests.length, 0);
  await page.locator('#sidebar nav [data-view="knowledge"]').click();
  const customer = page.locator(".hub-entity-row").filter({ hasText: "星河制造（合成）" });
  await customer.getByRole("button", { name: "查看档案", exact: true }).click();
  await page.getByRole("button", { name: "围绕此对象发起对话", exact: true }).click();
  await send("这个客户下一步该怎么推进？"); await done();
  const request = JSON.stringify((await stats()).requests.at(-1).body);
  assert.match(request, /星河制造|三条产线/);
  assert.match(request, /本地客户档案/);
  assert.doesNotMatch(request, /OTHER_CUSTOMER_CONTEXT_CANARY|苏州产业园|杭州产业园/);
  await page.getByRole("region", { name: "本轮客户资料", exact: true }).waitFor();
  const note = page.locator(".chat-project-note").last(); await note.locator("summary").click();
  await note.locator("textarea").fill("已核对：下周确认三条产线的试点范围（合成）。");
  await note.getByRole("button", { name: "确认保存到客户", exact: true }).click();
  await note.locator(".chat-assist-status").filter({ hasText: "已保存到客户档案" }).waitFor();
  assert.match(await note.innerText(), /尚未纳入正式销售台账/);
  await send("这个客户最近保存了什么？"); await done();
  assert.match(JSON.stringify((await stats()).requests.at(-1).body), /下周确认三条产线/);
  await page.screenshot({ path: join(output, "customer-context.png") });
  checks.push("explicit customer context reaches model; unrelated projects/customers excluded; confirmed note recalled with reporting boundary");
  const beforeClear = (await stats()).requests.length;
  await page.locator("#hub-auto-project").uncheck();
  await send("你好");
  await page.getByRole("heading", { name: "切换档案并开始新对话", exact: true }).waitFor();
  await page.locator(".app-confirm-dialog").getByRole("button", { name: "取消", exact: true }).click();
  await page.locator("#chat-status").filter({ hasText: "已取消切换" }).waitFor();
  assert.equal((await stats()).requests.length, beforeClear);
  await send("你好");
  await page.locator(".app-confirm-dialog").getByRole("button", { name: "开始新对话", exact: true }).click();
  await done();
  assert.doesNotMatch(JSON.stringify((await stats()).requests.at(-1).body), /星河制造|三条产线|本地客户档案/);
  await page.locator("#hub-auto-project").check();
  checks.push("disabling customer context requires scope reset; cancellation sends nothing; new conversation excludes old customer facts");
  await fresh();
  await send("先不要生成 PPT，帮我复盘本周客户进展");
  await page.getByRole("heading", { name: "确认本次工作流", exact: true }).waitFor();
  assert.equal(await page.locator("#hub-flow-type").inputValue(), "sales-review");
  await fresh(); await send("帮我把这份方案整理成 8 页 PPT");
  await page.locator("#presentation-task-form").waitFor();
  assert.equal(await page.locator("#ppt-pages").inputValue(), "8");
  await fresh(); await send("解读这份招标文件");
  await page.locator("#bid-chat-brief").waitFor();
  assert.match(await page.locator("#bid-chat-brief").innerText(), /解读这份招标文件/);
  await page.getByRole("button", { name: "确认所选投标项目并开始解读", exact: true }).click();
  await page.locator("#notice").filter({ hasText: "请先选择一个投标项目" }).waitFor();
  assert.equal(taskRequests, 0);
  await fresh(); await send("生成本周行动简报");
  await page.locator("#weekly-data-scope").waitFor();
  assert.match(await page.locator("#weekly-data-scope").innerText(), /不会自动纳入本简报/);
  checks.push("negation and paraphrases route correctly; bidding requires a project and confirmation; briefing scope disclosed; routing does not execute");
  assert.deepEqual(errors, []);
  await writeFile(join(output, "result.json"), JSON.stringify({ checks, layouts, errors }, null, 2));
  console.log(JSON.stringify({ output, checks, layouts }, null, 2));
} catch (error) {
  if (page) {
    console.error("UI status:", await page.locator("#chat-status").innerText());
    await writeFile(join(output, "failure.txt"), await page.locator("body").ariaSnapshot());
    await page.screenshot({ path: join(output, "failure.png"), fullPage: true });
  }
  throw error;
} finally {
  await context?.close(); await browser?.close();
  child.stdin.end("stop\n");
  const timer = setTimeout(() => child.kill(), 10000);
  try { await exited; } finally { clearTimeout(timer); }
  if (diagnostics) console.error(diagnostics);
}
