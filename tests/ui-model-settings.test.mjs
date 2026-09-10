// Tests the actual UI functions in a small DOM double; this is not visual/browser QA.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../ui/app.js", import.meta.url), "utf8");
const html = readFileSync(new URL("../ui/index.html", import.meta.url), "utf8");
const controlIds = Array.from(html.matchAll(/\bid="([^"]+)"/g), (match) => match[1]);
const parsed = ts.createSourceFile("app.js", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
const functions = new Set(["populateModelOptions", "modelProviders", "providerType", "isCliProvider", "apiModelProviders", "cliProvider", "matchingCliProvider", "runnableModelProviders",
  "saveEditedCapabilities", "showEditedCapabilities", "enabledModelIds", "refreshEditorModelOptions", "fillProviderEditor", "renderModelSettings",
  "selectedProviderType", "selectProviderType", "cliConfiguredModel", "renderProviderSettings", "detectCodingAssistants", "saveProviderSettings",
  "renderCodexThinking", "renderCodexCatalog", "loadCodexModels", "renderTaskThinkingOptions",
  "renderTaskRuntimeOptions", "taskRuntimeSelection", "modelPayload"]);
const hooks = ["model-instance", "add-model-provider", "model-select", "model-enabled-ids", "enable-model", "model-vendor", "discover-models", "save-model-settings", "reset-cli-provider", "codex-cli-model", "codex-cli-thinking", "refresh-codex-models"];
const pieces = [];
function visit(node) {
  if (ts.isFunctionDeclaration(node) && functions.has(node.name?.text)) pieces.push(node.getText(parsed));
  if (ts.isExpressionStatement(node) && hooks.some((id) => node.getText(parsed).startsWith(`$("${id}").on`))) pieces.push(node.getText(parsed));
  ts.forEachChild(node, visit);
}
visit(parsed);

class Element {
  children = []; value = ""; textContent = ""; checked = false; disabled = false; hidden = false; dataset = {}; placeholder = "";
  classList = { toggle() {}, add() {}, remove() {} };
  constructor(tag = "div") { this.tagName = tag.toUpperCase(); }
  get options() { return this.children.flatMap((child) => child.tagName === "OPTGROUP" ? child.options : [child]); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; if (this.tagName === "SELECT") this.value = this.options[0]?.value || ""; }
  focus() {}
  scrollIntoView() {}
}

function fixture() {
  const elements = new Map();
  const providerRadios = ["newapi", "claude-code", "codex-cli"].map((value, index) => Object.assign(new Element("input"), { value, checked: index === 0 }));
  const selects = new Set(["model-instance", "model-vendor", "model-api", "model-select", "model-role-scout", "model-role-reviewer", "task-model", "task-thinking", "codex-cli-model", "codex-cli-thinking"]);
  const $ = (id) => {
    assert.equal(controlIds.filter((candidate) => candidate === id).length, 1, `UI control must exist exactly once: ${id}`);
    if (!elements.has(id)) elements.set(id, new Element(selects.has(id) ? "select" : "div"));
    return elements.get(id);
  };
  const providers = ["a", "b"].map((id) => ({ id: `agent4market-${id}`, name: `供应商 ${id.toUpperCase()}`, vendor: "custom", api: "openai-completions", base_url: "https://example.com",
    has_api_key: true, status: "configured", enabled: true, models: [{ id: "same-model", reasoning: id === "b", context_window: 64000, max_tokens: 8000 }] }));
  const state = { model: { providers, provider_id: providers[0].id, default_model: providers[0].id + "/same-model", role_models: {}, status: "configured" } };
  const context = vm.createContext({ model: state, $, document: { createElement: (tag) => new Element(tag),
    querySelectorAll: (selector) => selector === 'input[name="provider-type"]' ? providerRadios : [] }, localStorage: { getItem() {}, setItem() {} },
    editingModels: new Map(), editingModelId: "", discoveredModelOptions: [], taskRuntimeCatalogKey: "", modelSettingsInitialized: false,
    providerSettingsInitialized: false, codingAssistantDetection: {}, CLI_PROVIDER_TYPES: new Set(["claude-code", "codex-cli"]),
    codexModelCatalog: null, codexCatalogLoading: false, codexCatalogAttempt: "",
    api: async () => { throw new Error("offline"); }, note() {}, confirmAction: async () => true });
  vm.runInContext(pieces.join("\n"), context);
  return { context, $, state, providerRadios };
}

test("same model IDs remain grouped and capabilities do not bleed between provider editors", () => {
  const { context, $, state } = fixture();
  context.renderModelSettings(); context.renderTaskRuntimeOptions();
  assert.equal($("model-instance").value, "agent4market-a");
  assert.equal($("model-reasoning").checked, false);
  assert.deepEqual($("task-model").options.map((item) => item.value), ["", "agent4market-a/same-model", "agent4market-b/same-model"]);
  context.fillProviderEditor(state.model.providers[1].id);
  assert.equal($("model-reasoning").checked, true);
  assert.equal($("model-make-default").checked, false);
  assert.equal($("model-base-url").readOnly, true);
  assert.equal($("model-api").disabled, true);
  $("task-model").value = "agent4market-b/same-model";
  assert.equal(context.taskRuntimeSelection().requested_model, "agent4market-b/same-model");
});

test("manual model entry works offline and failed discovery preserves IDs and typed capabilities", async () => {
  const { context, $ } = fixture();
  context.fillProviderEditor("");
  $("model-enabled-ids").value = "manual-model\nmanual-model";
  $("model-enabled-ids").oninput();
  assert.equal($("model-select").value, "manual-model");
  assert.equal($("save-model-settings").disabled, false);
  $("model-reasoning").checked = true;
  $("model-context-window").value = "90000";
  await $("discover-models").onclick();
  assert.equal($("model-select").value, "manual-model");
  assert.equal($("model-reasoning").checked, true);
  assert.match($("model-discovery-status").textContent, /保留已配置目录/);
  let payload;
  context.api = async (_url, options) => { payload = JSON.parse(options.body); throw new Error("capture only"); };
  await $("save-model-settings").onclick();
  assert.equal(payload.provider_id, null);
  assert.equal(payload.models.length, 1);
  assert.equal(payload.models[0].reasoning, true);
  assert.equal(payload.models[0].context_window, 90000);
});

test("CLI providers stay out of the API editor but remain selectable for ordinary tasks", () => {
  const { context, $, state, providerRadios } = fixture();
  const cli = { id: "agent4market-codex-cli", name: "Codex CLI", vendor: "codex-cli", api: "codex-cli",
    base_url: "https://chatgpt.com", executable_path: "C:\\Tools\\codex.exe", authentication: "cli-managed-unverified",
    status: "configured", enabled: true, models: [{ id: "gpt-real-account-model", enabled: true, tools: true }] };
  state.model.providers.push(cli);
  state.model.default_model = cli.id + "/" + cli.models[0].id;
  context.renderModelSettings(true);
  context.renderProviderSettings(true);
  context.renderTaskRuntimeOptions();

  assert.deepEqual($("model-instance").options.map((item) => item.value), ["", "agent4market-a", "agent4market-b"]);
  assert.deepEqual($("task-model").options.map((item) => item.value), ["", "agent4market-a/same-model", "agent4market-b/same-model", "agent4market-codex-cli/gpt-real-account-model"]);
  assert.equal(providerRadios.find((radio) => radio.value === "codex-cli").checked, true);
  assert.equal($("codex-cli-model").value, "gpt-real-account-model");
  assert.equal($("model-instance").value, "agent4market-a", "the API editor must never load the default CLI record");
  context.fillProviderEditor(cli.id);
  assert.equal(context.modelPayload().provider_id, null, "a CLI id must not be converted through the API save payload");
});

test("CLI detection never invents models and save updates only the selected CLI provider", async () => {
  const { context, $, state } = fixture();
  const existingClaude = { id: "agent4market-claude-code", name: "Claude Code", vendor: "claude-code", api: "claude-code",
    base_url: "https://api.anthropic.com", executable_path: "C:\\Detected\\claude.exe", version: "9.9.9", authentication: "cli-managed-unverified",
    status: "configured", enabled: true, models: [{ id: "old-real-model", enabled: true, tools: true }] };
  const existingCodex = { id: "agent4market-codex-cli", name: "Codex CLI", vendor: "codex-cli", api: "codex-cli",
    base_url: "https://chatgpt.com", executable_path: "C:\\Tools\\codex.exe", authentication: "cli-managed-unverified",
    status: "configured", enabled: true, models: [{ id: "codex-account-model", enabled: true, tools: true }] };
  state.model.providers.push(existingClaude, existingCodex);
  context.renderProviderSettings(true);
  context.api = async (url) => {
    assert.equal(url, "/api/coding-assistants/detect");
    return { "claude-code": { available: true, path: "C:\\Detected\\claude.exe", version: "9.9.9", backend_available: true,
      models: [{ id: "must-not-be-suggested", name: "Must not be suggested" }] }, "codex-cli": { available: false, models: [] } };
  };
  await context.detectCodingAssistants();
  assert.equal($("claude-code-path").value, "C:\\Detected\\claude.exe");
  assert.equal($("claude-code-model").value, "old-real-model", "detection must not replace the user-confirmed model ID");
  assert.match($("claude-code-status").textContent, /登录状态未验证/);

  context.selectProviderType("claude-code");
  $("claude-code-model").value = "claude-account-exact-model";
  let captured;
  context.api = async (url, options) => {
    captured = { url, body: JSON.parse(options.body) };
    const updated = { ...existingClaude, executable_path: captured.body.executable_path,
      models: [{ id: captured.body.selected_model, enabled: true, tools: true }] };
    return { message: "saved without login verification", model: { ...state.model,
      providers: [state.model.providers[0], state.model.providers[1], updated, existingCodex],
      default_model: `${updated.id}/${captured.body.selected_model}` } };
  };
  await context.saveProviderSettings();
  assert.equal(captured.url, "/api/model-provider-choice");
  assert.deepEqual(captured.body, { provider: "claude-code", executable_path: "C:\\Detected\\claude.exe",
    selected_model: "claude-account-exact-model", provider_id: "agent4market-claude-code" });
  assert.equal("arguments" in captured.body, false);
  assert.equal(context.model.model.providers.some((provider) => provider.id === existingCodex.id), true, "the other CLI provider must remain configured");
  assert.equal(context.model.model.providers.filter((provider) => provider.vendor === "custom").length, 2, "API providers must remain configured");
});

test("CLI removal reuses provider reset without opening the API editor", async () => {
  const { context, $, state } = fixture();
  const cli = { id: "agent4market-codex-cli", name: "Codex CLI", vendor: "codex-cli", api: "codex-cli",
    executable_path: "C:\\Tools\\codex.exe", status: "configured", enabled: true,
    models: [{ id: "codex-account-model", enabled: true, tools: true }] };
  state.model.providers.push(cli);
  state.model.default_model = cli.id + "/codex-account-model";
  context.renderProviderSettings(true);
  context.selectProviderType("codex-cli");
  let captured;
  context.api = async (url, options) => {
    captured = { url, body: JSON.parse(options.body) };
    return { ...state.model, providers: state.model.providers.filter((provider) => provider.id !== cli.id),
      default_model: "agent4market-a/same-model", message: "removed" };
  };
  await $("reset-cli-provider").onclick();
  assert.deepEqual(captured, { url: "/api/model-settings/reset", body: { provider_id: cli.id } });
  assert.equal(context.model.model.providers.some((provider) => provider.id === cli.id), false);
  assert.equal(context.model.model.providers.length, 2);
});

test("CLI controls use a discovered Codex catalog and state their security boundary", () => {
  assert.doesNotMatch(html, /name="provider-type" value="(?:claude-code|codex-cli)" disabled/u);
  assert.match(html, /id="claude-code-model" type="text"/u);
  assert.match(html, /select id="codex-cli-model"/u);
  assert.match(html, /select id="codex-cli-thinking"/u);
  assert.doesNotMatch(html, /claude-opus-5|claude-sonnet-5|claude-haiku-4\.5/u);
  assert.match(html, /“已安装”不代表已经登录/u);
  assert.match(html, /不会授予 CLI 自带的文件、Shell 或 MCP 权限/u);
  assert.match(html, /微信会话整理首版仅支持 API 模型/u);
  assert.match(source, /微信会话整理首版不支持 Claude Code 或 Codex CLI/u);
  assert.match(source, /localizeStaticInterface\(\);\s*load\(\)\s*\.then\(\(\) =>\s*(?:\{\s*checkAppUpdates\(false\);\s*return\s+)?detectCodingAssistants\(\)/u,
    "saved registry state must render before asynchronous CLI detection");
});

function codexCatalog() {
  return { discovery_id: "catalog-fixture", source: "cli-model-list-isolated", account_verified: false, models: [
    { id: "synthetic-a", display_name: "Synthetic A", is_default: true, supported_thinking_levels: ["low", "medium", "high", "max"],
      cli_reasoning: { supported_efforts: ["low", "medium", "high", "max", "ultra"], default_effort: "medium" } },
    { id: "synthetic-b", display_name: "Synthetic B", supported_thinking_levels: ["low", "high"],
      cli_reasoning: { supported_efforts: ["low", "high"], default_effort: "low" } },
  ] };
}

test("selecting Codex automatically reads metadata once and never silently replaces a saved model", async () => {
  const { context, $, state } = fixture();
  state.model.providers.push({ id: "agent4market-codex-cli", api: "codex-cli", status: "configured", enabled: true, models: [{ id: "previous-custom-id" }] });
  context.codingAssistantDetection = { "codex-cli": { available: true, path: "C:/synthetic/codex.exe", launch_sha256: "fixture-hash" } };
  context.renderProviderSettings(true);
  let calls = 0;
  context.api = async (url, options) => {
    calls++; assert.equal(url, "/api/coding-assistants/models");
    assert.deepEqual(JSON.parse(options.body), { provider: "codex-cli", executable_path: "C:/synthetic/codex.exe" });
    return codexCatalog();
  };
  context.selectProviderType("codex-cli");
  await new Promise(setImmediate);
  assert.equal(calls, 1);
  assert.equal($("codex-cli-model").value, "previous-custom-id");
  assert.equal($("save-provider-settings").disabled, true);
  assert.match($("codex-cli-catalog-status").textContent, /未验证账号权限/u);
  context.renderProviderSettings(); context.selectProviderType("codex-cli");
  await new Promise(setImmediate);
  assert.equal(calls, 1, "routine refresh must not repeatedly launch CLI");
  assert.equal(state.model.providers.at(-1).models[0].id, "previous-custom-id");
});

test("Codex efforts follow the selected model, disable ultra, and save exact catalog selection", async () => {
  const { context, $ } = fixture();
  context.codingAssistantDetection = { "codex-cli": { available: true, path: "C:/synthetic/codex.exe", launch_sha256: "fixture-hash" } };
  context.api = async () => codexCatalog();
  await context.loadCodexModels(true);
  context.selectProviderType("codex-cli"); $("codex-cli-path").value = "C:/synthetic/codex.exe";
  assert.equal($("codex-cli-model").value, "synthetic-a");
  assert.equal($("codex-cli-thinking").value, "medium");
  assert.equal($("codex-cli-thinking").options.find((option) => option.value === "ultra").disabled, true);
  $("codex-cli-model").value = "synthetic-b"; $("codex-cli-model").onchange();
  assert.deepEqual($("codex-cli-thinking").options.map((option) => option.value), ["low", "high"]);
  assert.equal($("codex-cli-thinking").value, "low");
  let saved;
  context.api = async (url, options) => { saved = { url, body: JSON.parse(options.body) }; throw new Error("synthetic save intercepted"); };
  $("codex-cli-thinking").value = "high";
  await context.saveProviderSettings();
  assert.deepEqual(saved, { url: "/api/model-provider-choice", body: { provider: "codex-cli", executable_path: "C:/synthetic/codex.exe",
    selected_model: "synthetic-b", discovery_id: "catalog-fixture", selected_thinking_level: "high" } });
  saved = null; $("codex-cli-thinking").value = "ultra";
  await context.saveProviderSettings(); assert.equal(saved, null);
});

test("task thinking choices use saved Codex capabilities and clear an incompatible previous choice", () => {
  const { context, $, state } = fixture();
  const record = { ...codexCatalog().models[1], default_thinking_level: "high", enabled: true, tools: true };
  state.model.providers.push({ id: "agent4market-codex-cli", api: "codex-cli", status: "configured", models: [record] });
  state.model.default_model = "agent4market-codex-cli/synthetic-b";
  $("task-thinking").dataset.initialized = "true"; $("task-thinking").value = "max";
  let notice = ""; context.note = (text) => { notice = text; };
  context.renderTaskRuntimeOptions();
  assert.deepEqual($("task-thinking").options.map((option) => option.value), ["", "low", "high"]);
  assert.equal($("task-thinking").options[0].textContent, "模型默认：high");
  assert.equal($("task-thinking").value, ""); assert.match(notice, /已清除/u);
  $("task-model").value = "agent4market-a/same-model"; context.renderTaskThinkingOptions();
  assert.ok($("task-thinking").options.some((option) => option.value === "max"));
});

test("failed Codex refresh preserves persisted providers and disallows saving stale metadata", async () => {
  const { context, $, state } = fixture();
  const before = JSON.stringify(state);
  context.codingAssistantDetection = { "codex-cli": { available: true, path: "C:/synthetic/codex.exe", launch_sha256: "fixture-hash" } };
  context.codexModelCatalog = codexCatalog();
  context.api = async () => { throw new Error("synthetic timeout"); };
  await context.loadCodexModels(true); context.selectProviderType("codex-cli");
  assert.equal(JSON.stringify(state), before); assert.equal(context.codexModelCatalog, null);
  assert.equal($("save-provider-settings").disabled, true);
  assert.match($("codex-cli-catalog-status").textContent, /timeout.*已有模型配置保持不变/u);
});
