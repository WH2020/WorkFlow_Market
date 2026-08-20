import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { execFile } from "node:child_process";
import {
  closeSync,
  existsSync,
  fstatSync,
  fsyncSync,
  lstatSync,
  linkSync,
  mkdirSync,
  openSync,
  readFileSync,
  readSync,
  realpathSync,
  readdirSync,
  renameSync,
  rmSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { createHash, randomUUID } from "node:crypto";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { Type } from "typebox";
import { normalizePublicUrl, openWebSource, readLocalPdf } from "./source-readers.ts";
import { searchPublicWeb } from "./web-search.ts";

export type AdapterHooks = {
  projectRoot: () => string;
  beforeLogicalTool: (logicalTool: string, params: unknown) => {
    intent_id?: string;
    payload_sha256?: string;
    task_id?: string;
    profile_id?: string;
    authorized_urls?: string[];
  } | void;
  afterLogicalTool: (logicalTool: string, params: unknown, details: unknown) => void;
  onLogicalToolError: (
    logicalTool: "knowledge.write" | "sales.write" | "artifact.deck.write",
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
const MAX_DECK_BYTES = 100 * 1024 * 1024;
const MAX_WEEKLY_PERIOD_DAYS = 31;
const MAX_WEEKLY_TASK_FILES = 500;
const MAX_WEEKLY_OUTPUT_FILES = 200;
const MAX_WEEKLY_SOURCE_BYTES = 32 * 1024 * 1024;
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

function parseTableContent(content: string, path: string, definition: TableDefinition): CsvTable {
  const parsed = parseCsv(content);
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

function readTable(path: string, definition: TableDefinition): CsvTable {
  return parseTableContent(readFileSync(path, "utf8"), path, definition);
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

function validateKnowledgeRecords(rows: Record<string, string>[]): void {
  const statuses = new Set(["verified", "pending", "superseded"]);
  for (const row of rows) {
    const id = row.source_id || "<missing>";
    if (!statuses.has(row.status)) throw new Error(`知识来源 ${id} 的 status 必须是 verified、pending 或 superseded`);
    for (const field of ["published_date", "accessed_date"]) {
      if (!row[field]?.trim()) continue;
      const date = row[field].slice(0, 10);
      const parsed = new Date(`${date}T00:00:00Z`);
      if (!/^\d{4}-\d{2}-\d{2}$/u.test(date) || Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== date) {
        throw new Error(`知识来源 ${id} 的 ${field} 必须是 ISO 日期`);
      }
    }
    if (row.status === "verified") {
      for (const field of ["publisher", "accessed_date", "source_type", "quality", "notes"]) {
        if (!row[field]?.trim()) throw new Error(`已核验知识来源 ${id} 缺少 ${field}`);
      }
      if (!row.url.trim() && !/evidence_refs=/u.test(row.notes)) {
        throw new Error(`已核验知识来源 ${id} 必须有 URL 或 notes 中的 evidence_refs`);
      }
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

function parsePeriod(period: { start: string; end: string }): { start: string; end: string; startMs: number; endExclusiveMs: number } {
  const parse = (value: string, field: string): number => {
    if (!/^\d{4}-\d{2}-\d{2}$/u.test(value)) throw new Error(`${field} 必须是 ISO 日期`);
    const validationTimestamp = Date.parse(`${value}T00:00:00.000Z`);
    const timestamp = Date.parse(`${value}T00:00:00.000+08:00`);
    if (!Number.isFinite(timestamp) || !Number.isFinite(validationTimestamp) || new Date(validationTimestamp).toISOString().slice(0, 10) !== value) {
      throw new Error(`${field} 不是有效日期`);
    }
    return timestamp;
  };
  const startMs = parse(period.start, "period.start");
  const endMs = parse(period.end, "period.end");
  if (startMs > endMs) throw new Error("周报周期开始日期不能晚于结束日期");
  const endExclusiveMs = endMs + 86_400_000;
  if ((endExclusiveMs - startMs) / 86_400_000 > MAX_WEEKLY_PERIOD_DAYS) {
    throw new Error(`周报周期不能超过 ${MAX_WEEKLY_PERIOD_DAYS} 天`);
  }
  return { ...period, startMs, endExclusiveMs };
}

function dateInPeriod(value: string | undefined, period: ReturnType<typeof parsePeriod>): boolean {
  if (!value) return false;
  const timestamp = Date.parse(value.length === 10 ? `${value}T00:00:00.000+08:00` : value);
  return Number.isFinite(timestamp) && timestamp >= period.startMs && timestamp < period.endExclusiveMs;
}

function binarySha256(path: string): string {
  const hash = createHash("sha256");
  const descriptor = openSync(path, "r");
  const buffer = Buffer.allocUnsafe(1024 * 1024);
  try {
    while (true) {
      const bytesRead = readSync(descriptor, buffer, 0, buffer.length, null);
      if (bytesRead === 0) break;
      hash.update(buffer.subarray(0, bytesRead));
    }
  } finally {
    closeSync(descriptor);
  }
  return hash.digest("hex");
}

type WeeklySourceVersion = { source_id: string; path: string; sha256: string; bytes: number; version: string };

function weeklyTextSnapshot(
  root: string,
  path: string,
  sourceId: string,
  maxBytes: number,
): { source: WeeklySourceVersion; text: string } {
  const meta = lstatSync(path);
  if (!meta.isFile() || meta.isSymbolicLink()) throw new Error(`周报来源不是普通文件：${relative(root, path)}`);
  const canonical = realpathSync.native(path);
  if (!isContained(root, canonical)) throw new Error(`周报来源越出项目目录：${relative(root, path)}`);
  const descriptor = openSync(canonical, "r");
  let content: Buffer;
  try {
    const opened = fstatSync(descriptor);
    if (!opened.isFile() || opened.size > maxBytes) throw new Error(`周报来源超过安全上限：${relative(root, canonical)}`);
    content = readFileSync(descriptor);
    if (content.length > maxBytes) throw new Error(`周报来源读取期间超过安全上限：${relative(root, canonical)}`);
  } finally {
    closeSync(descriptor);
  }
  const digest = createHash("sha256").update(content).digest("hex");
  const source = {
    source_id: sourceId,
    path: relative(root, canonical).replaceAll("\\", "/"),
    sha256: digest,
    bytes: content.length,
    version: `sha256:${digest}`,
  };
  return { source, text: content.toString("utf8") };
}

function weeklyBinarySnapshot(
  root: string,
  path: string,
  sourceId: string,
): { source: WeeklySourceVersion; modifiedAt: string } {
  const meta = lstatSync(path);
  if (!meta.isFile() || meta.isSymbolicLink()) throw new Error(`周报来源不是普通文件：${relative(root, path)}`);
  const canonical = realpathSync.native(path);
  if (!isContained(root, canonical)) throw new Error(`周报来源越出项目目录：${relative(root, path)}`);
  const descriptor = openSync(canonical, "r");
  const hash = createHash("sha256");
  const buffer = Buffer.allocUnsafe(1024 * 1024);
  let opened: ReturnType<typeof fstatSync>;
  try {
    opened = fstatSync(descriptor);
    while (true) {
      const bytesRead = readSync(descriptor, buffer, 0, buffer.length, null);
      if (bytesRead === 0) break;
      hash.update(buffer.subarray(0, bytesRead));
    }
  } finally {
    closeSync(descriptor);
  }
  const digest = hash.digest("hex");
  return {
    source: {
      source_id: sourceId,
      path: relative(root, canonical).replaceAll("\\", "/"),
      sha256: digest,
      bytes: opened.size,
      version: `sha256:${digest}`,
    },
    modifiedAt: opened.mtime.toISOString(),
  };
}

export function collectWeeklySnapshot(
  projectRoot: string,
  requestedPeriod: { start: string; end: string },
  profileId?: string,
): Record<string, unknown> {
  const period = parsePeriod(requestedPeriod);
  const root = realpathSync.native(resolve(projectRoot));
  const sources: WeeklySourceVersion[] = [];
  let sourceBytes = 0;
  const account = (source: WeeklySourceVersion): void => {
    sourceBytes += source.bytes;
    if (sourceBytes > MAX_WEEKLY_SOURCE_BYTES) throw new Error("周报快照来源总量超过 32 MiB 安全上限");
    sources.push(source);
  };

  const tasks: Record<string, unknown>[] = [];
  const allowedOutputPaths = new Set<string>();
  const truncation: {
    tasks: { discovered: number; inspected: number; returned: number; truncated: boolean };
    sales: Record<string, { matched: number; returned: number; truncated: boolean }>;
    knowledge: { matched: number; returned: number; truncated: boolean };
    outputs: { discovered: number; inspected: number; returned: number; truncated: boolean };
  } = {
    tasks: { discovered: 0, inspected: 0, returned: 0, truncated: false },
    sales: {},
    knowledge: { matched: 0, returned: 0, truncated: false },
    outputs: { discovered: 0, inspected: 0, returned: 0, truncated: false },
  };
  const taskDirectory = resolve(root, ".pi", "director-runtime", "tasks");
  if (existsSync(taskDirectory)) {
    const directoryMeta = lstatSync(taskDirectory);
    if (!directoryMeta.isDirectory() || directoryMeta.isSymbolicLink()) throw new Error("任务状态目录必须是普通目录");
    const canonicalTasks = realpathSync.native(taskDirectory);
    if (!isContained(root, canonicalTasks)) throw new Error("任务状态目录越出项目目录");
    const names = readdirSync(canonicalTasks).filter((name) => /^[A-Za-z0-9_-]{1,128}\.json$/u.test(name));
    const taskCandidates = names.map((name) => {
      const path = join(canonicalTasks, name);
      const meta = lstatSync(path);
      if (!meta.isFile() || meta.isSymbolicLink() || meta.size > 1024 * 1024) throw new Error(`任务状态文件无效：${name}`);
      return { name, modified: meta.mtimeMs };
    }).sort((left, right) => right.modified - left.modified || left.name.localeCompare(right.name));
    const selectedTasks = taskCandidates.slice(0, MAX_WEEKLY_TASK_FILES);
    truncation.tasks = {
      discovered: taskCandidates.length,
      inspected: selectedTasks.length,
      returned: 0,
      truncated: taskCandidates.length > selectedTasks.length,
    };
    for (const { name } of selectedTasks) {
      const path = join(canonicalTasks, name);
      const taskSnapshot = weeklyTextSnapshot(root, path, `task:${name.slice(0, -5)}`, 1024 * 1024);
      const task = JSON.parse(taskSnapshot.text) as Record<string, unknown>;
      if (profileId && task.profile_id !== profileId) continue;
      const matchingAudit = Array.isArray(task.audit)
        ? task.audit.filter((event) => event && typeof event === "object" && dateInPeriod(String((event as Record<string, unknown>).at ?? ""), period))
        : [];
      const audit = matchingAudit.slice(0, 500);
      if (!dateInPeriod(String(task.created_at ?? ""), period) && !dateInPeriod(String(task.updated_at ?? ""), period) && audit.length === 0) continue;
      account(taskSnapshot.source);
      if (Array.isArray(task.artifacts)) {
        for (const artifact of task.artifacts) {
          if (typeof artifact === "string" && /^outputs\/[^/\\\0]{1,180}$/u.test(artifact)) allowedOutputPaths.add(artifact);
        }
      }
      tasks.push({
        task_id: task.task_id,
        profile_id: task.profile_id,
        service_id: task.service_id,
        workflow_id: task.workflow_id,
        request: typeof task.request === "string" ? task.request.slice(0, 1000) : "",
        status: task.status,
        version: task.version,
        completed_nodes: Array.isArray(task.completed_nodes) ? task.completed_nodes.slice(0, 100) : [],
        artifacts: Array.isArray(task.artifacts) ? task.artifacts.slice(0, 100) : [],
        created_at: task.created_at,
        updated_at: task.updated_at,
        audit,
        audit_total: matchingAudit.length,
        audit_returned: audit.length,
        audit_truncated: matchingAudit.length > audit.length,
      });
    }
    truncation.tasks.returned = tasks.length;
  }

  const sales: Record<string, Record<string, string>[]> = {};
  const dateFields: Record<string, string[]> = {
    customers: ["updated_at", "last_evidence_date", "next_action_due"],
    activities: ["occurred_at", "created_at", "next_action_due"],
    resource_requests: ["requested_at", "updated_at", "deadline"],
  };
  if (profileId === "market-director" || profileId === undefined) {
    for (const tableName of ["customers", "activities", "resource_requests"] as const) {
      const definition = SALES_TABLES[tableName];
      const path = resolveDataFile(root, "sales", definition.file);
      const tableSnapshot = weeklyTextSnapshot(root, path, `sales:${tableName}`, MAX_CSV_BYTES);
      const table = parseTableContent(tableSnapshot.text, path, definition);
      account(tableSnapshot.source);
      const matchingRows = table.rows.filter((row) => dateFields[tableName]!.some((field) => dateInPeriod(row[field], period)));
      sales[tableName] = publicRows(table, matchingRows).slice(0, 1000);
      truncation.sales[tableName] = {
        matched: matchingRows.length,
        returned: sales[tableName].length,
        truncated: matchingRows.length > sales[tableName].length,
      };
    }
  }

  const knowledgePath = resolveDataFile(root, "knowledge", KNOWLEDGE_DEFINITION.file);
  const knowledgeSnapshot = weeklyTextSnapshot(root, knowledgePath, "knowledge:source-register", MAX_CSV_BYTES);
  const knowledgeTable = parseTableContent(knowledgeSnapshot.text, knowledgePath, KNOWLEDGE_DEFINITION);
  account(knowledgeSnapshot.source);
  const matchingKnowledge = knowledgeTable.rows.filter((row) => dateInPeriod(row.accessed_date, period));
  const knowledge = publicRows(knowledgeTable, matchingKnowledge).slice(0, 1000);
  truncation.knowledge = {
    matched: matchingKnowledge.length,
    returned: knowledge.length,
    truncated: matchingKnowledge.length > knowledge.length,
  };

  const outputs: Record<string, unknown>[] = [];
  const outputDirectory = resolve(root, "outputs");
  if (existsSync(outputDirectory)) {
    const outputMeta = lstatSync(outputDirectory);
    if (!outputMeta.isDirectory() || outputMeta.isSymbolicLink()) throw new Error("outputs/ 必须是普通目录");
    const canonicalOutputs = realpathSync.native(outputDirectory);
    if (!isContained(root, canonicalOutputs)) throw new Error("outputs/ 越出项目目录");
    const names = readdirSync(canonicalOutputs).sort();
    truncation.outputs.discovered = names.length;
    const candidates = names.flatMap((name) => {
      if (!/^[^/\\\0]{1,180}$/u.test(name)) throw new Error("outputs/ 包含不安全文件名");
      const path = join(canonicalOutputs, name);
      const meta = lstatSync(path);
      if (!meta.isFile() || meta.isSymbolicLink() || meta.size > MAX_DECK_BYTES) return [];
      if (!allowedOutputPaths.has(`outputs/${name}`)) return [];
      return [{ name, modified: meta.mtimeMs }];
    }).sort((left, right) => right.modified - left.modified || left.name.localeCompare(right.name));
    const selectedOutputs = candidates.slice(0, MAX_WEEKLY_OUTPUT_FILES);
    truncation.outputs.inspected = selectedOutputs.length;
    truncation.outputs.truncated = candidates.length > selectedOutputs.length;
    for (const { name } of selectedOutputs) {
      const path = join(canonicalOutputs, name);
      const outputSnapshot = weeklyBinarySnapshot(root, path, `output:${name}`);
      if (!dateInPeriod(outputSnapshot.modifiedAt, period)) continue;
      account(outputSnapshot.source);
      outputs.push({
        path: outputSnapshot.source.path,
        bytes: outputSnapshot.source.bytes,
        sha256: outputSnapshot.source.sha256,
        modified_at: outputSnapshot.modifiedAt,
      });
    }
    truncation.outputs.returned = outputs.length;
  }

  const snapshotBase = {
    schema_version: "1.0",
    period: { start: period.start, end: period.end },
    profile_id: profileId ?? null,
    generated_at: new Date().toISOString(),
    tasks,
    sales,
    outputs,
    knowledge,
    truncation,
    source_versions: sources,
  };
  return { ...snapshotBase, snapshot_sha256: createHash("sha256").update(JSON.stringify(snapshotBase), "utf8").digest("hex") };
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

function runDeckBuilder(
  nodePath: string,
  builderPath: string,
  inputPath: string,
  outputPath: string,
  qaDirectory: string,
): Promise<string> {
  return new Promise((resolvePromise, reject) => {
    execFile(
      nodePath,
      [builderPath, "--input", inputPath, "--output", outputPath, "--qa-dir", qaDirectory],
      { timeout: 180_000, windowsHide: true, maxBuffer: 2 * 1024 * 1024, env: artifactEnvironment() },
      (error, stdout, stderr) => {
        if (error) {
          reject(new Error(`PPT 构建失败：${String(stderr || stdout || error.message).trim().slice(0, 4000)}`));
          return;
        }
        resolvePromise(String(stdout).trim());
      },
    );
  });
}

function runDeckQa(
  nodePath: string,
  qaScript: string,
  outputPath: string,
  qaDirectory: string,
  expectedSlideCount: number,
): Promise<string> {
  return new Promise((resolvePromise, reject) => {
    execFile(
      nodePath,
      [qaScript, "--input", outputPath, "--qa-dir", qaDirectory, "--expected-slides", String(expectedSlideCount)],
      {
        timeout: 300_000,
        windowsHide: true,
        maxBuffer: 4 * 1024 * 1024,
        env: artifactEnvironment(),
      },
      (error, stdout, stderr) => {
        if (error) {
          reject(new Error(`PPT 溢出检查失败：${String(stderr || stdout || error.message).trim().slice(0, 4000)}`));
          return;
        }
        const result = String(stdout).trim();
        if (!/Test passed\. No overflow detected\./u.test(result)) {
          reject(new Error(`PPT 溢出检查未通过：${result.slice(0, 4000)}`));
          return;
        }
        resolvePromise(result);
      },
    );
  });
}

function artifactEnvironment(): NodeJS.ProcessEnv {
  const allowed = new Set([
    "PATH", "Path", "PATHEXT", "SystemRoot", "SYSTEMROOT", "WINDIR", "windir", "COMSPEC",
    "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA",
    "LANG", "LC_ALL", "LC_CTYPE", "XDG_CACHE_HOME",
    "WORKFLOW_LIBREOFFICE_PATH", "WORKFLOW_CJK_FONT", "WORKFLOW_LATIN_FONT",
  ]);
  return Object.fromEntries(Object.entries(process.env).filter(([key]) => allowed.has(key)));
}

function assertSafeDeckText(value: unknown, label: string, maxLength: number, singleLine = false): void {
  if (value === undefined) return;
  if (typeof value !== "string" || value.length > maxLength) throw new Error(`${label} 类型无效或过长`);
  if (/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/u.test(value)) throw new Error(`${label} 含控制字符`);
  if (/\[\/?Sources\]/iu.test(value)) throw new Error(`${label} 不能伪造 speaker notes 来源标记`);
  if (singleLine && /[\r\n]/u.test(value)) throw new Error(`${label} 必须是单行文本`);
}

export type DeckPayload = {
  schema_version: "1.0";
  snapshot_sha256: string;
  plan_sha256?: string;
  output_name: string;
  profile_id: "market-director" | "product-director";
  template_id: "ceo-weekly" | "management-report" | "government-program" | "technology-research";
  period: { start: string; end: string };
  slides: Array<{
    title: string;
    layout_intent?: "single-focus" | "fifty-fifty" | "two-thirds" | "three-column" | "top-hero" | "mixed-grid";
    subtitle?: string;
    eyebrow?: string;
    lead?: string;
    body?: string[];
    callout?: string;
    notes?: string;
    sources?: Array<{ title: string; url?: string; path?: string; sha256?: string; page?: number }>;
  }>;
};

export function assertDeckPayload(value: DeckPayload): void {
  if (value.schema_version !== "1.0") throw new Error("PPT 载荷 schema_version 必须为 1.0");
  if (!/^[a-f0-9]{64}$/u.test(value.snapshot_sha256)) throw new Error("PPT 载荷必须包含当前 weekly.snapshot 的 SHA-256");
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.pptx$/u.test(value.output_name)) {
    throw new Error("PPT 输出名必须是 1-120 字符的安全 ASCII .pptx 文件名");
  }
  if (!new Set(["ceo-weekly", "management-report", "government-program", "technology-research"]).has(value.template_id)) {
    throw new Error("PPT template_id 无效");
  }
  if (value.plan_sha256 !== undefined && !/^[a-f0-9]{64}$/u.test(value.plan_sha256)) {
    throw new Error("PPT plan_sha256 无效");
  }
  if (!new Set(["market-director", "product-director"]).has(value.profile_id)) throw new Error("PPT Profile 无效");
  parsePeriod(value.period);
  if (!Array.isArray(value.slides) || value.slides.length < 4 || value.slides.length > 10) {
    throw new Error("PPT 必须包含 4-10 页");
  }
  for (const [index, slide] of value.slides.entries()) {
    assertSafeDeckText(slide.title, `PPT 第 ${index + 1} 页标题`, 120);
    if (typeof slide.title !== "string" || !slide.title.trim()) throw new Error(`PPT 第 ${index + 1} 页标题缺失`);
    if (slide.layout_intent !== undefined && !new Set(["single-focus", "fifty-fifty", "two-thirds", "three-column", "top-hero", "mixed-grid"]).has(slide.layout_intent)) {
      throw new Error(`PPT 第 ${index + 1} 页 layout_intent 无效`);
    }
    assertSafeDeckText(slide.subtitle, `PPT 第 ${index + 1} 页副标题`, 240);
    assertSafeDeckText(slide.eyebrow, `PPT 第 ${index + 1} 页眉`, 80);
    assertSafeDeckText(slide.lead, `PPT 第 ${index + 1} 页引导语`, 240);
    assertSafeDeckText(slide.callout, `PPT 第 ${index + 1} 页结论框`, 240);
    assertSafeDeckText(slide.notes, `PPT 第 ${index + 1} 页备注`, 4000);
    const body = slide.body ?? [];
    if (!Array.isArray(body) || body.length > 7 || body.some((line) => typeof line !== "string" || !line.trim() || line.length > 180)) {
      throw new Error(`PPT 第 ${index + 1} 页正文必须是最多 7 条、每条不超过 180 字的非空文本`);
    }
    body.forEach((line, bodyIndex) => assertSafeDeckText(line, `PPT 第 ${index + 1} 页正文 ${bodyIndex + 1}`, 180));
    const sources = slide.sources ?? [];
    if (!Array.isArray(sources) || sources.length > 20) throw new Error(`PPT 第 ${index + 1} 页来源超过 20 条`);
    for (const source of sources) {
      assertSafeDeckText(source.title, `PPT 第 ${index + 1} 页来源标题`, 500, true);
      if (typeof source.title !== "string" || !source.title.trim()) throw new Error(`PPT 第 ${index + 1} 页来源标题无效`);
      if (!source.url && !source.path) throw new Error(`PPT 第 ${index + 1} 页来源必须包含 url 或 path`);
      if (source.url) {
        assertSafeDeckText(source.url, `PPT 第 ${index + 1} 页来源 URL`, 2048, true);
        if (source.url !== source.url.trim()) throw new Error(`PPT 第 ${index + 1} 页来源 URL 不能包含首尾空白`);
        let url: URL;
        try { url = new URL(source.url); } catch { throw new Error(`PPT 第 ${index + 1} 页来源 URL 无效`); }
        if (url.protocol !== "https:" && url.protocol !== "http:") throw new Error(`PPT 第 ${index + 1} 页来源只允许 http/https URL`);
        if (url.username || url.password) throw new Error(`PPT 第 ${index + 1} 页来源 URL 不能包含凭据`);
        for (const key of url.searchParams.keys()) {
          if (/(^|[-_])(api[-_]?key|access[-_]?token|token|password|secret|signature|sig|credential)([-_]|$)/iu.test(key)) {
            throw new Error(`PPT 第 ${index + 1} 页来源 URL 包含敏感查询参数`);
          }
        }
        if (/(token|password|secret|signature|credential|sig)=/iu.test(url.hash)) {
          throw new Error(`PPT 第 ${index + 1} 页来源 URL 片段包含敏感参数`);
        }
      }
      if (source.path) {
        assertSafeDeckText(source.path, `PPT 第 ${index + 1} 页来源路径`, 240, true);
        if (isAbsolute(source.path) || source.path.split(/[\\/]/u).some((segment) => segment === "..")) {
          throw new Error(`PPT 第 ${index + 1} 页来源路径必须是项目内相对路径`);
        }
        if (!/^[a-f0-9]{64}$/u.test(source.sha256 ?? "")) {
          throw new Error(`PPT 第 ${index + 1} 页本地来源必须包含证据快照中的 SHA-256`);
        }
      } else if (source.sha256 !== undefined) {
        throw new Error(`PPT 第 ${index + 1} 页纯 URL 来源不能携带本地文件 SHA-256`);
      }
      if (source.page !== undefined && (!Number.isInteger(source.page) || source.page < 1 || source.page > 100000)) {
        throw new Error(`PPT 第 ${index + 1} 页来源页码无效`);
      }
    }
  }
  if (!value.slides.some((slide) => (slide.sources?.length ?? 0) > 0)) {
    throw new Error("PPT 至少需要一条可追溯来源，来源将写入 speaker notes");
  }
}

function assertDeckSourcePaths(root: string, payload: DeckPayload): void {
  for (const [index, slide] of payload.slides.entries()) {
    for (const source of slide.sources ?? []) {
      if (!source.path) continue;
      const requested = resolve(root, source.path);
      if (!isContained(root, requested) || !existsSync(requested)) throw new Error(`PPT 第 ${index + 1} 页本地来源不存在或越出项目目录`);
      const meta = lstatSync(requested);
      const canonical = realpathSync.native(requested);
      if (!meta.isFile() || meta.isSymbolicLink() || meta.size > MAX_WEEKLY_SOURCE_BYTES || !isContained(root, canonical)) {
        throw new Error(`PPT 第 ${index + 1} 页本地来源必须是项目内普通文件`);
      }
      if (binarySha256(canonical) !== source.sha256) throw new Error(`PPT 第 ${index + 1} 页本地来源在 Approval 后已发生变化`);
    }
  }
}

type DeckCommitContext = { intent_id: string; payload_sha256: string; task_id: string; profile_id: string };
class DeckNotCommittedError extends Error {}
type DeckReceipt = {
  schema_version: "1.0";
  intent_id: string;
  task_id: string;
  payload_sha256: string;
  owner: "director_artifact_deck_write";
  target: string;
  status: "prepared" | "committed";
  artifact_sha256: string;
  bytes: number;
  slide_count: number;
  qa: {
    validation?: string;
    /** Legacy field retained only so receipts created before v0.4 remain recoverable. */
    slides_test?: string;
    preview_directory: string;
    montage: string;
    renderer?: string;
  };
  updated_at: string;
};

function deckReceiptPath(root: string, intentId: string): string {
  if (!/^[A-Za-z0-9-]{1,128}$/u.test(intentId)) throw new Error("PPT 写入意图 ID 无效");
  const requested = resolve(root, ".pi", "director-runtime", "artifact-commits");
  mkdirSync(requested, { recursive: true });
  const meta = lstatSync(requested);
  const canonical = realpathSync.native(requested);
  if (!meta.isDirectory() || meta.isSymbolicLink() || !isContained(root, canonical)) throw new Error("PPT receipt 目录无效");
  return join(canonical, `${intentId}.json`);
}

function acquireDeckIntentLock(root: string, intentId: string): { path: string; descriptor: number } {
  const path = `${deckReceiptPath(root, intentId)}.lock`;
  try {
    return { path, descriptor: acquireLock(path) };
  } catch (error) {
    if (error instanceof Error && error.message.endsWith(path)) {
      throw new DeckNotCommittedError("同一 PPT 写入意图正在由另一个任务处理，请稍后重试");
    }
    throw error;
  }
}

export function holdDeckIntentLockForTests(projectRoot: string, intentId: string): () => void {
  const root = realpathSync.native(resolve(projectRoot));
  const held = acquireDeckIntentLock(root, intentId);
  let released = false;
  return () => {
    if (released) return;
    released = true;
    releaseLock(held.path, held.descriptor);
  };
}

export function readCommittedDeckReceipt(root: string, path: string, commit: DeckCommitContext, outputPath: string): DeckReceipt | undefined {
  if (!existsSync(path)) return undefined;
  const receipt = JSON.parse(readFileSync(path, "utf8")) as DeckReceipt;
  const validationEvidence = receipt.qa?.validation ?? receipt.qa?.slides_test;
  if (
    receipt.schema_version !== "1.0" || receipt.intent_id !== commit.intent_id || receipt.task_id !== commit.task_id ||
    receipt.payload_sha256 !== commit.payload_sha256 || receipt.owner !== "director_artifact_deck_write" ||
    receipt.target !== `outputs/${outputPath.split(/[\\/]/u).at(-1)}` ||
    (receipt.status !== "prepared" && receipt.status !== "committed") ||
    !/^[a-f0-9]{64}$/u.test(receipt.artifact_sha256) ||
    !Number.isInteger(receipt.bytes) || receipt.bytes < 1 || receipt.bytes > MAX_DECK_BYTES ||
    !Number.isInteger(receipt.slide_count) || receipt.slide_count < 4 || receipt.slide_count > 10 ||
    !receipt.qa || typeof validationEvidence !== "string" || !/Test passed\. No overflow detected\./u.test(validationEvidence)
  ) throw new Error("PPT receipt 与当前任务/意图/载荷不一致，需人工恢复");
  const previewDirectory = resolve(root, receipt.qa.preview_directory);
  const montagePath = resolve(root, receipt.qa.montage);
  if (
    !isContained(root, previewDirectory) || !isContained(root, montagePath) ||
    !existsSync(previewDirectory) || !lstatSync(previewDirectory).isDirectory() || lstatSync(previewDirectory).isSymbolicLink() ||
    !existsSync(montagePath) || !lstatSync(montagePath).isFile() || lstatSync(montagePath).isSymbolicLink()
  ) throw new Error("PPT receipt 的 QA 证据缺失或越出项目目录，需人工恢复");
  if (!existsSync(outputPath)) {
    if (receipt.status === "committed") throw new Error("PPT receipt 标记已提交但正式文件缺失，需人工恢复");
    return undefined;
  }
  const meta = lstatSync(outputPath);
  if (!meta.isFile() || meta.isSymbolicLink() || meta.size !== receipt.bytes || binarySha256(outputPath) !== receipt.artifact_sha256) {
    throw new Error("PPT 正式文件与 receipt 哈希不一致，需人工恢复");
  }
  if (receipt.status === "prepared") {
    receipt.status = "committed";
    receipt.updated_at = new Date().toISOString();
    atomicWrite(path, `${JSON.stringify(receipt, null, 2)}\n`);
  }
  return receipt;
}

type DeckPublishProgress = { artifactPublished?: boolean; preserveRecoveryState?: boolean };
type DeckPublishOperations = {
  writeReceipt: (path: string, content: string) => void;
  linkArtifact: (temporaryPath: string, outputPath: string) => void;
};

function removeOwnedPreparedDeckReceipt(path: string, expected: DeckReceipt): boolean {
  if (!existsSync(path)) return true;
  let current: Partial<DeckReceipt>;
  try {
    current = JSON.parse(readFileSync(path, "utf8")) as Partial<DeckReceipt>;
  } catch {
    return false;
  }
  if (
    current.schema_version !== expected.schema_version || current.status !== "prepared" ||
    current.owner !== expected.owner || current.intent_id !== expected.intent_id ||
    current.task_id !== expected.task_id || current.payload_sha256 !== expected.payload_sha256 ||
    current.target !== expected.target || current.artifact_sha256 !== expected.artifact_sha256
  ) return false;
  unlinkSync(path);
  return true;
}

export function publishPreparedDeckArtifactForTests(
  receiptPath: string,
  temporaryDeckPath: string,
  outputPath: string,
  receipt: DeckReceipt,
  onProgress: (progress: DeckPublishProgress) => void,
  operations: DeckPublishOperations = {
    writeReceipt: (path, content) => atomicWrite(path, content),
    linkArtifact: (temporaryPath, targetPath) => linkSync(temporaryPath, targetPath),
  },
): void {
  operations.writeReceipt(receiptPath, `${JSON.stringify(receipt, null, 2)}\n`);
  try {
    operations.linkArtifact(temporaryDeckPath, outputPath);
    onProgress({ artifactPublished: true });
  } catch (error) {
    let removed = false;
    try {
      removed = removeOwnedPreparedDeckReceipt(receiptPath, receipt);
    } catch {
      onProgress({ preserveRecoveryState: true });
      throw new Error("PPT prepared receipt 清理失败，已保留 QA 证据供人工恢复", { cause: error });
    }
    if (!removed) onProgress({ preserveRecoveryState: true });
    if ((error as NodeJS.ErrnoException).code === "EEXIST") {
      throw new DeckNotCommittedError(`PPT 输出名在提交时被其他任务占用，当前意图未提交：${receipt.target}`);
    }
    throw error;
  }
  receipt.status = "committed";
  receipt.updated_at = new Date().toISOString();
  operations.writeReceipt(receiptPath, `${JSON.stringify(receipt, null, 2)}\n`);
}

async function buildWeeklyDeck(projectRoot: string, payload: DeckPayload, commit: DeckCommitContext): Promise<Record<string, unknown>> {
  assertDeckPayload(payload);
  const root = realpathSync.native(resolve(projectRoot));
  assertDeckSourcePaths(root, payload);
  const outputDirectory = resolve(root, "outputs");
  mkdirSync(outputDirectory, { recursive: true });
  const outputMeta = lstatSync(outputDirectory);
  if (!outputMeta.isDirectory() || outputMeta.isSymbolicLink()) throw new Error("outputs/ 必须是项目内普通目录，不能是符号链接");
  const canonicalOutputDirectory = realpathSync.native(outputDirectory);
  if (!isContained(root, canonicalOutputDirectory)) throw new Error("outputs/ 越出项目目录");
  const outputPath = resolve(canonicalOutputDirectory, payload.output_name);
  if (!isContained(canonicalOutputDirectory, outputPath)) throw new Error("PPT 输出路径越出 outputs/");
  const receiptPath = deckReceiptPath(root, commit.intent_id);
  const intentLock = acquireDeckIntentLock(root, commit.intent_id);
  try {
    const recovered = readCommittedDeckReceipt(root, receiptPath, commit, outputPath);
    if (recovered) return {
      path: recovered.target, receipt: relative(root, receiptPath).replaceAll("\\", "/"), sha256: recovered.artifact_sha256,
      bytes: recovered.bytes, slide_count: recovered.slide_count, qa: recovered.qa, recovered: true,
    };
    if (existsSync(outputPath)) {
      throw new DeckNotCommittedError(`PPT 输出文件已存在且不属于当前意图，拒绝覆盖：outputs/${payload.output_name}`);
    }

  const requestedJobDirectory = resolve(root, ".pi", "director-runtime", "deck-jobs");
  mkdirSync(requestedJobDirectory, { recursive: true });
  const jobDirectoryMeta = lstatSync(requestedJobDirectory);
  if (!jobDirectoryMeta.isDirectory() || jobDirectoryMeta.isSymbolicLink()) {
    throw new Error("PPT 临时任务目录必须是项目内普通目录，不能是符号链接");
  }
  const jobDirectory = realpathSync.native(requestedJobDirectory);
  if (!isContained(root, jobDirectory)) throw new Error("PPT 临时任务目录越出项目目录");
  const jobId = `${commit.intent_id}-${randomUUID()}`;
  const ownedJobDirectory = join(jobDirectory, jobId);
  mkdirSync(ownedJobDirectory, { recursive: false });
  const ownedCanonical = realpathSync.native(ownedJobDirectory);
  if (!isContained(jobDirectory, ownedCanonical) || lstatSync(ownedCanonical).isSymbolicLink()) throw new Error("PPT 私有任务目录无效");
  const inputPath = join(ownedCanonical, "payload.json");
  const temporaryDeckPath = join(ownedCanonical, "artifact.pptx");
  const qaDirectory = join(ownedCanonical, "qa");
  const builderPath = resolve(dirname(fileURLToPath(import.meta.url)), "..", "artifacts", "build-director-deck.mjs");
  const qaScript = resolve(dirname(fileURLToPath(import.meta.url)), "..", "artifacts", "validate-and-render-deck.mjs");
  const nodePath = process.execPath;
  if (!existsSync(builderPath)) throw new Error("PPT 构建脚本缺失");
  if (!existsSync(qaScript)) throw new Error("独立 PPT 渲染与 QA 脚本缺失");
  writeFileSync(inputPath, `${JSON.stringify(payload, null, 2)}\n`, { encoding: "utf8", flag: "wx", mode: 0o600 });
  let committed = false;
  let artifactPublished = false;
  let preserveRecoveryState = false;
  try {
    const stdout = await runDeckBuilder(nodePath, builderPath, inputPath, temporaryDeckPath, qaDirectory);
    if (!existsSync(temporaryDeckPath)) throw new Error("PPT 构建器未生成私有临时文件");
    const metadata = lstatSync(temporaryDeckPath);
    if (!metadata.isFile() || metadata.isSymbolicLink() || metadata.size < 1 || metadata.size > MAX_DECK_BYTES) {
      throw new Error("PPT 输出不是有效大小的普通文件");
    }
    const validation = await runDeckQa(nodePath, qaScript, temporaryDeckPath, qaDirectory, payload.slides.length);
    for (let index = 1; index <= payload.slides.length; index += 1) {
      const previewPath = join(qaDirectory, `slide-${String(index).padStart(2, "0")}.png`);
      if (!existsSync(previewPath)) throw new Error(`PPT 第 ${index} 页预览缺失`);
      const previewMeta = lstatSync(previewPath);
      if (!previewMeta.isFile() || previewMeta.isSymbolicLink() || previewMeta.size < 1) {
        throw new Error(`PPT 第 ${index} 页预览无效`);
      }
    }
    const montagePath = join(qaDirectory, "deck-montage.png");
    const montageMeta = lstatSync(montagePath);
    if (!montageMeta.isFile() || montageMeta.isSymbolicLink() || montageMeta.size < 1) {
      throw new Error("PPT 全页预览拼图无效");
    }
    let build: unknown;
    try { build = JSON.parse(stdout.split(/\r?\n/u).at(-1) ?? "{}"); } catch { build = { stdout: stdout.slice(0, 1000) }; }
    let qaResult: { renderer?: unknown } = {};
    try { qaResult = JSON.parse(validation.split(/\r?\n/u).at(-1) ?? "{}"); } catch { /* keep textual QA evidence */ }
    const artifactSha256 = binarySha256(temporaryDeckPath);
    const qa = {
      validation,
      preview_directory: relative(root, qaDirectory).replaceAll("\\", "/"),
      montage: relative(root, montagePath).replaceAll("\\", "/"),
      ...(typeof qaResult.renderer === "string" ? { renderer: qaResult.renderer } : {}),
    };
    const receipt: DeckReceipt = {
      schema_version: "1.0", intent_id: commit.intent_id, task_id: commit.task_id,
      payload_sha256: commit.payload_sha256, owner: "director_artifact_deck_write",
      target: `outputs/${payload.output_name}`, status: "prepared", artifact_sha256: artifactSha256,
      bytes: metadata.size, slide_count: payload.slides.length, qa, updated_at: new Date().toISOString(),
    };
    publishPreparedDeckArtifactForTests(
      receiptPath,
      temporaryDeckPath,
      outputPath,
      receipt,
      (progress) => {
        if (progress.artifactPublished) artifactPublished = true;
        if (progress.preserveRecoveryState) preserveRecoveryState = true;
      },
    );
    committed = true;
    return {
      path: `outputs/${payload.output_name}`,
      receipt: relative(root, receiptPath).replaceAll("\\", "/"),
      intent_id: commit.intent_id,
      payload_sha256: commit.payload_sha256,
      sha256: artifactSha256,
      bytes: metadata.size,
      slide_count: payload.slides.length,
      build,
      qa,
    };
  } catch (error) {
    throw error;
  } finally {
    if (committed) {
      if (existsSync(inputPath)) unlinkSync(inputPath);
      if (existsSync(temporaryDeckPath)) unlinkSync(temporaryDeckPath);
    } else if (!artifactPublished && !preserveRecoveryState && existsSync(ownedCanonical)) {
      rmSync(ownedCanonical, { recursive: true, force: true });
    }
  }
  } finally {
    releaseLock(intentLock.path, intentLock.descriptor);
  }
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
    if (definition === KNOWLEDGE_DEFINITION) validateKnowledgeRecords(table.rows);
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

type TaskEvidenceState = {
  schema_version: "1.0";
  task_id: string;
  searched_urls: string[];
  mutations: Record<string, string>;
  presentation_sources?: Record<string, PresentationEvidenceSource>;
  weekly_snapshot?: WeeklySnapshotEvidence;
  updated_at: string;
};

export type PresentationEvidenceSource = {
  source_id: string;
  title: string;
  source_type: string;
  url?: string;
  path?: string;
  content_sha256?: string;
  page_count?: number;
  extracted_pages?: number[];
  reliability: "standard" | "limited";
  accessed_at: string;
};

export type WeeklySnapshotEvidence = {
  profile_id: string;
  period: { start: string; end: string };
  snapshot_sha256: string;
  allowed_urls: string[];
  source_versions: Array<{ path: string; sha256: string }>;
};

function canonicalEvidence(value: unknown): string {
  if (value === null || typeof value === "string" || typeof value === "boolean") return JSON.stringify(value);
  if (typeof value === "number" && Number.isFinite(value)) return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalEvidence).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.entries(value as Record<string, unknown>)
      .filter(([, child]) => child !== undefined)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, child]) => `${JSON.stringify(key)}:${canonicalEvidence(child)}`)
      .join(",")}}`;
  }
  throw new Error("证据 mutation 必须是有限 JSON 数据");
}

function taskEvidencePath(projectRoot: string, taskId: string): string {
  if (!/^[A-Za-z0-9_-]{1,128}$/u.test(taskId)) throw new Error("证据 registry 的 task_id 无效");
  const root = realpathSync.native(resolve(projectRoot));
  const requested = resolve(root, ".pi", "director-runtime", "evidence");
  mkdirSync(requested, { recursive: true });
  const meta = lstatSync(requested);
  const canonical = realpathSync.native(requested);
  if (!meta.isDirectory() || meta.isSymbolicLink() || !isContained(root, canonical)) throw new Error("证据 registry 目录无效");
  return join(canonical, `${taskId}.json`);
}

function readTaskEvidence(projectRoot: string, taskId: string): TaskEvidenceState {
  const path = taskEvidencePath(projectRoot, taskId);
  if (!existsSync(path)) {
    return { schema_version: "1.0", task_id: taskId, searched_urls: [], mutations: {}, presentation_sources: {}, updated_at: new Date().toISOString() };
  }
  const meta = lstatSync(path);
  if (!meta.isFile() || meta.isSymbolicLink() || meta.size > 2 * 1024 * 1024) throw new Error("证据 registry 文件无效或过大");
  const state = JSON.parse(readFileSync(path, "utf8")) as TaskEvidenceState;
  if (
    state.schema_version !== "1.0" || state.task_id !== taskId ||
    !Array.isArray(state.searched_urls) || state.searched_urls.length > 500 ||
    state.searched_urls.some((url) => typeof url !== "string" || url.length > 2048) ||
    !state.mutations || typeof state.mutations !== "object" || Array.isArray(state.mutations) ||
    Object.keys(state.mutations).length > 500 || Object.values(state.mutations).some((value) => typeof value !== "string") ||
    (state.presentation_sources !== undefined && (
      !state.presentation_sources || typeof state.presentation_sources !== "object" || Array.isArray(state.presentation_sources) ||
      Object.keys(state.presentation_sources).length > 1000 ||
      Object.entries(state.presentation_sources).some(([sourceId, source]) => (
        !source || typeof source !== "object" || source.source_id !== sourceId ||
        typeof source.title !== "string" || !source.title.trim() || source.title.length > 500 ||
        typeof source.source_type !== "string" || !source.source_type.trim() || source.source_type.length > 80 ||
        (source.url !== undefined && typeof source.url !== "string") ||
        (source.path !== undefined && typeof source.path !== "string") ||
        (source.content_sha256 !== undefined && !/^[a-f0-9]{64}$/u.test(source.content_sha256)) ||
        (source.page_count !== undefined && (!Number.isInteger(source.page_count) || source.page_count < 1 || source.page_count > 100000)) ||
        (source.extracted_pages !== undefined && (
          !Array.isArray(source.extracted_pages) || source.extracted_pages.length > 200 ||
          new Set(source.extracted_pages).size !== source.extracted_pages.length ||
          source.extracted_pages.some((page) => !Number.isInteger(page) || page < 1 || page > (source.page_count ?? 100000))
        )) ||
        (source.reliability !== "standard" && source.reliability !== "limited") ||
        typeof source.accessed_at !== "string"
      ))
    )) ||
    (state.weekly_snapshot !== undefined && (
      !state.weekly_snapshot || typeof state.weekly_snapshot !== "object" ||
      typeof state.weekly_snapshot.profile_id !== "string" ||
      !state.weekly_snapshot.period || typeof state.weekly_snapshot.period.start !== "string" || typeof state.weekly_snapshot.period.end !== "string" ||
      !/^[a-f0-9]{64}$/u.test(state.weekly_snapshot.snapshot_sha256) ||
      !Array.isArray(state.weekly_snapshot.allowed_urls) || state.weekly_snapshot.allowed_urls.length > 1000 ||
      state.weekly_snapshot.allowed_urls.some((url) => typeof url !== "string" || url.length > 2048) ||
      !Array.isArray(state.weekly_snapshot.source_versions) || state.weekly_snapshot.source_versions.length > 1000 ||
      state.weekly_snapshot.source_versions.some((source) => !source || typeof source.path !== "string" || !/^[a-f0-9]{64}$/u.test(source.sha256))
    ))
  ) throw new Error("证据 registry 内容无效");
  return state;
}

function writeTaskEvidence(projectRoot: string, state: TaskEvidenceState): void {
  state.updated_at = new Date().toISOString();
  const content = `${JSON.stringify(state, null, 2)}\n`;
  if (Buffer.byteLength(content, "utf8") > 2 * 1024 * 1024) throw new Error("证据 registry 超过 2 MiB 安全上限");
  atomicWrite(taskEvidencePath(projectRoot, state.task_id), content);
}

type PresentationPlanInput = {
  schema_version: "1.0";
  project_id: string;
  profile_id: "market-director" | "product-director";
  scene: "weekly" | "industry" | "government" | "custom";
  mode: "quick" | "standard" | "strict";
  phase: "outline" | "final";
  version: number;
  expected_plan_sha256?: string;
  expected_context_snapshot_sha256?: string;
  period: { start: string; end: string };
  brief: {
    topic: string;
    audience: string;
    purpose: string;
    occasion: string;
    language: string;
    confidentiality: "internal" | "restricted" | "public";
    target_slides: number;
    expected_decision?: string;
    duration_minutes?: number;
  };
  evidence_refs: string[];
  outline: Array<{
    slide_id: string;
    order: number;
    conclusion_title: string;
    evidence_refs: string[];
  }>;
  slides?: Array<{
    slide_id: string;
    order: number;
    conclusion_title: string;
    audience_takeaway: string;
    facts: Array<{ text: string; evidence_refs: string[] }>;
    analyses: string[];
    hypotheses: string[];
    unknowns: string[];
    evidence_refs: string[];
    layout_intent: "single-focus" | "fifty-fifty" | "two-thirds" | "three-column" | "top-hero" | "mixed-grid";
    visual_assets: string[];
    speaker_notes: string;
    warnings: string[];
    render: DeckPayload["slides"][number];
  }>;
  design_system: {
    token_id: "management-report" | "government-program" | "technology-research";
  };
  output_name: string;
};

export type StoredPresentationPlan = Omit<PresentationPlanInput, "expected_plan_sha256" | "expected_context_snapshot_sha256"> & {
  task_id: string;
  context_snapshot_sha256: string;
  plan_sha256: string;
  updated_at: string;
};

function assertPlanText(value: unknown, field: string, maximum: number, allowEmpty = false): asserts value is string {
  if (typeof value !== "string" || (!allowEmpty && !value.trim()) || value.length > maximum || /[\u0000-\u0008\u000B\u000C\u000E-\u001F]/u.test(value)) {
    throw new Error(`${field} 无效或超过 ${maximum} 字符`);
  }
  if (/\[Sources\]/iu.test(value)) throw new Error(`${field} 不能伪造 speaker notes 来源块`);
}

function assertPlanId(value: unknown, field: string): asserts value is string {
  if (typeof value !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/u.test(value)) throw new Error(`${field} 必须是安全 ID`);
}

function presentationPlanPath(projectRoot: string, taskId: string): string {
  assertPlanId(taskId, "presentation plan task_id");
  const root = realpathSync.native(resolve(projectRoot));
  const requested = resolve(root, ".pi", "director-runtime", "presentation-plans");
  mkdirSync(requested, { recursive: true });
  const meta = lstatSync(requested);
  const canonical = realpathSync.native(requested);
  if (!meta.isDirectory() || meta.isSymbolicLink() || !isContained(root, canonical)) throw new Error("PPT plan 目录无效");
  return join(canonical, `${taskId}.json`);
}

export function readPresentationPlan(projectRoot: string, taskId: string): StoredPresentationPlan | undefined {
  const path = presentationPlanPath(projectRoot, taskId);
  if (!existsSync(path)) return undefined;
  const meta = lstatSync(path);
  if (!meta.isFile() || meta.isSymbolicLink() || meta.size > 2 * 1024 * 1024) throw new Error("PPT plan 文件无效或过大");
  const plan = JSON.parse(readFileSync(path, "utf8")) as StoredPresentationPlan;
  if (
    plan.schema_version !== "1.0" || plan.task_id !== taskId ||
    !Number.isInteger(plan.version) || plan.version < 1 ||
    !/^[a-f0-9]{64}$/u.test(plan.plan_sha256) ||
    !/^[a-f0-9]{64}$/u.test(plan.context_snapshot_sha256)
  ) throw new Error("PPT plan 持久化内容无效");
  const { plan_sha256: storedHash, updated_at: _updatedAt, ...hashBase } = plan;
  const computedHash = createHash("sha256").update(canonicalEvidence(hashBase), "utf8").digest("hex");
  if (computedHash !== storedHash) throw new Error("PPT plan 文件内容与 plan_sha256 不一致，拒绝使用未受控修改");
  return plan;
}

function sourceLocationMatches(
  rendered: NonNullable<DeckPayload["slides"][number]["sources"]>[number],
  source: PresentationEvidenceSource,
): boolean {
  if (rendered.title !== source.title) return false;
  if (source.source_type === "pdf") {
    if (rendered.page === undefined || !source.extracted_pages?.includes(rendered.page)) return false;
  } else if (rendered.page !== undefined) return false;
  if (source.url) {
    try { return rendered.url === normalizePublicUrl(source.url).toString() && rendered.path === undefined && rendered.sha256 === undefined; }
    catch { return false; }
  }
  return Boolean(source.path && rendered.path === source.path && rendered.sha256 === source.content_sha256 && rendered.url === undefined);
}

export function sourceLocationMatchesForTests(
  rendered: NonNullable<DeckPayload["slides"][number]["sources"]>[number],
  source: PresentationEvidenceSource,
): boolean {
  return sourceLocationMatches(rendered, source);
}

function normalizedPresentationContext(
  projectRoot: string,
  taskId: string,
  evidenceRefs: string[],
): { sources: PresentationEvidenceSource[]; sha256: string } {
  const registry = readTaskEvidence(projectRoot, taskId).presentation_sources ?? {};
  if (!Array.isArray(evidenceRefs) || evidenceRefs.length < 1 || evidenceRefs.length > 200 || new Set(evidenceRefs).size !== evidenceRefs.length) {
    throw new Error("PPT plan evidence_refs 必须包含 1-200 个不重复来源");
  }
  const root = realpathSync.native(resolve(projectRoot));
  const sources = evidenceRefs.map((sourceId) => {
    assertPlanId(sourceId, "PPT evidence source_id");
    const source = registry[sourceId];
    if (!source) throw new Error(`PPT 来源 ${sourceId} 不在当前任务证据 registry 中`);
    if (source.url) {
      const normalized = normalizePublicUrl(source.url).toString();
      if (normalized !== source.url) throw new Error(`PPT 来源 ${sourceId} URL 未规范化`);
    } else if (source.path) {
      if (!source.content_sha256) throw new Error(`PPT 本地来源 ${sourceId} 缺少 SHA-256`);
      const path = resolve(root, source.path);
      if (!isContained(root, path) || !existsSync(path)) throw new Error(`PPT 本地来源 ${sourceId} 不存在或越出项目目录`);
      const meta = lstatSync(path);
      const canonical = realpathSync.native(path);
      if (!meta.isFile() || meta.isSymbolicLink() || meta.size > MAX_WEEKLY_SOURCE_BYTES || !isContained(root, canonical)) {
        throw new Error(`PPT 本地来源 ${sourceId} 不是受控普通文件`);
      }
      if (binarySha256(canonical) !== source.content_sha256) throw new Error(`PPT 本地来源 ${sourceId} 自登记后已变化`);
    } else {
      throw new Error(`PPT 来源 ${sourceId} 缺少 URL 或项目相对路径`);
    }
    return source;
  }).sort((left, right) => left.source_id.localeCompare(right.source_id));
  const canonical = canonicalEvidence(sources);
  return { sources, sha256: createHash("sha256").update(canonical, "utf8").digest("hex") };
}

function validatePresentationPlan(
  projectRoot: string,
  taskId: string,
  profileId: string,
  value: PresentationPlanInput,
): { sources: PresentationEvidenceSource[]; contextSha256: string; normalized: Omit<PresentationPlanInput, "expected_plan_sha256" | "expected_context_snapshot_sha256"> } {
  if (value.schema_version !== "1.0") throw new Error("PPT plan schema_version 必须为 1.0");
  assertPlanId(value.project_id, "PPT project_id");
  if (value.profile_id !== profileId || !new Set(["market-director", "product-director"]).has(value.profile_id)) {
    throw new Error("PPT plan Profile 必须与当前受管任务一致");
  }
  if (!new Set(["weekly", "industry", "government", "custom"]).has(value.scene)) throw new Error("PPT scene 无效");
  if (!new Set(["quick", "standard", "strict"]).has(value.mode)) throw new Error("PPT mode 无效");
  if (value.phase !== "outline" && value.phase !== "final") throw new Error("PPT phase 无效");
  if (!Number.isInteger(value.version) || value.version < 1 || value.version > 10000) throw new Error("PPT plan version 无效");
  parsePeriod(value.period);
  for (const [field, maximum] of [["topic", 240], ["audience", 240], ["purpose", 500], ["occasion", 240], ["language", 40]] as const) {
    assertPlanText(value.brief?.[field], `PPT brief.${field}`, maximum);
  }
  if (!new Set(["internal", "restricted", "public"]).has(value.brief?.confidentiality)) throw new Error("PPT 保密等级无效");
  if (!Number.isInteger(value.brief?.target_slides) || value.brief.target_slides < 4 || value.brief.target_slides > 10) throw new Error("PPT target_slides 必须为 4-10");
  if (value.brief.expected_decision !== undefined) assertPlanText(value.brief.expected_decision, "PPT expected_decision", 500, true);
  if (value.brief.duration_minutes !== undefined && (!Number.isInteger(value.brief.duration_minutes) || value.brief.duration_minutes < 1 || value.brief.duration_minutes > 240)) {
    throw new Error("PPT duration_minutes 必须为 1-240");
  }
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.pptx$/u.test(value.output_name)) throw new Error("PPT plan output_name 必须是安全 ASCII .pptx 文件名");
  if (!new Set(["management-report", "government-program", "technology-research"]).has(value.design_system?.token_id)) throw new Error("PPT design token 无效");

  const context = normalizedPresentationContext(projectRoot, taskId, value.evidence_refs);
  if (value.expected_context_snapshot_sha256 !== undefined && value.expected_context_snapshot_sha256 !== context.sha256) {
    throw new Error("PPT 证据上下文已变化，请重新检查 plan");
  }
  if (!Array.isArray(value.outline) || value.outline.length < 4 || value.outline.length > 10 || value.outline.length !== value.brief.target_slides) {
    throw new Error("PPT outline 必须与 4-10 页 target_slides 一致");
  }
  const sourceIds = new Set(context.sources.map((source) => source.source_id));
  const outlineIds = new Set<string>();
  for (const [index, item] of value.outline.entries()) {
    assertPlanId(item.slide_id, `PPT outline[${index}].slide_id`);
    if (outlineIds.has(item.slide_id)) throw new Error("PPT outline slide_id 不能重复");
    outlineIds.add(item.slide_id);
    if (item.order !== index + 1) throw new Error("PPT outline order 必须从 1 连续递增");
    assertPlanText(item.conclusion_title, `PPT outline[${index}].conclusion_title`, 120);
    if (!Array.isArray(item.evidence_refs) || new Set(item.evidence_refs).size !== item.evidence_refs.length || item.evidence_refs.some((id) => !sourceIds.has(id))) {
      throw new Error(`PPT outline 第 ${index + 1} 页包含未登记或重复证据`);
    }
  }
  if (value.phase === "outline" && value.slides !== undefined && value.slides.length > 0) throw new Error("outline 阶段不得混入未确认的逐页策划");
  if (value.phase === "final") {
    if (!Array.isArray(value.slides) || value.slides.length !== value.outline.length) throw new Error("final plan slides 必须与 outline 一一对应");
    for (const [index, slide] of value.slides.entries()) {
      const outline = value.outline[index]!;
      if (slide.slide_id !== outline.slide_id || slide.order !== index + 1 || slide.conclusion_title !== outline.conclusion_title) {
        throw new Error(`PPT 第 ${index + 1} 页与已确认 outline 不一致`);
      }
      assertPlanText(slide.audience_takeaway, `PPT 第 ${index + 1} 页 audience_takeaway`, 500);
      if (!Array.isArray(slide.evidence_refs) || new Set(slide.evidence_refs).size !== slide.evidence_refs.length || slide.evidence_refs.some((id) => !sourceIds.has(id))) {
        throw new Error(`PPT 第 ${index + 1} 页包含未登记或重复证据`);
      }
      const slideEvidence = new Set(slide.evidence_refs);
      if (!Array.isArray(slide.facts) || slide.facts.length > 20) throw new Error(`PPT 第 ${index + 1} 页 facts 无效`);
      for (const fact of slide.facts) {
        assertPlanText(fact.text, `PPT 第 ${index + 1} 页 fact`, 500);
        if (!Array.isArray(fact.evidence_refs) || fact.evidence_refs.length < 1 || fact.evidence_refs.some((id) => !slideEvidence.has(id))) {
          throw new Error(`PPT 第 ${index + 1} 页事实缺少当前页证据`);
        }
      }
      for (const [field, maximum] of [["analyses", 20], ["hypotheses", 20], ["unknowns", 20], ["visual_assets", 20], ["warnings", 20]] as const) {
        const items = slide[field];
        if (!Array.isArray(items) || items.length > maximum) throw new Error(`PPT 第 ${index + 1} 页 ${field} 无效`);
        items.forEach((item) => assertPlanText(item, `PPT 第 ${index + 1} 页 ${field}`, 500, true));
      }
      if (!new Set(["single-focus", "fifty-fifty", "two-thirds", "three-column", "top-hero", "mixed-grid"]).has(slide.layout_intent)) throw new Error(`PPT 第 ${index + 1} 页 layout_intent 无效`);
      if (slide.render.layout_intent !== slide.layout_intent) throw new Error(`PPT 第 ${index + 1} 页 render.layout_intent 必须与逐页策划一致`);
      assertPlanText(slide.speaker_notes, `PPT 第 ${index + 1} 页 speaker_notes`, 4000, true);
      if (slide.render.title !== slide.conclusion_title) throw new Error(`PPT 第 ${index + 1} 页 render.title 必须与已确认结论标题一致`);
      if ((slide.render.notes ?? "") !== slide.speaker_notes) throw new Error(`PPT 第 ${index + 1} 页 render.notes 必须与 speaker_notes 一致`);
      const allowedRenderText = new Set([
        slide.audience_takeaway,
        value.brief.topic,
        value.brief.purpose,
        value.brief.occasion,
        ...(value.brief.expected_decision ? [value.brief.expected_decision] : []),
        ...slide.facts.map((fact) => fact.text),
        ...slide.analyses.map((text) => `分析：${text}`),
        ...slide.hypotheses.map((text) => `假设：${text}`),
        ...slide.unknowns.map((text) => `未知：${text}`),
        ...slide.warnings.map((text) => `风险：${text}`),
      ]);
      for (const [field, texts] of [
        ["subtitle", slide.render.subtitle ? [slide.render.subtitle] : []],
        ["lead", slide.render.lead ? [slide.render.lead] : []],
        ["body", slide.render.body ?? []],
        ["callout", slide.render.callout ? [slide.render.callout] : []],
      ] as const) {
        if (texts.some((text) => !allowedRenderText.has(text))) {
          throw new Error(`PPT 第 ${index + 1} 页 render.${field} 包含未映射到策划事实/判断/假设/未知的信息`);
        }
      }
      assertDeckPayload({
        schema_version: "1.0", snapshot_sha256: context.sha256, output_name: value.output_name,
        profile_id: value.profile_id, template_id: value.design_system.token_id, period: value.period,
        slides: value.slides.map((candidate) => candidate.render),
      });
      const renderedSources = slide.render.sources ?? [];
      for (const evidenceId of slideEvidence) {
        const source = context.sources.find((candidate) => candidate.source_id === evidenceId)!;
        if (!renderedSources.some((rendered) => sourceLocationMatches(rendered, source))) {
          throw new Error(`PPT 第 ${index + 1} 页 render.sources 缺少证据 ${evidenceId}`);
        }
      }
      if (renderedSources.some((rendered) => !context.sources.some((source) => slideEvidence.has(source.source_id) && sourceLocationMatches(rendered, source)))) {
        throw new Error(`PPT 第 ${index + 1} 页 render.sources 包含未登记来源`);
      }
    }
  }
  const { expected_plan_sha256: _ignoredPlan, expected_context_snapshot_sha256: _ignoredContext, ...normalized } = value;
  return { sources: context.sources, contextSha256: context.sha256, normalized };
}

export function writePresentationPlan(
  projectRoot: string,
  taskId: string,
  profileId: string,
  value: PresentationPlanInput,
): StoredPresentationPlan {
  const path = presentationPlanPath(projectRoot, taskId);
  const lockPath = `${path}.lock`;
  const descriptor = acquireLock(lockPath);
  try {
    const existing = readPresentationPlan(projectRoot, taskId);
    if (existing) {
      if (existing.task_id !== taskId || existing.profile_id !== profileId || existing.project_id !== value.project_id) throw new Error("PPT plan 不能跨任务、Profile 或 project 接管");
      if (value.version !== existing.version + 1) throw new Error(`PPT plan 版本冲突：期望 ${existing.version + 1}`);
      if (value.expected_plan_sha256 !== existing.plan_sha256) throw new Error("PPT plan expected_plan_sha256 与当前版本不一致");
      if (value.expected_context_snapshot_sha256 !== existing.context_snapshot_sha256) throw new Error("PPT plan 更新必须绑定前一版 context_snapshot_sha256");
      if (existing.phase !== "outline" || value.phase !== "final") throw new Error("同一任务只允许从已确认 outline 生成一次 final plan；其他修订必须新建任务并重新确认");
      const confirmedExisting = {
        project_id: existing.project_id, profile_id: existing.profile_id, scene: existing.scene, mode: existing.mode,
        period: existing.period, brief: existing.brief, evidence_refs: existing.evidence_refs,
        outline: existing.outline, output_name: existing.output_name,
      };
      const confirmedIncoming = {
        project_id: value.project_id, profile_id: value.profile_id, scene: value.scene, mode: value.mode,
        period: value.period, brief: value.brief, evidence_refs: value.evidence_refs,
        outline: value.outline, output_name: value.output_name,
      };
      if (canonicalEvidence(confirmedIncoming) !== canonicalEvidence(confirmedExisting)) {
        throw new Error("final plan 改变了已确认的 brief、outline、证据、周期或输出名；请新建修订任务并重新进行 outline Approval");
      }
    } else if (value.version !== 1 || value.expected_plan_sha256 !== undefined) {
      throw new Error("新 PPT plan 必须从 version=1 开始且不能携带旧 plan 哈希");
    }
    const validated = validatePresentationPlan(projectRoot, taskId, profileId, value);
    const hashBase = { ...validated.normalized, task_id: taskId, context_snapshot_sha256: validated.contextSha256 };
    const plan: StoredPresentationPlan = {
      ...validated.normalized,
      task_id: taskId,
      context_snapshot_sha256: validated.contextSha256,
      plan_sha256: createHash("sha256").update(canonicalEvidence(hashBase), "utf8").digest("hex"),
      updated_at: new Date().toISOString(),
    };
    const content = `${JSON.stringify(plan, null, 2)}\n`;
    if (Buffer.byteLength(content, "utf8") > 2 * 1024 * 1024) throw new Error("PPT plan 超过 2 MiB 安全上限");
    atomicWrite(path, content);
    return plan;
  } finally {
    releaseLock(lockPath, descriptor);
  }
}

export function assertDeckMatchesWeeklySnapshot(
  projectRoot: string,
  taskId: string,
  profileId: string,
  payload: DeckPayload,
): void {
  assertDeckPayload(payload);
  const evidenceState = readTaskEvidence(projectRoot, taskId);
  const snapshot = evidenceState.weekly_snapshot;
  if (!snapshot) throw new Error("当前任务缺少持久化 weekly.snapshot 证据；请重新执行周报快照");
  if (payload.profile_id !== profileId || snapshot.profile_id !== profileId) {
    throw new Error("PPT 载荷、周报快照与当前受管任务的 Profile 必须一致");
  }
  if (payload.period.start !== snapshot.period.start || payload.period.end !== snapshot.period.end) {
    throw new Error("PPT 载荷周期必须与当前 weekly.snapshot 周期完全一致");
  }
  if (payload.snapshot_sha256 !== snapshot.snapshot_sha256) {
    throw new Error("PPT 载荷 snapshot_sha256 与当前 weekly.snapshot 不一致");
  }
  const allowedUrls = new Set(snapshot.allowed_urls);
  const allowedPaths = new Map(snapshot.source_versions.map((source) => [source.path, source.sha256]));
  for (const [index, slide] of payload.slides.entries()) {
    for (const source of slide.sources ?? []) {
      if (source.url) {
        const normalized = normalizePublicUrl(source.url).toString();
        if (normalized !== source.url || !allowedUrls.has(normalized)) {
          throw new Error(`PPT 第 ${index + 1} 页 URL 来源不在当前 weekly.snapshot 证据中`);
        }
      }
      if (source.path) {
        const normalizedPath = source.path.replaceAll("\\", "/");
        if (normalizedPath !== source.path || allowedPaths.get(normalizedPath) !== source.sha256) {
          throw new Error(`PPT 第 ${index + 1} 页本地来源路径或 SHA-256 不在当前 weekly.snapshot 证据中`);
        }
      }
      if (source.page !== undefined) {
        const registeredSources = Object.values(evidenceState.presentation_sources ?? {});
        if (!registeredSources.some((registered) => sourceLocationMatches(source, registered))) {
          throw new Error(`PPT 第 ${index + 1} 页页码不在本任务实际提取的 PDF 页中`);
        }
      }
    }
  }
}

export function assertDeckMatchesPresentationPlan(
  projectRoot: string,
  taskId: string,
  profileId: string,
  payload: DeckPayload,
): void {
  assertDeckPayload(payload);
  if (!payload.plan_sha256) throw new Error("通用 PPT 载荷缺少 plan_sha256");
  const plan = readPresentationPlan(projectRoot, taskId);
  if (!plan || plan.phase !== "final" || !plan.slides) throw new Error("当前任务缺少可渲染的 final presentation plan");
  if (plan.profile_id !== profileId || payload.profile_id !== profileId) throw new Error("PPT plan、载荷与当前受管任务的 Profile 必须一致");
  if (payload.plan_sha256 !== plan.plan_sha256) throw new Error("PPT plan_sha256 与当前 plan 不一致");
  if (payload.snapshot_sha256 !== plan.context_snapshot_sha256) throw new Error("PPT snapshot_sha256 与当前 plan 证据上下文不一致");
  if (payload.period.start !== plan.period.start || payload.period.end !== plan.period.end) throw new Error("PPT period 与当前 plan 不一致");
  if (payload.output_name !== plan.output_name || payload.template_id !== plan.design_system.token_id) throw new Error("PPT 输出名或设计令牌与当前 plan 不一致");
  const plannedSlides = plan.slides.map((slide) => slide.render);
  if (canonicalEvidence(payload.slides) !== canonicalEvidence(plannedSlides)) throw new Error("PPT slides 与当前 final plan 的逐页渲染载荷不一致");
  const context = normalizedPresentationContext(projectRoot, taskId, plan.evidence_refs);
  if (context.sha256 !== plan.context_snapshot_sha256) throw new Error("PPT plan 的证据 registry 或本地文件在 Approval 后已变化");
}

export function assertDeckMatchesEvidenceContext(
  projectRoot: string,
  taskId: string,
  profileId: string,
  payload: DeckPayload,
): void {
  if (payload.plan_sha256) {
    assertDeckMatchesPresentationPlan(projectRoot, taskId, profileId, payload);
    return;
  }
  assertDeckMatchesWeeklySnapshot(projectRoot, taskId, profileId, payload);
}

export function registerDataAdapters(pi: ExtensionAPI, hooks: AdapterHooks): void {
  const searchableUrls = new Map<string, Set<string>>();
  const evidenceRegistry = new Map<string, Map<string, string>>();
  const presentationSourceRegistry = new Map<string, Map<string, PresentationEvidenceSource>>();
  const weeklySnapshotRegistry = new Map<string, WeeklySnapshotEvidence>();
  const loadedEvidence = new Set<string>();
  const taskKey = (context: { task_id?: string } | void): string => context?.task_id ?? "__standalone__";
  const loadEvidence = (context: { task_id?: string } | void): { urls: Set<string>; mutations: Map<string, string>; sources: Map<string, PresentationEvidenceSource> } => {
    const key = taskKey(context);
    if (key !== "__standalone__" && !loadedEvidence.has(key)) {
      const persisted = readTaskEvidence(hooks.projectRoot(), key);
      searchableUrls.set(key, new Set(persisted.searched_urls));
      evidenceRegistry.set(key, new Map(Object.entries(persisted.mutations)));
      presentationSourceRegistry.set(key, new Map(Object.entries(persisted.presentation_sources ?? {})));
      if (persisted.weekly_snapshot) weeklySnapshotRegistry.set(key, persisted.weekly_snapshot);
      loadedEvidence.add(key);
    }
    const urls = searchableUrls.get(key) ?? new Set<string>();
    const mutations = evidenceRegistry.get(key) ?? new Map<string, string>();
    const sources = presentationSourceRegistry.get(key) ?? new Map<string, PresentationEvidenceSource>();
    searchableUrls.set(key, urls);
    evidenceRegistry.set(key, mutations);
    presentationSourceRegistry.set(key, sources);
    return { urls, mutations, sources };
  };
  const saveEvidence = (context: { task_id?: string } | void): void => {
    const key = taskKey(context);
    if (key === "__standalone__") return;
    const loaded = loadEvidence(context);
    writeTaskEvidence(hooks.projectRoot(), {
      schema_version: "1.0",
      task_id: key,
      searched_urls: [...loaded.urls].sort(),
      mutations: Object.fromEntries([...loaded.mutations.entries()].sort(([left], [right]) => left.localeCompare(right))),
      presentation_sources: Object.fromEntries([...loaded.sources.entries()].sort(([left], [right]) => left.localeCompare(right))),
      weekly_snapshot: weeklySnapshotRegistry.get(key),
      updated_at: new Date().toISOString(),
    });
  };

  pi.registerTool({
    name: "director_weekly_snapshot",
    label: "读取本周事实快照",
    description: "按明确周期只读聚合任务审计、销售台账、outputs 元数据和知识库新增来源；每项保留来源版本与 SHA-256。",
    parameters: Type.Object({
      period: Type.Object({
        start: Type.String({ pattern: "^\\d{4}-\\d{2}-\\d{2}$" }),
        end: Type.String({ pattern: "^\\d{4}-\\d{2}-\\d{2}$" }),
      }),
      profile_id: Type.Optional(Type.Union([Type.Literal("market-director"), Type.Literal("product-director")])),
    }),
    async execute(_toolCallId, params) {
      const context = hooks.beforeLogicalTool("weekly.snapshot", params);
      if (context?.profile_id && params.profile_id && context.profile_id !== params.profile_id) {
        throw new Error("周报快照 Profile 必须与当前受管任务一致");
      }
      const profileId = context?.profile_id ?? params.profile_id;
      if (!profileId) throw new Error("周报快照缺少受管 Profile 上下文");
      const result = collectWeeklySnapshot(hooks.projectRoot(), params.period, profileId);
      if (!context?.task_id) throw new Error("周报快照缺少受管任务上下文");
      const snapshot = result as {
        snapshot_sha256: string;
        period: { start: string; end: string };
        knowledge: Array<Record<string, unknown>>;
        source_versions: Array<{ source_id?: string; path: string; sha256: string }>;
      };
      const allowedUrls = snapshot.knowledge.flatMap((row) => {
        if (typeof row.url !== "string" || !row.url.trim()) return [];
        try { return [normalizePublicUrl(row.url).toString()]; } catch { return []; }
      });
      const loaded = loadEvidence(context);
      for (const source of snapshot.source_versions) {
        const sourceId = `snapshot-${createHash("sha256").update(`${source.path}\n${source.sha256}`, "utf8").digest("hex").slice(0, 20)}`;
        loaded.sources.set(sourceId, {
          source_id: sourceId,
          title: source.path,
          source_type: source.source_id?.split(":", 1)[0] || "snapshot",
          path: source.path,
          content_sha256: source.sha256,
          reliability: "standard",
          accessed_at: new Date().toISOString(),
        });
      }
      for (const row of snapshot.knowledge) {
        if (typeof row.source_id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/u.test(row.source_id)) continue;
        if (typeof row.url !== "string" || !row.url.trim()) continue;
        try {
          const notes = typeof row.notes === "string" ? row.notes : "";
          const evidenceRefsText = /(?:^|;\s*)evidence_refs=([^;]+)/u.exec(notes)?.[1] ?? "";
          const extractedPages = [...evidenceRefsText.matchAll(/#page=(\d+)/gu)]
            .map((match) => Number(match[1]))
            .filter((page, index, pages) => Number.isInteger(page) && page >= 1 && page <= 100000 && pages.indexOf(page) === index);
          const pageCountValue = Number(/(?:^|;\s*)total_pages=(\d+)(?:;|$)/u.exec(notes)?.[1] ?? "");
          loaded.sources.set(row.source_id, {
            source_id: row.source_id,
            title: typeof row.title === "string" ? row.title.slice(0, 500) : row.source_id,
            source_type: typeof row.source_type === "string" ? row.source_type.slice(0, 80) : "knowledge",
            url: normalizePublicUrl(row.url).toString(),
            ...(row.source_type === "pdf" && Number.isInteger(pageCountValue) && pageCountValue >= 1 && pageCountValue <= 100000
              ? { page_count: pageCountValue }
              : {}),
            ...(row.source_type === "pdf" ? { extracted_pages: extractedPages } : {}),
            reliability: row.status === "verified" ? "standard" : "limited",
            accessed_at: new Date().toISOString(),
          });
        } catch {
          // An invalid historic URL remains in the weekly snapshot but is not eligible for a new deck plan.
        }
      }
      weeklySnapshotRegistry.set(context.task_id, {
        profile_id: profileId,
        period: snapshot.period,
        snapshot_sha256: snapshot.snapshot_sha256,
        allowed_urls: [...new Set(allowedUrls)].sort(),
        source_versions: snapshot.source_versions.map((source) => ({ path: source.path, sha256: source.sha256 })),
      });
      saveEvidence(context);
      assertResultSize(result);
      hooks.afterLogicalTool("weekly.snapshot", params, result);
      return { content: content(result), details: result };
    },
  });

  pi.registerTool({
    name: "director_web_search",
    label: "检索公开网页",
    description: "按广泛发现、官方资料、中文政策或近期信息场景检索公开来源。无专用密钥时使用官方免密公共接口；摘要只用于发现，必须继续读取正文核验。",
    parameters: Type.Object({
      queries: Type.Array(Type.String({ minLength: 1, maxLength: 400 }), { minItems: 1, maxItems: 10, uniqueItems: true }),
      count: Type.Optional(Type.Number({ minimum: 1, maximum: 10 })),
      country: Type.Optional(Type.String({ pattern: "^[A-Za-z]{2}$", description: "两位国家代码，例如 CN" })),
      search_lang: Type.Optional(Type.String({ pattern: "^[A-Za-z-]{2,10}$", description: "检索语言，例如 zh-hans" })),
      mode: Type.Optional(Type.Union([
        Type.Literal("auto"), Type.Literal("broad"), Type.Literal("official"),
        Type.Literal("chinese_policy"), Type.Literal("recent"),
      ], { description: "检索场景：自动、广泛发现、官方/技术资料、中文政策或近期信息" })),
      site: Type.Optional(Type.String({ minLength: 3, maxLength: 253, description: "可选的公网域名限定，例如 gov.cn" })),
      published_after: Type.Optional(Type.String({ pattern: "^\\d{4}-\\d{2}-\\d{2}$", description: "可选发布日期起点 YYYY-MM-DD" })),
      published_before: Type.Optional(Type.String({ pattern: "^\\d{4}-\\d{2}-\\d{2}$", description: "可选发布日期终点 YYYY-MM-DD" })),
      snippet_chars: Type.Optional(Type.Number({ minimum: 180, maximum: 1200, description: "每条候选摘要最大字符数" })),
    }),
    async execute(_toolCallId, params) {
      const context = hooks.beforeLogicalTool("web.search", params);
      const allowed = loadEvidence(context).urls;
      const result = await searchPublicWeb(params);
      for (const search of result.searches) {
        for (const item of search.results) allowed.add(item.url);
      }
      saveEvidence(context);
      assertResultSize(result);
      hooks.afterLogicalTool("web.search", params, result);
      return { content: content(result), details: result };
    },
  });

  pi.registerTool({
    name: "director_web_open",
    label: "读取公开网页正文",
    description: "批量读取上一轮公开检索返回的 URL，或用户在当前任务中明确提供的 URL；拒绝重定向、本机/私网目标、危险协议和超限响应。",
    parameters: Type.Object({
      items: Type.Array(Type.Object({
        url: Type.String({ minLength: 1, maxLength: 2048 }),
        user_provided: Type.Optional(Type.Boolean({ description: "仅当该 URL 直接来自用户当前任务时设为 true" })),
        title: Type.Optional(Type.String({ maxLength: 500 })),
      }), { minItems: 1, maxItems: 6 }),
      max_pages: Type.Optional(Type.Number({ minimum: 1, maximum: 200 })),
      max_chars: Type.Optional(Type.Number({ minimum: 1000, maximum: 200000 })),
    }),
    async execute(_toolCallId, params) {
      const context = hooks.beforeLogicalTool("web.open", params);
      const loaded = loadEvidence(context);
      const allowed = loaded.urls;
      const authorizedByUser = new Set(context?.authorized_urls ?? []);
      const distinctUrls = params.items.map((item) => item.url);
      if (new Set(distinctUrls).size !== distinctUrls.length) throw new Error("同一批网页正文读取不能包含重复 URL");
      const sources = [];
      for (const item of params.items) {
        const standaloneExplicit = taskKey(context) === "__standalone__" && item.user_provided === true;
        if (!allowed.has(item.url) && !authorizedByUser.has(item.url) && !standaloneExplicit) {
          throw new Error("该 URL 不是本次会话的公开检索结果，也未出现在用户原始任务中；请让用户明确提供 URL");
        }
        const source = await openWebSource(item.url, {
          title: item.title,
          maxPages: params.max_pages,
          maxChars: params.max_chars,
        });
        sources.push(source);
        loaded.mutations.set(source.source_id, canonicalEvidence(source.knowledge_mutation));
        loaded.sources.set(source.source_id, {
          source_id: source.source_id,
          title: source.title,
          source_type: source.source_type,
          ...(source.url ? { url: source.url } : {}),
          ...(source.path ? { path: source.path } : {}),
          content_sha256: source.content_sha256,
          ...(source.source_type === "pdf" && source.total_pages ? { page_count: source.total_pages } : {}),
          ...(source.source_type === "pdf" ? { extracted_pages: source.pages.filter((page) => page.text).map((page) => page.page) } : {}),
          reliability: source.extraction_reliability,
          accessed_at: source.accessed_at,
        });
      }
      saveEvidence(context);
      const result = { opened_at: new Date().toISOString(), sources };
      assertResultSize(result);
      hooks.afterLogicalTool("web.open", params, result);
      return { content: content(result), details: result };
    },
  });

  pi.registerTool({
    name: "director_pdf_read",
    label: "读取本地 PDF 资料",
    description: "只读取项目 inputs/ 或 data/inbox/ 下用户明确指定的普通 PDF 文件，返回带页码的文本证据；不会扫描其他目录。",
    parameters: Type.Object({
      path: Type.String({ minLength: 1, maxLength: 1024, description: "项目内相对路径，例如 inputs/report.pdf" }),
      title: Type.Optional(Type.String({ maxLength: 500 })),
      max_pages: Type.Optional(Type.Number({ minimum: 1, maximum: 200 })),
      max_chars: Type.Optional(Type.Number({ minimum: 1000, maximum: 200000 })),
    }),
    async execute(_toolCallId, params) {
      const context = hooks.beforeLogicalTool("pdf.read", params);
      const result = await readLocalPdf(hooks.projectRoot(), params.path, {
        title: params.title,
        maxPages: params.max_pages,
        maxChars: params.max_chars,
      });
      const loaded = loadEvidence(context);
      loaded.mutations.set(result.source_id, canonicalEvidence(result.knowledge_mutation));
      loaded.sources.set(result.source_id, {
        source_id: result.source_id,
        title: result.title,
        source_type: result.source_type,
        ...(result.url ? { url: result.url } : {}),
        ...(result.path ? { path: result.path } : {}),
        content_sha256: result.content_sha256,
        ...(result.total_pages ? { page_count: result.total_pages } : {}),
        extracted_pages: result.pages.filter((page) => page.text).map((page) => page.page),
        reliability: result.extraction_reliability,
        accessed_at: result.accessed_at,
      });
      saveEvidence(context);
      assertResultSize(result);
      hooks.afterLogicalTool("pdf.read", params, result);
      return { content: content(result), details: result };
    },
  });

  pi.registerTool({
    name: "director_presentation_plan_write",
    label: "保存 PPT 规划",
    description: "将当前任务/Profile 的 brief、证据引用、大纲、逐页策划和设计令牌原子写入受控 plan；使用版本和哈希防止覆盖并发修改。",
    parameters: Type.Object({
      schema_version: Type.Literal("1.0"),
      project_id: Type.String({ minLength: 1, maxLength: 128, pattern: "^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$" }),
      profile_id: Type.Union([Type.Literal("market-director"), Type.Literal("product-director")]),
      scene: Type.Union([Type.Literal("weekly"), Type.Literal("industry"), Type.Literal("government"), Type.Literal("custom")]),
      mode: Type.Union([Type.Literal("quick"), Type.Literal("standard"), Type.Literal("strict")]),
      phase: Type.Union([Type.Literal("outline"), Type.Literal("final")]),
      version: Type.Number({ minimum: 1, maximum: 10000 }),
      expected_plan_sha256: Type.Optional(Type.String({ pattern: "^[a-f0-9]{64}$" })),
      expected_context_snapshot_sha256: Type.Optional(Type.String({ pattern: "^[a-f0-9]{64}$" })),
      period: Type.Object({
        start: Type.String({ pattern: "^\\d{4}-\\d{2}-\\d{2}$" }),
        end: Type.String({ pattern: "^\\d{4}-\\d{2}-\\d{2}$" }),
      }),
      brief: Type.Object({
        topic: Type.String({ minLength: 1, maxLength: 240 }),
        audience: Type.String({ minLength: 1, maxLength: 240 }),
        purpose: Type.String({ minLength: 1, maxLength: 500 }),
        occasion: Type.String({ minLength: 1, maxLength: 240 }),
        language: Type.String({ minLength: 1, maxLength: 40 }),
        confidentiality: Type.Union([Type.Literal("internal"), Type.Literal("restricted"), Type.Literal("public")]),
        target_slides: Type.Number({ minimum: 4, maximum: 10 }),
        expected_decision: Type.Optional(Type.String({ maxLength: 500 })),
        duration_minutes: Type.Optional(Type.Number({ minimum: 1, maximum: 240 })),
      }),
      evidence_refs: Type.Array(Type.String({ minLength: 1, maxLength: 128 }), { minItems: 1, maxItems: 200, uniqueItems: true }),
      outline: Type.Array(Type.Object({
        slide_id: Type.String({ minLength: 1, maxLength: 128 }),
        order: Type.Number({ minimum: 1, maximum: 10 }),
        conclusion_title: Type.String({ minLength: 1, maxLength: 120 }),
        evidence_refs: Type.Array(Type.String({ minLength: 1, maxLength: 128 }), { maxItems: 50, uniqueItems: true }),
      }), { minItems: 4, maxItems: 10 }),
      slides: Type.Optional(Type.Array(Type.Object({
        slide_id: Type.String({ minLength: 1, maxLength: 128 }),
        order: Type.Number({ minimum: 1, maximum: 10 }),
        conclusion_title: Type.String({ minLength: 1, maxLength: 120 }),
        audience_takeaway: Type.String({ minLength: 1, maxLength: 500 }),
        facts: Type.Array(Type.Object({
          text: Type.String({ minLength: 1, maxLength: 500 }),
          evidence_refs: Type.Array(Type.String({ minLength: 1, maxLength: 128 }), { minItems: 1, maxItems: 20, uniqueItems: true }),
        }), { maxItems: 20 }),
        analyses: Type.Array(Type.String({ maxLength: 500 }), { maxItems: 20 }),
        hypotheses: Type.Array(Type.String({ maxLength: 500 }), { maxItems: 20 }),
        unknowns: Type.Array(Type.String({ maxLength: 500 }), { maxItems: 20 }),
        evidence_refs: Type.Array(Type.String({ minLength: 1, maxLength: 128 }), { maxItems: 50, uniqueItems: true }),
        layout_intent: Type.Union([
          Type.Literal("single-focus"), Type.Literal("fifty-fifty"), Type.Literal("two-thirds"),
          Type.Literal("three-column"), Type.Literal("top-hero"), Type.Literal("mixed-grid"),
        ]),
        visual_assets: Type.Array(Type.String({ maxLength: 500 }), { maxItems: 20 }),
        speaker_notes: Type.String({ maxLength: 4000 }),
        warnings: Type.Array(Type.String({ maxLength: 500 }), { maxItems: 20 }),
        render: Type.Object({
          title: Type.String({ minLength: 1, maxLength: 120 }),
          layout_intent: Type.Union([
            Type.Literal("single-focus"), Type.Literal("fifty-fifty"), Type.Literal("two-thirds"),
            Type.Literal("three-column"), Type.Literal("top-hero"), Type.Literal("mixed-grid"),
          ]),
          subtitle: Type.Optional(Type.String({ maxLength: 240 })),
          eyebrow: Type.Optional(Type.String({ maxLength: 80 })),
          lead: Type.Optional(Type.String({ maxLength: 240 })),
          body: Type.Optional(Type.Array(Type.String({ minLength: 1, maxLength: 180 }), { maxItems: 7 })),
          callout: Type.Optional(Type.String({ maxLength: 240 })),
          notes: Type.Optional(Type.String({ maxLength: 4000 })),
          sources: Type.Optional(Type.Array(Type.Object({
            title: Type.String({ minLength: 1, maxLength: 500 }),
            url: Type.Optional(Type.String({ maxLength: 2048 })),
            path: Type.Optional(Type.String({ maxLength: 1024 })),
            sha256: Type.Optional(Type.String({ pattern: "^[a-f0-9]{64}$" })),
            page: Type.Optional(Type.Number({ minimum: 1, maximum: 100000 })),
          }), { maxItems: 20 })),
        }),
      }), { minItems: 4, maxItems: 10 })),
      design_system: Type.Object({
        token_id: Type.Union([Type.Literal("management-report"), Type.Literal("government-program"), Type.Literal("technology-research")]),
      }),
      output_name: Type.String({ minLength: 6, maxLength: 125, pattern: "^[A-Za-z0-9][A-Za-z0-9._-]{0,119}\\.pptx$" }),
    }),
    async execute(_toolCallId, params) {
      const context = hooks.beforeLogicalTool("presentation.plan.write", params);
      if (!context?.task_id || !context.profile_id) throw new Error("PPT plan 写入缺少当前受管任务/Profile 上下文");
      const plan = writePresentationPlan(hooks.projectRoot(), context.task_id, context.profile_id, params as PresentationPlanInput);
      const result = {
        task_id: plan.task_id,
        profile_id: plan.profile_id,
        project_id: plan.project_id,
        phase: plan.phase,
        version: plan.version,
        plan_sha256: plan.plan_sha256,
        context_snapshot_sha256: plan.context_snapshot_sha256,
        path: `.pi/director-runtime/presentation-plans/${plan.task_id}.json`,
      };
      assertResultSize(result);
      hooks.afterLogicalTool("presentation.plan.write", params, result);
      return { content: content(result), details: result };
    },
  });

  pi.registerTool({
    name: "director_artifact_deck_write",
    label: "生成受控 PPT",
    description: "使用项目内置 PptxGenJS 与 LibreOffice QA 构建 4-10 页可编辑总监 PPT，输出到 outputs/，保留逐页来源备注且不覆盖已有文件。",
    parameters: Type.Object({
      schema_version: Type.Literal("1.0"),
      snapshot_sha256: Type.String({ pattern: "^[a-f0-9]{64}$", description: "当前 weekly.snapshot 或 final plan 的证据上下文 SHA-256" }),
      plan_sha256: Type.Optional(Type.String({ pattern: "^[a-f0-9]{64}$" })),
      output_name: Type.String({ minLength: 6, maxLength: 125, pattern: "^[A-Za-z0-9][A-Za-z0-9._-]{0,119}\\.pptx$" }),
      profile_id: Type.Union([Type.Literal("market-director"), Type.Literal("product-director")]),
      template_id: Type.Union([Type.Literal("ceo-weekly"), Type.Literal("management-report"), Type.Literal("government-program"), Type.Literal("technology-research")]),
      period: Type.Object({
        start: Type.String({ pattern: "^\\d{4}-\\d{2}-\\d{2}$" }),
        end: Type.String({ pattern: "^\\d{4}-\\d{2}-\\d{2}$" }),
      }),
      slides: Type.Array(Type.Object({
        title: Type.String({ minLength: 1, maxLength: 120 }),
        layout_intent: Type.Optional(Type.Union([
          Type.Literal("single-focus"), Type.Literal("fifty-fifty"), Type.Literal("two-thirds"),
          Type.Literal("three-column"), Type.Literal("top-hero"), Type.Literal("mixed-grid"),
        ])),
        subtitle: Type.Optional(Type.String({ maxLength: 240 })),
        eyebrow: Type.Optional(Type.String({ maxLength: 80 })),
        lead: Type.Optional(Type.String({ maxLength: 240 })),
        body: Type.Optional(Type.Array(Type.String({ minLength: 1, maxLength: 180 }), { maxItems: 7 })),
        callout: Type.Optional(Type.String({ maxLength: 240 })),
        notes: Type.Optional(Type.String({ maxLength: 4000 })),
        sources: Type.Optional(Type.Array(Type.Object({
          title: Type.String({ minLength: 1, maxLength: 500 }),
          url: Type.Optional(Type.String({ maxLength: 2048 })),
          path: Type.Optional(Type.String({ maxLength: 1024 })),
          sha256: Type.Optional(Type.String({ pattern: "^[a-f0-9]{64}$" })),
          page: Type.Optional(Type.Number({ minimum: 1, maximum: 100000 })),
        }), { maxItems: 20 })),
      }), { minItems: 4, maxItems: 10 }),
    }),
    async execute(_toolCallId, params) {
      assertDeckPayload(params as DeckPayload);
      const commit = hooks.beforeLogicalTool("artifact.deck.write", params);
      let buildStarted = false;
      try {
        if (!commit?.intent_id || !commit.payload_sha256 || !commit.task_id || !commit.profile_id) {
          throw new Error("PPT 写入缺少批准后的任务/意图/载荷/Profile 上下文");
        }
        if (commit.profile_id !== params.profile_id) throw new Error("PPT 载荷 Profile 与当前受管任务不一致");
        assertDeckMatchesEvidenceContext(hooks.projectRoot(), commit.task_id, commit.profile_id, params as DeckPayload);
        buildStarted = true;
        const result = await buildWeeklyDeck(hooks.projectRoot(), params as DeckPayload, commit as DeckCommitContext);
        assertResultSize(result);
        hooks.afterLogicalTool("artifact.deck.write", params, result);
        return { content: content(result), details: result };
      } catch (error) {
        const outputName = typeof params.output_name === "string" ? params.output_name : "";
        const target = outputName ? resolve(hooks.projectRoot(), "outputs", outputName) : "";
        hooks.onLogicalToolError(
          "artifact.deck.write",
          params,
          error instanceof DeckNotCommittedError
            ? "not_committed"
            : buildStarted && Boolean(target && existsSync(target)) ? "ambiguous" : "not_committed",
          error,
        );
        throw error;
      }
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
      const context = hooks.beforeLogicalTool("knowledge.search", params);
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
      const loaded = loadEvidence(context);
      for (const rawRow of result.searches.flatMap((search) => search.rows)) {
        const row = rawRow as Record<string, string>;
        if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/u.test(row.source_id)) continue;
        const hash = /(?:^|;\s*)content_sha256=([a-f0-9]{64})(?:;|$)/u.exec(row.notes)?.[1];
        const evidenceRefsText = /(?:^|;\s*)evidence_refs=([^;]+)/u.exec(row.notes)?.[1] ?? "";
        const evidenceRef = evidenceRefsText.split("|")[0]?.trim();
        const extractedPages = [...evidenceRefsText.matchAll(/#page=(\d+)/gu)]
          .map((match) => Number(match[1]))
          .filter((page, index, pages) => Number.isInteger(page) && page >= 1 && page <= 100000 && pages.indexOf(page) === index);
        const pageCountValue = Number(/(?:^|;\s*)total_pages=(\d+)(?:;|$)/u.exec(row.notes)?.[1] ?? "");
        let url: string | undefined;
        if (row.url.trim()) {
          try { url = normalizePublicUrl(row.url).toString(); } catch { continue; }
        }
        let relativePath: string | undefined;
        if (!url && evidenceRef) {
          const candidate = evidenceRef.replace(/#page=\d+$/u, "").replaceAll("\\", "/");
          if (/^(inputs|data\/inbox)\/[^\0]+\.pdf$/iu.test(candidate) && !candidate.split("/").includes("..")) relativePath = candidate;
        }
        if (!url && (!relativePath || !hash)) continue;
        const accessedAt = /^\d{4}-\d{2}-\d{2}$/u.test(row.accessed_date)
          ? `${row.accessed_date}T00:00:00.000Z`
          : result.snapshot_at;
        loaded.sources.set(row.source_id, {
          source_id: row.source_id,
          title: row.title.slice(0, 500),
          source_type: row.source_type || "knowledge",
          ...(url ? { url } : {}),
          ...(relativePath ? { path: relativePath } : {}),
          ...(hash ? { content_sha256: hash } : {}),
          ...(row.source_type === "pdf" && Number.isInteger(pageCountValue) && pageCountValue >= 1 && pageCountValue <= 100000
            ? { page_count: pageCountValue }
            : {}),
          ...(row.source_type === "pdf" ? { extracted_pages: extractedPages } : {}),
          reliability: row.status === "verified" ? "standard" : "limited",
          accessed_at: accessedAt,
        });
      }
      saveEvidence(context);
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
      const registered = loadEvidence(commit).mutations;
      for (const mutation of params.mutations) {
        const expected = registered?.get(mutation.record_id);
        if (mutation.operation === "insert" && registered.size > 0 && !expected) {
          throw new Error(`知识来源 ${mutation.record_id} 未出现在本任务读取证据中`);
        }
        if (/^(web|pdf)-/u.test(mutation.record_id) && !expected) {
          throw new Error(`工具来源 ${mutation.record_id} 缺少本任务证据 registry`);
        }
        if (expected && canonicalEvidence(mutation) !== expected) {
          throw new Error(`知识来源 ${mutation.record_id} 与本任务读取证据的冻结 mutation 不一致`);
        }
      }
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
