/** Content-free runtime telemetry. Never retain prompts, results, errors or tool arguments. */
export type BatchProgress = { phase: "batch" | "merge"; current: number; total: number; messages: number; level: number };
export type RuntimeActivity = {
  task_id: string; phase: "starting" | "model" | "tool" | "retry_wait" | "settled" | "error";
  phase_started_at: string; last_activity_at: string;
  error_code?: "MODEL_ERROR" | "MODEL_ABORTED" | "TOOL_ERROR";
  tool?: "wechat" | "other"; batch?: BatchProgress;
};

export function safeBatchProgress(value: unknown): BatchProgress | undefined {
  if (!value || typeof value !== "object") return;
  const item = value as BatchProgress;
  if (!["batch", "merge"].includes(item.phase) || ![item.current, item.total, item.messages, item.level]
    .every(n => Number.isSafeInteger(n) && n >= 0) || item.current < 1 || item.current > item.total ||
    item.total > 128 || item.messages > 1_000_000 || item.level > 128) return;
  return { phase: item.phase, current: item.current, total: item.total, messages: item.messages, level: item.level };
}

export class RuntimeActivityTracker {
  private value?: RuntimeActivity;
  private tools = new Map<string, "wechat" | "other">();
  private clock: () => string;
  constructor(clock: () => string = () => new Date().toISOString()) { this.clock = clock; }
  reset() { this.value = undefined; this.tools.clear(); }
  snapshot(taskId: string): RuntimeActivity | undefined {
    return this.value?.task_id === taskId ? structuredClone(this.value) : undefined;
  }
  private bind(taskId: string) {
    if (this.value?.task_id !== taskId) {
      this.reset(); const at = this.clock();
      this.value = { task_id: taskId, phase: "starting", phase_started_at: at, last_activity_at: at };
    }
    return this.value!;
  }
  phase(taskId: string, phase: RuntimeActivity["phase"]) {
    const item = this.bind(taskId), at = this.clock();
    if (phase !== item.phase) { item.phase_started_at = at; delete item.batch; delete item.tool; }
    item.phase = phase; item.last_activity_at = at;
  }
  start(taskId: string) { this.reset(); this.phase(taskId, "starting"); }
  model(taskId: string) {
    // Automatic model retry is not a final failure. A settled event decides that.
    this.phase(taskId, "model"); delete this.value!.error_code;
  }
  touch(taskId: string) { if (this.value?.task_id === taskId) this.value.last_activity_at = this.clock(); }
  messageEnd(taskId: string, reason: unknown) {
    if (reason === "error" || reason === "aborted") {
      this.phase(taskId, "retry_wait");
      this.value!.error_code = reason === "error" ? "MODEL_ERROR" : "MODEL_ABORTED";
    } else this.touch(taskId);
  }
  toolStart(taskId: string, id: string, name: string) {
    this.phase(taskId, "tool");
    const tool = name === "director_wechat_read" ? "wechat" : "other";
    this.tools.set(id, tool); this.value!.tool = tool;
  }
  toolUpdate(taskId: string, id: string, value: unknown) {
    if (this.value?.task_id !== taskId || this.tools.get(id) !== "wechat") return;
    const batch = safeBatchProgress(value);
    if (batch) { this.value.batch = batch; this.value.tool = "wechat"; this.touch(taskId); }
  }
  toolEnd(taskId: string, id: string, isError: boolean) {
    if (this.value?.task_id !== taskId || !this.tools.delete(id)) return;
    this.touch(taskId);
    if (!this.tools.size) this.phase(taskId, "model");
    if (isError) this.value.error_code = "TOOL_ERROR";
  }
  settled(taskId: string) {
    const code = this.bind(taskId).error_code;
    this.phase(taskId, code ? "error" : "settled"); this.tools.clear();
  }
}
