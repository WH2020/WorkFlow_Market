import {
  closeSync,
  existsSync,
  fsyncSync,
  mkdirSync,
  openSync,
  readFileSync,
  readdirSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { basename, join, resolve } from "node:path";
import { createHash, randomUUID } from "node:crypto";

export type RuntimeNode = {
  id: string;
  type: string;
  depends_on: string[];
  tool?: string;
  permissions?: string[];
};

export type RuntimeWorkflow = {
  id: string;
  nodes: RuntimeNode[];
};

export type TaskStatus =
  | "running"
  | "waiting_approval"
  | "completed"
  | "rejected"
  | "cancelled"
  | "failed";

export type TaskAuditEvent = {
  at: string;
  action: string;
  actor: "system" | "model" | "adapter" | "user";
  node_id?: string;
  note?: string;
};

export type WorkflowTask = {
  schema_version: "1.0";
  task_id: string;
  session_key: string;
  project_id?: string;
  schedule_id?: string;
  scheduled_for?: string;
  profile_id: string;
  service_id: string;
  workflow_id: string;
  request: string;
  status: TaskStatus;
  version: number;
  completed_nodes: string[];
  current_stage: number | null;
  current_node: string | null;
  waiting_nodes: string[];
  waiting_node: string | null;
  artifacts: string[];
  pending_write?: PendingWriteIntent;
  approval_request?: {
    decision: "approve" | "reject" | "cancel";
    requested_at: string;
    requested_by: string;
    expected_version: number;
    intent_id?: string;
    payload_sha256?: string;
  };
  created_at: string;
  updated_at: string;
  audit: TaskAuditEvent[];
};

export type PendingWriteIntent = {
  intent_id: string;
  logical_tool: StructuredWriteTool;
  canonical_payload: string;
  payload_sha256: string;
  proposed_at: string;
  proposed_by_node: string;
  status: "prepared" | "approved" | "committing" | "committed";
  approved_at?: string;
  approved_by_node?: string;
  committed_at?: string;
};

export type StructuredWriteTool = "knowledge.write" | "sales.write" | "artifact.deck.write";

const STRUCTURED_WRITE_TOOLS = new Set<StructuredWriteTool>([
  "knowledge.write",
  "sales.write",
  "artifact.deck.write",
]);

export type CompletionActor = "model" | "adapter" | "user";

export class TaskTransitionError extends Error {}
export class VersionConflictError extends Error {}

const TERMINAL_STATUSES = new Set<TaskStatus>(["completed", "rejected", "cancelled", "failed"]);
const AUTO_NODE_TYPES = new Set(["parallel", "join"]);

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function now(): string {
  return new Date().toISOString();
}

function canonicalValue(value: unknown): unknown {
  if (value === null || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .filter(([, child]) => child !== undefined)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, child]) => [key, canonicalValue(child)]),
    );
  }
  throw new TaskTransitionError("Write intent payload must be finite JSON data");
}

export function canonicalJson(value: unknown): string {
  return JSON.stringify(canonicalValue(value));
}

export function payloadSha256(value: unknown): string {
  return createHash("sha256").update(canonicalJson(value), "utf8").digest("hex");
}

function descendantIds(workflow: RuntimeWorkflow, nodeId: string): Set<string> {
  const result = new Set<string>();
  let changed = true;
  while (changed) {
    changed = false;
    for (const node of workflow.nodes) {
      if (!result.has(node.id) && (node.depends_on.includes(nodeId) || node.depends_on.some((id) => result.has(id)))) {
        result.add(node.id);
        changed = true;
      }
    }
  }
  return result;
}

function approvalWriteTools(workflow: RuntimeWorkflow, approvalNodeId: string): string[] {
  return workflow.nodes
    .filter((node) => node.depends_on.includes(approvalNodeId) && node.type === "tool" && STRUCTURED_WRITE_TOOLS.has(node.tool as StructuredWriteTool))
    .map((node) => node.tool!);
}

export function isTerminal(state: WorkflowTask): boolean {
  return TERMINAL_STATUSES.has(state.status);
}

