import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  TaskStore,
  TaskTransitionError,
  VersionConflictError,
  approveNode,
  assertApprovedWriteIntent,
  beginWriteCommit,
  cancelTask,
  completeLogicalTool,
  completeModelNode,
  consumeApprovalRequest,
  createTask,
  currentNodes,
  rejectApproval,
  proposeWriteIntent,
  recoverWriteFailure,
  type RuntimeWorkflow,
} from "../pi/extensions/task-runtime.ts";

const linearWorkflow: RuntimeWorkflow = {
  id: "test.linear",
  nodes: [
    { id: "draft", type: "agent", depends_on: [] },
    { id: "validate", type: "validator", depends_on: ["draft"] },
    { id: "approve", type: "approval", depends_on: ["validate"] },
    { id: "write", type: "tool", tool: "knowledge.write", depends_on: ["approve"] },
  ],
};

function start(workflow = linearWorkflow) {
  return createTask({
    sessionKey: "session-a",
    profileId: "market-director",
    serviceId: "test-service",
    workflow,
    request: "test request",
    taskId: "task-a",
  });
}

const writePayload = {
  mutations: [{ operation: "insert", record_id: "src-2", changes: { title: "来源", status: "new" } }],
};

function readyForApproval() {
  let task = start();
  task = completeModelNode(task, linearWorkflow, "draft", task.version);
  task = proposeWriteIntent(task, linearWorkflow, "knowledge.write", writePayload, task.version);
  return completeModelNode(task, linearWorkflow, "validate", task.version);
}

test("model cannot skip stages or complete approval/tool nodes", () => {
  const task = start();
  assert.deepEqual(currentNodes(task, linearWorkflow).map((node) => node.id), ["draft"]);
  assert.throws(
    () => completeModelNode(task, linearWorkflow, "validate", task.version),
    TaskTransitionError,
  );
  const drafted = completeModelNode(task, linearWorkflow, "draft", task.version);
  assert.throws(
    () => completeModelNode(drafted, linearWorkflow, "approve", drafted.version),
    TaskTransitionError,
  );
  assert.throws(
    () => completeModelNode(drafted, linearWorkflow, "validate", drafted.version),
    /Prepare the exact knowledge.write payload/,
  );
  const prepared = proposeWriteIntent(drafted, linearWorkflow, "knowledge.write", writePayload, drafted.version);
  const validated = completeModelNode(prepared, linearWorkflow, "validate", prepared.version);
  assert.equal(validated.status, "waiting_approval");
  assert.throws(
    () => completeLogicalTool(validated, linearWorkflow, "knowledge.write", validated.version),
    TaskTransitionError,
  );
});

test("only user approval advances an approval and tool adapter completes final node", () => {
  const validated = readyForApproval();
  const approved = approveNode(validated, linearWorkflow, "approve", validated.version, "ok");
  assert.equal(approved.status, "running");
  assert.deepEqual(currentNodes(approved, linearWorkflow).map((node) => node.id), ["write"]);
  const completed = completeLogicalTool(
    approved,
    linearWorkflow,
    "knowledge.write",
    approved.version,
    undefined,
    writePayload,
  );
  assert.equal(completed.status, "completed");
  assert.equal(completed.current_stage, null);
});

test("reject and cancel are terminal and audited", () => {
  let task = readyForApproval();
  const rejected = rejectApproval(task, linearWorkflow, "approve", task.version, "needs revision");
  assert.equal(rejected.status, "rejected");
  assert.equal(rejected.audit.at(-1)?.action, "approval_rejected");

  const cancelled = cancelTask(start(), start().version, "stop");
  assert.equal(cancelled.status, "cancelled");
});

test("parallel stage waits for every node and control nodes advance automatically", () => {
  const workflow: RuntimeWorkflow = {
    id: "test.parallel",
    nodes: [
      { id: "start", type: "agent", depends_on: [] },
      { id: "fanout", type: "parallel", depends_on: ["start"] },
      { id: "left", type: "agent", depends_on: ["fanout"] },
      { id: "right", type: "tool", tool: "knowledge.search", depends_on: ["fanout"] },
      { id: "join", type: "join", depends_on: ["left", "right"] },
      { id: "finish", type: "validator", depends_on: ["join"] },
    ],
  };
  let task = start(workflow);
  task = completeModelNode(task, workflow, "start", task.version);
  assert.ok(task.completed_nodes.includes("fanout"));
  assert.deepEqual(currentNodes(task, workflow).map((node) => node.id), ["left", "right"]);
  task = completeLogicalTool(task, workflow, "knowledge.search", task.version);
  assert.deepEqual(currentNodes(task, workflow).map((node) => node.id), ["left"]);
  task = completeModelNode(task, workflow, "left", task.version);
  assert.ok(task.completed_nodes.includes("join"));
  assert.deepEqual(currentNodes(task, workflow).map((node) => node.id), ["finish"]);
});

