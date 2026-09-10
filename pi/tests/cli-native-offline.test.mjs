import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { rm } from "node:fs/promises";
import http from "node:http";
import { homedir } from "node:os";
import { basename, isAbsolute, join, resolve } from "node:path";
import { test } from "node:test";
import { cliArguments, cliEnvironment, cliModelCatalog, cliOutputSchema, parseCliResult } from "../extensions/cli-model-provider.ts";

// Opt-in acceptance against explicitly selected installed CLIs. All home/auth
// directories are synthetic and both services are local fixtures, never a cloud
// model or the user's CLI login. With no paths configured these tests skip.
const cases = [
  ["claude-code", "claude-synthetic-fixture", process.env.AGENT4MARKET_TEST_CLAUDE_PATH],
  ["codex-cli", "gpt-5.5", process.env.AGENT4MARKET_TEST_CODEX_PATH],
  ["codex-cli", "synthetic-unknown-model", process.env.AGENT4MARKET_TEST_CODEX_PATH],
];

for (const [api, modelId, command] of cases) test(`${api} ${modelId}: native CLI sends no executable tools, no implicit context, and parses one structured response`, {
  skip: !command && "Provide an explicit AGENT4MARKET_TEST_CLAUDE_PATH / AGENT4MARKET_TEST_CODEX_PATH for offline native acceptance",
  timeout: 35000,
}, async () => {
  assert.ok(isAbsolute(command));
  const root = mkdtempSync(join(homedir(), ".a4m-native-offline-"));
  const home = join(root, "synthetic-home"); const cwd = join(root, "empty-workspace");
  mkdirSync(home); mkdirSync(cwd);
  const canary = "IMPLICIT_CONTEXT_MUST_NOT_LOAD_54e91bb7";
  writeFileSync(join(cwd, "AGENTS.md"), canary); writeFileSync(join(cwd, "CLAUDE.md"), canary);
  const schema = cliOutputSchema([]); const schemaPath = join(root, "schema.json"); const catalogPath = join(root, "models.json");
  const reasoning = api === "codex-cli" && modelId === "gpt-5.5" ? { supported_efforts: ["low", "medium", "high", "xhigh"], default_effort: "high" } : undefined;
  const level = reasoning ? "high" : "off";
  writeFileSync(schemaPath, JSON.stringify(schema)); writeFileSync(catalogPath, JSON.stringify(cliModelCatalog(modelId, 32000, reasoning)));
  const env = cliEnvironment();
  for (const key of ["HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "CLAUDE_CONFIG_DIR", "CODEX_HOME"]) env[key] = home;
  const answer = { text: "原生 CLI 离线协议成功", tool_calls: [] }; const requests = [];
  let getRequests = 0; let child; let timer;
  const emit = (res, type, value) => res.write(`event: ${type}\ndata: ${JSON.stringify({ type, ...value })}\n\n`);
  const server = http.createServer(async (req, res) => {
    if (req.method !== "POST") { getRequests++; res.writeHead(404).end("{}"); return; }
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    const raw = Buffer.concat(chunks).toString("utf8"); const body = JSON.parse(raw);
    requests.push({ model: body.model, tools: (body.tools ?? []).map(tool => tool.name ?? tool.type), canary: raw.includes(canary),
      ...(reasoning ? { effort: body.reasoning?.effort } : {}),
      syntheticAuth: api === "claude-code" ? req.headers["x-api-key"] === "synthetic-only-key" : !req.headers.authorization,
      isolatedInstructions: api === "codex-cli" ? body.instructions === cliModelCatalog(modelId).models[0].base_instructions : true });
    if (requests.length > 1) { res.writeHead(400).end("{}"); return; }
    res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache", connection: "close" });
    if (api === "claude-code") {
      emit(res, "message_start", { message: { id: "msg_synthetic", type: "message", role: "assistant", model: modelId, content: [], stop_reason: null, usage: { input_tokens: 12, output_tokens: 0 } } });
      emit(res, "content_block_start", { index: 0, content_block: { type: "tool_use", id: "toolu_synthetic", name: "StructuredOutput", input: {} } });
      emit(res, "content_block_delta", { index: 0, delta: { type: "input_json_delta", partial_json: JSON.stringify(answer) } });
      emit(res, "content_block_stop", { index: 0 });
      emit(res, "message_delta", { delta: { stop_reason: "tool_use", stop_sequence: null }, usage: { output_tokens: 8 } });
      emit(res, "message_stop", {});
    } else {
      const text = JSON.stringify(answer);
      const item = { type: "message", id: "msg_synthetic", role: "assistant", status: "completed", content: [{ type: "output_text", text, annotations: [] }] };
      const response = { id: "resp_synthetic", object: "response", created_at: 1, status: "in_progress", output: [], model: modelId };
      emit(res, "response.created", { response });
      emit(res, "response.output_item.added", { output_index: 0, item: { ...item, status: "in_progress", content: [] } });
      emit(res, "response.content_part.added", { item_id: item.id, output_index: 0, content_index: 0, part: { type: "output_text", text: "", annotations: [] } });
      emit(res, "response.output_text.delta", { item_id: item.id, output_index: 0, content_index: 0, delta: text });
      emit(res, "response.output_item.done", { output_index: 0, item });
      emit(res, "response.completed", { response: { ...response, status: "completed", output: [item], usage: { input_tokens: 12, output_tokens: 8, total_tokens: 20 } } });
    }
    res.end();
  });
  try {
    await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
    let args = cliArguments({ api, args: [], models: [{ id: modelId, ...(reasoning ? { cli_reasoning: reasoning, default_thinking_level: level } : {}) }] }, modelId, schema, level).map(arg => arg === "__A4M_CLI_OUTPUT_SCHEMA__" ? schemaPath
      : arg === 'model_catalog_json="__A4M_CLI_MODEL_CATALOG__"' ? `model_catalog_json=${JSON.stringify(catalogPath)}` : arg);
    if (api === "claude-code") {
      env.ANTHROPIC_API_KEY = "synthetic-only-key"; env.ANTHROPIC_BASE_URL = `http://127.0.0.1:${server.address().port}`;
    } else {
      // Test-only transport substitution. Production fixes OpenAI + ChatGPT auth.
      const replaced = ['model_provider="openai"', 'forced_login_method="chatgpt"'];
      args = args.filter((arg, index, all) => !replaced.includes(arg) && !(arg === "-c" && replaced.includes(all[index + 1])));
      args.splice(args.length - 1, 0, "-c", 'model_provider="a4m-offline"', "-c", 'model_providers.a4m-offline.name="Offline"',
        "-c", `model_providers.a4m-offline.base_url="http://127.0.0.1:${server.address().port}/v1"`, "-c", 'model_providers.a4m-offline.wire_api="responses"',
        "-c", "model_providers.a4m-offline.requires_openai_auth=false", "-c", "model_providers.a4m-offline.supports_websockets=false",
        "-c", "model_providers.a4m-offline.request_max_retries=0", "-c", "model_providers.a4m-offline.stream_max_retries=0");
    }
    child = spawn(command, args, { cwd, env, shell: false, windowsHide: true, stdio: ["pipe", "pipe", "pipe"] });
    child.stdin.end("Synthetic offline test. Return only the structured answer.");
    let stdout = ""; child.stderr.resume();
    child.stdout.on("data", chunk => { stdout += chunk.toString("utf8"); if (stdout.length > 2 * 1024 * 1024) child.kill(); });
    timer = setTimeout(() => child.kill(), 25000);
    const code = await new Promise((resolve, reject) => { child.on("close", resolve); child.on("error", reject); });
    assert.equal(code, 0, "native CLI must complete successfully"); assert.equal(requests.length, 1);
    assert.deepEqual(requests[0], { model: modelId, ...(reasoning ? { effort: level } : {}), tools: api === "claude-code" ? ["StructuredOutput"] : [], canary: false, syntheticAuth: true, isolatedInstructions: true });
    if (api === "codex-cli") assert.equal(getRequests, 0, "catalog must not fall back to remote model metadata");
    assert.deepEqual(parseCliResult(api, stdout, { messages: [], tools: [] }).calls, []);
    assert.equal(parseCliResult(api, stdout, { messages: [], tools: [] }).text, answer.text);
  } finally {
    clearTimeout(timer); if (child && child.exitCode === null) child.kill();
    server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
    assert.equal(resolve(root, ".."), resolve(homedir())); assert.ok(basename(root).startsWith(".a4m-native-offline-"));
    await rm(root, { recursive: true, force: true, maxRetries: 8, retryDelay: 250 });
  }
});
