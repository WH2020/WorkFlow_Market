import { execFile as execFileCallback } from "node:child_process";
import fs from "node:fs/promises";
import { basename, dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { promisify } from "node:util";

const execFile = promisify(execFileCallback);

export function fontFamilies(os = process.platform, environment = process.env) {
  const defaults = os === "win32"
    ? { cjk: "Microsoft YaHei", latin: "Arial" }
    : os === "darwin"
      ? { cjk: "PingFang SC", latin: "Arial" }
      : { cjk: "Noto Sans CJK SC", latin: "Arial" };
  return {
    cjk: environment.WORKFLOW_CJK_FONT?.trim() || defaults.cjk,
    latin: environment.WORKFLOW_LATIN_FONT?.trim() || defaults.latin,
  };
}

const FONTS = fontFamilies();

const THEMES = {
  "ceo-weekly": {
    name: "管理周报",
    cover: "#102A43",
    background: "#F6F8FA",
    paper: "#FFFFFF",
    ink: "#17212B",
    muted: "#617080",
    accent: "#087E8B",
    accentSoft: "#E3F3F2",
    highlight: "#D7A13A",
    line: "#D8E0E6",
    eyebrow: "EXECUTIVE WEEKLY",
  },
  "management-report": {
    name: "经营管理",
    cover: "#102A43",
    background: "#F6F8FA",
    paper: "#FFFFFF",
    ink: "#17212B",
    muted: "#617080",
    accent: "#087E8B",
    accentSoft: "#E3F3F2",
    highlight: "#D7A13A",
    line: "#D8E0E6",
    eyebrow: "MANAGEMENT BRIEF",
  },
  "government-program": {
    name: "政企合作",
    cover: "#7D2027",
    background: "#FBF8F3",
    paper: "#FFFFFF",
    ink: "#302722",
    muted: "#756A62",
    accent: "#A43138",
    accentSoft: "#F5E8E5",
    highlight: "#B99350",
    line: "#E1D8CD",
    eyebrow: "GOVERNMENT PROGRAM",
  },
  "technology-research": {
    name: "前沿研究",
    cover: "#071C33",
    background: "#F3F7FB",
    paper: "#FFFFFF",
    ink: "#13283D",
    muted: "#5B6F82",
    accent: "#138EA4",
    accentSoft: "#E0F4F7",
    highlight: "#47C0D0",
    line: "#D4E1EA",
    eyebrow: "TECHNOLOGY RESEARCH",
  },
};

function themeFor(templateId) {
  return THEMES[templateId] || THEMES["management-report"];
}

function titleSize(text, preferred, minimum) {
  const length = Array.from(text || "").length;
  if (length <= 16) return preferred;
  if (length <= 26) return Math.max(minimum, preferred - 7);
  return minimum;
}

export function slideTreatment(slide, index) {
  const body = Array.isArray(slide?.body) ? slide.body : [];
  const hasContent = Boolean(slide?.eyebrow?.trim() || slide?.lead?.trim() || slide?.callout?.trim() || body.length > 0);
  const intent = slide?.layout_intent || (index === 0 ? "top-hero" : "single-focus");
  if (index === 0 && !hasContent && (slide?.layout_intent === undefined || intent === "top-hero")) return "cover";
  return intent;
}

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
    ...(rows.length > 0 ? rows : ["- No external source cited on this slide; content is based on the approved task context."]),
    "[/Sources]",
  ].filter(Boolean).join("\n");
}

function addFooter(slide, period, pageNumber, totalPages, theme, onDark = false) {
  const y = 674;
  slide.shapes.add({
    geometry: "line",
    name: `footer-rule-${pageNumber}`,
    position: { left: 72, top: y - 14, width: 1136, height: 1 },
    fill: "none",
    line: { style: "solid", fill: onDark ? "#6F879A" : theme.line, width: 1 },
  });
  addText(
    slide,
    `footer-period-${pageNumber}`,
    `${period.start} 至 ${period.end}`,
    { left: 72, top: y, width: 360, height: 22 },
    { fontSize: 12, color: onDark ? "#D9E4EC" : theme.muted, fontFamily: FONTS.cjk },
  );
  addText(
    slide,
    `footer-page-${pageNumber}`,
    `${pageNumber} / ${totalPages}`,
    { left: 1080, top: y, width: 128, height: 22 },
    { fontSize: 12, color: onDark ? "#D9E4EC" : theme.muted, alignment: "right", fontFamily: FONTS.cjk },
  );
}

