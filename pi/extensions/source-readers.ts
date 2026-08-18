import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { lookup } from "node:dns/promises";
import { closeSync, existsSync, fstatSync, lstatSync, openSync, readFileSync, realpathSync } from "node:fs";
import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import { isIP } from "node:net";
import { dirname, isAbsolute, relative, resolve } from "node:path";
import { Readable } from "node:stream";
import { fileURLToPath } from "node:url";

const MAX_WEB_BODY_BYTES = 8 * 1024 * 1024;
const MAX_LOCAL_PDF_BYTES = 32 * 1024 * 1024;
const DEFAULT_MAX_CHARS = 60_000;
const MAX_TEXT_CHARS = 200_000;
const DEFAULT_MAX_PAGES = 80;
const MAX_PAGES = 200;

export type SourcePage = { page: number; text: string; chars: number };

export type SourceDocument = {
  source_id: string;
  source_type: "web" | "pdf";
  title: string;
  url?: string;
  path?: string;
  publisher: string;
  published_date: string;
  accessed_at: string;
  content_type: string;
  content_sha256: string;
  etag?: string;
  last_modified?: string;
  extraction_method: string;
  extraction_reliability: "standard" | "limited";
  text: string;
  summary: string;
  pages: SourcePage[];
  truncated: boolean;
  evidence_refs: string[];
  total_pages?: number;
  extracted_pages?: number;
  knowledge_mutation: {
    operation: "insert";
    record_id: string;
    changes: Record<string, string>;
  };
};

type PdfOptions = { maxPages?: number; maxChars?: number; title?: string; deadlineMs?: number; allowFallback?: boolean };
export type PublicAddress = { address: string; family: 4 | 6 };
type SourceRequest = (url: URL, target: PublicAddress, signal?: AbortSignal) => Promise<Response>;

let sourceRequestOverride: SourceRequest | undefined;

/** Test-only transport injection; production callers should leave this undefined. */
export function setSourceRequestForTests(request: SourceRequest | undefined): void {
  sourceRequestOverride = request;
}

function sha256(value: string | Uint8Array): string {
  return createHash("sha256").update(value).digest("hex");
}

function normalizeMaxChars(value: number | undefined): number {
  const resolved = value ?? DEFAULT_MAX_CHARS;
  if (!Number.isInteger(resolved) || resolved < 1_000 || resolved > MAX_TEXT_CHARS) {
    throw new Error(`max_chars 必须是 1000-${MAX_TEXT_CHARS} 的整数`);
  }
  return resolved;
}

function normalizeMaxPages(value: number | undefined): number {
  const resolved = value ?? DEFAULT_MAX_PAGES;
  if (!Number.isInteger(resolved) || resolved < 1 || resolved > MAX_PAGES) {
    throw new Error(`max_pages 必须是 1-${MAX_PAGES} 的整数`);
  }
  return resolved;
}

function decodeHtmlEntities(value: string): string {
  const named: Record<string, string> = {
    amp: "&", apos: "'", gt: ">", lt: "<", nbsp: " ", quot: '"',
  };
  return value.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/giu, (whole, entity: string) => {
    if (entity.startsWith("#x")) {
      const code = Number.parseInt(entity.slice(2), 16);
      return Number.isFinite(code) && code <= 0x10ffff ? String.fromCodePoint(code) : whole;
    }
    if (entity.startsWith("#")) {
      const code = Number.parseInt(entity.slice(1), 10);
      return Number.isFinite(code) && code <= 0x10ffff ? String.fromCodePoint(code) : whole;
    }
    return named[entity.toLowerCase()] ?? whole;
  });
}

function normalizeText(value: string): string {
  return value
    .replace(/\r\n?/gu, "\n")
    .replace(/[\t\f\v ]+/gu, " ")
    .replace(/ *\n */gu, "\n")
    .replace(/\n{3,}/gu, "\n\n")
    .trim();
}

