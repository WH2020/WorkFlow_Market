# Windows 一键更新

## 当前状态与范围

面向 Windows x64 正式安装版。用户希望在工作台内完成升级，同时保留模型配置、密钥、业务数据库、导入资料、产物和公司模板。自动检查仍只是检查；每次下载、安装和重启必须单独确认。

当前是未发布的源码实现。经用户授权，已在仓库外新建独立发布私钥并固定对应公钥；私钥以 Windows 当前用户 DPAPI 加密，文件和目录只允许当前用户及 SYSTEM 访问，没有明文私钥落盘，也不进入源码或安装包。尚未修改已安装应用、提交 Git 或发布新 Release。现有 0.20.3/0.20.4 不含此更新器，需要先手动安装以后正式发布的更新器版本。

公钥 SHA-256 指纹：`4a1ec45b941dfdf58a7551b91022149ad42c066362699854fc6e9e9b4157f57c`。当前 DPAPI 密文不是可迁移恢复备份；通常依赖原 Windows 账号和电脑，正式发布前须安排独立的加密备份。不能只复制密文到 Mac 就认为完成备份。[Windows DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)

支持更新准备、取消、大小/摘要/发布者签名验证、空闲检查、程序备份、停机替换、新版启动确认和旧程序恢复。不支持 Windows 源码/便携版就地升级、降级、跨数据格式迁移或无人确认安装。涉及数据格式变化时拒绝一键更新，另行制定升级与回退方案。macOS 使用独立的应用包与程序事务，见 [macOS 一键更新](macOS一键更新.md)。

## 用户流程

1. 设置 → 应用更新 → 检查更新。只有固定仓库 `WH2020/WorkFlow_Market` 的正式版本、完整 Windows 附件和可信公钥齐备时才能开始。
2. 确认“一键更新并重启”，先结束任务、停止生成并保存临时聊天内容。准备期间冻结新的业务操作；下载/暂存/验证阶段可请求取消，当前正在进行的安装器步骤结束后才响应取消。
3. 校验完成后，智能核心经标准输入 EOF 退出，后端受专用本机控制令牌关停。原生进程确认其托管的后台进程全部退出后才允许替换。
4. 备份受清单管理的程序文件，逐项替换，最后更新程序清单。先做不开放业务 API、不打开业务数据库的自检，再启动桌面和智能核心试运行。
5. 试运行确认之前不恢复任务、不调用模型，业务 API 和定时任务继续冻结。桌面与核心回执验证通过并短暂稳定运行后持久化提交，再解除冻结、按普通启动路径初始化业务服务。

## 数据和安装目录

安装根目录始终保持首次安装的路径，而不是复制配置和数据到新版本目录。这样保留现有 DPAPI 绑定、绝对路径引用及快捷方式目标。目录和快捷方式名称可能仍显示首次安装版本；应用内版本与卸载项 DisplayVersion 显示新版。

升级替换白名单不含 `.pi/`、`data/`、`inputs/`、`outputs/`、`library/templates/company/`，包括公开 `data/*.example.*` 文件也不就地替换。不覆盖未入程序清单的文件；同名新文件与现有未跟踪文件冲突时拒绝。正常应用启动产生的运行日志、缓存、派生模型配置不属于升级文件替换或数据迁移。

每个更新任务在 `.pi/app-updates/<随机任务ID>/` 保存控制文件、独立恢复 Python、旧版恢复辅助脚本、程序备份和日志。NSIS `/VERIFY=<任务ID>` 解包到同一用户目录下的全新测试暂存目录，不注册快捷方式或卸载项。原安装的保守卸载器可读取升级后的程序清单，仍不删除用户数据。

## 签名和下载边界

发布附件必须包括 `Agent4Market-<版本>-Setup-x64.exe`、`Windows-install-manifest.json`、`Windows-update-signature.json`。客户端只用固定 GitHub API 的附件 ID，不接收浏览器或 Release 文本提供的下载 URL。只允许一次 HTTPS 跳转到 `release-assets.githubusercontent.com`，不携带模型密钥、CLI 登录或 GitHub Token。