function addCover(presentation, payload, slideData, pageNumber, theme) {
  const slide = presentation.slides.add();
  slide.background.fill = theme.cover;
  slide.shapes.add({
    geometry: "rect",
    name: "cover-accent",
    position: { left: 72, top: 88, width: 12, height: 454 },
    fill: theme.highlight,
    line: { style: "solid", fill: "none", width: 0 },
  });
  slide.shapes.add({
    geometry: "rect",
    name: "cover-geometry-back",
    position: { left: 1010, top: 0, width: 270, height: 720 },
    fill: theme.accent,
    line: { style: "solid", fill: "none", width: 0 },
  });
  slide.shapes.add({
    geometry: "rect",
    name: "cover-geometry-front",
    position: { left: 1090, top: 0, width: 190, height: 720 },
    fill: theme.highlight,
    line: { style: "solid", fill: "none", width: 0 },
  });
  addText(
    slide,
    "cover-kicker",
    `${payload.profile_id === "product-director" ? "产品总监" : "市场总监"} · ${theme.name}`,
    { left: 120, top: 96, width: 520, height: 40 },
    { fontSize: 20, bold: true, color: theme.highlight, fontFamily: FONTS.cjk },
  );
  addText(
    slide,
    "cover-title",
    slideData.title,
    { left: 120, top: 184, width: 850, height: 190 },
    { fontSize: titleSize(slideData.title, 54, 38), bold: true, color: "#FFFFFF", fontFamily: FONTS.cjk },
  );
  addText(
    slide,
    "cover-subtitle",
    slideData.subtitle || `${payload.period.start} 至 ${payload.period.end}`,
    { left: 120, top: 410, width: 790, height: 72 },
    { fontSize: 22, color: "#D9E4EC", fontFamily: FONTS.cjk },
  );
  addText(
    slide,
    "cover-template",
    theme.eyebrow,
    { left: 120, top: 522, width: 500, height: 26 },
    { fontSize: 11, bold: true, color: "#AFC1D0", letterSpacing: 1.4, fontFamily: FONTS.latin },
  );
  addFooter(slide, payload.period, pageNumber, payload.slides.length, theme, true);
  slide.speakerNotes.textFrame.setText(sourceNotes(slideData));
}

function addGridItems(slide, lines, pageNumber, theme, bodyTop, bodyHeight, columns, startNumber = 1) {
  if (lines.length === 0) return;
  const gap = 24;
  const width = (1066 - (columns - 1) * gap) / columns;
  const rows = Math.ceil(lines.length / columns);
  const rowHeight = Math.max(58, Math.floor(bodyHeight / Math.max(rows, 1)));
  lines.forEach((line, index) => {
    const column = index % columns;
    const row = Math.floor(index / columns);
    const left = 72 + column * (width + gap);
    const top = bodyTop + row * rowHeight;
    const number = startNumber + index;
    slide.shapes.add({
      geometry: "line",
      name: `body-rule-${pageNumber}-${number}`,
      position: { left, top: top + rowHeight - 10, width, height: 1 },
      fill: "none",
      line: { style: "solid", fill: theme.line, width: 1 },
    });
    addText(
      slide,
      `body-number-${pageNumber}-${number}`,
      String(number).padStart(2, "0"),
      { left, top, width: 38, height: 28 },
      { fontSize: 12, bold: true, color: theme.accent, fontFamily: FONTS.latin },
    );
    addText(
      slide,
      `body-${pageNumber}-${number}`,
      line,
      { left: left + 45, top: top - 2, width: width - 45, height: rowHeight - 13 },
      { fontSize: columns === 3 ? 15 : 17, color: theme.ink, fontFamily: FONTS.cjk },
    );
  });
}