function extractHtml(html: string): { title: string; publishedDate: string; text: string } {
  const titleMatch = /<title\b[^>]*>([\s\S]*?)<\/title>/iu.exec(html);
  const publishedMatch = /<meta\b[^>]*(?:property|name)\s*=\s*["'](?:article:published_time|date|datepublished)["'][^>]*content\s*=\s*["']([^"']+)["'][^>]*>/iu.exec(html)
    ?? /<meta\b[^>]*content\s*=\s*["']([^"']+)["'][^>]*(?:property|name)\s*=\s*["'](?:article:published_time|date|datepublished)["'][^>]*>/iu.exec(html);
  const withoutExecutable = html
    .replace(/<!--[\s\S]*?-->/gu, " ")
    .replace(/<(script|style|noscript|svg|template)\b[^>]*>[\s\S]*?<\/\1>/giu, " ")
    .replace(/<head\b[^>]*>[\s\S]*?<\/head>/giu, " ");
  const withBreaks = withoutExecutable
    .replace(/<(br|hr)\b[^>]*\/?\s*>/giu, "\n")
    .replace(/<\/(p|div|li|article|section|main|header|footer|h[1-6]|tr)>/giu, "\n")
    .replace(/<[^>]+>/gu, " ");
  return {
    title: normalizeText(decodeHtmlEntities(titleMatch?.[1] ?? "")),
    publishedDate: normalizeText(publishedMatch?.[1] ?? "").slice(0, 64),
    text: normalizeText(decodeHtmlEntities(withBreaks)),
  };
}

function isPrivateIpv4(address: string): boolean {
  const parts = address.split(".").map(Number);
  if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) return true;
  const [a, b] = parts;
  return (
    a === 0 || a === 10 || a === 127 || a >= 224 ||
    (a === 100 && b >= 64 && b <= 127) ||
    (a === 169 && b === 254) ||
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 0) ||
    (a === 192 && b === 0 && parts[2] === 2) ||
    (a === 192 && b === 88 && parts[2] === 99) ||
    (a === 192 && b === 168) ||
    (a === 198 && (b === 18 || b === 19 || (b === 51 && parts[2] === 100))) ||
    (a === 203 && b === 0 && parts[2] === 113)
  );
}

function isPrivateAddress(address: string): boolean {
  const version = isIP(address);
  if (version === 4) return isPrivateIpv4(address);
  if (version !== 6) return true;
  const normalized = address.toLowerCase();
  if (normalized.startsWith("::ffff:")) return isPrivateIpv4(normalized.slice(7));
  if (normalized === "::" || normalized === "::1" || normalized.startsWith("fc") || normalized.startsWith("fd")) return true;
  const first = Number.parseInt(normalized.split(":")[0] || "0", 16);
  return (
    !Number.isFinite(first) || first < 0x2000 || first > 0x3fff ||
    normalized === "2001:0::" || normalized.startsWith("2001:0:") ||
    normalized === "2001:db8::" || normalized.startsWith("2001:db8:") ||
    normalized === "2001:2::" || normalized.startsWith("2001:2:") ||
    normalized === "2002::" || normalized.startsWith("2002:")
  );
}

async function resolvePublicNetworkTarget(url: URL): Promise<PublicAddress> {
  const hostname = url.hostname.replace(/^\[|\]$/gu, "").toLowerCase();
  if (!hostname || hostname === "localhost" || hostname.endsWith(".localhost")) {
    throw new Error("拒绝访问本机或未命名网络目标");
  }
  if (isIP(hostname)) {
    if (isPrivateAddress(hostname)) throw new Error("拒绝访问回环、私网、链路本地或保留地址");
    return { address: hostname, family: isIP(hostname) as 4 | 6 };
  }
  let addresses: Array<{ address: string; family: number }>;
  try {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      addresses = await Promise.race([
        lookup(hostname, { all: true, verbatim: true }),
        new Promise<never>((_resolve, reject) => {
          timer = setTimeout(() => reject(new Error("DNS lookup timed out")), 10_000);
        }),
      ]);
    } finally {
      if (timer) clearTimeout(timer);
    }
  } catch {
    throw new Error(`无法解析公开网页主机：${hostname}`);
  }
  if (addresses.length === 0 || addresses.some((item) => isPrivateAddress(item.address))) {
    throw new Error("网页主机解析到非公开网络地址，已拒绝访问");
  }
  const selected = addresses[0]!;
  if (selected.family !== 4 && selected.family !== 6) throw new Error("网页主机解析结果地址族无效");
  return { address: selected.address, family: selected.family };
}

