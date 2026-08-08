---
name: build-market-decks
description: 创建和修订市场总监使用的 PowerPoint 演示文稿，包括 CEO 周报、地方政府合作方案、客户解决方案、行业研究和销售复盘。用于选择、修改或替换模板，将资料转成逐页策划稿，生成可编辑 PPTX，并检查内容、来源和版式。
---

# 市场演示文稿

## 模板选择

读取 Project 的 `library/templates/template-catalog.json`。用户未指定时按任务类型使用 active 或对应内置模板。用户可以要求：使用默认、仅本次修改、导入为新模板、替换当前模板。使用插件根目录 `scripts/template_manager.py` 管理模板，不覆盖内置文件。

## 工作流

1. 明确受众、目标、演示场景、页数、必须保留内容和模板选择。
2. 完成资料核验；缺少来源的关键数字不得直接上版。
3. 按 `references/deck-workflow.md` 先形成逐页“数字便利贴”和策划稿。
4. 生成可编辑的文本、形状和图表。仅在复杂示意图需要时使用 SVG 或位图，不用整页图片冒充可编辑 PPT。
5. CEO 周报按 `references/weekly-ceo-structure.md` 控制在 7-10 页，默认 8 页。
6. 优先使用可用的 Presentations 插件；没有时使用插件脚本 `build_weekly_deck.py` 或任务环境中的 PPTX 工具。
7. 检查页面数量、标题层级、文本溢出、数字口径、来源、字体和对比度。返回文件位置及待人工确认项。

## 视觉原则

保持安静、专业、适合反复汇报。使用内容驱动的网格和足够留白，不机械套用卡片。每页只保留一个中心结论，图表直接回答问题。

