import fs from "node:fs/promises";
import { basename, dirname, resolve } from "node:path";

import JSZip from "jszip";

function argumentsFrom(argv) {
  const inputIndex = argv.indexOf("--input");
  const outputIndex = argv.indexOf("--output");
  const qaIndex = argv.indexOf("--qa-dir");
  if (inputIndex < 0 || outputIndex < 0 || qaIndex < 0) {
    throw new Error("usage: build-bid-document.mjs --input payload.json --output artifact.docx --qa-dir qa");
  }
  return {
    input: resolve(argv[inputIndex + 1]),
    output: resolve(argv[outputIndex + 1]),
    qaDir: resolve(argv[qaIndex + 1]),
  };
}

function xml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

function safeText(value, label, maximum) {
  if (typeof value !== "string" || !value.trim() || value.length > maximum || /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/u.test(value)) {
    throw new Error(`${label} 无效或过长`);
  }
  return value.trim();
}

function validate(payload) {
  if (!payload || payload.schema_version !== "1.0" || payload.profile_id !== "sales-director") throw new Error("标书载荷版本或角色无效");
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/u.test(payload.bid_id)) throw new Error("bid_id 无效");
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.docx$/u.test(payload.output_name)) throw new Error("输出文件名无效");
  if (!/^[a-f0-9]{64}$/u.test(payload.snapshot_sha256)) throw new Error("投标快照哈希无效");
  safeText(payload.title, "标题", 300);
  if (!Array.isArray(payload.sections) || payload.sections.length < 1 || payload.sections.length > 80) throw new Error("标书章节数量必须为 1–80");
  if (!Array.isArray(payload.sources) || payload.sources.length < 1 || payload.sources.length > 200) throw new Error("正式标书必须包含 1–200 条来源");
  let paragraphCount = 0;
  for (const [index, section] of payload.sections.entries()) {
    if (!section || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/u.test(section.section_id)) throw new Error(`第 ${index + 1} 个章节编号无效`);
    safeText(section.title, `第 ${index + 1} 个章节标题`, 300);
    if (!Number.isInteger(section.level) || section.level < 1 || section.level > 4) throw new Error(`第 ${index + 1} 个章节层级无效`);
    if (!Array.isArray(section.paragraphs) || section.paragraphs.length > 200) throw new Error(`第 ${index + 1} 个章节段落无效`);
    for (const paragraph of section.paragraphs) safeText(paragraph, `第 ${index + 1} 个章节段落`, 20_000);
    paragraphCount += section.paragraphs.length;
    for (const table of section.tables ?? []) {
      if (!Array.isArray(table.columns) || table.columns.length < 1 || table.columns.length > 6) throw new Error(`第 ${index + 1} 个章节表格列数无效`);
      if (!Array.isArray(table.rows) || table.rows.length > 100) throw new Error(`第 ${index + 1} 个章节表格行数无效`);
      table.columns.forEach((value) => safeText(value, "表格列名", 100));
      table.rows.forEach((row) => {
        if (!Array.isArray(row) || row.length !== table.columns.length) throw new Error("表格行列数不一致");
        row.forEach((value) => safeText(value, "表格单元格", 2_000));
      });
    }
  }
  if (paragraphCount < 1) throw new Error("正式标书至少需要一个正文段落");
  payload.sources.forEach((source, index) => {
    if (!source || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/u.test(source.source_id)) throw new Error(`第 ${index + 1} 条来源编号无效`);
    safeText(source.title, `第 ${index + 1} 条来源标题`, 500);
    safeText(source.path, `第 ${index + 1} 条来源路径`, 1024);
    if (!/^[a-f0-9]{64}$/u.test(source.sha256)) throw new Error(`第 ${index + 1} 条来源哈希无效`);
    if (source.page !== undefined && (!Number.isInteger(source.page) || source.page < 1 || source.page > 100_000)) throw new Error(`第 ${index + 1} 条来源页码无效`);
  });
  return payload;
}

function run(text, { bold = false, size = 22, color = "24344D", font } = {}) {
  return `<w:r><w:rPr><w:rFonts w:ascii="${xml(font)}" w:eastAsia="${xml(font)}" w:hAnsi="${xml(font)}"/><w:sz w:val="${size}"/><w:szCs w:val="${size}"/><w:color w:val="${color}"/>${bold ? "<w:b/><w:bCs/>" : ""}</w:rPr><w:t xml:space="preserve">${xml(text)}</w:t></w:r>`;
}

