/** Text-only model transport. No agent session, extensions, tools or workspace context. */
import { createInterface } from "node:readline";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";
import type { Api, Context, Model, SimpleStreamOptions } from "@earendil-works/pi-ai";
import { streamSimple as completions } from "@earendil-works/pi-ai/api/openai-completions";
import { streamSimple as responses } from "@earendil-works/pi-ai/api/openai-responses";
import { streamSimple as anthropic } from "@earendil-works/pi-ai/api/anthropic-messages";
import { cliLaunchSha256Async, cliModelThinking, createCliStream, executeCli, type CliBackend, type CliExecutor } from "./cli-model-provider.ts";

export const CHAT_SYSTEM = "你是工作台的自由聊天助手，默认用中文进行自然、清楚的多轮对话。只根据本次对话中用户主动输入的文字回答。你没有文件、业务资料、微信记录、网页、终端或其他工具的访问能力，不能创建任务、执行操作或代替用户审批。不要声称已经查询本地资料、联网核验或完成操作。需要这些能力时，说明应转到工作台相应功能。区分已知信息与推测，无法确定时直说。";
export type ChatMessage = { role: "user" | "assistant"; content: string };
export type ChatInput = {
  model: Model<Api>; api_key?: string; cli?: CliBackend;
  messages: ChatMessage[]; thinking: "off" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max";
};
export type ChatEvent = { type: "delta"; text: string } | { type: "done"; text: string; limited: boolean }
  | { type: "error"; code: string };
const MAX_OUTPUT = 128 * 1024;
const MAX_RESPONSE = 2 * 1024 * 1024;
const ZERO_USAGE = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } };

function contextFor(request: ChatInput): Context {
  if (!Array.isArray(request.messages) || request.messages.length < 1 || request.messages.length > 41 ||
      request.messages.some((item, index) => !item || item.role !== (index % 2 ? "assistant" : "user") ||
        typeof item.content !== "string" || !item.content || Buffer.byteLength(item.content) > MAX_OUTPUT) ||
      request.messages.at(-1)?.role !== "user") throw new Error("INVALID_INPUT");
  const model = request.model;
  if (!model || !model.id || !model.provider || !model.baseUrl || !Number.isSafeInteger(model.maxTokens) ||
      !Number.isSafeInteger(model.contextWindow) || model.maxTokens < 1 || model.contextWindow < 1) throw new Error("INVALID_MODEL");
  return { systemPrompt: CHAT_SYSTEM, tools: [], messages: request.messages.map((item) => item.role === "user"
    ? { role: "user", content: item.content, timestamp: Date.now() }
    : { role: "assistant", content: [{ type: "text", text: item.content }], api: model.api, provider: model.provider,
      model: model.id, stopReason: "stop", usage: { ...ZERO_USAGE }, timestamp: Date.now() }) };
}

/** One configured endpoint only; redirects cannot forward dialogue or credentials. */
export function endpointFetch(model: Model<Api>, transport: typeof fetch = globalThis.fetch, onLimit = () => {}, onStatus = (_status: number) => {}): typeof fetch {
  const suffix = model.api === "anthropic-messages" ? "/v1/messages" : model.api === "openai-responses" ? "/responses" : "/chat/completions";
  const expected = new URL(model.baseUrl.replace(/\/+$/u, "") + suffix);
  return async (input, options) => {
    const request = new Request(input, options);
    const actual = new URL(request.url);
    if (request.method !== "POST" || actual.origin !== expected.origin || actual.pathname !== expected.pathname ||
        [...actual.searchParams].some(([key, value]) => key !== "beta" || value !== "true") || actual.username || actual.password) {
      throw new Error("RECIPIENT_MISMATCH");
    }
    const response = await transport(request, { redirect: "error" });
    onStatus(response.status);
    if (!response.body) return response;
    const reader = response.body.getReader();
    let bytes = 0;
    const body = new ReadableStream<Uint8Array>({
      async pull(controller) {
        try {
          const part = await reader.read();
          if (part.done) { controller.close(); return; }
          bytes += part.value.byteLength;
          if (bytes > MAX_RESPONSE) {
            onLimit(); void reader.cancel().catch(() => {});
            controller.error(new Error("OUTPUT_LIMIT")); return;
          }
          controller.enqueue(part.value);
        } catch (error) { controller.error(error); }
      },
      cancel: (reason) => reader.cancel(reason),
    });
    return new Response(body, { status: response.status, statusText: response.statusText, headers: response.headers });
  };
}

