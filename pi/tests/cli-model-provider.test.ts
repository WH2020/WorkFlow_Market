import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Type } from "typebox";
import type { Context, Model, Api } from "@earendil-works/pi-ai";
import { cliArguments, cliEnvironment, cliLaunchSha256, cliLaunchSha256Async, cliModelCatalog, cliModelThinking, cliOutputSchema, cliPayload, createCliStream, parseCliResult, type CliBackend } from "../extensions/cli-model-provider.ts";

const tool = { name: "lookup_fixture", description: "Only synthetic data", parameters: Type.Object({ label: Type.String() }) };
const context: Context = { systemPrompt: "Only this task", messages: [{ role: "user", content: "合成请求", timestamp: 1 }], tools: [tool] };
const envelope = (text = "完成", calls: unknown[] = []) => ({ text, tool_calls: calls });
const call = { name: "lookup_fixture", arguments_json: '{"label":"合成"}' };
function wire(api: string, value: unknown) {
  return api === "claude-code" ? JSON.stringify({ type: "result", subtype: "success", is_error: false, structured_output: value, usage: { input_tokens: 12, output_tokens: 8 } })
    : [ { type: "thread.started", thread_id: "synthetic-thread" }, { type: "item.completed", item: { type: "agent_message", text: JSON.stringify(value) } }, { type: "turn.completed", usage: { input_tokens: 12, output_tokens: 8 } } ].map((item) => JSON.stringify(item)).join("\n");
}
function fixture(api: "claude-code" | "codex-cli") {
  const entry: CliBackend = { id: `agent4market-${api}-fixture`, api, base_url: api === "claude-code" ? "https://api.anthropic.com" : "https://chatgpt.com/backend-api/codex", command: process.execPath, executable_path: process.execPath, args: [], version: "fixture", runner_policy_version: 1, launch_sha256: "0".repeat(64), models: [{ id: "synthetic-model" }] };
  const model = { id: "synthetic-model", provider: entry.id, api, baseUrl: entry.base_url } as Model<Api>;
  return { entry, model };
}