大小与 SHA-256 用来校验下载一致性；另用客户端内置的 Ed25519 公钥认证发布者。签名绑定应用、平台、版本，以及安装器和清单各自的大小/摘要，验签在安装器执行前完成。即使 GitHub Release 与同源摘要一起被修改，没有独立私钥也不能生成有效更新签名。实现采用已在 Windows 解码依赖中打包的 PyCryptodome 3.23.0，不手写密码算法。[PyCryptodome EdDSA](https://pycryptodome.readthedocs.io/en/latest/src/signature/eddsa.html)

维护者必须先确定发布私钥的保管与离线备份方式，将对应 32 字节公钥的 Base64 固化到 `agent_platform/windows_update_trust.json`。私钥不得放进源码、发布包或 GitHub Release；不要与 Release 上传凭据共用保管位置。公钥不可用时禁用一键安装，不降级为仅 SHA 校验。

此 Ed25519 签名认证的是应用更新附件，不等于 Windows Authenticode 或 Apple Developer ID 签名，也不会自动消除首次手动安装的系统安全提示。

已有私钥的离线签名命令如下；签名脚本不会创建密钥、上传或发布，输出文件必须不存在。必须使用已验证且在隔离模式下包含 PyCryptodome 的解释器，不能依赖 Windows Store Python 的用户 site-packages：

```powershell
python -I -B scripts/sign-windows-update.py --private-key <受保护的DPAPI私钥路径> --installer <安装器路径> --manifest <清单路径> --version <版本> --output <Windows-update-signature.json路径>
```

如果使用口令加密 PEM，另加 `--ask-passphrase`，只在交互提示中输入口令，不放入命令行。需要另外创建新的密钥时，使用 `scripts/release_signing_key.py --create --directory <全新仓库外目录>`；该工具拒绝现有目录，不修改客户端公钥，不能在常规发布时重复生成密钥。

常规换钥可由旧私钥签署包含新公钥的过渡版本；下一次更新再使用新私钥。旧私钥丢失或泄露时不自动换钥，走显式手动安装和安全通知流程。

## 关停、事务和恢复

后端与智能核心以 `CREATE_SUSPENDED` 创建，在执行第一条指令前加入禁止 breakaway 的 Windows Job Object，再恢复线程。Job 设置 `KILL_ON_JOB_CLOSE`；即使中间父进程短命退出，后代仍由内核托管。更新停机回执要求所有托管组 ActiveProcesses 为 0；不以端口空闲或某一批 PID 快照代替这个条件。窗口关闭或原生异常退出时，后台进程组一并结束。[Windows Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)

工作进程运行于更新任务内独立的嵌入式 Python，恢复不依赖正在替换的旧运行时或 NSIS 暂存目录。复制文件先 fsync，控制文件与程序替换使用同盘 write-through rename；删除旧程序路径改为移入本任务的 retired 目录。程序清单最后替换，展开的事务日志在备份前限长。[MoveFileExW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw)

正常异常或再次启动时按持久化阶段恢复：提交前还原旧程序；已提交则核验新版后完成结果回执。校验既检查目标清单中的文件，也检查差异计划中应消失的路径确实不存在。回滚预检完全部已知旧/新内容及备份后才写文件，可重复执行。

备份损坏、未知程序变化、路径重解析点/硬链接、权限不安全或无法确认进程退出时停止，不覆盖不明内容。不要删除 active 或恢复材料来绕过检查。更新失败提示出现后先关闭工作台，再重新启动尝试恢复；仍失败时交由维护者按任务日志核验，不自行覆盖安装目录。

## 限制与发布前验收

- 首版保留安装器、暂存程序、独立恢复运行时、备份及退休文件，不自动清理；重复更新或取消会累计占用空间。准备时预算下载、暂存、恢复、备份、退休文件和单文件替换空间；空间不足拒绝开始。后续清理必须单独核验精确任务范围，不能删除整个 `.pi` 或按通配符删除安装目录。
- 已在 Windows 合成目录测试程序更新/回滚、部分替换中断、重复恢复、未知文件/硬链接拒绝、备份改动拒绝、签名伪造、下载边界、确认取消、并发门禁、提交回调异常和核心启动暂停；未读取当前安装、真实 CLI 登录或调用模型。
- Python 定向回归 51 项通过（使用已核验公开载荷的嵌入式 Python 及签名依赖；含私钥加密/签名 CLI、长文件名临时复制和 6 项 macOS 分发静态检查）；Rust 5 项通过，含短命父进程留下长命后代的 Job Object 验证；UI/任务 Node 回归与类型检查通过。独立恢复 Python 的 34 个运行文件在新私有目录复制后可启动更新辅助脚本。
- 内置浏览器连接不可用，使用项目现有离线 Edge 脚本完成 6 组场景，覆盖更新确认/取消、进度、更新源异常与窄窗口，外网请求被阻断。此验收促使配置/数据保留、重启和磁盘成本提示改为确认框直接可见。
- 已生成含新公钥的完整未发布候选包，并用独立私钥签名。实际 NSIS `/VERIFY` 安装到全新 UUID 目录后，28,068 个程序文件、原始清单摘要、安装后公钥及嵌入运行时全部校验通过；重复安装被拒绝，注册项未改变，安装包不包含私钥或签名维护工具。此次没有启动应用或模型服务。
- 2026-09-12 用户关闭原工作台后，已用全新合成安装完成完整原生验收：合成旧版 `0.0.0` → 已签名候选 `0.20.4`，真实 NSIS 暂存、验签、桌面关停、程序/EXE 替换、无业务自检、桌面/Pi 试运行回执和提交全部通过，合成配置及数据哨兵不变。仅在合成旧服务中注入固定本地附件来替代网络传输，生产安装器、签名和原生更新链路未替换；此结果**不包含实际 GitHub HTTPS 下载验收**。
- 已进一步注入“程序旧/新混合、事务中断、原暂存目录缺失”，由真实桌面启动入口调用独立恢复运行时，恢复旧程序并成功启动；重复校验程序清单及数据哨兵通过。测试进程均由已验证归属的句柄关闭，没有终止用户进程。
- 上述验收绑定冻结的 Windows 候选。后续 Mac 接入改动涉及共享 UI/服务/原生入口，发布时必须从最终审阅源码重新构建并核验 Windows 包，不能把旧候选当作最终交付。未做真实断电、硬件/文件系统损坏、多轮更新空间增长或跨机器验收。
- 发布前还须完成私钥独立加密备份、最终双平台包验收与真实更新源验证。当前未发布、未更改已有 Release，也未给用户实际安装打补丁。

精简回归入口：

```powershell
python -B -m unittest tests.test_windows_updates tests.test_app_updates tests.test_windows_installer
node --test tests/ui-app-updates.test.mjs tests/vertical-workflow-selection.test.ts
node node_modules/typescript/bin/tsc --noEmit
cargo test --offline --manifest-path desktop/src-tauri/Cargo.toml
node tests/ui-app-updates-browser.mjs
```

浏览器脚本可用 `A4M_BROWSER_PLAYWRIGHT` / `A4M_BROWSER_PYTHON` 指定已有工具，不需要下载新浏览器。测试中的版本与签名密钥均为合成值，不是实际 Release 或发布密钥。