export function normalizePublicUrl(value: string): URL {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error("网页 URL 无效");
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") throw new Error("只允许 http/https URL");
  if (url.username || url.password) throw new Error("网页 URL 不得包含用户名或密码");
  if ([...url.searchParams.keys()].some((key) => /(^|[-_])(api[-_]?key|access[-_]?token|token|password|secret|signature|sig|credential)([-_]|$)/iu.test(key))) {
    throw new Error("网页 URL 查询参数疑似包含密钥或令牌，已拒绝访问");
  }
  if (/(token|password|secret|signature|credential|sig)=/iu.test(url.hash)) {
    throw new Error("网页 URL 片段疑似包含密钥或令牌，已拒绝记录");
  }
  url.hash = "";
  return url;
}

export async function validatePublicUrl(value: string): Promise<URL> {
  const url = normalizePublicUrl(value);
  await resolvePublicNetworkTarget(url);
  return url;
}

function pinnedSourceRequest(url: URL, target: PublicAddress, signal?: AbortSignal): Promise<Response> {
  return new Promise((resolvePromise, reject) => {
    const transport = url.protocol === "https:" ? httpsRequest : httpRequest;
    const tlsHostname = url.hostname.replace(/^\[|\]$/gu, "");
    const request = transport(
      url,
      {
        method: "GET",
        headers: {
          Accept: "text/html, text/plain, application/xhtml+xml, application/pdf",
          "Accept-Encoding": "identity",
          "User-Agent": "WorkFlow-Market/0.3 source-reader",
        },
        lookup: ((
          _hostname: string,
          options: { all?: boolean } | number,
          callback: ((error: NodeJS.ErrnoException | null, address: string, family: number) => void)
            | ((error: NodeJS.ErrnoException | null, addresses: PublicAddress[]) => void),
        ) => {
          if (typeof options === "object" && options.all) {
            (callback as (error: NodeJS.ErrnoException | null, addresses: PublicAddress[]) => void)(null, [target]);
          } else {
            (callback as (error: NodeJS.ErrnoException | null, address: string, family: number) => void)(null, target.address, target.family);
          }
        }) as never,
        servername: url.protocol === "https:" && !isIP(tlsHostname) ? tlsHostname : undefined,
        signal,
      },
      (incoming) => {
        const status = incoming.statusCode ?? 0;
        if (status < 200 || status > 599) {
          incoming.destroy();
          reject(new Error("网页正文读取返回无效 HTTP 状态"));
          return;
        }
        const headers = new Headers();
        for (const [name, configured] of Object.entries(incoming.headers)) {
          if (Array.isArray(configured)) configured.forEach((item) => headers.append(name, item));
          else if (configured !== undefined) headers.set(name, configured);
        }
        const body = Readable.toWeb(incoming) as ReadableStream<Uint8Array>;
        resolvePromise(new Response(body, { status, headers }));
      },
    );
    request.setTimeout(10_000, () => request.destroy(new Error("网页正文连接空闲超时（10 秒）")));
    request.on("error", reject);
    request.end();
  });
}

/** Direct transport probe used by security regression tests. */
export function requestPinnedSourceForTests(url: URL, target: PublicAddress): Promise<Response> {
  return pinnedSourceRequest(url, target);
}

async function responseBytes(response: Response, limit: number, signal?: AbortSignal): Promise<Uint8Array> {
  const length = Number(response.headers.get("Content-Length") ?? "0");
  if (Number.isFinite(length) && length > limit) throw new Error(`来源正文超过 ${Math.trunc(limit / 1024 / 1024)} MiB 安全上限`);
  if (!response.body) return new Uint8Array();
  const reader = response.body.getReader();
  const cancelOnAbort = (): void => { void reader.cancel(); };
  signal?.addEventListener("abort", cancelOnAbort, { once: true });
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      if (signal?.aborted) throw new Error("网页正文读取超过 30 秒总时限");
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > limit) {
        await reader.cancel();
        throw new Error(`来源正文超过 ${Math.trunc(limit / 1024 / 1024)} MiB 安全上限`);
      }
      chunks.push(value);
    }
  } finally {
    signal?.removeEventListener("abort", cancelOnAbort);
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return bytes;
}

