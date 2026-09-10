// Executes real production functions with DOM doubles; not a visual browser test.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../ui/app.js", import.meta.url), "utf8");
const html = readFileSync(new URL("../ui/index.html", import.meta.url), "utf8");
const ids = Array.from(html.matchAll(/\bid="([^"]+)"/g), (match) => match[1]);
const parsed = ts.createSourceFile("app.js", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
const names = new Set(["wxDecipherStatus", "renderWxDecipherControls", "renderWxDecipherFiles", "runWxDecipher", "exportSelectedWechat", "refreshWxProcesses", "clearWxMedia", "restoreWxMedia"]);
const hooks = ["wxdecipher-add-files", "wxdecipher-file-input", "wxdecipher-clear-files", "wxdecipher-run", "wxdecipher-snapshot", "wechat-ownership", "wechat-export-selected", "wxdecipher-process-refresh", "wxdecipher-capture-confirm", "wxdecipher-media-choose", "wxdecipher-media-input", "wxdecipher-media-clear"];
const pieces = [];
function visit(node) {
  if (ts.isFunctionDeclaration(node) && names.has(node.name?.text)) pieces.push(node.getText(parsed));
  if (ts.isExpressionStatement(node) && hooks.some((id) => node.getText(parsed).startsWith(`$("${id}").on`) || node.getText(parsed).startsWith(`$("${id}").addEventListener(`))) pieces.push(node.getText(parsed));
  ts.forEachChild(node, visit);
}
visit(parsed);

class Element {
  children = []; value = ""; checked = false; disabled = false; textContent = ""; files = []; attributes = {};
  classList = { toggle() {}, add() {}, remove() {} };
  constructor(tag = "div") { this.tagName = tag.toUpperCase(); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(type, handler) { this["on" + type] = handler; }
  querySelectorAll(selector) { return this.children.flatMap((child) => [child, ...child.querySelectorAll(selector)]).filter((item) => selector.split(",").includes(item.tagName.toLowerCase())); }
  click() { this.clicked = true; this.onclick?.(); }
  remove() {}
}

function fixture() {
  const elements = new Map();
  const $ = (id) => {
    assert.equal(ids.filter((value) => value === id).length, 1, `Control exists exactly once: ${id}`);
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  const state = { files: [], busy: false, exporting: false };
  const mediaState = { busy: false, urls: [] };
  const wechatState = { selected: new Set(), messages: [], loaded: false, revision: "" };
  const calls = []; const notices = [];
  const context = vm.createContext({ $, wxdecipherState: state, wxmediaState: mediaState, wechatState, model: { wechat: { decipher: { decrypt_available: true } } },
    document: { createElement: (tag) => new Element(tag), body: new Element("body") },
    api: async (url, options) => { calls.push([url, options]); throw new Error("offline"); },
    load: async () => {}, renderWechatSummary() {}, wechatFilters: () => ({ date_from: "2026-09-01", date_to: "2026-09-09", query: "报价" }),
    confirmAction: async () => true, note: (...args) => notices.push(args),
    URL: { createObjectURL: () => "blob:synthetic", revokeObjectURL() {} }, Blob, atob, requestToken: "synthetic-token", setTimeout: (callback) => callback(),
  });
  vm.runInContext(pieces.join("\n"), context);
  $("wxdecipher-cipher-mode").value = "auto"; $("wechat-export-format").value = "jsonl";
  return { context, $, state, mediaState, wechatState, calls, notices };
}

test("file selection is additive and accepts selected WAL but rejects SHM, duplicates, and oversized batches", () => {
  const { $, state } = fixture();
  $("wxdecipher-file-input").files = [{ name: "message_0.db", size: 4096 }];
  $("wxdecipher-file-input").onchange();
  assert.equal(state.files.length, 1);
  $("wxdecipher-file-input").files = [{ name: "contact.db", size: 4096 }];
  $("wxdecipher-file-input").onchange(); assert.equal(state.files.length, 2);
  $("wxdecipher-file-input").files = [{ name: "MESSAGE_0.DB", size: 4096 }];
  $("wxdecipher-file-input").onchange(); assert.equal(state.files.length, 2);
  assert.match($("wxdecipher-status").textContent, /同名/);
  $("wxdecipher-file-input").files = [{ name: "message.db-shm", size: 4096 }];
  $("wxdecipher-file-input").onchange(); assert.equal(state.files.length, 2);
  $("wxdecipher-file-input").files = [{ name: "huge.db", size: 300 * 1024 * 1024 }];
  $("wxdecipher-file-input").onchange(); assert.equal(state.files.length, 2);
  $("wxdecipher-file-input").files = [{ name: "message_0.db-wal", size: 0 }];
  $("wxdecipher-file-input").onchange(); assert.equal(state.files.length, 3);
});

test("decoding is gated by both consent controls and invalid keys never leave the page", async () => {
  const { context, $, state, calls } = fixture();
  state.files = [{ file: { name: "message_0.db", size: 4096 }, key: "" }];
  context.renderWxDecipherControls(); assert.equal($("wxdecipher-run").disabled, true);
  await context.runWxDecipher(); assert.equal(calls.length, 0);
  $("wechat-ownership").checked = true; $("wxdecipher-snapshot").checked = true;
  $("wxdecipher-key").value = "not-a-valid-key";
  await context.runWxDecipher(); assert.equal(calls.length, 0);
  assert.match($("wxdecipher-status").textContent, /64 位/);
  $("wxdecipher-key").value = "";
  await context.runWxDecipher(); assert.equal(calls.length, 0);
  assert.match($("wxdecipher-status").textContent, /本人微信 ID/);
});

test("unsupported local-user isolation disables all new sensitive upload controls", () => {
  const { context, $, state } = fixture();
  state.files = [{ file: { name: "message.db", size: 4096 }, key: "" }];
  $("wechat-ownership").checked = true; $("wxdecipher-snapshot").checked = true; $("wxdecipher-capture-confirm").checked = true;
  context.model.wechat.http_available = false;
  context.renderWxDecipherControls();
  for (const id of ["choose-wechat-export", "wxdecipher-media-choose", "wxdecipher-add-files", "wxdecipher-run", "wxdecipher-process-refresh"]) {
    assert.equal($(id).disabled, true, id);
  }
  assert.match($("wxdecipher-dependencies").textContent, /用户隔离尚未就绪/);
});

test("successful decode uploads bytes before keys, clears secrets, and revokes model consent", async () => {
  const { context, $, state, wechatState } = fixture();
  const calls = [];
  state.files = [{ file: { name: "message_0.db", size: 4096 }, key: "a".repeat(64) }];
  $("wechat-ownership").checked = true; $("wxdecipher-snapshot").checked = true;
  $("wechat-model-sharing").checked = true; $("wxdecipher-key").value = "b".repeat(64);
  $("wechat-account").value = "合成微信";
  $("wxdecipher-self-id").value = "wxid_synthetic_self";
  context.api = async (url, options) => {
    calls.push([url, options]);
    assert.equal($("wxdecipher-key").value, "");
    if (url.endsWith("/sessions")) return { session_id: "session" };
    if (url.endsWith("/upload")) { assert.equal(options.body.name, "message_0.db"); assert.doesNotMatch(JSON.stringify(options.headers), /a{64}|b{64}/); return {}; }
    if (url.endsWith("/run")) return { message: "已导入", decipher: { messages: 3, databases: [{}], warnings: ["媒体占位"] } };
    throw new Error("Unexpected API call");
  };
  await context.runWxDecipher();
  assert.deepEqual(calls.map(([url]) => url), ["/api/wechat/decipher/sessions", "/api/wechat/decipher/upload", "/api/wechat/decipher/run"]);
  assert.equal(JSON.parse(calls[2][1].body).file_keys["message_0.db"], "a".repeat(64));
  assert.equal(state.files.length, 0); assert.equal(state.busy, false);
  assert.equal($("wechat-model-sharing").checked, false);
  assert.equal(wechatState.selected.size, 0);
  assert.match($("wxdecipher-status").textContent, /媒体占位/);
});

test("upload failure discards staging and retains file selections but not keys for retry", async () => {
  const { context, $, state } = fixture();
  const calls = [];
  state.files = [{ file: { name: "message_0.db", size: 4096 }, key: "a".repeat(64) }];
  $("wechat-ownership").checked = true; $("wxdecipher-snapshot").checked = true;
  $("wxdecipher-self-id").value = "wxid_synthetic_self";
  context.api = async (url) => {
    calls.push(url);
    if (url.endsWith("/sessions")) return { session_id: "session" };
    if (url.endsWith("/discard")) return {};
    throw new Error("上传中断");
  };
  await context.runWxDecipher();
  assert.equal(calls.at(-1), "/api/wechat/decipher/discard");
  assert.equal(state.files.length, 1); assert.equal(state.files[0].key, ""); assert.equal(state.busy, false);
  assert.match($("wxdecipher-status").textContent, /上传中断/);
});

test("local export requires explicit selection and confirmation and freezes the shown filters", async () => {
  const { context, $, wechatState, calls } = fixture();
  await context.exportSelectedWechat(); assert.equal(calls.length, 0);
  wechatState.selected.add("wxconv-synthetic");
  context.confirmAction = async () => false;
  await context.exportSelectedWechat(); assert.equal(calls.length, 0);
  let captured;
  context.confirmAction = async () => true;
  context.api = async (url, options) => { captured = [url, JSON.parse(options.body)]; return { content: "{}\n", filename: "WXDecipher-selected.jsonl", mime_type: "application/x-ndjson", message_count: 1 }; };
  await context.exportSelectedWechat();
  assert.equal(captured[0], "/api/wechat/export");
  assert.equal(captured[1].query, "报价");
  assert.deepEqual(captured[1].conversation_ids, ["wxconv-synthetic"]);
  assert.equal($("wechat-model-sharing").checked, false);
});

test("WAL needs a paired main file and explicit replay confirmation before any request", async () => {
  const { context, $, state, calls } = fixture();
  $("wechat-ownership").checked = true; $("wxdecipher-snapshot").checked = true; $("wxdecipher-self-id").value = "wxid_self";
  state.files = [{ file: { name: "message.db-wal", size: 32 }, key: "" }];
  await context.runWxDecipher(); assert.equal(calls.length, 0); assert.match($("wxdecipher-status").textContent, /同名主库/);
  state.files.push({ file: { name: "message.db", size: 4096 }, key: "" });
  await context.runWxDecipher(); assert.equal(calls.length, 0); assert.match($("wxdecipher-status").textContent, /离线重放/);
  $("wxdecipher-wal-confirm").checked = true;
  const captured = [];
  context.api = async (url, options) => { captured.push([url, options]); return url.endsWith("/sessions") ? { session_id: "synthetic" } : url.endsWith("/run") ? { decipher: {}, message: "done" } : {}; };
  await context.runWxDecipher();
  assert.equal(JSON.parse(captured.find(([url]) => url.endsWith("/run"))[1].body).wal_replay_confirmed, true);
});

test("process listing is not automatic and capture selection is one-use without returning keys", async () => {
  const { context, $, state, calls } = fixture();
  await context.refreshWxProcesses(); assert.equal(calls.length, 0);
  $("wechat-ownership").checked = true; $("wxdecipher-capture-confirm").checked = true;
  const captured = [];
  context.api = async (url, options) => {
    captured.push([url, options]);
    if (url.endsWith("/processes")) return { processes: [{ name: "Weixin.exe", process_id: 123, created_at: "456" }], message: "synthetic" };
    if (url.endsWith("/sessions")) return { session_id: "synthetic" };
    if (url.endsWith("/capture-consent")) return { consent_token: "synthetic-one-use-consent" };
    if (url.endsWith("/run")) return { decipher: { key_capture: { verified_databases: 1 } }, message: "done" };
    return {};
  };
  await context.refreshWxProcesses(); assert.equal(captured.length, 1);
  assert.equal($("wxdecipher-process").value, "", "Process must never be auto-selected");
  $("wxdecipher-snapshot").checked = true; $("wxdecipher-self-id").value = "wxid_self";
  state.files = [{ file: { name: "message.db", size: 4096 }, key: "" }];
  await context.runWxDecipher(); assert.equal(captured.length, 1);
  $("wxdecipher-process").value = $("wxdecipher-process").children[1].value;
  await context.runWxDecipher();
  const payload = JSON.parse(captured.find(([url]) => url.endsWith("/run"))[1].body);
  assert.deepEqual(payload.auto_capture, { process_id: 123, created_at: "456", confirmed: true, consent_token: "synthetic-one-use-consent" });
  assert.deepEqual(captured.map(([url]) => url.split("/").at(-1)), ["processes", "sessions", "upload", "capture-consent", "run"]);
  assert.equal($("wxdecipher-capture-confirm").checked, false);
  assert.equal($("wxdecipher-process").value, "");
});

test("media recovery validates inputs, clears image secrets, reports partial failure and revokes preview URLs", async () => {
  const { context, $, mediaState, calls } = fixture();
  const files = [{ name: "selected.dat", size: 100 }, { name: "broken.dat", size: 1 }];
  await context.restoreWxMedia(files); assert.equal(calls.length, 0);
  $("wechat-ownership").checked = true;
  $("wxdecipher-image-key").value = "not-a-key";
  await context.restoreWxMedia(files); assert.equal(calls.length, 0);
  $("wxdecipher-image-key").value = "0123456789abcdef";
  const captured = [];
  context.fetch = async (url, options) => {
    captured.push([url, options]); assert.equal($("wxdecipher-image-key").value, "");
    if (options.body.name === "broken.dat") throw new Error("unsupported fixture");
    return { ok: true, headers: { get: () => JSON.stringify({ mime_type: "image/png", previewable: true, filename: "synthetic.png", bytes: 5, decoder: "synthetic" }) }, blob: async () => new Blob(["hello"], { type: "image/png" }) };
  };
  await context.restoreWxMedia(files);
  assert.equal(captured.length, 2);
  assert.equal(captured[0][0], "/api/wechat/decipher/media");
  assert.equal(captured[0][1].headers["X-WXDecipher-Image-Key"], "0123456789abcdef");
  assert.match($("wxdecipher-media-status").textContent, /1\/2/);
  assert.equal(mediaState.urls.length, 1);
  assert.equal($("wechat-model-sharing").checked, false);
  const revoked = []; context.URL.revokeObjectURL = (url) => revoked.push(url);
  context.clearWxMedia(); assert.deepEqual(revoked, ["blob:synthetic"]);
  assert.equal(mediaState.urls.length, 0);
});
