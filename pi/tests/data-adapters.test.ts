import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, utimesSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { deflateSync } from "node:zlib";
import {
  assertDeckMatchesWeeklySnapshot,
  collectWeeklySnapshot,
  holdDeckIntentLockForTests,
  parseCsv,
  registerDataAdapters,
  serializeCsv,
} from "../extensions/data-adapters.ts";
import { setSourceRequestForTests } from "../extensions/source-readers.ts";
import { payloadSha256 } from "../extensions/task-runtime.ts";

type RegisteredTool = {
  execute: (toolCallId: string, params: Record<string, unknown>) => Promise<unknown>;
};

function fixture(fixedIntent = false, taskId?: string, profileId = "market-director") {
  const root = mkdtempSync(join(tmpdir(), "director-adapters-"));
  mkdirSync(join(root, "data", "knowledge"), { recursive: true });
  mkdirSync(join(root, "data", "sales"), { recursive: true });
  writeFileSync(
    join(root, "data", "knowledge", "source-register.csv"),
    [
      "source_id,title,url,publisher,published_date,accessed_date,region,topic,source_type,quality,exposure_status,status,notes",
      'src-1,"脑机, 综述",https://example.test,机构,2026-08-01,2026-08-18,中国,脑机,report,A,internal,verified,"第一行\n第二行"',
      "",
    ].join("\r\n"),
    "utf8",
  );
  writeFileSync(
    join(root, "data", "sales", "customers.csv"),
    "customer_id,customer_name,region,sector,owner,stage,health,key_contact,decision_maker,budget_path,next_action,next_action_due,last_evidence_date,risks,updated_at\r\nc-1,客户甲,上海,制造,销售甲,验证,green,,,,跟进,2026-08-20,2026-08-18,,2026-08-18T00:00:00.000Z\r\n",
    "utf8",
  );
  writeFileSync(
    join(root, "data", "sales", "sales-assets.csv"),
    "asset_id,asset_type,title,scope,customer_id,audience_role,sales_stage,use_case,owner,status,authorization_status,deidentification_status,version,source_path,evidence_refs,last_validated_at,next_review_at,usage_feedback,updated_at\r\n",
    "utf8",
  );
  const tools = new Map<string, RegisteredTool>();
  const before: string[] = [];
  const after: Array<{ tool: string; details: unknown }> = [];
  const errors: Array<{ tool: string; outcome: string }> = [];
  let intent = 0;
  const pi = {
    registerTool(tool: RegisteredTool & { name: string }) {
      tools.set(tool.name, tool);
    },
  };
  registerDataAdapters(pi as never, {
    projectRoot: () => root,
    beforeLogicalTool: (tool, params) => {
      before.push(tool);
      if (tool.endsWith(".write")) {
        if (!fixedIntent || intent === 0) intent += 1;
        return { intent_id: `intent-${intent}`, payload_sha256: payloadSha256(params), task_id: taskId, profile_id: profileId };
      }
      if (taskId) return { task_id: taskId, profile_id: profileId };
    },
    afterLogicalTool: (tool, _params, details) => after.push({ tool, details }),
    onLogicalToolError: (tool, _params, outcome) => errors.push({ tool, outcome }),
  });
  return {
    root,
    tools,
    before,
    after,
    errors,
    cleanup: () => rmSync(root, { recursive: true, force: true }),
  };
}

test("CSV parser and serializer preserve quotes and multiline fields", () => {
  const parsed = parseCsv('id,notes\r\n1,"a, b\n""quoted"""\r\n');
  assert.deepEqual(parsed, [
    ["id", "notes"],
    ["1", 'a, b\n"quoted"'],
  ]);
  const serialized = serializeCsv({ headers: parsed[0], rows: [{ id: "1", notes: parsed[1][1] }] });
  assert.deepEqual(parseCsv(serialized), parsed);
});

test("knowledge adapter searches and rejects a stale update", async () => {
  const state = fixture();
  try {
    const search = state.tools.get("director_knowledge_search")!;
    const found = (await search.execute("search", { queries: ["脑机"], limit: 5 })) as {
      details: { searches: Array<{ rows: Array<Record<string, string>> }> };
    };
    assert.equal(found.details.searches[0].rows.length, 1);
    const version = found.details.searches[0].rows[0]._record_version;

    const write = state.tools.get("director_knowledge_write")!;
    await write.execute("write", {
      mutations: [{ operation: "update", record_id: "src-1", changes: { notes: "已复核" }, expected_version: version }],
    });
    await assert.rejects(
      () =>
        write.execute("stale", {
          mutations: [{ operation: "update", record_id: "src-1", changes: { notes: "覆盖" }, expected_version: version }],
        }),
      /记录已变化，拒绝覆盖/,
    );
    assert.match(readFileSync(join(state.root, "data", "knowledge", "source-register.csv"), "utf8"), /已复核/);
    assert.equal(state.after.filter((entry) => entry.tool === "knowledge.write").length, 1);
  } finally {
    state.cleanup();
  }
});

