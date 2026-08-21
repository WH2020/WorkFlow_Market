import { createHash } from "node:crypto";
import { existsSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { backup, DatabaseSync } from "node:sqlite";

export const SQLITE_GATE_SCHEMA_VERSION = 1;
export const SQLITE_GATE_MINIMUM_NODE = "24.15.0";

export type GateFaultPoint = "after_begin" | "after_mutation" | "after_receipt" | "after_commit";

export type GateMutation = {
  intent_id: string;
  payload_sha256: string;
  operation: "insert" | "update";
  record_id: string;
  value: string;
  expected_version?: number;
};

export type GateCommitResult = {
  intent_id: string;
  payload_sha256: string;
  record_id: string;
  operation: "insert" | "update";
  version: number;
  value_sha256: string;
  committed_at: string;
};

export type GateReceipt = {
  intent_id: string;
  payload_sha256: string;
  result: GateCommitResult;
  committed_at: string;
};

export type GateRecord = {
  record_id: string;
  value: string;
  version: number;
  updated_at: string;
};

export type GateConfiguration = {
  sqlite_version: string;
  journal_mode: string;
  synchronous: number;
  foreign_keys: number;
  busy_timeout: number;
  integrity_check: string;
};

export class LocalBusinessStoreError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "LocalBusinessStoreError";
    this.code = code;
  }
}

export type LocalBusinessStoreOptions = {
  timeout_ms?: number;
  read_only?: boolean;
};

function assertBoundedText(value: unknown, label: string, maximum: number): asserts value is string {
  if (typeof value !== "string" || value.length === 0 || value.length > maximum) {
    throw new LocalBusinessStoreError("INVALID_INPUT", `${label} 必须是 1-${maximum} 个字符`);
  }
}

function assertSha256(value: unknown, label: string): asserts value is string {
  if (typeof value !== "string" || !/^[a-f0-9]{64}$/.test(value)) {
    throw new LocalBusinessStoreError("INVALID_INPUT", `${label} 必须是小写 SHA-256`);
  }
}

function assertTimeout(value: number): void {
  if (!Number.isSafeInteger(value) || value < 0 || value > 30_000) {
    throw new LocalBusinessStoreError("INVALID_INPUT", "SQLite timeout 必须在 0-30000 毫秒之间");
  }
}

function firstValue(row: unknown): unknown {
  if (!row || typeof row !== "object" || Array.isArray(row)) return undefined;
  return Object.values(row as Record<string, unknown>)[0];
}

function numberValue(row: unknown, label: string): number {
  const value = firstValue(row);
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new LocalBusinessStoreError("SQLITE_CAPABILITY", `无法读取 ${label}`);
  }
  return value;
}

function stringValue(row: unknown, label: string): string {
  const value = firstValue(row);
  if (typeof value !== "string") {
    throw new LocalBusinessStoreError("SQLITE_CAPABILITY", `无法读取 ${label}`);
  }
  return value;
}

function validateMutation(mutation: GateMutation): void {
  assertBoundedText(mutation.intent_id, "intent_id", 128);
  assertSha256(mutation.payload_sha256, "payload_sha256");
  assertBoundedText(mutation.record_id, "record_id", 128);
  assertBoundedText(mutation.value, "value", 64 * 1024);
  if (mutation.operation !== "insert" && mutation.operation !== "update") {
    throw new LocalBusinessStoreError("INVALID_INPUT", "operation 只能是 insert 或 update");
  }
  if (mutation.operation === "insert" && mutation.expected_version !== undefined) {
    throw new LocalBusinessStoreError("INVALID_INPUT", "insert 不接受 expected_version");
  }
  if (
    mutation.operation === "update"
    && (!Number.isSafeInteger(mutation.expected_version) || (mutation.expected_version ?? 0) < 1)
  ) {
    throw new LocalBusinessStoreError("INVALID_INPUT", "update 必须提供正整数 expected_version");
  }
}

function parseCommitResult(value: string): GateCommitResult {
  let result: unknown;
  try {
    result = JSON.parse(value);
  } catch {
    throw new LocalBusinessStoreError("RECEIPT_CORRUPT", "已提交回执不是有效 JSON");
  }
  if (!result || typeof result !== "object" || Array.isArray(result)) {
    throw new LocalBusinessStoreError("RECEIPT_CORRUPT", "已提交回执结构无效");
  }
  const candidate = result as Partial<GateCommitResult>;
  assertBoundedText(candidate.intent_id, "receipt.intent_id", 128);
  assertSha256(candidate.payload_sha256, "receipt.payload_sha256");
  assertBoundedText(candidate.record_id, "receipt.record_id", 128);
  assertSha256(candidate.value_sha256, "receipt.value_sha256");
  assertBoundedText(candidate.committed_at, "receipt.committed_at", 64);
  if (candidate.operation !== "insert" && candidate.operation !== "update") {
    throw new LocalBusinessStoreError("RECEIPT_CORRUPT", "回执 operation 无效");
  }
  if (!Number.isSafeInteger(candidate.version) || (candidate.version ?? 0) < 1) {
    throw new LocalBusinessStoreError("RECEIPT_CORRUPT", "回执 version 无效");
  }
  return candidate as GateCommitResult;
}