function pdfParserEnvironment(): NodeJS.ProcessEnv {
  const allowed = new Set([
    "PATH", "Path", "PATHEXT", "SystemRoot", "SYSTEMROOT", "WINDIR", "windir", "COMSPEC",
    "TEMP", "TMP", "TMPDIR", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
  ]);
  return Object.fromEntries(Object.entries(process.env).filter(([key]) => allowed.has(key)));
}

async function pdfJsPages(
  bytes: Uint8Array,
  maxPages: number,
  maxChars: number,
  timeoutMs = 45_000,
  allowFallback = true,
): Promise<{ pages: SourcePage[]; totalPages: number; method: "pdfjs-dist" | "builtin-text-layer" }> {
  const configured = process.env.WORKFLOW_PDFJS_MODULE?.trim();
  if (configured && !existsSync(configured)) throw new Error("WORKFLOW_PDFJS_MODULE 指向的文件不存在");
  const helper = resolve(dirname(fileURLToPath(import.meta.url)), "..", "artifacts", "extract-pdf-text.mjs");
  if (!existsSync(helper)) throw new Error("PDF.js 隔离解析脚本缺失");
  return new Promise((resolvePromise, reject) => {
    const args = [
      "--max-old-space-size=256",
      helper,
      "--max-pages", String(maxPages),
      "--max-chars", String(maxChars),
      "--allow-fallback", allowFallback && !configured ? "true" : "false",
    ];
    if (configured) args.push("--module", resolve(configured));
    const child = execFile(
      process.execPath,
      args,
      {
        timeout: Math.max(1, Math.min(45_000, Math.trunc(timeoutMs))),
        windowsHide: true,
        maxBuffer: 2 * 1024 * 1024,
        env: pdfParserEnvironment(),
      },
      (error, stdout, stderr) => {
        if (error) {
          const detail = String(stderr || stdout || error.message).trim().slice(0, 2000);
          const resourceFailure = Boolean(error.killed || error.signal)
            || error.code === "ERR_CHILD_PROCESS_STDIO_MAXBUFFER"
            || /heap out of memory|allocation failed|timed?\s*out/iu.test(detail);
          reject(new Error(`${resourceFailure ? "PDFJS_RESOURCE" : "PDFJS_PARSE"}: ${detail || "隔离解析失败"}`));
          return;
        }
        try {
          const lastLine = String(stdout).trim().split(/\r?\n/u).at(-1) ?? "";
          const parsed = JSON.parse(lastLine) as { pages?: unknown; totalPages?: unknown; method?: unknown };
          if (
            !Array.isArray(parsed.pages) || !Number.isInteger(parsed.totalPages) ||
            (parsed.method !== "pdfjs-dist" && parsed.method !== "builtin-text-layer")
          ) throw new Error("解析器返回结构无效");
          resolvePromise(parsed as { pages: SourcePage[]; totalPages: number; method: "pdfjs-dist" | "builtin-text-layer" });
        } catch (parseError) {
          reject(new Error(`PDFJS_PARSE: ${(parseError as Error).message}`));
        }
      },
    );
    child.stdin?.on("error", () => { /* The callback reports early parser termination. */ });
    child.stdin?.end(Buffer.from(bytes));
  });
}

