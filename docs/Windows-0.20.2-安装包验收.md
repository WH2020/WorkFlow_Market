# Windows 0.20.2 安装包验收

日期：2026-09-10。目标：生成包含可选 Claude Code / Codex CLI 后端的 Windows x64 安装程序；不更新、安装覆盖或迁移用户正在使用的 0.20.1。

本文记录当天首次本地交付包的历史验收。随后公开的 `v0.20.2` 安装包另包含应用更新入口与 Codex 模型目录修复，SHA-256 为 `93908d4800237d4da230d0db4465e5aee87acab882a80b7438a0d4edd3504023`，与下文旧包不同。公开包的最终文件清单、逐项验收结果及限制以 [正式 Release](https://github.com/WH2020/WorkFlow_Market/releases/tag/v0.20.2) 附件和说明为准；不能将下文旧包原生窗口验收误认为公开包已重跑该项测试。

## 产物

- 安装器：`Agent4Market-0.20.2-Setup-x64.exe`，173,450,121 字节。
- SHA-256：`5519ac842b84ecfa272dca3b97552a0c0511f50962c701098c9b10abcbd54f05`。
- 包内桌面 EXE：0.20.2，11,222,528 字节。安装器与桌面 EXE 均未代码签名。
- 发布清单：28,055 个文件，总计 801,489,895 字节；每项记录相对路径、大小和 SHA-256。
- 内置运行时：Python 3.11.9 x64、Node.js 24.19.0、pnpm 11.22.0、Pi 0.84.2。

## 安装与数据边界

NSIS 3.11 使用普通用户权限，固定安装到当前 Windows 用户目录下 `Agent4Market-0.20.2`。现有目标即使为空也拒绝覆盖；不扫描或迁移 0.20.1，不自动启动应用。

安装前检查 Git、ripgrep、fd 和 WebView2。目录采用原子创建，创建时即具备当前用户与 SYSTEM 的受保护完整权限，并检查祖先目录。解包后完整校验文件哈希，初始化六个空白业务文件后才注册快捷方式及卸载信息。快捷方式、卸载注册项均带版本号。

卸载仅删除清单中哈希未变化的程序文件；保留数据、`.pi`、输出、公司模板、改动或锁定文件，以及私有运行标记、安装清单和版本根目录。没有递归删除安装根目录、终止用户应用或修改旧版权限的操作。保留的同版本目录会阻止重装，这是本版明确的行为。

## 纯净性与依赖

原脚本宽泛收录未跟踪文件的问题已修正。输入为 Git 跟踪文件及逐文件审阅的 WX/CLI 新增文件白名单。实际检查发现的源码内 Windows 缓存文件未读取内容、未装包、未删除。

发布包检查结果：0 个重解析点、禁止目录、数据库、开发缓存及 `.pyc`；Python site-packages 中 0 个 `.pth` 和 `.egg-link`。`data/` 只有 9 个公开 `.example.csv/.example.json`，包括空表、空目录或明确示例，不含开发机微信、真实客户资料或账号配置。

Python 采用 [官方嵌入包](https://docs.python.org/3.11/using/windows.html#the-embeddable-package)，按应用依赖方式交付，不运行用户机 pip。固定 `_pth` 仅包含包内标准库、DLL目录、site-packages 和应用根；`isolated`、`no_site`、`no_user_site` 均为 1，禁用用户路径和启动扩展。真实导入 `sqlite3`、`ctypes`、Crypto、zstandard、PIL、pptx、yaml、faster_whisper 等模块成功。

Python ZIP 来自 [Python 3.11.9 官方发布](https://www.python.org/downloads/release/python-3119/)，MD5 与官网发布值一致；SHA-256 为 `009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b`。应用依赖的最终版本和逐文件哈希随发布清单保留；Python requirements 仍有范围依赖，未来重新构建可能解析到不同版本，尚不是 wheel 哈希锁定的完全可复现构建。

## 实际执行的检查

| 检查 | 结果 |
| --- | --- |
| Rust release `cargo build --release --locked` | 通过，EXE 元数据 0.20.2 |
| Python 包装/安装、CLI host/provider、环境、销售及投标存储相关回归 | 76 passed，31 subtests passed |
| TypeScript `tsc --noEmit` | 通过 |
| CLI 模型适配器、真实 SDK→包内 Python host→合成 CLI、两组 UI 回归 | 39/39 通过 |
| 原工作流、审批、存储、模型边界和传输等 `test:runtime` | 119/119 通过 |
| 发布清单及关键 CLI 源文件与源码一致性、独立只读包装审阅 | 通过 |
| `git diff --check` | 通过 |

实际安装验收使用 `/S /VERIFY=680d409216db46728e2d7f846bd88532`，只操作新建的 UUID 专用测试目录；不创建常规快捷方式或卸载注册项，不使用真实聊天和账号。安装、安装后全文件哈希与 ACL、包内 Python 隔离、重复安装拒绝、桌面 EXE `--self-test`、原生 WebView 窗口、窗口正常关闭与服务回收、保守卸载及数据保留全部通过。安装时无配置模型、任务 0、微信消息 0；卸载退出码 0，测试数据和修改过的 `ui/app.js` 保留，EXE 和包内解释器已移除。

首轮窗口退出检查向所有自有 HWND（包括隐藏消息窗口）发送关闭消息，出现退出超时；调整验收脚本只关闭标题匹配的可见应用主窗口、等待主窗口就绪后验证通过。没有据此认定或修改产品窗口缺陷。最终记录见同批次 `acceptance/installer-acceptance.json`（82.89 秒为恢复验收阶段用时，不是首次完整安装耗时）。

验收副本位于 `<USERPROFILE>\Agent4Market-0.20.2-test-680d409216db46728e2d7f846bd88532`，仅保留合成测试数据、修改标记、日志及 WebView 缓存。未对该根目录做递归清理。纯净构建源位于同一用户目录下 `Agent4Market-0.20.2-payload-20260910`，与交付安装器及旧 0.20.1 独立。

## 本机 CLI 权限限制

本次最初 CLI SDK 测试受到本机既有暂存目录状态影响。只读证据确认：LocalAppData 的祖先权限不满足安全策略；回退 `<USERPROFILE>\Agent4MarketCli` 目录虽由当前用户拥有且 DACL 受保护，但含 3 条 ACE，而策略要求仅当前用户和 SYSTEM 两条。因此运行时正确地拒绝启动 CLI。根因是当前目录权限不合规，不是包内 Python、环境变量大小写或缺少源码；无法确认权限何时、由谁发生变化。

没有修改或修复该既有目录的权限，也没有读取目录内原文。测试已改为使用隔离的合成 HOME/暂存目录，包内 Python 下 CLI 工具闭环、取消、错误遮蔽和接收方门禁均通过。安装新版本本身不会解决本机既有 CLI 暂存目录问题；使用 CLI 模式前仍需用户单独决定如何处理。API 模式不经过该 CLI 暂存目录。

## 未覆盖范围

未实际登录或调用用户 Claude/Codex 云端模型、未验证订阅与额度、未读取真实微信数据。CLI 首版文字任务及已验证版本限制保持不变；微信授权范围继续只允许 API 后端。没有在另一台全新 Windows 上验证，也未执行常规安装模式的桌面快捷方式/注册表写入。外部 Git/rg/fd/WebView2/LibreOffice 和代码签名仍是部署注意事项。