test("sales adapter reads several tables in one node and performs versioned writes", async () => {
  const state = fixture();
  try {
    const read = state.tools.get("director_sales_read")!;
    const snapshot = (await read.execute("read", { tables: ["customers"], query: "客户甲" })) as {
      details: { tables: Array<{ rows: Array<Record<string, string>> }> };
    };
    const version = snapshot.details.tables[0].rows[0]._record_version;
    const write = state.tools.get("director_sales_write")!;
    await write.execute("update", {
      table: "customers",
      mutations: [{ operation: "update", record_id: "c-1", changes: { next_action: "安排演示" }, expected_version: version }],
    });
    const source = readFileSync(join(state.root, "data", "sales", "customers.csv"), "utf8");
    assert.match(source, /安排演示/);
    assert.doesNotMatch(source, /\.tmp/);
    assert.equal(state.before.at(-1), "sales.write");
    assert.equal(state.after.at(-1)?.tool, "sales.write");
  } finally {
    state.cleanup();
  }
});

test("unknown columns and a held lock never modify sales data", async () => {
  const state = fixture();
  try {
    const path = join(state.root, "data", "sales", "customers.csv");
    const original = readFileSync(path, "utf8");
    const write = state.tools.get("director_sales_write")!;
    await assert.rejects(
      () =>
        write.execute("unknown", {
          table: "customers",
          mutations: [{ operation: "insert", record_id: "c-2", changes: { customer_name: "客户乙", hidden: "x" } }],
        }),
      /未知字段/,
    );
    assert.equal(readFileSync(path, "utf8"), original);

    writeFileSync(`${path}.lock`, "held", "utf8");
    await assert.rejects(
      () =>
        write.execute("locked", {
          table: "customers",
          mutations: [{ operation: "insert", record_id: "c-2", changes: { customer_name: "客户乙" } }],
        }),
      /另一个任务更新/,
    );
    assert.equal(readFileSync(path, "utf8"), original);
  } finally {
    state.cleanup();
  }
});

test("schema drift and duplicate stable IDs are rejected before any write", async () => {
  const state = fixture();
  try {
    const knowledgePath = join(state.root, "data", "knowledge", "source-register.csv");
    const knowledge = readFileSync(knowledgePath, "utf8").replace(
      "source_id,title,",
      "source_id,title,unexpected,",
    );
    writeFileSync(knowledgePath, knowledge, "utf8");
    await assert.rejects(
      () => state.tools.get("director_knowledge_search")!.execute("schema", { queries: ["脑机"] }),
      /表头.*契约不一致/,
    );

    const salesPath = join(state.root, "data", "sales", "customers.csv");
    const sales = readFileSync(salesPath, "utf8");
    writeFileSync(salesPath, `${sales}${sales.split(/\r?\n/u)[1]}\r\n`, "utf8");
    const before = readFileSync(salesPath, "utf8");
    await assert.rejects(
      () =>
        state.tools.get("director_sales_write")!.execute("duplicate", {
          table: "customers",
          mutations: [{ operation: "update", record_id: "c-1", changes: { next_action: "不应写入" }, expected_version: "irrelevant" }],
        }),
      /重复稳定主键/,
    );
    assert.equal(readFileSync(salesPath, "utf8"), before);
  } finally {
    state.cleanup();
  }
});

