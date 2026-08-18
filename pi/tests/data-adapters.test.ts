import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { parseCsv, registerDataAdapters, serializeCsv } from "../extensions/data-adapters.ts";
import { payloadSha256 } from "../extensions/task-runtime.ts";

type RegisteredTool = {
  execute: (toolCallId: string, params: Record<string, unknown>) => Promise<unknown>;
};

function fixture(fixedIntent = false) {
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
        return { intent_id: `intent-${intent}`, payload_sha256: payloadSha256(params) };
      }
    },
    afterLogicalTool: (tool, _params, details) => after.push({ tool, details }),
    onLogicalToolError: () => {},
  });
  return {
    root,
    tools,
    before,
    after,
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
