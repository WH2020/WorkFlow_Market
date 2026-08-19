---
name: build-director-decks
description: 创建市场总监或产品总监的管理汇报与每周工作 PPT。用于周五总结、CEO 周报、管理层决策汇报、产品周报和跨部门进展汇报；根据当前 Profile 选择内容结构，不套用另一个岗位的口径。
---

# 总监汇报与周报 PPT

## 开始前

确认当前 Profile、受众、汇报目标、时间范围、页数、模板和需要决策的事项。默认面向 CEO，控制在 7–10 页；用户另有要求时按实际场景调整。

## 共同结构

1. 先从知识快照、任务记录和业务台账收集本周事实，不把计划写成已完成。
2. 首页给出 3–5 条管理结论、主要风险和需要受众决定的事项。
3. 每页只表达一个中心结论；关键数字附口径、时间和来源。
4. 先形成大纲和逐页策划，再生成 PPT；周报作为 `$plan-director-presentations` 的 `scene=weekly`、`mode=quick` 预设，选择公司模板时复制原件，不覆盖模板库。
5. `collect_week` 必须先调用 `director_weekly_snapshot`，按 `period` 获取任务状态/审计、销售 customers/activities/resource_requests、outputs 元数据和知识库本周新增；只使用快照中的事实和来源版本。
6. `build_plan` 先形成 `phase=final` 的周报 plan，并在 `save_plan` 调用 `director_presentation_plan_write`。首版为 `version=1`；plan 中的 evidence_refs 只能使用当前快照登记到证据 registry 的来源，设计令牌默认 `management-report`。适配器返回 `plan_sha256` 和 `context_snapshot_sha256`。
7. `validate_payload` 只编制精确 deck 载荷，不生成文件。`slides` 必须逐字复用 final plan 每页的 `render`，携带返回的 `plan_sha256` 和 `context_snapshot_sha256`（作为 `snapshot_sha256`），Profile、period、template_id 和 output_name 必须与 plan 一致。调用 `director_propose_write_intent(logical_tool="artifact.deck.write", payload=完整载荷)` 做确定性绑定校验并冻结载荷 SHA-256，再完成 Validator。
8. 到达 Approval 必须暂停。用户批准的是完整载荷及其 SHA-256；批准前不得创建 `outputs/*.pptx`，载荷变化后必须重新冻结、重新批准。
9. 批准后 `render_deck` 才调用 `director_artifact_deck_write`。适配器使用项目内固定版本的 PptxGenJS 在当前 task/intent 私有目录生成可编辑 PPTX，再由 LibreOffice 真实转换为 PDF、PDF.js 逐页生成 PNG，并检查 layout JSON、文本容量、画布边界、文本框重叠、页数和 speaker notes 来源块；QA 通过后才独占提交到 `outputs/` 并写入 receipt。不得用普通 `write/edit` 伪造 `.pptx`，也不得依赖 Codex Desktop 私有运行时。
10. 每页外部事实和外部资产都放入 speaker notes 的 `[Sources]` 块；本地来源写项目相对路径，PDF 来源同时记录页码。URL 不得包含账号、口令、token、签名或密钥查询参数。
11. 生成后仍不发送、不上传、不对外使用；外部使用由用户另行决定。

## 适配器载荷

- `schema_version` 固定为 `1.0`。
- `plan_sha256` 必须来自当前任务保存的 final plan；`snapshot_sha256` 必须是同一次保存返回的 `context_snapshot_sha256`，不得复用另一任务或另一周期的哈希。
- `output_name` 是安全 ASCII `.pptx` 文件名；不得包含路径，已有文件不得覆盖。
- `profile_id` 使用当前 Profile；周报 `template_id` 使用 plan 的 `management-report`。
- `period.start/end` 使用 ISO 日期。
- `slides` 为 4–10 页。第一页是封面；后续每页包含单一结论式标题、可选 lead、最多 7 条 body、可选 callout、notes 和 sources。
- 市场总监默认顺序：本周结论、已完成事项、行业/政策、政府合作、销售进展、风险与待确认、下周行动。缺少真实内容时删页或写“未知/待确认”，不得补造。
- `sources` 每项包含标题以及 URL 或项目相对路径；URL 必须出现在快照的知识来源中。本地路径必须出现在 `source_versions` 中，并携带对应 `sha256`；提交前文件发生变化会停止。PDF 可加 `page`。没有外部来源的本地快照页也必须在 notes 说明证据范围。

## Profile 差异

### 市场总监

重点覆盖行业/政策变化、政府合作、重点客户、销售阶段、资源申请、风险和未来 14 天动作。客户进展只引用结构化证据，不按沟通数量评价销售。

### 产品总监

重点覆盖用户问题证据、需求与范围变化、指标/实验、路线图、交付健康度、发布风险、跨团队依赖和需要管理层取舍的事项。不要套用销售漏斗或政府合作结构。

## 交付

返回 PPT 路径、artifact receipt 路径、意图 ID、载荷 SHA-256、文件 SHA-256、模板、资料时间范围、LibreOffice 版本、逐页预览目录、独立 QA 结果、待确认项和未执行的外部操作。相同意图重试时先核验 receipt 与正式文件哈希，不能重复覆盖。项目依赖或 LibreOffice 缺失时停止在 `render_deck`，交付已批准的结构化载荷并明确未生成 PPT，不能跳过工具节点。
