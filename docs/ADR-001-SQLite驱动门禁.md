# ADR-001：阶段 A SQLite 驱动门禁

- 状态：**有条件通过——Windows x64 已通过，macOS arm64/x64 等待 CI**
- 日期：2026-08-21
- 范围：STORE-A0-01 / STORE-A0-02 / STORE-A0-03
- 关联设计：[SQLite 数据模型与迁移](ARCH-P0-SQLite数据模型与迁移.md)

## 1. 决策

阶段 A 选择“固定 Node 24 LTS 补丁版本 + 内置 `node:sqlite`”作为首选候选，不引入第三方 SQLite 原生二进制。正式切换仍需满足两个条件：

1. Windows x64、macOS arm64、macOS x64 的同一门禁全部通过。
2. 正式安装器固定到已验证的 Node 24.x 版本后，再把业务存储从 CSV 切换到 SQLite。

在这两个条件满足前：

- 当前 `package.json` 与安装器仍保持现有 Node 22.19+ 兼容声明。
- SQLite 探针不会被现有销售服务加载。
- 不创建、不迁移、不修改任何正式销售数据。

## 2. 选择依据

- Node 24 是 LTS 版本线，项目候选固定为 24.19.0。
- Node 官方文档显示，`node:sqlite` 从 24.15.0 起进入 Release Candidate；因此本机原有 24.14.0 会被门禁明确拒绝。
- 内置模块避免 `better-sqlite3` 在 Windows、macOS Intel 和 Apple Silicon 上的原生包、签名和供应链成本。
- SQLite schema 保持跨语言可读；Python 标准库只读验证被纳入强制门禁。

官方依据：

- [Node.js 24 LTS 生命周期](https://nodejs.org/en/about/previous-releases)
- [Node.js 24 SQLite 文档与稳定性记录](https://nodejs.org/download/release/latest-v24.x/docs/api/sqlite.html)
- [GitHub 托管运行器架构与标签](https://github.com/actions/runner-images)

## 3. 已实现的门禁

### 3.1 最小存储原型

`pi/extensions/local-business-store.ts` 实现了只用于 A0 的最小事务原型：

- WAL、`synchronous=FULL`、外键和有限 `busy_timeout`。
- 受控 `insert/update`，更新必须提供 `expected_version`。
- 业务 mutation 与 committed receipt 位于同一事务。
- 同一 intent + 同一 payload 幂等返回；同一 intent + 不同 payload 拒绝。
- 只读打开、完整性检查和受管备份。
- 禁止扩展加载、启用 defensive 模式、只使用参数化值。

该原型只有 `gate_*` 测试表，不是阶段 A 正式业务 schema。

### 3.2 故障注入

`pi/tests/sqlite-driver-gate.mjs` 与独立 worker 覆盖：

1. 运行时版本和 CPU 架构。
2. WAL、FULL、外键、锁预算和完整性。
3. 同一 intent 100 次重复提交。
4. 乐观并发版本冲突。
5. 两个独立进程并发提交相同 intent，以及同 intent 不同 payload 冲突。
6. `BEGIN` 后、mutation 后、receipt 后强制退出。
7. `COMMIT` 后、任务状态更新前强制退出并恢复。
8. 两个独立进程同时更新同一版本。
9. 写锁占用时的有限等待。
10. SQLite 在线备份、Node 只读打开和 Python `sqlite3` 只读对账。

门禁输出机器可读 JSON；测试数据库只建立在系统临时目录并在结束后清理。

### 3.3 CI 矩阵

`.github/workflows/cross-platform.yml` 新增独立 `sqlite-driver-gate`，使用 Node 24.19.0：

| 运行器 | 目标架构 | 状态 |
|---|---|---|
| `windows-latest` | x64 | 本地已通过；等待 CI 重复确认 |
| `macos-15` | arm64 | 等待 CI |
| `macos-15-intel` | x64 | 等待 CI |

每个平台都会上传单独的 JSON 报告。现有 Node 22.19 的产品回归 job 暂不改变，避免在 A0 结论前扩大兼容性变更。

## 4. Windows 本地结果

执行环境：

- Windows x64
- 官方 Node 24.19.0 便携包
- Node 归档 SHA-256：`57f71ab3652e797d84acddc79c81cc9ff1c6ddb2a1974cdb83f00fee9bff4c73`
- Node 内置 SQLite：3.53.3
- Python 3.11.9 / SQLite 3.45.1

结果：首次门禁 8/8 检查通过；增加同 intent 并发回归后，本地严格门禁为 9/9 通过。

| 检查 | 结果 |
|---|---|
| 配置与完整性 | WAL、FULL、外键、5 秒默认预算、integrity=ok |
| 幂等回执 | 100 次重试只产生 1 条记录、1 条回执 |
| 版本冲突 | 旧版本更新被拒绝 |
| 提交前退出 | 三个故障点均回滚记录和回执 |
| 提交后退出 | 重启后从 committed receipt 确定恢复 |
| 并发写入 | 1 个成功、1 个版本冲突，无覆盖 |
| 同 intent 并发 | 相同 payload 的两个调用共享一份回执；不同 payload 只有一个提交 |
| 锁预算 | 配置 250 ms，观察约 418 ms（含子进程启动），总等待受限 |
| 备份与跨语言读取 | Node 备份完整，Python 只读计数与 schema 一致 |

本机现有 Node 24.14.0 的拒绝结果也已验证：门禁在任何数据库场景执行前停止，提示最低版本 24.15.0。

## 5. 尚未证实的事项

- macOS Apple Silicon 与 Intel 的真实故障注入结果。
- GitHub Actions 上 Node 24.19.0 与两种 macOS 文件系统/锁行为。
- 正式 Tauri 安装包如何携带或发现固定 Node 24 运行时。
- 正式业务 schema、CSV 迁移和客户 360 查询性能；这些属于 A1–A3，不在 A0 原型中。

因此当前结论是“候选可继续”，不是“SQLite 已可替换正式数据”。

## 6. 最终通过规则

只有三份 CI 报告全部为 `status=ok`，且现有 TypeScript、Python、平台校验和桌面回归均通过，ADR 才可改为“通过”。之后再执行：

1. 固定安装器 Node 版本和升级提示。
2. 实现正式 schema v1 与迁移 manifest。
3. 在隔离副本上运行真实 CSV dry-run。
4. 用户审阅迁移报告并批准后，才允许切换主存储。

任一 macOS 门禁失败时，保持 CSV 主存储并评估 `better-sqlite3`；不得跳过失败平台或静默回退到另一驱动。

## 7. 复现命令

在 Node 24.15.0 以上、24.x 版本中运行：

```powershell
node --no-warnings pi/tests/sqlite-driver-gate.mjs `
  --expect-arch x64 `
  --report tmp/sqlite-driver-gate/windows-x64.json
```

macOS Apple Silicon 使用 `--expect-arch arm64`，Intel 使用 `--expect-arch x64`。也可以运行：

```text
npm run test:sqlite-gate
```

若系统 Node 低于 24.15.0，命令应失败，这是预期的发布保护行为。
