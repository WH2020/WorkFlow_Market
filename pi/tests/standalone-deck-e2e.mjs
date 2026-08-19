import assert from "node:assert/strict";
import { execFile as execFileCallback } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execFile = promisify(execFileCallback);
const repository = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");

async function main() {
  const temporary = await fs.mkdtemp(join(os.tmpdir(), "workflow-market-ppt-"));
  const input = join(temporary, "payload.json");
  const output = join(temporary, "standalone-agent-test.pptx");
  const qa = join(temporary, "qa");
  const payload = {
    schema_version: "1.0",
    snapshot_sha256: "a".repeat(64),
    output_name: "standalone-agent-test.pptx",
    profile_id: "market-director",
    template_id: "management-report",
    period: { start: "2026-08-17", end: "2026-08-21" },
    slides: [
      {
        title: "市场工作周报",
        subtitle: "独立 PPT 引擎验收",
        layout_intent: "top-hero",
        notes: "验证封面、备注和来源。",
        sources: [{ title: "PptxGenJS", url: "https://github.com/gitbrent/PptxGenJS" }],
      },
      {
        title: "本周工作形成三个可复用成果",
        lead: "工作流、知识沉淀和汇报能力均进入可验证状态。",
        body: ["完成独立 PPTX 生成", "完成真实逐页渲染", "完成来源备注验证"],
        layout_intent: "top-hero",
        notes: "本页用于验证 top-hero。",
        sources: [{ title: "LibreOffice", url: "https://www.libreoffice.org/" }],
      },
      {
        title: "生成与质量检查由两个边界清晰的模块完成",
        body: ["PptxGenJS：生成可编辑元素", "LibreOffice：转换为真实渲染 PDF", "PDF.js：逐页生成 PNG", "本地 QA：检查边界、备注与页数"],
        layout_intent: "fifty-fifty",
        notes: "本页用于验证双栏布局。",
        sources: [{ title: "PDF.js", url: "https://github.com/mozilla/pdf.js" }],
      },
      {
        title: "下一步是在真实业务周报中验证内容质量",
        body: ["先使用一周真实任务生成初稿", "由市场总监确认结论与来源", "根据反馈调整模板与字段"],
        callout: "所有对外动作仍由用户决定。",
        layout_intent: "two-thirds",
        notes: "本页用于验证主次栏和结论框。",
        sources: [{ title: "项目仓库", url: "https://github.com/WH2020/WorkFlow_Market" }],
      },
    ],
  };
  await fs.writeFile(input, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  try {
    const builder = join(repository, "pi", "artifacts", "build-director-deck.mjs");
    const validator = join(repository, "pi", "artifacts", "validate-and-render-deck.mjs");
    const built = await execFile(process.execPath, [builder, "--input", input, "--output", output, "--qa-dir", qa], {
      cwd: repository,
      timeout: 180_000,
      windowsHide: true,
    });
    const validated = await execFile(
      process.execPath,
      [validator, "--input", output, "--qa-dir", qa, "--expected-slides", "4"],
      { cwd: repository, timeout: 300_000, windowsHide: true, maxBuffer: 4 * 1024 * 1024 },
    );
    const buildResult = JSON.parse(built.stdout.trim().split(/\r?\n/u).at(-1));
    const qaResult = JSON.parse(validated.stdout.trim().split(/\r?\n/u).at(-1));
    assert.equal(buildResult.engine, "PptxGenJS 4.0.1");
    assert.equal(buildResult.slide_count, 4);
    assert.equal(qaResult.status, "ok");
    assert.equal(qaResult.preview_count, 4);
    assert.equal(qaResult.structure.editable_text_slides, 4);
    assert.match(qaResult.message, /No overflow detected/u);
    assert.ok((await fs.stat(output)).size > 10_000);
    assert.ok((await fs.stat(join(qa, "deck-montage.png"))).size > 1_000);
    process.stdout.write(`${JSON.stringify({ status: "ok", engine: buildResult.engine, renderer: qaResult.renderer, slides: 4 })}\n`);
  } finally {
    if (process.env.KEEP_PPT_E2E !== "1") await fs.rm(temporary, { recursive: true, force: true });
    else process.stdout.write(`PPT E2E artifacts: ${temporary}\n`);
  }
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.stack || error.message : String(error)}\n`);
  process.exitCode = 1;
});
