import assert from "node:assert/strict";
import test from "node:test";
import { createWechatReviewModel } from "../extensions/wechat-review-model.ts";

function fixture(api: string) {
  const recipient = { provider_id: `fixture-${api}`, model_id: "synthetic", api,
    base_url: api === "claude-code" ? "https://api.anthropic.com" : api === "codex-cli" ? "https://chatgpt.com/backend-api/codex" : "https://models.example" };
  const model = { id: recipient.model_id, provider: recipient.provider_id, api, baseUrl: recipient.base_url, contextWindow: 32000, maxTokens: 4096 };
  const state = { sent: 0, marked: false, guards: 0, usage: 0, authUrl: "", rejectGuardAt: Infinity,
    wireModel: model, responseModel: model, payload: {} as any, stopReason: "stop", content: [{ type: "text", text: "synthetic answer" }] as any[], captured: undefined as any };
  const registry = {
    getApiKeyAndHeaders: async () => ({ ok: true, apiKey: "synthetic-only", ...(state.authUrl ? { baseUrl: state.authUrl } : {}) }),
    getProvider: () => ({ streamSimple: (_model: unknown, context: any, options: any) => ({ result: async () => {
      state.captured = { context, options };
      assert.ok(state.marked, "bind sensitive recipient before provider execution");
      await options.onPayload(state.payload, state.wireModel);
      options.signal.throwIfAborted(); state.sent++;
      await options.onResponse({ status: 200, headers: {} }, state.responseModel);
      return { stopReason: state.stopReason, content: state.content, usage: { totalTokens: 42 } };
    } }) }),
  };
  const review = createWechatReviewModel({ model: model as never, recipient, registry: registry as never, thinking: "high", currentContextTokens: 100,
    guard: () => { if (++state.guards >= state.rejectGuardAt) throw new Error("PRIVATE_SOURCE_CANARY"); },
    markSensitive: () => { state.marked = true; }, recordUsage: () => { state.usage++; } });
  return { review, state };
}

for (const api of ["openai-responses", "claude-code", "codex-cli"]) {
  test(`${api}: isolated batch has no tools or history, keeps thinking and records usage`, async () => {
    const f = fixture(api); let scopeChecks = 0;
    assert.equal(await f.review.complete("bounded system", "selected synthetic batch", 2048, undefined, () => { scopeChecks++; }), "synthetic answer");
    assert.equal(f.state.sent, 1); assert.equal(f.state.usage, 1); assert.ok(scopeChecks >= 4);
    assert.deepEqual(f.state.captured.context.tools, []);
    assert.equal(f.state.captured.context.messages.length, 1);
    assert.equal(f.state.captured.options.reasoning, "high");
    assert.equal(f.state.captured.options.maxRetries, 0);
    assert.equal(f.state.captured.options.maxTokens, 2048);
  });

  test(`${api}: auth redirect, late guard and payload model drift send zero batches`, async () => {
    for (const mode of ["auth", "guard", "payload", "scope", "tools"] as const) {
      const f = fixture(api);
      if (mode === "auth") f.state.authUrl = "https://unapproved.invalid";
      if (mode === "guard") f.state.rejectGuardAt = 2;
      if (mode === "payload") f.state.wireModel = { ...f.state.wireModel, id: "other" };
      if (mode === "tools") f.state.payload = { tools: [{ type: "web_search" }] };
      await assert.rejects(f.review.complete("system", "PRIVATE_SOURCE_CANARY", 2048, undefined,
        mode === "scope" ? () => { throw new Error("revoked"); } : undefined), (error: Error) => {
        assert.doesNotMatch(error.message, /PRIVATE_SOURCE_CANARY|unapproved/); return true;
      });
      assert.equal(f.state.sent, 0); assert.equal(f.state.usage, 0);
    }
  });

  test(`${api}: cancellation, truncated responses and native tool calls never return a summary`, async () => {
    const f = fixture(api); const controller = new AbortController(); controller.abort();
    await assert.rejects(f.review.complete("sys", "synthetic", 2048, controller.signal), /取消/);
    assert.equal(f.state.sent, 0);
    for (const kind of ["truncated", "tool", "response-guard", "response-model"]) {
      const g = fixture(api);
      if (kind === "truncated") g.state.stopReason = "length";
      if (kind === "tool") g.state.content = [{ type: "toolCall", name: "shell", arguments: {} }];
      if (kind === "response-guard") g.state.rejectGuardAt = 4;
      if (kind === "response-model") g.state.responseModel = { ...g.state.responseModel, id: "other" };
      await assert.rejects(g.review.complete("sys", "synthetic", 2048), /未生成完整报告/);
      assert.equal(g.state.usage, 0);
    }
  });
}
