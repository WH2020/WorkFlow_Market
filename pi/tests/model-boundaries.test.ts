import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { ModelRuntime, ModelRegistry, ExtensionRunner, createExtensionRuntime, convertToLlm } from "@earendil-works/pi-coding-agent";
import { taskScopedMessages, taskCheckpoint, runtimeModelRecipient, managedModelCatalog } from "../extensions/model-selection.ts";
import { createTask, completeLogicalTool, completeModelNode, proposeWriteIntent } from "../extensions/task-runtime.ts";
import { createGovernedSubagentContract, updateGovernedSubagentContract } from "../extensions/subagent-contracts.ts";
import registerGuard from "../extensions/subagent-model-guard.ts";
import { buildGovernedSubagentLaunchForTests, validateGovernedSubagentResultForTests, validateRuntimeWorkflow } from "../extensions/vertical-workflow.ts";
import { routeNewServiceId, assertQuickPresentationRequest } from "../extensions/service-routing.ts";

const recipient = { provider_id: "agent4market-a", base_url: "https://example.com", api: "openai-responses", model_id: "same-model" };
const model = { provider: recipient.provider_id, id: recipient.model_id, baseUrl: "https://example.com/v1", api: recipient.api,
  reasoning: false, input: ["text"], contextWindow: 32000, maxTokens: 4096, name: "fixture", cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } };
const key = `${recipient.provider_id}/${recipient.model_id}`;
const task = () => createTask({ taskId: "task-boundary", sessionKey: "fixture", profileId: "sales-director", serviceId: "fixture",
  request: "当前任务", effectiveModel: key, effectiveRecipient: recipient,
  workflow: { id: "fixture", nodes: [{ id: "draft", type: "agent", depends_on: [] }] } });

test("CLI child guard enforces exact contract thinking before context and provider requests", () => {
  const root = mkdtempSync(join(tmpdir(), "director-child-thinking-"));
  const previous = process.env.AGENT4MARKET_CLI_BACKENDS_FILE;
  delete process.env.AGENT4MARKET_CLI_BACKENDS_FILE;
  try {
    const actual = { ...model, api: "codex-cli", baseUrl: "https://chatgpt.com/backend-api/codex", reasoning: true };
    const contract = createGovernedSubagentContract(root, { task_id: "task-thinking", profile_id: "sales-director", node_id: "review", task_version: 1,
      role: "readonly-reviewer", objective: "Synthetic review", allowed_tools: [], authorized_urls: [], expected_model: key,
      model_recipient: runtimeModelRecipient(actual as never), expected_thinking_level: "high" });
    const handlers = new Map<string, any>(); let thinking = "high"; let aborts = 0;
    registerGuard({ on: (event: string, handler: any) => handlers.set(event, handler), getThinkingLevel: () => thinking } as never);
    const ctx = { cwd: root, model: actual, abort: () => { aborts++; }, sessionManager: { buildContextEntries: () => [] } };
    const prompt = `[DIRECTOR_TASK_CONTEXT task-thinking]\n受管任务：task-thinking\n受管节点：review\ncontract_id：${contract.contract_id}\nSynthetic`;
    handlers.get("before_agent_start")({ prompt }, ctx); assert.equal(aborts, 0);
    const message = { role: "user", content: prompt };
    assert.deepEqual(handlers.get("context")({ messages: [message] }, ctx), { messages: [message] });
    thinking = "low";
    assert.deepEqual(handlers.get("before_provider_request")({}, ctx), {});
    assert.deepEqual(handlers.get("context")({ messages: [message] }, ctx), { messages: [] });
    assert.ok(aborts >= 2);
  } finally {
    if (previous === undefined) delete process.env.AGENT4MARKET_CLI_BACKENDS_FILE; else process.env.AGENT4MARKET_CLI_BACKENDS_FILE = previous;
    rmSync(root, { recursive: true, force: true });
  }
});

test("current task excludes old history and survives a validated local compaction", () => {
  const current = task();
  const marker = { role: "user", content: `[DIRECTOR_TASK_CONTEXT ${current.task_id}] 当前任务` };
  const old = { role: "user", content: "旧供应商私密历史" };
  assert.deepEqual(taskScopedMessages([old, marker], current.task_id), [marker]);
  const checkpoint = { type: "compaction", fromHook: true, ...taskCheckpoint(current) };
  const compacted = { role: "compactionSummary", summary: checkpoint.summary };
  const boundary = { entries: [checkpoint], recipient, model: key, taskVersion: current.version + 2 };
  assert.deepEqual(taskScopedMessages([compacted], current.task_id, boundary), [compacted]);
  assert.deepEqual(taskScopedMessages([compacted, marker], current.task_id, boundary), [marker]);
  for (const invalid of [
    { ...checkpoint, fromHook: false }, { ...checkpoint, summary: checkpoint.summary + "tampered" },
    { ...checkpoint, details: { ...checkpoint.details, task_id: "other-task" } },
    { ...checkpoint, details: { ...checkpoint.details, task_version: 9999 } },
    { ...checkpoint, details: { ...checkpoint.details, effective_model: "other/model" } },
    { ...checkpoint, details: { ...checkpoint.details, effective_recipient: { ...recipient, base_url: "https://other.example" } } },
  ]) assert.throws(() => taskScopedMessages([compacted], current.task_id, { ...boundary, entries: [invalid] }), /边界不可用/);
  assert.throws(() => taskScopedMessages([old], current.task_id), /边界不可用/);
  assert.doesNotMatch(checkpoint.summary, /旧供应商私密历史/);
});

