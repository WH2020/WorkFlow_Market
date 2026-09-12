# macOS 一键更新

## 状态与范围

当前为未发布源码实现，面向已登记的 macOS 13+ 通用安装版。目标是在一次用户确认的更新中，同步更新 `~/Applications/Agent4Market.app` 与原运行目录中的程序代码；不迁移安装根目录，不同步跨设备用户数据，也不读取或搬迁钥匙串。

旧公开版本没有此更新器，必须先手动安装以后正式发布的完整 runtime 包，并运行 `bash scripts/setup-macos.sh` 建立程序清单和运行依赖基线。源码检出或缺少基线的安装仍可检查版本，但不开放一键安装。

首版保留 `.venv`、`node_modules` 和外部 Python/Node。仅接受依赖声明、锁文件和当前已登记依赖指纹一致的程序升级；依赖或更新/数据协议改变、降级时拒绝，提示按发布说明手动安装。此限制避免更新途中安装不兼容的本机依赖；不应宣传为可自动升级任意 Python/Node 版本。

## 用户流程与安全边界

1. 设置 → 应用更新 → 检查更新；固定正式 Release 同时含程序 ZIP、通用应用 ZIP、清单和签名时才能开始。
2. 用户确认“一键更新并重启”，再在 macOS 系统对话框中确认。系统对话框默认取消、120 秒超时；只有浏览器令牌不足以授权 Mac 安装。准备期间冻结业务写入，下载/暂存/验证阶段可取消。
3. 验证独立 Ed25519 发布者签名、附件大小及 SHA-256、程序/应用清单、版本、通用架构与 `codesign --verify --deep --strict`。未知程序改动、不安全 owner/mode/扩展 ACL、越界链接、硬链接或依赖变化均拒绝。
4. 原生桌面通知 Pi 与后端退出，确认自有进程组停止。独立原生辅助程序等待实际父进程退出后才开始备份及替换。
5. 程序文件逐项替换，`.app` 以同卷原子交换切换；不支持原子交换的文件系统拒绝更新，不回退为先删除旧应用。
6. 先做不开放业务服务的自检，再进行隐藏桌面/Python/Pi 试启动。试启动使用隔离 HOME 和模型目录，不读取真实提供者密钥、不调用模型、不恢复业务任务。只有回执、稳定性和完整性核验通过，且试启动进程组再次完全停止，才提交事务；随后普通启动加载原有用户配置。

更新失败在程序写入前保持原样，写入后按日志恢复旧程序及旧 `.app`。关停无法确认时不回滚正在使用的程序，保留 active 与恢复材料。若交接控制文件只写入一部分，业务操作继续暂停，提示关闭并重新打开完成恢复，不擅自解除冻结。

## 数据、进程与恢复

原安装根由受保护的 `~/Library/Application Support/Agent4Market/install-root` 标记确定，不按当前工作目录寻找。路径保持不变，因此以安装根绑定的钥匙串服务名保持不变。

程序白名单排除 `.pi/`、`data/`、`inputs/`、`outputs/`、公司模板、`.venv/` 与 `node_modules/`。更新控制文件和合成试运行日志单独保存；普通启动的正常日志、缓存和派生配置不属于程序覆盖或数据迁移。

- 独立辅助程序、计划、日志：`~/Library/Application Support/Agent4Market/app-updates/<任务ID>/`。
- 原卷程序暂存、备份与 Pi 门禁：`<原运行目录>/.pi/app-updates/<任务ID>/`。
- 应用暂存/交换后的旧应用：`~/Applications/.Agent4Market-update-<任务ID>.app`。

恢复逻辑在 Tauri、WebView、Python、Node 初始化前运行，不依赖正在替换的程序或暂存目录。已提交事务验证新版后完成结果回执，未提交事务恢复旧版。未知内容、损坏备份或不安全路径只停止，不覆盖不明文件。恢复材料暂不自动清理；失败后不要手动删除 active 来绕过检查。