export async function runChatTurn(request: ChatInput, emit: (event: ChatEvent) => void, signal: AbortSignal,
  dependencies: { fetch?: typeof fetch; cliExecutor?: CliExecutor; verifyCli?: (signal?: AbortSignal) => Promise<void> } = {}) {
  let status = 0, responseLimit = false;
  try {
    if (signal.aborted) throw new Error("CANCELLED");
    const context = contextFor(request), model = request.model;
    const controller = new AbortController();
    const abort = () => controller.abort();
    signal.addEventListener("abort", abort, { once: true });
    const timeout = setTimeout(abort, 180_000);
    let text = "", bytes = 0, finished = false;
    try {
      if (signal.aborted) abort();
      const options: SimpleStreamOptions = { signal: controller.signal, apiKey: request.api_key,
        reasoning: request.thinking === "off" ? undefined : request.thinking,
        maxTokens: Math.min(model.maxTokens, 8192), maxRetries: 0, timeoutMs: 180_000,
        transport: "sse", cacheRetention: "none", fetch: endpointFetch(model, dependencies.fetch, () => { responseLimit = true; }, (value) => { status = value; }),
        onResponse: (response) => { status = response.status; },
        onPayload: (payload: any) => {
          if (Array.isArray(payload?.tools) && payload.tools.length) throw new Error("TOOLS_DISABLED");
        } };
      let stream;
      if (request.cli) {
        const entry = request.cli;
        const configured = entry.models.find((item) => item.id === model.id);
        if (!configured || !cliModelThinking(configured, entry.api).levels.includes(request.thinking)) throw new Error("INVALID_MODEL");
        stream = createCliStream(entry, dependencies.cliExecutor ?? executeCli, dependencies.verifyCli ?? (async (token) => {
          if (await cliLaunchSha256Async(entry, token) !== entry.launch_sha256) throw new Error("INVALID_MODEL");
        }))(model, context, options);
      } else {
        if (!request.api_key) throw new Error("MISSING_KEY");
        if (model.api === "openai-completions") stream = completions(model as Model<"openai-completions">, context, options);
        else if (model.api === "openai-responses") stream = responses(model as Model<"openai-responses">, context, options);
        else if (model.api === "anthropic-messages") stream = anthropic(model as Model<"anthropic-messages">, context, options);
        else throw new Error("INVALID_MODEL");
      }
      for await (const event of stream) {
        if (controller.signal.aborted) throw new Error(signal.aborted ? "CANCELLED" : "TIMEOUT");
        if (event.type.startsWith("toolcall")) { controller.abort(); throw new Error("TOOLS_DISABLED"); }
        if (event.type === "text_delta") {
          bytes += Buffer.byteLength(event.delta);
          if (bytes > MAX_OUTPUT) { controller.abort(); throw new Error("OUTPUT_LIMIT"); }
          text += event.delta;
          // Bound each IPC event as well as the total response.
          const characters = Array.from(event.delta);
          for (let offset = 0; offset < characters.length; offset += 2048) emit({ type: "delta", text: characters.slice(offset, offset + 2048).join("") });
        } else if (event.type === "error") {
          throw new Error(status === 401 || status === 403 ? "AUTH_FAILED" : "MODEL_ERROR");
        } else if (event.type === "done") {
          if (event.message.content.some((part) => part.type === "toolCall")) throw new Error("TOOLS_DISABLED");
          if (!text.trim()) throw new Error("EMPTY_RESPONSE");
          emit({ type: "done", text, limited: event.reason === "length" });
          finished = true;
        }
      }
      if (!finished) throw new Error("MODEL_ERROR");
    } finally {
      clearTimeout(timeout); signal.removeEventListener("abort", abort); controller.abort();
    }
  } catch (error) {
    const known = new Set(["CANCELLED", "TIMEOUT", "TOOLS_DISABLED", "OUTPUT_LIMIT", "INVALID_MODEL", "INVALID_INPUT", "MISSING_KEY", "AUTH_FAILED", "EMPTY_RESPONSE"]);
    const code = signal.aborted ? "CANCELLED" : responseLimit ? "OUTPUT_LIMIT" : error instanceof Error && known.has(error.message) ? error.message : "MODEL_ERROR";
    emit({ type: "error", code });
  }
}

async function main() {
  const controller = new AbortController();
  const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
  let started = false;
  input.on("close", () => controller.abort());
  input.on("line", (line) => {
    if (started) { controller.abort(); return; }
    started = true;
    if (Buffer.byteLength(line) > 512 * 1024) { process.exitCode = 2; input.close(); return; }
    let request: ChatInput;
    try { request = JSON.parse(line); } catch { process.exitCode = 2; input.close(); return; }
    void runChatTurn(request, (event) => process.stdout.write(JSON.stringify(event) + "\n"), controller.signal)
      .finally(() => { input.close(); process.stdin.destroy(); });
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) void main();
