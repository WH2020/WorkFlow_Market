import { existsSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { inflateSync } from "node:zlib";

const MAX_INPUT_BYTES = 32 * 1024 * 1024;
const MAX_OBJECTS = 5000;
const MAX_STREAMS = 64;
const MAX_REFS_PER_PAGE = 256;
const MAX_INFLATED_STREAM_BYTES = 8 * 1024 * 1024;
const MAX_STREAM_TEXT_CHARS = 250_000;

function parseArgs(argv) {
  const values = new Map();
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || value === undefined) throw new Error("PDF parser arguments are invalid");
    values.set(key, value);
  }
  const maxPages = Number(values.get("--max-pages"));
  const maxChars = Number(values.get("--max-chars"));
  if (!Number.isInteger(maxPages) || maxPages < 1 || maxPages > 200) throw new Error("max-pages is invalid");
  if (!Number.isInteger(maxChars) || maxChars < 1000 || maxChars > 200000) throw new Error("max-chars is invalid");
  const configured = values.get("--module");
  if (configured && !existsSync(configured)) throw new Error("configured PDF.js module does not exist");
  const allowFallbackValue = values.get("--allow-fallback");
  if (allowFallbackValue !== "true" && allowFallbackValue !== "false") throw new Error("allow-fallback is invalid");
  return { maxPages, maxChars, configured, allowFallback: allowFallbackValue === "true" };
}

function normalizeText(value) {
  return value
    .replace(/\r\n?/gu, "\n")
    .replace(/[\t\f\v ]+/gu, " ")
    .replace(/ *\n */gu, "\n")
    .replace(/\n{3,}/gu, "\n\n")
    .trim();
}

async function readInput() {
  const chunks = [];
  let total = 0;
  for await (const chunk of process.stdin) {
    total += chunk.length;
    if (total > MAX_INPUT_BYTES) throw new Error("PDF input exceeds 32 MiB");
    chunks.push(chunk);
  }
  return new Uint8Array(Buffer.concat(chunks, total));
}

function decodePdfLiteral(value) {
  let result = "";
  for (let index = 0; index < value.length; index += 1) {
    const char = value[index];
    if (char !== "\\") {
      result += char;
      continue;
    }
    const next = value[++index];
    if (next === undefined) break;
    const mapped = { n: "\n", r: "\r", t: "\t", b: "\b", f: "\f", "(": "(", ")": ")", "\\": "\\" };
    if (mapped[next] !== undefined) {
      result += mapped[next];
      continue;
    }
    if (/[0-7]/u.test(next)) {
      let octal = next;
      while (octal.length < 3 && /[0-7]/u.test(value[index + 1] ?? "")) octal += value[++index];
      result += String.fromCharCode(Number.parseInt(octal, 8));
      continue;
    }
    if (next !== "\n" && next !== "\r") result += next;
  }
  return result;
}

function decodePdfHex(value) {
  const compact = value.replace(/\s+/gu, "");
  const even = compact.length % 2 === 0 ? compact : `${compact}0`;
  const bytes = Buffer.from(even, "hex");
  if (bytes.length >= 2 && bytes[0] === 0xfe && bytes[1] === 0xff) {
    let output = "";
    for (let index = 2; index + 1 < bytes.length; index += 2) output += String.fromCharCode(bytes.readUInt16BE(index));
    return output;
  }
  return bytes.toString("utf8").replace(/\uFFFD/gu, "");
}

function extractPdfOperators(content) {
  const output = [];
  let remaining = MAX_STREAM_TEXT_CHARS;
  const append = (value) => {
    if (remaining <= 0 || !value) return;
    const clipped = value.slice(0, remaining);
    output.push(clipped);
    remaining -= clipped.length;
  };
  for (const block of content.matchAll(/BT([\s\S]*?)ET/gu)) {
    if (remaining <= 0) break;
    const body = block[1] ?? "";
    const tokens = /\(((?:\\.|[^\\()])*)\)\s*Tj|<([0-9a-f\s]+)>\s*Tj|\[((?:\\.|[^\]])*)\]\s*TJ|T\*|\bTd\b|\bTD\b/giu;
    for (const match of body.matchAll(tokens)) {
      if (remaining <= 0) break;
      if (match[1] !== undefined) append(decodePdfLiteral(match[1]));
      else if (match[2] !== undefined) append(decodePdfHex(match[2]));
      else if (match[3] !== undefined) {
        const parts = [];
        for (const item of match[3].matchAll(/\(((?:\\.|[^\\()])*)\)|<([0-9a-f\s]+)>/giu)) {
          parts.push(item[1] !== undefined ? decodePdfLiteral(item[1]) : decodePdfHex(item[2] ?? ""));
        }
        append(parts.join(""));
      } else {
        append("\n");
      }
    }
    append("\n");
  }
  return normalizeText(output.join(" "));
}