export function gatePayloadSha256(input: Omit<GateMutation, "payload_sha256">): string {
  const canonical = JSON.stringify({
    expected_version: input.expected_version ?? null,
    intent_id: input.intent_id,
    operation: input.operation,
    record_id: input.record_id,
    value: input.value,
  });
  return createHash("sha256").update(canonical, "utf8").digest("hex");
}

export class LocalBusinessStore {
  readonly database_path: string;
  readonly timeout_ms: number;
  readonly read_only: boolean;
  private readonly database: DatabaseSync;
  private closed = false;

  constructor(databasePath: string, options: LocalBusinessStoreOptions = {}) {
    assertBoundedText(databasePath, "databasePath", 32 * 1024);
    const timeout = options.timeout_ms ?? 5_000;
    assertTimeout(timeout);
    this.database_path = resolve(databasePath);
    this.timeout_ms = timeout;
    this.read_only = options.read_only ?? false;
    if (!this.read_only) mkdirSync(dirname(this.database_path), { recursive: true });
    this.database = new DatabaseSync(this.database_path, {
      allowExtension: false,
      defensive: true,
      enableDoubleQuotedStringLiterals: false,
      enableForeignKeyConstraints: true,
      readBigInts: false,
      readOnly: this.read_only,
      returnArrays: false,
      timeout,
    });
    this.database.exec(`PRAGMA busy_timeout = ${timeout}`);
    this.database.exec("PRAGMA foreign_keys = ON");
    if (!this.read_only) {
      this.database.exec("PRAGMA journal_mode = WAL");
      this.database.exec("PRAGMA synchronous = FULL");
      this.initializeSchema();
    } else {
      this.database.exec("PRAGMA query_only = ON");
    }
  }

  private initializeSchema(): void {
    this.database.exec(`
      CREATE TABLE IF NOT EXISTS gate_schema_migrations (
        version INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        applied_at TEXT NOT NULL
      ) STRICT;
      CREATE TABLE IF NOT EXISTS gate_records (
        record_id TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        version INTEGER NOT NULL CHECK (version >= 1),
        updated_at TEXT NOT NULL
      ) STRICT;
      CREATE TABLE IF NOT EXISTS gate_write_receipts (
        intent_id TEXT PRIMARY KEY,
        payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
        result_json TEXT NOT NULL CHECK (json_valid(result_json)),
        committed_at TEXT NOT NULL
      ) STRICT;
      INSERT INTO gate_schema_migrations(version, name, applied_at)
      VALUES (${SQLITE_GATE_SCHEMA_VERSION}, 'sqlite-driver-gate-v1', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
      ON CONFLICT(version) DO NOTHING;
    `);
  }

  close(): void {
    if (this.closed) return;
    this.database.close();
    this.closed = true;
  }

  configuration(): GateConfiguration {
    return {
      sqlite_version: stringValue(this.database.prepare("SELECT sqlite_version()").get(), "sqlite_version"),
      journal_mode: stringValue(this.database.prepare("PRAGMA journal_mode").get(), "journal_mode"),
      synchronous: numberValue(this.database.prepare("PRAGMA synchronous").get(), "synchronous"),
      foreign_keys: numberValue(this.database.prepare("PRAGMA foreign_keys").get(), "foreign_keys"),
      busy_timeout: numberValue(this.database.prepare("PRAGMA busy_timeout").get(), "busy_timeout"),
      integrity_check: stringValue(this.database.prepare("PRAGMA integrity_check").get(), "integrity_check"),
    };
  }

  readRecord(recordId: string): GateRecord | undefined {
    assertBoundedText(recordId, "record_id", 128);
    const row = this.database.prepare(
      "SELECT record_id, value, version, updated_at FROM gate_records WHERE record_id = ?",
    ).get(recordId) as GateRecord | undefined;
    return row;
  }

