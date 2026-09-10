// Actual UI functions with a DOM double; browser layout is tested separately.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../ui/app.js", import.meta.url), "utf8");
const html = readFileSync(new URL("../ui/index.html", import.meta.url), "utf8");
const ids = [...html.matchAll(/\bid="([^"]+)"/gu)].map((match) => match[1]);
const parsed = ts.createSourceFile("app.js", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
const names = new Set(["syncAppUpdateState", "appUpdateTime", "renderAppUpdates", "checkAppUpdates", "openAppUpdateRelease", "initializeAppUpdates"]);
const pieces = [];
function visit(node) {
  if (ts.isFunctionDeclaration(node) && names.has(node.name?.text)) pieces.push(node.getText(parsed));
  ts.forEachChild(node, visit);
}
visit(parsed);

const latest = { tag: "v0.20.3", version: "0.20.3", name: "Synthetic release", published_at: "2026-09-10T00:00:00Z", notes: "Synthetic notes" };
const snapshot = (extra = {}) => ({ instance_id: "fixture", revision: 0, status: "not_checked", checking: false,
  current_version: "0.20.2", latest: null, retry_after_seconds: 0, auto_retry_after_seconds: 0,
  can_open_release: false, update_available: false, checked_at: null, ...extra });

class Element {
  textContent = ""; disabled = false; checked = false; hidden = false;
  classes = new Set();
  classList = { toggle: (name, value) => value ? this.classes.add(name) : this.classes.delete(name) };
  content = { cloneNode: () => ({ cloned: true }) };
  set innerHTML(_value) { throw new Error("Remote release notes must not become HTML"); }
  scrollIntoView() { this.scrolled = true; }
  insertBefore() {}
}

function fixture(preference = null) {
  const elements = new Map(), calls = [], confirmations = [], timers = [], documentEvents = new Map(), windowEvents = new Map(), saved = new Map();
  let now = 0, responder = async () => snapshot({ revision: 1, status: "up_to_date" }), confirm = async () => true;
  class FakeDate extends Date { static now() { return now; } }
  const $ = (id) => {
    assert.equal(ids.filter((value) => value === id).length, 1, `control ${id} exists exactly once`);
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  const context = vm.createContext({ $, Date: FakeDate, Number, Boolean, String, JSON,
    document: { hidden: false, querySelector: () => new Element(), addEventListener: (name, fn) => documentEvents.set(name, fn) },
    window: { addEventListener: (name, fn) => windowEvents.set(name, fn) },
    localStorage: { getItem: () => preference, setItem: (key, value) => saved.set(key, value) },
    setInterval: (fn, milliseconds) => timers.push({ fn, milliseconds }), switchView: () => {},
    api: async (path, options) => { calls.push({ path, options }); return responder(path, options); },
    confirmAction: async (options) => { confirmations.push(options); return confirm(options); },
  });
  vm.runInContext(`let model = { app_updates: ${JSON.stringify(snapshot())} }; let requestToken = "synthetic";
    let appUpdateState = null, appUpdateRequestBusy = false, appUpdateOpening = false, appUpdateLocalError = "", appUpdateAutoEnabled = true, appUpdateLastAutoAttempt = null;
    ${pieces.join("\n")}
    globalThis.testUI = { render: renderAppUpdates, check: checkAppUpdates, open: openAppUpdateRelease, init: initializeAppUpdates,
      sync: syncAppUpdateState, setModel: (value) => { model.app_updates = value; }, state: () => appUpdateState,
      setEnabled: (value) => { appUpdateAutoEnabled = value; }, error: () => appUpdateLocalError };`, context);
  return { ui: context.testUI, $, calls, confirmations, timers, saved, documentEvents, windowEvents,
    advance: (milliseconds) => { now += milliseconds; }, respond: (fn) => { responder = fn; }, confirm: (fn) => { confirm = fn; } };
}

test("update UI exposes current version, notes and a badge without rendering remote HTML", () => {
  const f = fixture();
  f.ui.setModel(snapshot({ revision: 2, status: "available", update_available: true, can_open_release: true,
    latest: { ...latest, notes: '<img src=x onerror="evil()">\n<script>evil()</script>' } }));
  f.ui.render();
  assert.match(f.$("app-update-current-version").textContent, /0\.20\.2/u);
  assert.match(f.$("app-update-status").textContent, /发现新版本 0\.20\.3/u);
  assert.equal(f.$("app-updates-badge").hidden, false);
  assert.equal(f.$("app-update-open-release").disabled, false);
  assert.match(f.$("app-update-release-notes").textContent, /<script>evil/u);
});

test("unavailable, latest and ahead states never pretend that an error is up to date", () => {
  const f = fixture();
  for (const [status, text] of [["up_to_date", "最新版本"], ["current_ahead", "不需要降级"], ["error", "Synthetic 404"]]) {
    f.ui.sync(snapshot({ instance_id: status, status, error: { message: "Synthetic 404" } }));
    f.ui.setModel(null); f.ui.render();
    assert.ok(f.$("app-update-status").textContent.includes(text));
    assert.equal(f.$("app-updates-badge").hidden, true);
    assert.equal(f.$("app-update-open-release").disabled, true);
    if (status === "error") assert.doesNotMatch(f.$("app-update-status").textContent, /最新版本/u);
  }
});

test("late bootstrap snapshots cannot replace a newer check response", () => {
  const f = fixture();
  f.ui.sync(snapshot({ revision: 5, status: "available", update_available: true, can_open_release: true, latest }));
  f.ui.setModel(snapshot({ revision: 4, status: "checking", checking: true }));
  f.ui.render();
  assert.match(f.$("app-update-status").textContent, /发现新版本/u);
  assert.equal(f.ui.state().revision, 5);
});

test("startup and periodic checks are spaced five minutes apart and can be disabled", async () => {
  const f = fixture();
  f.ui.init();
  await f.ui.check();
  assert.equal(f.calls.length, 1, "first startup check works even with a zero fixture clock");
  f.advance(60_000); await f.timers[0].fn();
  assert.equal(f.calls.length, 1);
  f.advance(240_001); await f.timers[0].fn();
  assert.equal(f.calls.length, 2);
  f.ui.setEnabled(false); f.advance(300_001); await f.timers[0].fn();
  assert.equal(f.calls.length, 2);
  await f.ui.check(true);
  assert.equal(f.calls.length, 3, "manual checking remains available when auto checking is off");
  assert.equal(f.timers[0].milliseconds, 60_000);
});

test("remembered opt-out suppresses startup and persists checkbox changes", async () => {
  const f = fixture("false");
  f.ui.init(); f.ui.render(); await f.ui.check();
  assert.equal(f.calls.length, 0);
  assert.equal(f.$("app-update-auto").checked, false);
  f.$("app-update-auto").checked = true;
  f.$("app-update-auto").onchange();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(f.saved.get("agent4market-auto-updates"), "true");
  assert.equal(f.calls.length, 1);
});

test("in-flight, rate-limit and automatic backoff states suppress redundant requests", async () => {
  const f = fixture();
  for (const fields of [{ checking: true }, { retry_after_seconds: 90 }, { auto_retry_after_seconds: 600 }]) {
    f.ui.sync(snapshot({ instance_id: JSON.stringify(fields), ...fields }));
    await f.ui.check();
  }
  assert.equal(f.calls.length, 0);
  f.ui.sync(snapshot({ instance_id: "new", status: "error", auto_retry_after_seconds: 600 }));
  await f.ui.check(true);
  assert.equal(f.calls.length, 1, "manual retry is distinct from automatic backoff");
});

test("opening a release requires confirmation, only submits the frozen tag and coalesces clicks", async () => {
  const f = fixture();
  f.ui.setModel(null);
  f.ui.sync(snapshot({ status: "available", can_open_release: true, latest }));
  let finish;
  f.confirm(() => new Promise((resolve) => { finish = resolve; }));
  f.respond(async () => ({ message: "Opened synthetic release" }));
  const pending = f.ui.open();
  await f.ui.open();
  assert.equal(f.confirmations.length, 1);
  assert.equal(f.calls.length, 0);
  finish(true); await pending;
  assert.equal(f.calls[0].path, "/api/app-updates/open-release");
  assert.deepEqual(JSON.parse(f.calls[0].options.body), { tag: "v0.20.3" });
  assert.match(f.$("app-update-action-status").textContent, /Opened synthetic/u);
  assert.equal(f.$("app-update-open-release").disabled, false);
});

test("cancelled release opening and failed network checks remain visible and non-mutating", async () => {
  const f = fixture();
  f.ui.setModel(null);
  f.ui.sync(snapshot({ status: "available", can_open_release: true, latest }));
  f.confirm(async () => false);
  await f.ui.open();
  assert.equal(f.calls.length, 0);
  f.respond(async () => { throw new Error("Synthetic disconnected"); });
  await f.ui.check(true);
  assert.match(f.$("app-update-status").textContent, /Synthetic disconnected/u);
  assert.equal(f.calls.length, 1);
  f.ui.sync(snapshot({ revision: 3, status: "up_to_date" })); f.ui.render();
  assert.doesNotMatch(f.$("app-update-status").textContent, /Synthetic disconnected/u);
});
