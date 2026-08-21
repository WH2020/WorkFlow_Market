import assert from "node:assert/strict";
import { execFile as execFileCallback } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";
import { promisify } from "node:util";

import JSZip from "jszip";

const execFile = promisify(execFileCallback);
const builder = resolve("pi/artifacts/build-bid-document.mjs");

function payload(outputName = "formal-bid.docx") {
  return {
    schema_version: "1.0",
    profile_id: "sales-director",
    bid_id: "bid-document-test",
    snapshot_sha256: "a".repeat(64),
    output_name: outputName,
    title: "测试项目投标文件",
    buyer: "测试采购人",
    bidder: "测试投标人",
    sections: [{
      section_id: "section-001",
      title: "项目理解与实施方案",
      level: 1,
      paragraphs: ["本段仅用于验证可编辑文字文档的结构、中文文本和来源附录。"],
      tables: [{ columns: ["检查项", "状态"], rows: [["来源核验", "已完成"]] }],
    }],
    sources: [{
      source_id: "source-001",
      title: "测试招标文件.pdf",
      path: "inputs/bids/bid-document-test/test.pdf",
      sha256: "b".repeat(64),
      page: 3,
    }],
  };
}

test("bid document builder creates a self-contained editable DOCX with source appendix", async () => {
  const root = await mkdtemp(join(tmpdir(), "agent4market-bid-docx-"));
  try {
    const input = join(root, "payload.json");
    const output = join(root, "formal-bid.docx");
    const qa = join(root, "qa");
    await writeFile(input, `${JSON.stringify(payload(), null, 2)}\n`, "utf8");
    const result = await execFile(process.execPath, [builder, "--input", input, "--output", output, "--qa-dir", qa], {
      timeout: 30_000,
      windowsHide: true,
      maxBuffer: 1024 * 1024,
    });
    const built = JSON.parse(String(result.stdout).trim());
    assert.equal(built.manifest.sections, 1);
    assert.equal(built.manifest.sources, 1);
    const archive = await JSZip.loadAsync(await readFile(output), { checkCRC32: true });
    const document = await archive.file("word/document.xml").async("string");
    const relationships = await archive.file("word/_rels/document.xml.rels").async("string");
    assert.match(document, /测试项目投标文件/u);
    assert.match(document, /来源与证据附录/u);
    assert.match(document, /生成边界/u);
    assert.doesNotMatch(relationships, /TargetMode="External"/u);
    assert.ok(archive.file("word/styles.xml"));
    assert.ok(archive.file("word/header1.xml"));
    assert.ok(archive.file("word/footer1.xml"));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("bid document builder rejects an unsafe output name in the approved payload", async () => {
  const root = await mkdtemp(join(tmpdir(), "agent4market-bid-docx-invalid-"));
  try {
    const input = join(root, "payload.json");
    await writeFile(input, `${JSON.stringify(payload("../outside.docx"), null, 2)}\n`, "utf8");
    await assert.rejects(
      execFile(process.execPath, [builder, "--input", input, "--output", join(root, "artifact.docx"), "--qa-dir", join(root, "qa")], {
        timeout: 30_000,
        windowsHide: true,
        maxBuffer: 1024 * 1024,
      }),
      /输出文件名无效/u,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
