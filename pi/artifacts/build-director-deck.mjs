import { execFile as execFileCallback } from "node:child_process";
import fs from "node:fs/promises";
import { basename, dirname, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { promisify } from "node:util";

const execFile = promisify(execFileCallback);

function parseArguments(argv) {
  const inputIndex = argv.indexOf("--input");
  const outputIndex = argv.indexOf("--output");
  const qaIndex = argv.indexOf("--qa-dir");
  if (inputIndex < 0 || outputIndex < 0 || qaIndex < 0 || !argv[inputIndex + 1] || !argv[outputIndex + 1] || !argv[qaIndex + 1]) {
    throw new Error("usage: build-director-deck.mjs --input payload.json --output deck.pptx --qa-dir qa");
  }
  return { input: resolve(argv[inputIndex + 1]), output: resolve(argv[outputIndex + 1]), qaDir: resolve(argv[qaIndex + 1]) };
}

async function loadArtifactTool() {
  const configured = process.env.WORKFLOW_ARTIFACT_TOOL_PATH?.trim();
  if (configured) {
    const modulePath = resolve(configured, "dist", "artifact_tool.mjs");
    return import(pathToFileURL(modulePath).href);
  }
  try {
    return await import("@oai/artifact-tool");
  } catch {
    throw new Error(
      "未找到 @oai/artifact-tool。请把 WORKFLOW_ARTIFACT_TOOL_PATH 设置为该包的绝对目录后重试。",
    );
  }
}

async function markArtifactOperation() {
  const marker = process.env.WORKFLOW_PRESENTATIONS_MARKER?.trim();
  if (!marker) {
    throw new Error(
      "缺少 WORKFLOW_PRESENTATIONS_MARKER；请设置为 Presentations Skill 的 mark_artifact_operation_started.mjs 绝对路径。",
    );
  }
  await execFile(process.execPath, [
    resolve(marker),
    "--operation-kind", "create",
    "--expected-output-count", "1",
    "--output-format", "pptx",
  ], { timeout: 30_000, windowsHide: true });
}

function addText(slide, name, text, position, style) {
  const shape = slide.shapes.add({
    geometry: "textbox",
    name,
    position,
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  shape.text = text;
  shape.text.style = style;
  return shape;
}

function sourceNotes(slide) {
  const sources = Array.isArray(slide.sources) ? slide.sources : [];
  const rows = sources.map((source) => {
    const locator = source.url || source.path || "local-evidence";
    const page = source.page ? `#page=${source.page}` : "";
    return `- ${source.title || "来源"} | ${locator}${page}`;
  });
  return [
    slide.notes?.trim() || "",
    "[Sources]",
    ...(rows.length > 0 ? rows : ["- No external source; compiled from the approved local weekly snapshot."]),
    "[/Sources]",
  ].filter(Boolean).join("\n");
}

function addFooter(slide, period, pageNumber, totalPages) {
  const y = 674;
  slide.shapes.add({
    geometry: "line",
    name: `footer-rule-${pageNumber}`,
    position: { left: 72, top: y - 14, width: 1136, height: 1 },
    fill: "none",
    line: { style: "solid", fill: "#B8BCC4", width: 1 },
  });
  addText(
    slide,
    `footer-period-${pageNumber}`,
    `${period.start} 至 ${period.end}`,
    { left: 72, top: y, width: 360, height: 22 },
    { fontSize: 12, color: "#62666D", fontFamily: "Microsoft YaHei" },
  );
  addText(
    slide,
    `footer-page-${pageNumber}`,
    `${pageNumber} / ${totalPages}`,
    { left: 1080, top: y, width: 128, height: 22 },
    { fontSize: 12, color: "#62666D", alignment: "right", fontFamily: "Microsoft YaHei" },
  );
}

function addCover(presentation, payload, slideData, pageNumber) {
  const slide = presentation.slides.add();
  slide.background.fill = "#FFFFFF";
  slide.shapes.add({
    geometry: "rect",
    name: "cover-accent",
    position: { left: 72, top: 92, width: 14, height: 430 },
    fill: "#3D8DFF",
    line: { style: "solid", fill: "none", width: 0 },
  });
  addText(
    slide,
    "cover-kicker",
    payload.profile_id === "product-director" ? "产品总监周报" : "市场总监周报",
    { left: 120, top: 96, width: 420, height: 40 },
    { fontSize: 22, bold: true, color: "#3D8DFF", fontFamily: "Microsoft YaHei" },
  );
  addText(
    slide,
    "cover-title",
    slideData.title,
    { left: 120, top: 188, width: 980, height: 180 },
    { fontSize: 58, bold: true, color: "#000000", fontFamily: "Microsoft YaHei" },
  );
  addText(
    slide,
    "cover-subtitle",
    slideData.subtitle || `${payload.period.start} 至 ${payload.period.end}`,
    { left: 120, top: 404, width: 820, height: 72 },
    { fontSize: 24, color: "#4B4F55", fontFamily: "Microsoft YaHei" },
  );
  addFooter(slide, payload.period, pageNumber, payload.slides.length);
  slide.speakerNotes.textFrame.setText(sourceNotes(slideData));
}

function addContentSlide(presentation, payload, slideData, pageNumber) {
  const slide = presentation.slides.add();
  slide.background.fill = "#FFFFFF";
  addText(
    slide,
    `eyebrow-${pageNumber}`,
    slideData.eyebrow || (payload.profile_id === "product-director" ? "PRODUCT WEEKLY" : "MARKET WEEKLY"),
    { left: 72, top: 48, width: 320, height: 28 },
    { fontSize: 14, bold: true, color: "#3D8DFF", fontFamily: "Arial" },
  );
  addText(
    slide,
    `title-${pageNumber}`,
    slideData.title,
    { left: 72, top: 88, width: 1050, height: 82 },
    { fontSize: 38, bold: true, color: "#000000", fontFamily: "Microsoft YaHei" },
  );
  const lines = Array.isArray(slideData.body) ? slideData.body : [];
  const lead = slideData.lead?.trim();
  if (lead) {
    addText(
      slide,
      `lead-${pageNumber}`,
      lead,
      { left: 72, top: 194, width: 1070, height: 76 },
      { fontSize: 25, bold: true, color: "#24272B", fontFamily: "Microsoft YaHei" },
    );
  }
  const bodyTop = lead ? 296 : 206;
  const lineHeight = Math.min(72, Math.max(46, Math.floor(330 / Math.max(lines.length, 1))));
  lines.forEach((line, index) => {
    const top = bodyTop + index * lineHeight;
    slide.shapes.add({
      geometry: "rect",
      name: `bullet-${pageNumber}-${index + 1}`,
      position: { left: 76, top: top + 11, width: 10, height: 10 },
      fill: index === 0 ? "#3D8DFF" : "#B8BCC4",
      line: { style: "solid", fill: "none", width: 0 },
    });
    addText(
      slide,
      `body-${pageNumber}-${index + 1}`,
      line,
      { left: 108, top, width: 1030, height: Math.max(38, lineHeight - 6) },
      { fontSize: 20, color: "#24272B", fontFamily: "Microsoft YaHei" },
    );
  });
  if (slideData.callout) {
    slide.shapes.add({
      geometry: "rect",
      name: `callout-bg-${pageNumber}`,
      position: { left: 72, top: 566, width: 1066, height: 72 },
      fill: "#EDEDED",
      line: { style: "solid", fill: "none", width: 0 },
    });
    addText(
      slide,
      `callout-${pageNumber}`,
      slideData.callout,
      { left: 96, top: 584, width: 1018, height: 42 },
      { fontSize: 18, bold: true, color: "#000000", fontFamily: "Microsoft YaHei" },
    );
  }
  addFooter(slide, payload.period, pageNumber, payload.slides.length);
  slide.speakerNotes.textFrame.setText(sourceNotes(slideData));
}

async function main() {
  const paths = parseArguments(process.argv.slice(2));
  const payload = JSON.parse(await fs.readFile(paths.input, "utf8"));
  const { Presentation, PresentationFile } = await loadArtifactTool();
  await markArtifactOperation();
  const presentation = Presentation.create({ slideSize: { width: 1280, height: 720 } });
  payload.slides.forEach((slide, index) => {
    if (index === 0) addCover(presentation, payload, slide, index + 1);
    else addContentSlide(presentation, payload, slide, index + 1);
  });
  await fs.mkdir(paths.qaDir, { recursive: true });
  for (const [index, slide] of presentation.slides.items.entries()) {
    const stem = `slide-${String(index + 1).padStart(2, "0")}`;
    const png = await presentation.export({ slide, format: "png", scale: 1 });
    await fs.writeFile(resolve(paths.qaDir, `${stem}.png`), new Uint8Array(await png.arrayBuffer()));
    const layout = await slide.export({ format: "layout" });
    await fs.writeFile(resolve(paths.qaDir, `${stem}.layout.json`), await layout.text());
  }
  await fs.mkdir(dirname(paths.output), { recursive: true });
  const pptx = await PresentationFile.exportPptx(presentation);
  await pptx.save(paths.output);
  const result = {
    output: paths.output,
    filename: basename(paths.output),
    slide_count: payload.slides.length,
    source_note_slides: payload.slides.filter((slide) => Array.isArray(slide.sources) && slide.sources.length > 0).length,
    qa_dir: paths.qaDir,
  };
  await new Promise((done) => process.stdout.write(`${JSON.stringify(result)}\n`, done));
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.stack || error.message : String(error)}\n`);
  process.exitCode = 1;
});
