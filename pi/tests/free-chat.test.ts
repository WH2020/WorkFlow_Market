import assert from "node:assert/strict";
import test from "node:test";
import { CHAT_SYSTEM, endpointFetch, runChatTurn, type ChatInput, type ChatEvent } from "../extensions/free-chat.ts";
import type { CliBackend } from "../extensions/cli-model-provider.ts";

function input(): ChatInput {
  return { model: { id: "synthetic-model", name: "Fixture", provider: "fixture", api: "openai-completions", baseUrl: "https://fixture.invalid/v1",
    reasoning: false, input: ["text"], contextWindow: 32000, maxTokens: 4096, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } },
    api_key: "synthetic-key", messages: [{ role: "user", content: "合成用户" }, { role: "assistant", content: "合成回答" }, { role: "user", content: "继续" }], thinking: "off" };
}

test("endpoint wrapper binds method/path/origin and forbids redirects", async () => {
  let calls = 0;
  const fetcher = endpointFetch(input().model, async (_request, options) => {
    calls++; assert.equal(options?.redirect, "error"); return new Response("{}");
  });
  for (const url of ["https://other.invalid/v1/chat/completions", "https://fixture.invalid/v1/responses", "https://fixture.invalid/v1/chat/completions?redirect=x"]) {
    await assert.rejects(fetcher(url, { method: "POST" }), /RECIPIENT_MISMATCH/u);
  }
  await assert.rejects(fetcher("https://fixture.invalid/v1/chat/completions"), /RECIPIENT_MISMATCH/u);
  await fetcher("https://fixture.invalid/v1/chat/completions", { method: "POST" });
  assert.equal(calls, 1);
});

test("raw oversized SSE or error body is stopped before SDK parsing can grow unbounded", async () => {
  for (const status of [200, 401]) {
    const events: ChatEvent[] = [];
    await runChatTurn(input(), (event) => events.push(event), new AbortController().signal, {
      fetch: async () => new Response("data: " + "x".repeat(2 * 1024 * 1024), { status, headers: { "Content-Type": status === 200 ? "text/event-stream" : "application/json" } }),
    });
    assert.deepEqual(events, [{ type: "error", code: "OUTPUT_LIMIT" }]);
  }
});

for (const api of ["claude-code", "codex-cli"] as const) {
  test(`${api}: existing adapter gets only this dialogue, with native and workbench tools disabled`, async () => {
    const request = input(), events: ChatEvent[] = [];
    const base = api === "claude-code" ? "https://api.anthropic.com" : "https://chatgpt.com/backend-api/codex";
    const entry: CliBackend = { id: "fixture", api, base_url: base, command: process.execPath, executable_path: process.execPath,
      args: [], version: "synthetic", runner_policy_version: 1, launch_sha256: "0".repeat(64), models: [{ id: "synthetic-model" }] };
    request.cli = entry; request.model = { ...request.model, api, baseUrl: base }; delete request.api_key;
    let calls = 0, launchInput = "", launchArgs: string[] = [];
    await runChatTurn(request, (event) => events.push(event), new AbortController().signal, {
      verifyCli: async () => {}, cliExecutor: async (launch) => {
        calls++;
        launchInput = launch.input; launchArgs = launch.args;
        const result = { text: "合成😀回复", tool_calls: [] };
        const stdout = api === "claude-code" ? JSON.stringify({ type: "result", subtype: "success", is_error: false, structured_output: result })
          : [{ type: "item.completed", item: { type: "agent_message", text: JSON.stringify(result) } }, { type: "turn.completed" }].map((part) => JSON.stringify(part)).join("\n");
        return { code: 0, stdout };
      },
    });
    const prompt = JSON.parse(launchInput.split("\n\nTASK_JSON:\n")[1]);
    assert.deepEqual(prompt.tools, []);
    assert.equal(prompt.system_prompt, CHAT_SYSTEM);
    assert.equal(prompt.messages.length, 3);
    assert.match(launchInput, /合成用户/u); assert.match(launchInput, /合成回答/u);
    if (api === "claude-code") { assert.equal(launchArgs[launchArgs.indexOf("--tools") + 1], ""); assert.ok(launchArgs.includes("--no-session-persistence")); }
    else { assert.ok(launchArgs.includes("--ignore-user-config")); assert.ok(launchArgs.includes("--ephemeral")); assert.ok(launchArgs.includes("shell_tool")); }
    assert.equal(calls, 1); assert.equal(events.at(-1)?.type, "done", JSON.stringify(events));
    assert.ok(events.some((event) => event.type === "delta" && event.text.includes("😀")));
  });
}

test("pre-cancelled requests and invalid role injection never reach fetch", async () => {
  let calls = 0;
  const request = input(), controller = new AbortController(), events: ChatEvent[] = [];
  request.messages[0].role = "system" as any;
  await runChatTurn(request, (event) => events.push(event), controller.signal, { fetch: async () => { calls++; throw new Error("should not run"); } });
  assert.equal(calls, 0); assert.equal(events.at(-1)?.type, "error");
  controller.abort(); events.length = 0;
  await runChatTurn(input(), (event) => events.push(event), controller.signal, { fetch: async () => { calls++; throw new Error("should not run"); } });
  assert.equal(calls, 0); assert.deepEqual(events, [{ type: "error", code: "CANCELLED" }]);
});
