import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import {
  closeSync,
  existsSync,
  fsyncSync,
  lstatSync,
  mkdirSync,
  openSync,
  readFileSync,
  realpathSync,
  renameSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { createHash, randomUUID } from "node:crypto";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import { Type } from "typebox";

export type AdapterHooks = {
  projectRoot: () => string;
  beforeLogicalTool: (logicalTool: string, params: unknown) => { intent_id?: string; payload_sha256?: string } | void;
  afterLogicalTool: (logicalTool: string, params: unknown, details: unknown) => void;
  onLogicalToolError: (
    logicalTool: "knowledge.write" | "sales.write",
    params: unknown,
    outcome: "not_committed" | "ambiguous",
    error: unknown,
  ) => void;
};

type TableDefinition = {
  file: string;
  key: string;
  columns: string[];
  requiredOnInsert: string[];
  timestamp?: string;
};

type CsvTable = {
  headers: string[];
  rows: Record<string, string>[];
};

const MAX_CSV_BYTES = 16 * 1024 * 1024;
const MAX_TOOL_RESULT_BYTES = 2 * 1024 * 1024;
const MAX_WEB_RESPONSE_BYTES = 2 * 1024 * 1024;
const DEFAULT_LIMIT = 20;
const MAX_LIMIT = 100;

const SALES_TABLES = {
  customers: {
    file: "customers.csv",
    key: "customer_id",
    columns: [
      "customer_id", "customer_name", "region", "sector", "owner", "stage", "health",
      "key_contact", "decision_maker", "budget_path", "next_action", "next_action_due",
      "last_evidence_date", "risks", "updated_at",
    ],
    requiredOnInsert: ["customer_id", "customer_name"],
    timestamp: "updated_at",
  },
  activities: {
    file: "activities.csv",
    key: "activity_id",
    columns: [
      "activity_id", "customer_id", "salesperson_id", "occurred_at", "channel", "activity_type",
      "summary", "evidence_path", "commitment", "next_action", "next_action_due", "created_at",
    ],
    requiredOnInsert: ["activity_id", "customer_id", "occurred_at", "summary"],
    timestamp: "created_at",
  },
  resource_requests: {
    file: "resource-requests.csv",
    key: "request_id",
    columns: [
      "request_id", "customer_id", "salesperson_id", "requested_at", "resource_type",
      "request_summary", "business_reason", "deadline", "owner", "status", "decision",
      "decision_reason", "updated_at",
    ],
    requiredOnInsert: ["request_id", "customer_id", "requested_at", "request_summary"],
    timestamp: "updated_at",
  },
  sales_assets: {
    file: "sales-assets.csv",
    key: "asset_id",
    columns: [
      "asset_id", "asset_type", "title", "scope", "customer_id", "audience_role", "sales_stage",
      "use_case", "owner", "status", "authorization_status", "deidentification_status", "version",
      "source_path", "evidence_refs", "last_validated_at", "next_review_at", "usage_feedback", "updated_at",
    ],
    requiredOnInsert: ["asset_id", "asset_type", "title", "scope"],
    timestamp: "updated_at",
  },
} satisfies Record<string, TableDefinition>;

type SalesTableName = keyof typeof SALES_TABLES;

const KNOWLEDGE_DEFINITION: TableDefinition = {
  file: "source-register.csv",
  key: "source_id",
  columns: [
    "source_id", "title", "url", "publisher", "published_date", "accessed_date", "region",
    "topic", "source_type", "quality", "exposure_status", "status", "notes",
  ],
  requiredOnInsert: ["source_id", "title", "status"],
};

function isContained(root: string, candidate: string): boolean {
  const containment = relative(root, candidate);
  return containment === "" || (!containment.startsWith("..") && !isAbsolute(containment));
}

function resolveDataFile(projectRoot: string, namespace: "knowledge" | "sales", file: string): string {
  const root = realpathSync.native(resolve(projectRoot));
  const configured = resolve(root, "data", namespace, file);
  if (!existsSync(configured)) {
    throw new Error(
      `本地数据文件不存在：data/${namespace}/${file}。请先运行 init_local_data.py，系统不会自动创建或覆盖业务数据。`,
    );
  }
  const metadata = lstatSync(configured);
  if (!metadata.isFile() || metadata.isSymbolicLink()) {
    throw new Error(`拒绝读取非普通数据文件：data/${namespace}/${file}`);
  }
  if (metadata.size > MAX_CSV_BYTES) {
    throw new Error(`数据文件超过 ${MAX_CSV_BYTES / 1024 / 1024} MiB 安全上限：data/${namespace}/${file}`);
  }
  const canonical = realpathSync.native(configured);
  if (!isContained(root, canonical)) throw new Error(`数据文件越出项目目录：${configured}`);
  return canonical;
}

export function parseCsv(source: string): string[][] {
  const text = source.replace(/^\uFEFF/, "");
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (quoted) {
      if (char === '"') {
        if (text[index + 1] === '"') {
          field += '"';
          index += 1;
        } else {
          quoted = false;
        }
      } else {
        field += char;
      }
      continue;
    }
    if (char === '"') {
      if (field.length !== 0) throw new Error("CSV 引号位置无效");
      quoted = true;
    } else if (char === ",") {
      row.push(field);
      field = "";
    } else if (char === "\n") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else if (char !== "\r") {
      field += char;
    }
  }
  if (quoted) throw new Error("CSV 存在未闭合引号");
  if (field.length > 0 || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  return rows.filter((candidate, index) => index === 0 || candidate.some((value) => value !== ""));
}

function csvEscape(value: string): string {
  return /[",\r\n]/.test(value) ? `"${value.replaceAll('"', '""')}"` : value;
}

export function serializeCsv(table: CsvTable): string {
  const lines = [table.headers, ...table.rows.map((row) => table.headers.map((header) => row[header] ?? ""))];
  return `${lines.map((line) => line.map(csvEscape).join(",")).join("\r\n")}\r\n`;
}

function readTable(path: string, definition: TableDefinition): CsvTable {
  const parsed = parseCsv(readFileSync(path, "utf8"));
  if (parsed.length === 0) throw new Error(`CSV 缺少表头：${path}`);
  const headers = parsed[0];
  if (headers.length === 0 || headers.some((header) => header.trim() === "")) {
    throw new Error(`CSV 表头无效：${path}`);
  }
  if (new Set(headers).size !== headers.length) throw new Error(`CSV 存在重复表头：${path}`);
  if (
    headers.length !== definition.columns.length ||
    headers.some((header, index) => header !== definition.columns[index])
  ) {
    throw new Error(`CSV 表头与 ${definition.file} 契约不一致，拒绝继续处理`);
  }
  const rows = parsed.slice(1).map((values, index) => {
    if (values.length !== headers.length) {
      throw new Error(`CSV 第 ${index + 2} 行字段数与表头不一致：${path}`);
    }
    return Object.fromEntries(headers.map((header, column) => [header, values[column]]));
  });
  return { headers, rows };
}

function recordVersion(headers: string[], row: Record<string, string>): string {
  const canonical = JSON.stringify(headers.map((header) => row[header] ?? ""));
  return createHash("sha256").update(canonical, "utf8").digest("hex");
}

function isoDate(value: string, field: string, assetId: string): string | undefined {
  if (!value.trim()) return undefined;
  const date = value.slice(0, 10);
  const parsed = new Date(`${date}T00:00:00Z`);
  if (
    !/^\d{4}-\d{2}-\d{2}$/u.test(date) ||
    Number.isNaN(parsed.getTime()) ||
    parsed.toISOString().slice(0, 10) !== date
  ) {
    throw new Error(`销售资产 ${assetId} 的 ${field} 必须以 ISO 日期开头`);
  }
  return date;
}

function validateSalesAssets(rows: Record<string, string>[]): void {
  const scopes = new Set(["generic", "customer-specific"]);
  const statuses = new Set(["draft", "internal-review", "active", "stale", "retired"]);
  const authorizations = new Set(["unknown", "pending", "approved", "not-required"]);
  const deidentifications = new Set(["unknown", "pending", "passed", "not-applicable"]);
  const today = new Date().toISOString().slice(0, 10);
  for (const row of rows) {
    const id = row.asset_id || "<missing>";
    if (!scopes.has(row.scope)) throw new Error(`销售资产 ${id} 的 scope 无效`);
    if (!statuses.has(row.status)) throw new Error(`销售资产 ${id} 的 status 无效`);
    if (!authorizations.has(row.authorization_status)) throw new Error(`销售资产 ${id} 的 authorization_status 无效`);
    if (!deidentifications.has(row.deidentification_status)) throw new Error(`销售资产 ${id} 的 deidentification_status 无效`);
    if (row.scope === "customer-specific" && !row.customer_id.trim()) {
      throw new Error(`客户专属销售资产 ${id} 必须填写 customer_id`);
    }
    const lastValidated = isoDate(row.last_validated_at, "last_validated_at", id);
    const nextReview = isoDate(row.next_review_at, "next_review_at", id);
    if (row.status === "active") {
      for (const field of ["owner", "version", "source_path", "evidence_refs", "last_validated_at", "next_review_at"]) {
        if (!row[field]?.trim()) throw new Error(`启用销售资产 ${id} 缺少 ${field}`);
      }
      if (!new Set(["approved", "not-required"]).has(row.authorization_status)) {
        throw new Error(`启用销售资产 ${id} 必须完成授权`);
      }
      if (!new Set(["passed", "not-applicable"]).has(row.deidentification_status)) {
        throw new Error(`启用销售资产 ${id} 必须完成脱敏核验`);
      }
      if (nextReview && nextReview < today) throw new Error(`启用销售资产 ${id} 已超过 next_review_at，应标记 stale`);
      if (lastValidated && lastValidated > today) throw new Error(`销售资产 ${id} 的 last_validated_at 在未来`);
    }
  }
}

function publicRows(table: CsvTable, rows: Record<string, string>[]) {
  return rows.map((row) => ({ ...row, _record_version: recordVersion(table.headers, row) }));
}

function normalizeLimit(limit: number | undefined): number {
  if (limit === undefined) return DEFAULT_LIMIT;
  if (!Number.isInteger(limit) || limit < 1 || limit > MAX_LIMIT) {
    throw new Error(`limit 必须是 1-${MAX_LIMIT} 的整数`);
  }
  return limit;
}

function matchesQuery(row: Record<string, string>, query: string): boolean {
  const normalized = query.trim().toLocaleLowerCase("zh-CN");
  if (!normalized) return true;
  return Object.values(row).some((value) => value.toLocaleLowerCase("zh-CN").includes(normalized));
}

function matchesFilters(row: Record<string, string>, filters: Record<string, string> | undefined): boolean {
  if (!filters) return true;
  return Object.entries(filters).every(([column, expected]) => {
    if (!(column in row)) throw new Error(`未知筛选字段：${column}`);
    return row[column].toLocaleLowerCase("zh-CN") === expected.toLocaleLowerCase("zh-CN");
  });
}

const ownedDataLocks = new Map<number, string>();

function acquireLock(path: string): number {
  try {
    const descriptor = openSync(path, "wx", 0o600);
    const nonce = randomUUID();
    writeFileSync(descriptor, JSON.stringify({ pid: process.pid, nonce, created_at: new Date().toISOString() }), "utf8");
    fsyncSync(descriptor);
    ownedDataLocks.set(descriptor, nonce);
    return descriptor;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "EEXIST") {
      throw new Error(`数据文件正在被另一个任务更新，请稍后重试：${path}`);
    }
    throw error;
  }
}

function releaseLock(path: string, descriptor: number): void {
  const nonce = ownedDataLocks.get(descriptor);
  ownedDataLocks.delete(descriptor);
  closeSync(descriptor);
  if (!nonce) return;
  try {
    const owner = JSON.parse(readFileSync(path, "utf8")) as { nonce?: unknown };
    if (owner.nonce === nonce) unlinkSync(path);
  } catch {
    // Never delete a lock whose ownership cannot be proven.
  }
}

function atomicWrite(path: string, content: string): void {
  const temporary = join(dirname(path), `.${randomUUID()}.tmp`);
  let descriptor: number | undefined;
  try {
    descriptor = openSync(temporary, "wx", 0o600);
    writeFileSync(descriptor, content, "utf8");
    fsyncSync(descriptor);
    closeSync(descriptor);
    descriptor = undefined;
    renameSync(temporary, path);
  } finally {
    if (descriptor !== undefined) closeSync(descriptor);
    if (existsSync(temporary)) unlinkSync(temporary);
  }
}

type CommitReceipt = {
  schema_version: "1.0";
  intent_id: string;
  payload_sha256: string;
  target: string;
  status: "prepared" | "committed";
  before_sha256: string;
  after_sha256: string;
  result: { operations: Array<{ operation: string; record: Record<string, string> & { _record_version: string } }> };
  updated_at: string;
};

function fileSha256(content: string): string {
  return createHash("sha256").update(content, "utf8").digest("hex");
}

function receiptPath(projectRoot: string, intentId: string): string {
  if (!/^[A-Za-z0-9-]{1,128}$/.test(intentId)) throw new Error("写入意图 ID 无效");
  const directory = resolve(projectRoot, ".pi", "director-runtime", "commits");
  mkdirSync(directory, { recursive: true });
  return join(directory, `${intentId}.json`);
}

function saveReceipt(path: string, receipt: CommitReceipt): void {
  atomicWrite(path, `${JSON.stringify(receipt, null, 2)}\n`);
}

function inspectReceipt(
  projectRoot: string,
  commit: { intent_id: string; payload_sha256: string; target: string },
  targetPath: string | undefined,
): { outcome: "not_committed" | "committed" | "ambiguous"; result?: CommitReceipt["result"] } {
  try {
    const journalPath = receiptPath(projectRoot, commit.intent_id);
    if (!existsSync(journalPath)) return { outcome: "not_committed" };
    const receipt = JSON.parse(readFileSync(journalPath, "utf8")) as CommitReceipt;
    if (
      !targetPath ||
      receipt.schema_version !== "1.0" ||
      receipt.intent_id !== commit.intent_id ||
      receipt.payload_sha256 !== commit.payload_sha256 ||
      receipt.target !== commit.target ||
      !/^[a-f0-9]{64}$/u.test(receipt.before_sha256) ||
      !/^[a-f0-9]{64}$/u.test(receipt.after_sha256) ||
      (receipt.status !== "prepared" && receipt.status !== "committed") ||
      !receipt.result || !Array.isArray(receipt.result.operations) ||
      !existsSync(targetPath)
    ) return { outcome: "ambiguous" };
    const current = fileSha256(readFileSync(targetPath, "utf8"));
    if (current === receipt.after_sha256) {
      if (receipt.status !== "committed") {
        receipt.status = "committed";
        receipt.updated_at = new Date().toISOString();
        saveReceipt(journalPath, receipt);
      }
      return { outcome: "committed", result: receipt.result };
    }
    if (receipt.status === "prepared" && current === receipt.before_sha256) return { outcome: "not_committed" };
    return { outcome: "ambiguous" };
  } catch {
    return { outcome: "ambiguous" };
  }
}

type Mutation = {
  operation: "insert" | "update";
  record_id: string;
  changes: Record<string, string>;
  expected_version?: string;
};

function mutateRecords(
  path: string,
  definition: TableDefinition,
  mutations: Mutation[],
  commit: { projectRoot: string; intent_id: string; payload_sha256: string; target: string },
): { operations: Array<{ operation: string; record: Record<string, string> & { _record_version: string } }> } {
  if (mutations.length < 1 || mutations.length > 100) throw new Error("每批必须包含 1-100 条变更");
  const requestIds = mutations.map((mutation) => mutation.record_id);
  if (new Set(requestIds).size !== requestIds.length) throw new Error("同一批次不能重复变更相同稳定 ID");
  const lockPath = `${path}.lock`;
  const lockDescriptor = acquireLock(lockPath);
  try {
    const beforeContent = readFileSync(path, "utf8");
    const beforeSha256 = fileSha256(beforeContent);
    const journalPath = receiptPath(commit.projectRoot, commit.intent_id);
    if (existsSync(journalPath)) {
      const receipt = JSON.parse(readFileSync(journalPath, "utf8")) as CommitReceipt;
      if (
        receipt.schema_version !== "1.0" ||
        receipt.intent_id !== commit.intent_id ||
        receipt.payload_sha256 !== commit.payload_sha256 ||
        receipt.target !== commit.target ||
        !/^[a-f0-9]{64}$/u.test(receipt.before_sha256) ||
        !/^[a-f0-9]{64}$/u.test(receipt.after_sha256) ||
        (receipt.status !== "prepared" && receipt.status !== "committed") ||
        !receipt.result || !Array.isArray(receipt.result.operations)
      ) {
        throw new Error("提交日志与当前冻结写入不一致，拒绝执行");
      }
      if (beforeSha256 === receipt.after_sha256) {
        if (receipt.status !== "committed") {
          receipt.status = "committed";
          receipt.updated_at = new Date().toISOString();
          saveReceipt(journalPath, receipt);
        }
        return receipt.result;
      }
      if (receipt.status === "committed") {
        throw new Error("已提交的数据快照后来发生变化，拒绝重复执行；请人工核对");
      }
      if (beforeSha256 !== receipt.before_sha256) {
        throw new Error("业务数据既不匹配提交前也不匹配提交后快照，需要人工恢复");
      }
    }
    const table = readTable(path, definition);
    if (!table.headers.includes(definition.key)) throw new Error(`CSV 缺少主键列：${definition.key}`);
    const stableIds = table.rows.map((row) => row[definition.key]);
    if (stableIds.some((value) => !value?.trim())) {
      throw new Error(`CSV 存在空稳定主键：${definition.key}`);
    }
    if (new Set(stableIds).size !== stableIds.length) {
      throw new Error(`CSV 存在重复稳定主键：${definition.key}`);
    }
    const outputRows: Array<{ operation: string; record: Record<string, string> }> = [];
    const timestamp = new Date().toISOString();
    for (const mutation of mutations) {
      const { operation, record_id: recordId, changes, expected_version: expectedVersion } = mutation;
      if (operation !== "insert" && operation !== "update") throw new Error(`不支持的写入操作：${operation}`);
      for (const [column, value] of Object.entries(changes)) {
        if (!table.headers.includes(column)) throw new Error(`拒绝写入未知字段：${column}`);
        if (typeof value !== "string") throw new Error(`字段 ${column} 必须是字符串`);
        if (/^[\t\r]/u.test(value) || /^\s*[=+\-@]/u.test(value)) {
          throw new Error(`字段 ${column} 含可能被表格软件执行的公式前缀，拒绝写入`);
        }
      }
      if (!recordId.trim()) throw new Error(`${definition.key} 不能为空`);
      if (changes[definition.key] !== undefined && changes[definition.key] !== recordId) {
        throw new Error(`不能在变更中修改稳定主键 ${definition.key}`);
      }
      const existingIndex = table.rows.findIndex((row) => row[definition.key] === recordId);
      let output: Record<string, string>;
      if (operation === "insert") {
        if (existingIndex >= 0) throw new Error(`${definition.key}=${recordId} 已存在，拒绝覆盖`);
        output = Object.fromEntries(table.headers.map((header) => [header, changes[header] ?? ""]));
        output[definition.key] = recordId;
        for (const required of definition.requiredOnInsert) {
          if (!output[required]?.trim()) throw new Error(`新增记录缺少必填字段：${required}`);
        }
        if (definition.timestamp && !output[definition.timestamp]) output[definition.timestamp] = timestamp;
        table.rows.push(output);
      } else {
        if (existingIndex < 0) throw new Error(`${definition.key}=${recordId} 不存在，不能更新`);
        if (!expectedVersion) throw new Error("更新必须提供读取结果中的 _record_version");
        const existing = table.rows[existingIndex];
        const currentVersion = recordVersion(table.headers, existing);
        if (currentVersion !== expectedVersion) {
          throw new Error(`记录已变化，拒绝覆盖。请重新读取后再提交：${definition.key}=${recordId}`);
        }
        output = { ...existing, ...changes, [definition.key]: recordId };
        if (definition.timestamp) output[definition.timestamp] = timestamp;
        table.rows[existingIndex] = output;
      }
      outputRows.push({ operation, record: output });
    }
    const result = {
      operations: outputRows.map(({ operation, record }) => ({
        operation,
        record: { ...record, _record_version: recordVersion(table.headers, record) },
      })),
    };
    if (definition === SALES_TABLES.sales_assets) validateSalesAssets(table.rows);
    const afterContent = serializeCsv(table);
    const receipt: CommitReceipt = {
      schema_version: "1.0",
      intent_id: commit.intent_id,
      payload_sha256: commit.payload_sha256,
      target: commit.target,
      status: "prepared",
      before_sha256: beforeSha256,
      after_sha256: fileSha256(afterContent),
      result,
      updated_at: new Date().toISOString(),
    };
    saveReceipt(journalPath, receipt);
    atomicWrite(path, afterContent);
    receipt.status = "committed";
    receipt.updated_at = new Date().toISOString();
    saveReceipt(journalPath, receipt);
    return result;
  } finally {
    releaseLock(lockPath, lockDescriptor);
  }
}

function content(value: unknown) {
  return [{ type: "text" as const, text: JSON.stringify(value, null, 2) }];
}

function assertResultSize(value: unknown): void {
  if (Buffer.byteLength(JSON.stringify(value), "utf8") > MAX_TOOL_RESULT_BYTES) {
    throw new Error("工具结果超过 2 MiB 安全上限；请缩小查询范围或 limit");
  }
}

function assertDistinctQueries(queries: string[]): void {
  const normalized = queries.map((query) => query.trim().toLocaleLowerCase("zh-CN"));
  if (normalized.some((query) => !query)) throw new Error("查询词不能为空");
  if (new Set(normalized).size !== normalized.length) throw new Error("同一批次不能包含重复查询词");
}

const filtersSchema = Type.Optional(
  Type.Record(Type.String(), Type.String(), {
    description: "精确筛选，键必须是目标 CSV 的表头字段",
  }),
);

const changesSchema = Type.Record(Type.String(), Type.String(), {
  description: "拟新增或修改的字段；不能包含 CSV 表头以外字段",
});

const mutationSchema = Type.Object({
  operation: Type.Union([Type.Literal("insert"), Type.Literal("update")]),
  record_id: Type.String({ minLength: 1, maxLength: 128, description: "目标表的稳定 ID" }),
  changes: changesSchema,
  expected_version: Type.Optional(Type.String({ description: "update 时必填，来自 _record_version" })),
});

export function registerDataAdapters(pi: ExtensionAPI, hooks: AdapterHooks): void {
  pi.registerTool({
    name: "director_web_search",
    label: "检索公开网页",
    description: "通过用户配置的 Brave Search API 检索公开资料；需要 BRAVE_SEARCH_API_KEY，不会回退到非官方网页抓取。",
    parameters: Type.Object({
      queries: Type.Array(Type.String({ minLength: 1, maxLength: 400 }), { minItems: 1, maxItems: 10, uniqueItems: true }),
      count: Type.Optional(Type.Number({ minimum: 1, maximum: 10 })),
      country: Type.Optional(Type.String({ pattern: "^[A-Za-z]{2}$", description: "两位国家代码，例如 CN" })),
      search_lang: Type.Optional(Type.String({ pattern: "^[A-Za-z-]{2,10}$", description: "检索语言，例如 zh-hans" })),
    }),
    async execute(_toolCallId, params) {
      hooks.beforeLogicalTool("web.search", params);
      const key = process.env.BRAVE_SEARCH_API_KEY?.trim();
      if (!key) {
        throw new Error("公开检索尚未配置。请仅在本机设置 BRAVE_SEARCH_API_KEY 后重试；密钥不得写入仓库。");
      }
      assertDistinctQueries(params.queries);
      const searches = [];
      const totalSignal = AbortSignal.timeout(60_000);
      for (const configuredQuery of params.queries) {
        const query = configuredQuery.trim();
        if (!query || query.length > 400 || query.split(/\s+/u).length > 50) {
          throw new Error("检索词必须为 1-400 字符且不超过 50 个词");
        }
        const endpoint = new URL("https://api.search.brave.com/res/v1/web/search");
        endpoint.searchParams.set("q", query);
        endpoint.searchParams.set("count", String(Math.trunc(params.count ?? 10)));
        if (params.country) endpoint.searchParams.set("country", params.country.toUpperCase());
        if (params.search_lang) endpoint.searchParams.set("search_lang", params.search_lang.toLowerCase());
        const response = await fetch(endpoint, {
          headers: { Accept: "application/json", "X-Subscription-Token": key },
          signal: totalSignal,
          redirect: "error",
        });
        if (!response.ok) throw new Error(`公开检索失败（HTTP ${response.status}）。请检查 API 配置、配额或网络后重试。`);
        const length = Number(response.headers.get("Content-Length") ?? "0");
        if (Number.isFinite(length) && length > MAX_WEB_RESPONSE_BYTES) throw new Error("公开检索响应超过 2 MiB 安全上限");
        const responseText = await response.text();
        if (Buffer.byteLength(responseText, "utf8") > MAX_WEB_RESPONSE_BYTES) throw new Error("公开检索响应超过 2 MiB 安全上限");
        const payload = JSON.parse(responseText) as {
          web?: { results?: Array<{ title?: unknown; url?: unknown; description?: unknown; age?: unknown }> };
        };
        const results = (payload.web?.results ?? []).slice(0, 10).flatMap((item) => {
          if (typeof item.title !== "string" || typeof item.url !== "string") return [];
          try {
            const url = new URL(item.url);
            if (url.protocol !== "https:" && url.protocol !== "http:") return [];
            return [{ title: item.title.slice(0, 500), url: url.toString().slice(0, 2048), description: typeof item.description === "string" ? item.description.slice(0, 2000) : "", age: typeof item.age === "string" ? item.age.slice(0, 100) : "" }];
          } catch { return []; }
        });
        searches.push({ query, results });
      }
      const result = { provider: "brave", searched_at: new Date().toISOString(), searches };
      assertResultSize(result);
      hooks.afterLogicalTool("web.search", params, result);
      return { content: content(result), details: result };
    },
  });

  pi.registerTool({
    name: "director_knowledge_search",
    label: "检索本地知识库",
    description: "检索 data/knowledge/source-register.csv，返回来源记录与记录版本，不读取个人聊天。",
    parameters: Type.Object({
      queries: Type.Array(Type.String({ minLength: 1, maxLength: 400, description: "在所有字段中进行不区分大小写的包含检索" }), { minItems: 1, maxItems: 10, uniqueItems: true }),
      filters: filtersSchema,
      limit: Type.Optional(Type.Number({ minimum: 1, maximum: MAX_LIMIT })),
    }),
    async execute(_toolCallId, params) {
      hooks.beforeLogicalTool("knowledge.search", params);
      assertDistinctQueries(params.queries);
      const path = resolveDataFile(hooks.projectRoot(), "knowledge", KNOWLEDGE_DEFINITION.file);
      const table = readTable(path, KNOWLEDGE_DEFINITION);
      const result = {
        snapshot_at: new Date().toISOString(),
        searches: params.queries.map((query) => {
          const matched = table.rows.filter((row) => matchesQuery(row, query)).filter((row) => matchesFilters(row, params.filters));
          const rows = matched.slice(0, normalizeLimit(params.limit));
          return { query, total_matches: matched.length, returned: rows.length, rows: publicRows(table, rows) };
        }),
      };
      if (result.searches.reduce((total, search) => total + search.returned, 0) > 200) {
        throw new Error("本次知识库检索总返回行数超过 200，请缩小范围或 limit");
      }
      assertResultSize(result);
      hooks.afterLogicalTool("knowledge.search", params, result);
      return { content: content(result), details: result };
    },
  });

  pi.registerTool({
    name: "director_knowledge_write",
    label: "更新本地知识来源登记",
    description: "经 DAG 审批后新增或带版本更新知识来源；不支持删除或任意文件写入。",
    parameters: Type.Object({
      mutations: Type.Array(mutationSchema, { minItems: 1, maxItems: 100 }),
    }),
    async execute(_toolCallId, params) {
      const commit = hooks.beforeLogicalTool("knowledge.write", params);
      if (!commit?.intent_id || !commit.payload_sha256) throw new Error("写入缺少受管提交上下文");
      let path: string | undefined;
      try {
        path = resolveDataFile(hooks.projectRoot(), "knowledge", KNOWLEDGE_DEFINITION.file);
        const result = mutateRecords(path, KNOWLEDGE_DEFINITION, params.mutations, {
          projectRoot: hooks.projectRoot(), intent_id: commit.intent_id,
          payload_sha256: commit.payload_sha256, target: "knowledge/source-register",
        });
        hooks.afterLogicalTool("knowledge.write", params, result);
        return { content: content(result), details: result };
      } catch (error) {
        const inspected = inspectReceipt(hooks.projectRoot(), {
          intent_id: commit.intent_id, payload_sha256: commit.payload_sha256,
          target: "knowledge/source-register",
        }, path);
        if (inspected.outcome === "committed" && inspected.result) {
          hooks.afterLogicalTool("knowledge.write", params, inspected.result);
          return { content: content(inspected.result), details: inspected.result };
        }
        hooks.onLogicalToolError(
          "knowledge.write", params,
          inspected.outcome === "committed" ? "ambiguous" : inspected.outcome,
          error,
        );
        throw error;
      }
    },
  });

  pi.registerTool({
    name: "director_sales_read",
    label: "读取销售台账",
    description: "按表、关键词和精确字段筛选读取本地销售台账；不接入个人聊天。",
    parameters: Type.Object({
      tables: Type.Array(
        Type.Union([
          Type.Literal("customers"),
          Type.Literal("activities"),
          Type.Literal("resource_requests"),
          Type.Literal("sales_assets"),
        ]),
        { minItems: 1, maxItems: 4, uniqueItems: true },
      ),
      query: Type.Optional(Type.String()),
      limit: Type.Optional(Type.Number({ minimum: 1, maximum: MAX_LIMIT })),
    }),
    async execute(_toolCallId, params) {
      hooks.beforeLogicalTool("sales.read", params);
      const tables = params.tables.map((configuredTable) => {
        const tableName = configuredTable as SalesTableName;
        const definition = SALES_TABLES[tableName];
        const path = resolveDataFile(hooks.projectRoot(), "sales", definition.file);
        const table = readTable(path, definition);
        const matched = table.rows.filter((row) => matchesQuery(row, params.query ?? ""));
        const rows = matched.slice(0, normalizeLimit(params.limit));
        return {
          table: tableName,
          total_matches: matched.length,
          returned: rows.length,
          rows: publicRows(table, rows),
        };
      });
      const result = {
        snapshot_at: new Date().toISOString(),
        tables,
      };
      if (tables.reduce((total, table) => total + table.returned, 0) > 200) {
        throw new Error("本次销售台账读取总返回行数超过 200，请缩小范围或 limit");
      }
      assertResultSize(result);
      hooks.afterLogicalTool("sales.read", params, result);
      return { content: content(result), details: result };
    },
  });

  pi.registerTool({
    name: "director_sales_write",
    label: "更新销售台账",
    description: "经 DAG 审批后新增或带版本更新销售台账记录；不支持删除、改主键或任意字段。",
    parameters: Type.Object({
      table: Type.Union([
        Type.Literal("customers"),
        Type.Literal("activities"),
        Type.Literal("resource_requests"),
        Type.Literal("sales_assets"),
      ]),
      mutations: Type.Array(mutationSchema, { minItems: 1, maxItems: 100 }),
    }),
    async execute(_toolCallId, params) {
      const commit = hooks.beforeLogicalTool("sales.write", params);
      if (!commit?.intent_id || !commit.payload_sha256) throw new Error("写入缺少受管提交上下文");
      const tableName = params.table as SalesTableName;
      const definition = SALES_TABLES[tableName];
      let path: string | undefined;
      try {
        path = resolveDataFile(hooks.projectRoot(), "sales", definition.file);
        const result = {
          table: tableName,
          ...mutateRecords(path, definition, params.mutations, {
            projectRoot: hooks.projectRoot(), intent_id: commit.intent_id,
            payload_sha256: commit.payload_sha256, target: `sales/${tableName}`,
          }),
        };
        hooks.afterLogicalTool("sales.write", params, result);
        return { content: content(result), details: result };
      } catch (error) {
        const inspected = inspectReceipt(hooks.projectRoot(), {
          intent_id: commit.intent_id, payload_sha256: commit.payload_sha256,
          target: `sales/${tableName}`,
        }, path);
        if (inspected.outcome === "committed" && inspected.result) {
          const recovered = { table: tableName, ...inspected.result };
          hooks.afterLogicalTool("sales.write", params, recovered);
          return { content: content(recovered), details: recovered };
        }
        hooks.onLogicalToolError(
          "sales.write", params,
          inspected.outcome === "committed" ? "ambiguous" : inspected.outcome,
          error,
        );
        throw error;
      }
    },
  });
}