test("in-memory version conflicts are rejected", () => {
  const task = start();
  assert.throws(
    () => completeModelNode(task, linearWorkflow, "draft", task.version + 1),
    VersionConflictError,
  );
});

test("task store persists, restores and rejects stale writers", () => {
  const root = mkdtempSync(join(tmpdir(), "director-runtime-"));
  try {
    const store = new TaskStore(root);
    const first = start();
    store.save(first);
    assert.deepEqual(store.load(first.task_id), first);
    assert.equal(store.findActive(first.session_key)?.task_id, first.task_id);

    const second = completeModelNode(first, linearWorkflow, "draft", first.version);
    store.save(second, first.version);
    assert.deepEqual(store.load(first.task_id), second);
    assert.throws(() => store.save(first, first.version), VersionConflictError);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("failed snapshot publication leaves the previous JSON intact", () => {
  const root = mkdtempSync(join(tmpdir(), "director-runtime-"));
  try {
    const store = new TaskStore(root);
    const first = start();
    store.save(first);
    const path = join(store.directory, `${first.task_id}.json`);
    const before = readFileSync(path, "utf8");
    writeFileSync(`${path}.lock`, "owned elsewhere", "utf8");
    const second = completeModelNode(first, linearWorkflow, "draft", first.version);
    assert.throws(() => store.save(second, first.version));
    assert.equal(readFileSync(path, "utf8"), before);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("external UI approval request consumes exactly one reserved version", () => {
  const task = readyForApproval();
  const baseVersion = task.version;
  const requested = {
    ...task,
    version: baseVersion + 1,
    approval_request: {
      decision: "approve" as const,
      requested_at: "2026-08-18T00:00:00.000Z",
      requested_by: "local-workbench",
      expected_version: baseVersion,
      intent_id: task.pending_write!.intent_id,
      payload_sha256: task.pending_write!.payload_sha256,
    },
  };
  const approved = consumeApprovalRequest(requested, linearWorkflow);
  assert.equal(approved.version, baseVersion + 2);
  assert.equal(approved.status, "running");
  assert.equal(approved.approval_request, undefined);
  assert.throws(
    () => consumeApprovalRequest({ ...requested, version: baseVersion + 2 }, linearWorkflow),
    VersionConflictError,
  );
});

test("approval is bound to the exact frozen batch and commit can roll back safely", () => {
  const waiting = readyForApproval();
  const approved = approveNode(waiting, linearWorkflow, "approve", waiting.version);
  assertApprovedWriteIntent(approved, "knowledge.write", writePayload);
  assert.throws(
    () => assertApprovedWriteIntent(approved, "knowledge.write", { mutations: [] }),
    /differs from the exact payload/,
  );
  const committing = beginWriteCommit(
    approved, linearWorkflow, "knowledge.write", writePayload, approved.version,
  );
  assert.equal(committing.pending_write?.status, "committing");
  const recovered = recoverWriteFailure(
    committing, "knowledge.write", writePayload, "not_committed", committing.version, "lock held",
  );
  assert.equal(recovered.pending_write?.status, "approved");
});

test("external approval rejects a stale or replaced intent hash", () => {
  const task = readyForApproval();
  const requested = {
    ...task,
    version: task.version + 1,
    approval_request: {
      decision: "approve" as const,
      requested_at: "2026-08-18T00:00:00.000Z",
      requested_by: "local-workbench",
      expected_version: task.version,
      intent_id: task.pending_write!.intent_id,
      payload_sha256: "0".repeat(64),
    },
  };
  assert.throws(() => consumeApprovalRequest(requested, linearWorkflow), /not bound/);
});

test("write intent preparation selects its direct predecessor inside a parallel model stage", () => {
  const workflow: RuntimeWorkflow = {
    id: "test.parallel-prep",
    nodes: [
      { id: "start", type: "parallel", depends_on: [] },
      { id: "prepare", type: "validator", depends_on: ["start"] },
      { id: "other", type: "agent", depends_on: ["start"] },
      { id: "approve", type: "approval", depends_on: ["prepare"] },
      { id: "write", type: "tool", tool: "knowledge.write", depends_on: ["approve"] },
    ],
  };
  const task = start(workflow);
  assert.deepEqual(currentNodes(task, workflow).map((node) => node.id), ["other", "prepare"]);
  const prepared = proposeWriteIntent(task, workflow, "knowledge.write", writePayload, task.version);
  assert.equal(prepared.pending_write?.proposed_by_node, "prepare");
});
