import type { ModelRegistry } from "@earendil-works/pi-coding-agent";
import type { Api, Model, Usage } from "@earendil-works/pi-ai";
import { runtimeModelRecipient, sameRecipient, type ModelRecipient } from "./model-selection.ts";
import type { TaskThinkingLevel } from "./task-runtime.ts";
import type { WechatReviewModel } from "./wechat-batching.ts";
import { endpointFetch } from "./free-chat.ts";

/** Isolated calls need their own guards: they never traverse Pi conversation hooks. */
export function createWechatReviewModel(options: {
  model: Model<Api>; registry: Pick<ModelRegistry, "getApiKeyAndHeaders" | "getProvider">;
  recipient: ModelRecipient; thinking?: TaskThinkingLevel; currentContextTokens?: number;
  guard: () => void; markSensitive: () => void; recordUsage: (usage: Usage) => void;
}): WechatReviewModel {
  const { registry, recipient, guard } = options;
  const model = { ...options.model };
  return {
    contextWindow: model.contextWindow, maxTokens: model.maxTokens, currentContextTokens: options.currentContextTokens,
    complete: async (system, input, outputTokens, signal, beforeSend) => {
      const controller = new AbortController();
      const boundedSignal = AbortSignal.any([...(signal ? [signal] : []), controller.signal, AbortSignal.timeout(180_000)]);
      const check = () => { boundedSignal.throwIfAborted(); guard(); beforeSend?.(); };
      try {
        check();
        const auth = await registry.getApiKeyAndHeaders(model);
        check();
        if (!auth.ok) throw new Error("模型认证不可用");
        const actualModel = auth.baseUrl ? { ...model, baseUrl: auth.baseUrl } : model;
        if (!sameRecipient(runtimeModelRecipient(actualModel), recipient)) throw new Error("模型认证接收地址变化");
        const provider = registry.getProvider(model.provider);
        if (!provider) throw new Error("供应商不可用");
        const cli = model.api === "claude-code" || model.api === "codex-cli";
        if (!cli && !["openai-completions", "openai-responses", "anthropic-messages"].includes(model.api)) throw new Error("未验证的批次模型协议");
        options.markSensitive();
        const response = await provider.streamSimple(actualModel, {
          systemPrompt: system, messages: [{ role: "user", content: input, timestamp: Date.now() }], tools: [],
        }, {
          apiKey: auth.apiKey, headers: auth.headers, env: auth.env, signal: boundedSignal,
          maxTokens: outputTokens, timeoutMs: 180_000, maxRetries: 0,
          transport: "sse", cacheRetention: "none",
          ...(!cli ? { fetch: endpointFetch(actualModel) } : {}),
          reasoning: options.thinking === "off" ? undefined : options.thinking,
          onPayload: (payload, sendingModel) => {
            try {
              check();
              if (!sameRecipient(runtimeModelRecipient(sendingModel), recipient)) throw new Error("批次发送模型不一致");
              const tools = (payload as { tools?: unknown } | undefined)?.tools;
              if (tools !== undefined && (!Array.isArray(tools) || tools.length)) throw new Error("批次请求包含未授权工具");
            } catch (error) { controller.abort(); throw error; }
          },
          onResponse: (_response, receivingModel) => {
            try {
              check();
              if (!sameRecipient(runtimeModelRecipient(receivingModel), recipient)) throw new Error("批次响应模型不一致");
            } catch (error) { controller.abort(); throw error; }
          },
        }).result();
        check();
        if (response.stopReason !== "stop" || response.content.some((part) => part.type !== "text" && part.type !== "thinking")) {
          throw new Error("输出不完整或返回了未授权工具");
        }
        options.recordUsage(response.usage);
        return response.content.filter((part) => part.type === "text").map((part) => part.text).join("\n");
      } catch {
        controller.abort();
        // Never expose SDK errors containing source messages, URLs or credentials.
        if (signal?.aborted) throw new Error("微信分批整理已取消；未生成完整报告，重试会重新处理已发送批次");
        throw new Error("微信分批整理失败（模型边界、认证、超时或输出异常）；未生成完整报告，重试会重新处理已发送批次");
      }
    },
  };
}
