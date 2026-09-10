// Exercise actual UI handlers and api(), including Fetch's inferred media type.
// The transport is a local double: no business request, model call or account data.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../ui/app.js", import.meta.url), "utf8");
const parsed = ts.createSourceFile("app.js", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
const names = new Set(["api", "accountId", "matchPlayInput", "acceptRecommendation", "ignoreRecommendation", "loadCustomerSignals"]);
const pieces = [];
function visit(node) {
  if (ts.isFunctionDeclaration(node) && names.has(node.name?.text)) pieces.push(node.getText(parsed));
  ts.forEachChild(node, visit);
}
visit(parsed);
assert.equal(pieces.length, names.size);

function fixture() {
  const calls = [], failures = [], matches = [], refreshes = [], elements = new Map();
  const customerState = { selectedId: "synthetic-account", rows: [{ account_id: "synthetic-account" }],
    signalsLoading: false, signalsGeneration: 0, signals: [] };
  const $ = (id) => {
    if (!elements.has(id)) elements.set(id, {});
    return elements.get(id);
  };
  const context = vm.createContext({ customerState, $, requestToken: "synthetic-token",
    confirmAction: async (options) => { failures.push(options); },
    loadCustomerRecommendations: async (account) => { refreshes.push(account); },
    renderCustomerDetail: () => {}, renderPlayResult: (result, input) => { matches.push({ result, input }); },
    fetch: async (path, options) => {
      const request = new Request("http://127.0.0.1:1" + path, options);
      const contentType = request.headers.get("content-type");
      const body = await request.text();
      calls.push({ path, method: request.method, contentType, body, token: request.headers.get("x-director-token") });
      const ok = request.method === "GET" || contentType === "application/json" || contentType === "application/octet-stream";
      return { ok, json: async () => ok ? { success: true, data: { status: "no_match", signals: [{ severity: "high" }, { severity: "low" }] } }
        : { error: "只接受 JSON 请求" } };
    },
  });
  vm.runInContext(pieces.join("\n") + "\nglobalThis.subject = { api, matchPlayInput, acceptRecommendation, ignoreRecommendation, loadCustomerSignals };", context);
  return { ui: context.subject, calls, failures, matches, refreshes, customerState, $ };
}

function checkJSON(call, route, payload) {
  assert.ok(call, "The UI handler must reach the request transport");
  assert.equal(call.path, route);
  assert.equal(call.method, "POST");
  assert.equal(call.contentType, "application/json");
  assert.equal(call.token, "synthetic-token");
  assert.deepEqual(JSON.parse(call.body), payload);
}

test("quick-command intent request sends JSON and reaches the result renderer", async () => {
  const f = fixture();
  await f.ui.matchPlayInput("synthetic intent test");
  checkJSON(f.calls[0], "/api/a4/match-play", { user_input: "synthetic intent test", account_id: "synthetic-account" });
  assert.equal(f.matches.length, 1);
  assert.doesNotMatch(f.$("play-matcher-result").innerHTML, /只接受 JSON/u);
});

test("accept recommendation preserves edits, JSON media type and request token", async () => {
  const f = fixture();
  assert.equal(await f.ui.acceptRecommendation("synthetic-rec", { note: "synthetic edit" }), true);
  checkJSON(f.calls[0], "/api/a4/recommendations/accept", { recommendation_id: "synthetic-rec", user_edits: { note: "synthetic edit" } });
  assert.deepEqual(f.refreshes, ["synthetic-account"]);
  assert.equal(f.failures.length, 0);
});

test("ignore recommendation preserves reason and JSON media type", async () => {
  const f = fixture();
  assert.equal(await f.ui.ignoreRecommendation("synthetic-rec", "synthetic reason"), true);
  checkJSON(f.calls[0], "/api/a4/recommendations/ignore", { recommendation_id: "synthetic-rec", reason: "synthetic reason" });
  assert.equal(f.failures.length, 0);
});

test("signal request resolves the selected customer and sends JSON", async () => {
  const f = fixture();
  await f.ui.loadCustomerSignals("synthetic-account");
  checkJSON(f.calls[0], "/api/a4/evaluate-signals", { account_id: "synthetic-account" });
  assert.equal(f.customerState.signalsError, "");
  assert.equal(f.customerState.signals.length, 1);
  assert.equal(f.customerState.signalsLoading, false);
});

test("missing customer does not send a signal request", async () => {
  const f = fixture();
  f.customerState.rows = [];
  await f.ui.loadCustomerSignals("missing-synthetic-account");
  assert.equal(f.calls.length, 0);
  assert.equal(f.customerState.signalsError, "客户数据不存在");
  assert.equal(f.customerState.signalsLoading, false);
});

test("all literal api JSON payloads declare a JSON content type", () => {
  const missing = [];
  function scan(node) {
    if (ts.isCallExpression(node) && node.expression.getText(parsed) === "api" && node.arguments[1] && ts.isObjectLiteralExpression(node.arguments[1])) {
      const property = (name) => node.arguments[1].properties.find((item) => item.name?.getText(parsed).replace(/["']/gu, "") === name);
      const body = property("body")?.initializer?.getText(parsed) || "";
      const headers = property("headers")?.initializer?.getText(parsed) || "";
      if (body.startsWith("JSON.stringify(") && !/content-type["']?\s*:\s*["']application\/json/iu.test(headers)) {
        missing.push(node.arguments[0].getText(parsed));
      }
    }
    ts.forEachChild(node, scan);
  }
  scan(parsed);
  assert.deepEqual(missing, []);
});

test("GET and explicit binary upload transport remain unchanged", async () => {
  const f = fixture();
  await f.ui.api("/synthetic-get");
  assert.equal(f.calls[0].contentType, null);
  assert.equal(f.calls[0].token, null);
  await f.ui.api("/synthetic-upload", { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: new Uint8Array([1, 2]) });
  assert.equal(f.calls[1].contentType, "application/octet-stream");
  assert.equal(f.calls[1].token, "synthetic-token");
});
