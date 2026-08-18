# 来源卡片

每条卡片包含：

- `source_id`：稳定唯一标识。
- `title`、`url`、`publisher`。
- `published_date`、`accessed_date`。
- `region`、`topic`、`source_type`、`quality`。
- `key_facts`：可逐条引用的事实。
- `important_quotes`：必要时保留原文及页码。
- `interpretation`：与事实分开的分析。
- `limitations`：口径、样本、时效和利益相关限制。
- `exposure_status`：研究接触状态，使用 `未触达`、`匿名触达` 或 `身份暴露`；判定方法见 `research-exposure-check.md`。
- `status`：`verified`、`pending` 或 `superseded`。
- `notes`：至少记录 `content_sha256`、提取方法、可靠度、截断状态和证据页码；网页可同时记录 ETag 与 Last-Modified。`limited` 提取结果不能直接升级为正式事实。

知识卡片更新时保留旧来源，不用新结论覆盖历史记录。