程序复制使用 64 KiB 缓冲流式校验，摘要不符时不替换目标；权限在文件持久化前设置。准备与交接前按设备聚合同卷的下载、暂存、备份、程序增长、最大临时文件及余量。此空间检查不是永久预留；其他进程之后填满磁盘仍会导致停止并要求释放空间后恢复。

Mac 的受控核心使用独立进程组，试运行的整个进程集合由辅助程序持有未回收的组长。所有发信号和组查询发生在回收之前，避免 PID/PGID 复用；不能只把端口空闲当作关停完成。Darwin 对已退出但未回收的进程有不同的查询/发信号语义，处理遵循 [Apple XNU 进程查询](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/kern_prot.c)、[信号实现](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/kern_sig.c)及 [libproc](https://github.com/apple-oss-distributions/xnu/blob/main/libsyscall/wrappers/libproc/libproc.c)。无法清理的残留进程使更新停止，不扫描或终止其他应用。

旧设置中的独立智能核心 Terminal 选项不再启动非托管核心，改用嵌入式核心及日志；保留原设置文件，不因此阻断应用启动。

## 构建与发布

`scripts/build-macos-desktop.sh` 要求已审阅、已提交的干净源码，使 `.app` 与 `git archive` 程序导出对应同一提交。通用构建额外生成以下**未签名**更新附件，并将初始清单放入完整手动安装包：

- `Agent4Market-<版本>-macos-programs.zip`
- `Agent4Market-<版本>-macos-universal-app.zip`
- `macOS-install-manifest.json`

`scripts/sign-macos-update.py` 在已有发布密钥的受保护环境中离线生成 `macOS-update-signature.json`，签名绑定平台、版本和全部三个附件。客户端复用已固定发布公钥；不创建新的发布密钥，不把私钥放到 Mac 安装包或 GitHub CI。

```text
python -I -B scripts/sign-macos-update.py --private-key <已有加密私钥> --version <版本> --programs <程序ZIP> --app <通用应用ZIP> --manifest <清单> --output <macOS-update-signature.json>
```

当前本地 DPAPI 发布密钥只能在受支持的原 Windows 用户环境使用；它不是可移植备份。签名可在该环境对 Mac 构建附件离线完成。正式发布前仍须安排独立加密备份。

独立更新签名不等于 Apple Developer ID 或公证。默认 ad-hoc 构建的系统信任限制仍在；更新器不会移除 quarantine 属性或绕过 Gatekeeper。

## 实际验证与未覆盖项

2026-09-12，执行环境为 Windows：

- Python 定向测试 54 项通过：签名平台/版本/双附件绑定、清单/ZIP/链接拒绝、依赖约束、构建白名单、同卷空间汇总、部分交接保持冻结，以及共享更新 HTTP 门禁和 Windows 回归。
- `cargo test --offline --manifest-path desktop/src-tauri/Cargo.toml`：8 项通过，其中 3 项是跨平台文件事务测试，覆盖真实程序新增/删除/替换、重复回滚、退休路径校验及用户修改预检；不是 Mac 原生执行。
- `node --test tests/ui-app-updates.test.mjs tests/vertical-workflow-selection.test.ts`：39 项通过；`pnpm check:types` 通过。
- 新 Mac 原生辅助程序、文件事务和 POSIX 模块在 `aarch64-apple-darwin`、`x86_64-apple-darwin` 下通过 `cargo check --tests`。使用最小 Tauri 接口替身，验证平台 API/类型，不代表完整桌面链接、codesign 或 Mac 实机启动。

CI 已增加 Apple Silicon / Intel 两个 Mac 的原生 Rust 测试入口，包括短命组长留下后代与正常退出的进程组测试；本轮没有提交或触发这些作业。**尚未在 Mac 构建、签署或安装新包，也未实测 Finder 启动时的外部依赖路径、应用交换、完整试启动、失败回退、钥匙串不变及 Gatekeeper。正式交付前必须在两个架构的合成安装中完成这些验收。**

没有改动用户已有 Windows/Mac 安装、业务资料或真实凭据，没有提交 Git、推送或发布 Release。