async function extractPdf(bytes: Uint8Array, options: PdfOptions): Promise<{
  pages: SourcePage[];
  text: string;
  truncated: boolean;
  method: string;
  totalPages: number;
  extractedPages: number;
}> {
  const maxPages = normalizeMaxPages(options.maxPages);
  const maxChars = normalizeMaxChars(options.maxChars);
  let extraction: { pages: SourcePage[]; totalPages: number };
  let method: string;
  try {
    const parsed = await pdfJsPages(bytes, maxPages, maxChars, options.deadlineMs, options.allowFallback !== false);
    extraction = parsed;
    method = parsed.method;
  } catch (error) {
    if ((error as Error).message.startsWith("PDFJS_RESOURCE:")) {
      throw new Error("PDF.js 解析超过隔离进程的时间或 256 MiB 资源预算，已终止");
    }
    throw new Error(`${options.allowFallback === false ? "在线 PDF" : "PDF"} 解析失败，已安全停止：${(error as Error).message}`);
  }
  const pages: SourcePage[] = [];
  const chunks: string[] = [];
  let used = 0;
  let characterTruncated = false;
  for (const page of extraction.pages) {
    const marker = `[第 ${page.page} 页]\n`;
    const separator = chunks.length > 0 ? "\n\n" : "";
    const available = maxChars - used - marker.length - separator.length;
    if (available <= 0) {
      characterTruncated = true;
      break;
    }
    const text = page.text.slice(0, available);
    if (text.length < page.text.length) characterTruncated = true;
    if (!text && !page.text) continue;
    chunks.push(`${marker}${text}`);
    used += separator.length + marker.length + text.length;
    pages.push({ page: page.page, text, chars: text.length });
    if (text.length < page.text.length) break;
  }
  const fullText = normalizeText(chunks.join("\n\n"));
  if (!fullText || fullText.replace(/\[第 \d+ 页\]/gu, "").trim().length < 10) {
    throw new Error("PDF 未包含可可靠读取的文本层；请提供可检索 PDF，或配置 WORKFLOW_PDFJS_MODULE 后重试");
  }
  const truncated = characterTruncated || extraction.totalPages > extraction.pages.length || pages.length < extraction.pages.length;
  return {
    pages,
    text: fullText,
    truncated,
    method,
    totalPages: extraction.totalPages,
    extractedPages: pages.length,
  };
}

function sourceSummary(text: string): string {
  return text.replace(/\s+/gu, " ").trim().slice(0, 800);
}

function withKnowledgeMutation(document: Omit<SourceDocument, "knowledge_mutation">): SourceDocument {
  const notes = [
    `extraction_method=${document.extraction_method}`,
    `extraction_reliability=${document.extraction_reliability}`,
    `content_sha256=${document.content_sha256}`,
    `etag=${document.etag ?? ""}`,
    `last_modified=${document.last_modified ?? ""}`,
    `total_pages=${document.total_pages ?? ""}`,
    `extracted_pages=${document.extracted_pages ?? ""}`,
    `truncated=${document.truncated ? "true" : "false"}`,
    `evidence_refs=${document.evidence_refs.join(" | ")}`,
    `summary=${document.summary}`,
  ].join("; ");
  return {
    ...document,
    knowledge_mutation: {
      operation: "insert",
      record_id: document.source_id,
      changes: {
        title: document.title,
        url: document.url ?? "",
        publisher: document.publisher,
        published_date: document.published_date.slice(0, 10),
        accessed_date: document.accessed_at.slice(0, 10),
        region: "",
        topic: "",
        source_type: document.source_type,
        quality: "",
        exposure_status: "not-assessed",
        status: "pending",
        notes,
      },
    },
  };
}

