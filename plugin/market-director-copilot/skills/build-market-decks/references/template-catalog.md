# 模板库

内置模板：`ceo-weekly`、`government-formal`、`customer-solution`、`industry-research`、`sales-review`。

用户上传的公司 PowerPoint 模板由 `$create-office-documents` 管理，保存在 `library/templates/company/powerpoint/`。本次指定的公司模板优先于 active 公司模板，active 公司模板优先于以下内置模板。

模板选择规则：

- CEO 汇报使用 `ceo-weekly`。
- 政府领导、招商或项目建议书使用 `government-formal`。
- 客户售前与解决方案使用 `customer-solution`。
- 行业趋势和知识分享使用 `industry-research`。
- 销售管理会议使用 `sales-review`。

导入用户模板时使用 `scripts/office_template_manager.py` 创建新 ID 和版本，再由用户决定是否设为 active。替换模板必须生成新版本并保留旧文件；不得覆盖内置模板，以便随时恢复。
