// Real production submit function with synthetic DOM/HTTP doubles; no cloud calls.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../ui/app.js", import.meta.url), "utf8");
const parsed = ts.createSourceFile("app.js", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
let submit;
function visit(node) {
  if (ts.isFunctionDeclaration(node) && node.name?.text === "createWechatReview") submit = node.getText(parsed);
  ts.forEachChild(node, visit);
}
visit(parsed);
assert.ok(submit);
assert.doesNotMatch(source, /wechat-model-sharing|请先确认允许当前任务使用所选会话文字/);
assert.doesNotMatch(readFileSync(new URL("../ui/index.html", import.meta.url), "utf8"), /wechat-model-sharing/);

function fixture(api) {
  const provider = { id: `fixture-${api}`, name: api, api, status: "configured",
    base_url: api === "claude-code" ? "https://api.anthropic.com" : api === "codex-cli" ? "https://chatgpt.com/backend-api/codex" : "https://models.example",
    models: [{ id: "synthetic-model", enabled: true, tools: true }] };
  const modelKey = `${provider.id}/synthetic-model`;
  const calls = []; const confirmations = [];
  const context = vm.createContext({
    formatDate: () => "2026-09-16", selectedProject: "project-default",
    wechatState: { selected: new Set(["synthetic-conversation"]), rows: [{ conversation_id: "synthetic-conversation", account_id: "synthetic-account" }] },
    wechatFilters: () => ({ date_from: "2026-09-15", date_to: "2026-09-16", query: "预算" }),
    taskRuntimeSelection: () => ({ requested_model: modelKey }), modelProviders: () => [provider],
    confirmAction: async (options) => { confirmations.push(options); return true; },
    api: async (url, options) => { calls.push({ url, body: JSON.parse(options.body) }); return url.endsWith("scopes")
      ? { scope_id: "wechat-scope-0123456789abcdefabcd", title: "合成范围", date_from: "2026-09-15", date_to: "2026-09-16", conversation_count: 1, message_count: 2 }
      : { request_id: "synthetic-request" }; },
    note() {}, load: async () => {}, switchView() {},
  });
  vm.runInContext(submit, context);
  return { context, provider, calls, confirmations, modelKey };
}

for (const api of ["openai-responses", "claude-code", "codex-cli"]) {
  test(`${api}: selected WeChat scope uses one existing confirmation and the exact model`, async () => {
    const f = fixture(api);
    await f.context.createWechatReview();
    assert.equal(f.confirmations.length, 1);
    assert.equal(f.confirmations[0].title, "确认整理微信会话");
    assert.doesNotMatch(JSON.stringify(f.confirmations), /账号切换|无法.*冻结/);
    assert.equal(f.calls.length, 2);
    assert.deepEqual(f.calls[0].body.model_recipient, { provider_id: f.provider.id, base_url: f.provider.base_url, api, model_id: "synthetic-model" });
    assert.deepEqual(f.calls[0].body.conversation_ids, ["synthetic-conversation"]);
    assert.equal(f.calls[0].body.model_sharing_confirmed, true);
    assert.equal(f.calls[1].body.requested_model, f.modelKey);
    assert.equal(f.calls[1].body.service_id, "wechat-review");
  });

  test(`${api}: empty selection and cancellation send nothing without a consent checkbox`, async () => {
    const f = fixture(api);
    f.context.wechatState.selected.clear();
    await assert.rejects(f.context.createWechatReview(), /至少选择/);
    f.context.wechatState.selected.add("synthetic-conversation");
    f.context.confirmAction = async () => false;
    await f.context.createWechatReview();
    assert.deepEqual(f.calls, []);
  });

  test(`${api}: hidden selections are disclosed and chat library context is not sent`, async () => {
    const f = fixture(api);
    f.context.wechatState.rows[0].display_name = '合成销售群';
    f.context.workbench = { filterGroups: () => [], created() {}, selection: () => ({library_selection:{entity_id:'do-not-send'}}) };
    await f.context.createWechatReview();
    assert.match(f.confirmations[0].detail,/合成销售群/);
    assert.match(f.confirmations[0].detail,/1 个在当前筛选中不可见/);
    assert.equal(f.calls[1].body.library_selection,undefined);
  });

  test(`${api}: unavailable models cannot create a scope`, async () => {
    const f = fixture(api); f.provider.status = "unavailable";
    await assert.rejects(f.context.createWechatReview(), /配置并选择/);
    assert.deepEqual(f.calls, []);
  });
}
