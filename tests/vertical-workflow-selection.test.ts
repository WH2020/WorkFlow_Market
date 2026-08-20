import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

import verticalWorkflow, {
  removeAgentRuntimeLease,
  selectWorkbenchRequest,
  validateTaskMessage,
  validateRuntimeWorkflow,
  writeAgentRuntimeLease,
} from "../pi/extensions/vertical-workflow.ts";
import {
  approveNode,
  beginWriteCommit,
  completeLogicalTool,
  completeModelNode,
  createTask,
  proposeWriteIntent,
  type RuntimeWorkflow,
} from "../pi/extensions/task-runtime.ts";

function request(requestId: string, profileId: string, status: "requested" | "accepted" = "requested") {
  return {
    schema_version: "1.0" as const,
    request_id: requestId,
    status,
    profile_id: profileId,
    service_id: "service",
    workflow_id: "workflow",
    request: "do work",
    created_at: "2026-08-18T00:00:00.000Z",
    source: "local-workbench" as const,
  };
}

test("workbench request selection prefers the active profile", () => {
  const product = request("request-product", "product-director");
  const market = request("request-market", "market-director");
  assert.equal(selectWorkbenchRequest([product, market], "market-director"), market);
});

test("workbench request selection returns another profile when no active-profile request exists", () => {
  const product = request("request-product", "product-director");
  assert.equal(selectWorkbenchRequest([product], "market-director"), product);
});

test("accepted workbench requests are never selected", () => {
  assert.equal(
    selectWorkbenchRequest([request("request-done", "product-director", "accepted")], "market-director"),
    undefined,
  );
});

