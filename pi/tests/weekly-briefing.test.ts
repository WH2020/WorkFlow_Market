import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { archiveWeeklySnapshot, assertDeckPayload, collectWeeklySnapshot } from "../extensions/data-adapters.ts";
import { buildPersonalizedWeeklyBriefing } from "../extensions/weekly-briefing.ts";


test("personalized weekly briefing keeps deterministic ownership and source provenance", () => {
  const result = buildPersonalizedWeeklyBriefing({
    period: { start: "2026-08-24", end: "2026-08-28" },
    salespeople: [
      { salesperson_id: "seller-a", name: "销售甲", active: true },
      { salesperson_id: "seller-new", name: "新销售", active: true },
    ],
    customers: [
      { customer_id: "account-a", customer_name: "客户甲", owner: "销售甲", next_action: "确认试点范围", next_action_due: "2026-08-28" },
      { customer_id: "account-u", customer_name: "待分配客户", owner: "未知姓名" },
    ],
    activities: [{ activity_id: "activity-a", customer_id: "account-a", salesperson_id: "seller-a", occurred_at: "2026-08-25T09:00:00+08:00", summary: "客户确认试点意向" }],
    resource_requests: [{ request_id: "request-a", customer_id: "account-a", salesperson_id: "seller-a", request_summary: "申请技术演示支持", deadline: "2026-08-28", status: "open" }],
    sales_assets: [
      { asset_id: "asset-ok", asset_type: "deck", title: "试点方案", customer_id: "account-a", status: "active", authorization_status: "authorized", source_path: "inputs/plan.pptx" },
      { asset_id: "asset-blocked", asset_type: "deck", title: "未授权材料", customer_id: "account-a", status: "active", authorization_status: "pending", source_path: "inputs/private.pptx" },
    ],
  }) as {
    manager_rollup: { unassigned_accounts: Array<{ account_id: string }> };
    seller_briefs: Array<Record<string, any>>;
    validation: { filtered_asset_count: number; messages: string[] };
  };
  const seller = result.seller_briefs.find((row) => row.salesperson_id === "seller-a")!;
  assert.deepEqual(seller.account_updates[0].evidence, { type: "activity", id: "activity-a" });
  assert.deepEqual(seller.top_actions[0].evidence, { type: "resource_request", id: "request-a" });
  assert.equal(seller.recommended_assets[0].source_path, "inputs/plan.pptx");
  assert.equal("url" in seller.recommended_assets[0], false);
  assert.match(result.seller_briefs.find((row) => row.salesperson_id === "seller-new")!.welcome_note, /尚未找到可确认归属的客户/u);
  assert.deepEqual(result.manager_rollup.unassigned_accounts.map((row) => row.account_id), ["account-u"]);
  assert.equal(result.validation.filtered_asset_count, 1);
  assert.ok(result.validation.messages.some((message) => message.includes("没有自动猜测负责人")));
});

test("sales-director weekly snapshot includes sales action briefing", () => {
  const root = mkdtempSync(join(tmpdir(), "weekly-sales-director-"));
  try {
    mkdirSync(join(root, "data", "knowledge"), { recursive: true });
    mkdirSync(join(root, "data", "sales"), { recursive: true });
    writeFileSync(join(root, "data", "knowledge", "source-register.csv"), "source_id,title,url,publisher,published_date,accessed_date,region,topic,source_type,quality,exposure_status,status,notes\r\n", "utf8");
    writeFileSync(join(root, "data", "sales", "customers.csv"), "customer_id,customer_name,region,sector,owner,stage,health,key_contact,decision_maker,budget_path,next_action,next_action_due,last_evidence_date,risks,updated_at\r\naccount-a,客户甲,,,销售甲,,,,,,确认范围,2026-08-28,,,2026-08-25T09:00:00+08:00\r\n", "utf8");
    writeFileSync(join(root, "data", "sales", "activities.csv"), "activity_id,customer_id,salesperson_id,occurred_at,channel,activity_type,summary,evidence_path,commitment,next_action,next_action_due,created_at\r\nactivity-a,account-a,seller-a,2026-08-25T10:00:00+08:00,现场,拜访,客户确认意向,,,,,2026-08-25T10:00:00+08:00\r\n", "utf8");
    writeFileSync(join(root, "data", "sales", "resource-requests.csv"), "request_id,customer_id,salesperson_id,requested_at,resource_type,request_summary,business_reason,deadline,owner,status,decision,decision_reason,updated_at\r\n", "utf8");
    writeFileSync(join(root, "data", "sales", "sales-assets.csv"), "asset_id,asset_type,title,scope,customer_id,audience_role,sales_stage,use_case,owner,status,authorization_status,deidentification_status,version,source_path,evidence_refs,last_validated_at,next_review_at,usage_feedback,updated_at\r\n", "utf8");
    writeFileSync(join(root, "data", "sales", "salespeople.json"), JSON.stringify({ salespeople: [{ salesperson_id: "seller-a", name: "销售甲", active: true }] }), "utf8");
    const snapshot = collectWeeklySnapshot(root, { start: "2026-08-24", end: "2026-08-28" }, "sales-director") as {
      sales: { customers: unknown[]; activities: unknown[] };
      sales_briefing: { manager_rollup: { seller_count: number }; seller_briefs: Array<{ salesperson_id: string }> };
      snapshot_sha256: string;
    };
    assert.equal(snapshot.sales.customers.length, 1);
    assert.equal(snapshot.sales.activities.length, 1);
    assert.equal(snapshot.sales_briefing.manager_rollup.seller_count, 1);
    assert.equal(snapshot.sales_briefing.seller_briefs[0]!.salesperson_id, "seller-a");
    assert.match(snapshot.snapshot_sha256, /^[a-f0-9]{64}$/u);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("weekly snapshot archives are immutable and task/hash bound", () => {
  const root = mkdtempSync(join(tmpdir(), "weekly-archive-"));
  try {
    const snapshot = { schema_version: "1.0", snapshot_sha256: "a".repeat(64), period: { start: "2026-08-24", end: "2026-08-28" } };
    const path = archiveWeeklySnapshot(root, "task-week", snapshot);
    assert.equal(path, `.pi/director-runtime/weekly-snapshots/task-week-${"a".repeat(64)}.json`);
    assert.equal(archiveWeeklySnapshot(root, "task-week", snapshot), path);
    const absolute = join(root, ...path.split("/"));
    writeFileSync(absolute, "{}\n", "utf8");
    assert.throws(() => archiveWeeklySnapshot(root, "task-week", snapshot), /内容不一致/u);
    assert.equal(readFileSync(absolute, "utf8"), "{}\n", "archive helper must not overwrite an existing path");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("sales-director is an accepted governed deck profile", () => {
  const payload = {
    schema_version: "1.0" as const,
    snapshot_sha256: "b".repeat(64),
    output_name: "sales-action-brief.pptx",
    profile_id: "sales-director" as const,
    template_id: "management-report" as const,
    period: { start: "2026-08-24", end: "2026-08-28" },
    slides: Array.from({ length: 4 }, (_, index) => ({
      title: `第 ${index + 1} 页`, body: ["有来源约束的内容"],
      ...(index === 0 ? { sources: [{ title: "示例来源", url: "https://example.com/source" }] } : {}),
    })),
  };
  assert.doesNotThrow(() => assertDeckPayload(payload));
  assert.throws(() => assertDeckPayload({ ...payload, profile_id: "unknown-profile" as never }), /Profile 无效/u);
});
