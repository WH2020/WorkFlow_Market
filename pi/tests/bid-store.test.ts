import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";

import { bidDocumentSnapshotSha256, bidSnapshotSha256, openBiddingStore } from "../extensions/bid-store.ts";

function fixture(): { root: string; bidId: string } {
  const root = mkdtempSync(join(tmpdir(), "agent4market-bid-store-"));
  const bootstrap = openBiddingStore(root, false);
  const databasePath = bootstrap.database_path;
  bootstrap.close();
  const database = new DatabaseSync(databasePath);
  const at = new Date().toISOString();
  const bidId = "bid-test-001";
  database.prepare(`
    INSERT INTO bid_projects(
      bid_id,workspace_project_id,name,status,current_stage,go_no_go,version,created_at,updated_at
    ) VALUES (?, 'project-default', '测试采购项目', 'interpreting', 'interpretation', 'pending', 1, ?, ?)
  `).run(bidId, at, at);
  database.close();
  return { root, bidId };
}

test("bidding store commits approved mutations idempotently and exposes a stable snapshot", () => {
  const { root, bidId } = fixture();
  try {
    const store = openBiddingStore(root, false);
    try {
      const before = store.readProject(bidId)!;
      const beforeHash = bidSnapshotSha256(before);
      assert.match(beforeHash, /^[a-f0-9]{64}$/u);
      const request = {
        intent_id: "intent-bid-001",
        task_id: "task-bid-001",
        session_id: "session-bid-001",
        logical_tool: "bid.write" as const,
        approved_payload_sha256: "1".repeat(64),
        bid_id: bidId,
        mutations: [{
          operation: "insert" as const,
          table: "bid_requirements" as const,
          record_id: "requirement-001",
          changes: {
            bid_id: bidId,
            category: "qualification",
            mandatory: "1",
            title: "主体资格",
            requirement_text: "投标人须具备独立法人资格。",
            evidence_locator_json: JSON.stringify({ document_id: "document-001", page: 3 }),
            verification_status: "verified",
            response_status: "unaddressed",
          },
        }],
      };
      const committed = store.commit(request);
      assert.equal(committed.mutations[0]?.version, 1);
      assert.deepEqual(store.commit(request), committed);
      const after = store.readProject(bidId)!;
      assert.equal((after.sections as Record<string, unknown[]>).requirements.length, 1);
      assert.notEqual(bidSnapshotSha256(after), beforeHash);
      assert.notEqual(bidDocumentSnapshotSha256(after), bidDocumentSnapshotSha256(before));
      assert.equal(store.readReceipt(request.intent_id)?.payload_sha256, request.approved_payload_sha256);
      assert.throws(() => store.commit({ ...request, approved_payload_sha256: "2".repeat(64) }), /同一投标写入 intent/);
    } finally { store.close(); }
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("bidding store initializes an empty SQLite header but rejects unknown existing tables", () => {
  const emptyRoot = mkdtempSync(join(tmpdir(), "agent4market-bid-empty-header-"));
  const unknownRoot = mkdtempSync(join(tmpdir(), "agent4market-bid-unknown-schema-"));
  try {
    const emptyData = join(emptyRoot, "data", "bids");
    mkdirSync(emptyData, { recursive: true });
    const emptyDatabase = new DatabaseSync(join(emptyData, "bidding.sqlite3"));
    emptyDatabase.exec("PRAGMA user_version = 0");
    emptyDatabase.close();
    const initialized = openBiddingStore(emptyRoot, false);
    assert.deepEqual(initialized.searchProjects(), []);
    initialized.close();

    const unknownData = join(unknownRoot, "data", "bids");
    mkdirSync(unknownData, { recursive: true });
    const unknownDatabase = new DatabaseSync(join(unknownData, "bidding.sqlite3"));
    unknownDatabase.exec("CREATE TABLE foreign_business_data(value TEXT); INSERT INTO foreign_business_data VALUES ('preserve')");
    unknownDatabase.close();
    assert.throws(() => openBiddingStore(unknownRoot, false), /未知业务表/u);
    const preserved = new DatabaseSync(join(unknownData, "bidding.sqlite3"), { readOnly: true });
    assert.equal((preserved.prepare("SELECT value FROM foreign_business_data").get() as { value: string }).value, "preserve");
    preserved.close();
  } finally {
    rmSync(emptyRoot, { recursive: true, force: true });
    rmSync(unknownRoot, { recursive: true, force: true });
  }
});

test("bidding store enforces record versions and registers only matching generated artifacts", () => {
  const { root, bidId } = fixture();
  try {
    const store = openBiddingStore(root, false);
    try {
      const update = {
        intent_id: "intent-bid-update",
        task_id: "task-bid-update",
        session_id: "session-bid-update",
        logical_tool: "bid.write" as const,
        approved_payload_sha256: "3".repeat(64),
        bid_id: bidId,
        mutations: [{
          operation: "update" as const,
          table: "bid_projects" as const,
          record_id: bidId,
          expected_version: "sqlite:1",
          changes: { summary: "已完成首轮解读。" },
        }],
      };
      assert.equal(store.commit(update).mutations[0]?.version, 2);
      assert.throws(() => store.commit({ ...update, intent_id: "intent-stale", approved_payload_sha256: "4".repeat(64) }), /已更新/);
      const beforeArtifact = store.readProject(bidId)!;
      const beforeArtifactFullHash = bidSnapshotSha256(beforeArtifact);
      const beforeArtifactDocumentHash = bidDocumentSnapshotSha256(beforeArtifact);

      const outputDirectory = join(root, "outputs", "bids", bidId);
      mkdirSync(outputDirectory, { recursive: true });
      const output = join(outputDirectory, "formal-bid.docx");
      const bytes = Buffer.from("controlled docx fixture");
      writeFileSync(output, bytes);
      const digest = createHash("sha256").update(bytes).digest("hex");
      const artifact = store.recordArtifact({
        bid_id: bidId,
        task_id: "task-document-001",
        intent_id: "intent-document-001",
        relative_path: `outputs/bids/${bidId}/formal-bid.docx`,
        sha256: digest,
        byte_size: bytes.length,
        qa: { status: "ok" },
      });
      assert.equal(artifact.status, "ready");
      assert.equal(store.recordArtifact({
        bid_id: bidId,
        task_id: "task-document-001",
        intent_id: "intent-document-001",
        relative_path: `outputs/bids/${bidId}/formal-bid.docx`,
        sha256: digest,
        byte_size: bytes.length,
        qa: { status: "ok" },
      }).artifact_id, artifact.artifact_id);
      const afterArtifact = store.readProject(bidId)!;
      assert.notEqual(bidSnapshotSha256(afterArtifact), beforeArtifactFullHash);
      assert.equal(bidDocumentSnapshotSha256(afterArtifact), beforeArtifactDocumentHash);
    } finally { store.close(); }
  } finally { rmSync(root, { recursive: true, force: true }); }
});