export async function openWebSource(value: string, options: PdfOptions = {}): Promise<SourceDocument> {
  const controller = new AbortController();
  const deadlineAt = Date.now() + 30_000;
  const deadline = setTimeout(() => controller.abort(), 30_000);
  try {
    const url = normalizePublicUrl(value);
    const target = await resolvePublicNetworkTarget(url);
    if (controller.signal.aborted) throw new Error("网页正文读取超过 30 秒总时限");
    const response = await (sourceRequestOverride ?? pinnedSourceRequest)(url, target, controller.signal);
    if (!response.ok) {
      await response.body?.cancel();
      throw new Error(`网页正文读取失败（HTTP ${response.status}）`);
    }
    const rawContentType = response.headers.get("Content-Type") ?? "";
    const contentType = rawContentType.split(";", 1)[0]!.trim().toLowerCase();
    const accepted = new Set(["text/html", "text/plain", "application/xhtml+xml", "application/pdf"]);
    if (!accepted.has(contentType)) {
      await response.body?.cancel();
      throw new Error(`不支持的网页正文类型：${contentType || "missing"}`);
    }
    const contentEncoding = (response.headers.get("Content-Encoding") ?? "identity").trim().toLowerCase();
    if (contentEncoding && contentEncoding !== "identity") {
      await response.body?.cancel();
      throw new Error(`网页正文返回未允许的压缩编码：${contentEncoding}`);
    }
    const bytes = await responseBytes(response, MAX_WEB_BODY_BYTES, controller.signal);
    const contentSha256 = sha256(bytes);
    const accessedAt = new Date().toISOString();
    const etag = (response.headers.get("ETag") ?? "").slice(0, 500);
    const lastModified = (response.headers.get("Last-Modified") ?? "").slice(0, 500);
    if (contentType === "application/pdf") {
      const remainingMs = deadlineAt - Date.now();
      if (remainingMs <= 0) throw new Error("网页正文读取及 PDF 提取超过 30 秒总时限");
      const extracted = await extractPdf(bytes, { ...options, deadlineMs: remainingMs, allowFallback: false });
      if (Date.now() > deadlineAt) throw new Error("网页正文读取及 PDF 提取超过 30 秒总时限");
      const configuredName = url.pathname.split("/").filter(Boolean).at(-1) ?? "在线 PDF";
      let fallbackTitle = configuredName;
      try { fallbackTitle = decodeURIComponent(configuredName); } catch { /* Keep the encoded filename. */ }
      return withKnowledgeMutation({
        source_id: `pdf-${sha256(`${url.toString()}\n${contentSha256}`).slice(0, 20)}`,
        source_type: "pdf",
        title: options.title?.trim().slice(0, 500) || fallbackTitle.slice(0, 500),
        url: url.toString(),
        publisher: url.hostname,
        published_date: "",
        accessed_at: accessedAt,
        content_type: contentType,
        content_sha256: contentSha256,
        etag,
        last_modified: lastModified,
        extraction_method: extracted.method,
        extraction_reliability: extracted.method === "pdfjs-dist" ? "standard" : "limited",
        text: extracted.text,
        summary: sourceSummary(extracted.text),
        pages: extracted.pages,
        truncated: extracted.truncated,
        evidence_refs: extracted.pages.filter((page) => page.text).map((page) => `${url}#page=${page.page}`),
        total_pages: extracted.totalPages,
        extracted_pages: extracted.extractedPages,
      });
    }
    const charset = /charset\s*=\s*["']?([^;"'\s]+)/iu.exec(rawContentType)?.[1]?.trim().toLowerCase() || "utf-8";
    let raw: string;
    try {
      raw = new TextDecoder(charset, { fatal: false }).decode(bytes);
    } catch {
      throw new Error(`不支持的网页字符编码：${charset}`);
    }
    const extracted = contentType === "text/plain" ? { title: "", publishedDate: "", text: normalizeText(raw) } : extractHtml(raw);
    const maxChars = normalizeMaxChars(options.maxChars);
    if (extracted.text.length < 10) throw new Error("网页正文为空或不包含可读取文本");
    const title = extracted.title || url.hostname;
    return withKnowledgeMutation({
      source_id: `web-${sha256(`${url.toString()}\n${contentSha256}`).slice(0, 20)}`,
      source_type: "web",
      title: title.slice(0, 500),
      url: url.toString(),
      publisher: url.hostname,
      published_date: extracted.publishedDate,
      accessed_at: accessedAt,
      content_type: contentType,
      content_sha256: contentSha256,
      etag,
      last_modified: lastModified,
      extraction_method: contentType === "text/plain" ? "plain-text" : "sanitized-html",
      extraction_reliability: contentType === "text/plain" ? "standard" : "limited",
      text: extracted.text.slice(0, maxChars),
      summary: sourceSummary(extracted.text),
      pages: [],
      truncated: extracted.text.length > maxChars,
      evidence_refs: [url.toString()],
    });
  } finally {
    clearTimeout(deadline);
  }
}

function isContained(root: string, candidate: string): boolean {
  const containment = relative(root, candidate);
  return containment === "" || (!containment.startsWith("..") && !isAbsolute(containment));
}

export async function readLocalPdf(projectRoot: string, configuredPath: string, options: PdfOptions = {}): Promise<SourceDocument> {
  if (!configuredPath.trim() || isAbsolute(configuredPath) || configuredPath.includes("\0")) throw new Error("PDF 路径必须是项目内相对路径");
  const root = realpathSync.native(resolve(projectRoot));
  const candidate = resolve(root, configuredPath);
  if (!isContained(root, candidate)) throw new Error("PDF 路径越出项目目录");
  const allowedRoots = [resolve(root, "inputs"), resolve(root, "data", "inbox")];
  if (!allowedRoots.some((allowed) => isContained(allowed, candidate))) {
    throw new Error("本地 PDF 只能放在 inputs/ 或 data/inbox/ 下");
  }
  if (!candidate.toLowerCase().endsWith(".pdf")) throw new Error("本地资料读取只接受 .pdf 文件");
  if (!existsSync(candidate)) throw new Error(`PDF 文件不存在：${configuredPath}`);
  const metadata = lstatSync(candidate);
  if (!metadata.isFile() || metadata.isSymbolicLink()) throw new Error("拒绝读取非普通文件或符号链接 PDF");
  if (metadata.size > MAX_LOCAL_PDF_BYTES) throw new Error("PDF 超过 32 MiB 安全上限");
  const canonical = realpathSync.native(candidate);
  if (!isContained(root, canonical) || !allowedRoots.some((allowed) => isContained(allowed, canonical))) {
    throw new Error("PDF 规范路径越出受控输入目录");
  }
  let bytes: Uint8Array;
  let openedIdentity: { dev: number; ino: number };
  const handle = openSync(canonical, "r");
  try {
    const openedMetadata = fstatSync(handle);
    if (!openedMetadata.isFile()) throw new Error("打开后的 PDF 不是普通文件");
    if (openedMetadata.size > MAX_LOCAL_PDF_BYTES) throw new Error("PDF 超过 32 MiB 安全上限");
    if (openedMetadata.dev !== metadata.dev || openedMetadata.ino !== metadata.ino) throw new Error("PDF 在打开前已发生替换，请重试");
    openedIdentity = { dev: openedMetadata.dev, ino: openedMetadata.ino };
    bytes = new Uint8Array(readFileSync(handle));
    if (bytes.byteLength > MAX_LOCAL_PDF_BYTES) throw new Error("PDF 读取期间超过 32 MiB 安全上限");
  } finally {
    closeSync(handle);
  }
  const afterRead = lstatSync(candidate);
  if (!afterRead.isFile() || afterRead.isSymbolicLink() || afterRead.dev !== openedIdentity.dev || afterRead.ino !== openedIdentity.ino) {
    throw new Error("PDF 在读取期间已发生替换，请重试");
  }
  if (Buffer.from(bytes.subarray(0, 5)).toString("ascii") !== "%PDF-") throw new Error("文件头不是有效 PDF");
  const extracted = await extractPdf(bytes, options);
  const relativePath = relative(root, canonical).replaceAll("\\", "/");
  const accessedAt = new Date().toISOString();
  return withKnowledgeMutation({
    source_id: `pdf-${sha256(bytes).slice(0, 20)}`,
    source_type: "pdf",
    title: options.title?.trim().slice(0, 500) || relativePath.split("/").at(-1)!.replace(/\.pdf$/iu, ""),
    path: relativePath,
    publisher: "",
    published_date: "",
    accessed_at: accessedAt,
    content_type: "application/pdf",
    content_sha256: sha256(bytes),
    extraction_method: extracted.method,
    extraction_reliability: extracted.method === "pdfjs-dist" ? "standard" : "limited",
    text: extracted.text,
    summary: sourceSummary(extracted.text),
    pages: extracted.pages,
    truncated: extracted.truncated,
    evidence_refs: extracted.pages.filter((page) => page.text).map((page) => `${relativePath}#page=${page.page}`),
    total_pages: extracted.totalPages,
    extracted_pages: extracted.extractedPages,
  });
}
