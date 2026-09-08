import { createHash } from "node:crypto";
import { lstatSync, readFileSync } from "node:fs";
import type { WorkflowTask } from "./task-runtime.ts";

export type ModelRecipient = { provider_id: string; base_url: string; api: string; model_id: string };

export function managedModelCatalog(): Record<string, ModelRecipient> | undefined {
  let value = process.env.AGENT4MARKET_MANAGED_MODELS;
  const file = process.env.AGENT4MARKET_MANAGED_MODELS_FILE;
  if (file) {
    const stat = lstatSync(file);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 32 * 1024 * 1024) throw new Error("受管模型目录文件无效");
    const bytes = readFileSync(file);
    if (createHash("sha256").update(bytes).digest("hex") !== process.env.AGENT4MARKET_MANAGED_MODELS_SHA256) {
      throw new Error("受管模型目录已变化，请重启智能核心；不会使用未核验的接收地址");
    }
    value = bytes.toString("utf8");
  }
  if (!value) return undefined;
  const parsed = JSON.parse(value) as Record<string, ModelRecipient>;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("受管模型目录无效");
  return parsed;
}

export function modelRecipient(key: string | undefined): ModelRecipient | undefined {
  return key ? managedModelCatalog()?.[key] : undefined;
}

export function frozenRoleConfiguration(): { roleModels: Record<string, string>; roleRecipients: Record<string, ModelRecipient> } {
  const roles = JSON.parse(process.env.AGENT4MARKET_ROLE_MODELS || "{}") as Record<string, string>;
  const catalog = managedModelCatalog();
  const recipients: Record<string, ModelRecipient> = {};
  for (const [role, key] of Object.entries(roles)) {
    if (!["director-research-scout", "director-readonly-reviewer"].includes(role) || typeof key !== "string" || (catalog && !catalog[key])) {
      throw new Error("只读角色的模型配置无效");
    }
    if (catalog?.[key]) recipients[role] = { ...catalog[key] };
  }
  return { roleModels: { ...roles }, roleRecipients: recipients };
}

export function runtimeModelRecipient(model: { provider: string; id: string; api: string; baseUrl: string } | undefined): ModelRecipient | undefined {
  if (!model) return undefined;
  const base = model.baseUrl.replace(/\/+$/u, "");
  return { provider_id: model.provider, model_id: model.id, api: model.api,
    base_url: model.api === "anthropic-messages" ? base : base.replace(/\/v1$/u, "") };
}

export function sameRecipient(left: unknown, right: unknown): boolean {
  if (!left || !right || typeof left !== "object" || typeof right !== "object") return false;
  const a = left as ModelRecipient;
  const b = right as ModelRecipient;
  return ["provider_id", "base_url", "api", "model_id"].every((key) =>
    typeof a[key as keyof ModelRecipient] === "string" && !!a[key as keyof ModelRecipient] &&
    a[key as keyof ModelRecipient] === b[key as keyof ModelRecipient]);
}

/** Only the current task's messages may cross a newly selected provider boundary. */
export function taskScopedMessages<T extends { role: string }>(messages: T[], taskId: string, boundary: {
  entries?: readonly unknown[]; model?: string; recipient?: ModelRecipient; taskVersion?: number;
} = {}): T[] {
  const marker = `[DIRECTOR_TASK_CONTEXT ${taskId}]`;
  const start = messages.findIndex((message) => {
    if (message.role !== "user") return false;
    const content = (message as T & { content?: unknown }).content;
    return typeof content === "string" ? content.includes(marker) : Array.isArray(content) && content.some((part: unknown) => {
      if (!part || typeof part !== "object") return false;
      const block = part as { type?: string; text?: string };
      return block.type === "text" && typeof block.text === "string" && block.text.includes(marker);
    });
  });
  if (start >= 0) return messages.slice(start);
  // A compaction marker is structured, locally generated and bound to the same task/recipient.
  // Never rely on an LLM-generated summary preserving the original user marker verbatim.
  const checkpoint = boundary.entries?.find((entry: any) => entry?.type === "compaction") as any;
  const details = checkpoint?.details;
  if (checkpoint?.fromHook === true && details?.kind === "agent4market-task-checkpoint" &&
      details.task_id === taskId && details.effective_model === boundary.model &&
      Number.isInteger(details.task_version) && details.task_version >= 1 &&
      (boundary.taskVersion === undefined || details.task_version <= boundary.taskVersion) &&
      (!boundary.recipient || sameRecipient(details.effective_recipient, boundary.recipient)) &&
      typeof checkpoint.summary === "string" && details.summary_sha256 === hash(checkpoint.summary)) {
    const summaryIndex = messages.findIndex((message: any) => message.role === "compactionSummary" && message.summary === checkpoint.summary);
    if (summaryIndex >= 0) return messages.slice(summaryIndex);
  }
  throw new Error("任务上下文边界不可用，请恢复任务会话后再继续；未向模型发送历史内容");
}

function hash(value: string): string { return createHash("sha256").update(value).digest("hex"); }

/** No remote summarizer: its SDK path bypasses normal request/context guards. */
export function taskCheckpoint(task: WorkflowTask) {
  const summary = "当前受管任务的本地检查点；这是状态摘要，不是新增业务事实。继续前核对 DAG 和本地资料；未保存的长篇分析需重新核验。\n" + JSON.stringify({
    task_id: task.task_id, workflow_id: task.workflow_id, request: task.request.slice(0, 8000),
    status: task.status, current_node: task.current_node, waiting_nodes: task.waiting_nodes,
    completed_nodes: task.completed_nodes, artifacts: task.artifacts.slice(-30).map((path) => path.slice(0, 250)),
    recent_audit: task.audit.slice(-8).map((event) => ({ ...event, note: event.note?.slice(0, 800) })),
    pending_write: task.pending_write ? { intent_id: task.pending_write.intent_id, logical_tool: task.pending_write.logical_tool,
      payload_sha256: task.pending_write.payload_sha256, status: task.pending_write.status } : undefined,
  });
  return { summary, details: { schema_version: "1.0", kind: "agent4market-task-checkpoint", task_id: task.task_id,
    task_version: task.version, effective_model: task.effective_model, effective_recipient: task.effective_recipient,
    summary_sha256: hash(summary) } };
}
