import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { rmSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { basename, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { after, before, test } from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { Type } from "typebox";
import { createAgentSession, DefaultResourceLoader, ModelRegistry, ModelRuntime, SessionManager, SettingsManager } from "@earendil-works/pi-coding-agent";
import registerCliProviders, { cliLaunchSha256 } from "../extensions/cli-model-provider.ts";
import registerModelGuard from "../extensions/subagent-model-guard.ts";
import { createGovernedSubagentContract } from "../extensions/subagent-contracts.ts";
import { runtimeModelRecipient } from "../extensions/model-selection.ts";

// Real Pi SDK -> production CLI transport -> Python process host -> synthetic Node CLI.
// This test never launches a real coding assistant, reads a login, or uses a network.
const protocols = ["claude-code", "codex-cli"];
const previous = new Map();
let root, runtime, registry;
function setenv(name, value) { previous.set(name, process.env[name]); process.env[name] = value; }

before(async () => {
  const repository = fileURLToPath(new URL("../../", import.meta.url));
  const python = process.env.AGENT4MARKET_TEST_PYTHON || "python";
  root = join(homedir(), `.agent4market-cli-test-${randomUUID().replaceAll("-", "")}`);
  execFileSync(python, ["-I", "-B", "-c",
    "import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from agent_platform.wechat_privacy import ensure_private_directory;ensure_private_directory(Path(sys.argv[2]))",
    repository, root]);
  // The process-host workspace belongs to this fixture too. Never depend on
  // or repair the user's persistent CLI runtime directories during a test.
  for (const name of ["HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA"]) setenv(name, root);
  const command = process.execPath;
  const entryPoint = fileURLToPath(new URL("fixtures/cli-provider-fixture.mjs", import.meta.url));
  const providers = protocols.map((api) => {
    const entry = { id: `agent4market-${api}-synthetic`, api, base_url: api === "claude-code" ? "https://api.anthropic.com" : "https://chatgpt.com/backend-api/codex", executable_path: entryPoint, command, args: [entryPoint], version: "synthetic-fixture", runner_policy_version: 1, models: [{ id: "synthetic-model", context_window: 32000, max_tokens: 4096 }] };
    return { ...entry, launch_sha256: cliLaunchSha256(entry) };
  });
  const manifest = join(root, "cli-backends.json");
  const bytes = JSON.stringify({ version: 1, providers });
  writeFileSync(manifest, bytes, { mode: 0o600 });
  setenv("AGENT4MARKET_CLI_BACKENDS_FILE", manifest);
  setenv("AGENT4MARKET_CLI_BACKENDS_SHA256", createHash("sha256").update(bytes).digest("hex"));
  setenv("AGENT4MARKET_CLI_PYTHON", execFileSync(python, ["-I", "-c", "import sys;sys.path.insert(0, sys.argv[1]);from agent_platform.environment import cli_python_executable;print(cli_python_executable())", repository], { encoding: "utf8" }).trim());
  setenv("AGENT4MARKET_TEST_PRIVATE_KEY", "SYNTHETIC_SECRET_CANARY");
  runtime = await ModelRuntime.create({ authPath: join(root, "auth.json"), modelsPath: join(root, "models.json"), modelsStorePath: join(root, "cache"), allowModelNetwork: false });
  registry = new ModelRegistry(runtime);
  registerCliProviders({ registerProvider: registry.registerProvider.bind(registry) });
});

after(() => {
  for (const [key, value] of previous) { if (value === undefined) delete process.env[key]; else process.env[key] = value; }
  if (root) {
    assert.equal(resolve(root, ".."), resolve(homedir()));
    assert.ok(basename(root).startsWith(".agent4market-cli-test-"));
    rmSync(root, { recursive: true, force: true });
  }
});

async function sessionFor(api, calls, guarded = false) {
  const model = registry.find(`agent4market-${api}-synthetic`, "synthetic-model");
  assert.ok(model, "custom CLI provider must register before selection");
  const settings = SettingsManager.inMemory({ packages: [], extensions: [], compaction: { enabled: false }, retry: { enabled: false, provider: { maxRetries: 0 } }, enableAnalytics: false, enableInstallTelemetry: false });
  const resourceLoader = new DefaultResourceLoader({ cwd: root, agentDir: join(root, "agent"), settingsManager: settings, noExtensions: true, extensionFactories: guarded ? [registerModelGuard] : [registerCliProviders], noSkills: true, noPromptTemplates: true, noThemes: true, noContextFiles: true, systemPrompt: "Synthetic CLI transport test. Call lookup_fixture once, then answer." });
  await resourceLoader.reload();
  const { session } = await createAgentSession({ cwd: root, agentDir: join(root, "agent"), modelRuntime: runtime, model, thinkingLevel: "off", resourceLoader, settingsManager: settings, sessionManager: SessionManager.inMemory(root), tools: guarded ? [] : ["lookup_fixture"], customTools: [{
    name: "lookup_fixture", label: "Synthetic lookup", description: "Only synthetic data", parameters: Type.Object({ label: Type.String() }),
    execute: async (_id, args) => { calls.push(args); return { content: [{ type: "text", text: "FIXTURE_TOOL_RESULT" }], details: {} }; },
  }] });
  await session.bindExtensions({});
  return session;
}

for (const api of protocols) {
  test(`${api}: real SDK and process host complete the controlled tool-result roundtrip`, { timeout: 20000 }, async () => {
    const calls = []; const session = await sessionFor(api, calls);
    try {
      await session.prompt("fixture:roundtrip 合成任务");
      assert.deepEqual(calls, [{ label: "离线 CLI" }], session.messages.at(-1)?.errorMessage);
      assert.equal(session.messages.at(-1).stopReason, "stop");
      assert.equal(session.messages.at(-1).content[0].text, "合成 CLI 工具闭环完成");
    } finally { await session.abort(); session.dispose(); }
  });
  test(`${api}: auth error does not expose raw output or execute tools`, { timeout: 15000 }, async () => {
    const calls = []; const session = await sessionFor(api, calls);
    try {
      await session.prompt("fixture:auth-error");
      assert.deepEqual(calls, []); assert.equal(session.messages.at(-1).stopReason, "error");
      assert.ok(!JSON.stringify(session.messages.at(-1)).includes("PRIVATE_FIXTURE_DETAIL"));
      assert.match(session.messages.at(-1).errorMessage, /CLI/);
    } finally { await session.abort(); session.dispose(); }
  });
  test(`${api}: live cancellation kills the owned process request and drops results`, { timeout: 15000 }, async () => {
    const calls = []; const session = await sessionFor(api, calls);
    try {
      const running = session.prompt("fixture:cancel");
      await delay(800); await session.abort(); await running;
      assert.deepEqual(calls, []); assert.equal(session.isStreaming, false);
      assert.equal(session.messages.at(-1).stopReason, "aborted");
    } finally { await session.abort(); session.dispose(); }
  });
  test(`${api}: frozen recipient guard prevents a real provider process request`, { timeout: 15000 }, async () => {
    const calls = []; const session = await sessionFor(api, calls, true);
    try {
      const contract = createGovernedSubagentContract(root, { task_id: "task-cli-boundary", profile_id: "sales-director", node_id: "review", task_version: 1, role: "readonly-reviewer", objective: "Synthetic only", allowed_tools: [], authorized_urls: [], expected_model: `${session.model.provider}/${session.model.id}`, model_recipient: { ...runtimeModelRecipient(session.model), base_url: "https://wrong-recipient.example" } });
      await session.prompt(`[DIRECTOR_TASK_CONTEXT ${contract.task_id}]\n受管任务：${contract.task_id}\n受管节点：review\ncontract_id：${contract.contract_id}\nfixture:roundtrip PRIVATE_TASK_CANARY`);
      assert.deepEqual(calls, []);
      assert.ok(!session.messages.some((message) => message.role === "assistant" && ["stop", "toolUse"].includes(message.stopReason)));
    } finally { await session.abort(); session.dispose(); }
  });
}