export function planStages(workflow: RuntimeWorkflow): RuntimeNode[][] {
  const nodes = new Map(workflow.nodes.map((node) => [node.id, node]));
  if (nodes.size !== workflow.nodes.length) {
    throw new TaskTransitionError(`Workflow ${workflow.id} contains duplicate node IDs`);
  }
  const remaining = new Set(nodes.keys());
  const completed = new Set<string>();
  const stages: RuntimeNode[][] = [];
  while (remaining.size > 0) {
    const ready = [...remaining]
      .filter((id) => {
        const node = nodes.get(id)!;
        return node.depends_on.every((dependency) => nodes.has(dependency) && completed.has(dependency));
      })
      .sort()
      .map((id) => nodes.get(id)!);
    if (ready.length === 0) {
      throw new TaskTransitionError(`Workflow ${workflow.id} is cyclic or has a missing dependency`);
    }
    stages.push(ready);
    for (const node of ready) {
      remaining.delete(node.id);
      completed.add(node.id);
    }
  }
  return stages;
}

function appendAudit(
  state: WorkflowTask,
  action: string,
  actor: TaskAuditEvent["actor"],
  nodeId?: string,
  note?: string,
): void {
  const event: TaskAuditEvent = { at: now(), action, actor };
  if (nodeId !== undefined) event.node_id = nodeId;
  if (note !== undefined) event.note = note;
  state.audit.push(event);
  state.updated_at = state.audit[state.audit.length - 1]!.at;
  state.version += 1;
}

function refreshDerivedState(state: WorkflowTask, workflow: RuntimeWorkflow): void {
  if (isTerminal(state)) return;
  const completed = new Set(state.completed_nodes);
  const stages = planStages(workflow);
  let stageIndex = stages.findIndex((stage) => stage.some((node) => !completed.has(node.id)));
  while (stageIndex >= 0) {
    const automatic = stages[stageIndex]!.filter(
      (node) => !completed.has(node.id) && AUTO_NODE_TYPES.has(node.type),
    );
    if (automatic.length === 0) break;
    for (const node of automatic) {
      completed.add(node.id);
      state.completed_nodes.push(node.id);
      appendAudit(state, "auto_complete", "system", node.id);
    }
    stageIndex = stages.findIndex((stage) => stage.some((node) => !completed.has(node.id)));
  }
  if (stageIndex < 0) {
    state.current_stage = null;
    state.current_node = null;
    state.waiting_nodes = [];
    state.waiting_node = null;
    state.status = "completed";
    appendAudit(state, "task_completed", "system");
    return;
  }
  state.current_stage = stageIndex;
  const pending = stages[stageIndex]!.filter((node) => !completed.has(node.id));
  state.current_node = pending[0]?.id ?? null;
  state.waiting_nodes = pending.filter((node) => node.type === "approval").map((node) => node.id);
  state.waiting_node = state.waiting_nodes[0] ?? null;
  state.status = state.waiting_nodes.length > 0 ? "waiting_approval" : "running";
}

export function createTask(input: {
  sessionKey: string;
  profileId: string;
  serviceId: string;
  workflow: RuntimeWorkflow;
  request: string;
  taskId?: string;
  projectId?: string;
  scheduleId?: string;
  scheduledFor?: string;
}): WorkflowTask {
  const timestamp = now();
  const state: WorkflowTask = {
    schema_version: "1.0",
    task_id: input.taskId ?? randomUUID(),
    session_key: input.sessionKey,
    ...(input.projectId ? { project_id: input.projectId } : {}),
    ...(input.scheduleId ? { schedule_id: input.scheduleId } : {}),
    ...(input.scheduledFor ? { scheduled_for: input.scheduledFor } : {}),
    profile_id: input.profileId,
    service_id: input.serviceId,
    workflow_id: input.workflow.id,
    request: input.request,
    status: "running",
    version: 1,
    completed_nodes: [],
    current_stage: 0,
    current_node: null,
    waiting_nodes: [],
    waiting_node: null,
    artifacts: [],
    created_at: timestamp,
    updated_at: timestamp,
    audit: [{ at: timestamp, action: "task_started", actor: "user" }],
  };
  refreshDerivedState(state, input.workflow);
  return state;
}

export function currentNodes(state: WorkflowTask, workflow: RuntimeWorkflow): RuntimeNode[] {
  if (isTerminal(state) || state.current_stage === null) return [];
  const completed = new Set(state.completed_nodes);
  return planStages(workflow)[state.current_stage]!.filter((node) => !completed.has(node.id));
}

function assertExpectedVersion(state: WorkflowTask, expectedVersion: number): void {
  if (state.version !== expectedVersion) {
    throw new VersionConflictError(`Expected task version ${expectedVersion}, found ${state.version}`);
  }
}

