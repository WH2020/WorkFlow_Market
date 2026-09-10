import { createHash, randomUUID } from "node:crypto";
import { closeSync, createReadStream, lstatSync, openSync, readFileSync, readSync, realpathSync } from "node:fs";
import { isAbsolute, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
import { StringDecoder } from "node:string_decoder";
import { Check } from "typebox/value";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import {
  createAssistantMessageEventStream, validateToolArguments,
  type AssistantMessage, type Context, type Model, type Api, type SimpleStreamOptions,
} from "@earendil-works/pi-ai";

export type CliApi = "claude-code" | "codex-cli";
type CliThinkingLevel = "off" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max";
type CliModel = { id: string; name?: string; display_name?: string; context_window?: number; max_tokens?: number;
  cli_reasoning?: { supported_efforts: string[]; default_effort: string }; default_thinking_level?: CliThinkingLevel };
const CLI_THINKING_LEVELS: CliThinkingLevel[] = ["off", "minimal", "low", "medium", "high", "xhigh", "max"];
const nativeEffort = (level: string) => level === "off" ? "none" : level;
export type CliBackend = {
  id: string; api: CliApi; base_url: string; executable_path: string; command: string; args: string[];
  version: string; launch_sha256: string; runner_policy_version: number;
  models: CliModel[];
};
export const CLI_ENDPOINTS = { "claude-code": "https://api.anthropic.com", "codex-cli": "https://chatgpt.com/backend-api/codex" };
const MAX_INPUT = 1024 * 1024;
const MAX_OUTPUT = 2 * 1024 * 1024;
const MAX_TEXT = 128 * 1024;
const TIMEOUT_MS = 180_000;
const activeRuns = new Map<string, number>();
const HELP = "CLI 执行失败；请在本机终端检查 CLI 登录和模型可用性。未切换到其他模型或 API。";

export class CliProviderError extends Error {}
function fail(message: string): never { throw new CliProviderError(message); }
const hash = (bytes: string | Buffer) => createHash("sha256").update(bytes).digest("hex");
const object = (value: unknown): value is Record<string, any> => !!value && typeof value === "object" && !Array.isArray(value);
const keysAre = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).sort().join("|") === [...keys].sort().join("|");

type CliLaunch = Pick<CliBackend, "executable_path" | "command" | "args">;
function cliLaunchFiles(entry: CliLaunch): { absolute: string; normalized: string }[] {
  const seen = new Set<string>();
  const files = [];
  for (const path of [entry.executable_path, entry.command, ...entry.args]) {
    if (!isAbsolute(path)) fail("CLI 启动路径无效，请重新检测并配置");
    if (lstatSync(path).isSymbolicLink()) fail("CLI 启动入口不能是重解析链接");
    const absolute = realpathSync(path);
    const normalized = process.platform === "win32" ? absolute.toLowerCase() : absolute;
    if (seen.has(normalized)) continue;
    seen.add(normalized);
    const stat = lstatSync(absolute);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 512 * 1024 * 1024) fail("CLI 启动文件无效");
    files.push({ absolute, normalized });
  }
  return files;
}

/** Same canonical identity as Python; bounded memory, used by local fixture setup. */
export function cliLaunchSha256(entry: CliLaunch): string {
  const buffer = Buffer.allocUnsafe(1024 * 1024);
  const records = cliLaunchFiles(entry).map(({ absolute, normalized }) => {
    const digest = createHash("sha256"); const fd = openSync(absolute, "r");
    try { let count; while ((count = readSync(fd, buffer, 0, buffer.length, null)) !== 0) digest.update(buffer.subarray(0, count)); }
    finally { closeSync(fd); }
    return { path: normalized, sha256: digest.digest("hex") };
  });
  return hash(JSON.stringify(records));
}

/** Runtime binary hashing is interruptible and never allocates an exe-sized Buffer. */
export async function cliLaunchSha256Async(entry: CliLaunch, signal?: AbortSignal): Promise<string> {
  const records = [];
  for (const { absolute, normalized } of cliLaunchFiles(entry)) {
    if (signal?.aborted) fail("CLI 请求已取消");
    const digest = createHash("sha256");
    for await (const chunk of createReadStream(absolute, { highWaterMark: 1024 * 1024, signal })) {
      if (signal?.aborted) fail("CLI 请求已取消");
      digest.update(chunk as Buffer);
    }
    records.push({ path: normalized, sha256: digest.digest("hex") });
  }
  return hash(JSON.stringify(records));
}

