import { createHash } from "node:crypto";
import {
  closeSync,
  existsSync,
  lstatSync,
  mkdirSync,
  openSync,
  readFileSync,
  realpathSync,
  rmSync,
} from "node:fs";
import { dirname, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { DatabaseSync } from "node:sqlite";

import { LocalBusinessStoreError } from "./local-business-store.ts";

export const BID_SCHEMA_VERSION = 1;
export const BID_APPLICATION_VERSION = "0.15.0";
export const BID_MAX_MUTATIONS = 100;

type SqlValue = string | number | null;

type BidTableDefinition = {
  id: string;
  fields: readonly string[];
  required: readonly string[];
  integers?: readonly string[];
  json?: readonly string[];
  insertAllowed?: boolean;
};

const BID_TABLES = {
  bid_projects: {
    id: "bid_id",
    fields: [
      "account_id", "opportunity_id", "workspace_project_id", "name", "buyer", "tender_number",
      "lot_name", "owner", "deadline_at", "budget_minor", "currency", "status", "current_stage",
      "go_no_go", "decision_reason", "summary",
    ],
    required: ["workspace_project_id", "name", "status", "current_stage", "go_no_go"],
    integers: ["budget_minor"],
    insertAllowed: false,
  },
  bid_milestones: {
    id: "milestone_id",
    fields: ["bid_id", "milestone_type", "title", "due_at", "owner", "status", "evidence_json"],
    required: ["bid_id", "milestone_type", "title", "status", "evidence_json"],
    json: ["evidence_json"],
  },
  bid_requirements: {
    id: "requirement_id",
    fields: [
      "bid_id", "category", "mandatory", "score_points", "title", "requirement_text",
      "evidence_locator_json", "verification_status", "response_status", "owner", "due_at",
    ],
    required: [
      "bid_id", "category", "mandatory", "title", "requirement_text", "evidence_locator_json",
      "verification_status", "response_status",
    ],
    integers: ["mandatory", "score_points"],
    json: ["evidence_locator_json"],
  },
  bid_response_matrix: {
    id: "response_id",
    fields: [
      "bid_id", "requirement_id", "section_id", "response_strategy", "material_need", "material_status",
      "owner", "due_at", "deviation", "status",
    ],
    required: ["bid_id", "requirement_id", "material_status", "status"],
  },
  bid_facts: {
    id: "fact_id",
    fields: [
      "bid_id", "category", "field_name", "value_text", "evidence_json", "verification_status",
      "affected_sections_json",
    ],
    required: ["bid_id", "category", "field_name", "evidence_json", "verification_status", "affected_sections_json"],
    json: ["evidence_json", "affected_sections_json"],
  },
  bid_sections: {
    id: "section_id",
    fields: [
      "bid_id", "parent_section_id", "order_index", "level", "title", "objective", "owner",
      "content_markdown", "evidence_json", "status", "input_sha256",
    ],
    required: ["bid_id", "order_index", "level", "title", "evidence_json", "status"],
    integers: ["order_index", "level"],
    json: ["evidence_json"],
  },
  bid_checks: {
    id: "check_id",
    fields: [
      "bid_id", "rule_id", "rule_version", "category", "severity", "status", "finding",
      "recommendation", "requirement_id", "section_id", "evidence_json", "input_sha256",
      "resolved_by", "resolved_at",
    ],
    required: [
      "bid_id", "rule_id", "rule_version", "category", "severity", "status", "finding",
      "evidence_json", "input_sha256",
    ],
    json: ["evidence_json"],
  },
  bid_risks: {
    id: "risk_id",
    fields: [
      "bid_id", "category", "risk_text", "impact", "likelihood", "status", "owner",
      "mitigation_action", "evidence_json",
    ],
    required: ["bid_id", "category", "risk_text", "impact", "likelihood", "status", "evidence_json"],
    json: ["evidence_json"],
  },
  bid_decisions: {
    id: "decision_id",
    fields: [
      "bid_id", "decision_type", "decision", "rationale", "approved_by", "approval_task_id",
      "payload_sha256", "decided_at",
    ],
    required: ["bid_id", "decision_type", "decision", "approved_by", "decided_at"],
  },
  bid_outcomes: {
    id: "outcome_id",
    fields: [
      "bid_id", "result", "amount_minor", "currency", "reason", "competitor_notes", "lessons",
      "evidence_json", "decided_at",
    ],
    required: ["bid_id", "result", "evidence_json"],
    integers: ["amount_minor"],
    json: ["evidence_json"],
  },
} as const satisfies Record<string, BidTableDefinition>;

export type BidTable = keyof typeof BID_TABLES;

export type BidMutation = {
  operation: "insert" | "update";
  table: BidTable;
  record_id: string;
  changes: Record<string, string>;
  expected_version?: string;
};

export type BidCommitRequest = {
  intent_id: string;
  task_id: string;
  session_id: string;
  logical_tool: "bid.write";
  approved_payload_sha256: string;
  bid_id: string;
  mutations: BidMutation[];
};

export type BidCommitResult = {
  intent_id: string;
  task_id: string;
  logical_tool: "bid.write";
  payload_sha256: string;
  bid_id: string;
  mutations: Array<{ table: BidTable; record_id: string; operation: "insert" | "update"; version: number }>;
  committed_at: string;
};

function sha256(value: string | Buffer): string {
  return createHash("sha256").update(value).digest("hex");
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value === "boolean" || typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number" && Number.isFinite(value)) return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (!value || typeof value !== "object") throw new LocalBusinessStoreError("INVALID_INPUT", "快照包含不可序列化的值");
  return `{${Object.entries(value as Record<string, unknown>)
    .filter(([, item]) => item !== undefined)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`).join(",")}}`;
}

export function bidSnapshotSha256(value: unknown): string {
  return sha256(Buffer.from(canonicalJson(value), "utf8"));
}

/**
 * Hash only the inputs that can affect a generated bid document. Generated
 * artifacts are deliberately excluded so registering the just-built DOCX does
 * not invalidate the approval that produced it. All other project sections,
 * including checks, decisions, and outcomes, remain covered by the approval.
 */
export function bidDocumentSnapshotSha256(value: unknown): string {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new LocalBusinessStoreError("INVALID_INPUT", "正式标书快照结构无效");
  }
  const snapshot = value as Record<string, unknown>;
  const rawSections = snapshot.sections;
  if (!rawSections || typeof rawSections !== "object" || Array.isArray(rawSections)) {
    throw new LocalBusinessStoreError("INVALID_INPUT", "正式标书快照缺少项目分区");
  }
  const { artifacts: _generatedArtifacts, ...documentSections } = rawSections as Record<string, unknown>;
  return bidSnapshotSha256({ project: snapshot.project, sections: documentSections });
}

function assertText(value: unknown, label: string, maximum = 1024): asserts value is string {
  if (typeof value !== "string" || !value.trim() || value.length > maximum || /[\0\r\n]/u.test(label === "record_id" ? value : "")) {
    throw new LocalBusinessStoreError("INVALID_INPUT", `${label} 必须是有效文本`);
  }
}

function assertSafeId(value: unknown, label: string): asserts value is string {
  if (typeof value !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/u.test(value)) {
    throw new LocalBusinessStoreError("INVALID_INPUT", `${label} 必须是安全编号`);
  }
}

function assertSha(value: unknown, label: string): asserts value is string {
  if (typeof value !== "string" || !/^[a-f0-9]{64}$/u.test(value)) {
    throw new LocalBusinessStoreError("INVALID_INPUT", `${label} 必须是小写 SHA-256`);
  }
}

function defaultSchemaPath(): string {
  return fileURLToPath(new URL("../../agent_platform/bid_migrations/001_bidding_core.sql", import.meta.url));
}

function defaultManifestPath(): string {
  return fileURLToPath(new URL("../../agent_platform/bid_migrations/manifest.json", import.meta.url));
}

function migration(): { sql: string; name: string; hash: string } {
  const manifest = JSON.parse(readFileSync(defaultManifestPath(), "utf8")) as Record<string, unknown>;
  const entries = manifest.migrations;
  if (
    manifest.schema_version !== BID_SCHEMA_VERSION || manifest.application_version !== BID_APPLICATION_VERSION ||
    !Array.isArray(entries) || entries.length !== 1
  ) {
    throw new LocalBusinessStoreError("SCHEMA_MANIFEST", "招投标迁移清单版本无效");
  }
  const entry = entries[0] as Record<string, unknown>;
  if (entry.version !== BID_SCHEMA_VERSION || typeof entry.file !== "string" || typeof entry.name !== "string") {
    throw new LocalBusinessStoreError("SCHEMA_MANIFEST", "招投标迁移条目无效");
  }
  const sql = readFileSync(defaultSchemaPath(), "utf8");
  const hash = sha256(Buffer.from(sql, "utf8"));
  if (entry.sha256 !== hash) throw new LocalBusinessStoreError("SCHEMA_HASH_MISMATCH", "招投标 schema SQL 哈希不一致");
  return { sql, name: entry.name, hash };
}

function mapSqlError(error: unknown): never {
  if (error instanceof LocalBusinessStoreError) throw error;
  const candidate = error as { code?: string; message?: string; errcode?: number; errstr?: string };
  const primaryCode = typeof candidate?.errcode === "number" ? candidate.errcode & 0xff : undefined;
  if (primaryCode === 5 || primaryCode === 6 || /locked|busy/iu.test(candidate.message ?? "")) {
    throw new LocalBusinessStoreError("STORE_BUSY", "招投标数据库正在被另一个短事务占用，请稍后重试");
  }
  if (candidate?.code === "ERR_SQLITE_CONSTRAINT" || primaryCode === 19 || /constraint/iu.test(candidate.errstr ?? "")) {
    throw new LocalBusinessStoreError("CONSTRAINT", `招投标业务约束未通过：${candidate.message ?? "SQLite constraint"}`);
  }
  if (primaryCode === 8) throw new LocalBusinessStoreError("READ_ONLY", "SQLite 拒绝写入只读招投标数据库");
  if (primaryCode === 13) throw new LocalBusinessStoreError("STORE_FULL", "招投标数据库所在磁盘空间不足");
  if (primaryCode === 11 || primaryCode === 26) throw new LocalBusinessStoreError("STORE_CORRUPT", "招投标数据库损坏或格式无效");
  throw new LocalBusinessStoreError("SQLITE_ERROR", candidate?.message ?? String(error));
}

function parseVersion(value: unknown): number {
  if (typeof value === "string" && /^sqlite:[1-9]\d*$/u.test(value)) return Number(value.slice(7));
  throw new LocalBusinessStoreError("INVALID_INPUT", "update 的 expected_version 必须来自本次读取返回的 _record_version");
}

function convertChanges(definition: BidTableDefinition, changes: Record<string, string>): Record<string, SqlValue> {
  if (!changes || typeof changes !== "object" || Array.isArray(changes)) {
    throw new LocalBusinessStoreError("INVALID_INPUT", "changes 必须是字段对象");
  }
  const fields = Object.keys(changes);
  if (fields.length < 1 || fields.length > definition.fields.length) {
    throw new LocalBusinessStoreError("INVALID_INPUT", "每条投标变更必须包含至少一个受控字段");
  }
  const result: Record<string, SqlValue> = {};
  for (const field of fields.sort()) {
    if (!definition.fields.includes(field)) throw new LocalBusinessStoreError("INVALID_FIELD", `不允许写入字段 ${field}`);
    const value = changes[field];
    if (typeof value !== "string" || value.length > 200_000 || /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/u.test(value)) {
      throw new LocalBusinessStoreError("INVALID_INPUT", `${field} 必须是安全文本且不超过 20 万字`);
    }
    if (definition.integers?.includes(field)) {
      if (value === "") result[field] = null;
      else if (!/^-?\d+$/u.test(value) || !Number.isSafeInteger(Number(value))) {
        throw new LocalBusinessStoreError("INVALID_INPUT", `${field} 必须是整数文本`);
      } else result[field] = Number(value);
      continue;
    }
    if (definition.json?.includes(field)) {
      try {
        const parsed = JSON.parse(value);
        if (parsed === undefined) throw new Error("undefined");
      } catch {
        throw new LocalBusinessStoreError("INVALID_INPUT", `${field} 必须是有效 JSON`);
      }
      result[field] = value;
      continue;
    }
    result[field] = value === "" ? null : value;
  }
  return result;
}

function numberFromRow(row: unknown, label: string): number {
  const value = row && typeof row === "object" && !Array.isArray(row) ? Object.values(row as Record<string, unknown>)[0] : undefined;
  if (typeof value !== "number" || !Number.isSafeInteger(value)) throw new LocalBusinessStoreError("STORE_CORRUPT", `${label} 无效`);
  return value;
}

function stringFromRow(row: unknown, label: string): string {
  const value = row && typeof row === "object" && !Array.isArray(row) ? Object.values(row as Record<string, unknown>)[0] : undefined;
  if (typeof value !== "string") throw new LocalBusinessStoreError("STORE_CORRUPT", `${label} 无效`);
  return value;
}

export class BiddingStore {
  readonly project_root: string;
  readonly database_path: string;
  readonly read_only: boolean;
  readonly binding_id: string;
  private readonly database: DatabaseSync;
  private closed = false;

  constructor(projectRoot: string, readOnly = false) {
    const root = realpathSync.native(resolve(projectRoot));
    this.project_root = root;
    const dataRoot = resolve(root, "data", "bids");
    if (existsSync(dataRoot) && lstatSync(dataRoot).isSymbolicLink()) {
      throw new LocalBusinessStoreError("UNSAFE_PATH", "招投标数据目录不能是符号链接");
    }
    mkdirSync(dataRoot, { recursive: true });
    const canonicalData = realpathSync.native(dataRoot);
    const containment = relative(root, canonicalData);
    if (containment.startsWith("..")) throw new LocalBusinessStoreError("UNSAFE_PATH", "招投标数据目录越出项目范围");
    this.database_path = resolve(canonicalData, "bidding.sqlite3");
    this.read_only = readOnly;
    this.binding_id = `bid-sqlite:v${BID_SCHEMA_VERSION}:${sha256(Buffer.from(this.database_path.toLocaleLowerCase(), "utf8")).slice(0, 24)}`;
    const existed = existsSync(this.database_path);
    if (existed && lstatSync(this.database_path).isSymbolicLink()) {
      throw new LocalBusinessStoreError("UNSAFE_PATH", "招投标数据库不能是符号链接");
    }
    let ownsNewFile = false;
    if (!existed) {
      if (readOnly) throw new LocalBusinessStoreError("STORE_MISSING", "招投标数据库尚未建立");
      try {
        const descriptor = openSync(this.database_path, "wx", 0o600);
        closeSync(descriptor);
        ownsNewFile = true;
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
      }
    }
    try {
      this.database = new DatabaseSync(this.database_path, {
        allowExtension: false,
        defensive: true,
        enableDoubleQuotedStringLiterals: false,
        enableForeignKeyConstraints: true,
        readBigInts: false,
        readOnly,
        returnArrays: false,
        timeout: 5_000,
      });
    } catch (error) {
      if (ownsNewFile) rmSync(this.database_path, { force: true });
      throw error;
    }
    try {
      this.database.exec("PRAGMA busy_timeout = 5000");
      this.database.exec("PRAGMA foreign_keys = ON");
      if (readOnly) this.database.exec("PRAGMA query_only = ON");
      else {
        this.database.exec("PRAGMA journal_mode = WAL");
        this.database.exec("PRAGMA synchronous = FULL");
      }
      const hasSchema = Boolean(this.database.prepare(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bid_schema_migrations'",
      ).get());
      if (!readOnly && !hasSchema) {
        this.database.exec("BEGIN IMMEDIATE");
        const tables = this.database.prepare(
          "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
        ).all() as Array<{ name: string }>;
        if (!tables.some((row) => row.name === "bid_schema_migrations")) {
          if (tables.length > 0) {
            throw new LocalBusinessStoreError("SCHEMA_UNSUPPORTED", "招投标数据库已有未知业务表，拒绝自动初始化");
          }
          const applied = migration();
          this.database.exec(applied.sql);
          const at = new Date().toISOString();
          this.database.prepare(
            "INSERT INTO bid_schema_migrations(version,name,script_sha256,applied_at,application_version,result) VALUES (?,?,?,?,?,'applied')",
          ).run(BID_SCHEMA_VERSION, applied.name, applied.hash, at, BID_APPLICATION_VERSION);
          this.database.prepare("INSERT INTO bid_metadata(key,value,updated_at) VALUES ('schema_version',?,?)")
            .run(String(BID_SCHEMA_VERSION), at);
        }
        this.database.exec("COMMIT");
      }
      this.assertSchema();
    } catch (error) {
      try { this.database.exec("ROLLBACK"); } catch { /* no active transaction */ }
      this.close();
      if (ownsNewFile) for (const suffix of ["", "-wal", "-shm"]) rmSync(`${this.database_path}${suffix}`, { force: true });
      throw error;
    }
  }

  private assertSchema(): void {
    try {
      const row = this.database.prepare(
        "SELECT version,script_sha256 FROM bid_schema_migrations ORDER BY version DESC LIMIT 1",
      ).get() as { version?: unknown; script_sha256?: unknown } | undefined;
      const expected = migration();
      if (row?.version !== BID_SCHEMA_VERSION || row.script_sha256 !== expected.hash) {
        throw new LocalBusinessStoreError("SCHEMA_UNSUPPORTED", `只支持招投标 schema v${BID_SCHEMA_VERSION}`);
      }
      const quick = stringFromRow(this.database.prepare("PRAGMA quick_check").get(), "quick_check");
      if (quick !== "ok") throw new LocalBusinessStoreError("STORE_CORRUPT", `招投标数据库快速检查失败：${quick}`);
    } catch (error) { mapSqlError(error); }
  }

  close(): void {
    if (this.closed) return;
    this.database.close();
    this.closed = true;
  }

  searchProjects(query = "", limit = 50): Record<string, unknown>[] {
    if (typeof query !== "string" || query.length > 500 || !Number.isSafeInteger(limit) || limit < 1 || limit > 100) {
      throw new LocalBusinessStoreError("INVALID_INPUT", "投标项目查询条件无效");
    }
    const normalized = query.normalize("NFKC").trim().toLowerCase();
    const escaped = normalized.replaceAll("\\", "\\\\").replaceAll("%", "\\%").replaceAll("_", "\\_");
    return this.database.prepare(`
      SELECT p.*,
        (SELECT count(*) FROM bid_documents d WHERE d.bid_id=p.bid_id AND d.deleted_at IS NULL) AS document_count,
        (SELECT count(*) FROM bid_requirements r WHERE r.bid_id=p.bid_id AND r.deleted_at IS NULL) AS requirement_count,
        (SELECT count(*) FROM bid_checks c WHERE c.bid_id=p.bid_id AND c.deleted_at IS NULL AND c.status='open' AND c.severity IN ('critical','high')) AS high_risk_check_count
      FROM bid_projects p WHERE p.deleted_at IS NULL
        AND (?='' OR lower(p.name||' '||coalesce(p.buyer,'')||' '||coalesce(p.tender_number,'')) LIKE ? ESCAPE '\\')
      ORDER BY CASE WHEN p.deadline_at IS NULL THEN 1 ELSE 0 END,p.deadline_at,p.updated_at DESC,p.bid_id LIMIT ?
    `).all(normalized, `%${escaped}%`, limit) as Record<string, unknown>[];
  }

  readProject(bidId: string, sections?: string[]): Record<string, unknown> | undefined {
    assertSafeId(bidId, "bid_id");
    const project = this.database.prepare("SELECT * FROM bid_projects WHERE bid_id=? AND deleted_at IS NULL").get(bidId) as Record<string, unknown> | undefined;
    if (!project) return undefined;
    const sectionMap: Record<string, { table: string; order: string }> = {
      documents: { table: "bid_documents", order: "created_at DESC,document_id" },
      milestones: { table: "bid_milestones", order: "due_at,milestone_id" },
      requirements: { table: "bid_requirements", order: "mandatory DESC,category,requirement_id" },
      response_matrix: { table: "bid_response_matrix", order: "status,response_id" },
      facts: { table: "bid_facts", order: "category,field_name,fact_id" },
      sections: { table: "bid_sections", order: "order_index,section_id" },
      checks: { table: "bid_checks", order: "severity,status,check_id" },
      risks: { table: "bid_risks", order: "impact,risk_id" },
      decisions: { table: "bid_decisions", order: "decided_at DESC,decision_id" },
      artifacts: { table: "bid_artifacts", order: "updated_at DESC,artifact_id" },
      outcomes: { table: "bid_outcomes", order: "updated_at DESC,outcome_id" },
    };
    const selected = sections ?? Object.keys(sectionMap);
    if (new Set(selected).size !== selected.length || selected.some((section) => !(section in sectionMap))) {
      throw new LocalBusinessStoreError("INVALID_INPUT", "投标项目分区无效");
    }
    const result: Record<string, unknown> = { project, sections: {} };
    const output = result.sections as Record<string, unknown>;
    for (const section of selected) {
      const definition = sectionMap[section]!;
      output[section] = this.database.prepare(
        `SELECT * FROM ${definition.table} WHERE bid_id=? AND deleted_at IS NULL ORDER BY ${definition.order} LIMIT 1000`,
      ).all(bidId) as Record<string, unknown>[];
    }
    return result;
  }

  readReceipt(intentId: string): BidCommitResult | undefined {
    assertText(intentId, "intent_id", 128);
    const row = this.database.prepare(
      "SELECT payload_sha256,result_json FROM bid_write_receipts WHERE intent_id=?",
    ).get(intentId) as { payload_sha256: string; result_json: string } | undefined;
    if (!row) return undefined;
    try {
      const result = JSON.parse(row.result_json) as BidCommitResult;
      if (result.intent_id !== intentId || result.payload_sha256 !== row.payload_sha256 || !Array.isArray(result.mutations)) throw new Error("receipt mismatch");
      return result;
    } catch (error) {
      throw new LocalBusinessStoreError("RECEIPT_CORRUPT", `招投标写入回执损坏：${String(error)}`);
    }
  }

  recordArtifact(input: {
    bid_id: string;
    task_id: string;
    intent_id: string;
    relative_path: string;
    sha256: string;
    byte_size: number;
    qa: Record<string, unknown>;
  }): Record<string, unknown> {
    if (this.read_only) throw new LocalBusinessStoreError("READ_ONLY", "只读连接不能登记投标产物");
    assertSafeId(input.bid_id, "bid_id");
    assertText(input.task_id, "task_id", 128);
    assertText(input.intent_id, "intent_id", 128);
    assertSha(input.sha256, "artifact sha256");
    if (
      typeof input.relative_path !== "string" ||
      !new RegExp(`^outputs/bids/${input.bid_id.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&")}/[A-Za-z0-9][A-Za-z0-9._-]{0,119}\\.docx$`, "u").test(input.relative_path) ||
      !Number.isSafeInteger(input.byte_size) || input.byte_size < 1 || input.byte_size > 100 * 1024 * 1024
    ) throw new LocalBusinessStoreError("INVALID_INPUT", "投标产物路径或大小无效");
    const artifactPath = resolve(this.project_root, input.relative_path);
    if (!existsSync(artifactPath) || lstatSync(artifactPath).isSymbolicLink() || !lstatSync(artifactPath).isFile()) {
      throw new LocalBusinessStoreError("NOT_FOUND", "待登记的投标产物不存在或不是普通文件");
    }
    const artifactMeta = lstatSync(artifactPath);
    if (artifactMeta.size !== input.byte_size || sha256(readFileSync(artifactPath)) !== input.sha256) {
      throw new LocalBusinessStoreError("CONSTRAINT", "投标产物大小或 SHA-256 与生成回执不一致");
    }
    const artifactId = `artifact-${sha256(Buffer.from(`${input.intent_id}:docx`, "utf8")).slice(0, 24)}`;
    const at = new Date().toISOString();
    let began = false;
    try {
      this.database.exec("BEGIN IMMEDIATE");
      began = true;
      const existing = this.database.prepare("SELECT * FROM bid_artifacts WHERE artifact_id=?").get(artifactId) as Record<string, unknown> | undefined;
      if (existing) {
        if (
          existing.bid_id !== input.bid_id || existing.relative_path !== input.relative_path ||
          existing.sha256 !== input.sha256 || existing.intent_id !== input.intent_id || existing.task_id !== input.task_id
        ) throw new LocalBusinessStoreError("INTENT_PAYLOAD_CONFLICT", "同一文件生成意图已登记为不同产物");
        this.database.exec("COMMIT");
        began = false;
        return existing;
      }
      if (!this.database.prepare("SELECT 1 FROM bid_projects WHERE bid_id=? AND deleted_at IS NULL").get(input.bid_id)) {
        throw new LocalBusinessStoreError("NOT_FOUND", "投标项目不存在");
      }
      this.database.prepare(`
        INSERT INTO bid_artifacts(
          artifact_id,bid_id,artifact_type,relative_path,sha256,byte_size,task_id,intent_id,status,qa_json,
          version,created_at,updated_at
        ) VALUES (?,?, 'final_docx',?,?,?,?,?,'ready',?,1,?,?)
      `).run(
        artifactId, input.bid_id, input.relative_path, input.sha256, input.byte_size,
        input.task_id, input.intent_id, JSON.stringify(input.qa), at, at,
      );
      this.database.prepare(
        "INSERT INTO bid_events(event_id,bid_id,event_type,title,detail_json,actor,task_id,created_at) VALUES (?,?,?,?,?,'system',?,?)",
      ).run(
        `event-${sha256(Buffer.from(`${input.intent_id}:artifact-event`, "utf8")).slice(0, 24)}`,
        input.bid_id,
        "artifact_ready",
        "正式投标文件已生成并通过检查",
        JSON.stringify({ artifact_id: artifactId, path: input.relative_path, sha256: input.sha256 }),
        input.task_id,
        at,
      );
      this.database.exec("COMMIT");
      began = false;
      return this.database.prepare("SELECT * FROM bid_artifacts WHERE artifact_id=?").get(artifactId) as Record<string, unknown>;
    } catch (error) {
      if (began) try { this.database.exec("ROLLBACK"); } catch { /* close performs final rollback */ }
      mapSqlError(error);
    }
  }

  commit(request: BidCommitRequest): BidCommitResult {
    if (this.read_only) throw new LocalBusinessStoreError("READ_ONLY", "只读连接不能写入招投标数据库");
    assertText(request.intent_id, "intent_id", 128);
    assertText(request.task_id, "task_id", 128);
    assertText(request.session_id, "session_id", 128);
    assertSafeId(request.bid_id, "bid_id");
    assertSha(request.approved_payload_sha256, "approved_payload_sha256");
    if (!Array.isArray(request.mutations) || request.mutations.length < 1 || request.mutations.length > BID_MAX_MUTATIONS) {
      throw new LocalBusinessStoreError("INVALID_INPUT", `每次投标写入必须包含 1-${BID_MAX_MUTATIONS} 条变更`);
    }
    const prepared = request.mutations.map((mutation) => {
      if (!mutation || typeof mutation !== "object" || !(mutation.table in BID_TABLES)) {
        throw new LocalBusinessStoreError("INVALID_INPUT", "投标变更表无效");
      }
      if (mutation.operation !== "insert" && mutation.operation !== "update") throw new LocalBusinessStoreError("INVALID_INPUT", "投标变更操作无效");
      assertSafeId(mutation.record_id, "record_id");
      const definition = BID_TABLES[mutation.table] as BidTableDefinition;
      if (mutation.operation === "insert" && definition.insertAllowed === false) {
        throw new LocalBusinessStoreError("INVALID_INPUT", `${mutation.table} 只允许更新现有记录`);
      }
      const values = convertChanges(definition, mutation.changes);
      if (mutation.table !== "bid_projects") {
        if (values.bid_id !== request.bid_id) throw new LocalBusinessStoreError("INVALID_INPUT", `${mutation.table} 必须绑定当前 bid_id`);
      } else if (mutation.record_id !== request.bid_id) {
        throw new LocalBusinessStoreError("INVALID_INPUT", "投标项目更新目标与当前 bid_id 不一致");
      }
      if (mutation.operation === "insert") {
        if (mutation.expected_version !== undefined) throw new LocalBusinessStoreError("INVALID_INPUT", "insert 不接受 expected_version");
        for (const field of definition.required) {
          if (!(field in values) || values[field] === null || values[field] === "") {
            throw new LocalBusinessStoreError("INVALID_INPUT", `insert 缺少必填字段 ${mutation.table}.${field}`);
          }
        }
      } else parseVersion(mutation.expected_version);
      return { mutation, definition, values };
    });

    let began = false;
    try {
      this.database.exec("BEGIN IMMEDIATE");
      began = true;
      const previous = this.readReceipt(request.intent_id);
      if (previous) {
        if (previous.payload_sha256 !== request.approved_payload_sha256 || previous.bid_id !== request.bid_id) {
          throw new LocalBusinessStoreError("INTENT_PAYLOAD_CONFLICT", "同一投标写入 intent 不能绑定不同载荷");
        }
        this.database.exec("COMMIT");
        began = false;
        return previous;
      }
      if (!this.database.prepare("SELECT 1 FROM bid_projects WHERE bid_id=? AND deleted_at IS NULL").get(request.bid_id)) {
        throw new LocalBusinessStoreError("NOT_FOUND", "投标项目不存在");
      }
      const committedAt = new Date().toISOString();
      const results: BidCommitResult["mutations"] = [];
      for (const { mutation, definition, values } of prepared) {
        const keys = Object.keys(values).sort();
        if (mutation.operation === "insert") {
          const columns = [definition.id, ...keys, "version", "created_at", "updated_at"];
          const parameters = [mutation.record_id, ...keys.map((key) => values[key]), 1, committedAt, committedAt];
          this.database.prepare(`INSERT INTO ${mutation.table} (${columns.join(",")}) VALUES (${columns.map(() => "?").join(",")})`).run(...parameters);
          results.push({ table: mutation.table, record_id: mutation.record_id, operation: "insert", version: 1 });
        } else {
          const expected = parseVersion(mutation.expected_version);
          const assignments = [...keys.map((key) => `${key}=?`), "version=version+1", "updated_at=?"];
          const changed = this.database.prepare(
            `UPDATE ${mutation.table} SET ${assignments.join(",")} WHERE ${definition.id}=? AND version=? AND deleted_at IS NULL`,
          ).run(...keys.map((key) => values[key]), committedAt, mutation.record_id, expected);
          if (changed.changes !== 1) throw new LocalBusinessStoreError("VERSION_CONFLICT", `${mutation.table}/${mutation.record_id} 已更新，请重新读取`);
          results.push({ table: mutation.table, record_id: mutation.record_id, operation: "update", version: expected + 1 });
        }
      }
      this.database.prepare(
        "INSERT INTO bid_events(event_id,bid_id,event_type,title,detail_json,actor,task_id,created_at) VALUES (?,?,?,?,?,'assistant',?,?)",
      ).run(
        `event-${createHash("sha256").update(`${request.intent_id}:event`, "utf8").digest("hex").slice(0, 24)}`,
        request.bid_id,
        "governed_write",
        "提交已批准的投标项目变更",
        JSON.stringify({ intent_id: request.intent_id, mutation_count: results.length }),
        request.task_id,
        committedAt,
      );
      const receipt: BidCommitResult = {
        intent_id: request.intent_id,
        task_id: request.task_id,
        logical_tool: "bid.write",
        payload_sha256: request.approved_payload_sha256,
        bid_id: request.bid_id,
        mutations: results,
        committed_at: committedAt,
      };
      this.database.prepare(
        "INSERT INTO bid_write_receipts(intent_id,task_id,session_id,logical_tool,payload_sha256,result_json,committed_at) VALUES (?,?,?,?,?,?,?)",
      ).run(request.intent_id, request.task_id, request.session_id, "bid.write", request.approved_payload_sha256, JSON.stringify(receipt), committedAt);
      this.database.exec("COMMIT");
      began = false;
      return receipt;
    } catch (error) {
      if (began) try { this.database.exec("ROLLBACK"); } catch { /* close performs final rollback */ }
      mapSqlError(error);
    }
  }
}

export function openBiddingStore(projectRoot: string, readOnly = false): BiddingStore {
  return new BiddingStore(projectRoot, readOnly);
}
