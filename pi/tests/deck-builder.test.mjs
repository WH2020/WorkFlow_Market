import assert from "node:assert/strict";
import test from "node:test";

import { fontFamilies, slideTreatment } from "../artifacts/build-director-deck.mjs";

test("only a content-free first top-hero slide is treated as a cover", () => {
  assert.equal(slideTreatment({ title: "封面", subtitle: "副标题", layout_intent: "top-hero" }, 0), "cover");
  assert.equal(slideTreatment({ title: "正文", body: ["不得丢失"], layout_intent: "top-hero" }, 0), "top-hero");
  assert.equal(slideTreatment({ title: "正文", lead: "不得丢失", layout_intent: "single-focus" }, 0), "single-focus");
  assert.equal(slideTreatment({ title: "正文", eyebrow: "章节", layout_intent: "three-column" }, 0), "three-column");
});

test("all approved layout intents remain distinct renderer treatments", () => {
  const intents = ["single-focus", "fifty-fifty", "two-thirds", "three-column", "top-hero", "mixed-grid"];
  for (const intent of intents) assert.equal(slideTreatment({ title: intent, body: ["内容"], layout_intent: intent }, 1), intent);
  assert.equal(slideTreatment({ title: "legacy", body: ["内容"] }, 1), "single-focus");
});

test("PPT fonts use native Windows and macOS CJK defaults with an explicit override", () => {
  assert.deepEqual(fontFamilies("win32", {}), { cjk: "Microsoft YaHei", latin: "Arial" });
  assert.deepEqual(fontFamilies("darwin", {}), { cjk: "PingFang SC", latin: "Arial" });
  assert.deepEqual(
    fontFamilies("darwin", { WORKFLOW_CJK_FONT: "Custom CJK", WORKFLOW_LATIN_FONT: "Custom Latin" }),
    { cjk: "Custom CJK", latin: "Custom Latin" },
  );
});