export function loadCliBackends(): CliBackend[] {
  const path = process.env.AGENT4MARKET_CLI_BACKENDS_FILE;
  if (!path) return [];
  const stat = lstatSync(path);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size > MAX_INPUT) fail("CLI 配置文件无效");
  const bytes = readFileSync(path);
  if (hash(bytes) !== process.env.AGENT4MARKET_CLI_BACKENDS_SHA256) fail("CLI 配置已变化，请重启智能核心");
  const data = JSON.parse(bytes.toString("utf8"));
  if (!object(data) || data.version !== 1 || !Array.isArray(data.providers) || data.providers.length > 32) fail("CLI 配置格式无效");
  const ids = new Set<string>();
  for (const entry of data.providers) {
    if (!object(entry) || !/^agent4market-[a-zA-Z0-9_-]+$/u.test(entry.id) || ids.has(entry.id) ||
        !(entry.api in CLI_ENDPOINTS) || entry.base_url !== CLI_ENDPOINTS[entry.api as CliApi] ||
        entry.runner_policy_version !== 1 || !Array.isArray(entry.args) || entry.args.length > 2 ||
        !Array.isArray(entry.models) || !entry.models.length || entry.models.length > 128 ||
        typeof entry.version !== "string" || !/^[a-f0-9]{64}$/u.test(entry.launch_sha256)) fail("CLI 配置格式无效");
    ids.add(entry.id);
    for (const model of entry.models) {
      if (!object(model) || typeof model.id !== "string" || !model.id.trim() || model.id.length > 200 || /[\u0000-\u001f]/u.test(model.id)) fail("CLI 模型标识无效");
      cliModelThinking(model as CliModel, entry.api);
    }
  }
  return data.providers as CliBackend[];
}

export function cliModelThinking(model: CliModel, api: CliApi) {
  const reasoning = model.cli_reasoning;
  if (reasoning === undefined) {
    if (model.default_thinking_level !== undefined) fail("CLI 思考配置不完整");
    return { levels: ["off"] as CliThinkingLevel[], defaultLevel: "off" as CliThinkingLevel,
      reasoning: false, thinkingLevelMap: Object.fromEntries(CLI_THINKING_LEVELS.map((level) => [level, level === "off" ? "none" : null])) };
  }
  if (api !== "codex-cli" || !object(reasoning) || !keysAre(reasoning, ["supported_efforts", "default_effort"]) ||
      !Array.isArray(reasoning.supported_efforts) || !reasoning.supported_efforts.length || reasoning.supported_efforts.length > 32 ||
      reasoning.supported_efforts.some((v) => typeof v !== "string" || !/^[a-z][a-z0-9_-]{0,31}$/u.test(v)) ||
      new Set(reasoning.supported_efforts).size !== reasoning.supported_efforts.length || !reasoning.supported_efforts.includes(reasoning.default_effort)) fail("CLI 思考能力配置无效");
  const levels = CLI_THINKING_LEVELS.filter((level) => reasoning.supported_efforts.includes(nativeEffort(level)));
  if (!levels.includes(model.default_thinking_level!)) fail("CLI 默认思考强度无效");
  return { levels, defaultLevel: model.default_thinking_level!, reasoning: levels.some((level) => level !== "off"),
    thinkingLevelMap: Object.fromEntries(CLI_THINKING_LEVELS.map((level) => [level, levels.includes(level) ? nativeEffort(level) : null])) };
}

/** Task defaults come from the same pinned backend as transport; recipient identity is unchanged. */
export function cliTaskThinking(model: Model<Api> | undefined) {
  return cliRecipientThinking(model && { provider_id: model.provider, api: model.api, base_url: model.baseUrl, model_id: model.id });
}

export function cliRecipientThinking(model: { provider_id: string; api: string; base_url: string; model_id: string } | undefined) {
  if (!model || !(model.api in CLI_ENDPOINTS)) return undefined;
  const entry = loadCliBackends().find((item) => item.id === model.provider_id && item.api === model.api && item.base_url === model.base_url);
  const configured = entry?.models.find((item) => item.id === model.model_id);
  if (!entry || !configured) fail("CLI 模型配置不可用，请重启智能核心");
  return cliModelThinking(configured, entry.api);
}