  readReceipt(intentId: string): GateReceipt | undefined {
    assertBoundedText(intentId, "intent_id", 128);
    const row = this.database.prepare(
      "SELECT intent_id, payload_sha256, result_json, committed_at FROM gate_write_receipts WHERE intent_id = ?",
    ).get(intentId) as { intent_id: string; payload_sha256: string; result_json: string; committed_at: string } | undefined;
    if (!row) return undefined;
    return {
      intent_id: row.intent_id,
      payload_sha256: row.payload_sha256,
      result: parseCommitResult(row.result_json),
      committed_at: row.committed_at,
    };
  }

  counts(): { records: number; receipts: number } {
    return {
      records: numberValue(this.database.prepare("SELECT count(*) FROM gate_records").get(), "record count"),
      receipts: numberValue(this.database.prepare("SELECT count(*) FROM gate_write_receipts").get(), "receipt count"),
    };
  }

  commitMutation(
    mutation: GateMutation,
    faultInjector?: (point: GateFaultPoint) => void,
  ): GateCommitResult {
    if (this.read_only) throw new LocalBusinessStoreError("READ_ONLY", "只读连接不能写入");
    validateMutation(mutation);
    let began = false;
    let committed = false;
    try {
      this.database.exec("BEGIN IMMEDIATE");
      began = true;
      faultInjector?.("after_begin");

      const receipt = this.readReceipt(mutation.intent_id);
      if (receipt) {
        if (receipt.payload_sha256 !== mutation.payload_sha256) {
          throw new LocalBusinessStoreError("INTENT_PAYLOAD_CONFLICT", "同一 intent 不能绑定不同 payload");
        }
        this.database.exec("COMMIT");
        committed = true;
        return receipt.result;
      }

      const existing = this.readRecord(mutation.record_id);
      const committedAt = new Date().toISOString();
      let version: number;
      if (mutation.operation === "insert") {
        if (existing) {
          throw new LocalBusinessStoreError("RECORD_EXISTS", `记录已存在: ${mutation.record_id}`);
        }
        version = 1;
        this.database.prepare(
          "INSERT INTO gate_records(record_id, value, version, updated_at) VALUES (?, ?, ?, ?)",
        ).run(mutation.record_id, mutation.value, version, committedAt);
      } else {
        if (!existing || existing.version !== mutation.expected_version) {
          throw new LocalBusinessStoreError(
            "VERSION_CONFLICT",
            `记录版本冲突: expected=${mutation.expected_version}, actual=${existing?.version ?? "missing"}`,
          );
        }
        version = existing.version + 1;
        const result = this.database.prepare(
          "UPDATE gate_records SET value = ?, version = ?, updated_at = ? WHERE record_id = ? AND version = ?",
        ).run(mutation.value, version, committedAt, mutation.record_id, mutation.expected_version!);
        if (result.changes !== 1) {
          throw new LocalBusinessStoreError("VERSION_CONFLICT", "记录在事务内发生版本冲突");
        }
      }

      faultInjector?.("after_mutation");
      const result: GateCommitResult = {
        intent_id: mutation.intent_id,
        payload_sha256: mutation.payload_sha256,
        record_id: mutation.record_id,
        operation: mutation.operation,
        version,
        value_sha256: createHash("sha256").update(mutation.value, "utf8").digest("hex"),
        committed_at: committedAt,
      };
      this.database.prepare(
        "INSERT INTO gate_write_receipts(intent_id, payload_sha256, result_json, committed_at) VALUES (?, ?, ?, ?)",
      ).run(mutation.intent_id, mutation.payload_sha256, JSON.stringify(result), committedAt);
      faultInjector?.("after_receipt");
      this.database.exec("COMMIT");
      committed = true;
      faultInjector?.("after_commit");
      return result;
    } catch (error) {
      if (began && !committed) {
        try {
          this.database.exec("ROLLBACK");
        } catch {
          // A killed process cannot run this branch; SQLite rolls the transaction back on connection close.
        }
      }
      throw error;
    }
  }

  async backupTo(targetPath: string): Promise<number> {
    if (this.read_only) throw new LocalBusinessStoreError("READ_ONLY", "只读连接不能创建受管备份");
    assertBoundedText(targetPath, "targetPath", 32 * 1024);
    const target = resolve(targetPath);
    if (target === this.database_path) {
      throw new LocalBusinessStoreError("INVALID_INPUT", "备份路径不能与源数据库相同");
    }
    if (existsSync(target)) {
      throw new LocalBusinessStoreError("TARGET_EXISTS", "备份目标已存在，拒绝覆盖");
    }
    mkdirSync(dirname(target), { recursive: true });
    return backup(this.database, target);
  }
}
