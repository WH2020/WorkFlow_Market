import { execFile as execFileCallback } from "node:child_process";
import fs from "node:fs/promises";
import { basename, extname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { promisify } from "node:util";

import { createCanvas, DOMMatrix, ImageData, loadImage, Path2D } from "@napi-rs/canvas";
import JSZip from "jszip";
import { findLibreOffice } from "./validate-and-render-deck.mjs";

const execFile = promisify(execFileCallback);
const MAX_DOCUMENT_BYTES = 100 * 1024 * 1024;

function parseArguments(argv) {
  const inputIndex = argv.indexOf("--input");
  const qaIndex = argv.indexOf("--qa-dir");
  if (inputIndex < 0 || qaIndex < 0) {
    throw new Error("usage: validate-and-render-document.mjs --input artifact.docx --qa-dir qa");
  }
  return { input: resolve(argv[inputIndex + 1]), qaDir: resolve(argv[qaIndex + 1]) };
}

function decodeXml(value) {
  return value.replaceAll("&lt;", "<").replaceAll("&gt;", ">").replaceAll("&quot;", '"').replaceAll("&apos;", "'").replaceAll("&amp;", "&");
}

async function validateStructure(input, qaDir) {
  const metadata = await fs.stat(input);
  if (!metadata.isFile() || metadata.size < 1 || metadata.size > MAX_DOCUMENT_BYTES || extname(input).toLowerCase() !== ".docx") {
    throw new Error("DOCX 文件不存在、为空、过大或扩展名无效");
  }
  const archive = await JSZip.loadAsync(await fs.readFile(input), { checkCRC32: true });
  const required = [
    "[Content_Types].xml", "_rels/.rels", "word/document.xml", "word/styles.xml",
    "word/settings.xml", "word/header1.xml", "word/footer1.xml", "word/_rels/document.xml.rels",
  ];
  for (const name of required) if (!archive.file(name)) throw new Error(`DOCX 缺少必要结构：${name}`);
  const documentXml = await archive.file("word/document.xml").async("string");
  const text = decodeXml([...documentXml.matchAll(/<w:t(?:\s[^>]*)?>([\s\S]*?)<\/w:t>/gu)].map((match) => match[1]).join(""));
  if (text.length < 50 || !text.includes("来源与证据附录") || !text.includes("生成边界")) {
    throw new Error("DOCX 正文、来源附录或生成边界缺失");
  }
  const relationships = await archive.file("word/_rels/document.xml.rels").async("string");
  if (/TargetMode="External"/iu.test(relationships)) throw new Error("正式标书不能包含外部关系或自动加载资源");
  const manifest = JSON.parse(await fs.readFile(join(qaDir, "document-layout.json"), "utf8"));
  if (
    manifest.schema_version !== "1.0" || !Number.isInteger(manifest.sections) || manifest.sections < 1 ||
    !Number.isInteger(manifest.paragraphs) || manifest.paragraphs < 1 ||
    !Number.isInteger(manifest.sources) || manifest.sources < 1
  ) throw new Error("文档构建清单无效");
  return { bytes: metadata.size, xml_text_characters: text.length, manifest };
}

async function renderPdf(pdfPath, qaDir) {
  globalThis.DOMMatrix = DOMMatrix;
  globalThis.ImageData = ImageData;
  globalThis.Path2D = Path2D;
  const { getDocument } = await import("pdfjs-dist/legacy/build/pdf.mjs");
  const loading = getDocument({
    data: new Uint8Array(await fs.readFile(pdfPath)),
    disableWorker: true,
    isEvalSupported: false,
    useSystemFonts: true,
  });
  const document = await loading.promise;
  if (document.numPages < 2 || document.numPages > 500) throw new Error(`LibreOffice 渲染页数异常：${document.numPages}`);
  const previews = [];
  const pageTextCounts = [];
  for (let pageNumber = 1; pageNumber <= document.numPages; pageNumber += 1) {
    const page = await document.getPage(pageNumber);
    const text = await page.getTextContent();
    const characters = text.items.reduce((total, item) => total + (typeof item.str === "string" ? item.str.trim().length : 0), 0);
    pageTextCounts.push(characters);
    if (characters < 1) throw new Error(`第 ${pageNumber} 页渲染后没有可识别文本，可能存在空白页或内容丢失`);
    const viewport = page.getViewport({ scale: 1.25 });
    const canvas = createCanvas(Math.ceil(viewport.width), Math.ceil(viewport.height));
    const context = canvas.getContext("2d");
    context.fillStyle = "#FFFFFF";
    context.fillRect(0, 0, canvas.width, canvas.height);
    await page.render({ canvas, canvasContext: context, viewport }).promise;
    const path = join(qaDir, `page-${String(pageNumber).padStart(3, "0")}.png`);
    await fs.writeFile(path, canvas.toBuffer("image/png"));
    previews.push(path);
  }
  await document.cleanup();
  await loading.destroy();
  return { pageCount: previews.length, previews, pageTextCounts };
}

async function montage(previews, output) {
  const images = await Promise.all(previews.map((path) => loadImage(path)));
  const columns = Math.min(4, images.length);
  const rows = Math.ceil(images.length / columns);
  const cellWidth = 300;
  const cellHeight = 440;
  const canvas = createCanvas(columns * cellWidth, rows * cellHeight);
  const context = canvas.getContext("2d");
  context.fillStyle = "#E8EDF2";
  context.fillRect(0, 0, canvas.width, canvas.height);
  images.forEach((image, index) => {
    const column = index % columns;
    const row = Math.floor(index / columns);
    const x = column * cellWidth + 12;
    const y = row * cellHeight + 12;
    const maxWidth = 276;
    const maxHeight = 390;
    const scale = Math.min(maxWidth / image.width, maxHeight / image.height);
    const width = image.width * scale;
    const height = image.height * scale;
    context.fillStyle = "#FFFFFF";
    context.fillRect(x, y, maxWidth, maxHeight);
    context.drawImage(image, x + (maxWidth - width) / 2, y, width, height);
    context.fillStyle = "#263442";
    context.font = "14px sans-serif";
    context.fillText(`第 ${index + 1} 页`, x, y + 416);
  });
  await fs.writeFile(output, canvas.toBuffer("image/png"));
}

async function renderWithLibreOffice(input, qaDir) {
  const soffice = await findLibreOffice();
  const renderDirectory = join(qaDir, "rendered-pdf");
  const profileDirectory = join(qaDir, ".libreoffice-profile");
  await fs.mkdir(renderDirectory, { recursive: true });
  await fs.mkdir(profileDirectory, { recursive: true });
  try {
    await execFile(soffice, [
      `-env:UserInstallation=${pathToFileURL(profileDirectory).href}`,
      "--headless", "--nologo", "--nodefault", "--nolockcheck", "--nofirststartwizard",
      "--convert-to", "pdf:writer_pdf_Export", "--outdir", renderDirectory, input,
    ], { timeout: 240_000, windowsHide: true, maxBuffer: 2 * 1024 * 1024 });
    const pdfs = (await fs.readdir(renderDirectory)).filter((name) => name.toLowerCase().endsWith(".pdf"));
    if (pdfs.length !== 1) throw new Error(`LibreOffice 未生成唯一 PDF：${pdfs.join(", ") || "none"}`);
    const pdf = join(renderDirectory, pdfs[0]);
    const rendered = await renderPdf(pdf, qaDir);
    const montagePath = join(qaDir, "document-montage.png");
    await montage(rendered.previews, montagePath);
    let version = "LibreOffice";
    try {
      const checked = await execFile(soffice, ["--version"], { timeout: 15_000, windowsHide: true });
      version = String(checked.stdout || checked.stderr || version).trim() || version;
    } catch { /* successful render is authoritative */ }
    return { renderer: version, pdf, montage: montagePath, ...rendered };
  } finally {
    await fs.rm(profileDirectory, { recursive: true, force: true });
  }
}

async function main() {
  const paths = parseArguments(process.argv.slice(2));
  await fs.mkdir(paths.qaDir, { recursive: true });
  const structure = await validateStructure(paths.input, paths.qaDir);
  const render = await renderWithLibreOffice(paths.input, paths.qaDir);
  const renderedText = render.pageTextCounts.reduce((total, value) => total + value, 0);
  if (renderedText < Math.min(50, Math.floor(structure.xml_text_characters * 0.2))) {
    throw new Error("渲染后可识别文本明显不足，可能存在内容丢失");
  }
  const result = {
    status: "ok",
    message: "Test passed. Document rendered without blank pages.",
    input: paths.input,
    filename: basename(paths.input),
    structure,
    renderer: render.renderer,
    page_count: render.pageCount,
    preview_count: render.previews.length,
    preview_directory: paths.qaDir,
    montage: render.montage,
    pdf: render.pdf,
  };
  process.stdout.write(`${JSON.stringify(result)}\n`);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.stack || error.message : String(error)}\n`);
    process.exitCode = 1;
  });
}