function fallbackPdfPages(bytes, maxPages, maxChars) {
  const source = Buffer.from(bytes).toString("latin1");
  const objects = new Map();
  for (const match of source.matchAll(/(?:^|\r?\n)(\d+)\s+\d+\s+obj\b([\s\S]*?)endobj/gu)) {
    objects.set(Number(match[1]), match[2] ?? "");
    if (objects.size > MAX_OBJECTS) throw new Error(`fallback object count exceeds ${MAX_OBJECTS}`);
  }
  const pageObjects = [...objects.values()].filter((body) => /\/Type\s*\/Page(?!s)\b/u.test(body));
  const cache = new Map();
  let streamCount = 0;
  const extractReference = (reference) => {
    if (cache.has(reference)) return cache.get(reference);
    streamCount += 1;
    if (streamCount > MAX_STREAMS) throw new Error(`fallback stream count exceeds ${MAX_STREAMS}`);
    const objectBody = objects.get(reference) ?? "";
    const match = /stream\r?\n([\s\S]*?)\r?\nendstream/gu.exec(objectBody);
    if (!match) {
      cache.set(reference, "");
      return "";
    }
    let streamBytes = Buffer.from(match[1] ?? "", "latin1");
    if (/\/FlateDecode\b/u.test(objectBody.slice(0, match.index))) {
      try {
        streamBytes = inflateSync(streamBytes, { maxOutputLength: MAX_INFLATED_STREAM_BYTES });
      } catch {
        cache.set(reference, "");
        return "";
      }
    }
    const text = extractPdfOperators(streamBytes.toString("latin1"));
    cache.set(reference, text);
    return text;
  };
  const joinBounded = (values, limit) => {
    const output = [];
    let remaining = limit;
    for (const value of values) {
      if (remaining <= 0) break;
      const clipped = value.slice(0, remaining);
      if (clipped) output.push(clipped);
      remaining -= clipped.length;
    }
    return normalizeText(output.join("\n"));
  };
  const pages = [];
  let remainingChars = maxChars;
  for (const pageBody of pageObjects.slice(0, maxPages)) {
    if (remainingChars <= 0) break;
    const contents = /\/Contents\s*(?:\[\s*)?((?:\d+\s+\d+\s+R\s*)+)/u.exec(pageBody)?.[1] ?? "";
    const refs = [...contents.matchAll(/(\d+)\s+\d+\s+R/gu)].slice(0, MAX_REFS_PER_PAGE).map((match) => Number(match[1]));
    const text = joinBounded((function* () { for (const reference of refs) yield extractReference(reference); })(), remainingChars);
    remainingChars -= text.length;
    pages.push({ page: pages.length + 1, text, chars: text.length });
  }
  if (pages.length === 0 && remainingChars > 0) {
    const references = [...objects.keys()].slice(0, MAX_STREAMS);
    const text = joinBounded((function* () { for (const reference of references) yield extractReference(reference); })(), remainingChars);
    if (text) pages.push({ page: 1, text, chars: text.length });
  }
  return { pages, totalPages: pageObjects.length || pages.length, method: "builtin-text-layer" };
}

async function main() {
  const { maxPages, maxChars, configured, allowFallback } = parseArgs(process.argv.slice(2));
  const bytes = await readInput();
  const moduleSpecifier = configured ? pathToFileURL(configured).href : "pdfjs-dist/legacy/build/pdf.mjs";
  let document;
  try {
    const module = await import(moduleSpecifier);
    document = await module.getDocument({ data: bytes.slice(), disableWorker: true, useSystemFonts: true }).promise;
    const pages = [];
    let remaining = maxChars;
    const pageLimit = Math.min(document.numPages, maxPages);
    for (let pageNumber = 1; pageNumber <= pageLimit && remaining > 0; pageNumber += 1) {
      const page = await document.getPage(pageNumber);
      const content = await page.getTextContent();
      const fragments = [];
      let pageRemaining = remaining;
      for (const item of content.items) {
        if (pageRemaining <= 0) break;
        if (typeof item.str !== "string") continue;
        const fragment = `${item.str}${item.hasEOL ? "\n" : " "}`.slice(0, pageRemaining);
        fragments.push(fragment);
        pageRemaining -= fragment.length;
      }
      const text = normalizeText(fragments.join(""));
      remaining -= text.length;
      pages.push({ page: pageNumber, text, chars: text.length });
    }
    process.stdout.write(`${JSON.stringify({ pages, totalPages: document.numPages, method: "pdfjs-dist" })}\n`);
  } catch (error) {
    if (!allowFallback) throw error;
    process.stdout.write(`${JSON.stringify(fallbackPdfPages(bytes, maxPages, maxChars))}\n`);
  } finally {
    if (document && typeof document.destroy === "function") await document.destroy();
  }
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.stack || error.message : String(error)}\n`);
  process.exitCode = 1;
});