function addBody(slide, slideData, pageNumber, theme, bodyTop) {
  const lines = Array.isArray(slideData.body) ? slideData.body : [];
  const intent = slideData.layout_intent || "single-focus";
  const bodyBottom = slideData.callout ? 548 : 612;
  const bodyHeight = Math.max(180, bodyBottom - bodyTop);
  if (intent === "top-hero") {
    let remaining = lines;
    let gridTop = bodyTop;
    let gridHeight = bodyHeight;
    if (!slideData.lead?.trim() && lines.length > 0) {
      slide.shapes.add({
        geometry: "rect",
        name: `body-hero-${pageNumber}`,
        position: { left: 72, top: bodyTop, width: 1066, height: 92 },
        fill: theme.paper,
        line: { style: "solid", fill: theme.line, width: 1 },
      });
      addText(
        slide,
        `body-hero-text-${pageNumber}`,
        lines[0],
        { left: 102, top: bodyTop + 24, width: 1006, height: 48 },
        { fontSize: 23, bold: true, color: theme.ink, fontFamily: FONTS.cjk },
      );
      remaining = lines.slice(1);
      gridTop += 112;
      gridHeight -= 112;
    }
    addGridItems(slide, remaining, pageNumber, theme, gridTop, gridHeight, 2, lines.length === remaining.length ? 1 : 2);
    return;
  }
  if (intent === "mixed-grid" && lines.length > 0) {
    slide.shapes.add({
      geometry: "rect",
      name: `body-mixed-hero-${pageNumber}`,
      position: { left: 72, top: bodyTop, width: 1066, height: 76 },
      fill: theme.accentSoft,
      line: { style: "solid", fill: theme.accent, width: 1 },
    });
    addText(
      slide,
      `body-mixed-hero-text-${pageNumber}`,
      lines[0],
      { left: 102, top: bodyTop + 18, width: 1006, height: 40 },
      { fontSize: 20, bold: true, color: theme.ink, fontFamily: FONTS.cjk },
    );
    addGridItems(slide, lines.slice(1), pageNumber, theme, bodyTop + 96, bodyHeight - 96, 2, 2);
    return;
  }
  if (intent === "two-thirds" && lines.length > 1) {
    slide.shapes.add({
      geometry: "rect",
      name: `body-feature-${pageNumber}`,
      position: { left: 72, top: bodyTop, width: 650, height: bodyHeight },
      fill: theme.paper,
      line: { style: "solid", fill: theme.line, width: 1 },
    });
    addText(
      slide,
      `body-feature-number-${pageNumber}`,
      "01",
      { left: 102, top: bodyTop + 28, width: 50, height: 30 },
      { fontSize: 13, bold: true, color: theme.accent, fontFamily: FONTS.latin },
    );
    addText(
      slide,
      `body-feature-text-${pageNumber}`,
      lines[0],
      { left: 102, top: bodyTop + 78, width: 590, height: bodyHeight - 110 },
      { fontSize: 25, bold: true, color: theme.ink, fontFamily: FONTS.cjk },
    );
    const remaining = lines.slice(1);
    const rightHeight = Math.max(52, Math.floor(bodyHeight / remaining.length));
    remaining.forEach((line, index) => {
      const top = bodyTop + index * rightHeight;
      addText(
        slide,
        `body-number-${pageNumber}-${index + 2}`,
        String(index + 2).padStart(2, "0"),
        { left: 770, top, width: 38, height: 28 },
        { fontSize: 12, bold: true, color: theme.accent, fontFamily: FONTS.latin },
      );
      addText(
        slide,
        `body-${pageNumber}-${index + 2}`,
        line,
        { left: 816, top: top - 2, width: 322, height: rightHeight - 8 },
        { fontSize: 16, color: theme.ink, fontFamily: FONTS.cjk },
      );
    });
    return;
  }
  if (intent === "fifty-fifty" || intent === "three-column") {
    const columns = intent === "three-column" ? 3 : 2;
    addGridItems(slide, lines, pageNumber, theme, bodyTop, bodyHeight, columns);
    return;
  }
  if (lines.length <= 4) {
    const lineHeight = Math.min(82, Math.max(52, Math.floor(bodyHeight / Math.max(lines.length, 1))));
    lines.forEach((line, index) => {
      const top = bodyTop + index * lineHeight;
      slide.shapes.add({
        geometry: "rect",
        name: `bullet-${pageNumber}-${index + 1}`,
        position: { left: 76, top: top + 8, width: 8, height: 24 },
        fill: index === 0 ? theme.accent : theme.line,
        line: { style: "solid", fill: "none", width: 0 },
      });
      addText(
        slide,
        `body-${pageNumber}-${index + 1}`,
        line,
        { left: 106, top, width: 1018, height: Math.max(42, lineHeight - 5) },
        { fontSize: 20, color: theme.ink, fontFamily: FONTS.cjk },
      );
    });
    return;
  }
  const rows = Math.ceil(lines.length / 2);
  const rowHeight = Math.min(88, Math.floor(bodyHeight / rows));
  lines.forEach((line, index) => {
    const column = index % 2;
    const row = Math.floor(index / 2);
    const left = 72 + column * 548;
    const top = bodyTop + row * rowHeight;
    slide.shapes.add({
      geometry: "line",
      name: `body-rule-${pageNumber}-${index + 1}`,
      position: { left, top: top + rowHeight - 10, width: 500, height: 1 },
      fill: "none",
      line: { style: "solid", fill: theme.line, width: 1 },
    });
    addText(
      slide,
      `body-number-${pageNumber}-${index + 1}`,
      String(index + 1).padStart(2, "0"),
      { left, top, width: 38, height: 28 },
      { fontSize: 12, bold: true, color: theme.accent, fontFamily: FONTS.latin },
    );
    addText(
      slide,
      `body-${pageNumber}-${index + 1}`,
      line,
      { left: left + 45, top: top - 2, width: 455, height: rowHeight - 13 },
      { fontSize: 17, color: theme.ink, fontFamily: FONTS.cjk },
    );
  });
}

