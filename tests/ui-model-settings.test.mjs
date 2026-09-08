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
const functions = new Set(["populateModelOptions", "modelProviders", "saveEditedCapabilities", "showEditedCapabilities", "enabledModelIds",
  "refreshEditorModelOptions", "fillProviderEditor", "renderModelSettings", "renderProviderSettings", "renderTaskRuntimeOptions", "taskRuntimeSelection", "modelPayload"]);
const hooks = ["model-instance", "add-model-provider", "model-select", "model-enabled-ids", "enable-model", "model-vendor", "discover-models", "save-model-settings"];
const pieces = [];
function visit(node) {
  if (ts.isFunctionDeclaration(node) && functions.has(node.name?.text)) pieces.push(node.getText(parsed));
  if (ts.isExpressionStatement(node) && hooks.some((id) => node.getText(parsed).startsWith(`$("${id}").on`))) pieces.push(node.getText(parsed));
  ts.forEachChild(node, visit);
}
visit(parsed);

class Element {
  children = []; value = ""; textContent = ""; checked = false; disabled = false; dataset = {}; placeholder = "";
  classList = { toggle() {}, add() {}, remove() {} };
  constructor(tag = "div") { this.tagName = tag.toUpperCase(); }
  get options() { return this.children.flatMap((child) => child.tagName === "OPTGROUP" ? child.options : [child]); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; if (this.tagName === "SELECT") this.value = this.options[0]?.value || ""; }
  focus() {}
}

function fixture() {
  const elements = new Map();
  const selects = new Set(["model-instance", "model-vendor", "model-api", "model-select", "model-role-scout", "model-role-reviewer", "task-model", "task-thinking"]);
  const $ = (id) => {
    assert.equal(controlIds.filter((candidate) => candidate === id).length, 1, `UI control must exist exactly once: ${id}`);
    if (!elements.has(id)) elements.set(id, new Element(selects.has(id) ? "select" : "div"));
    return elements.get(id);
  };
  const providers = ["a", "b"].map((id) => ({ id: `agent4market-${id}`, name: `供应商 ${id.toUpperCase()}`, vendor: "custom", api: "openai-completions", base_url: "https://example.com",
    has_api_key: true, status: "configured", enabled: true, models: [{ id: "same-model", reasoning: id === "b", context_window: 64000, max_tokens: 8000 }] }));
  const state = { model: { providers, provider_id: providers[0].id, default_model: providers[0].id + "/same-model", role_models: {}, status: "configured" } };
  const context = vm.createContext({ model: state, $, document: { createElement: (tag) => new Element(tag) }, localStorage: { getItem() {}, setItem() {} },
    editingModels: new Map(), editingModelId: "", discoveredModelOptions: [], taskRuntimeCatalogKey: "", modelSettingsInitialized: false,
    api: async () => { throw new Error("offline"); }, note() {} });
  vm.runInContext(pieces.join("\n"), context);
  return { context, $, state };
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