test("web adapter requires explicit configuration and returns only safe result fields", async () => {
  const state = fixture();
  const originalKey = process.env.BRAVE_SEARCH_API_KEY;
  const originalFetch = globalThis.fetch;
  try {
    const search = state.tools.get("director_web_search")!;
    delete process.env.BRAVE_SEARCH_API_KEY;
    await assert.rejects(() => search.execute("missing", { queries: ["具身智能"] }), /尚未配置/);
    process.env.BRAVE_SEARCH_API_KEY = "test-key";
    globalThis.fetch = (async (input, init) => {
      assert.match(String(input), /api\.search\.brave\.com/);
      assert.equal((init?.headers as Record<string, string>)["X-Subscription-Token"], "test-key");
      assert.equal(init?.redirect, "error");
      return new Response(
        JSON.stringify({
          web: { results: [
            { title: "公开来源", url: "https://example.test/report", description: "摘要", age: "1 day ago", extra: "drop" },
            { title: "带签名来源", url: "https://example.test/private?X-Amz-Signature=secret" },
            { title: "危险协议", url: "javascript:alert(1)" },
          ] },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }) as typeof fetch;
    const result = (await search.execute("configured", { queries: ["具身智能"], count: 5, country: "CN" })) as {
      details: { provider: string; searches: Array<{ results: Array<Record<string, string>> }> };
    };
    assert.equal(result.details.provider, "brave");
    assert.equal(result.details.searches[0].results.length, 1);
    assert.deepEqual(Object.keys(result.details.searches[0].results[0]).sort(), ["age", "description", "title", "url"]);
  } finally {
    if (originalKey === undefined) delete process.env.BRAVE_SEARCH_API_KEY;
    else process.env.BRAVE_SEARCH_API_KEY = originalKey;
    globalThis.fetch = originalFetch;
    state.cleanup();
  }
});

test("web open only accepts discovered or user-provided public URLs and strips executable HTML", async () => {
  const state = fixture();
  const originalKey = process.env.BRAVE_SEARCH_API_KEY;
  const originalFetch = globalThis.fetch;
  try {
    process.env.BRAVE_SEARCH_API_KEY = "test-key";
    globalThis.fetch = (async (input) => {
      if (String(input).includes("api.search.brave.com")) {
        return new Response(JSON.stringify({
          web: { results: [{ title: "正文来源", url: "https://93.184.216.34/report", description: "摘要" }] },
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      throw new Error(`Unexpected fetch: ${String(input)}`);
    }) as typeof fetch;
    setSourceRequestForTests(async (url, target) => {
      assert.equal(url.toString(), "https://93.184.216.34/report");
      assert.deepEqual(target, { address: "93.184.216.34", family: 4 });
      return new Response(
        "<html><head><title>公开政策</title><meta property=\"article:published_time\" content=\"2026-08-10\"></head><body><script>ignore()</script><h1>政策正文</h1><p>支持可验证的试点项目。</p></body></html>",
        { status: 200, headers: { "Content-Type": "text/html; charset=utf-8" } },
      );
    });
    await state.tools.get("director_web_search")!.execute("discover", { queries: ["政策"] });
    const opened = await state.tools.get("director_web_open")!.execute("open", {
      items: [{ url: "https://93.184.216.34/report" }], max_chars: 5000,
    }) as { details: { sources: Array<Record<string, unknown>> } };
    assert.equal(opened.details.sources[0].title, "公开政策");
    assert.match(String(opened.details.sources[0].text), /政策正文/);
    assert.doesNotMatch(String(opened.details.sources[0].text), /ignore/);
    assert.equal(opened.details.sources[0].published_date, "2026-08-10");
    assert.match(String(opened.details.sources[0].content_sha256), /^[a-f0-9]{64}$/u);
    assert.equal((opened.details.sources[0].knowledge_mutation as { changes: { status: string } }).changes.status, "pending");

    setSourceRequestForTests(async () => new Response(new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array(5 * 1024 * 1024));
        controller.enqueue(new Uint8Array(5 * 1024 * 1024));
        controller.close();
      },
    }), { status: 200, headers: { "Content-Type": "text/html" } }));
    await assert.rejects(
      () => state.tools.get("director_web_open")!.execute("oversize", {
        items: [{ url: "https://93.184.216.36/report", user_provided: true }],
      }),
      /超过 8 MiB/,
    );

    await assert.rejects(
      () => state.tools.get("director_web_open")!.execute("private", {
        items: [{ url: "http://127.0.0.1/admin", user_provided: true }],
      }),
      /私网|回环/,
    );
    await assert.rejects(
      () => state.tools.get("director_web_open")!.execute("unapproved", {
        items: [{ url: "https://93.184.216.35/report" }],
      }),
      /不是本次会话的公开检索结果/,
    );
  } finally {
    setSourceRequestForTests(undefined);
    if (originalKey === undefined) delete process.env.BRAVE_SEARCH_API_KEY;
    else process.env.BRAVE_SEARCH_API_KEY = originalKey;
    globalThis.fetch = originalFetch;
    state.cleanup();
  }
});

test("local PDF reader is confined to approved input folders and returns page evidence", async () => {
  const state = fixture();
  try {
    mkdirSync(join(state.root, "inputs"), { recursive: true });
    const stream = "BT /F1 12 Tf 72 720 Td (PDF evidence page one) Tj ET";
    const secondStream = "BT /F1 12 Tf 72 720 Td (PDF evidence page two) Tj ET";
    const pdf = [
      "%PDF-1.4",
      "1 0 obj",
      "<< /Type /Page /Contents 2 0 R >>",
      "endobj",
      "2 0 obj",
      `<< /Length ${stream.length} >>`,
      "stream",
      stream,
      "endstream",
      "endobj",
      "3 0 obj",
      "<< /Type /Page /Contents 4 0 R >>",
      "endobj",
      "4 0 obj",
      `<< /Length ${secondStream.length} >>`,
      "stream",
      secondStream,
      "endstream",
      "endobj",
      "%%EOF",
    ].join("\n");
    writeFileSync(join(state.root, "inputs", "evidence.pdf"), pdf, "latin1");
    const result = await state.tools.get("director_pdf_read")!.execute("pdf", {
      path: "inputs/evidence.pdf", max_chars: 5000, max_pages: 1,
    }) as { details: { source_id: string; text: string; evidence_refs: string[]; truncated: boolean; total_pages: number; extracted_pages: number; knowledge_mutation: { changes: { notes: string } } } };
    assert.match(result.details.source_id, /^pdf-/u);
    assert.match(result.details.text, /PDF evidence page one/);
    assert.deepEqual(result.details.evidence_refs, ["inputs/evidence.pdf#page=1"]);
    assert.equal(result.details.total_pages, 2);
    assert.equal(result.details.extracted_pages, 1);
    assert.equal(result.details.truncated, true);
    assert.match(result.details.knowledge_mutation.changes.notes, /#page=1/);

    const compressedBomb = deflateSync(Buffer.alloc(8 * 1024 * 1024 + 1, 0x41));
    const bombPdf = Buffer.concat([
      Buffer.from("%PDF-1.4\n1 0 obj\n<< /Type /Page /Contents 2 0 R >>\nendobj\n2 0 obj\n", "latin1"),
      Buffer.from(`<< /Length ${compressedBomb.length} /Filter /FlateDecode >>\nstream\n`, "latin1"),
      compressedBomb,
      Buffer.from("\nendstream\nendobj\n%%EOF", "latin1"),
    ]);
    writeFileSync(join(state.root, "inputs", "compressed-bomb.pdf"), bombPdf);
    await assert.rejects(
      () => state.tools.get("director_pdf_read")!.execute("bomb", { path: "inputs/compressed-bomb.pdf" }),
      /未包含可可靠读取的文本层/,
    );

    writeFileSync(join(state.root, "outside.pdf"), pdf, "latin1");
    await assert.rejects(
      () => state.tools.get("director_pdf_read")!.execute("outside", { path: "outside.pdf" }),
      /只能放在 inputs|data\/inbox/,
    );
  } finally {
    state.cleanup();
  }
});

test("task evidence survives adapter restart and freezes the exact knowledge mutation", async () => {
  const state = fixture(false, "task-evidence-restart");
  const originalFetch = globalThis.fetch;
  const originalKey = process.env.BRAVE_SEARCH_API_KEY;
  process.env.BRAVE_SEARCH_API_KEY = "test-key";
  const registerRestartedTools = () => {
    const tools = new Map<string, RegisteredTool>();
    registerDataAdapters({ registerTool(tool: RegisteredTool & { name: string }) { tools.set(tool.name, tool); } } as never, {
      projectRoot: () => state.root,
      beforeLogicalTool: (tool, params) => tool.endsWith(".write")
        ? { intent_id: "intent-restart", payload_sha256: payloadSha256(params), task_id: "task-evidence-restart", profile_id: "market-director" }
        : { task_id: "task-evidence-restart", profile_id: "market-director" },
      afterLogicalTool: () => {},
      onLogicalToolError: () => {},
    });
    return tools;
  };
  try {
    globalThis.fetch = (async () => new Response(JSON.stringify({
      web: { results: [{ title: "可恢复证据", url: "https://93.184.216.34/restart", description: "证据摘要" }] },
    }), { status: 200, headers: { "Content-Type": "application/json" } })) as typeof fetch;
    setSourceRequestForTests(async () => new Response(
      "<html><head><title>可恢复证据</title></head><body><p>跨进程重启后仍需保持证据链。</p></body></html>",
      { status: 200, headers: { "Content-Type": "text/html; charset=utf-8" } },
    ));
    await state.tools.get("director_web_search")!.execute("search", { queries: ["证据"] });

    const afterSearchRestart = registerRestartedTools();
    const opened = await afterSearchRestart.get("director_web_open")!.execute("open", {
      items: [{ url: "https://93.184.216.34/restart" }],
    }) as { details: { sources: Array<{ knowledge_mutation: Record<string, unknown>; source_id: string }> } };
    const mutation = opened.details.sources[0].knowledge_mutation;

    const beforeWriteRestart = registerRestartedTools();
    await beforeWriteRestart.get("director_knowledge_write")!.execute("write", { mutations: [mutation] });
    const stored = readFileSync(join(state.root, "data", "knowledge", "source-register.csv"), "utf8");
    assert.match(stored, new RegExp(opened.details.sources[0].source_id, "u"));
    assert.match(stored, /跨进程重启/u);
  } finally {
    setSourceRequestForTests(undefined);
    if (originalKey === undefined) delete process.env.BRAVE_SEARCH_API_KEY;
    else process.env.BRAVE_SEARCH_API_KEY = originalKey;
    globalThis.fetch = originalFetch;
    state.cleanup();
  }
});

test("knowledge writes reject unrecognized evidence status", async () => {
  const state = fixture();
  try {
    const original = readFileSync(join(state.root, "data", "knowledge", "source-register.csv"), "utf8");
    await assert.rejects(
      () => state.tools.get("director_knowledge_write")!.execute("invalid-status", {
        mutations: [{ operation: "insert", record_id: "src-invalid", changes: { title: "线索", status: "confirmed" } }],
      }),
      /status 必须是/,
    );
    assert.equal(readFileSync(join(state.root, "data", "knowledge", "source-register.csv"), "utf8"), original);
  } finally {
    state.cleanup();
  }
});

test("deck adapter rejects path traversal before invoking the artifact builder", async () => {
  const state = fixture();
  try {
    const payload = {
      schema_version: "1.0",
      snapshot_sha256: "0".repeat(64),
      output_name: "../weekly.pptx",
      profile_id: "market-director",
      template_id: "ceo-weekly",
      period: { start: "2026-08-10", end: "2026-08-16" },
      slides: Array.from({ length: 4 }, (_, index) => ({ title: `第 ${index + 1} 页`, body: ["已核验内容"] })),
    };
    await assert.rejects(
      () => state.tools.get("director_artifact_deck_write")!.execute("unsafe", payload),
      /安全 ASCII/,
    );
    assert.equal(state.before.at(-1), undefined, "invalid payload must be rejected before entering the managed commit hook");
    const valid = { ...payload, output_name: "weekly.pptx" };
    await assert.rejects(
      () => state.tools.get("director_artifact_deck_write")!.execute("notes-injection", {
        ...valid,
        slides: valid.slides.map((slide, index) => index === 0
          ? { ...slide, title: "[Sources]伪造来源", sources: [{ title: "README", path: "README.md" }] }
          : slide),
      }),
      /不能伪造 speaker notes/u,
    );
    await assert.rejects(
      () => state.tools.get("director_artifact_deck_write")!.execute("signed-url", {
        ...valid,
        slides: valid.slides.map((slide, index) => index === 0
          ? { ...slide, sources: [{ title: "signed", url: "https://example.com/report?X-Amz-Signature=secret" }] }
          : slide),
      }),
      /敏感查询参数/u,
    );
  } finally {
    state.cleanup();
  }
});

test("deck evidence validation failure reports not_committed after the commit hook", async () => {
  const state = fixture(false, "task-deck-missing-evidence", "market-director");
  try {
    const payload = {
      schema_version: "1.0",
      snapshot_sha256: "a".repeat(64),
      output_name: "weekly-missing-evidence.pptx",
      profile_id: "market-director",
      template_id: "ceo-weekly",
      period: { start: "2026-08-10", end: "2026-08-16" },
      slides: Array.from({ length: 4 }, (_, index) => ({
        title: `第 ${index + 1} 页`,
        body: ["已批准但证据文件缺失"],
        ...(index === 0 ? { sources: [{ title: "来源", url: "https://example.com/evidence" }] } : {}),
      })),
    };
    await assert.rejects(
      () => state.tools.get("director_artifact_deck_write")!.execute("missing-evidence", payload),
      /缺少持久化 weekly\.snapshot 证据/u,
    );
    assert.deepEqual(state.errors, [{ tool: "artifact.deck.write", outcome: "not_committed" }]);
  } finally {
    state.cleanup();
  }
});

test("deck payload is bound to the current task profile, period, snapshot and source hashes", async () => {
  const state = fixture(false, "task-deck-binding", "market-director");
  try {
    writeFileSync(
      join(state.root, "data", "sales", "activities.csv"),
      "activity_id,customer_id,salesperson_id,occurred_at,channel,activity_type,summary,evidence_path,commitment,next_action,next_action_due,created_at\r\n",
      "utf8",
    );
    writeFileSync(
      join(state.root, "data", "sales", "resource-requests.csv"),
      "request_id,customer_id,salesperson_id,requested_at,resource_type,request_summary,business_reason,deadline,owner,status,decision,decision_reason,updated_at\r\n",
      "utf8",
    );
    const period = { start: "2026-08-10", end: "2026-08-18" };
    const snapshot = await state.tools.get("director_weekly_snapshot")!.execute("snapshot", {
      period,
      profile_id: "market-director",
    }) as { details: { snapshot_sha256: string; source_versions: Array<{ path: string; sha256: string }> } };
    const knowledgeSource = snapshot.details.source_versions.find((source) => source.path === "data/knowledge/source-register.csv");
    assert.ok(knowledgeSource);
    const payload = {
      schema_version: "1.0" as const,
      snapshot_sha256: snapshot.details.snapshot_sha256,
      output_name: "weekly-bound.pptx",
      profile_id: "market-director" as const,
      template_id: "ceo-weekly" as const,
      period,
      slides: Array.from({ length: 4 }, (_, index) => ({
        title: `第 ${index + 1} 页`,
        body: ["仅使用当前快照事实"],
        ...(index === 0 ? { sources: [{ title: "知识来源登记", path: knowledgeSource.path, sha256: knowledgeSource.sha256 }] } : {}),
      })),
    };
    assert.doesNotThrow(() => assertDeckMatchesWeeklySnapshot(state.root, "task-deck-binding", "market-director", payload));
    assert.throws(
      () => assertDeckMatchesWeeklySnapshot(state.root, "task-deck-binding", "market-director", { ...payload, snapshot_sha256: "f".repeat(64) }),
      /snapshot_sha256.*不一致/u,
    );
    assert.throws(
      () => assertDeckMatchesWeeklySnapshot(state.root, "task-deck-binding", "product-director", payload),
      /Profile 必须一致/u,
    );
    const rogueUrlPayload = structuredClone(payload);
    rogueUrlPayload.slides[0]!.sources = [{ title: "未入快照来源", url: "https://example.com/rogue" }] as never;
    assert.throws(
      () => assertDeckMatchesWeeklySnapshot(state.root, "task-deck-binding", "market-director", rogueUrlPayload),
      /URL 来源不在当前 weekly\.snapshot/u,
    );

    const restartedTools = new Map<string, RegisteredTool>();
    registerDataAdapters({ registerTool(tool: RegisteredTool & { name: string }) { restartedTools.set(tool.name, tool); } } as never, {
      projectRoot: () => state.root,
      beforeLogicalTool: () => ({ task_id: "task-deck-binding", profile_id: "market-director" }),
      afterLogicalTool: () => {},
      onLogicalToolError: () => {},
    });
    const refreshed = await restartedTools.get("director_weekly_snapshot")!.execute("snapshot-after-restart", {
      period,
      profile_id: "market-director",
    }) as { details: { snapshot_sha256: string } };
    assert.notEqual(refreshed.details.snapshot_sha256, snapshot.details.snapshot_sha256);
    assert.throws(
      () => assertDeckMatchesWeeklySnapshot(state.root, "task-deck-binding", "market-director", payload),
      /snapshot_sha256.*不一致/u,
    );
    assert.doesNotThrow(() => assertDeckMatchesWeeklySnapshot(
      state.root,
      "task-deck-binding",
      "market-director",
      { ...payload, snapshot_sha256: refreshed.details.snapshot_sha256 },
    ));
    mkdirSync(join(state.root, "outputs"), { recursive: true });
    writeFileSync(join(state.root, "outputs", payload.output_name), "occupied", "utf8");
    await assert.rejects(
      () => state.tools.get("director_artifact_deck_write")!.execute("occupied-output", {
        ...payload,
        snapshot_sha256: refreshed.details.snapshot_sha256,
      }),
      /已存在且不属于当前意图/u,
    );
    assert.deepEqual(state.errors.at(-1), { tool: "artifact.deck.write", outcome: "not_committed" });
  } finally {
    state.cleanup();
  }
});

test("the same deck intent is exclusively locked across the full commit lifecycle", () => {
  const state = fixture();
  try {
    const release = holdDeckIntentLockForTests(state.root, "intent-same-deck");
    assert.throws(
      () => holdDeckIntentLockForTests(state.root, "intent-same-deck"),
      /同一 PPT 写入意图正在由另一个任务处理/u,
    );
    release();
    const releaseAfterRetry = holdDeckIntentLockForTests(state.root, "intent-same-deck");
    releaseAfterRetry();
  } finally {
    state.cleanup();
  }
});

test("weekly snapshot is period-bounded and preserves source versions and hashes", () => {
  const state = fixture();
  try {
    writeFileSync(
      join(state.root, "data", "sales", "activities.csv"),
      "activity_id,customer_id,salesperson_id,occurred_at,channel,activity_type,summary,evidence_path,commitment,next_action,next_action_due,created_at\r\na-1,c-1,s-1,2026-08-15T10:00:00.000Z,meeting,visit,本周拜访,,,,,2026-08-15T10:00:00.000Z\r\na-boundary,c-1,s-1,2026-08-09T16:00:00.000Z,meeting,visit,北京时间周一零点,,,,,2026-08-09T16:00:00.000Z\r\na-before,c-1,s-1,2026-08-09T15:59:59.999Z,meeting,visit,北京时间周日前,,,,,2026-08-09T15:59:59.999Z\r\na-old,c-1,s-1,2026-07-01T10:00:00.000Z,meeting,visit,旧拜访,,,,,2026-07-01T10:00:00.000Z\r\n",
      "utf8",
    );
    writeFileSync(
      join(state.root, "data", "sales", "resource-requests.csv"),
      "request_id,customer_id,salesperson_id,requested_at,resource_type,request_summary,business_reason,deadline,owner,status,decision,decision_reason,updated_at\r\nr-1,c-1,s-1,2026-08-16,方案,需要支持,推进验证,2026-08-20,市场,pending,,,2026-08-16T08:00:00.000Z\r\n",
      "utf8",
    );
    const taskDirectory = join(state.root, ".pi", "director-runtime", "tasks");
    mkdirSync(taskDirectory, { recursive: true });
    writeFileSync(join(taskDirectory, "task-week.json"), JSON.stringify({
      task_id: "task-week", profile_id: "market-director", service_id: "weekly-report",
      workflow_id: "shared.reporting.weekly-deck", request: "生成周报", status: "completed", version: 4,
      completed_nodes: ["snapshot"], artifacts: ["outputs/evidence.txt"],
      created_at: "2026-08-14T08:00:00.000Z", updated_at: "2026-08-16T08:00:00.000Z",
      audit: [{ at: "2026-08-16T08:00:00.000Z", action: "tool_completed", actor: "adapter" }],
    }), "utf8");
    writeFileSync(join(taskDirectory, "task-product.json"), JSON.stringify({
      task_id: "task-product", profile_id: "product-director", service_id: "weekly-report",
      workflow_id: "shared.reporting.weekly-deck", request: "产品周报", status: "completed", version: 2,
      completed_nodes: ["snapshot"], artifacts: ["outputs/product-evidence.txt"],
      created_at: "2026-08-14T09:00:00.000Z", updated_at: "2026-08-16T09:00:00.000Z", audit: [],
    }), "utf8");
    const outputDirectory = join(state.root, "outputs");
    mkdirSync(outputDirectory, { recursive: true });
    const outputPath = join(outputDirectory, "evidence.txt");
    writeFileSync(outputPath, "weekly evidence", "utf8");
    utimesSync(outputPath, new Date("2026-08-16T12:00:00.000Z"), new Date("2026-08-16T12:00:00.000Z"));
    const productOutputPath = join(outputDirectory, "product-evidence.txt");
    writeFileSync(productOutputPath, "product evidence", "utf8");
    utimesSync(productOutputPath, new Date("2026-08-16T12:00:00.000Z"), new Date("2026-08-16T12:00:00.000Z"));

    const snapshot = collectWeeklySnapshot(state.root, { start: "2026-08-10", end: "2026-08-16" }, "market-director") as {
      snapshot_sha256: string;
      tasks: Array<{ task_id: string }>;
      sales: { activities: Array<{ activity_id: string }>; resource_requests: Array<{ request_id: string }> };
      outputs: Array<{ path: string; sha256: string }>;
      source_versions: Array<{ path: string; sha256: string; version: string }>;
    };
    assert.match(snapshot.snapshot_sha256, /^[a-f0-9]{64}$/u);
    assert.deepEqual(snapshot.tasks.map((task) => task.task_id), ["task-week"]);
    assert.deepEqual(snapshot.sales.activities.map((row) => row.activity_id), ["a-1", "a-boundary"]);
    assert.deepEqual(snapshot.sales.resource_requests.map((row) => row.request_id), ["r-1"]);
    assert.equal(snapshot.outputs[0].path, "outputs/evidence.txt");
    assert.equal(snapshot.outputs.length, 1);
    assert.match(snapshot.outputs[0].sha256, /^[a-f0-9]{64}$/u);
    assert.ok(snapshot.source_versions.every((source) => /^[a-f0-9]{64}$/u.test(source.sha256) && source.version.startsWith("sha256:")));
    assert.ok(snapshot.source_versions.every((source) => source.path !== ".pi/director-runtime/tasks/task-product.json"));
    const productSnapshot = collectWeeklySnapshot(state.root, { start: "2026-08-10", end: "2026-08-16" }, "product-director") as {
      tasks: Array<{ task_id: string }>;
      sales: Record<string, unknown>;
      outputs: Array<{ path: string }>;
      source_versions: Array<{ path: string }>;
    };
    assert.deepEqual(productSnapshot.tasks.map((task) => task.task_id), ["task-product"]);
    assert.deepEqual(productSnapshot.sales, {});
    assert.deepEqual(productSnapshot.outputs.map((output) => output.path), ["outputs/product-evidence.txt"]);
    assert.ok(productSnapshot.source_versions.every((source) => source.path !== ".pi/director-runtime/tasks/task-week.json"));
    assert.throws(
      () => collectWeeklySnapshot(state.root, { start: "2026-01-01", end: "2026-03-01" }),
      /不能超过 31 天/,
    );
  } finally {
    state.cleanup();
  }
});

test("weekly snapshot rejects cross-profile requests and reports task inventory truncation", async () => {
  const state = fixture(false, "task-weekly-profile", "market-director");
  try {
    await assert.rejects(
      () => state.tools.get("director_weekly_snapshot")!.execute("wrong-profile", {
        period: { start: "2026-08-10", end: "2026-08-16" },
        profile_id: "product-director",
      }),
      /Profile 必须与当前受管任务一致/u,
    );

    const taskDirectory = join(state.root, ".pi", "director-runtime", "tasks");
    mkdirSync(taskDirectory, { recursive: true });
    for (let index = 0; index < 501; index += 1) {
      writeFileSync(join(taskDirectory, `product-${String(index).padStart(3, "0")}.json`), JSON.stringify({
        task_id: `product-${index}`,
        profile_id: "product-director",
        created_at: "2026-07-01T00:00:00.000Z",
        updated_at: "2026-07-01T00:00:00.000Z",
        audit: [],
      }), "utf8");
    }
    const snapshot = collectWeeklySnapshot(
      state.root,
      { start: "2026-08-10", end: "2026-08-16" },
      "product-director",
    ) as { truncation: { tasks: { discovered: number; inspected: number; returned: number; truncated: boolean } } };
    assert.deepEqual(snapshot.truncation.tasks, {
      discovered: 501,
      inspected: 500,
      returned: 0,
      truncated: true,
    });
  } finally {
    state.cleanup();
  }
});

test("a same-table batch is all-or-nothing and a committed receipt makes retry idempotent", async () => {
  const state = fixture(true);
  try {
    const path = join(state.root, "data", "sales", "customers.csv");
    const original = readFileSync(path, "utf8");
    const write = state.tools.get("director_sales_write")!;
    await assert.rejects(
      () => write.execute("atomic", {
        table: "customers",
        mutations: [
          { operation: "insert", record_id: "c-2", changes: { customer_name: "客户乙" } },
          { operation: "insert", record_id: "c-3", changes: { hidden: "invalid" } },
        ],
      }),
      /未知字段/,
    );
    assert.equal(readFileSync(path, "utf8"), original);

    const payload = {
      table: "customers",
      mutations: [
        { operation: "insert", record_id: "c-2", changes: { customer_name: "客户乙" } },
        { operation: "insert", record_id: "c-3", changes: { customer_name: "客户丙" } },
      ],
    };
    // A fresh fixed intent is required because the rejected payload was never approved in real runtime.
    state.cleanup();
    const retryState = fixture(true);
    try {
      const retryWrite = retryState.tools.get("director_sales_write")!;
      await retryWrite.execute("first", payload);
      const afterFirst = readFileSync(join(retryState.root, "data", "sales", "customers.csv"), "utf8");
      await retryWrite.execute("retry", payload);
      assert.equal(readFileSync(join(retryState.root, "data", "sales", "customers.csv"), "utf8"), afterFirst);
      assert.match(afterFirst, /客户乙/);
      assert.match(afterFirst, /客户丙/);
    } finally {
      retryState.cleanup();
    }
  } finally {
    // cleanup is idempotent because the first fixture may already have been removed above.
    state.cleanup();
  }
});

test("sales asset activation enforces authorization, deidentification and review rules", async () => {
  const state = fixture();
  try {
    const path = join(state.root, "data", "sales", "sales-assets.csv");
    const original = readFileSync(path, "utf8");
    await assert.rejects(
      () => state.tools.get("director_sales_write")!.execute("unsafe-asset", {
        table: "sales_assets",
        mutations: [{
          operation: "insert", record_id: "asset-1",
          changes: {
            asset_type: "deck", title: "客户方案", scope: "customer-specific", customer_id: "",
            status: "active", authorization_status: "unknown", deidentification_status: "unknown",
          },
        }],
      }),
      /customer_id|客户专属/,
    );
    assert.equal(readFileSync(path, "utf8"), original);
  } finally {
    state.cleanup();
  }
});

test("CSV writes reject spreadsheet formula injection prefixes", async () => {
  const state = fixture();
  try {
    const path = join(state.root, "data", "sales", "customers.csv");
    const original = readFileSync(path, "utf8");
    await assert.rejects(
      () => state.tools.get("director_sales_write")!.execute("formula", {
        table: "customers",
        mutations: [{ operation: "insert", record_id: "c-formula", changes: { customer_name: "=HYPERLINK(\"https://evil.test\")" } }],
      }),
      /公式前缀/,
    );
    assert.equal(readFileSync(path, "utf8"), original);
  } finally {
    state.cleanup();
  }
});
