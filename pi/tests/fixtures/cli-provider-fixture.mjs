// Synthetic CLI only. No network, credentials, real files or business data.
let input = "";
for await (const chunk of process.stdin) input += chunk;
const task = JSON.parse(input.split("\nTASK_JSON:\n").at(-1));
const api = process.argv.includes("--print") ? "claude-code" : "codex-cli";
if (["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AGENT4MARKET_TEST_PRIVATE_KEY", "NODE_OPTIONS", "CODEX_HOME", "CLAUDE_CONFIG_DIR"].some((key) => process.env[key])) process.exit(93);
if (process.cwd().includes("WorkFlow_Market")) process.exit(94);
if (input.includes("fixture:auth-error")) { process.stderr.write("401 synthetic authentication failure PRIVATE_FIXTURE_DETAIL"); process.exit(1); }
if (input.includes("fixture:cancel")) { setInterval(() => {}, 1000); }
else {
  const done = input.includes("FIXTURE_TOOL_RESULT");
  const result = { text: done ? "合成 CLI 工具闭环完成" : "", tool_calls: done ? [] : [{ name: "lookup_fixture", arguments_json: '{"label":"离线 CLI"}' }] };
  if (api === "claude-code") process.stdout.write(JSON.stringify({ type: "result", subtype: "success", is_error: false, structured_output: result }));
  else process.stdout.write([{ type: "thread.started", thread_id: "synthetic-only" }, { type: "item.completed", item: { type: "agent_message", text: JSON.stringify(result) } }, { type: "turn.completed", usage: { input_tokens: 10, output_tokens: 6 } }].map((event) => JSON.stringify(event)).join("\n"));
}