function paragraph(text, { style = "BodyText", align, before = 0, after = 120, pageBreakBefore = false, font } = {}) {
  const properties = [
    `<w:pStyle w:val="${style}"/>`,
    align ? `<w:jc w:val="${align}"/>` : "",
    `<w:spacing w:before="${before}" w:after="${after}" w:line="360" w:lineRule="auto"/>`,
    pageBreakBefore ? "<w:pageBreakBefore/>" : "",
    "<w:widowControl/>",
  ].join("");
  return `<w:p><w:pPr>${properties}</w:pPr>${run(text, { font })}</w:p>`;
}

function styledParagraph(text, style, font, options = {}) {
  const sizes = { Title: 48, Subtitle: 25, Heading1: 32, Heading2: 28, Heading3: 25, Heading4: 23, Caption: 19 };
  const colors = { Title: "17375E", Subtitle: "5D6B7A", Heading1: "17375E", Heading2: "1E5B8A", Heading3: "24344D", Heading4: "40556D", Caption: "667788" };
  return `<w:p><w:pPr><w:pStyle w:val="${style}"/>${options.align ? `<w:jc w:val="${options.align}"/>` : ""}<w:spacing w:before="${options.before ?? 160}" w:after="${options.after ?? 120}"/>${options.pageBreakBefore ? "<w:pageBreakBefore/>" : ""}<w:keepNext/><w:widowControl/></w:pPr>${run(text, { bold: style !== "Subtitle" && style !== "Caption", size: sizes[style] ?? 22, color: colors[style] ?? "24344D", font })}</w:p>`;
}

function pageBreak() {
  return "<w:p><w:r><w:br w:type=\"page\"/></w:r></w:p>";
}

function cell(text, font, header = false, width = 1500) {
  const fill = header ? "DCE6F1" : "FFFFFF";
  return `<w:tc><w:tcPr><w:tcW w:w="${width}" w:type="dxa"/><w:shd w:fill="${fill}"/><w:tcMar><w:top w:w="80" w:type="dxa"/><w:left w:w="100" w:type="dxa"/><w:bottom w:w="80" w:type="dxa"/><w:right w:w="100" w:type="dxa"/></w:tcMar></w:tcPr><w:p><w:pPr><w:spacing w:after="40"/><w:widowControl/></w:pPr>${run(text, { bold: header, size: 19, font })}</w:p></w:tc>`;
}

function tableXml(table, font) {
  const width = Math.floor(9000 / table.columns.length);
  const borders = "<w:tblBorders><w:top w:val=\"single\" w:sz=\"4\" w:color=\"AAB7C4\"/><w:left w:val=\"single\" w:sz=\"4\" w:color=\"AAB7C4\"/><w:bottom w:val=\"single\" w:sz=\"4\" w:color=\"AAB7C4\"/><w:right w:val=\"single\" w:sz=\"4\" w:color=\"AAB7C4\"/><w:insideH w:val=\"single\" w:sz=\"3\" w:color=\"D5DDE5\"/><w:insideV w:val=\"single\" w:sz=\"3\" w:color=\"D5DDE5\"/></w:tblBorders>";
  const header = `<w:tr><w:trPr><w:tblHeader/></w:trPr>${table.columns.map((value) => cell(value, font, true, width)).join("")}</w:tr>`;
  const rows = table.rows.map((row) => `<w:tr><w:trPr><w:cantSplit/></w:trPr>${row.map((value) => cell(value, font, false, width)).join("")}</w:tr>`).join("");
  return `<w:tbl><w:tblPr><w:tblW w:w="9000" w:type="dxa"/><w:tblLayout w:type="fixed"/>${borders}<w:tblCellMar><w:top w:w="80" w:type="dxa"/><w:left w:w="100" w:type="dxa"/><w:bottom w:w="80" w:type="dxa"/><w:right w:w="100" w:type="dxa"/></w:tblCellMar></w:tblPr><w:tblGrid>${table.columns.map(() => `<w:gridCol w:w="${width}"/>`).join("")}</w:tblGrid>${header}${rows}</w:tbl>`;
}

