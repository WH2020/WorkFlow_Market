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
const names = new Set(["validWxSourcePreferences", "saveWxSourcePreferences", "forgetWxSourcePreferences", "initializeWxSourcePreferences", "wxDecipherStatus", "renderWxStorageDirectory", "renderWxDecipherControls", "renderWxDatabaseDiscovery", "appendWxDecipherEntries", "discoverWxDatabases", "addDiscoveredWxDatabases", "renderWxDecipherFiles", "requestWxStorageAccess", "copyWxDatabases", "clearWxCopiedDatabases", "runWxDecipher", "exportSelectedWechat", "refreshWxProcesses", "clearWxMedia", "restoreWxMedia"]);
const hooks = ["wxdecipher-auto-discover", "wxdecipher-add-files", "wxdecipher-discovery-add", "wxdecipher-discovery-dismiss", "wxdecipher-file-input", "wxdecipher-clear-files", "wxdecipher-run", "wxdecipher-snapshot", "wechat-ownership", "wechat-export-selected", "wxdecipher-process-refresh", "wxdecipher-capture-confirm", "wxdecipher-media-choose", "wxdecipher-media-input", "wxdecipher-media-clear"];
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

function fixture(saved = new Map()) {
  const elements = new Map();
  const $ = (id) => {
    assert.equal(ids.filter((value) => value === id).length, 1, `Control exists exactly once: ${id}`);
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  const state = { files: [], busy: false, exporting: false, captureLoading: false, discoveryLoading: false, discoveryGroups: [], discoveryPlatform: "" };
  const mediaState = { busy: false, urls: [] };
  const wechatState = { selected: new Set(), messages: [], loaded: false, revision: "" };
  const calls = []; const notices = [];
  const context = vm.createContext({ $, wxdecipherState: state, wxmediaState: mediaState, wechatState, model: { wechat: { decipher: { decrypt_available: true } } },
    localStorage: { getItem: (key) => saved.get(key) ?? null, setItem: (key, value) => saved.set(key, value), removeItem: (key) => saved.delete(key) },
    document: { createElement: (tag) => new Element(tag), body: new Element("body") },
    api: async (url, options) => { calls.push([url, options]); throw new Error("offline"); },
    load: async () => {}, renderWechatSummary() {}, wechatFilters: () => ({ date_from: "2026-09-01", date_to: "2026-09-09", query: "报价" }),
    confirmAction: async () => true, note: (...args) => notices.push(args),
    URL: { createObjectURL: () => "blob:synthetic", revokeObjectURL() {} }, Blob, atob, requestToken: "synthetic-token", setTimeout: (callback) => callback(),
  });
  vm.runInContext(pieces.join("\n"), context);
  context.initializeWxSourcePreferences();
  $("wxdecipher-cipher-mode").value = "auto"; $("wechat-export-format").value = "jsonl";
  return { context, $, state, mediaState, wechatState, calls, notices, saved };
}

test("manual source hints survive reopening without restoring files, keys or consent", () => {
  for (const directory of ["E:\\synthetic\\微信记录\\wxid_test", "/Users/synthetic/WeChat Files/wxid_test"]) {
    const first = fixture();
    first.$("wxdecipher-directory-path").value = directory;
    first.$("wxdecipher-directory-path").oninput();
    first.$("wxdecipher-self-id").value = "wxid_test";
    first.$("wxdecipher-key").value = "synthetic-secret-key";
    first.$("wechat-ownership").checked = true;
    first.$("wxdecipher-snapshot").checked = true;
    first.state.files = [{ file: { name: "message.db", size: 4096 }, key: "synthetic-file-key" }];
    markCopied(first.state);
    first.$("wxdecipher-self-id").onchange();
    const stored = JSON.parse(first.saved.get("agent4market.wechat-source.v1"));
    assert.deepEqual(stored, { version: 1, directory, selfId: "wxid_test" });
    assert.equal(first.saved.size, 1);
    const next = fixture(first.saved);
    assert.equal(next.$("wxdecipher-directory-path").value, directory);
    assert.equal(next.$("wxdecipher-self-id").value, "wxid_test");
    assert.equal(next.$("wxdecipher-key").value, "");
    for (const id of ["wechat-ownership", "wxdecipher-snapshot", "wxdecipher-wal-confirm", "wxdecipher-capture-confirm"]) assert.equal(next.$(id).checked, false);
    assert.equal(next.state.files.length, 0);
    assert.equal(next.state.copied, undefined);
    assert.equal(next.state.discoveryGroups.length, 0);
    assert.equal(next.calls.length, 0, "Restoring preferences never requests discovery, copy or parsing");
    assert.match(next.$("wxdecipher-source-memory-status").textContent, /已恢复上次/u);
  }
  assert.match(source, /initializeWxSourcePreferences\(\);\s*initializeAppUpdates\(\);/u, "Restore once at startup, not on periodic render");
});

test("editing, clearing fields and forgetting replace or remove only source hints", () => {
  const f = fixture();
  f.saved.set("unrelated-preference", "keep");
  f.$("wxdecipher-self-id").value = "wxid_first"; f.$("wxdecipher-self-id").oninput();
  f.$("wxdecipher-self-id").value = "wxid_second"; f.$("wxdecipher-self-id").oninput();
  assert.equal(fixture(f.saved).$("wxdecipher-self-id").value, "wxid_second");
  f.$("wxdecipher-self-id").value = ""; f.$("wxdecipher-self-id").oninput();
  assert.equal(f.saved.has("agent4market.wechat-source.v1"), false);
  f.$("wxdecipher-directory-path").value = "E:\\synthetic"; f.$("wxdecipher-directory-path").oninput();
  f.state.copied = {}; f.context.renderWxDecipherControls();
  assert.equal(f.$("wxdecipher-source-forget").disabled, true);
  f.context.forgetWxSourcePreferences();
  assert.equal(f.saved.has("agent4market.wechat-source.v1"), true);
  f.state.copied = null;
  f.$("wxdecipher-source-forget").onclick();
  assert.equal(f.saved.has("agent4market.wechat-source.v1"), false);
  assert.equal(f.saved.get("unrelated-preference"), "keep");
  assert.equal(f.$("wxdecipher-directory-path").value, "");
  assert.equal(fixture(f.saved).$("wxdecipher-directory-path").value, "");
  assert.equal(f.calls.length, 0);
});

test("corrupt, unsupported and oversized preferences are ignored safely", () => {
  for (const saved of ["broken json", "null", JSON.stringify({ version: 2, directory: "E:\\synthetic", selfId: "wxid_test" }),
    JSON.stringify({ version: 1, directory: "x".repeat(4097), selfId: "wxid_test" }),
    JSON.stringify({ version: 1, directory: "E:\\synthetic", selfId: "x".repeat(129) }),
    JSON.stringify({ version: 1, directory: "bad\npath", selfId: "wxid_test" }), "x".repeat(32769)]) {
    const f = fixture(new Map([["agent4market.wechat-source.v1", saved]]));
    assert.equal(f.$("wxdecipher-directory-path").value, "");
    assert.equal(f.$("wxdecipher-self-id").value, "");
    assert.match(f.$("wxdecipher-source-memory-status").textContent, /无法恢复/u);
    assert.equal(f.calls.length, 0);
  }
});

test("unavailable storage and invalid edits do not block manual inputs or claim a successful save", () => {
  const f = fixture();
  f.context.localStorage = { getItem() { throw new Error("unavailable"); }, setItem() { throw new Error("quota"); }, removeItem() { throw new Error("unavailable"); } };
  f.context.initializeWxSourcePreferences();
  f.$("wxdecipher-directory-path").value = "E:\\synthetic";
  f.$("wxdecipher-directory-path").oninput();
  assert.match(f.$("wxdecipher-source-memory-status").textContent, /无法记住/u);
  f.$("wxdecipher-source-forget").onclick();
  assert.match(f.$("wxdecipher-source-memory-status").textContent, /未能清除/u);
  assert.equal(f.$("wxdecipher-directory-path").value, "E:\\synthetic");
  f.$("wxdecipher-self-id").value = "x".repeat(129); f.$("wxdecipher-self-id").oninput();
  assert.match(f.$("wxdecipher-source-memory-status").textContent, /未保存/u);
  assert.equal(f.calls.length, 0);
});

function markCopied(state) {
  state.copied = { sessionId: "synthetic", directory: "C:\\Chosen\\synthetic", fileCount: state.files.length, expiresAt: Date.now() + 1800000 };
}

test("storage location is visible before copying and uses only verified backend state", () => {
  const f = fixture();
  f.context.renderWxDecipherControls();
  assert.match(f.$("wxdecipher-storage-directory").textContent, /正在读取/u);
  for (const directory of ["E:\\Synthetic private data\\微信副本", "/Users/synthetic/微信数据库", "E:\\" + "long-directory".repeat(70)]) {
    f.context.model.wechat.storage = { status: "configured", configured: true, directory };
    f.context.renderWxDecipherControls();
    assert.ok(f.$("wxdecipher-storage-directory").textContent.includes(directory));
    assert.equal(f.$("wxdecipher-storage-setup").textContent, "更改专用数据目录");
    assert.equal(f.state.copied, undefined);
  }
  f.context.model.wechat.storage = { status: "default", configured: false, directory: "E:\\Synthetic\\data\\wechat" };
  f.context.renderWxDecipherControls();
  assert.match(f.$("wxdecipher-storage-directory").textContent, /尚未设置专用数据目录/u);
  assert.match(f.$("wxdecipher-storage-directory").textContent, /E:\\Synthetic\\data\\wechat/u);
  assert.equal(f.$("wxdecipher-storage-setup").textContent, "申请专用数据目录");
  for (const status of ["error", "unavailable"]) {
    f.context.model.wechat.storage = { status, directory: null, message: "Synthetic permission error" };
    f.context.renderWxDecipherControls();
    assert.equal(f.$("wxdecipher-storage-directory").textContent, "Synthetic permission error");
    assert.doesNotMatch(f.$("wxdecipher-storage-directory").textContent, /E:\\/u, "Do not retain a stale path after permissions fail");
  }
  assert.equal(f.calls.length, 0, "Rendering never requests permission or reads database files");
});

test("confirmed storage selection refreshes the persistent location display", async () => {
  const f = fixture();
  const directory = "E:\\Synthetic\\chosen-store";
  f.context.api = async () => ({ configured: true, directory, message: "已配置" });
  f.context.confirmAction = async () => directory;
  let loads = 0;
  f.context.load = async () => { loads++; f.context.model.wechat.storage = { status: "configured", configured: true, directory }; };
  await f.context.requestWxStorageAccess();
  assert.equal(loads, 1);
  assert.ok(f.$("wxdecipher-storage-directory").textContent.includes(directory));
});

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

test("automatic discovery requires ownership and adds exactly one platform account without reading file bytes", async () => {
  const { context, $, state, calls } = fixture();
  await context.discoverWxDatabases();
  assert.equal(calls.length, 0);
  assert.match($("wxdecipher-status").textContent, /本人账号/);
  $("wechat-ownership").checked = true;
  context.api = async (url, options) => {
    assert.equal(url, "/api/wechat/decipher/discover");
    assert.deepEqual(JSON.parse(options.body), { ownership_confirmed: true });
    return { platform_label: "Windows", message: "找到 1 个", groups: [{ group_id: "group-1", label: "Windows · wxid_test", files: [
      { candidate_id: "a".repeat(32), name: "contact.db", bytes: 4096 },
      { candidate_id: "b".repeat(32), name: "message_0.db", bytes: 8192 },
    ] }] };
  };
  await context.discoverWxDatabases();
  assert.equal(state.files.length, 0, "Discovery must not select or copy files automatically");
  assert.equal($("wxdecipher-discovery").hidden, false);
  $("wxdecipher-discovery-group").value = "group-1";
  context.addDiscoveredWxDatabases();
  assert.equal(state.files.length, 2);
  assert.equal(state.files[0].candidateId, "a".repeat(32));
  assert.equal(state.discoveryGroups.length, 0);
});

test("decoding is gated by both consent controls and invalid keys never leave the page", async () => {
  const { context, $, state, calls } = fixture();
  state.files = [{ file: { name: "message_0.db", size: 4096 }, key: "" }];
  context.renderWxDecipherControls(); assert.equal($("wxdecipher-run").disabled, true);
  await context.runWxDecipher(); assert.equal(calls.length, 0);
  $("wechat-ownership").checked = true; $("wxdecipher-snapshot").checked = true;
  markCopied(state);
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
  for (const id of ["choose-wechat-export", "wxdecipher-media-choose", "wxdecipher-auto-discover", "wxdecipher-add-files", "wxdecipher-run", "wxdecipher-process-refresh"]) {
    assert.equal($(id).disabled, true, id);
  }
  assert.match($("wxdecipher-dependencies").textContent, /用户隔离尚未就绪/);
});

test("manual directory search validates empty input and sends the supplied directory", async () => {
  const { context, $, calls, state } = fixture();
  $("wechat-ownership").checked = true;
  await context.discoverWxDatabases("  ");
  assert.equal(calls.length, 0);
  let sent;
  context.api = async (url, options) => { sent = JSON.parse(options.body); return { groups: [], message: "未找到数据库" }; };
  await context.discoverWxDatabases("E:\\synthetic\\xwechat_files\\wxid_test");
  assert.equal(sent.directory, "E:\\synthetic\\xwechat_files\\wxid_test");
  assert.equal(sent.ownership_confirmed, true);
  assert.equal(state.discoveryLoading, false);
  assert.match($("wxdecipher-status").textContent, /未找到/);
});

test("storage permission is granted only after confirming the displayed target", async () => {
  const { context, $, state } = fixture();
  const calls = [];
  context.api = async (url, options) => {
    calls.push([url, options]);
    return url.endsWith("/plan") ? { configured: false, directory: "synthetic-private-directory", confirmation_token: "ticket", message: "Create private store" } : { message: "已创建专用目录" };
  };
  context.confirmAction = async (options) => { assert.match(options.message, /synthetic-private-directory/); return false; };
  await context.requestWxStorageAccess();
  assert.equal(calls.length, 1);
  assert.equal(state.storageLoading, false);
  context.confirmAction = async (options) => options.inputValue ?? true;
  await context.requestWxStorageAccess();
  assert.equal(calls.at(-1)[0], "/api/wechat/storage/grant");
  assert.deepEqual(JSON.parse(calls.at(-1)[1].body), { confirmed: true, confirmation_token: "ticket" });
  assert.match($("wxdecipher-status").textContent, /已创建/);
});

test("custom storage path is replanned and the final normalized target is confirmed before grant", async () => {
  const { context, $ } = fixture();
  const calls = []; const dialogs = [];
  context.api = async (url, options) => {
    calls.push([url, JSON.parse(options.body)]);
    if (url.endsWith("/grant")) return { directory: "C:\\Private\\Chosen", message: "已创建" };
    return calls.length === 1 ? { configured: true, directory: "C:\\Private\\Old", message: "已启用" }
      : { configured: false, directory: "C:\\Private\\Chosen", confirmation_token: "custom-ticket", message: "先复制再解析" };
  };
  context.confirmAction = async (options) => {
    dialogs.push(options);
    return options.inputValue !== undefined ? "C:\\Private\\Chosen" : true;
  };
  await context.requestWxStorageAccess();
  assert.deepEqual(calls.map(([url]) => url), ["/api/wechat/storage/plan", "/api/wechat/storage/plan", "/api/wechat/storage/grant"]);
  assert.deepEqual(calls[1][1], { directory: "C:\\Private\\Chosen" });
  assert.deepEqual(calls[2][1], { confirmed: true, confirmation_token: "custom-ticket" });
  assert.match(dialogs[1].message, /C:\\Private\\Chosen/);
  assert.match($("wxdecipher-status").textContent, /C:\\Private\\Chosen/);
});

test("canceling final custom storage confirmation does not grant or retry import", async () => {
  const { context } = fixture();
  const calls = [];
  context.api = async (url, options) => { calls.push(url); return { directory: JSON.parse(options.body).directory || "default", confirmation_token: "ticket" }; };
  context.confirmAction = async (options) => options.inputValue !== undefined ? "custom" : false;
  await context.requestWxStorageAccess();
  assert.deepEqual(calls, ["/api/wechat/storage/plan", "/api/wechat/storage/plan"]);
});

test("unsafe staging failure offers permission after cleanup without retrying database import", async () => {
  const { context, $, state } = fixture();
  state.files = [{ file: { name: "message_0.db", size: 4096 }, key: "" }];
  $("wechat-ownership").checked = true; $("wxdecipher-snapshot").checked = true; $("wxdecipher-self-id").value = "wxid_self";
  const calls = [];
  context.api = async (url) => {
    calls.push(url);
    if (url.endsWith("/sessions")) throw Object.assign(new Error("synthetic privacy error"), { code: "UNSAFE_PATH" });
    return { directory: "synthetic-private-directory", message: "Create private store", confirmation_token: "ticket" };
  };
  context.confirmAction = async () => false;
  await context.copyWxDatabases();
  assert.deepEqual(calls, ["/api/wechat/decipher/sessions", "/api/wechat/storage/plan"]);
  assert.equal(state.files.length, 1);
  assert.equal(state.busy, false);
});

test("automatic discovery imports opaque candidates server-side while manual files still upload bytes", async () => {
  const { context, $, state } = fixture();
  const calls = [];
  state.files = [
    { file: { name: "contact.db", size: 4096 }, candidateId: "a".repeat(32), key: "" },
    { file: { name: "message_0.db", size: 8192 }, candidateId: "b".repeat(32), key: "" },
    { file: { name: "session.db", size: 4096 }, key: "" },
  ];
  $("wechat-ownership").checked = true; $("wxdecipher-snapshot").checked = true; $("wxdecipher-self-id").value = "wxid_self";
  context.api = async (url, options) => {
    calls.push([url, options]);
    if (url.endsWith("/sessions")) return { session_id: "synthetic" };
    if (url.endsWith("/finish-copy")) return { expires_in_seconds: 1800 };
    return {};
  };
  await context.copyWxDatabases();
  assert.deepEqual(calls.map(([url]) => url.split("/").at(-1)), ["sessions", "import-discovered", "upload", "finish-copy"]);
  assert.equal(JSON.parse(calls[0][1].body).retain_copies, true);
  assert.equal(state.copied.sessionId, "synthetic");
  const discovered = JSON.parse(calls[1][1].body);
  assert.deepEqual(discovered.candidate_ids, ["a".repeat(32), "b".repeat(32)]);
  assert.equal(calls[2][1].body.name, "session.db");
});

test("successful parse only reads copied session, clears secrets, and clears model selection scope", async () => {
  const { context, $, state, wechatState } = fixture();
  const calls = [];
  state.files = [{ file: { name: "message_0.db", size: 4096 }, key: "a".repeat(64) }];
  markCopied(state);
  $("wechat-ownership").checked = true; $("wxdecipher-snapshot").checked = true;
  $("wxdecipher-key").value = "b".repeat(64);
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
  assert.deepEqual(calls.map(([url]) => url), ["/api/wechat/decipher/run"]);
  assert.equal(JSON.parse(calls[0][1].body).file_keys["message_0.db"], "a".repeat(64));
  assert.equal(state.files.length, 1); assert.equal(state.busy, false);
  assert.equal(state.copied.sessionId, "synthetic");
  assert.doesNotMatch(html, /id="wechat-model-sharing"/);
  assert.equal(wechatState.selected.size, 0);
  assert.match($("wxdecipher-status").textContent, /媒体占位/);
});

test("automatic database copy displays its target and copy failure never starts parsing", async () => {
  const { context, $, state } = fixture();
  state.files = [{ file: { name: "message_0.db", size: 4096 }, candidateId: "a".repeat(32), key: "" }];
  $("wechat-ownership").checked = true; $("wxdecipher-snapshot").checked = true; $("wxdecipher-self-id").value = "wxid_synthetic";
  const calls = [];
  context.api = async (url) => {
    calls.push(url.split("/").at(-1));
    if (url.endsWith("/sessions")) return { session_id: "synthetic", working_directory: "C:\\Chosen\\decipher\\synthetic" };
    if (url.endsWith("/import-discovered")) {
      assert.match($("wxdecipher-status").textContent, /正在自动复制/);
      assert.match($("wxdecipher-status").textContent, /C:\\Chosen\\decipher\\synthetic/);
      throw new Error("复制期间源文件发生变化");
    }
    if (url.endsWith("/discard")) return {};
    throw new Error("must not parse after a failed copy");
  };
  await context.copyWxDatabases();
  assert.deepEqual(calls, ["sessions", "import-discovered", "discard"]);
  assert.equal(state.files.length, 1);
  assert.match($("wxdecipher-status").textContent, /源文件发生变化/);
});

test("copy upload failure discards partial staging and keeps selection for retry", async () => {
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
  await context.copyWxDatabases();
  assert.equal(calls.at(-1), "/api/wechat/decipher/discard");
  assert.equal(state.files.length, 1); assert.equal(state.busy, false);
  assert.equal(state.copied, undefined);
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
  assert.doesNotMatch(html, /id="wechat-model-sharing"/);
});

test("parse requires a ready copy and retry sends only the same copied session", async () => {
  const { context, $, state } = fixture();
  state.files = [{ file: { name: "message.db", size: 4096 }, key: "" }];
  $("wechat-ownership").checked = true; $("wxdecipher-snapshot").checked = true; $("wxdecipher-self-id").value = "wxid_self";
  const calls = [];
  context.api = async (url, options) => { calls.push([url, JSON.parse(options.body)]); throw new Error("密钥错误"); };
  await context.runWxDecipher();
  assert.equal(calls.length, 0); assert.match($("wxdecipher-status").textContent, /先点击.*复制/);
  markCopied(state);
  context.renderWxDecipherControls();
  assert.equal($("wxdecipher-copy").disabled, true);
  assert.equal($("wxdecipher-run").disabled, false);
  assert.equal($("wxdecipher-add-files").disabled, true);
  const original = state.copied;
  await context.runWxDecipher();
  await context.runWxDecipher();
  assert.deepEqual(calls.map(([url]) => url), ["/api/wechat/decipher/run", "/api/wechat/decipher/run"]);
  assert.equal(calls[0][1].session_id, calls[1][1].session_id);
  assert.equal(state.copied, original);
  state.copied.expiresAt = 0;
  await context.runWxDecipher();
  assert.equal(calls.length, 2);
});

test("copy ignores keys and process consent, and clear requires confirmation", async () => {
  const { context, $, state } = fixture();
  state.files = [{ file: { name: "message.db", size: 4096 }, key: "do-not-send" }];
  $("wechat-ownership").checked = true; $("wxdecipher-snapshot").checked = true; $("wxdecipher-self-id").value = "wxid_self";
  $("wxdecipher-key").value = "invalid-not-used-for-copy"; $("wxdecipher-capture-confirm").checked = true;
  const calls = [];
  context.api = async (url, options) => {
    calls.push(url.split("/").at(-1));
    assert.doesNotMatch(JSON.stringify(options), /do-not-send|invalid-not-used/);
    if (url.endsWith("/sessions")) return { session_id: "synthetic" };
    if (url.endsWith("/finish-copy")) return { expires_in_seconds: 1800 };
    return {};
  };
  await context.copyWxDatabases();
  assert.deepEqual(calls, ["sessions", "upload", "finish-copy"]);
  assert.ok(state.copied);
  context.confirmAction = async () => false;
  await context.clearWxCopiedDatabases(); assert.equal(calls.length, 3);
  context.confirmAction = async () => true;
  await context.clearWxCopiedDatabases();
  assert.equal(calls.at(-1), "discard"); assert.equal(state.copied, null); assert.equal(state.files.length, 0);
});

test("WAL needs a paired main file and explicit replay confirmation before any request", async () => {
  const { context, $, state, calls } = fixture();
  $("wechat-ownership").checked = true; $("wxdecipher-snapshot").checked = true; $("wxdecipher-self-id").value = "wxid_self";
  state.files = [{ file: { name: "message.db-wal", size: 32 }, key: "" }];
  markCopied(state);
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
    if (url.endsWith("/processes")) return { processes: [{ name: "微信", process_id: 123, created_at: "456", version: "4.1.13.34", architecture: "arm64", profile_id: "wechat-macos-4.1" }], message: "synthetic" };
    if (url.endsWith("/sessions")) return { session_id: "synthetic" };
    if (url.endsWith("/capture-consent")) return { consent_token: "synthetic-one-use-consent" };
    if (url.endsWith("/run")) return { decipher: { key_capture: { verified_databases: 1 } }, message: "done" };
    return {};
  };
  await context.refreshWxProcesses(); assert.equal(captured.length, 1);
  assert.equal($("wxdecipher-process").value, "", "Process must never be auto-selected");
  assert.match($("wxdecipher-process").children[1].textContent, /v4\.1\.13\.34 · arm64 · wechat-macos-4\.1/);
  $("wxdecipher-snapshot").checked = true; $("wxdecipher-self-id").value = "wxid_self";
  state.files = [{ file: { name: "message.db", size: 4096 }, key: "" }];
  markCopied(state);
  await context.runWxDecipher(); assert.equal(captured.length, 1);
  $("wxdecipher-process").value = $("wxdecipher-process").children[1].value;
  await context.runWxDecipher();
  const payload = JSON.parse(captured.find(([url]) => url.endsWith("/run"))[1].body);
  assert.deepEqual(payload.auto_capture, { process_id: 123, created_at: "456", confirmed: true, consent_token: "synthetic-one-use-consent" });
  assert.deepEqual(captured.map(([url]) => url.split("/").at(-1)), ["processes", "capture-consent", "run"]);
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
  assert.doesNotMatch(html, /id="wechat-model-sharing"/);
  const revoked = []; context.URL.revokeObjectURL = (url) => revoked.push(url);
  context.clearWxMedia(); assert.deepEqual(revoked, ["blob:synthetic"]);
  assert.equal(mediaState.urls.length, 0);
});