/** Explicit allowlist: no provider keys, proxy, NODE_OPTIONS, hooks, or endpoint overrides. */
export function cliEnvironment(source: NodeJS.ProcessEnv = process.env): NodeJS.ProcessEnv {
  const names = new Set(["PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "COMSPEC", "LANG", "LC_ALL"]);
  const result: NodeJS.ProcessEnv = {};
  for (const [key, value] of Object.entries(source)) if (names.has(key.toUpperCase()) && value !== undefined) result[key] = value;
  // Workbench credentials and per-task CODEX_HOME / CLAUDE_CONFIG_DIR never propagate.
  result.NO_COLOR = "1";
  result.CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC = "1";
  result.DISABLE_AUTOUPDATER = "1";
  result.ANTHROPIC_BASE_URL = CLI_ENDPOINTS["claude-code"];
  result.CLAUDE_CODE_USE_BEDROCK = "0";
  result.CLAUDE_CODE_USE_VERTEX = "0";
  result.CLAUDE_CODE_USE_FOUNDRY = "0";
  return result;
}

export function cliOutputSchema(toolNames: string[]) {
  return { type: "object", additionalProperties: false, required: ["text", "tool_calls"], properties: {
    text: { type: "string" }, tool_calls: { type: "array", maxItems: toolNames.length ? 1 : 0, items: {
      type: "object", additionalProperties: false, required: ["name", "arguments_json"], properties: {
        name: { type: "string", ...(toolNames.length ? { enum: toolNames } : {}) }, arguments_json: { type: "string" },
      },
    } },
  } };
}

const SYSTEM = "You are a single-turn model provider for Agent4Market, not an autonomous coding agent. Follow the supplied task system_prompt and conversation. Native tools are disabled. Return exactly one JSON object with text (string) and tool_calls (array). To use a workbench tool, return at most ONE tool call with its exact name and arguments_json (a JSON-encoded object); the host will validate and execute it and provide its result next turn. Never fabricate tool results, approvals or completed actions. Do not wrap JSON in Markdown. If no tool is needed, return your answer in text and tool_calls: [].";
const DISABLED_CODEX_FEATURES = ["shell_tool", "browser_use", "browser_use_external", "code_mode", "code_mode_host", "computer_use", "image_generation", "apps", "plugins", "multi_agent", "multi_agent_v2", "view_image", "memories", "hooks", "workspace_dependencies", "shell_snapshot", "goals", "skill_search", "tool_suggest", "unbounded_connection_retries"];

/** Codex 0.149.1 adds apply_patch for known models independently of feature flags.
 * A strict per-request catalog preserves the requested model ID but removes all
 * client-side native tool capabilities. Never use its bundled fallback metadata.
 */
export function cliModelCatalog(modelId: string, contextWindow = 32000, thinking?: CliModel["cli_reasoning"]) {
  return { models: [{
    slug: modelId, display_name: modelId, description: "Agent4Market isolated provider",
    default_reasoning_level: thinking?.default_effort ?? null,
    supported_reasoning_levels: thinking?.supported_efforts.map((effort) => ({ effort, description: effort })) ?? [], shell_type: "disabled",
    visibility: "list", minimal_client_version: "0.0.1", supported_in_api: true, priority: 1,
    availability_nux: null, upgrade: null, base_instructions: SYSTEM, model_messages: null,
    include_skills_usage_instructions: false, include_plugin_usage_instructions: false, include_apps_usage_instructions: false,
    supports_reasoning_summary_parameter: true, default_reasoning_summary: "none", support_verbosity: false, default_verbosity: null,
    apply_patch_tool_type: null, web_search_tool_type: "text", truncation_policy: { mode: "bytes", limit: MAX_INPUT },
    supports_parallel_tool_calls: false, supports_image_detail_original: false, context_window: contextWindow, max_context_window: contextWindow,
    auto_compact_token_limit: null, comp_hash: null, effective_context_window_percent: 95, experimental_supported_tools: [],
    input_modalities: ["text"], supports_search_tool: false, supports_experimental_context: false, use_responses_lite: false,
    node_repl_auto_review_required: false, node_repl_disabled: true, auto_review_model_override: null, model_specialty: null,
    tool_mode: null, multi_agent_version: null, multi_agent_reasoning_effort: null, supports_reasoning_summaries: true,
  }] };
}

export function cliArguments(entry: CliBackend, model: string, schema: unknown, level: CliThinkingLevel = "off"): string[] {
  const configured = entry.models.find((item) => item.id === model);
  if (!configured || !cliModelThinking(configured, entry.api).levels.includes(level)) fail("该 CLI 模型不支持所选思考强度；未启动模型请求");
  if (entry.api === "claude-code") return [...entry.args, "--print", "--safe-mode", "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
    "--setting-sources", "", "--no-session-persistence", "--permission-mode", "dontAsk", "--disable-slash-commands", "--no-chrome",
    "--output-format", "json", "--json-schema", JSON.stringify(schema), "--system-prompt", SYSTEM, "--model", model];
  return [...entry.args, "exec", "--strict-config", "--ignore-user-config", "--ignore-rules", "--ephemeral", "--sandbox", "read-only", "--skip-git-repo-check", "--json", "--color", "never",
    "-c", 'model_provider="openai"', "-c", 'forced_login_method="chatgpt"', "-c", 'approval_policy="never"', "-c", 'web_search="disabled"',
    "-c", "skills.include_instructions=false", "-c", "project_doc_max_bytes=0", "-c", "tools.update_plan.enabled=false", "-c", "tools.experimental_request_user_input.enabled=false",
    "-c", 'model_catalog_json="__A4M_CLI_MODEL_CATALOG__"',
    ...(configured.cli_reasoning ? ["-c", `model_reasoning_effort=${JSON.stringify(nativeEffort(level))}`] : []),
    ...DISABLED_CODEX_FEATURES.flatMap((feature) => ["--disable", feature]), "--output-schema", "__A4M_CLI_OUTPUT_SCHEMA__", "--model", model, "-"];
}

function textContent(content: unknown): any[] {
  if (typeof content === "string") return [{ type: "text", text: content }];
  if (!Array.isArray(content)) fail("CLI 上下文格式无效");
  return content.flatMap<Record<string, unknown>>((part) => {
    if (!object(part)) fail("CLI 上下文格式无效");
    if (part.type === "image") fail("CLI 后端当前只支持文字，请改用支持图片的 API 模型");
    if (part.type === "text" && typeof part.text === "string") return [{ type: "text", text: part.text }];
    if (part.type === "toolCall" && typeof part.id === "string" && typeof part.name === "string" && object(part.arguments))
      return [{ type: "toolCall", id: part.id, name: part.name, arguments: part.arguments }];
    if (part.type === "thinking") return [];
    return fail("CLI 不支持该上下文内容类型");
  });
}

export function cliPayload(model: Model<Api>, context: Context) {
  return { protocol: "agent4market-cli-v1", model: model.id, system_prompt: context.systemPrompt ?? "", messages: context.messages.map((message) => ({
    role: message.role, content: textContent(message.content), ...(message.role === "toolResult" ? { tool_call_id: message.toolCallId, name: message.toolName, is_error: message.isError } : {}),
  })), tools: (context.tools ?? []).map((tool) => ({ name: tool.name, description: tool.description, parameters: closedToolSchema(tool.parameters) })) };
}

/** Closed object parameters by default; explicit map/additionalProperties schemas stay open. */
function closedToolSchema(schema: any): any {
  if (Array.isArray(schema)) return schema.map(closedToolSchema);
  if (!object(schema)) return schema;
  const result = { ...schema };
  for (const [key, value] of Object.entries(schema)) result[key] = closedToolSchema(value);
  if (schema.type === "object" && object(schema.properties) && !("additionalProperties" in schema)) result.additionalProperties = false;
  return result;
}

type CliRequest = { command: string; args: string[]; schema: unknown; catalog?: unknown; input: string; signal?: AbortSignal };
export type CliExecution = { stdout: string; code: number; errorCategory?: string };
export type CliExecutor = (request: CliRequest) => Promise<CliExecution>;

/** Python creates the neutral private cwd and a suspended Windows child in a kill-on-close Job. */
export const executeCli: CliExecutor = (request) => new Promise((fulfill, reject) => {
  if (request.signal?.aborted) { reject(new CliProviderError("CLI 请求已取消")); return; }
  const python = process.env.AGENT4MARKET_CLI_PYTHON;
  if (!python || !isAbsolute(python)) { reject(new CliProviderError("CLI 进程宿主不可用，请重启智能核心")); return; }
  const pythonStat = lstatSync(python);
  if (!pythonStat.isFile() || pythonStat.isSymbolicLink()) { reject(new CliProviderError("CLI 进程宿主不可用，请重启智能核心")); return; }
  const host = fileURLToPath(new URL("../../agent_platform/cli_process_host.py", import.meta.url));
  const env = cliEnvironment();
  env.A4M_CLI_LAUNCH = JSON.stringify([request.command, ...request.args]);
  env.A4M_CLI_SCHEMA = JSON.stringify(request.schema);
  if (request.catalog !== undefined) env.A4M_CLI_MODEL_CATALOG = JSON.stringify(request.catalog);
  const child = spawn(python, ["-I", "-B", host], { cwd: resolve(host, ".."), env, shell: false, windowsHide: true, detached: process.platform !== "win32", stdio: ["pipe", "pipe", "pipe"] });
  let failure = "";
  let stdout = "";
  let stdoutBytes = 0;
  let stderrBytes = 0;
  let category: string | undefined;
  let finished = false;
  const decoder = new StringDecoder("utf8");
  const kill = (reason: string) => {
    if (finished || failure) return;
    failure = reason;
    child.stdin.destroy();
    if (process.platform === "win32") child.kill("SIGKILL"); // Host handle closure kills its entire owned Job.
    else if (child.pid) { try { process.kill(-child.pid, "SIGKILL"); } catch { child.kill("SIGKILL"); } }
  };
  const abort = () => kill("CLI 请求已取消");
  request.signal?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(() => kill("CLI 请求超时，已终止本次进程；未重试或切换模型"), TIMEOUT_MS);
  child.stdout.on("data", (chunk: Buffer) => {
    stdoutBytes += chunk.length;
    if (stdoutBytes > MAX_OUTPUT) { kill("CLI 输出超过安全上限，未执行任何工具"); return; }
    if (!failure) stdout += decoder.write(chunk);
  });
  child.stderr.on("data", (chunk: Buffer) => {
    stderrBytes += chunk.length;
    // Classify only, never retain or expose raw stderr (it can contain prompts or credentials).
    const text = chunk.toString("utf8");
    if (text.includes("A4M_CLI_HOST_PRIVATE_PATH")) category = "private-path";
    if (text.includes("A4M_CLI_HOST_INVALID_CONFIG")) category = "host-config";
    if (text.includes("A4M_CLI_HOST_LAUNCH_FAILED")) category = "host-launch";
    if (stderrBytes > MAX_OUTPUT) kill("CLI 错误输出超过安全上限");
  });
  child.stdin.on("error", () => { /* Early CLI exit is handled by close; never echo the input. */ });
  child.on("error", () => { failure ||= "无法启动 CLI 进程，请重新检测安装"; });
  child.on("close", (code) => {
    finished = true; clearTimeout(timer); request.signal?.removeEventListener("abort", abort);
    stdout += decoder.end();
    if (failure) reject(new CliProviderError(failure));
    else fulfill({ stdout, code: code ?? 1, errorCategory: category });
  });
  if (request.signal?.aborted) abort();
  if (!failure) child.stdin.end(request.input, "utf8");
});

export function parseCliResult(api: CliApi, stdout: string, context: Context): { text: string; calls: { name: string; arguments: Record<string, any> }[]; usage?: Record<string, number> } {
  if (Buffer.byteLength(stdout) > MAX_OUTPUT) fail("CLI 输出超过安全上限");
  let envelope: unknown;
  let usage: Record<string, number> | undefined;
  try {
    if (api === "claude-code") {
      const result = JSON.parse(stdout);
      if (!object(result) || result.type !== "result" || result.is_error || result.subtype !== "success") fail(HELP);
      envelope = result.structured_output ?? JSON.parse(result.result);
      usage = object(result.usage) ? result.usage : undefined;
    } else {
      const lines = stdout.trim().split(/\r?\n/u);
      let completed = false;
      let final: string | undefined;
      for (const line of lines) {
        const event = JSON.parse(line);
        if (!object(event)) fail("CLI 响应格式无效");
        if (["error", "turn.failed"].includes(event.type)) fail(HELP);
        if (event.type === "item.completed") {
          if (event.item?.type === "agent_message") {
            if (final !== undefined) fail("CLI 返回了多个最终响应");
            final = event.item.text;
          } else if (event.item?.type !== "reasoning") fail("CLI 尝试使用原生工具，已拒绝本次输出");
        }
        if (event.type === "turn.completed") {
          if (completed) fail("CLI 返回了重复完成事件");
          completed = true; usage = object(event.usage) ? event.usage : undefined;
        }
      }
      if (!completed || typeof final !== "string") fail("CLI 未返回完整结果，未执行任何工具");
      envelope = JSON.parse(final);
    }
  } catch (error) {
    if (error instanceof CliProviderError) throw error;
    return fail("CLI 未返回有效的结构化结果，未执行任何工具");
  }
  if (!object(envelope) || !keysAre(envelope, ["text", "tool_calls"]) || typeof envelope.text !== "string" ||
      Buffer.byteLength(envelope.text) > MAX_TEXT || !Array.isArray(envelope.tool_calls) || envelope.tool_calls.length > 1 ||
      (!envelope.text.trim() && !envelope.tool_calls.length)) fail("CLI 结果不符合单轮工具协议");
  const calls = envelope.tool_calls.map((call) => {
    if (!object(call) || !keysAre(call, ["name", "arguments_json"]) || typeof call.name !== "string" || typeof call.arguments_json !== "string" || Buffer.byteLength(call.arguments_json) > MAX_TEXT) fail("CLI 工具请求格式无效");
    const tool = context.tools?.find((item) => item.name === call.name);
    if (!tool) fail("CLI 请求了未授权的工具");
    try {
      const args = JSON.parse(call.arguments_json);
      if (!object(args)) fail("CLI 工具参数必须是 JSON 对象");
      if (!Check(closedToolSchema(tool.parameters), args)) fail("CLI 工具参数类型或字段不匹配");
      const validated = validateToolArguments(tool, { type: "toolCall", id: "validation-only", name: call.name, arguments: args });
      return { name: call.name, arguments: validated };
    } catch { return fail("CLI 工具参数未通过工作台校验，未执行任何工具"); }
  });
  return { text: envelope.text, calls, usage };
}

export function createCliStream(entry: CliBackend, executor: CliExecutor = executeCli, verify: (signal?: AbortSignal) => void | Promise<void> = async (signal) => {
  const current = loadCliBackends().find((item) => item.id === entry.id);
  if (!current || JSON.stringify(current) !== JSON.stringify(entry) || await cliLaunchSha256Async(entry, signal) !== entry.launch_sha256) fail("CLI 安装或配置已变化，请重新检测并添加新供应商实例");
}) {
  return (model: Model<Api>, context: Context, options?: SimpleStreamOptions) => {
    const stream = createAssistantMessageEventStream();
    const output: AssistantMessage = { role: "assistant", content: [], api: model.api, provider: model.provider, model: model.id,
      usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } }, stopReason: "pending", timestamp: Date.now() };
    void (async () => {
      let acquired = false;
      try {
        stream.push({ type: "start", partial: output });
        if (options?.signal?.aborted) fail("CLI 请求已取消");
        if (model.provider !== entry.id || model.api !== entry.api || model.baseUrl !== entry.base_url || !entry.models.some((item) => item.id === model.id)) fail("CLI 实际接收方与冻结模型不一致");
        if ((activeRuns.get(entry.id) ?? 0) >= 2) fail("该 CLI 已有两个请求运行，请稍后重试");
        activeRuns.set(entry.id, (activeRuns.get(entry.id) ?? 0) + 1); acquired = true;
        await verify(options?.signal);
        if (options?.signal?.aborted) fail("CLI 请求已取消");
        const initial = cliPayload(model, context);
        const replacement = await options?.onPayload?.(initial, model);
        const payload = replacement === undefined ? initial : replacement;
        if (options?.signal?.aborted) fail("CLI 请求已取消");
        if (!object(payload) || payload.protocol !== initial.protocol || payload.model !== model.id || !Array.isArray(payload.messages) || !Array.isArray(payload.tools) || typeof payload.system_prompt !== "string") fail("CLI 请求被边界校验拒绝");
        // A lifecycle callback may narrow context, but cannot grant additional tools.
        const names = payload.tools.map((tool: any) => tool?.name);
        if (names.some((name: unknown) => typeof name !== "string" || !context.tools?.some((tool) => tool.name === name))) fail("CLI 请求包含未授权工具");
        const input = SYSTEM + "\n\nTASK_JSON:\n" + JSON.stringify(payload);
        if (Buffer.byteLength(input) > MAX_INPUT) fail("CLI 上下文超过 1 MiB，请缩小任务范围");
        const schema = cliOutputSchema(names);
        const response = await executor({ command: entry.command, args: cliArguments(entry, model.id, schema, options?.reasoning ?? "off"), schema,
          ...(entry.api === "codex-cli" ? { catalog: cliModelCatalog(model.id, model.contextWindow,
            entry.models.find((item) => item.id === model.id)?.cli_reasoning && {
              supported_efforts: cliModelThinking(entry.models.find((item) => item.id === model.id)!, entry.api).levels.map(nativeEffort),
              default_effort: nativeEffort(options?.reasoning ?? "off"),
            }) } : {}), input, signal: options?.signal });
        await options?.onResponse?.({ status: response.code === 0 ? 200 : 502, headers: { "x-agent4market-transport": entry.api } }, model);
        if (options?.signal?.aborted) fail("CLI 请求已取消");
        if (response.code !== 0) {
          const hostErrors: Record<string, string> = {
            "private-path": "CLI 私有运行目录权限不安全或不可用，未启动 CLI；请检查当前用户目录权限",
            "host-config": "CLI 隔离启动参数无效，请更新适配器后重新检测",
            "host-launch": "无法以隔离模式启动 CLI，请重新检测本机程序",
          };
          fail(hostErrors[response.errorCategory ?? ""] ?? HELP);
        }
        const result = parseCliResult(entry.api, response.stdout, { ...context, tools: context.tools?.filter((tool) => names.includes(tool.name)) });
        if (result.text) {
          const index = output.content.length;
          output.content.push({ type: "text", text: "" });
          stream.push({ type: "text_start", contentIndex: index, partial: output });
          output.content[index] = { type: "text", text: result.text };
          stream.push({ type: "text_delta", contentIndex: index, delta: result.text, partial: output });
          stream.push({ type: "text_end", contentIndex: index, content: result.text, partial: output });
        }
        for (const call of result.calls) {
          const index = output.content.length;
          const toolCall = { type: "toolCall" as const, id: `cli_${randomUUID().replaceAll("-", "")}`, ...call };
          output.content.push(toolCall);
          stream.push({ type: "toolcall_start", contentIndex: index, partial: output });
          stream.push({ type: "toolcall_delta", contentIndex: index, delta: JSON.stringify(call.arguments), partial: output });
          stream.push({ type: "toolcall_end", contentIndex: index, toolCall, partial: output });
        }
        const count = (key: string) => Number.isSafeInteger(result.usage?.[key]) && result.usage![key]! >= 0 ? result.usage![key]! : 0;
        output.usage.input = count("input_tokens"); output.usage.output = count("output_tokens");
        output.usage.cacheRead = count(entry.api === "codex-cli" ? "cached_input_tokens" : "cache_read_input_tokens");
        output.usage.cacheWrite = count("cache_creation_input_tokens");
        if (entry.api === "codex-cli") output.usage.input = Math.max(0, output.usage.input - output.usage.cacheRead);
        output.usage.totalTokens = output.usage.input + output.usage.output + output.usage.cacheRead + output.usage.cacheWrite;
        output.stopReason = result.calls.length ? "toolUse" : "stop";
        stream.push({ type: "done", reason: output.stopReason, message: output });
      } catch (error) {
        output.content = []; output.stopReason = options?.signal?.aborted ? "aborted" : "error";
        output.errorMessage = error instanceof CliProviderError ? error.message : "CLI 配置或响应校验失败，未执行任何工具";
        stream.push({ type: "error", reason: output.stopReason, error: output });
      } finally {
        if (acquired) activeRuns.set(entry.id, Math.max(0, (activeRuns.get(entry.id) ?? 1) - 1));
        stream.end();
      }
    })();
    return stream;
  };
}

export default function registerCliProviders(pi: ExtensionAPI): void {
  for (const entry of loadCliBackends()) {
    pi.registerProvider(entry.id, { name: entry.api === "claude-code" ? "Claude Code" : "Codex CLI", api: entry.api, baseUrl: entry.base_url, apiKey: "cli-managed-auth",
      models: entry.models.map((model) => ({ id: model.id, name: model.display_name ?? model.name ?? model.id,
        reasoning: cliModelThinking(model, entry.api).reasoning, thinkingLevelMap: cliModelThinking(model, entry.api).thinkingLevelMap, input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow: model.context_window ?? 32000, maxTokens: model.max_tokens ?? 4096 })),
      streamSimple: createCliStream(entry),
    });
  }
}
