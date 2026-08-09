# 公司办公模板库

## 支持范围

| 文档类型 | 可导入格式 | 典型用途 |
| --- | --- | --- |
| `word` | `.docx`、`.dotx` | 报告、方案、通知、纪要、制度文件 |
| `excel` | `.xlsx`、`.xltx` | 台账、预算、报销、分析表、数据模板 |
| `powerpoint` | `.pptx`、`.potx` | 周报、政府方案、客户方案、研究和复盘 |

宏格式默认不导入。PDF、图片和品牌手册可以作为视觉参考，但不能替代可编辑 Office 模板。

## 导入与替换

用户上传模板后：

1. 确认模板名称、文档类型、任务类型和是否设为 active。
2. 使用插件根目录脚本导入：

```powershell
.\.venv\Scripts\python.exe plugin\market-director-copilot\scripts\office_template_manager.py `
  --project . import `
  --id company-weekly-report `
  --name "公司周报模板" `
  --type word `
  --kind weekly `
  --file "D:\资料\公司周报模板.docx" `
  --activate
```

3. 同一模板更新时使用 `replace`，脚本创建新版本并保留历史文件：

```powershell
.\.venv\Scripts\python.exe plugin\market-director-copilot\scripts\office_template_manager.py `
  --project . replace company-weekly-report `
  --file "D:\资料\公司周报模板_v2.docx"
```

4. 使用 `validate` 核对目录、哈希和 OOXML 结构：

```powershell
.\.venv\Scripts\python.exe plugin\market-director-copilot\scripts\office_template_manager.py --project . validate
```

## 选择规则

使用 `resolve` 返回实际模板路径：

```powershell
.\.venv\Scripts\python.exe plugin\market-director-copilot\scripts\office_template_manager.py `
  --project . resolve --type word --kind weekly
```

选择优先级为显式模板 ID、任务类型 active、文档类型 active。PowerPoint 没有公司模板时，脚本回退到现有内置 PPT 模板；Word 和 Excel 回退到标准空白文档。

## 目录与版本

```text
library/templates/
  office-template-catalog.json       本地目录，由脚本生成
  company/<type>/<id>/vNNN/          用户上传的不可变版本
  template-catalog.json              内置 PPT 目录
```

目录项记录模板 ID、名称、文档类型、任务类型、当前版本、相对路径、原始文件名、SHA-256、文件大小和更新时间。模板内容及本地目录默认不提交 Git。

## 生成约束

- 从模板副本生成，不直接修改库内原件。
- 保留模板中有业务语义的工作表、版式、样式、命名区域和占位符。
- 不保留无关示例数据；无法判断示例内容是否应删除时列为待确认。
- 不擅自解除工作表保护、文档保护或受限编辑。
- 输出必须保持可编辑；确需转为图片或 PDF 时，同时保留可编辑源文件。
