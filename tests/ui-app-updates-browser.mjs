// Optional offline browser acceptance: real UI/HTTP and synthetic release source.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { createInterface } from "node:readline";
import { once } from "node:events";
import { join, resolve } from "node:path";

const { chromium } = await import(process.env.A4M_BROWSER_PLAYWRIGHT || "playwright");
const output = resolve(process.env.A4M_BROWSER_OUTPUT || `outputs/verification/app-updates-${Date.now()}`);
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true, channel: process.env.A4M_BROWSER_CHANNEL || "msedge" });
const checks = [];

async function scenario(mode, test, { automatic = true } = {}) {
  const child = spawn(process.env.A4M_BROWSER_PYTHON || "python", ["-B", "-u", "tests/app_updates_browser_fixture.py", "--mode", mode],
    { cwd: process.cwd(), windowsHide: true, stdio: ["pipe", "pipe", "pipe"] });
  const exited = once(child, "exit");
  let diagnostics = "", context;
  child.stderr.on("data", (chunk) => { diagnostics = (diagnostics + chunk).slice(-8000); });
  try {
    const fixture = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`Fixture timeout: ${diagnostics}`)), 20000);
      const lines = createInterface({ input: child.stdout });
      lines.once("line", (line) => { clearTimeout(timer); lines.close(); resolve(JSON.parse(line)); });
      child.once("exit", (code) => { clearTimeout(timer); reject(new Error(`Fixture exit ${code}: ${diagnostics}`)); });
    });
    context = await browser.newContext({ viewport: { width: 1440, height: 1080 } });
    await context.route("**/*", (route) => new URL(route.request().url()).origin === fixture.url ? route.continue() : route.abort());
    if (!automatic) await context.addInitScript(() => localStorage.setItem("agent4market-auto-updates", "false"));
    const page = await context.newPage(), errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(fixture.url);
    await page.locator("#app-update-current-version").filter({ hasText: "0.20.2" }).waitFor({ state: "attached" });
    await test(page, async () => (await context.request.get(`${fixture.url}/fixture/stats`)).json());
    assert.deepEqual(errors, []);
    checks.push(mode + (automatic ? "" : "-manual"));
  } finally {
    if (context) await context.close();
    child.stdin.end("\n");
    const timer = setTimeout(() => child.kill(), 10000);
    try { await exited; } finally { clearTimeout(timer); }
  }
}

