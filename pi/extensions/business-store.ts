import { createHash } from "node:crypto";
import { closeSync, existsSync, lstatSync, mkdirSync, openSync, readFileSync, rmSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { backup, DatabaseSync } from "node:sqlite";

import { LocalBusinessStoreError } from "./local-business-store.ts";

export const BUSINESS_SCHEMA_VERSION = 1;
export const BUSINESS_STORE_APPLICATION_VERSION = "0.13.6";
export const BUSINESS_STORE_MAX_MUTATIONS = 100;

type SqlValue = string | number | null;

type TableDefinition = {
  id: string;
  fields: readonly string[];
  required: readonly string[];
};

const TABLES = {
  accounts: {
    id: "account_id",
    fields: ["name", "normalized_name", "region", "sector", "owner", "lifecycle_stage", "health", "budget_path", "summary", "project_id", "deleted_at"],
    required: ["name", "normalized_name"],
  },
  contacts: {
    id: "contact_id",
    fields: ["display_name", "organization", "title", "email", "phone", "identity_status", "deleted_at"],
    required: ["display_name", "identity_status"],
  },
  account_contacts: {
    id: "account_contact_id",
    fields: ["account_id", "contact_id", "role", "influence_level", "decision_role", "relationship_status", "is_primary", "deleted_at"],
    required: ["account_id", "contact_id"],
  },
  opportunities: {
    id: "opportunity_id",
    fields: ["account_id", "name", "owner", "stage", "health", "amount_min_minor", "amount_max_minor", "currency", "expected_decision_at", "win_hypothesis", "loss_reason", "next_stage_condition", "deleted_at"],
    required: ["account_id", "name"],
  },
  sources: {
    id: "source_id",
    fields: ["title", "url", "publisher", "published_date", "accessed_date", "region", "topic", "source_type", "quality", "exposure_status", "status", "limitations", "notes", "legacy_key_facts", "legacy_important_quotes", "legacy_interpretation", "file_path", "file_sha256", "deleted_at"],
    required: ["title", "status"],
  },
  activities: {
    id: "activity_id",
    fields: ["account_id", "opportunity_id", "salesperson_id", "occurred_at", "channel", "activity_type", "summary", "participants_text", "source_id", "source_path", "source_sha256", "evidence_status", "deleted_at"],
    required: ["account_id", "occurred_at", "summary"],
  },
  commitments: {
    id: "commitment_id",
    fields: ["account_id", "opportunity_id", "source_activity_id", "direction", "commitment_text", "due_at", "status", "deleted_at"],
    required: ["account_id", "direction", "commitment_text", "status"],
  },
  risks: {
    id: "risk_id",
    fields: ["account_id", "opportunity_id", "risk_text", "category", "impact", "likelihood", "status", "owner", "mitigation_action", "source_id", "deleted_at"],
    required: ["account_id", "risk_text"],
  },
  signals: {
    id: "signal_id",
    fields: ["account_id", "signal_type", "subject_type", "subject_id", "rule_version", "trigger_json", "evidence_version_hash", "fingerprint", "severity", "status", "first_seen_at", "last_seen_at", "resolved_at", "deleted_at"],
    required: ["signal_type", "subject_type", "subject_id", "rule_version", "trigger_json", "evidence_version_hash", "fingerprint", "severity", "status", "first_seen_at", "last_seen_at"],
  },
  actions: {
    id: "action_id",
    fields: ["account_id", "opportunity_id", "source_activity_id", "source_signal_id", "source_task_id", "action_text", "owner", "due_at", "priority", "status", "origin", "completion_evidence", "completed_at", "deleted_at"],
    required: ["account_id", "action_text", "origin"],
  },
  resource_requests: {
    id: "request_id",
    fields: ["account_id", "opportunity_id", "action_id", "salesperson_id", "requested_at", "resource_type", "request_summary", "business_reason", "deadline", "owner", "status", "decision", "decision_reason", "approval_receipt_id", "decided_at", "deleted_at"],
    required: ["account_id", "requested_at", "request_summary"],
  },
  sales_assets: {
    id: "asset_id",
    fields: ["asset_type", "title", "scope", "account_id", "opportunity_id", "audience_role", "sales_stage", "use_case", "owner", "status", "authorization_status", "deidentification_status", "source_path", "source_sha256", "source_status", "legacy_evidence_refs", "last_validated_at", "next_review_at", "usage_feedback", "deleted_at"],
    required: ["asset_type", "title", "scope"],
  },
  evidence_refs: {
    id: "evidence_ref_id",
    fields: ["entity_type", "entity_id", "field_name", "source_id", "locator_json", "claim_kind", "verification_status", "note", "superseded_at", "deleted_at"],
    required: ["entity_type", "entity_id", "source_id", "locator_json", "claim_kind", "verification_status"],
  },
  action_suggestions: {
    id: "suggestion_id",
    fields: ["account_id", "signal_id", "suggestion_text", "suggested_owner", "suggested_due_at", "model_id", "model_parameters_json", "status", "user_feedback", "accepted_action_id", "deleted_at"],
    required: ["account_id", "suggestion_text", "status"],
  },
  plays: {
    id: "play_id",
    fields: ["name", "applicable_scenarios", "enabled", "deleted_at"],
    required: ["name"],
  },
  play_versions: {
    id: "play_version_id",
    fields: ["play_id", "version_label", "definition_json", "definition_sha256", "deleted_at"],
    required: ["play_id", "version_label", "definition_json", "definition_sha256"],
  },
  play_runs: {
    id: "play_run_id",
    fields: ["play_version_id", "account_id", "project_id", "input_summary", "status", "started_at", "finished_at", "deleted_at"],
    required: ["play_version_id", "status", "started_at"],
  },
  task_links: {
    id: "task_link_id",
    fields: ["task_id", "account_id", "opportunity_id", "play_run_id", "project_id", "relation_type", "deleted_at"],
    required: ["task_id", "relation_type"],
  },
  artifacts: {
    id: "artifact_id",
    fields: ["relative_path", "artifact_type", "sha256", "task_id", "account_id", "opportunity_id", "status", "deleted_at"],
    required: ["relative_path", "artifact_type", "sha256", "status"],
  },
} as const satisfies Record<string, TableDefinition>;

export type BusinessTable = keyof typeof TABLES;

export type BusinessMutation = {
  operation: "insert" | "update";
  table: BusinessTable;
  record_id: string;
  values: Record<string, SqlValue>;
  expected_version?: number;
};

export type BusinessCommitInput = {
  intent_id: string;
  task_id: string;
  session_id: string;
  logical_tool: string;
  mutations: BusinessMutation[];
};

export type BusinessCommitRequest = BusinessCommitInput & { payload_sha256: string };

export type BusinessMutationResult = {
  table: BusinessTable;
  record_id: string;
  operation: "insert" | "update";
  version: number;
};

export type BusinessCommitResult = {
  intent_id: string;
  task_id: string;
  logical_tool: string;
  payload_sha256: string;
  mutations: BusinessMutationResult[];
  committed_at: string;
};

export type BusinessStoreOptions = {
  timeout_ms?: number;
  read_only?: boolean;
  create_if_missing?: boolean;
  schema_path?: string;
  manifest_path?: string;
};

function canonicalJson(value: unknown): string {
  if (value === null || typeof value === "boolean" || typeof value === "number" || typeof value === "string") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (typeof value !== "object") throw new LocalBusinessStoreError("INVALID_INPUT", "载荷包含不能序列化的值");
  return `{${Object.entries(value as Record<string, unknown>)
    .sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)
    .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`).join(",")}}`;
}

function sha256(value: string | Buffer): string {
  return createHash("sha256").update(value).digest("hex");
}

function assertText(value: unknown, label: string, maximum = 1024): asserts value is string {
  if (typeof value !== "string" || value.length < 1 || value.length > maximum) {
    throw new LocalBusinessStoreError("INVALID_INPUT", `${label} 必须是 1-${maximum} 个字符`);
  }
}

function assertSha(value: unknown, label: string): asserts value is string {
  if (typeof value !== "string" || !/^[a-f0-9]{64}$/.test(value)) {
    throw new LocalBusinessStoreError("INVALID_INPUT", `${label} 必须是小写 SHA-256`);
  }
}

function numberFromRow(row: unknown, label: string): number {
  if (!row || typeof row !== "object" || Array.isArray(row)) {
    throw new LocalBusinessStoreError("STORE_CORRUPT", `无法读取 ${label}`);
  }
  const value = Object.values(row as Record<string, unknown>)[0];
  if (typeof value !== "number" || !Number.isSafeInteger(value)) {
    throw new LocalBusinessStoreError("STORE_CORRUPT", `${label} 不是安全整数`);
  }
  return value;
}

function stringFromRow(row: unknown, label: string): string {
  if (!row || typeof row !== "object" || Array.isArray(row)) {
    throw new LocalBusinessStoreError("STORE_CORRUPT", `无法读取 ${label}`);
  }
  const value = Object.values(row as Record<string, unknown>)[0];
  if (typeof value !== "string") throw new LocalBusinessStoreError("STORE_CORRUPT", `${label} 不是文本`);
  return value;
}

function defaultSchemaPath(): string {
  return fileURLToPath(new URL("../../agent_platform/migrations/001_sales_core.sql", import.meta.url));
}

function defaultManifestPath(): string {
  return fileURLToPath(new URL("../../agent_platform/migrations/manifest.json", import.meta.url));
}

function readMigration(schemaPath: string, manifestPath: string): { sql: string; hash: string; name: string } {
  const sql = readFileSync(schemaPath, "utf8");
  let manifest: unknown;
  try {
    manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
  } catch (error) {
    throw new LocalBusinessStoreError("SCHEMA_MANIFEST", `无法读取迁移清单: ${String(error)}`);
  }
  const root = manifest as { schema_version?: unknown; application_version?: unknown; migrations?: unknown[] };
  const item = root?.migrations?.[0] as Record<string, unknown> | undefined;
  if (
    root.schema_version !== BUSINESS_SCHEMA_VERSION
    || root.application_version !== BUSINESS_STORE_APPLICATION_VERSION
    || !item
    || item.version !== BUSINESS_SCHEMA_VERSION
    || typeof item.name !== "string"
  ) {
    throw new LocalBusinessStoreError("SCHEMA_MANIFEST", "迁移清单缺少 schema v1");
  }
  assertSha(item.sha256, "migration.sha256");
  const actual = sha256(Buffer.from(sql, "utf8"));
  if (actual !== item.sha256) throw new LocalBusinessStoreError("SCHEMA_HASH_MISMATCH", "schema SQL 与迁移清单哈希不一致");
  return { sql, hash: actual, name: item.name };
}

function validateMutation(mutation: BusinessMutation): TableDefinition {
  const definition = TABLES[mutation.table] as TableDefinition | undefined;
  if (!definition) throw new LocalBusinessStoreError("INVALID_INPUT", `不支持的业务表: ${String(mutation.table)}`);
  assertText(mutation.record_id, "record_id", 128);
  if (mutation.operation !== "insert" && mutation.operation !== "update") {
    throw new LocalBusinessStoreError("INVALID_INPUT", "operation 只能是 insert 或 update");
  }
  if (!mutation.values || typeof mutation.values !== "object" || Array.isArray(mutation.values)) {
    throw new LocalBusinessStoreError("INVALID_INPUT", "values 必须是字段对象");
  }
  const keys = Object.keys(mutation.values);
  if (keys.length === 0 || keys.length > definition.fields.length) {
    throw new LocalBusinessStoreError("INVALID_INPUT", "mutation 必须包含至少一个受控字段");
  }
  for (const key of keys) {
    if (!definition.fields.includes(key)) throw new LocalBusinessStoreError("INVALID_FIELD", `${mutation.table}.${key} 不允许写入`);
    const value = mutation.values[key];
    if (value !== null && typeof value !== "string" && typeof value !== "number") {
      throw new LocalBusinessStoreError("INVALID_INPUT", `${mutation.table}.${key} 只能是文本、数字或 null`);
    }
    if (typeof value === "string" && value.length > 1024 * 1024) {
      throw new LocalBusinessStoreError("INVALID_INPUT", `${mutation.table}.${key} 超过 1 MiB`);
    }
    if (typeof value === "number" && !Number.isSafeInteger(value)) {
      throw new LocalBusinessStoreError("INVALID_INPUT", `${mutation.table}.${key} 必须是安全整数`);
    }
  }
  if (mutation.operation === "insert") {
    if (mutation.expected_version !== undefined) throw new LocalBusinessStoreError("INVALID_INPUT", "insert 不接受 expected_version");
    for (const field of definition.required) {
      if (!(field in mutation.values) || mutation.values[field] === null || mutation.values[field] === "") {
        throw new LocalBusinessStoreError("INVALID_INPUT", `insert 缺少必填字段 ${mutation.table}.${field}`);
      }
    }
  } else if (!Number.isSafeInteger(mutation.expected_version) || (mutation.expected_version ?? 0) < 1) {
    throw new LocalBusinessStoreError("INVALID_INPUT", "update 必须提供正整数 expected_version");
  }
  return definition;
}

function mapSqlError(error: unknown): never {
  if (error instanceof LocalBusinessStoreError) throw error;
  const candidate = error as { code?: string; message?: string; errcode?: number; errstr?: string };
  const primaryCode = typeof candidate?.errcode === "number" ? candidate.errcode & 0xff : undefined;
  if (candidate?.code === "ERR_SQLITE_ERROR" && (primaryCode === 5 || primaryCode === 6 || /locked|busy/i.test(candidate.message ?? ""))) {
    throw new LocalBusinessStoreError("STORE_BUSY", "数据库正在被另一个短事务占用，请稍后重试");
  }
  if (candidate?.code === "ERR_SQLITE_CONSTRAINT" || primaryCode === 19 || /constraint/i.test(candidate.errstr ?? "")) {
    throw new LocalBusinessStoreError("CONSTRAINT", `业务约束未通过: ${candidate.message ?? "SQLite constraint"}`);
  }
  if (primaryCode === 8) throw new LocalBusinessStoreError("READ_ONLY", "SQLite 拒绝写入只读数据库");
  if (primaryCode === 13) throw new LocalBusinessStoreError("STORE_FULL", "数据库所在磁盘空间不足");
  if (primaryCode === 11 || primaryCode === 26) throw new LocalBusinessStoreError("STORE_CORRUPT", "数据库文件损坏或格式无效");
  throw new LocalBusinessStoreError("SQLITE_ERROR", candidate?.message ?? String(error));
}

export function businessPayloadSha256(input: BusinessCommitInput): string {
  return sha256(Buffer.from(canonicalJson(input), "utf8"));
}

export class SalesBusinessStore {
  readonly database_path: string;
  readonly read_only: boolean;
  readonly timeout_ms: number;
  private readonly database: DatabaseSync;
  private closed = false;

  constructor(databasePath: string, options: BusinessStoreOptions = {}) {
    assertText(databasePath, "databasePath", 32 * 1024);
    const timeout = options.timeout_ms ?? 5_000;
    if (!Number.isSafeInteger(timeout) || timeout < 0 || timeout > 30_000) {
      throw new LocalBusinessStoreError("INVALID_INPUT", "SQLite timeout 必须在 0-30000 毫秒之间");
    }
    this.database_path = resolve(databasePath);
    this.read_only = options.read_only ?? false;
    this.timeout_ms = timeout;
    const existed = existsSync(this.database_path);
    if (existed && lstatSync(this.database_path).isSymbolicLink()) {
      throw new LocalBusinessStoreError("UNSAFE_PATH", "数据库文件不能是符号链接");
    }
    if (!existed && (this.read_only || !options.create_if_missing)) {
      throw new LocalBusinessStoreError("STORE_MISSING", "业务数据库不存在；拒绝隐式创建");
    }
    let ownsNewFile = false;
    if (!existed) {
      mkdirSync(dirname(this.database_path), { recursive: true });
      try {
        const descriptor = openSync(this.database_path, "wx", 0o600);
        closeSync(descriptor);
        ownsNewFile = true;
      } catch (error) {
        const candidate = error as { code?: string };
        if (candidate.code === "EEXIST") throw new LocalBusinessStoreError("TARGET_EXISTS", "业务数据库刚被另一个进程创建");
        throw error;
      }
    }
    let opened: DatabaseSync;
    try {
      opened = new DatabaseSync(this.database_path, {
        allowExtension: false,
        defensive: true,
        enableDoubleQuotedStringLiterals: false,
        enableForeignKeyConstraints: true,
        readBigInts: false,
        readOnly: this.read_only,
        returnArrays: false,
        timeout,
      });
    } catch (error) {
      if (ownsNewFile) rmSync(this.database_path, { force: true });
      throw error;
    }
    this.database = opened;
    try {
      this.database.exec(`PRAGMA busy_timeout = ${timeout}`);
      this.database.exec("PRAGMA foreign_keys = ON");
      if (this.read_only) {
        this.database.exec("PRAGMA query_only = ON");
      } else {
        this.database.exec("PRAGMA journal_mode = WAL");
        this.database.exec("PRAGMA synchronous = FULL");
      }
      if (!existed) {
        const migration = readMigration(
          resolve(options.schema_path ?? defaultSchemaPath()),
          resolve(options.manifest_path ?? defaultManifestPath()),
        );
        this.database.exec("BEGIN IMMEDIATE");
        this.database.exec(migration.sql);
        const appliedAt = new Date().toISOString();
        this.database.prepare(
          "INSERT INTO schema_migrations(version, name, script_sha256, applied_at, application_version, result) VALUES (?, ?, ?, ?, ?, 'applied')",
        ).run(BUSINESS_SCHEMA_VERSION, migration.name, migration.hash, appliedAt, BUSINESS_STORE_APPLICATION_VERSION);
        this.database.prepare("INSERT INTO store_metadata(key, value, updated_at) VALUES ('schema_version', ?, ?)")
          .run(String(BUSINESS_SCHEMA_VERSION), appliedAt);
        this.database.exec("COMMIT");
      }
      this.assertSchema();
    } catch (error) {
      try { this.database.exec("ROLLBACK"); } catch { /* there may be no active transaction */ }
      this.close();
      if (ownsNewFile) {
        for (const suffix of ["", "-wal", "-shm"]) rmSync(`${this.database_path}${suffix}`, { force: true });
      }
      throw error;
    }
  }

  private assertSchema(): void {
    try {
      const version = numberFromRow(this.database.prepare("SELECT max(version) FROM schema_migrations").get(), "schema version");
      if (version !== BUSINESS_SCHEMA_VERSION) {
        throw new LocalBusinessStoreError("SCHEMA_UNSUPPORTED", `只支持 schema v${BUSINESS_SCHEMA_VERSION}，当前为 v${version}`);
      }
      const integrity = stringFromRow(this.database.prepare("PRAGMA quick_check").get(), "quick_check");
      if (integrity !== "ok") throw new LocalBusinessStoreError("STORE_CORRUPT", `数据库快速检查失败: ${integrity}`);
    } catch (error) {
      mapSqlError(error);
    }
  }

  close(): void {
    if (this.closed) return;
    this.database.close();
    this.closed = true;
  }

  configuration(): Record<string, string | number> {
    return {
      sqlite_version: stringFromRow(this.database.prepare("SELECT sqlite_version()").get(), "sqlite_version"),
      schema_version: numberFromRow(this.database.prepare("SELECT max(version) FROM schema_migrations").get(), "schema_version"),
      journal_mode: stringFromRow(this.database.prepare("PRAGMA journal_mode").get(), "journal_mode"),
      synchronous: numberFromRow(this.database.prepare("PRAGMA synchronous").get(), "synchronous"),
      foreign_keys: numberFromRow(this.database.prepare("PRAGMA foreign_keys").get(), "foreign_keys"),
      busy_timeout: numberFromRow(this.database.prepare("PRAGMA busy_timeout").get(), "busy_timeout"),
      integrity_check: stringFromRow(this.database.prepare("PRAGMA integrity_check").get(), "integrity_check"),
    };
  }

  readAccount(accountId: string): Record<string, unknown> | undefined {
    assertText(accountId, "account_id", 128);
    return this.database.prepare(
      "SELECT account_id, name, normalized_name, region, sector, owner, lifecycle_stage, health, budget_path, summary, project_id, version, created_at, updated_at, deleted_at FROM accounts WHERE account_id = ?",
    ).get(accountId) as Record<string, unknown> | undefined;
  }

  searchAccounts(query: string, limit = 20): Record<string, unknown>[] {
    if (typeof query !== "string" || query.length > 500) throw new LocalBusinessStoreError("INVALID_INPUT", "客户查询最多 500 字符");
    if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100) throw new LocalBusinessStoreError("INVALID_INPUT", "limit 必须为 1-100");
    const normalized = query.normalize("NFKC").trim().replace(/\s+/g, " ").toLowerCase();
    const escaped = normalized.replaceAll("\\", "\\\\").replaceAll("%", "\\%").replaceAll("_", "\\_");
    return this.database.prepare(
      "SELECT account_id, name, region, sector, owner, lifecycle_stage, health, version, updated_at FROM accounts WHERE deleted_at IS NULL AND (? = '' OR normalized_name LIKE ? ESCAPE '\\') ORDER BY updated_at DESC, account_id LIMIT ?",
    ).all(normalized, `%${escaped}%`, limit) as Record<string, unknown>[];
  }

  readReceipt(intentId: string): BusinessCommitResult | undefined {
    assertText(intentId, "intent_id", 128);
    const row = this.database.prepare(
      "SELECT payload_sha256, result_json FROM write_receipts WHERE intent_id = ? AND status = 'committed'",
    ).get(intentId) as { payload_sha256: string; result_json: string } | undefined;
    if (!row) return undefined;
    try {
      const result = JSON.parse(row.result_json) as BusinessCommitResult;
      if (result.intent_id !== intentId || result.payload_sha256 !== row.payload_sha256 || !Array.isArray(result.mutations)) {
        throw new Error("receipt fields do not match");
      }
      return result;
    } catch (error) {
      throw new LocalBusinessStoreError("RECEIPT_CORRUPT", `已提交回执损坏: ${String(error)}`);
    }
  }

  commit(request: BusinessCommitRequest): BusinessCommitResult {
    if (this.read_only) throw new LocalBusinessStoreError("READ_ONLY", "只读连接不能写入");
    assertText(request.intent_id, "intent_id", 128);
    assertText(request.task_id, "task_id", 128);
    assertText(request.session_id, "session_id", 128);
    assertText(request.logical_tool, "logical_tool", 128);
    assertSha(request.payload_sha256, "payload_sha256");
    if (!Array.isArray(request.mutations) || request.mutations.length < 1 || request.mutations.length > BUSINESS_STORE_MAX_MUTATIONS) {
      throw new LocalBusinessStoreError("INVALID_INPUT", `每个 intent 必须包含 1-${BUSINESS_STORE_MAX_MUTATIONS} 个 mutation`);
    }
    const input: BusinessCommitInput = {
      intent_id: request.intent_id,
      task_id: request.task_id,
      session_id: request.session_id,
      logical_tool: request.logical_tool,
      mutations: request.mutations,
    };
    if (businessPayloadSha256(input) !== request.payload_sha256) {
      throw new LocalBusinessStoreError("PAYLOAD_HASH_MISMATCH", "payload_sha256 与规范化 mutation 不一致");
    }
    const definitions = request.mutations.map(validateMutation);
    let began = false;
    try {
      this.database.exec("BEGIN IMMEDIATE");
      began = true;
      const receipt = this.readReceipt(request.intent_id);
      if (receipt) {
        if (receipt.payload_sha256 !== request.payload_sha256) {
          throw new LocalBusinessStoreError("INTENT_PAYLOAD_CONFLICT", "同一 intent 不能绑定不同 payload");
        }
        this.database.exec("COMMIT");
        began = false;
        return receipt;
      }
      const committedAt = new Date().toISOString();
      const results: BusinessMutationResult[] = [];
      for (let index = 0; index < request.mutations.length; index += 1) {
        const mutation = request.mutations[index];
        const definition = definitions[index];
        const keys = Object.keys(mutation.values).sort();
        if (mutation.operation === "insert") {
          const columns = [definition.id, ...keys, "version", "created_at", "updated_at"];
          const placeholders = columns.map(() => "?").join(", ");
          const values = [mutation.record_id, ...keys.map((key) => mutation.values[key]), 1, committedAt, committedAt];
          this.database.prepare(`INSERT INTO ${mutation.table} (${columns.join(", ")}) VALUES (${placeholders})`).run(...values);
          results.push({ table: mutation.table, record_id: mutation.record_id, operation: "insert", version: 1 });
        } else {
          const assignments = [...keys.map((key) => `${key} = ?`), "version = version + 1", "updated_at = ?"];
          const result = this.database.prepare(
            `UPDATE ${mutation.table} SET ${assignments.join(", ")} WHERE ${definition.id} = ? AND version = ?`,
          ).run(...keys.map((key) => mutation.values[key]), committedAt, mutation.record_id, mutation.expected_version!);
          if (result.changes !== 1) {
            throw new LocalBusinessStoreError("VERSION_CONFLICT", `${mutation.table}/${mutation.record_id} 版本冲突或记录不存在`);
          }
          results.push({
            table: mutation.table,
            record_id: mutation.record_id,
            operation: "update",
            version: mutation.expected_version! + 1,
          });
        }
      }
      const committed: BusinessCommitResult = {
        intent_id: request.intent_id,
        task_id: request.task_id,
        logical_tool: request.logical_tool,
        payload_sha256: request.payload_sha256,
        mutations: results,
        committed_at: committedAt,
      };
      this.database.prepare(
        "INSERT INTO write_receipts(intent_id, task_id, session_id, logical_tool, payload_sha256, status, result_json, committed_at) VALUES (?, ?, ?, ?, ?, 'committed', ?, ?)",
      ).run(request.intent_id, request.task_id, request.session_id, request.logical_tool, request.payload_sha256, JSON.stringify(committed), committedAt);
      this.database.exec("COMMIT");
      began = false;
      return committed;
    } catch (error) {
      if (began) {
        try { this.database.exec("ROLLBACK"); } catch { /* connection close performs final rollback */ }
      }
      mapSqlError(error);
    }
  }

  foreignKeyViolations(): Record<string, unknown>[] {
    return this.database.prepare("PRAGMA foreign_key_check").all() as Record<string, unknown>[];
  }

  tableCount(table: BusinessTable): number {
    if (!(table in TABLES)) throw new LocalBusinessStoreError("INVALID_INPUT", "未知业务表");
    return numberFromRow(this.database.prepare(`SELECT count(*) FROM ${table}`).get(), `${table} count`);
  }

  async backupTo(targetPath: string): Promise<number> {
    if (this.read_only) throw new LocalBusinessStoreError("READ_ONLY", "只读连接不能创建备份");
    assertText(targetPath, "targetPath", 32 * 1024);
    const target = resolve(targetPath);
    if (target === this.database_path) throw new LocalBusinessStoreError("INVALID_INPUT", "备份路径不能等于数据库路径");
    if (existsSync(target)) throw new LocalBusinessStoreError("TARGET_EXISTS", "备份目标已存在，拒绝覆盖");
    mkdirSync(dirname(target), { recursive: true });
    return backup(this.database, target);
  }
}
