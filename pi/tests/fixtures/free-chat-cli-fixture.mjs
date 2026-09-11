// Synthetic CLI only: no network and no real credentials, config or business files.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const task = JSON.parse(input.split("\nTASK_JSON:\n").at(-1));
const claude = process.argv.includes("--print");
assert.deepEqual(task.tools, []);
assert.ok(process.env.USERPROFILE.includes("Agent4MarketChatTests-"));
assert.equal(process.env.USERPROFILE, process.env.HOME);
assert.equal(process.env.USERPROFILE, process.env.APPDATA);
assert.ok(process.cwd().includes("Agent4MarketFreeChat"));
for (const name of ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AGENT4MARKET_TEST_PRIVATE_KEY", "NODE_OPTIONS", "CODEX_HOME", "CLAUDE_CONFIG_DIR"]) assert.equal(process.env[name], undefined);
assert.ok(!input.includes("SYNTHETIC_BUSINESS_DATA_CANARY"));
const schema = claude ? JSON.parse(process.argv[process.argv.indexOf("--json-schema") + 1])
  : JSON.parse(readFileSync(process.argv[process.argv.indexOf("--output-schema") + 1], "utf8"));
assert.equal(schema.properties.tool_calls.maxItems, 0);
if (claude) { assert.equal(process.argv[process.argv.indexOf("--tools") + 1], ""); assert.ok(process.argv.includes("--no-session-persistence")); }
else { assert.ok(process.argv.includes("--ignore-user-config")); assert.ok(process.argv.includes("--ephemeral")); assert.ok(process.argv.includes("shell_tool")); }
if (input.includes("fixture:cancel")) setInterval(() => {}, 1000);
else {
  const result = { text: JSON.stringify({ messages: task.messages.length, remembers: input.includes("小松"), pid: process.pid, cwd: process.cwd() }),
    tool_calls: input.includes("fixture:tool") ? [{ name: "forbidden_tool", arguments_json: "{}" }] : [] };
  if (claude) process.stdout.write(JSON.stringify({ type: "result", subtype: "success", is_error: false, structured_output: result }));
  else {
    const diagnostics = input.includes("fixture:reconnect") ? [
      { type: "error", message: "Reconnecting... 2/5 (request timed out)" },
      { type: "item.completed", item: { type: "error", message: "PRIVATE_DIAGNOSTIC_CANARY" } },
    ] : [];
    process.stdout.write([...diagnostics, { type: "item.completed", item: { type: "agent_message", text: JSON.stringify(result) } },
      { type: "turn.completed" }].map((event) => JSON.stringify(event)).join("\n"));
  }
}