try {
  await scenario("available", async (page, stats) => {
    await page.locator("#app-updates-button-label").filter({ hasText: "发现新版本" }).waitFor();
    await page.locator("#app-updates-button").click();
    await page.locator("#app-update-status").filter({ hasText: "0.20.3" }).waitFor();
    assert.equal(await page.locator("#app-updates-badge").isVisible(), true);
    const dot = await page.locator("#app-updates-badge").boundingBox();
    assert.equal(dot.width, 7); assert.equal(dot.height, 7);
    await page.locator("#app-update-release summary").click();
    assert.match(await page.locator("#app-update-release-notes").innerText(), /<script>/u);
    assert.equal(await page.locator("#app-update-release-notes script").count(), 0);
    await page.locator("#app-update-panel").screenshot({ path: join(output, "update-available-synthetic.png") });
    await page.locator("#app-update-open-release").click();
    await page.getByRole("alertdialog").waitFor();
    assert.match(await page.getByRole("alertdialog").innerText(), /不会自动下载、安装或重启/u);
    await page.getByRole("alertdialog").getByRole("button", { name: "取消", exact: true }).click();
    assert.deepEqual((await stats()).opened, []);
    await page.locator("#app-update-open-release").click();
    await page.getByRole("alertdialog").getByRole("button", { name: "打开发布页", exact: true }).click();
    await page.locator("#app-update-action-status").filter({ hasText: "当前程序未被覆盖" }).waitFor();
    assert.deepEqual((await stats()).opened, ["https://github.com/WH2020/WorkFlow_Market/releases/tag/v0.20.3"]);
    await page.locator("#app-update-install").click();
    await page.getByRole("alertdialog").waitFor();
    assert.match(await page.getByRole("alertdialog").innerText(), /配置、密钥和业务数据保留在原目录/u);
    await page.getByRole("alertdialog").screenshot({ path: join(output, "update-install-confirm-synthetic.png") });
    await page.getByRole("alertdialog").getByRole("button", { name: "取消", exact: true }).click();
    assert.deepEqual((await stats()).installed, []);
    await page.locator("#app-update-install").click();
    await page.getByRole("alertdialog").getByRole("button", { name: "确认更新并重启", exact: true }).click();
    await page.locator("#app-update-install-status").filter({ hasText: "正在下载" }).waitFor();
    assert.deepEqual((await stats()).installed, ["v0.20.3"]);
    assert.equal(await page.locator("#app-update-progress").getAttribute("value"), "50");
    assert.equal(await page.locator("#app-update-install").isDisabled(), true);
    await page.locator("#app-update-panel").screenshot({ path: join(output, "update-install-progress-synthetic.png") });
    await page.locator("#app-update-cancel").click();
    await page.locator("#app-update-install-status").filter({ hasText: "更新已取消" }).waitFor();
    assert.equal((await stats()).cancelled, 1);
    await page.locator("#app-update-auto").uncheck();
    await page.reload();
    await page.locator("#app-update-current-version").filter({ hasText: "0.20.2" }).waitFor({ state: "attached" });
    await page.locator('button.sidebar-settings[data-view="settings"]').click();
    assert.equal(await page.locator("#app-update-auto").isChecked(), false);
    assert.equal((await stats()).fetches, 1);
    await page.setViewportSize({ width: 980, height: 860 });
    await page.locator("#app-update-panel").scrollIntoViewIfNeeded();
    const panel = await page.locator("#app-update-panel").boundingBox();
    const button = await page.locator("#app-update-open-release").boundingBox();
    assert.ok(button.x >= panel.x && button.x + button.width <= panel.x + panel.width);
    const installButton = await page.locator("#app-update-install").boundingBox();
    assert.ok(installButton.x >= panel.x && installButton.x + installButton.width <= panel.x + panel.width);
    await page.locator("#app-update-panel").screenshot({ path: join(output, "update-narrow-synthetic.png") });
  });
  await scenario("current", async (page, stats) => {
    await page.locator('button.sidebar-settings[data-view="settings"]').click();
    assert.match(await page.locator("#app-update-status").innerText(), /尚未检查/u);
    assert.equal((await stats()).fetches, 0);
    await page.locator("#app-update-check").click();
    await page.locator("#app-update-status").filter({ hasText: "已是当前发布的最新版本" }).waitFor();
    assert.equal(await page.locator("#app-update-open-release").isDisabled(), true);
    assert.equal((await stats()).fetches, 1);
  }, { automatic: false });
  for (const [mode, label] of [["ahead", "本机版本高于"], ["unavailable", "更新源暂不可用"], ["network", "无法连接 GitHub"], ["rate", "暂时限制"]]) {
    await scenario(mode, async (page, stats) => {
      await page.locator('button.sidebar-settings[data-view="settings"]').click();
      await page.locator("#app-update-status").filter({ hasText: label }).waitFor();
      assert.equal(await page.locator("#app-updates-badge").isVisible(), false);
      assert.equal(await page.locator("#app-update-open-release").isDisabled(), true);
      assert.equal((await stats()).fetches, 1);
      if (mode === "unavailable") {
        assert.doesNotMatch(await page.locator("#app-update-status").innerText(), /已是.*最新/u);
        await page.locator("#app-update-panel").screenshot({ path: join(output, "update-source-unavailable-synthetic.png") });
      }
      if (mode === "rate") assert.equal(await page.locator("#app-update-check").isDisabled(), true);
    });
  }
  console.log(JSON.stringify({ status: "passed", checks, screenshots: output, external_pages_opened: false }));
} finally { await browser.close(); }