test("child guard accepts its first frozen launch, blocks fallback through the real Pi runner and aborts the agent loop", async () => {
  const root = mkdtempSync(join(tmpdir(), "director-child-boundary-"));
  try {
    const contract = createGovernedSubagentContract(root, { task_id: "task-boundary", profile_id: "sales-director", node_id: "review",
      task_version: 1, role: "readonly-reviewer", objective: "只读复核", allowed_tools: [], authorized_urls: [], expected_model: key, model_recipient: recipient });
    assert.throws(() => updateGovernedSubagentContract(root, contract.contract_id, (value) => ({ ...value, model_recipient: { ...recipient, base_url: "https://other.example" } })), /immutable/);
    const launch = buildGovernedSubagentLaunchForTests({ taskId: contract.task_id, profileId: contract.profile_id, request: "复核", contractId: contract.contract_id,
      node: { id: "review", type: "subagent", depends_on: [], permissions: [], boundary: { objective: "复核", allowed_tools: [], max_turns: 6, write_scope: [] } } as never });
    const handlers = new Map<string, any[]>();
    registerGuard({ on: (event: string, handler: any) => handlers.set(event, [handler]) } as never);
    let aborts = 0;
    let actualModel = model;
    let abortAgent = () => { aborts++; };
    const context = { cwd: root, get model() { return actualModel; }, abort: () => abortAgent(),
      sessionManager: { buildContextEntries: () => [] } };
    const extension = { path: "guard", handlers };
    const runner = new ExtensionRunner([extension] as never, createExtensionRuntime(), root, {} as never, {} as never);
    runner.createContext = () => context as never;
    await runner.emitBeforeAgentStart(launch.task, undefined, "fixture", { cwd: root });
    const currentMessage = { role: "user", content: launch.task, timestamp: Date.now() };
    assert.deepEqual(await runner.emitContext([currentMessage] as never), [currentMessage]);
    assert.equal(aborts, 0);
    actualModel = { ...model, provider: "agent4market-other" };
    assert.deepEqual(await runner.emitContext([currentMessage] as never), []);
    assert.deepEqual(await runner.emitBeforeProviderRequest({ messages: [currentMessage] }), {});
    assert.ok(aborts > 0);
    const { Agent } = await import(new URL("../../pi-agent-core/dist/index.js", import.meta.resolve("@earendil-works/pi-coding-agent")).href);
    let sentMessages: unknown[] | undefined;
    let streamAborted = false;
    const agent = new Agent({ initialState: { model: actualModel, systemPrompt: "fixture" }, convertToLlm,
      transformContext: (messages: any) => runner.emitContext(messages),
      streamFn: (_model: unknown, llmContext: any, options: any) => {
        sentMessages = llmContext.messages;
        streamAborted = options.signal.aborted;
        throw new Error("fixture transport is disabled");
      } });
    abortAgent = () => agent.abort();
    await agent.prompt(currentMessage);
    assert.deepEqual(sentMessages, []);
    assert.equal(streamAborted, true);
    assert.deepEqual(await runner.emit({ type: "session_before_compact" } as never), { cancel: true });
    const pending = { agent: launch.agent, context: launch.context, role: "readonly-reviewer", allowed_tool_names: [] } as const;
    const result = { agent: launch.agent, context: launch.context, exitCode: 0, model: key, finalOutput: "复核完成" };
    assert.equal(validateGovernedSubagentResultForTests({ mode: "single", results: [result] }, pending as never, contract).model, key);
    assert.throws(() => validateGovernedSubagentResultForTests({ mode: "single", results: [{ ...result, modelAttempts: [{ model: "other/fallback" }] }] }, pending as never, contract), /fallback/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("Python registry config is accepted by the real Pi model runtime for all three transports", async () => {
  const root = mkdtempSync(join(tmpdir(), "director-model-integration-"));
  const previous: Record<string, string | undefined> = {};
  try {
    const script = `import json,sys\nfrom pathlib import Path\nfrom agent_platform import model_registry as m\nfrom tests.test_model_provider import public_resolver\nr=Path(sys.argv[1]); r.mkdir(exist_ok=True)\nfor api in sorted(m.APIS):\n m.configure_provider(r, provider_id=None, name=api, vendor='custom', base_url='https://example.com', api_key='fixture-'+api, selected_model='same-model', models=[{'id':'same-model'}], api=api, allow_private_network=False, environ={},home=r/'home',resolver=public_resolver)\nprint(json.dumps(m.runtime_configuration(r,environ={},home=r/'home')))\n`;
    const [, environment] = JSON.parse(execFileSync("python", ["-B", "-c", script, root], { encoding: "utf8" }));
    for (const [name, value] of Object.entries(environment)) { previous[name] = process.env[name]; process.env[name] = String(value); }
    const runtime = await ModelRuntime.create({ authPath: join(root, "auth.json"), modelsPath: join(root, "home", ".pi", "agent", "models.json"), modelsStorePath: join(root, "cache"), allowModelNetwork: false });
    const registry = new ModelRegistry(runtime);
    assert.equal(registry.getError(), undefined);
    const catalog = managedModelCatalog()!;
    assert.equal(Object.keys(catalog).length, 3);
    for (const [fullKey, recipient] of Object.entries(catalog)) {
      const record = recipient as any;
      const found = registry.find(record.provider_id, record.model_id);
      assert.ok(found, fullKey);
      assert.deepEqual(runtimeModelRecipient(found), recipient);
      const auth = await registry.getApiKeyAndHeaders(found);
      assert.equal(auth.ok, true);
      if (auth.ok) assert.equal(auth.apiKey, `fixture-${record.api}`);
    }
    writeFileSync(environment.AGENT4MARKET_MANAGED_MODELS_FILE, "{}");
    assert.throws(() => managedModelCatalog(), /目录已变化/);
  } finally {
    for (const [name, value] of Object.entries(previous)) { if (value === undefined) delete process.env[name]; else process.env[name] = value; }
    rmSync(root, { recursive: true, force: true });
  }
});

test("readonly DAG can complete without a write intent; quick deck still requires an exact approved payload", () => {
  const readWorkflow = JSON.parse(readFileSync("vertical_plugins/market/sales/workflows/pipeline-review-readonly-v2.json", "utf8"));
  validateRuntimeWorkflow(readWorkflow, JSON.parse(readFileSync("vertical_plugins/market/sales/plugin.json", "utf8")));
  let current = createTask({ taskId: "task-ro", sessionKey: "fixture", profileId: "sales-director", serviceId: "sales-review-readonly", workflow: readWorkflow, request: "仅分析" });
  current = completeLogicalTool(current, readWorkflow, "sales.read", current.version);
  current = completeModelNode(current, readWorkflow, "analyze", current.version, "分析结果");
  current = completeModelNode(current, readWorkflow, "validate_analysis", current.version, "只读验收");
  assert.equal(current.status, "completed");
  assert.equal(current.pending_write, undefined);
  const quick = JSON.parse(readFileSync("vertical_plugins/shared/presentation_studio/workflows/presentation-studio-quick-v2.json", "utf8"));
  validateRuntimeWorkflow(quick, JSON.parse(readFileSync("vertical_plugins/shared/presentation_studio/plugin.json", "utf8")));
  assert.equal(quick.nodes.filter((node: any) => node.type === "approval").length, 1);
  assert.equal(quick.nodes.filter((node: any) => node.tool?.startsWith("web.")).length, 0);
  current = createTask({ taskId: "task-quick", sessionKey: "fixture", profileId: "sales-director", serviceId: "presentation-studio-quick", workflow: quick, request: "内部 quick" });
  for (const node of quick.nodes.slice(0, 6)) current = node.type === "tool" ? completeLogicalTool(current, quick, node.tool, current.version) : completeModelNode(current, quick, node.id, current.version);
  assert.throws(() => completeModelNode(current, quick, "validate_and_freeze", current.version), /exact/);
  current = proposeWriteIntent(current, quick, "artifact.deck.write", { output_name: "fixture.pptx" }, current.version);
  current = completeModelNode(current, quick, "validate_and_freeze", current.version);
  assert.equal(current.status, "waiting_approval");
  assert.throws(() => completeLogicalTool(current, quick, "artifact.deck.write", current.version), /current|approval|pending/);
});

test("new service routing is explicit and rejects quick presentation privilege widening", () => {
  assert.equal(routeNewServiceId("sales-review", "仅分析不写入"), "sales-review-readonly");
  assert.equal(routeNewServiceId("industry-research", "形成报告并申请入库"), "industry-research");
  const brief = { schema_version: "1.0", mode: "quick", confidentiality: "internal", scene: "industry", source_scope: "profile-knowledge-only" };
  const request = (value: unknown) => `[PRESENTATION_BRIEF]\n${JSON.stringify(value)}\n[/PRESENTATION_BRIEF]`;
  assert.equal(routeNewServiceId("presentation-studio", request(brief)), "presentation-studio-quick");
  for (const change of [{ scene: "government" }, { confidentiality: "public" }, { mode: "strict" }, { source_scope: "public-web-and-profile-knowledge" }]) {
    assert.throws(() => assertQuickPresentationRequest(request({ ...brief, ...change })), /快速路径/);
  }
});