function styles(font) {
  const style = (id, name, size, color, bold = false, outline) => `<w:style w:type="paragraph" w:styleId="${id}"><w:name w:val="${name}"/><w:basedOn w:val="Normal"/><w:next w:val="BodyText"/>${outline !== undefined ? `<w:pPr><w:outlineLvl w:val="${outline}"/><w:keepNext/><w:keepLines/><w:spacing w:before="240" w:after="120"/></w:pPr>` : ""}<w:rPr><w:rFonts w:ascii="${xml(font)}" w:eastAsia="${xml(font)}" w:hAnsi="${xml(font)}"/><w:sz w:val="${size}"/><w:szCs w:val="${size}"/><w:color w:val="${color}"/>${bold ? "<w:b/><w:bCs/>" : ""}</w:rPr></w:style>`;
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="${xml(font)}" w:eastAsia="${xml(font)}" w:hAnsi="${xml(font)}"/><w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="zh-CN" w:eastAsia="zh-CN"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:line="360" w:lineRule="auto" w:after="120"/><w:widowControl/></w:pPr></w:pPrDefault></w:docDefaults>${style("Normal", "正文", 22, "24344D")}${style("BodyText", "正文文本", 22, "24344D")}${style("Title", "标题", 48, "17375E", true)}${style("Subtitle", "副标题", 25, "5D6B7A")}${style("Heading1", "标题 1", 32, "17375E", true, 0)}${style("Heading2", "标题 2", 28, "1E5B8A", true, 1)}${style("Heading3", "标题 3", 25, "24344D", true, 2)}${style("Heading4", "标题 4", 23, "40556D", true, 3)}${style("Caption", "题注", 19, "667788")}</w:styles>`;
}

function documentXml(payload, font) {
  const body = [];
  body.push(styledParagraph(payload.title, "Title", font, { align: "center", before: 1600, after: 280 }));
  if (payload.subtitle) body.push(styledParagraph(payload.subtitle, "Subtitle", font, { align: "center", before: 0, after: 420 }));
  const cover = [
    payload.buyer ? `采购人：${payload.buyer}` : "",
    payload.tender_number ? `招标编号：${payload.tender_number}` : "",
    payload.bidder ? `投标人：${payload.bidder}` : "",
    payload.generated_date ? `生成日期：${payload.generated_date}` : "",
    payload.confidentiality ? `使用范围：${payload.confidentiality}` : "内部审阅稿",
  ].filter(Boolean);
  cover.forEach((line) => body.push(paragraph(line, { style: "Subtitle", align: "center", after: 120, font })));
  body.push(pageBreak());
  body.push(styledParagraph("目录", "Heading1", font));
  payload.sections.forEach((section, index) => body.push(paragraph(`${index + 1}. ${section.title}`, { after: 80, font })));
  body.push(paragraph("提示：定稿前请在文字处理软件中更新目录、页码和交叉引用。", { style: "Caption", font }));
  body.push(pageBreak());
  payload.sections.forEach((section, index) => {
    body.push(styledParagraph(section.title, `Heading${section.level}`, font, { pageBreakBefore: index > 0 && section.level === 1 }));
    section.paragraphs.forEach((value) => body.push(paragraph(value, { font })));
    (section.tables ?? []).forEach((table) => {
      if (table.title) body.push(styledParagraph(table.title, "Caption", font, { before: 120, after: 80 }));
      body.push(tableXml(table, font));
      body.push(paragraph("", { after: 80, font }));
    });
  });
  if ((payload.warnings ?? []).length) {
    body.push(styledParagraph("待确认事项与风险提示", "Heading1", font, { pageBreakBefore: true }));
    payload.warnings.forEach((warning, index) => body.push(paragraph(`${index + 1}. ${warning}`, { font })));
  }
  body.push(styledParagraph("来源与证据附录", "Heading1", font, { pageBreakBefore: true }));
  payload.sources.forEach((source, index) => {
    const locator = [source.path, source.page ? `第 ${source.page} 页` : "", source.locator ?? ""].filter(Boolean).join(" · ");
    body.push(paragraph(`${index + 1}. ${source.title}｜${locator}｜SHA-256 ${source.sha256}`, { style: "Caption", font }));
  });
  body.push(styledParagraph("生成边界", "Heading2", font));
  body.push(paragraph("本文件由本地辅助工具基于已登记资料生成，仍需负责人完成事实、商务、法务、签章、报价和递交要求的最终人工复核。本工具未执行报名、签章、加密、上传、发送或外部提交。", { font }));
  body.push(`<w:sectPr><w:headerReference w:type="default" r:id="rId1"/><w:footerReference w:type="default" r:id="rId2"/><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1276" w:right="1276" w:bottom="1276" w:left="1276" w:header="567" w:footer="567" w:gutter="0"/><w:cols w:space="425"/><w:docGrid w:type="lines" w:linePitch="312"/></w:sectPr>`);
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body>${body.join("")}</w:body></w:document>`;
}

async function build(payload, output, qaDir) {
  const font = process.env.WORKFLOW_CJK_FONT?.trim() || "Microsoft YaHei";
  const zip = new JSZip();
  zip.file("[Content_Types].xml", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/><Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/><Override PartName="/word/fontTable.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.fontTable+xml"/><Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/><Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/><Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/><Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/></Types>`);
  zip.file("_rels/.rels", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>`);
  zip.file("word/document.xml", documentXml(payload, font));
  zip.file("word/styles.xml", styles(font));
  zip.file("word/settings.xml", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:zoom w:percent="100"/><w:defaultTabStop w:val="420"/><w:characterSpacingControl w:val="doNotCompress"/><w:themeFontLang w:val="zh-CN" w:eastAsia="zh-CN"/><w:updateFields w:val="true"/></w:settings>`);
  zip.file("word/fontTable.xml", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:fonts xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:font w:name="${xml(font)}"><w:family w:val="swiss"/><w:charset w:val="86"/></w:font></w:fonts>`);
  zip.file("word/header1.xml", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:pPr><w:jc w:val="right"/><w:pBdr><w:bottom w:val="single" w:sz="4" w:space="4" w:color="B8C5D2"/></w:pBdr></w:pPr>${run(payload.title, { size: 18, color: "667788", font })}</w:p></w:hdr>`);
  zip.file("word/footer1.xml", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:pPr><w:jc w:val="center"/><w:pBdr><w:top w:val="single" w:sz="4" w:space="4" w:color="D5DDE5"/></w:pBdr></w:pPr>${run("第 ", { size: 18, color: "667788", font })}<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>${run(" 页", { size: 18, color: "667788", font })}</w:p></w:ftr>`);
  zip.file("word/_rels/document.xml.rels", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/><Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/><Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/fontTable" Target="fontTable.xml"/></Relationships>`);
  const stamp = new Date().toISOString();
  zip.file("docProps/core.xml", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>${xml(payload.title)}</dc:title><dc:subject>智能招投标正式文件</dc:subject><dc:creator>Agent4Market 本地工作台</dc:creator><cp:lastModifiedBy>Agent4Market 本地工作台</cp:lastModifiedBy><dcterms:created xsi:type="dcterms:W3CDTF">${stamp}</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">${stamp}</dcterms:modified></cp:coreProperties>`);
  zip.file("docProps/app.xml", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>Agent4Market</Application><AppVersion>0.15.0</AppVersion></Properties>`);
  await fs.mkdir(dirname(output), { recursive: true });
  await fs.mkdir(qaDir, { recursive: true });
  const content = await zip.generateAsync({ type: "nodebuffer", compression: "DEFLATE", compressionOptions: { level: 9 }, platform: "DOS" });
  await fs.writeFile(output, content, { flag: "wx", mode: 0o600 });
  const manifest = {
    schema_version: "1.0",
    title: payload.title,
    bid_id: payload.bid_id,
    sections: payload.sections.length,
    paragraphs: payload.sections.reduce((total, section) => total + section.paragraphs.length, 0),
    tables: payload.sections.reduce((total, section) => total + (section.tables?.length ?? 0), 0),
    sources: payload.sources.length,
    font,
  };
  await fs.writeFile(resolve(qaDir, "document-layout.json"), `${JSON.stringify(manifest, null, 2)}\n`, { flag: "wx" });
  return { output, filename: basename(output), bytes: content.length, manifest };
}

async function main() {
  const paths = argumentsFrom(process.argv.slice(2));
  const payload = validate(JSON.parse(await fs.readFile(paths.input, "utf8")));
  const result = await build(payload, paths.output, paths.qaDir);
  process.stdout.write(`${JSON.stringify(result)}\n`);
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`);
  process.exitCode = 1;
});