function assertCurrentNode(state: WorkflowTask, workflow: RuntimeWorkflow, nodeId: string): RuntimeNode {
  if (isTerminal(state)) throw new TaskTransitionError(`Task ${state.task_id} is ${state.status}`);
  const node = currentNodes(state, workflow).find((candidate) => candidate.id === nodeId);
  if (!node) throw new TaskTransitionError(`Node ${nodeId} is not pending in the current stage`);
  return node;
}

export function completeModelNode(
  source: WorkflowTask,
  workflow: RuntimeWorkflow,
  nodeId: string,
  expectedVersion: number,
  note?: string,
): WorkflowTask {
  assertExpectedVersion(source, expectedVersion);
  const state = clone(source);
  const node = assertCurrentNode(state, workflow, nodeId);
  if (node.type !== "agent" && node.type !== "validator") {
    throw new TaskTransitionError(
      `Model completion is limited to agent/validator nodes; ${nodeId} is ${node.type}`,
    );
  }
  const directApprovals = workflow.nodes.filter(
    (candidate) => candidate.type === "approval" && candidate.depends_on.includes(node.id),
  );
  const protectedWrites = workflow.nodes.filter(
    (candidate) =>
      candidate.type === "tool" &&
      STRUCTURED_WRITE_TOOLS.has(candidate.tool as StructuredWriteTool) &&
      directApprovals.some((approval) => candidate.depends_on.includes(approval.id)),
  );
  if (protectedWrites.length > 0) {
    if (
      protectedWrites.length !== 1 ||
      !state.pending_write ||
      state.pending_write.status !== "prepared" ||
      state.pending_write.logical_tool !== protectedWrites[0]!.tool
    ) {
      throw new TaskTransitionError(
        `Prepare the exact ${protectedWrites[0]?.tool ?? "structured write"} payload before completing ${node.id}`,
      );
    }
  }
  state.completed_nodes.push(node.id);
  appendAudit(state, "node_completed", "model", node.id, note);
  refreshDerivedState(state, workflow);
  return state;
}

export function completeSubagentNode(
  source: WorkflowTask,
  workflow: RuntimeWorkflow,
  nodeId: string,
  expectedVersion: number,
  receiptPath: string,
  receiptSha256: string,
  note?: string,
): WorkflowTask {
  assertExpectedVersion(source, expectedVersion);
  const state = clone(source);
  const node = assertCurrentNode(state, workflow, nodeId);
  if (node.type !== "subagent") {
    throw new TaskTransitionError(`Subagent completion is limited to subagent nodes; ${nodeId} is ${node.type}`);
  }
  if (!/^\.pi\/director-runtime\/subagent-results\/[a-f0-9-]{36}\.json$/u.test(receiptPath)) {
    throw new TaskTransitionError("Subagent completion receipt path is invalid");
  }
  if (!/^[a-f0-9]{64}$/u.test(receiptSha256)) {
    throw new TaskTransitionError("Subagent completion receipt SHA-256 is invalid");
  }
  state.completed_nodes.push(node.id);
  if (!state.artifacts.includes(receiptPath)) state.artifacts.push(receiptPath);
  appendAudit(
    state,
    "subagent_completed",
    "adapter",
    node.id,
    JSON.stringify({ receipt: receiptPath, sha256: receiptSha256, ...(note ? { note: note.slice(0, 1000) } : {}) }),
  );
  refreshDerivedState(state, workflow);
  return state;
}

