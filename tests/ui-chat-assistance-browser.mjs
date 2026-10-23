// Isolated UI + HTTP + synthetic model. No user database, login or external requests.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { createInterface } from "node:readline";
import { once } from "node:events";
import { join, resolve } from "node:path";

const { chromium } = await import(process.env.A4M_BROWSER_PLAYWRIGHT || "playwright");
const output = resolve(process.env.A4M_BROWSER_OUTPUT || `outputs/verification/chat-assistance-${Date.now()}`);
await mkdir(output, { recursive: true });
const child = spawn(process.env.A4M_BROWSER_PYTHON || "python", ["-I", "-B", "-u", "tests/free_chat_browser_fixture.py"],
  { cwd: process.cwd(), windowsHide: true, stdio: ["pipe", "pipe", "pipe"] });
const exited = once(child, "exit");
let diagnostics = "", browser, context;
child.stderr.on("data", chunk => { diagnostics = (diagnostics + chunk).slice(-8000); });
const checks = [];
try {
  const fixture = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`Fixture timeout: ${diagnostics}`)), 20000);
    createInterface({ input: child.stdout }).once("line", line => { clearTimeout(timer); resolve(JSON.parse(line)); });
    child.once("exit", code => { clearTimeout(timer); reject(new Error(`Fixture exit ${code}: ${diagnostics}`)); });
  });
  browser = await chromium.launch({ headless: true, channel: process.env.A4M_BROWSER_CHANNEL || "msedge" });
  context = await browser.newContext({ viewport: { width: 1360, height: 1000 } });
  await context.route("**/*", route => new URL(route.request().url()).origin === fixture.url ? route.continue() : route.abort());
  await context.addInitScript(() => localStorage.setItem("agent4market-auto-updates", "false"));
  const page = await context.newPage(), errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const stats = async () => (await context.request.get(`${fixture.url}/fixture/stats`)).json();
  const open = async target => {
    await target.goto(fixture.url);
    await target.locator('button.nav-item[data-view="chat"]').click();
    await target.locator("#chat-status").filter({ hasText: "准备好了" }).waitFor();
  };
  const send = async (text, target = page) => {
    await target.locator("#chat-input").fill(text);
    await target.locator("#chat-send").click();
    await target.locator("#chat-status").filter({ hasText: "回复完成" }).waitFor();
    await target.locator("#chat-stop").waitFor({ state: "hidden" });
  };
  const fresh = async () => {
    await page.locator("#chat-new").click();
    await page.locator("#chat-status").filter({ hasText: "新对话已准备好" }).waitFor();
  };
  await open(page);
  await send("以后回答请简短，并且先给结论");
  assert.equal(await page.locator(".chat-preference-suggestion").count(), 2);
  await page.locator("#chat-memory-open").click();
  await page.getByRole("heading", { name: "跨对话回答偏好" }).waitFor();
  assert.equal(await page.locator("#chat-memory-panel select").first().inputValue(), "");
  await page.locator("#chat-memory-open").click();
  for (const key of ["length", "structure"]) {
    const card = page.locator(`.chat-preference-suggestion[data-preference-key="${key}"]`);
    await card.getByRole("button", { name: "记住这个偏好" }).click();
    await card.locator(".chat-assist-status").filter({ hasText: "已记住" }).waitFor();
  }
  await page.screenshot({ path: join(output, "preference-cards.png"), fullPage: true });
  checks.push("explicit suggestions are pending until confirmed; multiple saves advance revisions");

  await page.reload();
  await page.locator('button.nav-item[data-view="chat"]').click();
  await page.locator("#chat-status").filter({ hasText: "准备好了" }).waitFor();
  await send("请介绍一下你的能力");
  assert.match(JSON.stringify((await stats()).requests.at(-1).body), /简短回答/);
  assert.match(JSON.stringify((await stats()).requests.at(-1).body), /先结论后依据/);
  assert.equal(await page.locator(".chat-preferences-used").count(), 1);
  await fresh();
  await page.locator("#chat-use-preferences").uncheck();
  await send("你好");
  assert.doesNotMatch(JSON.stringify((await stats()).requests.at(-1).body), /用户确认的回答偏好|简短回答/);
  assert.equal(await page.locator(".chat-preferences-used").count(), 0);
  await page.locator("#chat-use-preferences").check();
  await page.locator("#chat-memory-open").click();
  await page.getByRole("heading", { name: "跨对话回答偏好" }).waitFor();
  const length = page.locator(".chat-preference-row").filter({ hasText: "回答详略" });
  await length.locator("select").selectOption("detailed");
  await length.getByRole("button", { name: "保存", exact: true }).click();
  await length.locator(".chat-assist-status").filter({ hasText: "已保存" }).waitFor();
  await fresh(); await send("现在你好");
  assert.match(JSON.stringify((await stats()).requests.at(-1).body), /详细解释/);
  assert.doesNotMatch(JSON.stringify((await stats()).requests.at(-1).body), /简短回答/);
  for (const label of ["回答详略", "回答结构"]) {
    const row = page.locator(".chat-preference-row").filter({ hasText: label });
    await row.getByRole("button", { name: "忘记", exact: true }).click();
    await row.locator(".chat-assist-status").filter({ hasText: "已忘记" }).waitFor();
  }
  await fresh(); await send("你好啊");
  assert.doesNotMatch(JSON.stringify((await stats()).requests.at(-1).body), /用户确认的回答偏好|详细解释/);
  await page.locator("#chat-memory-open").click();
  checks.push("preferences persist across reload; current turn opt-out; edit and forget affect new turns");

  await fresh(); await send("苏园的预算还没确认，下周五客户内部讨论。");
  const card = page.locator(".chat-project-card").last();
  assert.match(await card.innerText(), /苏州产业园项目/);
  assert.match(await card.innerText(), /待补充/);
  const note = page.locator(".chat-project-note").last();
  await note.locator("summary").click();
  assert.match(await note.locator("textarea").inputValue(), /预算还没确认/);
  await note.locator("textarea").fill('<img src="https://fixture.invalid/x" onerror="alert(1)"> 合成客户预算待确认，需下周补充。');
  await page.screenshot({ path: join(output, "project-note-form.png"), fullPage: true });

  const other = await context.newPage(); await open(other);
  await send("苏园客户补充：需要交付清单。", other);
  const otherNote = other.locator(".chat-project-note").last();
  await otherNote.locator("summary").click();
  await otherNote.getByRole("button", { name: "确认保存到项目" }).click();
  await otherNote.locator(".chat-assist-status").filter({ hasText: "已保存到项目" }).waitFor();
  await note.getByRole("button", { name: "确认保存到项目" }).click();
  await note.locator(".chat-assist-status.error").filter({ hasText: "更新" }).waitFor();
  assert.match(await note.locator("textarea").inputValue(), /预算待确认/);
  await note.getByRole("button", { name: "刷新档案版本" }).click();
  await note.locator(".chat-assist-status").filter({ hasText: "已刷新" }).waitFor();
  await note.getByRole("button", { name: "确认保存到项目" }).click();
  await note.locator(".chat-assist-status").filter({ hasText: "已保存到项目" }).waitFor();
  assert.equal(await note.getByRole("button", { name: "确认保存到项目" }).isDisabled(), true);
  await send("这个项目有哪些最近记录？");
  assert.match(JSON.stringify((await stats()).requests.at(-1).body), /合成客户预算待确认/);
  assert.match(JSON.stringify((await stats()).requests.at(-1).body), /用户确认的对话记录/);
  await page.locator(".chat-project-card").last().locator("summary").click();
  assert.equal(await page.locator("#chat-conversation img, #chat-conversation script").count(), 0);
  checks.push("project cards and inline notes; conflict retains draft; refresh + confirm; next turn recalls saved note; no executable HTML");

  const followup = page.locator(".chat-project-note").last();
  await followup.locator("summary").click();
  await followup.locator("textarea").fill("这是一条只用于追问的合成补充");
  await followup.getByRole("button", { name: "仅用于追问，不保存" }).click();
  await page.locator("#chat-status").filter({ hasText: "回复完成" }).waitFor();
  assert.match(JSON.stringify((await stats()).requests.at(-1).body), /只用于追问的合成补充/);
  assert.match(await page.locator(".chat-project-card").last().innerText(), /2 \/ 2 条/);
  checks.push("inline form can continue the conversation without persisting a business note");

  await page.setViewportSize({ width: 390, height: 844 });
  const bounds = await page.locator(".chat-project-card").last().boundingBox();
  assert.ok(bounds && bounds.x >= 0 && bounds.x + bounds.width <= 390);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
  assert.equal(overflow, false);
  await page.screenshot({ path: join(output, "chat-cards-mobile.png"), fullPage: true });
  checks.push("390px narrow layout and zero page script errors");
  assert.deepEqual(errors, []);
  await writeFile(join(output, "result.json"), JSON.stringify({ checks, errors }, null, 2));
  console.log(JSON.stringify({ output, checks }, null, 2));
} finally {
  await context?.close(); await browser?.close();
  if (child.exitCode === null) child.stdin.end("stop\n");
  await exited;
}