test("CLI environment is an explicit allowlist, not a copy of workbench keys and routing", () => {
  const env = cliEnvironment({ Path: "synthetic-path", USERPROFILE: "synthetic-home", OPENAI_API_KEY: "private-canary", ANTHROPIC_API_KEY: "private-canary", HTTP_PROXY: "private-proxy", NODE_OPTIONS: "--require evil", CODEX_HOME: "redirected-home", CLAUDE_CONFIG_DIR: "redirected-home", AGENT4MARKET_KEY: "private-canary" });
  assert.equal(env.Path, "synthetic-path");
  assert.ok(!JSON.stringify(env).includes("private-canary"));
  for (const key of ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HTTP_PROXY", "NODE_OPTIONS", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "AGENT4MARKET_KEY"]) assert.equal(env[key], undefined);
  assert.equal(env.ANTHROPIC_BASE_URL, "https://api.anthropic.com");
});

test("Codex catalog thinking levels map exactly and never alias ultra or allow off implicitly", () => {
  const { entry } = fixture("codex-cli");
  entry.models[0] = { id: "synthetic-model", cli_reasoning: { supported_efforts: ["low", "high", "max", "ultra"], default_effort: "low" }, default_thinking_level: "high" };
  const policy = cliModelThinking(entry.models[0], entry.api);
  assert.deepEqual(policy.levels, ["low", "high", "max"]); assert.equal(policy.defaultLevel, "high");
  assert.equal(policy.thinkingLevelMap.max, "max"); assert.equal(policy.thinkingLevelMap.off, null);
  for (const level of policy.levels) {
    const args = cliArguments(entry, "synthetic-model", {}, level);
    const assignments = args.filter((value) => value.startsWith("model_reasoning_effort="));
    assert.deepEqual(assignments, [`model_reasoning_effort="${level}"`]);
  }
  for (const level of ["off", "medium", "ultra", 'high"injection']) {
    assert.throws(() => cliArguments(entry, "synthetic-model", {}, level as any), /不支持/u);
  }
  assert.throws(() => cliModelThinking({ ...entry.models[0], default_thinking_level: "medium" }, entry.api), /默认/u);
});

test("Codex stream forwards selected reasoning into argv and restricted catalog; invalid choice executes zero times", async () => {
  const { entry, model } = fixture("codex-cli");
  entry.models[0] = { id: model.id, cli_reasoning: { supported_efforts: ["low", "high", "max", "ultra"], default_effort: "low" }, default_thinking_level: "high" };
  let calls = 0;
  const stream = createCliStream(entry, async (request) => {
    calls++;
    assert.ok(request.args.includes('model_reasoning_effort="max"'));
    const catalog = request.catalog as ReturnType<typeof cliModelCatalog>;
    const native = catalog.models[0];
    assert.equal(native.default_reasoning_level, "max");
    assert.deepEqual(native.supported_reasoning_levels.map((item) => item.effort), ["low", "high", "max"]);
    assert.equal(native.apply_patch_tool_type, null); assert.deepEqual(native.experimental_supported_tools, []);
    return { code: 0, stdout: wire(entry.api, envelope()) };
  }, () => {});
  const valid = await stream(model, context, { reasoning: "max" }).result();
  assert.equal(valid.stopReason, "stop"); assert.equal(calls, 1);
  for (const reasoning of [undefined, "medium", "ultra"]) {
    const result = await stream(model, context, { reasoning: reasoning as any }).result();
    assert.equal(result.stopReason, "error"); assert.match(result.errorMessage!, /不支持/u);
    assert.equal(calls, 1);
  }
});

for (const api of ["claude-code", "codex-cli"] as const) {
  test(`${api}: production arguments disable native tools and persistence, fix model and do not contain task text`, () => {
    const { entry } = fixture(api);
    const args = cliArguments(entry, "synthetic-model", cliOutputSchema([tool.name]));
    assert.equal(args[args.indexOf("--model") + 1], "synthetic-model");
    if (api === "claude-code") {
      assert.equal(args[args.indexOf("--tools") + 1], "");
      for (const flag of ["--safe-mode", "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands", "--no-chrome"]) assert.ok(args.includes(flag));
    } else {
      for (const flag of ["--strict-config", "--ignore-rules", "--ignore-user-config", "--ephemeral", "--sandbox", "--output-schema", "tools.update_plan.enabled=false", "tools.experimental_request_user_input.enabled=false"]) assert.ok(args.includes(flag));
      for (const feature of ["shell_tool", "code_mode", "browser_use", "plugins", "multi_agent", "view_image", "skill_search"]) assert.equal(args[args.indexOf(feature) - 1], "--disable");
      assert.ok(args.includes('forced_login_method="chatgpt"'));
      assert.ok(args.includes('model_catalog_json="__A4M_CLI_MODEL_CATALOG__"'));
    }
    assert.ok(!args.join(" ").includes("合成请求"));
  });

  test(`${api}: single validated tool call becomes a Pi event with a host-generated ID`, async () => {
    const { entry, model } = fixture(api);
    let sent = ""; const order: string[] = [];
    const stream = createCliStream(entry, async (request) => { order.push("spawn"); sent = request.input; return { code: 0, stdout: wire(api, envelope("", [call])) }; }, () => {})(model, context, {
      onPayload: (payload: any) => { order.push("payload"); return { ...payload, system_prompt: "REPLACED_PAYLOAD" }; },
      onResponse: () => { order.push("response"); },
    });
    const result = await stream.result();
    assert.deepEqual(order, ["payload", "spawn", "response"]);
    assert.match(sent, /REPLACED_PAYLOAD/); assert.ok(!sent.includes("Only this task"));
    assert.equal(result.stopReason, "toolUse");
    const part = result.content[0];
    assert.equal(part.type, "toolCall");
    if (part.type === "toolCall") { assert.match(part.id, /^cli_[a-f0-9]{32}$/); assert.equal(part.name, tool.name); assert.deepEqual(part.arguments, { label: "合成" }); }
    assert.equal(result.usage.totalTokens, 20);
  });

  test(`${api}: lifecycle veto and wrong recipient cause zero executions`, async () => {
    const { entry, model } = fixture(api); let executions = 0;
    const run = createCliStream(entry, async () => { executions++; throw new Error("must not run"); }, () => {});
    const controller = new AbortController();
    assert.equal((await run(model, context, { signal: controller.signal, onPayload: () => { controller.abort(); return {}; } }).result()).stopReason, "aborted");
    assert.equal((await run(model, context, { onPayload: () => ({}) }).result()).stopReason, "error");
    assert.equal((await run({ ...model, baseUrl: "https://wrong.example" }, context).result()).stopReason, "error");
    assert.equal(executions, 0);
  });

  test(`${api}: errors, binary changes, image input and late responses cannot produce tools or fallback`, async () => {
    const { entry, model } = fixture(api); let executions = 0;
    const executor = async () => { executions++; return { code: 1, stdout: "PRIVATE_CONTEXT_OR_KEY", errorCategory: "auth" }; };
    const error = await createCliStream(entry, executor, () => {})(model, context).result();
    assert.equal(error.stopReason, "error"); assert.deepEqual(error.content, []); assert.ok(!error.errorMessage?.includes("PRIVATE_CONTEXT_OR_KEY"));
    const changed = await createCliStream(entry, executor, () => { throw new Error("PRIVATE_PATH"); })(model, context).result();
    assert.ok(!changed.errorMessage?.includes("PRIVATE_PATH"));
    const imageContext: Context = { messages: [{ role: "user", content: [{ type: "image", data: "private-base64", mimeType: "image/png" }], timestamp: 1 }] };
    assert.equal((await createCliStream(entry, executor, () => {})(model, imageContext).result()).stopReason, "error");
    assert.equal(executions, 1);
    const abort = new AbortController();
    const late = await createCliStream(entry, async () => { abort.abort(); return { code: 0, stdout: wire(api, envelope("", [call])) }; }, () => {})(model, context, { signal: abort.signal }).result();
    assert.equal(late.stopReason, "aborted"); assert.deepEqual(late.content, []);
  });

  test(`${api}: invalid and multiple calls, unknown tools, truncated and trailing JSON fail closed`, () => {
    for (const value of [envelope("", [call, call]), envelope("", [{ ...call, name: "Bash" }]), envelope("", [{ ...call, arguments_json: "[]" }]), envelope("", [{ ...call, arguments_json: '{"label":42}' }]), envelope("", [{ ...call, arguments_json: '{"label":"ok","extra":"undeclared"}' }]), envelope("", [{ ...call, arguments_json: "{partial" }]), envelope("", []), { ...envelope(), extra: true }]) {
      assert.throws(() => parseCliResult(api, wire(api, value), context));
    }
    assert.throws(() => parseCliResult(api, "{partial", context));
    assert.throws(() => parseCliResult(api, wire(api, envelope()) + "\nnot-json", context));
    assert.throws(() => parseCliResult(api, "x".repeat(2 * 1024 * 1024 + 1), context));
  });
}

const codexEvents = (...events: unknown[]) => events.map((event) => JSON.stringify(event)).join("\n");
const reconnect = { type: "error", message: "Reconnecting... 2/5 (request timed out)" };
const transportWarning = { type: "item.completed", item: { id: "diagnostic", type: "error", message: "Synthetic transport fallback warning" } };

test("Codex reconnect diagnostics do not discard one subsequently completed structured response", () => {
  for (const diagnostics of [[reconnect], [transportWarning], [reconnect, reconnect, transportWarning]]) {
    for (const value of [envelope(), envelope("", [call])]) {
      const result = parseCliResult("codex-cli", codexEvents(...diagnostics) + "\n" + wire("codex-cli", value), context);
      assert.equal(result.text, value.text);
      assert.equal(result.calls.length, value.tool_calls.length);
      assert.deepEqual(result.usage, { input_tokens: 12, output_tokens: 8 });
    }
  }
});

test("Codex diagnostics alone, missing completion, and terminal failures still fail closed", () => {
  const reply = { type: "item.completed", item: { type: "agent_message", text: JSON.stringify(envelope()) } };
  const failed = { type: "turn.failed", error: { message: "PRIVATE_DIAGNOSTIC_CANARY" } };
  for (const stdout of [
    codexEvents(reconnect), codexEvents(transportWarning), codexEvents(reconnect, reply),
    codexEvents(reconnect, { type: "turn.completed" }),
    codexEvents(reconnect, failed) + "\n" + wire("codex-cli", envelope()),
    wire("codex-cli", envelope()) + "\n" + codexEvents(failed),
  ]) {
    assert.throws(() => parseCliResult("codex-cli", stdout, context), (error: Error) => {
      assert.ok(!error.message.includes("PRIVATE_DIAGNOSTIC_CANARY")); return true;
    });
  }
});

test("Codex text and reasoning lifecycle events remain compatible during recovery", () => {
  const stdout = codexEvents(
    { type: "thread.started", thread_id: "synthetic" }, { type: "turn.started" }, reconnect,
    ...["item.started", "item.updated", "item.completed"].map((type) => ({ type, item: { id: "thinking", type: "reasoning", text: "Synthetic reasoning" } })),
    { type: "item.started", item: { id: "reply", type: "agent_message", text: "" } },
    { type: "item.updated", item: { id: "reply", type: "agent_message", text: "{partial" } },
    { type: "item.completed", item: { id: "reply", type: "agent_message", text: JSON.stringify(envelope()) } },
    { type: "turn.completed" },
  );
  assert.deepEqual(parseCliResult("codex-cli", stdout, context), { text: "完成", calls: [], usage: undefined });
});

test("Codex recovery rejects malformed and unknown events rather than guessing their meaning", () => {
  for (const event of [null, [], {}, { type: 1 }, { type: "unknown" }, { type: "item.unknown", item: { type: "error" } },
    { type: "item.started", item: null }, { type: "item.completed", item: { type: "agent_message" } }]) {
    assert.throws(() => parseCliResult("codex-cli", codexEvents(reconnect, event) + "\n" + wire("codex-cli", envelope()), context));
  }
});

test("Codex recovery cannot hide malformed envelopes, multiple replies, or events after completion", () => {
  const reply = { type: "item.completed", item: { type: "agent_message", text: JSON.stringify(envelope()) } };
  for (const stdout of [
    codexEvents(reconnect, { ...reply, item: { ...reply.item, text: "not-json" } }, { type: "turn.completed" }),
    codexEvents(reconnect, reply, reply, { type: "turn.completed" }),
    codexEvents(reconnect, { ...reply, item: { ...reply.item, text: JSON.stringify(envelope("", [{ ...call, name: "Bash" }])) } }, { type: "turn.completed" }),
    ...[reconnect, transportWarning, reply, { type: "turn.started" }, { type: "turn.completed" }]
      .map((trailing) => wire("codex-cli", envelope()) + "\n" + codexEvents(trailing)),
  ]) assert.throws(() => parseCliResult("codex-cli", stdout, context));
});

test("Codex native tool items at every lifecycle stage remain forbidden after a reconnect", () => {
  for (const type of ["item.started", "item.updated", "item.completed"]) {
    for (const itemType of ["command_execution", "file_change", "mcp_tool_call", "web_search", "todo_list", "unknown_tool"]) {
      const bad = { type, item: { id: "forbidden", type: itemType, command: "not-run" } };
      const stdout = codexEvents(reconnect, bad) + "\n" + wire("codex-cli", envelope("", [call]));
      assert.throws(() => parseCliResult("codex-cli", stdout, context), /原生工具/);
    }
  }
});

test("Codex stream emits only validated content after recovery and never exposes diagnostics", async () => {
  const { entry, model } = fixture("codex-cli"); let executions = 0;
  const diagnostic = { type: "error", message: "PRIVATE_DIAGNOSTIC_CANARY" };
  const stdout = codexEvents(diagnostic, transportWarning) + "\n" + wire("codex-cli", envelope());
  for (const code of [0, 1]) {
    const result = await createCliStream(entry, async () => { executions++; return { code, stdout }; }, () => {})(model, context).result();
    assert.equal(result.stopReason, code === 0 ? "stop" : "error");
    assert.deepEqual(result.content, code === 0 ? [{ type: "text", text: "完成" }] : []);
    assert.ok(!JSON.stringify(result).includes("PRIVATE_DIAGNOSTIC_CANARY"));
  }
  assert.equal(executions, 2, "exactly one execution per request; no adapter retry or fallback");
});

test("tool schemas close nested object fields but preserve explicitly declared maps", () => {
  const nested = { name: "nested", description: "Synthetic", parameters: Type.Object({ record: Type.Object({ label: Type.String() }), labels: Type.Record(Type.String(), Type.String()) }) };
  const nestedContext = { ...context, tools: [nested] };
  const build = (args: unknown) => wire("claude-code", envelope("", [{ name: "nested", arguments_json: JSON.stringify(args) }]));
  assert.throws(() => parseCliResult("claude-code", build({ record: { label: "ok", extra: "rejected" }, labels: {} }), nestedContext));
  const value = { record: { label: "ok" }, labels: { any_declared_map_key: "allowed" } };
  assert.deepEqual(parseCliResult("claude-code", build(value), nestedContext).calls[0].arguments, value);
  const sent = cliPayload(fixture("claude-code").model, nestedContext).tools[0].parameters;
  assert.equal(sent.additionalProperties, false);
  assert.equal(sent.properties.record.additionalProperties, false);
  assert.ok(sent.properties.labels.patternProperties || sent.properties.labels.additionalProperties === true);
});

test("runtime binary hash has the same canonical identity and can be aborted during streaming", async () => {
  const root = mkdtempSync(join(tmpdir(), "a4m-cli-hash-"));
  try {
    const path = join(root, "synthetic.bin"); writeFileSync(path, Buffer.alloc(3 * 1024 * 1024, 37));
    const entry = { executable_path: path, command: path, args: [] };
    assert.equal(await cliLaunchSha256Async(entry), cliLaunchSha256(entry));
    const abort = new AbortController(); const hashing = cliLaunchSha256Async(entry, abort.signal);
    setImmediate(() => abort.abort());
    await assert.rejects(hashing);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("an aborted or third concurrent verification cannot start a CLI process", async () => {
  const { entry, model } = fixture("claude-code"); const abort = new AbortController(); let executions = 0;
  let finish: () => void = () => {}; const waiting = new Promise<void>((resolve) => { finish = resolve; });
  const run = createCliStream(entry, async () => { executions++; return { code: 0, stdout: wire("claude-code", envelope()) }; }, () => waiting);
  const first = run(model, context, { signal: abort.signal }); const second = run(model, context, { signal: abort.signal });
  const third = await run(model, context).result();
  assert.equal(third.stopReason, "error"); assert.match(third.errorMessage!, /两个请求/);
  abort.abort(); finish();
  assert.equal((await first.result()).stopReason, "aborted"); assert.equal((await second.result()).stopReason, "aborted"); assert.equal(executions, 0);
});

test("Codex native catalog preserves any selected model ID and cannot enable model-specific patch tools", () => {
  for (const id of ["gpt-5.5", "synthetic-unknown-model"]) {
    const { models: [model] } = cliModelCatalog(id, 48000);
    assert.equal(model.slug, id); assert.equal(model.display_name, id);
    assert.equal(model.context_window, 48000); assert.equal(model.max_context_window, 48000);
    assert.equal(model.shell_type, "disabled"); assert.equal(model.apply_patch_tool_type, null);
    assert.deepEqual(model.experimental_supported_tools, []); assert.equal(model.tool_mode, null);
    assert.equal(model.node_repl_disabled, true); assert.equal(model.use_responses_lite, false);
    assert.match(model.base_instructions, /single-turn model provider/);
  }
});
