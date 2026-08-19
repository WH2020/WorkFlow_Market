---
name: plan-director-presentations
description: 规划市场总监或产品总监的行业、政府、管理和自定义 PPT；用于先澄清 brief、绑定任务证据、编排大纲与逐页策划，再经审批生成可编辑 PPTX。周报沿用 build-director-decks 预设。
---

# PPT 工作室

## 先确定任务

先确认当前 Profile、主题、受众、目的和使用场合。再补充期望决策、演讲时长、4–10 页目标、资料范围、保密等级、语言、输出名和时间范围。已明确的字段不重复询问；受众、目的或场合缺失时，停在 `create_brief`，提出最少的关键问题。

模式写入 plan：`quick` 用于结构稳定的内部汇报，`standard` 用于行业研究和普通方案，`strict` 用于政府或重要客户材料。Phase 1 的 DAG 对三种模式都保留一次大纲确认和一次正式生成硬审批；这是偏安全的统一门槛。当前严格模式不额外增加逐页策划 Approval，而是在正式生成硬审批中展示逐页策划与冻结载荷；独立逐页策划确认留到 Phase 2。

## 证据与规划顺序

1. 公开资料必须先经 `director_web_search` 发现，再由 `director_web_open` 读取正文；随后调用 `director_knowledge_search` 补充当前 Profile 可读的内部来源。不要把内部资料拼入外部检索词。
2. 将外部事实、分析判断、假设和未知分开。事实必须引用当前任务证据 registry 的 `source_id`；分析与假设要明确标记，不能伪装成检索结果。
3. `propose_outline` 只组织 4–10 张便利贴，每页一个结论式标题。调用 `director_presentation_plan_write` 保存 `phase=outline`，再完成节点并停在大纲 Approval。
4. 用户确认后生成逐页策划。每页包含受众所得、事实及证据、分析、假设、未知、版面意图、讲者备注、风险提示和与正式渲染一一对应的 `render`。
   - `render.title` 必须等于已确认的 `conclusion_title`，`render.notes` 必须等于 `speaker_notes`。
   - `render.subtitle/lead/body/callout` 只能逐条取自 brief、受众所得、事实，或使用 `分析：`、`假设：`、`未知：`、`风险：` 前缀映射相应策划字段；不得在 render 中新写未进入策划的信息。
5. 设计令牌只允许 `management-report`、`government-program`、`technology-research`。布局意图从 `single-focus`、`fifty-fifty`、`two-thirds`、`three-column`、`top-hero`、`mixed-grid` 中选择；内容决定布局，不为填满模板增加事实。
6. 调用 `director_presentation_plan_write` 保存 `phase=final`，必须同时携带 outline 保存返回的 `expected_plan_sha256` 和 `expected_context_snapshot_sha256`。同一任务从 outline 到 final 只允许增加逐页策划和确定设计令牌；不得改变已确认的 brief、scene、mode、period、evidence_refs、outline 或 output_name。任何大纲修订都走新的任务和新的 outline Approval。适配器会核对 Profile、任务、版本、证据引用和文件哈希，原子写入 `.pi/director-runtime/presentation-plans/<task_id>.json`，返回新的 `plan_sha256` 与 `context_snapshot_sha256`。
7. `validate_and_freeze` 将 plan 的 `render` 数组原样作为正式 deck `slides`，并携带同一 `plan_sha256`、`context_snapshot_sha256`、Profile、period、template_id 和 output_name。调用 `director_propose_write_intent(logical_tool="artifact.deck.write", payload=完整载荷)` 后才能完成 Validator。
8. 到达 `approve_render` 必须暂停。任何 plan、来源、Profile、输出名或正式载荷变化都会使旧审批失效。
9. 批准后只调用 `director_artifact_deck_write`；QA 通过前不会提交到 `outputs/`，同名文件不覆盖，相同 intent 依 receipt 恢复。

## 工作台修订请求

请求中出现 `[PRESENTATION_PLAN_REVISION]` 时，将其视为一个新的受管任务，而不是旧任务的续写。读取其中的 `source_task_id`、`source_plan_sha256` 和精简 outline 作为用户明确提出的修订上下文，只接受本次请求列出的增删、排序和标题修改；仍须重新执行当前任务的资料/证据收集、从 `version=1` 写入新 task plan、重新确认大纲并重新冻结正式载荷。不得复制旧任务的 evidence registry、Approval、intent_id、payload SHA-256 或 receipt，也不得直接修改旧 plan JSON。引用的旧 plan 缺失或哈希不符时停止并要求用户重新打开当前版本。

## Plan 必要契约

- `schema_version`: `1.0`
- `project_id`: 1–128 位安全 ID；同一任务后续更新不得改变
- `profile_id`: 当前 Profile
- `scene`: `weekly`、`industry`、`government` 或 `custom`
- `mode`: `quick`、`standard` 或 `strict`
- `phase`: `outline` 或 `final`
- `version`: 首次为 1，后续严格加 1
- `period.start/end`: ISO 日期，最多 31 天
- `brief`: `topic`、`audience`、`purpose`、`occasion`、`language`、`confidentiality`、`target_slides`；可加期望决策和时长
- `evidence_refs`: 当前任务 registry 中的稳定 `source_id`，至少一项
- `outline`: 4–10 项，`slide_id`、`order`、`conclusion_title`、`evidence_refs`
- `slides`: `phase=final` 时必须与 outline 一一对应；每页的事实引用和 `render.sources` 必须来自该页 evidence_refs；`render.layout_intent` 必须与逐页策划的 `layout_intent` 完全一致
- `design_system.token_id`: 三套设计令牌之一
- `output_name`: 安全 ASCII `.pptx` 文件名

不得在 plan、speaker notes 或 URL 中写入 API key、token、签名、账号口令。生成后不发送、不上传、不自动对外使用。
