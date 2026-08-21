# A1 SQLite 存储与迁移使用说明

- 状态：实现完成；Windows、macOS Intel 与 macOS Apple Silicon 回归通过（[跨平台 CI #32450446290](https://github.com/WH2020/WorkFlow_Market/actions/runs/32450446290)）
- 适用范围：销售总监本地版 schema v1
- 关联设计：[SQLite 数据模型与迁移](ARCH-P0-SQLite数据模型与迁移.md)
- 关联计划：[实施与验收计划](PLAN-P0-实施与验收.md)

## 1. 当前能力与边界

A1 已提供正式 schema v1、TypeScript 事务 Store、离线 CSV 迁移、行级隔离、对账、备份恢复、显式 CSV 导出/回环导入，以及绑定精确报告、数据库和批准文件的存储指针切换。

当前在线 `sales.*` 与 `knowledge.*` 适配器仍使用旧 CSV；它们要到 A2 才会接入 SQLite。A1 不会因为数据库文件存在就自动切换，也不会双写。仓库中的测试只使用临时夹具，本轮没有扫描、迁移或修改真实业务 CSV。

## 2. 文件与契约

```text
agent_platform/migrations/001_sales_core.sql   schema v1
agent_platform/migrations/manifest.json        固定 SQL SHA-256
pi/extensions/business-store.ts                受控在线事务 Store
agent_platform/sales_store.py                   迁移、校验、备份、导出和指针工具
contracts/sales-mutation.schema.json            mutation 契约
contracts/migration-report.schema.json          迁移报告契约
```

数据库、WAL/SHM、迁移报告、业务导出、备份和存储指针都已加入 `.gitignore`。迁移 SQL 强制使用 LF，避免 Windows 换行转换破坏清单哈希。

## 3. 安全迁移顺序

### 3.1 只读预检

```text
python -m agent_platform migrate-sales-store --dry-run
```

默认读取项目内以下固定数据：

- `data/sales/customers.csv`
- `data/sales/activities.csv`
- `data/sales/resource-requests.csv`
- `data/sales/sales-assets.csv`
- `data/sales/salespeople.json`
- `data/knowledge/source-register.csv`

不指定 `--report` 时，预检只向终端输出 JSON，不创建数据库或报告文件。输入文件上限为 16 MiB/文件、250,000 行/文件；损坏 CSV、错误表头、符号链接目录或越界路径会在建库前停止。

### 3.2 创建 staging

```text
python -m agent_platform migrate-sales-store --staging --database data/imports/manual-a1/staging.db --report data/imports/manual-a1/migration-report.json
```

目标必须不存在。工具先检查可用空间，在同目录私有临时文件中完成建库、导入、外键检查和完整性检查，再以不覆盖方式发布。每个 CSV 数据行在 `import_rows` 中只能得到以下一种结果：

- `imported`
- `skipped_duplicate`
- `quarantined`
- `failed`

重复主键、孤立客户引用、非法时间、未知状态、公式型输入和含敏感参数的 URL 不会进入业务表；原行 JSON、行号、行哈希和错误码会保留在 staging 数据库。缺失的受控资料文件可以作为待修复元数据导入，绝对路径和越界路径不会写入业务表。

### 3.3 校验、备份与导出

```text
python -m agent_platform verify-sales-store --database data/imports/manual-a1/staging.db
python -m agent_platform backup-sales-store --database data/imports/manual-a1/staging.db --target backups/database/agent4market-v1.db
python -m agent_platform export-sales-store --database data/imports/manual-a1/staging.db --target-dir data/exports/manual-a1
python -m agent_platform import-sales-store-export --source-dir data/exports/manual-a1 --target-database data/imports/manual-a1/round-trip.db
```

备份、恢复、导出和回环导入都拒绝覆盖现有目标。CSV 导出会对电子表格公式前缀加安全转义，并在清单中记录精确单元格；回环导入按清单恢复原值和 `null`，然后比较全部核心表计数与逻辑状态 SHA-256。

## 4. 存储切换与恢复

当前阶段不要在正式工作目录执行切换；A2 适配和 A5 迁移向导尚未完成。底层命令已经实现，供自动化测试和后续受控 UI 调用。

切换要求同时满足：

1. staging 报告 `cutover_ready=true`。
2. 报告自身 SHA-256、数据库文件 SHA-256 和逻辑状态 SHA-256 全部一致。
3. 批准 JSON 精确绑定 `migration_batch_id`、`database_sha256`、`report_sha256`、`approval_id` 和 UTC `approved_at`。
4. 调用方提供当前存储指针 SHA-256；不存在时明确写 `absent`。

```text
python -m agent_platform activate-sales-store --database data/imports/manual-a1/staging.db --report data/imports/manual-a1/migration-report.json --approval data/imports/manual-a1/cutover-approval.json --expected-pointer-sha256 absent
```

切换使用 `prepared → pointer published → committed` 回执。若进程在指针发布和回执完成之间退出，使用相同参数重试会根据精确哈希确定恢复，不会创建第二次切换。

只有切换后逻辑业务状态完全没有变化时才允许自动恢复旧指针：

```text
python -m agent_platform rollback-sales-store --batch-id migration-<20位哈希> --expected-current-pointer-sha256 <当前指针SHA-256>
```

一旦检测到切换后有业务字段或关联变化，回滚会返回 `ROLLBACK_REQUIRES_RECONCILIATION`，必须先导出差异并人工决定，不能自动回到旧 CSV。

## 5. A2 前仍未开放的能力

- 工作台不会展示 SQLite 客户列表或客户 360。
- `sales.read/write`、`knowledge.search/write` 尚未读取 schema v1。
- 安装运行时尚未切到固定 Node 24.19.0。
- 没有对真实业务数据执行预检或 staging 导入。
- 没有启用正式 `data/storage-backend.json`。

因此 A1 是可验证的数据底座，不是正式数据切换发布。