export function proposeWriteIntent(
  source: WorkflowTask,
  workflow: RuntimeWorkflow,
  logicalTool: StructuredWriteTool,
  payload: unknown,
  expectedVersion: number,
): WorkflowTask {
  assertExpectedVersion(source, expectedVersion);
  const state = clone(source);
  const currentModelNodes = currentNodes(state, workflow).filter(
    (node) => node.type === "agent" || node.type === "validator",
  );
  const candidates = currentModelNodes.filter((node) => {
    const approvals = workflow.nodes.filter(
      (candidate) => candidate.type === "approval" && candidate.depends_on.includes(node.id),
    );
    return workflow.nodes.some(
      (candidate) =>
        candidate.type === "tool" &&
        candidate.tool === logicalTool &&
        approvals.some((approval) => candidate.depends_on.includes(approval.id)),
    );
  });
  if (candidates.length !== 1) {
    throw new TaskTransitionError("A write intent can only be prepared at one current agent/validator node");
  }
  const approvals = workflow.nodes.filter(
    (node) => node.type === "approval" && node.depends_on.includes(candidates[0]!.id),
  );
  const matchingWrites = workflow.nodes.filter((node) =>
    node.type === "tool" &&
    node.tool === logicalTool &&
    approvals.some((approval) => node.depends_on.includes(approval.id)),
  );
  if (matchingWrites.length !== 1) {
    throw new TaskTransitionError(`Write intent must target exactly one future ${logicalTool} node`);
  }
  const canonical = canonicalJson(payload);
  if (Buffer.byteLength(canonical, "utf8") > 256 * 1024) {
    throw new TaskTransitionError("Write intent payload exceeds the 256 KiB safety limit");
  }
  state.pending_write = {
    intent_id: randomUUID(),
    logical_tool: logicalTool,
    canonical_payload: canonical,
    payload_sha256: createHash("sha256").update(canonical, "utf8").digest("hex"),
    proposed_at: now(),
    proposed_by_node: candidates[0]!.id,
    status: "prepared",
  };
  appendAudit(state, "write_intent_prepared", "model", candidates[0]!.id, state.pending_write.payload_sha256);
  return state;
}

export function assertApprovedWriteIntent(
  state: WorkflowTask,
  logicalTool: string,
  payload: unknown,
): PendingWriteIntent {
  const intent = state.pending_write;
  if (!intent || intent.logical_tool !== logicalTool || (intent.status !== "approved" && intent.status !== "committing")) {
    throw new TaskTransitionError(`Logical write ${logicalTool} has no approved frozen payload`);
  }
  const actual = payloadSha256(payload);
  if (actual !== intent.payload_sha256) {
    throw new TaskTransitionError("Write payload differs from the exact payload approved by the user");
  }
  return intent;
}

export function beginWriteCommit(
  source: WorkflowTask,
  workflow: RuntimeWorkflow,
  logicalTool: StructuredWriteTool,
  payload: unknown,
  expectedVersion: number,
): WorkflowTask {
  assertExpectedVersion(source, expectedVersion);
  const state = clone(source);
  const matches = currentNodes(state, workflow).filter(
    (node) => node.type === "tool" && node.tool === logicalTool,
  );
  if (matches.length !== 1) throw new TaskTransitionError(`Logical tool ${logicalTool} is not current`);
  const intent = assertApprovedWriteIntent(state, logicalTool, payload);
  if (intent.status === "committing") return state;
  state.pending_write!.status = "committing";
  appendAudit(state, "write_commit_started", "adapter", matches[0]!.id, intent.payload_sha256);
  return state;
}

export function recoverWriteFailure(
  source: WorkflowTask,
  logicalTool: StructuredWriteTool,
  payload: unknown,
  outcome: "not_committed" | "ambiguous",
  expectedVersion: number,
  note?: string,
): WorkflowTask {
  assertExpectedVersion(source, expectedVersion);
  const state = clone(source);
  const intent = assertApprovedWriteIntent(state, logicalTool, payload);
  if (intent.status !== "committing") throw new TaskTransitionError("Write is not in committing state");
  if (outcome === "not_committed") {
    state.pending_write!.status = "approved";
    appendAudit(state, "write_commit_rolled_back", "adapter", undefined, note);
    return state;
  }
  state.status = "failed";
  state.current_stage = null;
  state.current_node = null;
  state.waiting_nodes = [];
  state.waiting_node = null;
  appendAudit(state, "write_commit_ambiguous", "adapter", undefined, note);
  return state;
}

export function completeLogicalTool(
  source: WorkflowTask,
  workflow: RuntimeWorkflow,
  logicalTool: string,
  expectedVersion: number,
  note?: string,
  payload?: unknown,
): WorkflowTask {
  assertExpectedVersion(source, expectedVersion);
  const state = clone(source);
  const matches = currentNodes(state, workflow).filter(
    (node) => node.type === "tool" && node.tool === logicalTool,
  );
  if (matches.length !== 1) {
    throw new TaskTransitionError(
      `Logical tool ${logicalTool} must match exactly one current tool node; found ${matches.length}`,
    );
  }
  if (STRUCTURED_WRITE_TOOLS.has(logicalTool as StructuredWriteTool)) {
    assertApprovedWriteIntent(state, logicalTool, payload);
    state.pending_write!.status = "committed";
    state.pending_write!.committed_at = now();
  }
  if (logicalTool === "artifact.deck.write" && note) {
    try {
      const details = JSON.parse(note) as { path?: unknown; receipt?: unknown };
      for (const candidate of [details.path, details.receipt]) {
        if (typeof candidate === "string" && candidate && !state.artifacts.includes(candidate)) {
          state.artifacts.push(candidate);
        }
      }
    } catch {
      // Tool completion remains valid even if an older adapter supplied a non-JSON note.
    }
  }
  state.completed_nodes.push(matches[0]!.id);
  appendAudit(state, "tool_completed", "adapter", matches[0]!.id, note);
  refreshDerivedState(state, workflow);
  return state;
}

