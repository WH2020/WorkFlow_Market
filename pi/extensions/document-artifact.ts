import { execFile as execFileCallback } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import {
  closeSync,
  existsSync,
  fsyncSync,
  lstatSync,
  linkSync,
  mkdirSync,
  openSync,
  readFileSync,
  readdirSync,
  realpathSync,
  renameSync,
  rmSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

import { bidDocumentSnapshotSha256, openBiddingStore } from "./bid-store.ts";

const execFile = promisify(execFileCallback);
const MAX_DOCUMENT_BYTES = 100 * 1024 * 1024;

export type BidDocumentPayload = {
  schema_version: "1.0";
  profile_id: "sales-director";
  bid_id: string;
  snapshot_sha256: string;
  output_name: string;
  title: string;
  subtitle?: string;
  buyer?: string;
  tender_number?: string;
  bidder?: string;
  generated_date?: string;
  confidentiality?: string;
  sections: Array<{
    section_id: string;
    title: string;
    level: 1 | 2 | 3 | 4;
    paragraphs: string[];
    tables?: Array<{ title?: string; columns: string[]; rows: string[][] }>;
  }>;
  sources: Array<{
    source_id: string;
    title: string;
    path: string;
    sha256: string;
    page?: number;
    locator?: string;
  }>;
  warnings?: string[];
};

export type DocumentCommitContext = {
  intent_id: string;
  payload_sha256: string;
  task_id: string;
  profile_id: string;
};

type DocumentReceipt = {
  schema_version: "1.0";
  intent_id: string;
  task_id: string;
  bid_id: string;
  payload_sha256: string;
  owner: "director_artifact_document_write";
  target: string;
  status: "prepared" | "committed";
  artifact_sha256: string;
  bytes: number;
  source_count: number;
  page_count: number;
  qa: {
    validation: string;
    preview_directory: string;
    montage: string;
    pdf: string;
    renderer?: string;
  };
  updated_at: string;
};

export class DocumentNotCommittedError extends Error {}

function isContained(root: string, candidate: string): boolean {
  const containment = relative(root, candidate);
  return containment === "" || (!containment.startsWith("..") && !isAbsolute(containment));
}

function sha256(path: string): string {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

function safeText(value: unknown, label: string, maximum: number, required = true): string {
  if (value === undefined && !required) return "";
  if (
    typeof value !== "string" || (required && !value.trim()) || value.length > maximum ||
    /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/u.test(value)
  ) throw new Error(`${label} 无效或过长`);
  return value;
}

function safeId(value: unknown, label: string): string {
  if (typeof value !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/u.test(value)) throw new Error(`${label} 无效`);
  return value;
}

function atomicText(path: string, content: string): void {
  let descriptor: number | undefined;
  try {
    descriptor = openSync(path, "wx", 0o600);
    writeFileSync(descriptor, content, "utf8");
    fsyncSync(descriptor);
    closeSync(descriptor);
    descriptor = undefined;
  } catch (error) {
    if (descriptor !== undefined) closeSync(descriptor);
    throw error;
  }
}

function replaceText(path: string, content: string): void {
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

function receiptDirectory(root: string): string {
  const directory = resolve(root, ".pi", "director-runtime", "document-commits");
  mkdirSync(directory, { recursive: true });
  const meta = lstatSync(directory);
  const canonical = realpathSync.native(directory);
  if (!meta.isDirectory() || meta.isSymbolicLink() || !isContained(root, canonical)) throw new Error("正式标书回执目录无效");
  return canonical;
}

function receiptPath(root: string, intentId: string): string {
  safeId(intentId, "文件生成意图编号");
  return join(receiptDirectory(root), `${intentId}.json`);
}

function lockIntent(root: string, intentId: string): { path: string; descriptor: number } {
  const path = `${receiptPath(root, intentId)}.lock`;
  try {
    const descriptor = openSync(path, "wx", 0o600);
    writeFileSync(descriptor, JSON.stringify({ pid: process.pid, nonce: randomUUID(), created_at: new Date().toISOString() }), "utf8");
    fsyncSync(descriptor);
    return { path, descriptor };
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "EEXIST") throw new DocumentNotCommittedError("同一正式标书生成意图正在处理，请稍后重试");
    throw error;
  }
}

function unlock(held: { path: string; descriptor: number }): void {
  closeSync(held.descriptor);
  unlinkSync(held.path);
}

function artifactEnvironment(): NodeJS.ProcessEnv {
  const allowed = new Set([
    "PATH", "Path", "PATHEXT", "SystemRoot", "SYSTEMROOT", "WINDIR", "windir", "COMSPEC",
    "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA",
    "ProgramFiles", "ProgramFiles(x86)", "LANG", "LC_ALL", "LC_CTYPE", "XDG_CACHE_HOME",
    "WORKFLOW_LIBREOFFICE_PATH", "WORKFLOW_CJK_FONT", "WORKFLOW_LATIN_FONT",
  ]);
  return Object.fromEntries(Object.entries(process.env).filter(([key]) => allowed.has(key)));
}

export function assertBidDocumentPayload(projectRoot: string, profileId: string, value: BidDocumentPayload): void {
  if (!value || value.schema_version !== "1.0" || value.profile_id !== "sales-director" || profileId !== value.profile_id) {
    throw new Error("正式标书载荷版本或角色与当前任务不一致");
  }
  safeId(value.bid_id, "bid_id");
  if (!/^[a-f0-9]{64}$/u.test(value.snapshot_sha256)) throw new Error("正式标书快照 SHA-256 无效");
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.docx$/u.test(value.output_name)) throw new Error("正式标书输出文件名无效");
  safeText(value.title, "正式标书标题", 300);
  for (const [label, text, maximum] of [
    ["副标题", value.subtitle, 500], ["采购人", value.buyer, 500], ["招标编号", value.tender_number, 200],
    ["投标人", value.bidder, 500], ["生成日期", value.generated_date, 40], ["使用范围", value.confidentiality, 100],
  ] as Array<[string, unknown, number]>) safeText(text, label, maximum, false);
  if (!Array.isArray(value.sections) || value.sections.length < 1 || value.sections.length > 80) throw new Error("正式标书章节必须为 1–80 个");
  if (!Array.isArray(value.sources) || value.sources.length < 1 || value.sources.length > 200) throw new Error("正式标书必须引用 1–200 条已登记资料");
  if (!Array.isArray(value.warnings ?? []) || (value.warnings?.length ?? 0) > 100) throw new Error("正式标书风险提示超过上限");
  value.warnings?.forEach((warning) => safeText(warning, "风险提示", 2000));

  const store = openBiddingStore(projectRoot, true);
  try {
    const snapshot = store.readProject(value.bid_id);
    if (!snapshot) throw new Error("投标项目不存在");
    if (bidDocumentSnapshotSha256(snapshot) !== value.snapshot_sha256) throw new Error("投标项目在正式文件审批前已变化，请重新读取并生成内容");
    const parts = snapshot.sections as Record<string, Array<Record<string, unknown>>>;
    const allowedSections = new Map((parts.sections ?? []).map((section) => [String(section.section_id), section]));
    const seenSections = new Set<string>();
    let paragraphs = 0;
    for (const [index, section] of value.sections.entries()) {
      safeId(section.section_id, `第 ${index + 1} 个章节编号`);
      if (seenSections.has(section.section_id)) throw new Error("正式标书章节编号重复");
      seenSections.add(section.section_id);
      const registered = allowedSections.get(section.section_id);
      if (!registered || registered.title !== section.title) throw new Error(`章节 ${section.section_id} 未登记或标题与已批准目录不一致`);
      if (!Number.isInteger(section.level) || section.level < 1 || section.level > 4) throw new Error(`章节 ${section.section_id} 层级无效`);
      if (!Array.isArray(section.paragraphs) || section.paragraphs.length > 200) throw new Error(`章节 ${section.section_id} 段落数量无效`);
      section.paragraphs.forEach((paragraph) => safeText(paragraph, `章节 ${section.section_id} 正文`, 20_000));
      paragraphs += section.paragraphs.length;
      if (!Array.isArray(section.tables ?? []) || (section.tables?.length ?? 0) > 20) throw new Error(`章节 ${section.section_id} 表格过多`);
      for (const table of section.tables ?? []) {
        safeText(table.title, "表格标题", 300, false);
        if (!Array.isArray(table.columns) || table.columns.length < 1 || table.columns.length > 6) throw new Error("正式标书表格列数无效");
        if (!Array.isArray(table.rows) || table.rows.length > 100) throw new Error("正式标书表格行数无效");
        table.columns.forEach((item) => safeText(item, "表格列名", 100));
        table.rows.forEach((row) => {
          if (!Array.isArray(row) || row.length !== table.columns.length) throw new Error("正式标书表格行列数不一致");
          row.forEach((item) => safeText(item, "表格单元格", 2000));
        });
      }
    }
    if (paragraphs < 1) throw new Error("正式标书没有正文段落");
    const documents = new Map((parts.documents ?? []).map((document) => [String(document.document_id), document]));
    const sourceIds = new Set<string>();
    for (const source of value.sources) {
      safeId(source.source_id, "来源编号");
      if (sourceIds.has(source.source_id)) throw new Error("正式标书来源编号重复");
      sourceIds.add(source.source_id);
      safeText(source.title, "来源标题", 500);
      safeText(source.path, "来源路径", 1024);
      safeText(source.locator, "来源定位", 500, false);
      if (!/^[a-f0-9]{64}$/u.test(source.sha256)) throw new Error("正式标书来源 SHA-256 无效");
      const document = documents.get(source.source_id);
      if (
        !document || document.relative_path !== source.path || document.sha256 !== source.sha256 ||
        document.display_name !== source.title
      ) throw new Error(`来源 ${source.source_id} 与当前投标项目登记文件不一致`);
      if (source.page !== undefined && (!Number.isInteger(source.page) || source.page < 1 || source.page > 100_000)) throw new Error("正式标书来源页码无效");
      const pageCount = Number(document.page_count ?? 0);
      if (source.page !== undefined && pageCount > 0 && source.page > pageCount) throw new Error(`来源 ${source.source_id} 页码超过文件总页数`);
    }
  } finally { store.close(); }
}

function readReceipt(root: string, path: string, commit: DocumentCommitContext, payload: BidDocumentPayload, output: string): DocumentReceipt | undefined {
  if (!existsSync(path)) return undefined;
  const receipt = JSON.parse(readFileSync(path, "utf8")) as DocumentReceipt;
  const expectedTarget = `outputs/bids/${payload.bid_id}/${payload.output_name}`;
  if (
    receipt.schema_version !== "1.0" || receipt.intent_id !== commit.intent_id || receipt.task_id !== commit.task_id ||
    receipt.bid_id !== payload.bid_id || receipt.payload_sha256 !== commit.payload_sha256 ||
    receipt.owner !== "director_artifact_document_write" || receipt.target !== expectedTarget ||
    (receipt.status !== "prepared" && receipt.status !== "committed") || !/^[a-f0-9]{64}$/u.test(receipt.artifact_sha256) ||
    !Number.isInteger(receipt.bytes) || receipt.bytes < 1 || receipt.bytes > MAX_DOCUMENT_BYTES ||
    !Number.isInteger(receipt.page_count) || receipt.page_count < 2 || receipt.page_count > 500 ||
    !receipt.qa || typeof receipt.qa.validation !== "string" || !receipt.qa.validation.includes("Test passed. Document rendered")
  ) throw new Error("正式标书回执与当前任务、意图或载荷不一致，需人工恢复");
  for (const pathValue of [receipt.qa.preview_directory, receipt.qa.montage, receipt.qa.pdf]) {
    const candidate = resolve(root, pathValue);
    if (!isContained(root, candidate) || !existsSync(candidate) || lstatSync(candidate).isSymbolicLink()) {
      throw new Error("正式标书回执的渲染检查证据缺失或越出项目范围");
    }
  }
  if (!existsSync(output)) {
    if (receipt.status === "committed") throw new Error("正式标书回执已提交但文件缺失，需人工恢复");
    return undefined;
  }
  const metadata = lstatSync(output);
  if (!metadata.isFile() || metadata.isSymbolicLink() || metadata.size !== receipt.bytes || sha256(output) !== receipt.artifact_sha256) {
    throw new Error("正式标书文件与回执哈希不一致，需人工恢复");
  }
  if (receipt.status === "prepared") {
    receipt.status = "committed";
    receipt.updated_at = new Date().toISOString();
    replaceText(path, `${JSON.stringify(receipt, null, 2)}\n`);
  }
  return receipt;
}

export async function buildBidDocument(
  projectRoot: string,
  payload: BidDocumentPayload,
  commit: DocumentCommitContext,
): Promise<Record<string, unknown>> {
  assertBidDocumentPayload(projectRoot, commit.profile_id, payload);
  const root = realpathSync.native(resolve(projectRoot));
  const outputRoot = resolve(root, "outputs", "bids", payload.bid_id);
  mkdirSync(outputRoot, { recursive: true });
  const outputMeta = lstatSync(outputRoot);
  const canonicalOutputRoot = realpathSync.native(outputRoot);
  if (!outputMeta.isDirectory() || outputMeta.isSymbolicLink() || !isContained(root, canonicalOutputRoot)) throw new Error("投标产物目录无效");
  const output = resolve(canonicalOutputRoot, payload.output_name);
  if (!isContained(canonicalOutputRoot, output)) throw new Error("正式标书输出路径越出投标产物目录");
  const receipt = receiptPath(root, commit.intent_id);
  const held = lockIntent(root, commit.intent_id);
  try {
    const recovered = readReceipt(root, receipt, commit, payload, output);
    if (recovered) return {
      path: recovered.target,
      receipt: relative(root, receipt).replaceAll("\\", "/"),
      sha256: recovered.artifact_sha256,
      bytes: recovered.bytes,
      page_count: recovered.page_count,
      qa: recovered.qa,
      recovered: true,
    };
    if (existsSync(output)) throw new DocumentNotCommittedError(`正式标书输出名已被占用，拒绝覆盖：${payload.output_name}`);

    const jobRoot = resolve(root, ".pi", "director-runtime", "document-jobs");
    mkdirSync(jobRoot, { recursive: true });
    const canonicalJobRoot = realpathSync.native(jobRoot);
    if (lstatSync(jobRoot).isSymbolicLink() || !isContained(root, canonicalJobRoot)) throw new Error("正式标书临时任务目录无效");
    const job = resolve(canonicalJobRoot, `${commit.intent_id}-${randomUUID()}`);
    mkdirSync(job, { recursive: false });
    if (lstatSync(job).isSymbolicLink() || !isContained(canonicalJobRoot, realpathSync.native(job))) throw new Error("正式标书私有任务目录无效");
    const input = join(job, "payload.json");
    const temporaryDocument = join(job, "artifact.docx");
    const qaDirectory = join(job, "qa");
    writeFileSync(input, `${JSON.stringify(payload, null, 2)}\n`, { encoding: "utf8", flag: "wx", mode: 0o600 });
    const artifactRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..", "artifacts");
    const builder = resolve(artifactRoot, "build-bid-document.mjs");
    const validator = resolve(artifactRoot, "validate-and-render-document.mjs");
    if (!existsSync(builder) || !existsSync(validator)) throw new Error("正式标书生成或渲染检查工具缺失");
    let published = false;
    try {
      const built = await execFile(process.execPath, [builder, "--input", input, "--output", temporaryDocument, "--qa-dir", qaDirectory], {
        timeout: 180_000, windowsHide: true, maxBuffer: 2 * 1024 * 1024, env: artifactEnvironment(),
      });
      if (!existsSync(temporaryDocument)) throw new Error("正式标书构建器未生成文件");
      const metadata = lstatSync(temporaryDocument);
      if (!metadata.isFile() || metadata.isSymbolicLink() || metadata.size < 1 || metadata.size > MAX_DOCUMENT_BYTES) throw new Error("正式标书不是有效大小的普通文件");
      const checked = await execFile(process.execPath, [validator, "--input", temporaryDocument, "--qa-dir", qaDirectory], {
        timeout: 300_000, windowsHide: true, maxBuffer: 4 * 1024 * 1024, env: artifactEnvironment(),
      });
      const validation = String(checked.stdout).trim();
      if (!validation.includes("Test passed. Document rendered")) throw new Error(`正式标书渲染检查未通过：${validation.slice(0, 4000)}`);
      let qaResult: Record<string, unknown> = {};
      try { qaResult = JSON.parse(validation.split(/\r?\n/u).at(-1) ?? "{}"); } catch { /* keep textual evidence */ }
      const pageCount = Number(qaResult.page_count);
      if (!Number.isInteger(pageCount) || pageCount < 2 || pageCount > 500) throw new Error("正式标书渲染页数无效");
      const montage = join(qaDirectory, "document-montage.png");
      const pdfs = join(qaDirectory, "rendered-pdf");
      const names = existsSync(pdfs) ? readdirSync(pdfs) : [];
      const pdfName = names.find((name) => name.toLowerCase().endsWith(".pdf"));
      const pdf = pdfName ? join(pdfs, pdfName) : "";
      if (!existsSync(montage) || !pdf || !existsSync(pdf)) throw new Error("正式标书渲染预览或 PDF 检查证据缺失");
      const artifactSha256 = sha256(temporaryDocument);
      const qa = {
        validation,
        preview_directory: relative(root, qaDirectory).replaceAll("\\", "/"),
        montage: relative(root, montage).replaceAll("\\", "/"),
        pdf: relative(root, pdf).replaceAll("\\", "/"),
        ...(typeof qaResult.renderer === "string" ? { renderer: qaResult.renderer } : {}),
      };
      const prepared: DocumentReceipt = {
        schema_version: "1.0",
        intent_id: commit.intent_id,
        task_id: commit.task_id,
        bid_id: payload.bid_id,
        payload_sha256: commit.payload_sha256,
        owner: "director_artifact_document_write",
        target: `outputs/bids/${payload.bid_id}/${payload.output_name}`,
        status: "prepared",
        artifact_sha256: artifactSha256,
        bytes: metadata.size,
        source_count: payload.sources.length,
        page_count: pageCount,
        qa,
        updated_at: new Date().toISOString(),
      };
      atomicText(receipt, `${JSON.stringify(prepared, null, 2)}\n`);
      try {
        linkSync(temporaryDocument, output);
        published = true;
      } catch (error) {
        if (existsSync(receipt)) unlinkSync(receipt);
        if ((error as NodeJS.ErrnoException).code === "EEXIST") throw new DocumentNotCommittedError("正式标书输出名在提交时被其他任务占用");
        throw error;
      }
      prepared.status = "committed";
      prepared.updated_at = new Date().toISOString();
      replaceText(receipt, `${JSON.stringify(prepared, null, 2)}\n`);
      if (existsSync(input)) unlinkSync(input);
      if (existsSync(temporaryDocument)) unlinkSync(temporaryDocument);
      let build: unknown;
      try { build = JSON.parse(String(built.stdout).trim().split(/\r?\n/u).at(-1) ?? "{}"); } catch { build = {}; }
      return {
        path: prepared.target,
        receipt: relative(root, receipt).replaceAll("\\", "/"),
        intent_id: commit.intent_id,
        payload_sha256: commit.payload_sha256,
        sha256: artifactSha256,
        bytes: metadata.size,
        page_count: pageCount,
        build,
        qa,
      };
    } catch (error) {
      if (!published && existsSync(job)) rmSync(job, { recursive: true, force: true });
      throw error;
    }
  } finally { unlock(held); }
}
