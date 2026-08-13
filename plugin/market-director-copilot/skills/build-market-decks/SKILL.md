---
name: build-market-decks
description: 创建和修订市场总监使用的 PowerPoint 演示文稿，包括 CEO 周报、地方政府合作方案、客户解决方案、销售演示或路演、行业研究和销售复盘。销售演示、客户汇报和路演优先使用本 Skill；公司模板办公文档、表格和非演示销售材料优先使用 `$create-office-documents`。支持原生 PPTX 与 SVG-first Bento Grid 双模式，用于按销售阶段和买方角色策划销售 Deck，选择、修改或替换模板，生成可编辑或 SVG 页面，并检查内容、来源和版式。
---

# 市场演示文稿

## 模板选择

先使用 `$create-office-documents` 检查 `library/templates/office-template-catalog.json` 中是否有用户明确指定或 active 的公司 PowerPoint 模板；没有时读取 `library/templates/template-catalog.json` 并按任务类型使用对应内置模板。公司模板使用 `scripts/office_template_manager.py` 版本化管理，内置模板使用 `scripts/template_manager.py` 管理；两者均不得被生成结果覆盖。

## 生产模式

- `native`：默认模式。使用 PPT 原生文本、形状和图表，适合 CEO 周报、政府正式文件、数据表和需要多人逐项修改的交付物。
- `svg-first`：视觉模式。读取 `references/svg-bento-workflow.md`，以 1280×720 为画布，根据内容重要性自动组合 Bento Grid 卡片，先生成 SVG 页面，再按需要封装为 SVG 图片型 PPTX。必须明确告知用户 SVG 进入 PPTX 后不等于原生文本框。

用户未指定时按编辑需求选择：需要逐项改字使用 `native`；需要复杂信息架构、视觉叙事或并列比较使用 `svg-first`。一个演示文稿可以逐页混用，但逐页策划稿必须标记模式。

## 工作流

1. 明确受众、目标、演示场景、页数、必须保留内容和模板选择。
2. 完成资料核验；缺少来源的关键数字不得直接上版。
3. 销售演示、客户路演或内部推动材料先读取 `references/sales-deck-profile.md`；其他演示按任务专用 reference 执行。
4. 按 `references/deck-workflow.md` 先形成逐页“数字便利贴”和策划稿。
5. `native` 模式生成可编辑的文本、形状和图表；`svg-first` 模式使用 `scripts/build_svg_bento_deck.py` 生成页面 SVG 和 manifest。
6. CEO 周报按 `references/weekly-ceo-structure.md` 控制在 7-10 页，默认 8 页。
7. 优先使用可用的 Presentations 插件；没有时使用插件脚本 `build_weekly_deck.py` 或 `build_svg_bento_deck.py`。
8. `native` 使用 `validate_outputs.py`；`svg-first` 使用 `validate_svg_deck.py`，并在 PowerPoint 或浏览器中逐页检查实际渲染。
9. 返回文件位置、生产模式、来源和待人工确认项。不得把 SVG 图片型 PPTX 描述成逐字可编辑的原生 PPT。

## 视觉原则

保持安静、专业、适合反复汇报。使用内容驱动的网格和足够留白，不机械套用卡片。每页只保留一个中心结论，图表直接回答问题。