export function approveNode(
  source: WorkflowTask,
  workflow: RuntimeWorkflow,
  nodeId: string,
  expectedVersion: number,
  note?: string,
): WorkflowTask {
  assertExpectedVersion(source, expectedVersion);
  const state = clone(source);
  const node = assertCurrentNode(state, workflow, nodeId);
  if (node.type !== "approval") {
    throw new TaskTransitionError(`Only an approval node can be approved; ${nodeId} is ${node.type}`);
  }
  const writeTools = [...new Set(approvalWriteTools(workflow, node.id))];
  if (writeTools.length > 0) {
    if (writeTools.length !== 1 || !state.pending_write || state.pending_write.status !== "prepared") {
      throw new TaskTransitionError("Approval before a structured write requires exactly one prepared write intent");
    }
    if (state.pending_write.logical_tool !== writeTools[0]) {
      throw new TaskTransitionError("Prepared write intent does not match the write protected by this approval");
    }
    state.pending_write.status = "approved";
    state.pending_write.approved_at = now();
    state.pending_write.approved_by_node = node.id;
  }
  state.completed_nodes.push(node.id);
  const boundNote = state.pending_write?.status === "approved"
    ? `intent=${state.pending_write.intent_id} sha256=${state.pending_write.payload_sha256}${note ? `; ${note}` : ""}`
    : note;
  appendAudit(state, "approval_granted", "user", node.id, boundNote);
  refreshDerivedState(state, workflow);
  return state;
}

export function rejectApproval(
  source: WorkflowTask,
  workflow: RuntimeWorkflow,
  nodeId: string,
  expectedVersion: number,
  note?: string,
): WorkflowTask {
  assertExpectedVersion(source, expectedVersion);
  const state = clone(source);
  const node = assertCurrentNode(state, workflow, nodeId);
  if (node.type !== "approval") {
    throw new TaskTransitionError(`Only an approval node can be rejected; ${nodeId} is ${node.type}`);
  }
  state.status = "rejected";
  state.current_stage = null;
  state.current_node = null;
  state.waiting_nodes = [];
  state.waiting_node = null;
  appendAudit(state, "approval_rejected", "user", node.id, note);
  return state;
}

export function cancelTask(
  source: WorkflowTask,
  expectedVersion: number,
  note?: string,
): WorkflowTask {
  assertExpectedVersion(source, expectedVersion);
  if (isTerminal(source)) throw new TaskTransitionError(`Task ${source.task_id} is already ${source.status}`);
  if (source.pending_write?.status === "committing") {
    throw new TaskTransitionError("A structured write is committing; reconcile it before cancellation");
  }
  const state = clone(source);
  state.status = "cancelled";
  state.current_stage = null;
  state.current_node = null;
  state.waiting_nodes = [];
  state.waiting_node = null;
  appendAudit(state, "task_cancelled", "user", undefined, note);
  return state;
}

export function consumeApprovalRequest(
  source: WorkflowTask,
  workflow: RuntimeWorkflow,
): WorkflowTask {
  const request = source.approval_request;
  if (!request) throw new TaskTransitionError("Task has no external approval request");
  if (source.version !== request.expected_version + 1) {
    throw new VersionConflictError(
      `External decision expected base version ${request.expected_version}, found ${source.version}`,
    );
  }
  const clean: WorkflowTask = clone(source);
  delete clean.approval_request;
  if (request.decision === "approve" && clean.pending_write?.status === "prepared") {
    if (
      request.intent_id !== clean.pending_write.intent_id ||
      request.payload_sha256 !== clean.pending_write.payload_sha256
    ) {
      throw new TaskTransitionError("External approval is not bound to the current frozen write intent");
    }
  } else if (request.intent_id !== undefined || request.payload_sha256 !== undefined) {
    throw new TaskTransitionError("External approval supplied a write intent for a task without one");
  }
  const note = `${request.requested_by} @ ${request.requested_at}`;
  if (request.decision === "cancel") return cancelTask(clean, clean.version, note);
  if (clean.status !== "waiting_approval" || clean.waiting_nodes.length !== 1) {
    throw new TaskTransitionError("External approve/reject requires exactly one current approval node");
  }
  const nodeId = clean.waiting_nodes[0]!;
  return request.decision === "approve"
    ? approveNode(clean, workflow, nodeId, clean.version, note)
    : rejectApproval(clean, workflow, nodeId, clean.version, note);
}

