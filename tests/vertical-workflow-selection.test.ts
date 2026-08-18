import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

import verticalWorkflow, { selectWorkbenchRequest, validateRuntimeWorkflow } from "../pi/extensions/vertical-workflow.ts";
import {
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

function harness(root: string) {
  const handlers = new Map<string, (...args: unknown[]) => unknown>();
  const commands = new Map<string, { handler: (args: string, ctx: unknown) => Promise<void> }>();
  const tools = new Map<string, { execute: (toolCallId: string, params: Record<string, unknown>) => Promise<unknown> }>();
  const messages: string[] = [];
  const entries: Array<{ type: "custom"; customType: string; data: unknown }> = [];
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
    sendUserMessage(content: string) {
      messages.push(content);
    },
  } as unknown as ExtensionAPI;
  verticalWorkflow(pi);
  const ui = { setStatus() {}, notify() {}, select: async () => undefined, input: async () => undefined };
  const context = {
    cwd: root,
    hasUI: true,
    ui,
    sessionManager: {
      getEntries: () => entries,
      getSessionFile: () => join(root, "session.jsonl"),
    },
  };
  return { handlers, commands, tools, messages, entries, context };
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
