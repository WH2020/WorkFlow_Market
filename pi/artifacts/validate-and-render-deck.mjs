import { execFile as execFileCallback } from "node:child_process";
import fs from "node:fs/promises";
import { constants as fsConstants } from "node:fs";
import os from "node:os";
import { basename, delimiter, dirname, extname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { promisify } from "node:util";

import { createCanvas, DOMMatrix, ImageData, loadImage, Path2D } from "@napi-rs/canvas";
import JSZip from "jszip";

const execFile = promisify(execFileCallback);
const MAX_DECK_BYTES = 50 * 1024 * 1024;

function parseArguments(argv) {
  const inputIndex = argv.indexOf("--input");
  const qaIndex = argv.indexOf("--qa-dir");
  const countIndex = argv.indexOf("--expected-slides");
  if (inputIndex < 0 || qaIndex < 0 || countIndex < 0) {
    throw new Error("usage: validate-and-render-deck.mjs --input deck.pptx --qa-dir qa --expected-slides 4");
  }
  const expectedSlides = Number(argv[countIndex + 1]);
  if (!Number.isInteger(expectedSlides) || expectedSlides < 4 || expectedSlides > 10) {
    throw new Error("expected slide count must be an integer from 4 to 10");
  }
  return {
    input: resolve(argv[inputIndex + 1]),
    qaDir: resolve(argv[qaIndex + 1]),
    expectedSlides,
  };
}

async function isExecutable(path) {
  try {
    await fs.access(path, process.platform === "win32" ? fsConstants.F_OK : fsConstants.X_OK);
    return (await fs.stat(path)).isFile();
  } catch {
    return false;
  }
}

function executableNames() {
  return process.platform === "win32" ? ["soffice.com", "soffice.exe"] : ["soffice"];
}

export async function findLibreOffice(environment = process.env, platform = process.platform) {
  const explicit = environment.WORKFLOW_LIBREOFFICE_PATH?.trim();
  if (explicit) {
    const resolved = resolve(explicit);
    if (!await isExecutable(resolved)) throw new Error("WORKFLOW_LIBREOFFICE_PATH 不是可执行的 LibreOffice 文件");
    return resolved;
  }
  const candidates = [];
  const pathValue = environment.PATH || environment.Path || "";
  for (const directory of pathValue.split(delimiter).filter(Boolean)) {
    for (const name of executableNames()) candidates.push(resolve(directory, name));
  }
  if (platform === "win32") {
    for (const root of [environment.ProgramFiles, environment["ProgramFiles(x86)"], environment.LOCALAPPDATA].filter(Boolean)) {
      candidates.push(resolve(root, "LibreOffice", "program", "soffice.com"));
      candidates.push(resolve(root, "LibreOffice", "program", "soffice.exe"));
    }
  } else if (platform === "darwin") {
    candidates.push("/Applications/LibreOffice.app/Contents/MacOS/soffice");
    candidates.push(resolve(os.homedir(), "Applications", "LibreOffice.app", "Contents", "MacOS", "soffice"));
    candidates.push("/opt/homebrew/bin/soffice", "/usr/local/bin/soffice");
  } else {
    candidates.push("/usr/bin/soffice", "/usr/local/bin/soffice", "/snap/bin/libreoffice");
  }
  for (const candidate of [...new Set(candidates)]) {
    if (await isExecutable(candidate)) return resolve(candidate);
  }
  throw new Error("未找到 LibreOffice。请运行对应平台 setup 安装器，或设置 WORKFLOW_LIBREOFFICE_PATH");
}

function decodeXml(value) {
  return value
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&quot;", '"')
    .replaceAll("&apos;", "'")
    .replaceAll("&amp;", "&");
}

function numericPart(path) {
  return Number(path.match(/(\d+)\.xml$/u)?.[1] || 0);
}

async function validateStructure(input, expectedSlides) {
  const metadata = await fs.stat(input);
  if (!metadata.isFile() || metadata.size < 1 || metadata.size > MAX_DECK_BYTES || extname(input).toLowerCase() !== ".pptx") {
    throw new Error("PPTX 文件不存在、为空、过大或扩展名无效");
  }
  const archive = await JSZip.loadAsync(await fs.readFile(input), { checkCRC32: true });
  const names = Object.keys(archive.files);
  const slides = names.filter((name) => /^ppt\/slides\/slide\d+\.xml$/u.test(name)).sort((a, b) => numericPart(a) - numericPart(b));
  const notes = names.filter((name) => /^ppt\/notesSlides\/notesSlide\d+\.xml$/u.test(name)).sort((a, b) => numericPart(a) - numericPart(b));
  if (slides.length !== expectedSlides || notes.length !== expectedSlides) {
    throw new Error(`PPTX 页数或 speaker notes 数量无效：slides=${slides.length}, notes=${notes.length}`);
  }
  let editableTextSlides = 0;
  for (const slideName of slides) {
    const xml = await archive.file(slideName).async("string");
    const textNodes = [...xml.matchAll(/<a:t>([\s\S]*?)<\/a:t>/gu)];
    if (textNodes.length === 0) throw new Error(`PPTX 页面不含可编辑文本对象：${slideName}`);
    editableTextSlides += 1;
  }
  for (const noteName of notes) {
    const xml = await archive.file(noteName).async("string");
    const text = decodeXml([...xml.matchAll(/<a:t>([\s\S]*?)<\/a:t>/gu)].map((match) => match[1]).join(""));
    if ((text.match(/\[Sources\]/gu) || []).length !== 1 || (text.match(/\[\/Sources\]/gu) || []).length !== 1) {
      throw new Error(`speaker notes 来源块无效：${noteName}`);
    }
  }
  return {
    bytes: metadata.size,
    slide_count: slides.length,
    notes_count: notes.length,
    editable_text_slides: editableTextSlides,
  };
}

function overlap(first, second) {
  return Math.max(0, Math.min(first.x + first.w, second.x + second.w) - Math.max(first.x, second.x))
    * Math.max(0, Math.min(first.y + first.h, second.y + second.h) - Math.max(first.y, second.y));
}

async function validateLayouts(qaDirectory, expectedSlides) {
  const results = [];
  for (let slide = 1; slide <= expectedSlides; slide += 1) {
    const path = join(qaDirectory, `slide-${String(slide).padStart(2, "0")}.layout.json`);
    const layout = JSON.parse(await fs.readFile(path, "utf8"));
    if (layout.slide !== slide || !Array.isArray(layout.elements) || layout.width <= 0 || layout.height <= 0) {
      throw new Error(`第 ${slide} 页 layout manifest 无效`);
    }
    const textElements = [];
    for (const element of layout.elements) {
      const values = [element.x, element.y, element.w, element.h];
      if (values.some((value) => !Number.isFinite(value)) || element.w <= 0 || element.h <= 0) {
        throw new Error(`第 ${slide} 页元素 ${element.name} 的几何信息无效`);
      }
      const epsilon = 0.003;
      if (element.x < -epsilon || element.y < -epsilon || element.x + element.w > layout.width + epsilon || element.y + element.h > layout.height + epsilon) {
        throw new Error(`第 ${slide} 页元素 ${element.name} 越出画布`);
      }
      if (element.type === "text") textElements.push(element);
    }
    for (let first = 0; first < textElements.length; first += 1) {
      for (let second = first + 1; second < textElements.length; second += 1) {
        if (overlap(textElements[first], textElements[second]) > 0.003) {
          throw new Error(`第 ${slide} 页文本框重叠：${textElements[first].name} / ${textElements[second].name}`);
        }
      }
    }
    results.push({ slide, elements: layout.elements.length, text_elements: textElements.length });
  }
  return results;
}

async function renderPdfToPng(pdfPath, qaDirectory, expectedSlides) {
  globalThis.DOMMatrix = DOMMatrix;
  globalThis.ImageData = ImageData;
  globalThis.Path2D = Path2D;
  const { getDocument } = await import("pdfjs-dist/legacy/build/pdf.mjs");
  const loadingTask = getDocument({
    data: new Uint8Array(await fs.readFile(pdfPath)),
    disableWorker: true,
    isEvalSupported: false,
    useSystemFonts: true,
  });
  const document = await loadingTask.promise;
  if (document.numPages !== expectedSlides) throw new Error(`LibreOffice 渲染页数不符：${document.numPages}`);
  const previews = [];
  for (let pageNumber = 1; pageNumber <= document.numPages; pageNumber += 1) {
    const page = await document.getPage(pageNumber);
    const viewport = page.getViewport({ scale: 1.5 });
    const canvas = createCanvas(Math.ceil(viewport.width), Math.ceil(viewport.height));
    const context = canvas.getContext("2d");
    context.fillStyle = "#FFFFFF";
    context.fillRect(0, 0, canvas.width, canvas.height);
    await page.render({ canvas, canvasContext: context, viewport }).promise;
    const path = join(qaDirectory, `slide-${String(pageNumber).padStart(2, "0")}.png`);
    await fs.writeFile(path, canvas.toBuffer("image/png"));
    previews.push(path);
  }
  await document.cleanup();
  await loadingTask.destroy();
  return previews;
}

async function createMontage(previews, outputPath) {
  const images = await Promise.all(previews.map((path) => loadImage(path)));
  const columns = Math.min(3, images.length);
  const rows = Math.ceil(images.length / columns);
  const cellWidth = 480;
  const cellHeight = 292;
  const canvas = createCanvas(columns * cellWidth, rows * cellHeight);
  const context = canvas.getContext("2d");
  context.fillStyle = "#E8EDF2";
  context.fillRect(0, 0, canvas.width, canvas.height);
  images.forEach((image, index) => {
    const column = index % columns;
    const row = Math.floor(index / columns);
    const x = column * cellWidth + 12;
    const y = row * cellHeight + 12;
    context.fillStyle = "#FFFFFF";
    context.fillRect(x, y, 456, 256.5);
    context.drawImage(image, x, y, 456, 256.5);
    context.fillStyle = "#263442";
    context.font = "14px sans-serif";
    context.fillText(`Slide ${index + 1}`, x, y + 275);
  });
  await fs.writeFile(outputPath, canvas.toBuffer("image/png"));
}

async function renderWithLibreOffice(input, qaDirectory, expectedSlides) {
  const soffice = await findLibreOffice();
  const renderDirectory = join(qaDirectory, "rendered-pdf");
  const profileDirectory = join(qaDirectory, ".libreoffice-profile");
  await fs.mkdir(renderDirectory, { recursive: true });
  await fs.mkdir(profileDirectory, { recursive: true });
  const profileUri = pathToFileURL(profileDirectory).href;
  try {
    await execFile(soffice, [
      `-env:UserInstallation=${profileUri}`,
      "--headless", "--nologo", "--nodefault", "--nolockcheck", "--nofirststartwizard",
      "--convert-to", "pdf:impress_pdf_Export", "--outdir", renderDirectory, input,
    ], { timeout: 180_000, windowsHide: true, maxBuffer: 2 * 1024 * 1024 });
    const pdfs = (await fs.readdir(renderDirectory)).filter((name) => name.toLowerCase().endsWith(".pdf"));
    if (pdfs.length !== 1) throw new Error(`LibreOffice 未生成唯一 PDF：${pdfs.join(", ") || "none"}`);
    const pdfPath = join(renderDirectory, pdfs[0]);
    const previews = await renderPdfToPng(pdfPath, qaDirectory, expectedSlides);
    const montagePath = join(qaDirectory, "deck-montage.png");
    await createMontage(previews, montagePath);
    let version = "LibreOffice";
    try {
      const checked = await execFile(soffice, ["--version"], { timeout: 15_000, windowsHide: true });
      version = String(checked.stdout || checked.stderr || version).trim() || version;
    } catch {
      // Rendering success is authoritative even when a GUI launcher does not return a version string.
    }
    return { renderer: version, pdf: pdfPath, previews, montage: montagePath };
  } finally {
    await fs.rm(profileDirectory, { recursive: true, force: true });
  }
}

async function main() {
  const paths = parseArguments(process.argv.slice(2));
  await fs.mkdir(paths.qaDir, { recursive: true });
  const structure = await validateStructure(paths.input, paths.expectedSlides);
  const layouts = await validateLayouts(paths.qaDir, paths.expectedSlides);
  const render = await renderWithLibreOffice(paths.input, paths.qaDir, paths.expectedSlides);
  const result = {
    status: "ok",
    message: "Test passed. No overflow detected.",
    input: paths.input,
    filename: basename(paths.input),
    structure,
    layouts,
    renderer: render.renderer,
    preview_count: render.previews.length,
    montage: render.montage,
  };
  process.stdout.write(`${JSON.stringify(result)}\n`);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.stack || error.message : String(error)}\n`);
    process.exitCode = 1;
  });
}