function validateTaskId(taskId: string): void {
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(taskId)) throw new Error(`Unsafe task ID: ${taskId}`);
}

export function acquireTaskLock(path: string): number {
  try {
    const descriptor = openSync(path, "wx", 0o600);
    const nonce = randomUUID();
    writeFileSync(descriptor, JSON.stringify({ pid: process.pid, nonce, created_at: new Date().toISOString() }), "utf8");
    fsyncSync(descriptor);
    ownedTaskLocks.set(descriptor, nonce);
    return descriptor;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "EEXIST") {
      throw new VersionConflictError(`Task lock is held: ${path}`);
    }
    throw error;
  }
}

const ownedTaskLocks = new Map<number, string>();

export function releaseTaskLock(path: string, descriptor: number): void {
  const nonce = ownedTaskLocks.get(descriptor);
  ownedTaskLocks.delete(descriptor);
  closeSync(descriptor);
  if (!nonce) return;
  try {
    const owner = JSON.parse(readFileSync(path, "utf8")) as { nonce?: unknown };
    if (owner.nonce === nonce) rmSync(path, { force: true });
  } catch {
    // Never delete a lock whose nonce cannot be verified.
  }
}

export class TaskStore {
  readonly directory: string;

  constructor(projectRoot: string) {
    this.directory = resolve(projectRoot, ".pi", "director-runtime", "tasks");
  }

  private pathFor(taskId: string): string {
    validateTaskId(taskId);
    return join(this.directory, `${taskId}.json`);
  }

  load(taskId: string): WorkflowTask | undefined {
    const path = this.pathFor(taskId);
    if (!existsSync(path)) return undefined;
    return JSON.parse(readFileSync(path, "utf8")) as WorkflowTask;
  }

  findActive(sessionKey: string): WorkflowTask | undefined {
    if (!existsSync(this.directory)) return undefined;
    const active = readdirSync(this.directory)
      .filter((name) => name.endsWith(".json"))
      .map((name) => this.load(basename(name, ".json")))
      .filter((task): task is WorkflowTask => Boolean(task))
      .filter((task) => task.session_key === sessionKey && !isTerminal(task));
    if (active.length > 1) {
      throw new TaskTransitionError(`Session has ${active.length} active tasks; manual recovery is required`);
    }
    return active[0];
  }

  save(state: WorkflowTask, expectedVersion?: number): void {
    mkdirSync(this.directory, { recursive: true });
    const path = this.pathFor(state.task_id);
    const lockPath = `${path}.lock`;
    let lock: number | undefined;
    let temporaryHandle: number | undefined;
    let temporary: string | undefined;
    try {
      lock = acquireTaskLock(lockPath);
      const current = this.load(state.task_id);
      if (expectedVersion === undefined) {
        if (current) throw new VersionConflictError(`Task ${state.task_id} already exists`);
      } else if (!current || current.version !== expectedVersion) {
        throw new VersionConflictError(
          `Expected persisted version ${expectedVersion}, found ${current?.version ?? "missing"}`,
        );
      }
      if (expectedVersion !== undefined && state.version <= expectedVersion) {
        throw new VersionConflictError(
          `New task version ${state.version} must be greater than ${expectedVersion}`,
        );
      }
      temporary = `${path}.${process.pid}.${randomUUID()}.tmp`;
      temporaryHandle = openSync(temporary, "wx", 0o600);
      writeFileSync(temporaryHandle, `${JSON.stringify(state, null, 2)}\n`, "utf8");
      fsyncSync(temporaryHandle);
      closeSync(temporaryHandle);
      temporaryHandle = undefined;
      renameSync(temporary, path);
      temporary = undefined;
    } finally {
      if (temporaryHandle !== undefined) closeSync(temporaryHandle);
      if (lock !== undefined) releaseTaskLock(lockPath, lock);
      if (temporary && existsSync(temporary)) rmSync(temporary, { force: true });
    }
  }

}