function addContentSlide(presentation, payload, slideData, pageNumber, theme) {
  const slide = presentation.slides.add();
  slide.background.fill = theme.background;
  slide.shapes.add({
    geometry: "rect",
    name: `top-accent-${pageNumber}`,
    position: { left: 0, top: 0, width: 1280, height: 8 },
    fill: theme.accent,
    line: { style: "solid", fill: "none", width: 0 },
  });
  slide.shapes.add({
    geometry: "rect",
    name: `page-watermark-${pageNumber}`,
    position: { left: 1135, top: 42, width: 75, height: 62 },
    fill: theme.accentSoft,
    line: { style: "solid", fill: "none", width: 0 },
  });
  addText(
    slide,
    `page-watermark-text-${pageNumber}`,
    String(pageNumber).padStart(2, "0"),
    { left: 1145, top: 50, width: 55, height: 44 },
    { fontSize: 25, bold: true, color: theme.accent, alignment: "center", fontFamily: FONTS.latin },
  );
  addText(
    slide,
    `eyebrow-${pageNumber}`,
    slideData.eyebrow || theme.eyebrow,
    { left: 72, top: 48, width: 320, height: 28 },
    { fontSize: 13, bold: true, color: theme.accent, letterSpacing: 1.1, fontFamily: FONTS.latin },
  );
  addText(
    slide,
    `title-${pageNumber}`,
    slideData.title,
    { left: 72, top: 88, width: 1000, height: 82 },
    { fontSize: titleSize(slideData.title, 36, 29), bold: true, color: theme.ink, fontFamily: FONTS.cjk },
  );
  const lead = slideData.lead?.trim() || slideData.subtitle?.trim();
  if (lead) {
    slide.shapes.add({
      geometry: "rect",
      name: `lead-bg-${pageNumber}`,
      position: { left: 72, top: 188, width: 1066, height: 76 },
      fill: theme.paper,
      line: { style: "solid", fill: theme.line, width: 1 },
    });
    addText(
      slide,
      `lead-${pageNumber}`,
      lead,
      { left: 96, top: 204, width: 1018, height: 48 },
      { fontSize: 21, bold: true, color: theme.ink, fontFamily: FONTS.cjk },
    );
  }
  addBody(slide, slideData, pageNumber, theme, lead ? 292 : 198);
  if (slideData.callout) {
    slide.shapes.add({
      geometry: "rect",
      name: `callout-bg-${pageNumber}`,
      position: { left: 72, top: 568, width: 1066, height: 70 },
      fill: theme.accentSoft,
      line: { style: "solid", fill: theme.accent, width: 1 },
    });
    addText(
      slide,
      `callout-${pageNumber}`,
      slideData.callout,
      { left: 96, top: 584, width: 1018, height: 40 },
      { fontSize: 18, bold: true, color: theme.ink, fontFamily: FONTS.cjk },
    );
  }
  addFooter(slide, payload.period, pageNumber, payload.slides.length, theme);
  slide.speakerNotes.textFrame.setText(sourceNotes(slideData));
}

async function main() {
  const paths = parseArguments(process.argv.slice(2));
  const payload = JSON.parse(await fs.readFile(paths.input, "utf8"));
  const { Presentation, PresentationFile } = await loadArtifactTool();
  await markArtifactOperation();
  const theme = themeFor(payload.template_id);
  const presentation = Presentation.create({ slideSize: { width: 1280, height: 720 } });
  payload.slides.forEach((slide, index) => {
    if (slideTreatment(slide, index) === "cover") addCover(presentation, payload, slide, index + 1, theme);
    else addContentSlide(presentation, payload, slide, index + 1, theme);
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
    template_id: payload.template_id,
    theme_name: theme.name,
    qa_dir: paths.qaDir,
  };
  await new Promise((done) => process.stdout.write(`${JSON.stringify(result)}\n`, done));
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.stack || error.message : String(error)}\n`);
    process.exitCode = 1;
  });
}
