import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { assertSafePublicQueryForTests } from "../extensions/subagent-readonly.ts";
import {
  cleanupExpiredSubagentContracts,
  createGovernedSubagentContract,
  loadGovernedSubagentContract,
  recordGovernedSearchUrls,
  recordGovernedSources,
  updateGovernedSubagentContract,
  writeGovernedSubagentResult,
} from "../extensions/subagent-contracts.ts";
import {
  buildGovernedSubagentLaunchForTests,
  validateGovernedSubagentResultForTests,
} from "../extensions/vertical-workflow.ts";

test("governed subagent contract freezes identity and produces a hashed local receipt", () => {
  const root = mkdtempSync(join(tmpdir(), "agent4market-subagent-"));
  try {
    const contract = createGovernedSubagentContract(root, {
      task_id: "task-research",
      profile_id: "sales-director",
      node_id: "public-research",
      task_version: 3,
      role: "research-scout",
      objective: "核验公开来源",
      allowed_tools: ["web.search", "web.open"],
      authorized_urls: ["https://example.com/user-source"],
    });
    recordGovernedSearchUrls(root, contract.contract_id, ["https://example.com/search-result"]);
    recordGovernedSources(root, contract.contract_id, [{
      source_id: "web-source-1",
      title: "Example source",
      source_type: "web",
      url: "https://example.com/search-result",
      content_sha256: "b".repeat(64),
      accessed_at: "2026-08-19T00:00:00.000Z",
      extraction_reliability: "standard",
    }]);
    const loaded = loadGovernedSubagentContract(root, contract.contract_id);
    assert.equal(loaded.revision, 2);
    assert.equal(loaded.sources.length, 1);
    assert.throws(
      () => updateGovernedSubagentContract(root, contract.contract_id, (value) => ({ ...value, task_id: "other-task" })),
      /immutable fields/,
    );
    const receipt = writeGovernedSubagentResult(root, loaded, {
      agent: "director-research-scout",
      model: "agent4market-newapi/model-a",
      run_id: "run-1",
      output: "已核验 Example source。",
    });
    const stored = JSON.parse(readFileSync(join(root, receipt.path), "utf8")) as { receipt_sha256: string };
    assert.equal(stored.receipt_sha256, receipt.result.receipt_sha256);
    assert.match(stored.receipt_sha256, /^[a-f0-9]{64}$/u);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("governed launch binds role, context, tools and rejects forged result tools", () => {
  const root = mkdtempSync(join(tmpdir(), "agent4market-subagent-result-"));
  try {
    const contract = createGovernedSubagentContract(root, {
      task_id: "task-a",
      profile_id: "sales-director",
      node_id: "public-research",
      task_version: 2,
      role: "research-scout",
      objective: "查找并打开公开来源",
      allowed_tools: ["web.search", "web.open"],
      authorized_urls: [],
    });
    recordGovernedSources(root, contract.contract_id, [{
      source_id: "source-a",
      title: "Source A",
      source_type: "web",
      url: "https://example.com/a",
      content_sha256: "c".repeat(64),
      accessed_at: "2026-08-19T00:00:00.000Z",
    }]);
    const loaded = loadGovernedSubagentContract(root, contract.contract_id);
    const launch = buildGovernedSubagentLaunchForTests({
      taskId: "task-a",
      profileId: "sales-director",
      request: "研究脑机接口公开市场资料",
      node: {
        id: "public-research",
        type: "subagent",
        depends_on: [],
        permissions: ["web.read"],
        boundary: {
          objective: "查找并打开公开来源",
          allowed_tools: ["web.search", "web.open"],
          max_turns: 8,
          write_scope: [],
        },
      },
      contractId: contract.contract_id,
    });
    assert.equal(launch.agent, "director-research-scout");
    assert.equal(launch.context, "fresh");
    assert.ok(launch.task.includes(contract.contract_id));
    const details = {
      mode: "single",
      runId: "run-a",
      results: [{
        agent: launch.agent,
        context: launch.context,
        exitCode: 0,
        finalOutput: "来源 A 已核验。",
        model: "provider/model",
        toolCalls: [
          { name: "director_child_web_search" },
          { name: "director_child_web_open" },
        ],
      }],
    };
    assert.equal(validateGovernedSubagentResultForTests(details, {
      agent: launch.agent,
      context: launch.context,
      role: "research-scout",
      allowed_tool_names: launch.allowedToolNames,
    }, loaded).output, "来源 A 已核验。");
    details.results[0]!.toolCalls.push({ name: "write" });
    assert.throws(
      () => validateGovernedSubagentResultForTests(details, {
        agent: launch.agent,
        context: launch.context,
        role: "research-scout",
        allowed_tool_names: launch.allowedToolNames,
      }, loaded),
      /outside the frozen allowlist/,
    );

    (details.results[0] as unknown as { toolCalls: Array<Record<string, string>> }).toolCalls = [
      { text: "bash {}", expandedText: "bash {}" },
    ];
    assert.throws(
      () => validateGovernedSubagentResultForTests(details, {
        agent: launch.agent,
        context: launch.context,
        role: "research-scout",
        allowed_tool_names: launch.allowedToolNames,
      }, loaded),
      /outside the frozen allowlist/,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("public query guard rejects credentials and stale contracts are cleaned safely", () => {
  assert.equal(assertSafePublicQueryForTests("脑机接口 产业政策 2026"), "脑机接口 产业政策 2026");
  assert.throws(() => assertSafePublicQueryForTests("customer test@example.com plan"), /敏感信息/);
  assert.throws(() => assertSafePublicQueryForTests("api_key=secret-value"), /敏感信息/);

  const root = mkdtempSync(join(tmpdir(), "agent4market-subagent-expiry-"));
  try {
    const contract = createGovernedSubagentContract(root, {
      task_id: "task-expiry",
      profile_id: "sales-director",
      node_id: "review",
      task_version: 1,
      role: "readonly-reviewer",
      objective: "复核方案",
      allowed_tools: [],
      authorized_urls: [],
    }, 60_000);
    assert.equal(cleanupExpiredSubagentContracts(root, Date.parse(contract.expires_at) + 1), 1);
    assert.throws(() => loadGovernedSubagentContract(root, contract.contract_id));
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
