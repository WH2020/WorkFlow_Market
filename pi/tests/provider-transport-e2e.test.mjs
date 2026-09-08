import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { EventEmitter, once } from "node:events";
import { mkdtempSync, rmSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { basename, join, resolve } from "node:path";
import { after, before, test } from "node:test";
import { Type } from "typebox";
import { createAgentSession, DefaultResourceLoader, ModelRegistry, ModelRuntime, SessionManager, SettingsManager } from "@earendil-works/pi-coding-agent";
import { createGovernedSubagentContract } from "../extensions/subagent-contracts.ts";
import { runtimeModelRecipient } from "../extensions/model-selection.ts";
import registerModelGuard from "../extensions/subagent-model-guard.ts";

// Real Python configuration -> Pi SDK -> HTTP/SSE -> tool execution -> follow-up.
// Only this owned loopback server receives requests; all credentials/data are fake.
const protocols = ["openai-completions", "openai-responses", "anthropic-messages"];
const paths = { "openai-completions": "/v1/chat/completions", "openai-responses": "/v1/responses", "anthropic-messages": "/v1/messages" };
const requests = [];
const received = new EventEmitter();
const previousEnvironment = new Map();
let root;
let server;
let registry;
let runtime;

function event(response, type, payload) {
  response.write(`${type ? `event: ${type}\n` : ""}data: ${JSON.stringify(payload)}\n\n`);
}

function respond(response, api, toolCall, holdOpen = false) {
  response.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache", connection: "close" });
  const text = "隔离联调完成";
  const args = JSON.stringify({ label: "隔离测试" });
  if (api === "openai-completions") {
    const chunk = (delta, finish_reason = null) => event(response, null, { id: "chatcmpl_fixture", object: "chat.completion.chunk", created: 1, model: "same-model", choices: [{ index: 0, delta, finish_reason }] });
    chunk({ role: "assistant", content: "" });
    if (toolCall) {
      chunk({ tool_calls: [{ index: 0, id: "call_fixture", type: "function", function: { name: "lookup_fixture", arguments: args.slice(0, 10) } }] });
      chunk({ tool_calls: [{ index: 0, function: { arguments: args.slice(10) } }] });
    } else chunk({ content: text });
    if (holdOpen) return;
    chunk({}, toolCall ? "tool_calls" : "stop");
    response.write("data: [DONE]\n\n");
  } else if (api === "openai-responses") {
    let sequence = 0;
    const emit = (type, extra) => event(response, type, { type, sequence_number: sequence++, ...extra });
    const item = toolCall
      ? { type: "function_call", id: "fc_fixture", call_id: "call_fixture", name: "lookup_fixture", arguments: args, status: "completed" }
      : { type: "message", id: "msg_fixture", role: "assistant", status: "completed", content: [{ type: "output_text", text, annotations: [] }] };
    const result = { id: "resp_fixture", object: "response", created_at: 1, status: "in_progress", output: [], model: "same-model" };
    emit("response.created", { response: result });
    emit("response.output_item.added", { output_index: 0, item: { ...item, status: "in_progress", ...(toolCall ? { arguments: "" } : { content: [] }) } });
    if (toolCall) {
      for (const delta of [args.slice(0, 10), args.slice(10)]) emit("response.function_call_arguments.delta", { item_id: item.id, output_index: 0, delta });
      emit("response.function_call_arguments.done", { item_id: item.id, output_index: 0, arguments: args });
    } else {
      emit("response.content_part.added", { item_id: item.id, output_index: 0, content_index: 0, part: { type: "output_text", text: "", annotations: [] } });
      emit("response.output_text.delta", { item_id: item.id, output_index: 0, content_index: 0, delta: text });
    }
    if (holdOpen) return;
    emit("response.output_item.done", { output_index: 0, item });
    emit("response.completed", { response: { ...result, status: "completed", output: [item], usage: { input_tokens: 12, output_tokens: 8, total_tokens: 20 } } });
  } else {
    const emit = (type, extra) => event(response, type, { type, ...extra });
    emit("message_start", { message: { id: "msg_fixture", type: "message", role: "assistant", model: "same-model", content: [], stop_reason: null, stop_sequence: null, usage: { input_tokens: 12, output_tokens: 0 } } });
    emit("content_block_start", { index: 0, content_block: toolCall ? { type: "tool_use", id: "call_fixture", name: "lookup_fixture", input: {} } : { type: "text", text: "" } });
    if (toolCall) {
      for (const partial_json of [args.slice(0, 10), args.slice(10)]) emit("content_block_delta", { index: 0, delta: { type: "input_json_delta", partial_json } });
    } else emit("content_block_delta", { index: 0, delta: { type: "text_delta", text } });
    if (holdOpen) return;
    emit("content_block_stop", { index: 0 });
    emit("message_delta", { delta: { stop_reason: toolCall ? "tool_use" : "end_turn", stop_sequence: null }, usage: { output_tokens: 8 } });
    emit("message_stop", {});
  }
  response.end();
}

before(async () => {
  root = mkdtempSync(join(tmpdir(), "agent4market-transport-e2e-"));
  server = createServer(async (request, response) => {
    try {
      let raw = "";
      for await (const chunk of request) {
        raw += chunk;
        if (raw.length > 1_000_000) throw new Error("fixture request too large");
      }
      const api = protocols.find((candidate) => request.url === paths[candidate]);
      const body = JSON.parse(raw);
      const mode = raw.includes("fixture:cancel") ? "cancel" : raw.includes("fixture:reject") ? "reject" : raw.includes("fixture:guard-ok") ? "guard-ok" : "roundtrip";
      const record = { api, url: request.url, method: request.method, headers: request.headers, body, mode };
      requests.push(record);
      if (!api || request.method !== "POST") {
        response.writeHead(404).end("unexpected fixture endpoint");
        return;
      }
      if (mode === "reject") response.writeHead(401, { "content-type": "application/json" }).end(JSON.stringify({ type: "error", error: { type: "authentication_error", message: "fixture rejected credential" } }));
      else respond(response, api, mode === "roundtrip" && !raw.includes("FIXTURE_TOOL_RESULT"), mode === "cancel");
      received.emit(`${api}:${mode}`, record);
    } catch (error) {
      response.writeHead(500).end(String(error));
    }
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = `http://127.0.0.1:${server.address().port}`;
  const script = `import json,sys\nfrom pathlib import Path\nfrom agent_platform import model_registry as m\nr=Path(sys.argv[1])\nfor api in sorted(m.APIS):\n m.configure_provider(r, provider_id=None, name=api, vendor='custom', base_url=sys.argv[2], api_key='fixture-'+api, selected_model='same-model', models=[{'id':'same-model'}], api=api, allow_private_network=True, environ={},home=r/'home')\nprint(json.dumps(m.runtime_configuration(r,environ={},home=r/'home')))\n`;
  const [, environment] = JSON.parse(execFileSync("python", ["-B", "-c", script, root, address], { encoding: "utf8", timeout: 15000 }));
  for (const [name, value] of Object.entries(environment)) {
    previousEnvironment.set(name, process.env[name]);
    process.env[name] = String(value);
  }
  runtime = await ModelRuntime.create({ authPath: join(root, "auth.json"), modelsPath: join(root, "home", ".pi", "agent", "models.json"), modelsStorePath: join(root, "cache"), allowModelNetwork: false });
  registry = new ModelRegistry(runtime);
  assert.equal(registry.getError(), undefined);
});

after(async () => {
  if (server?.listening) {
    const closed = once(server, "close");
    server.close();
    server.closeAllConnections();
    await closed;
  }
  for (const [name, value] of previousEnvironment) {
    if (value === undefined) delete process.env[name];
    else process.env[name] = value;
  }
  if (root) {
    assert.equal(resolve(root, ".."), resolve(tmpdir()));
    assert.ok(basename(root).startsWith("agent4market-transport-e2e-"));
    rmSync(root, { recursive: true, force: true });
  }
});

async function createSession(api, onTool = () => {}, guarded = false) {
  const model = registry.getAll().find((item) => item.provider.startsWith("agent4market-") && item.api === api);
  assert.ok(model, api);
  const settings = SettingsManager.inMemory({ packages: [], extensions: [], compaction: { enabled: false }, retry: { enabled: false, provider: { maxRetries: 0, timeoutMs: 5000 } }, enableAnalytics: false, enableInstallTelemetry: false, transport: "sse" });
  const resourceLoader = new DefaultResourceLoader({ cwd: root, agentDir: join(root, "agent"), settingsManager: settings, noExtensions: true, extensionFactories: guarded ? [registerModelGuard] : [], noSkills: true, noPromptTemplates: true, noThemes: true, noContextFiles: true, systemPrompt: "Offline transport fixture. Use lookup_fixture once, then finish." });
  await resourceLoader.reload();
  const { session } = await createAgentSession({ cwd: root, agentDir: join(root, "agent"), modelRuntime: runtime, model, thinkingLevel: "off", resourceLoader, settingsManager: settings, sessionManager: SessionManager.inMemory(root), tools: guarded ? [] : ["lookup_fixture"], customTools: [{
    name: "lookup_fixture", label: "Offline fixture", description: "Return synthetic test data only.", parameters: Type.Object({ label: Type.String() }),
    execute: async (_id, params) => { onTool(params); return { content: [{ type: "text", text: "FIXTURE_TOOL_RESULT" }], details: {} }; },
  }] });
  await session.bindExtensions({});
  return session;
}

function assertRequest(record, api) {
  assert.equal(record.api, api);
  assert.equal(record.url, paths[api]);
  assert.equal(record.body.model, "same-model");
  assert.equal(record.body.stream, true);
  const key = `fixture-${api}`;
  assert.equal(api === "anthropic-messages" ? record.headers["x-api-key"] : record.headers.authorization, api === "anthropic-messages" ? key : `Bearer ${key}`);
  for (const other of protocols.filter((candidate) => candidate !== api)) assert.ok(!JSON.stringify(record).includes(`fixture-${other}`));
}

function guardedPrompt(session, text, recipient = runtimeModelRecipient(session.model)) {
  const contract = createGovernedSubagentContract(root, {
    task_id: "task-http-boundary", profile_id: "sales-director", node_id: "review", task_version: 1,
    role: "readonly-reviewer", objective: "Synthetic boundary test", allowed_tools: [], authorized_urls: [],
    expected_model: `${session.model.provider}/${session.model.id}`, model_recipient: recipient,
  });
  return `[DIRECTOR_TASK_CONTEXT ${contract.task_id}]\n受管任务：${contract.task_id}\n受管节点：review\ncontract_id：${contract.contract_id}\n${text}`;
}

for (const api of protocols) {
  test(`${api}: real Pi SDK streams a tool call, executes it once, and sends the result back`, { timeout: 15000 }, async () => {
    const calls = [];
    const session = await createSession(api, (args) => calls.push(args));
    const start = requests.length;
    try {
      await session.prompt("fixture:roundtrip 请处理隔离测试");
      const sent = requests.slice(start);
      assert.equal(sent.length, 2);
      sent.forEach((record) => assertRequest(record, api));
      assert.match(JSON.stringify(sent[0].body.tools), /lookup_fixture/);
      assert.match(JSON.stringify(sent[1].body), /FIXTURE_TOOL_RESULT/);
      assert.deepEqual(calls, [{ label: "隔离测试" }]);
      assert.equal(session.messages.at(-1).stopReason, "stop");
      assert.equal(session.messages.at(-1).content.find((part) => part.type === "text")?.text, "隔离联调完成");
    } finally { await session.abort(); session.dispose(); }
  });

  test(`${api}: cancellation ends a live HTTP stream without a follow-up request`, { timeout: 15000 }, async () => {
    const session = await createSession(api);
    const start = requests.length;
    try {
      const accepted = once(received, `${api}:cancel`, { signal: AbortSignal.timeout(7000) });
      const prompt = session.prompt("fixture:cancel");
      await accepted;
      await session.abort();
      await prompt;
      assert.equal(session.isStreaming, false);
      assert.equal(session.messages.at(-1).stopReason, "aborted");
      assert.equal(requests.length - start, 1);
      assertRequest(requests[start], api);
    } finally { await session.abort(); session.dispose(); }
  });

  test(`${api}: rejected credentials surface an error without provider fallback`, { timeout: 15000 }, async () => {
    const session = await createSession(api);
    const start = requests.length;
    try {
      await session.prompt("fixture:reject");
      assert.equal(requests.length - start, 1);
      assertRequest(requests[start], api);
      assert.equal(session.messages.at(-1).stopReason, "error");
      assert.match(session.messages.at(-1).errorMessage, /401|fixture rejected credential/);
    } finally { await session.abort(); session.dispose(); }
  });

  test(`${api}: the real SDK recipient guard aborts before any HTTP request`, { timeout: 15000 }, async () => {
    const session = await createSession(api, () => assert.fail("guarded tool must not run"), true);
    const start = requests.length;
    try {
      await session.prompt(guardedPrompt(session, "fixture:roundtrip PRIVATE_FIXTURE_MUST_NOT_BE_SENT", { ...runtimeModelRecipient(session.model), base_url: "https://different-recipient.example" }));
      assert.equal(requests.length, start, "a changed recipient must never receive the request, even an empty one");
      assert.equal(session.isStreaming, false);
      // Pi 0.84.2 represents cancellation during request preparation as an error,
      // whereas cancellation after the HTTP stream starts is marked "aborted".
      assert.equal(session.messages.at(-1).stopReason, "error");
      assert.match(session.messages.at(-1).errorMessage, /aborted/i);
      assert.equal(session.messages.at(-1).usage.totalTokens, 0);
    } finally { await session.abort(); session.dispose(); }
  });

  test(`${api}: the real SDK recipient guard allows its correctly bound first request`, { timeout: 15000 }, async () => {
    const session = await createSession(api, () => assert.fail("readonly fixture must not call tools"), true);
    const start = requests.length;
    try {
      await session.prompt(guardedPrompt(session, "fixture:guard-ok"));
      assert.equal(requests.length - start, 1);
      assertRequest(requests[start], api);
      assert.equal(session.messages.at(-1).stopReason, "stop", session.messages.at(-1).errorMessage);
      assert.equal(session.messages.at(-1).content.find((part) => part.type === "text")?.text, "隔离联调完成");
    } finally { await session.abort(); session.dispose(); }
  });
}