test("agent runtime lease is atomic and only its nonce owner can remove it", () => {
  const root = mkdtempSync(join(tmpdir(), "director-agent-lease-"));
  try {
    const nonce = "a5a5a5a5-1111-4222-8333-a5a5a5a5a5a5";
    const path = writeAgentRuntimeLease(root, {
      schema_version: "1.0",
      pid: process.pid,
      nonce,
      profile_id: "sales-director",
      session_key: "session-a",
      task_id: "task-a",
      task_status: "running",
      heartbeat_at: new Date().toISOString(),
    });
    const saved = JSON.parse(readFileSync(path, "utf8")) as { nonce: string; task_id: string };
    assert.equal(saved.task_id, "task-a");
    removeAgentRuntimeLease(path, "00000000-0000-4000-8000-000000000000");
    assert.equal(existsSync(path), true);
    removeAgentRuntimeLease(path, nonce);
    assert.equal(existsSync(path), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("sales director edition exposes only sales and government skills", async () => {
  const root = mkdtempSync(join(tmpdir(), "sales-director-edition-"));
  const previousProfile = process.env.WORKFLOW_AGENT_PROFILE;
  const previousEdition = process.env.WORKFLOW_AGENT_EDITION_PROFILE;
  process.env.WORKFLOW_AGENT_PROFILE = "product-director";
  process.env.WORKFLOW_AGENT_EDITION_PROFILE = "sales-director";
  try {
    writeRequest(root, "product-director");
    const runtime = harness(root);
    const resources = runtime.handlers.get("resources_discover")?.() as { skillPaths: string[] };
    assert.ok(resources.skillPaths.some((path) => path.includes("manage-market-pipeline")));
    assert.ok(resources.skillPaths.some((path) => path.includes("draft-government-program")));
    assert.equal(resources.skillPaths.some((path) => path.includes("product-discovery")), false);
    await runtime.handlers.get("session_start")?.({}, runtime.context);
    await new Promise((resolve) => setTimeout(resolve, 30));
    const leasePath = join(root, ".pi", "director-runtime", "agent-leases", `${process.pid}.json`);
    const lease = JSON.parse(readFileSync(leasePath, "utf8")) as { profile_id: string; task_id: string | null };
    assert.equal(lease.profile_id, "sales-director");
    assert.equal(lease.task_id, null);
    assert.equal(runtime.messages.some((message) => message.includes("product-director")), false);
    await runtime.handlers.get("session_shutdown")?.({}, runtime.context);
    assert.equal(existsSync(leasePath), false);
  } finally {
    if (previousProfile === undefined) delete process.env.WORKFLOW_AGENT_PROFILE;
    else process.env.WORKFLOW_AGENT_PROFILE = previousProfile;
    if (previousEdition === undefined) delete process.env.WORKFLOW_AGENT_EDITION_PROFILE;
    else process.env.WORKFLOW_AGENT_EDITION_PROFILE = previousEdition;
    rmSync(root, { recursive: true, force: true });
  }
});

function harness(root: string) {
  const handlers = new Map<string, (...args: unknown[]) => unknown>();
  const commands = new Map<string, { handler: (args: string, ctx: unknown) => Promise<void> }>();
  const tools = new Map<string, { execute: (toolCallId: string, params: Record<string, unknown>) => Promise<unknown> }>();
  const messages: string[] = [];
  const deliveries: Array<{ content: string; deliverAs?: string }> = [];
  const entries: Array<{ type: "custom"; customType: string; data: unknown }> = [];
  const model = (provider: string, id: string, reasoning: boolean) => ({
    provider, id, name: id, api: "openai-completions", baseUrl: "https://models.example/v1",
    reasoning, input: ["text"], cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 128_000, maxTokens: 32_768,
  });
  const defaultModel = model("test-provider", "default-model", false);
  const gatewayModel = model("agent4market-newapi", "gpt-5.5", false);
  const builtinReasoningModel = model("openai", "gpt-5.5", true);
  const availableModels = [defaultModel, gatewayModel, builtinReasoningModel];
  let selectedModel = defaultModel;
  let thinkingLevel = "off";
  const pi = {
    on(name: string, handler: (...args: unknown[]) => unknown) {
      handlers.set(name, handler);
    },
    registerTool(tool: { name: string; execute: (toolCallId: string, params: Record<string, unknown>) => Promise<unknown> }) {
      tools.set(tool.name, tool);
    },
    registerCommand(name: string, options: { handler: (args: string, ctx: unknown) => Promise<void> }) {
      commands.set(name, options);
    },
    appendEntry(customType: string, data: unknown) {
      entries.push({ type: "custom", customType, data });
    },
    sendUserMessage(content: string, options?: { deliverAs?: string }) {
      messages.push(content);
      deliveries.push({ content, deliverAs: options?.deliverAs });
    },
    async setModel(next: typeof defaultModel) {
      selectedModel = next;
      if (!next.reasoning) thinkingLevel = "off";
      return true;
    },
    getThinkingLevel() { return thinkingLevel; },
    setThinkingLevel(level: string) { thinkingLevel = selectedModel.reasoning ? level : "off"; },
  } as unknown as ExtensionAPI;
  verticalWorkflow(pi);
  const ui = { setStatus() {}, notify() {}, select: async () => undefined, input: async () => undefined };
  const context = {
    cwd: root,
    mode: "tui",
    hasUI: true,
    ui,
    sessionManager: {
      getEntries: () => entries,
      getSessionFile: () => join(root, "session.jsonl"),
    },
    model: defaultModel,
    modelRegistry: {
      getAll: () => availableModels,
      getAvailable: () => availableModels,
      find: (provider: string, id: string) => availableModels.find((candidate) => candidate.provider === provider && candidate.id === id),
    },
    scopedModels: [],
  };
  return { handlers, commands, tools, messages, deliveries, entries, context, selectedModel: () => selectedModel, thinkingLevel: () => thinkingLevel };
}

function writeRequest(root: string, profileId: string): void {
  const directory = join(root, ".pi", "director-runtime", "requests");
  mkdirSync(directory, { recursive: true });
  const payload = {
    ...request("request-profile-switch", profileId),
    service_id: profileId === "product-director" ? "product-discovery" : "industry-research",
    workflow_id: profileId === "product-director" ? "product.discovery.opportunity" : "shared.research.frontier",
  };
  writeFileSync(join(directory, "request-profile-switch.json"), JSON.stringify(payload), "utf8");
}

function writePresentationRevisionRequest(root: string): string {
  const requestId = "request-presentation-revision";
  const directory = join(root, ".pi", "director-runtime", "requests");
  mkdirSync(directory, { recursive: true });
  const payload = {
    ...request(requestId, "market-director"),
    service_id: "presentation-studio",
    workflow_id: "shared.presentation.studio",
    request: "[PRESENTATION_PLAN_REVISION]\n{\"source_task_id\":\"task-old\"}\n[/PRESENTATION_PLAN_REVISION]",
    request_kind: "presentation-plan-revision",
    revision_of_task_id: "task-old",
    source_plan_sha256: "b".repeat(64),
  };
  writeFileSync(join(directory, `${requestId}.json`), JSON.stringify(payload), "utf8");
  return requestId;
}

test("a scheduled sales workbench request preserves project and schedule provenance", async () => {
  const root = mkdtempSync(join(tmpdir(), "director-scheduled-request-"));
  const previousProfile = process.env.WORKFLOW_AGENT_PROFILE;
  const previousEdition = process.env.WORKFLOW_AGENT_EDITION_PROFILE;
  process.env.WORKFLOW_AGENT_PROFILE = "sales-director";
  process.env.WORKFLOW_AGENT_EDITION_PROFILE = "sales-director";
  const requestId = "request-scheduled-sales";
  try {
    const directory = join(root, ".pi", "director-runtime", "requests");
    mkdirSync(directory, { recursive: true });
    writeFileSync(join(directory, `${requestId}.json`), JSON.stringify({
      ...request(requestId, "sales-director"),
      service_id: "industry-research", workflow_id: "shared.research.frontier-subagent",
      project_id: "project-customer-a", schedule_id: "schedule-daily-a", scheduled_for: "2026-08-19",
    }), "utf8");
    const runtime = harness(root);
    await runtime.handlers.get("session_start")?.({}, runtime.context);
    await new Promise((resolve) => setTimeout(resolve, 40));
    const task = JSON.parse(readFileSync(join(root, ".pi", "director-runtime", "tasks", `${requestId}.json`), "utf8")) as {
      project_id?: string; schedule_id?: string; scheduled_for?: string;
    };
    assert.equal(task.project_id, "project-customer-a");
    assert.equal(task.schedule_id, "schedule-daily-a");
    assert.equal(task.scheduled_for, "2026-08-19");
    const consumed = JSON.parse(readFileSync(join(directory, `${requestId}.json`), "utf8")) as { status: string };
    assert.equal(consumed.status, "accepted");
    await runtime.handlers.get("session_shutdown")?.({}, runtime.context);
  } finally {
    if (previousProfile === undefined) delete process.env.WORKFLOW_AGENT_PROFILE;
    else process.env.WORKFLOW_AGENT_PROFILE = previousProfile;
    if (previousEdition === undefined) delete process.env.WORKFLOW_AGENT_EDITION_PROFILE;
    else process.env.WORKFLOW_AGENT_EDITION_PROFILE = previousEdition;
    rmSync(root, { recursive: true, force: true });
  }
});

test("a workbench request applies and freezes its model and thinking level", async () => {
  const root = mkdtempSync(join(tmpdir(), "director-task-model-"));
  const previousProfile = process.env.WORKFLOW_AGENT_PROFILE;
  const previousEdition = process.env.WORKFLOW_AGENT_EDITION_PROFILE;
  process.env.WORKFLOW_AGENT_PROFILE = "sales-director";
  process.env.WORKFLOW_AGENT_EDITION_PROFILE = "sales-director";
  const requestId = "request-task-model";
  try {
    const directory = join(root, ".pi", "director-runtime", "requests");
    mkdirSync(directory, { recursive: true });
    writeFileSync(join(directory, `${requestId}.json`), JSON.stringify({
      ...request(requestId, "sales-director"),
      service_id: "sales-review", workflow_id: "market.sales.pipeline-review",
      requested_model: "agent4market-newapi/gpt-5.5", requested_thinking_level: "high",
    }), "utf8");
    const runtime = harness(root);
    await runtime.handlers.get("session_start")?.({}, runtime.context);
    await new Promise((resolve) => setTimeout(resolve, 50));
    const task = JSON.parse(readFileSync(
      join(root, ".pi", "director-runtime", "tasks", `${requestId}.json`), "utf8",
    )) as Record<string, unknown>;
    assert.equal(task.requested_model, "agent4market-newapi/gpt-5.5");
    assert.equal(task.requested_thinking_level, "high");
    assert.equal(task.effective_model, "agent4market-newapi/gpt-5.5");
    assert.equal(task.effective_thinking_level, "high");
    assert.equal(runtime.selectedModel().provider, "agent4market-newapi");
    assert.equal(runtime.selectedModel().reasoning, true);
    assert.equal(runtime.thinkingLevel(), "high");
    await runtime.handlers.get("session_shutdown")?.({}, runtime.context);
  } finally {
    if (previousProfile === undefined) delete process.env.WORKFLOW_AGENT_PROFILE;
    else process.env.WORKFLOW_AGENT_PROFILE = previousProfile;
    if (previousEdition === undefined) delete process.env.WORKFLOW_AGENT_EDITION_PROFILE;
    else process.env.WORKFLOW_AGENT_EDITION_PROFILE = previousEdition;
    rmSync(root, { recursive: true, force: true });
  }
});

test("a tampered workbench model request is not accepted or persisted", async () => {
  const root = mkdtempSync(join(tmpdir(), "director-task-model-reject-"));
  const previousProfile = process.env.WORKFLOW_AGENT_PROFILE;
  const previousEdition = process.env.WORKFLOW_AGENT_EDITION_PROFILE;
  process.env.WORKFLOW_AGENT_PROFILE = "sales-director";
  process.env.WORKFLOW_AGENT_EDITION_PROFILE = "sales-director";
  const requestId = "request-task-model-reject";
  try {
    const directory = join(root, ".pi", "director-runtime", "requests");
    mkdirSync(directory, { recursive: true });
    const path = join(directory, `${requestId}.json`);
    writeFileSync(path, JSON.stringify({
      ...request(requestId, "sales-director"),
      service_id: "sales-review", workflow_id: "market.sales.pipeline-review",
      requested_model: "agent4market-newapi/not-installed", requested_thinking_level: "max",
    }), "utf8");
    const runtime = harness(root);
    await runtime.handlers.get("session_start")?.({}, runtime.context);
    await new Promise((resolve) => setTimeout(resolve, 50));
    assert.equal(existsSync(join(root, ".pi", "director-runtime", "tasks", `${requestId}.json`)), false);
    assert.equal((JSON.parse(readFileSync(path, "utf8")) as { status: string }).status, "requested");
    assert.equal(runtime.selectedModel().provider, "test-provider");
    assert.equal(runtime.thinkingLevel(), "off");
    await runtime.handlers.get("session_shutdown")?.({}, runtime.context);
  } finally {
    if (previousProfile === undefined) delete process.env.WORKFLOW_AGENT_PROFILE;
    else process.env.WORKFLOW_AGENT_PROFILE = previousProfile;
    if (previousEdition === undefined) delete process.env.WORKFLOW_AGENT_EDITION_PROFILE;
    else process.env.WORKFLOW_AGENT_EDITION_PROFILE = previousEdition;
    rmSync(root, { recursive: true, force: true });
  }
});

test("a restarted workbench request creates a fresh task linked to its source", async () => {
  const root = mkdtempSync(join(tmpdir(), "director-restart-request-"));
  const previousProfile = process.env.WORKFLOW_AGENT_PROFILE;
  const previousEdition = process.env.WORKFLOW_AGENT_EDITION_PROFILE;
  process.env.WORKFLOW_AGENT_PROFILE = "sales-director";
  process.env.WORKFLOW_AGENT_EDITION_PROFILE = "sales-director";
  const requestId = "request-restart-new";
  try {
    const directory = join(root, ".pi", "director-runtime", "requests");
    mkdirSync(directory, { recursive: true });
    writeFileSync(join(directory, `${requestId}.json`), JSON.stringify({
      ...request(requestId, "sales-director"),
      service_id: "sales-review", workflow_id: "market.sales.pipeline-review",
      request_kind: "task-restart", restart_of_task_id: "task-old", source_task_version: 7,
    }), "utf8");
    const runtime = harness(root);
    await runtime.handlers.get("session_start")?.({}, runtime.context);
    await new Promise((resolve) => setTimeout(resolve, 50));
    const task = JSON.parse(readFileSync(
      join(root, ".pi", "director-runtime", "tasks", `${requestId}.json`), "utf8",
    )) as { restarted_from_task_id?: string };
    assert.equal(task.restarted_from_task_id, "task-old");
    const consumed = JSON.parse(readFileSync(join(directory, `${requestId}.json`), "utf8")) as { status: string };
    assert.equal(consumed.status, "accepted");
    await runtime.handlers.get("session_shutdown")?.({}, runtime.context);
  } finally {
    if (previousProfile === undefined) delete process.env.WORKFLOW_AGENT_PROFILE;
    else process.env.WORKFLOW_AGENT_PROFILE = previousProfile;
    if (previousEdition === undefined) delete process.env.WORKFLOW_AGENT_EDITION_PROFILE;
    else process.env.WORKFLOW_AGENT_EDITION_PROFILE = previousEdition;
    rmSync(root, { recursive: true, force: true });
  }
});

test("a valid idle workbench request queues a command-context profile reload", async () => {
  const root = mkdtempSync(join(tmpdir(), "director-profile-switch-"));
  const previousProfile = process.env.WORKFLOW_AGENT_PROFILE;
  process.env.WORKFLOW_AGENT_PROFILE = "market-director";
  try {
    writeRequest(root, "product-director");
    const runtime = harness(root);
    try {
      await runtime.handlers.get("session_start")?.({}, runtime.context);
      await new Promise((resolve) => setTimeout(resolve, 30));
      assert.ok(runtime.messages.includes("/director-apply-profile-switch product-director"));
      let reloads = 0;
      await runtime.commands.get("director-apply-profile-switch")!.handler("product-director", {
        ...runtime.context,
        reload: async () => {
          reloads += 1;
        },
      });
      assert.equal(reloads, 1);
    } finally {
      await runtime.handlers.get("session_shutdown")?.({}, runtime.context);
    }
  } finally {
    if (previousProfile === undefined) delete process.env.WORKFLOW_AGENT_PROFILE;
    else process.env.WORKFLOW_AGENT_PROFILE = previousProfile;
    rmSync(root, { recursive: true, force: true });
  }
});

test("the runtime accepts a task-bound presentation revision request from the workbench", async () => {
  const root = mkdtempSync(join(tmpdir(), "director-presentation-revision-"));
  const previousProfile = process.env.WORKFLOW_AGENT_PROFILE;
  process.env.WORKFLOW_AGENT_PROFILE = "market-director";
  try {
    const requestId = writePresentationRevisionRequest(root);
    const runtime = harness(root);
    await runtime.handlers.get("session_start")?.({}, runtime.context);
    await new Promise((resolve) => setTimeout(resolve, 30));
    const persisted = JSON.parse(readFileSync(
      join(root, ".pi", "director-runtime", "requests", `${requestId}.json`),
      "utf8",
    )) as { status: string; task_id?: string; request_kind?: string };
    assert.equal(persisted.status, "accepted");
    assert.equal(persisted.task_id, requestId);
    assert.equal(persisted.request_kind, "presentation-plan-revision");
    await runtime.handlers.get("session_shutdown")?.({}, runtime.context);
  } finally {
    if (previousProfile === undefined) delete process.env.WORKFLOW_AGENT_PROFILE;
    else process.env.WORKFLOW_AGENT_PROFILE = previousProfile;
    rmSync(root, { recursive: true, force: true });
  }
});

test("an active managed task prevents automatic profile switching", async () => {
  const root = mkdtempSync(join(tmpdir(), "director-profile-active-"));
  const previousProfile = process.env.WORKFLOW_AGENT_PROFILE;
  process.env.WORKFLOW_AGENT_PROFILE = "market-director";
  try {
    const runtime = harness(root);
    await runtime.handlers.get("session_start")?.({}, runtime.context);
    await runtime.commands.get("director-run")!.handler(
      "industry-research research task",
      runtime.context,
    );
    writeRequest(root, "product-director");
    await new Promise((resolve) => setTimeout(resolve, 30));
    assert.equal(
      runtime.messages.some((message) => message.startsWith("/director-apply-profile-switch")),
      false,
    );
    await runtime.handlers.get("session_shutdown")?.({}, runtime.context);
  } finally {
    if (previousProfile === undefined) delete process.env.WORKFLOW_AGENT_PROFILE;
    else process.env.WORKFLOW_AGENT_PROFILE = previousProfile;
    rmSync(root, { recursive: true, force: true });
  }
});

test("managed tasks block unknown tools and restrict ordinary writes to outputs", async () => {
  const root = mkdtempSync(join(tmpdir(), "director-tool-guard-"));
  const previousProfile = process.env.WORKFLOW_AGENT_PROFILE;
  process.env.WORKFLOW_AGENT_PROFILE = "market-director";
  try {
    const runtime = harness(root);
    await runtime.handlers.get("session_start")?.({}, runtime.context);
    await runtime.commands.get("director-run")!.handler(
      "industry-research research task",
      runtime.context,
    );
    const guard = runtime.handlers.get("tool_call")!;
    const unknown = guard({ toolName: "third_party_writer", input: {} }, runtime.context) as {
      block?: boolean;
    };
    assert.equal(unknown.block, true);
    const protectedWrite = guard(
      { toolName: "write", input: { path: "profiles/market-director/profile.json", content: "x" } },
      runtime.context,
    ) as { block?: boolean };
    assert.equal(protectedWrite.block, true);
    const prematureArtifactWrite = guard(
      { toolName: "write", input: { path: "outputs/report.md", content: "ok" } },
      runtime.context,
    ) as { block?: boolean };
    assert.equal(prematureArtifactWrite.block, true);
    const disguisedDeckWrite = guard(
      { toolName: "write", input: { path: "outputs/fake.pptx.", content: "not a deck" } },
      runtime.context,
    ) as { block?: boolean; reason?: string };
    assert.equal(disguisedDeckWrite.block, true);
    assert.match(disguisedDeckWrite.reason ?? "", /PPTX/u);
    assert.equal(guard({ toolName: "read", input: { path: "README.md" } }, runtime.context), undefined);
    assert.equal(
      (guard({ toolName: "read", input: { path: join(root, "README.md") } }, runtime.context) as { block?: boolean }).block,
      true,
    );
    assert.equal(
      (guard({ toolName: "read", input: { path: "data/sales/customers.csv" } }, runtime.context) as { block?: boolean }).block,
      true,
    );
    assert.equal(
      (guard({ toolName: "read", input: { path: ".env" } }, runtime.context) as { block?: boolean }).block,
      true,
    );
    assert.equal(guard({ toolName: "director_report_progress", input: { phase: "analyzing", summary: "working" } }, runtime.context), undefined);
    await runtime.tools.get("director_complete_node")!.execute("complete-scope", { node_id: "scope" });
    assert.equal(
      (guard({ toolName: "read", input: { path: "README.md" } }, runtime.context) as { block?: boolean }).block,
      true,
    );
    const writeDuringToolStage = guard(
      { toolName: "write", input: { path: "outputs/report.md", content: "bypass" } },
      runtime.context,
    ) as { block?: boolean; reason?: string };
    assert.equal(writeDuringToolStage.block, true);
    assert.match(writeDuringToolStage.reason ?? "", /确定性 Tool/u);
    await runtime.handlers.get("session_shutdown")?.({}, runtime.context);
  } finally {
    if (previousProfile === undefined) delete process.env.WORKFLOW_AGENT_PROFILE;
    else process.env.WORKFLOW_AGENT_PROFILE = previousProfile;
    rmSync(root, { recursive: true, force: true });
  }
});

test("the runtime poll consumes a workbench approval and resumes the next stage", async () => {
  const root = mkdtempSync(join(tmpdir(), "director-approval-poll-"));
  const previousProfile = process.env.WORKFLOW_AGENT_PROFILE;
  process.env.WORKFLOW_AGENT_PROFILE = "market-director";
  const workflow: RuntimeWorkflow = {
    id: "market.sales.pipeline-review",
    nodes: [
      { id: "load_accounts", type: "tool", tool: "sales.read", depends_on: [] },
      { id: "analyze", type: "agent", depends_on: ["load_accounts"] },
      { id: "confirm", type: "approval", depends_on: ["analyze"] },
      { id: "update", type: "tool", tool: "sales.write", depends_on: ["confirm"] },
      { id: "validate_updates", type: "validator", depends_on: ["update"] },
    ],
  };
  try {
    let task = createTask({
      sessionKey: join(root, "session.jsonl"),
      profileId: "market-director",
      serviceId: "sales-review",
      workflow,
      request: "review",
      taskId: "task-ui-approval",
    });
    task = completeLogicalTool(task, workflow, "sales.read", task.version);
    const payload = {
      table: "customers",
      mutations: [{ operation: "update", record_id: "c-1", changes: { next_action: "follow up" }, expected_version: "v1" }],
    };
    task = proposeWriteIntent(task, workflow, "sales.write", payload, task.version);
    task = completeModelNode(task, workflow, "analyze", task.version);
    const base = task;
    const disk = {
      ...base,
      version: base.version + 1,
      approval_request: {
        decision: "approve" as const,
        requested_at: "2026-08-18T00:00:00.000Z",
        requested_by: "local-workbench",
        expected_version: base.version,
        intent_id: task.pending_write!.intent_id,
        payload_sha256: task.pending_write!.payload_sha256,
      },
    };
    const taskDirectory = join(root, ".pi", "director-runtime", "tasks");
    mkdirSync(taskDirectory, { recursive: true });
    writeFileSync(join(taskDirectory, `${task.task_id}.json`), JSON.stringify(disk), "utf8");

    const runtime = harness(root);
    runtime.entries.push({ type: "custom", customType: "director-task-state", data: base });
    await runtime.handlers.get("session_start")?.({}, runtime.context);
    await new Promise((resolve) => setTimeout(resolve, 30));
    const persisted = JSON.parse(
      readFileSync(join(taskDirectory, `${task.task_id}.json`), "utf8"),
    ) as { status: string; approval_request?: unknown };
    assert.equal(persisted.status, "running");
    assert.equal(persisted.approval_request, undefined);
    assert.ok(runtime.messages.some((message) => message.includes("本地工作台已批准受管任务")));
    await runtime.handlers.get("session_shutdown")?.({}, runtime.context);
  } finally {
    if (previousProfile === undefined) delete process.env.WORKFLOW_AGENT_PROFILE;
    else process.env.WORKFLOW_AGENT_PROFILE = previousProfile;
    rmSync(root, { recursive: true, force: true });
  }
});

test("a fresh runtime safely adopts an approved task from an ended session", async () => {
  const root = mkdtempSync(join(tmpdir(), "director-detached-approval-"));
  const previousProfile = process.env.WORKFLOW_AGENT_PROFILE;
  const previousEdition = process.env.WORKFLOW_AGENT_EDITION_PROFILE;
  process.env.WORKFLOW_AGENT_PROFILE = "sales-director";
  process.env.WORKFLOW_AGENT_EDITION_PROFILE = "sales-director";
  const workflow: RuntimeWorkflow = {
    id: "market.sales.pipeline-review",
    nodes: [
      { id: "load_accounts", type: "tool", tool: "sales.read", depends_on: [] },
      { id: "analyze", type: "agent", depends_on: ["load_accounts"] },
      { id: "confirm", type: "approval", depends_on: ["analyze"] },
      { id: "update", type: "tool", tool: "sales.write", depends_on: ["confirm"] },
      { id: "validate_updates", type: "validator", depends_on: ["update"] },
    ],
  };
  try {
    let task = createTask({
      sessionKey: "ended-session.jsonl", profileId: "sales-director",
      serviceId: "sales-review", workflow, request: "review", taskId: "task-detached-approval",
    });
    task = completeLogicalTool(task, workflow, "sales.read", task.version);
    task = proposeWriteIntent(task, workflow, "sales.write", {
      table: "customers",
      mutations: [{ operation: "update", record_id: "c-1", changes: { next_action: "follow up" }, expected_version: "v1" }],
    }, task.version);
    task = completeModelNode(task, workflow, "analyze", task.version);
    const disk = {
      ...task,
      version: task.version + 1,
      approval_request: {
        decision: "approve" as const, requested_at: "2026-08-20T00:00:00.000Z",
        requested_by: "local-workbench", expected_version: task.version,
        intent_id: task.pending_write!.intent_id, payload_sha256: task.pending_write!.payload_sha256,
      },
    };
    const taskDirectory = join(root, ".pi", "director-runtime", "tasks");
    mkdirSync(taskDirectory, { recursive: true });
    writeFileSync(join(taskDirectory, "task-detached-approval.json"), JSON.stringify(disk), "utf8");

    const runtime = harness(root);
    await runtime.handlers.get("session_start")?.({}, runtime.context);
    await new Promise((resolve) => setTimeout(resolve, 70));
    const persisted = JSON.parse(readFileSync(join(taskDirectory, "task-detached-approval.json"), "utf8")) as {
      session_key: string; status: string; approval_request?: unknown;
      pending_write?: { status: string }; audit: Array<{ action: string }>;
    };
    assert.equal(persisted.session_key, join(root, "session.jsonl"));
    assert.equal(persisted.status, "running");
    assert.equal(persisted.pending_write?.status, "approved");
    assert.equal(persisted.approval_request, undefined);
    assert.ok(persisted.audit.some((event) => event.action === "task_session_rebound"));
    assert.ok(runtime.messages.some((message) => message.includes("已从旧会话安全接管")));
    const lease = JSON.parse(readFileSync(
      join(root, ".pi", "director-runtime", "agent-leases", `${process.pid}.json`), "utf8",
    )) as { task_id: string };
    assert.equal(lease.task_id, "task-detached-approval");
    await runtime.handlers.get("session_shutdown")?.({}, runtime.context);
  } finally {
    if (previousProfile === undefined) delete process.env.WORKFLOW_AGENT_PROFILE;
    else process.env.WORKFLOW_AGENT_PROFILE = previousProfile;
    if (previousEdition === undefined) delete process.env.WORKFLOW_AGENT_EDITION_PROFILE;
    else process.env.WORKFLOW_AGENT_EDITION_PROFILE = previousEdition;
    rmSync(root, { recursive: true, force: true });
  }
});

test("a fresh runtime rebinds one interrupted committing task and continues its exact checkpoint", async () => {
  const root = mkdtempSync(join(tmpdir(), "director-restart-commit-"));
  const previousProfile = process.env.WORKFLOW_AGENT_PROFILE;
  const previousEdition = process.env.WORKFLOW_AGENT_EDITION_PROFILE;
  process.env.WORKFLOW_AGENT_PROFILE = "sales-director";
  process.env.WORKFLOW_AGENT_EDITION_PROFILE = "sales-director";
  const workflow: RuntimeWorkflow = {
    id: "market.sales.pipeline-review",
    nodes: [
      { id: "load_accounts", type: "tool", tool: "sales.read", depends_on: [] },
      { id: "analyze", type: "agent", depends_on: ["load_accounts"] },
      { id: "confirm", type: "approval", depends_on: ["analyze"] },
      { id: "update", type: "tool", tool: "sales.write", depends_on: ["confirm"] },
      { id: "validate_updates", type: "validator", depends_on: ["update"] },
    ],
  };
  try {
    const payload = {
      table: "customers",
      mutations: [{ operation: "update", record_id: "c-1", changes: { next_action: "follow up" }, expected_version: "v1" }],
    };
    let task = createTask({
      sessionKey: "ended-session.jsonl", profileId: "sales-director",
      serviceId: "sales-review", workflow, request: "review", taskId: "task-restart-commit",
    });
    task = completeLogicalTool(task, workflow, "sales.read", task.version);
    task = proposeWriteIntent(task, workflow, "sales.write", payload, task.version);
    task = completeModelNode(task, workflow, "analyze", task.version);
    task = approveNode(task, workflow, "confirm", task.version);
    task = beginWriteCommit(task, workflow, "sales.write", payload, task.version);
    const taskDirectory = join(root, ".pi", "director-runtime", "tasks");
    mkdirSync(taskDirectory, { recursive: true });
    writeFileSync(join(taskDirectory, `${task.task_id}.json`), JSON.stringify(task), "utf8");

    const runtime = harness(root);
    await runtime.handlers.get("session_start")?.({}, runtime.context);
    await new Promise((resolve) => setTimeout(resolve, 40));
    const persisted = JSON.parse(readFileSync(join(taskDirectory, `${task.task_id}.json`), "utf8")) as {
      session_key: string; status: string; current_node: string; pending_write: { status: string };
      audit: Array<{ action: string }>;
    };
    assert.equal(persisted.session_key, join(root, "session.jsonl"));
    assert.equal(persisted.status, "running");
    assert.equal(persisted.current_node, "update");
    assert.equal(persisted.pending_write.status, "committing");
    assert.ok(persisted.audit.some((event) => event.action === "task_session_rebound"));
    assert.ok(runtime.messages.some((message) => message.includes("Agent 已重启并安全接管未完成任务")));
    await runtime.handlers.get("session_shutdown")?.({}, runtime.context);
  } finally {
    if (previousProfile === undefined) delete process.env.WORKFLOW_AGENT_PROFILE;
    else process.env.WORKFLOW_AGENT_PROFILE = previousProfile;
    if (previousEdition === undefined) delete process.env.WORKFLOW_AGENT_EDITION_PROFILE;
    else process.env.WORKFLOW_AGENT_EDITION_PROFILE = previousEdition;
    rmSync(root, { recursive: true, force: true });
  }
});

test("a detached approval with a replaced hash remains fail closed", async () => {
  const root = mkdtempSync(join(tmpdir(), "director-detached-approval-hash-"));
  const previousProfile = process.env.WORKFLOW_AGENT_PROFILE;
  const previousEdition = process.env.WORKFLOW_AGENT_EDITION_PROFILE;
  process.env.WORKFLOW_AGENT_PROFILE = "sales-director";
  process.env.WORKFLOW_AGENT_EDITION_PROFILE = "sales-director";
  const workflow: RuntimeWorkflow = {
    id: "market.sales.pipeline-review",
    nodes: [
      { id: "load_accounts", type: "tool", tool: "sales.read", depends_on: [] },
      { id: "analyze", type: "agent", depends_on: ["load_accounts"] },
      { id: "confirm", type: "approval", depends_on: ["analyze"] },
      { id: "update", type: "tool", tool: "sales.write", depends_on: ["confirm"] },
    ],
  };
  try {
    let task = createTask({
      sessionKey: "ended-session.jsonl", profileId: "sales-director",
      serviceId: "sales-review", workflow, request: "review", taskId: "task-detached-tampered",
    });
    task = completeLogicalTool(task, workflow, "sales.read", task.version);
    task = proposeWriteIntent(task, workflow, "sales.write", {
      table: "customers",
      mutations: [{ operation: "update", record_id: "c-1", changes: { next_action: "follow up" }, expected_version: "v1" }],
    }, task.version);
    task = completeModelNode(task, workflow, "analyze", task.version);
    const disk = {
      ...task,
      version: task.version + 1,
      approval_request: {
        decision: "approve" as const, requested_at: "2026-08-20T00:00:00.000Z",
        requested_by: "local-workbench", expected_version: task.version,
        intent_id: task.pending_write!.intent_id, payload_sha256: "f".repeat(64),
      },
    };
    const taskDirectory = join(root, ".pi", "director-runtime", "tasks");
    mkdirSync(taskDirectory, { recursive: true });
    const taskPath = join(taskDirectory, "task-detached-tampered.json");
    writeFileSync(taskPath, JSON.stringify(disk), "utf8");
    const runtime = harness(root);
    await runtime.handlers.get("session_start")?.({}, runtime.context);
    await new Promise((resolve) => setTimeout(resolve, 60));
    const persisted = JSON.parse(readFileSync(taskPath, "utf8")) as {
      session_key: string; status: string; approval_request?: unknown; pending_write?: { status: string };
    };
    assert.equal(persisted.session_key, "ended-session.jsonl");
    assert.equal(persisted.status, "waiting_approval");
    assert.equal(persisted.pending_write?.status, "prepared");
    assert.ok(persisted.approval_request);
    const lease = JSON.parse(readFileSync(
      join(root, ".pi", "director-runtime", "agent-leases", `${process.pid}.json`), "utf8",
    )) as { task_id: string | null };
    assert.equal(lease.task_id, null);
    await runtime.handlers.get("session_shutdown")?.({}, runtime.context);
  } finally {
    if (previousProfile === undefined) delete process.env.WORKFLOW_AGENT_PROFILE;
    else process.env.WORKFLOW_AGENT_PROFILE = previousProfile;
    if (previousEdition === undefined) delete process.env.WORKFLOW_AGENT_EDITION_PROFILE;
    else process.env.WORKFLOW_AGENT_EDITION_PROFILE = previousEdition;
    rmSync(root, { recursive: true, force: true });
  }
});

test("the runtime poll closes an interrupted task after a workbench cancellation request", async () => {
  const root = mkdtempSync(join(tmpdir(), "director-detached-cancel-"));
  const previousProfile = process.env.WORKFLOW_AGENT_PROFILE;
  const previousEdition = process.env.WORKFLOW_AGENT_EDITION_PROFILE;
  process.env.WORKFLOW_AGENT_PROFILE = "sales-director";
  process.env.WORKFLOW_AGENT_EDITION_PROFILE = "sales-director";
  const workflow: RuntimeWorkflow = {
    id: "market.sales.pipeline-review",
    nodes: [
      { id: "load_accounts", type: "tool", tool: "sales.read", depends_on: [] },
      { id: "analyze", type: "agent", depends_on: ["load_accounts"] },
      { id: "confirm", type: "approval", depends_on: ["analyze"] },
      { id: "update", type: "tool", tool: "sales.write", depends_on: ["confirm"] },
      { id: "validate_updates", type: "validator", depends_on: ["update"] },
    ],
  };
  try {
    const task = createTask({
      sessionKey: "ended-session.jsonl",
      profileId: "sales-director",
      serviceId: "sales-review",
      workflow,
      request: "review",
      taskId: "task-interrupted",
    });
    const disk = {
      ...task,
      version: task.version + 1,
      approval_request: {
        decision: "cancel" as const,
        requested_at: "2026-08-19T00:00:00.000Z",
        requested_by: "local-workbench",
        expected_version: task.version,
      },
    };
    const taskDirectory = join(root, ".pi", "director-runtime", "tasks");
    mkdirSync(taskDirectory, { recursive: true });
    writeFileSync(join(taskDirectory, "task-interrupted.json"), JSON.stringify(disk), "utf8");

    const runtime = harness(root);
    await runtime.handlers.get("session_start")?.({}, runtime.context);
    await new Promise((resolve) => setTimeout(resolve, 50));
    const persisted = JSON.parse(readFileSync(join(taskDirectory, "task-interrupted.json"), "utf8")) as {
      status: string;
      approval_request?: unknown;
    };
    assert.equal(persisted.status, "cancelled");
    assert.equal(persisted.approval_request, undefined);
    await runtime.handlers.get("session_shutdown")?.({}, runtime.context);
  } finally {
    if (previousProfile === undefined) delete process.env.WORKFLOW_AGENT_PROFILE;
    else process.env.WORKFLOW_AGENT_PROFILE = previousProfile;
    if (previousEdition === undefined) delete process.env.WORKFLOW_AGENT_EDITION_PROFILE;
    else process.env.WORKFLOW_AGENT_EDITION_PROFILE = previousEdition;
    rmSync(root, { recursive: true, force: true });
  }
});

test("the runtime poll safely rebinds an interrupted task to the current session", async () => {
  const root = mkdtempSync(join(tmpdir(), "director-detached-resume-"));
  const previousProfile = process.env.WORKFLOW_AGENT_PROFILE;
  const previousEdition = process.env.WORKFLOW_AGENT_EDITION_PROFILE;
  process.env.WORKFLOW_AGENT_PROFILE = "sales-director";
  process.env.WORKFLOW_AGENT_EDITION_PROFILE = "sales-director";
  const workflow: RuntimeWorkflow = {
    id: "market.sales.pipeline-review",
    nodes: [
      { id: "load_accounts", type: "tool", tool: "sales.read", depends_on: [] },
      { id: "analyze", type: "agent", depends_on: ["load_accounts"] },
      { id: "confirm", type: "approval", depends_on: ["analyze"] },
      { id: "update", type: "tool", tool: "sales.write", depends_on: ["confirm"] },
      { id: "validate_updates", type: "validator", depends_on: ["update"] },
    ],
  };
  try {
    const task = createTask({
      sessionKey: "ended-session.jsonl",
      profileId: "sales-director",
      serviceId: "sales-review",
      workflow,
      request: "review",
      taskId: "task-resume",
    });
    const disk = {
      ...task,
      version: task.version + 1,
      approval_request: {
        decision: "resume" as const,
        requested_at: "2026-08-19T00:00:00.000Z",
        requested_by: "local-workbench",
        expected_version: task.version,
      },
    };
    const taskDirectory = join(root, ".pi", "director-runtime", "tasks");
    mkdirSync(taskDirectory, { recursive: true });
    writeFileSync(join(taskDirectory, "task-resume.json"), JSON.stringify(disk), "utf8");

    const runtime = harness(root);
    await runtime.handlers.get("session_start")?.({}, runtime.context);
    await new Promise((resolve) => setTimeout(resolve, 50));
    const persisted = JSON.parse(readFileSync(join(taskDirectory, "task-resume.json"), "utf8")) as {
      session_key: string; status: string; approval_request?: unknown; audit: Array<{ action: string }>;
    };
    assert.equal(persisted.session_key, join(root, "session.jsonl"));
    assert.equal(persisted.status, "running");
    assert.equal(persisted.approval_request, undefined);
    assert.equal(persisted.audit.at(-1)?.action, "task_resumed");
    assert.ok(runtime.messages.some((message) => message.includes("受管任务 task-resume")));
    const lease = JSON.parse(readFileSync(
      join(root, ".pi", "director-runtime", "agent-leases", `${process.pid}.json`), "utf8",
    )) as { task_id: string };
    assert.equal(lease.task_id, "task-resume");
    await runtime.handlers.get("session_shutdown")?.({}, runtime.context);
  } finally {
    if (previousProfile === undefined) delete process.env.WORKFLOW_AGENT_PROFILE;
    else process.env.WORKFLOW_AGENT_PROFILE = previousProfile;
    if (previousEdition === undefined) delete process.env.WORKFLOW_AGENT_EDITION_PROFILE;
    else process.env.WORKFLOW_AGENT_EDITION_PROFILE = previousEdition;
    rmSync(root, { recursive: true, force: true });
  }
});

test("task-bound workbench messages are delivered through Pi steering queue", async () => {
  const root = mkdtempSync(join(tmpdir(), "director-task-message-"));
  const previousProfile = process.env.WORKFLOW_AGENT_PROFILE;
  const previousEdition = process.env.WORKFLOW_AGENT_EDITION_PROFILE;
  process.env.WORKFLOW_AGENT_PROFILE = "sales-director";
  process.env.WORKFLOW_AGENT_EDITION_PROFILE = "sales-director";
  const workflow: RuntimeWorkflow = {
    id: "market.sales.pipeline-review",
    nodes: [
      { id: "load_accounts", type: "tool", tool: "sales.read", depends_on: [] },
      { id: "analyze", type: "agent", depends_on: ["load_accounts"] },
    ],
  };
  try {
    const task = createTask({
      sessionKey: join(root, "session.jsonl"), profileId: "sales-director",
      serviceId: "sales-review", workflow, request: "review", taskId: "task-message-a",
    });
    const taskDirectory = join(root, ".pi", "director-runtime", "tasks");
    const messageDirectory = join(root, ".pi", "director-runtime", "task-messages");
    mkdirSync(taskDirectory, { recursive: true });
    mkdirSync(messageDirectory, { recursive: true });
    writeFileSync(join(taskDirectory, `${task.task_id}.json`), JSON.stringify(task), "utf8");
    const messageId = "message-1234567890abcdef";
    const message = validateTaskMessage({
      schema_version: "1.0", message_id: messageId, task_id: task.task_id,
      profile_id: "sales-director", mode: "redirect", content: "先检查预算风险",
      status: "queued", created_at: new Date().toISOString(),
    }, messageId);
    writeFileSync(join(messageDirectory, `${messageId}.json`), JSON.stringify(message), "utf8");
    const runtime = harness(root);
    runtime.entries.push({ type: "custom", customType: "director-task-state", data: task });
    await runtime.handlers.get("session_start")?.({}, runtime.context);
    await new Promise((resolve) => setTimeout(resolve, 60));
    const delivered = JSON.parse(readFileSync(join(messageDirectory, `${messageId}.json`), "utf8")) as { status: string };
    assert.equal(delivered.status, "delivered");
    assert.ok(runtime.deliveries.some((entry) => entry.deliverAs === "steer" && entry.content.includes("先检查预算风险")));
    await runtime.handlers.get("session_shutdown")?.({}, runtime.context);
  } finally {
    if (previousProfile === undefined) delete process.env.WORKFLOW_AGENT_PROFILE;
    else process.env.WORKFLOW_AGENT_PROFILE = previousProfile;
    if (previousEdition === undefined) delete process.env.WORKFLOW_AGENT_EDITION_PROFILE;
    else process.env.WORKFLOW_AGENT_EDITION_PROFILE = previousEdition;
    rmSync(root, { recursive: true, force: true });
  }
});

test("the progress tool writes a bounded user-visible event", async () => {
  const root = mkdtempSync(join(tmpdir(), "director-progress-event-"));
  const previousProfile = process.env.WORKFLOW_AGENT_PROFILE;
  process.env.WORKFLOW_AGENT_PROFILE = "sales-director";
  try {
    const runtime = harness(root);
    await runtime.handlers.get("session_start")?.({}, runtime.context);
    await runtime.commands.get("director-run")!.handler("sales-review 复盘客户 A", runtime.context);
    await runtime.tools.get("director_report_progress")!.execute("progress-1", {
      phase: "analyzing", summary: "正在比较客户推进记录与资源缺口。",
      basis: "最近一次跟进没有明确下一步负责人。", next_step: "形成风险清单。",
    });
    const events = readdirSync(join(root, ".pi", "director-runtime", "task-events"))
      .filter((name) => name.startsWith("event-") && name.endsWith(".json"));
    assert.ok(events.length >= 2);
    const values = events.map((name) => JSON.parse(readFileSync(join(root, ".pi", "director-runtime", "task-events", name), "utf8")) as { source: string; basis?: string });
    assert.ok(values.some((event) => event.source === "assistant" && event.basis === "最近一次跟进没有明确下一步负责人。"));
    await runtime.handlers.get("session_shutdown")?.({}, runtime.context);
  } finally {
    if (previousProfile === undefined) delete process.env.WORKFLOW_AGENT_PROFILE;
    else process.env.WORKFLOW_AGENT_PROFILE = previousProfile;
    rmSync(root, { recursive: true, force: true });
  }
});

test("runtime validation rejects permission escalation and non-direct write approval", () => {
  const manifest = {
    api_version: "1.0", id: "test.plugin", version: "1.0.0",
    permissions: ["knowledge.read", "knowledge.write"], skills: ["test-skill"],
    workflows: ["test.workflow"], dependencies: [],
  };
  const workflow = {
    id: "test.workflow", plugin: "test.plugin", display_name: "test",
    entry_nodes: ["draft"], output_nodes: ["write"],
    nodes: [
      { id: "draft", type: "agent", skill: "test-skill", depends_on: [], permissions: ["knowledge.read"] },
      { id: "gate", type: "approval", policy: "user", depends_on: ["draft"], permissions: [] },
      { id: "write", type: "tool", tool: "knowledge.write", depends_on: ["gate"], permissions: ["knowledge.write"] },
    ],
  };
  assert.doesNotThrow(() => validateRuntimeWorkflow(workflow, manifest));
  const escalated = structuredClone(workflow);
  escalated.nodes[0]!.permissions = ["sales.write"];
  assert.throws(() => validateRuntimeWorkflow(escalated, manifest), /escalates permissions/);
  const indirect = structuredClone(workflow);
  indirect.nodes.splice(2, 0, { id: "middle", type: "validator", check: "ok", depends_on: ["gate"], permissions: [] } as never);
  indirect.nodes.at(-1)!.depends_on = ["middle"];
  assert.throws(() => validateRuntimeWorkflow(indirect, manifest), /exactly one direct approval dependency/);
  const multiPredecessor = structuredClone(workflow);
  multiPredecessor.nodes.splice(1, 0, { id: "second", type: "validator", check: "ok", depends_on: ["draft"], permissions: [] } as never);
  multiPredecessor.nodes.find((node) => node.id === "gate")!.depends_on = ["draft", "second"];
  assert.throws(() => validateRuntimeWorkflow(multiPredecessor, manifest), /one direct agent\/validator predecessor/);
});
