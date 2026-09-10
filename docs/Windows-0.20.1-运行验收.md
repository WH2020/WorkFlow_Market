# Agent4Market 0.20.1 Windows 本机运行版

2026-09-09 已生成并完成本机原生桌面验收。此交付是当前用户的私有运行目录，不是可单独移动的单文件 EXE，也不是已签名安装包。

## 启动位置

- 运行目录：`C:\Users\<Windows用户名>\Agent4Market-0.20.1`
- 程序：`C:\Users\<Windows用户名>\Agent4Market-0.20.1\Agent4Market.exe`
- 版本：`0.20.1`
- EXE 大小：`11,215,360` 字节
- EXE SHA-256：`5200ebb09e324d4147bbee1e4345ec3da02ace9aff1448c95b8665a3b7ae2761`
- Authenticode 状态：`NotSigned`

先退出旧版本，再双击上述 EXE 即可启动。必须保留整个目录；首次使用模型功能需要在工作台设置中配置模型提供者。手动解密、WAL 重放、会话预览/导出和媒体副本恢复不需要调用模型。

原 2026-09-08 的 `outputs/verification/windows-native-20260908-4f812dd7f5ce458e86f31f0ce5b5631e/release/Agent4Market.exe` 未覆盖，仍为 0.20.0。源工程现有业务资料及配置没有复制到新目录；原目录权限未修改。

## 运行边界及依赖

Windows 启动器只使用 EXE 所在的完整运行目录，不搜索启动时的工作目录或祖先目录。私有标记模式使用包内 Python、Node、Pi、独立 `.pi/agent` 配置及 `.pi/webview2` 浏览器缓存；关键包内依赖缺失或越出运行目录时拒绝启动。

安装根的受保护 DACL 只授权当前 Windows 用户与 SYSTEM。WebView2 缓存目录继承该私有权限。本机 8765 端口被占用时拒绝接管服务；本次后端启动使用随机实例标识核对。此标识不是 API 鉴权令牌，微信 API 仍依赖 Windows 同用户、同登录会话的客户端进程校验。

随目录提供的运行依赖：

| 依赖 | 已验证版本 |
| --- | --- |
| Python 虚拟环境 | 3.11.9 |
| Node.js | 24.19.0 |
| pnpm（包含其 dist 目录） | 11.22.0 |
| Pi CLI / pi-subagents | 0.84.2 / 0.51.0 |
| PyCryptodome / zstandard / Pillow | 3.23.0 / 0.25.0 / 12.3.0 |
| python-pptx / PyYAML / faster-whisper | 1.0.2 / 6.0.3 / 1.2.1 |

仍依赖本机独立安装的 Python 3.11 基础解释器、Windows PowerShell、Git 2.53.0、ripgrep 15.2.0、fd 10.4.2、WebView2，以及 PPT 渲染使用的 LibreOffice。Python 基础解释器来自本机 Windows Store 安装，不能移除后仍期待此虚拟环境独立运行。LibreOffice 检测位置为 `C:\Program Files\LibreOffice\program\soffice.com`。

Node 依赖按锁文件在目标目录重新安装，禁用依赖安装脚本并使用实际复制，未复用开发目录中写死绝对路径的 Pi shim。独立静态扫描未发现 reparse point；关键源代码、模板和资源与当前源工程一致。EXE 中保留一处构建时的源码目录字符串，仅为编译来源信息，不是运行根定位或执行依赖。

## 实际验证

| 验证 | 结果 |
| --- | --- |
| `build-windows-desktop.ps1 -OutputPath <最终EXE>` | release 构建成功，直接运行最终输出位置的 `--self-test`，退出码 0 |
| 最终 EXE 普通启动 | 实际 WebView2 窗口成功创建，AI 核心 `idle` 且有心跳；模型未配置、任务数 0 |
| 最终 EXE 微信 UI 重复验收 | 错误密钥拒绝；已有合成文件去重；会话预览、3 条 JSONL 导出、WAL 重放、媒体恢复与下载通过 |
| 首次原生 UI 数据导入 | 从 0 条开始，主库导入 3 条、含 WAL 副本再导入 5 条 |
| 隔离检查 | 在 `C:\Windows` 工作目录启动；PATH 去除源工程和 Codex，环境白名单不继承模型凭据，仍正常运行 |
| 请求边界 | 微信 UI 验收中模型/任务写入请求 0，微信进程列表请求 0 |
| 原生异常启动 | 单独复制 EXE、缺失私有 Python、已有非工作台监听者三个场景均拒绝，退出码 2 |
| Python 全量 | `400 passed, 8 skipped, 128 subtests passed`；8 项为平台不适用跳过，独立 SQLCipher 合成向量已启用 |
| 最后定向 Python 回归 | `81 passed, 2 subtests passed` |
| Rust 单元测试 | `3 passed` |
| 包内诊断 | `agent_platform doctor` 为 `ok`，core/PPT 均 ready；`pip check` 无冲突，全部主要 Python 包可导入 |
| 资源闭包 | 六组运行资源及 UI、锁文件、依赖声明、启动脚本与源文件哈希一致，关键依赖在位 |
| 退出检查 | 验收程序及其后端已退出，8765 与临时调试端口 19265 未留下监听 |

全量回归命令（在源工程执行）：

```powershell
$env:WXDECIPHER_SQLCIPHER4_FIXTURE = '<仓库根目录>/outputs/verification/wxdecipher-5b29d55d24684a2abab60414ab0a3a13/sqlcipher-4.0-testkey.db'
python -m pytest tests ui/test_server.py plugin/market-director-copilot/tests -q
cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked --offline
```

原生验收使用 `tests/windows-native-browser-acceptance.mjs` 连接被测 EXE 自身的 WebView2，没有替换后端。调试仅对本次测试进程设置浏览器参数，不修改注册表；机制参照 [Microsoft WebView2 调试说明](https://learn.microsoft.com/en-us/microsoft-edge/webview2/how-to/debug-visual-studio-code)。Playwright 仅为外部验收工具，不是程序运行依赖。

最终 EXE 对应的微信 UI 验收结果和截图：

- `outputs/verification/native-desktop/result.json`（含被测 EXE SHA-256）
- `outputs/verification/native-desktop/webview2-wechat.png`
- `outputs/verification/native-normal-start/result.json`
- `outputs/verification/native-normal-start/webview2-home.png`
- `outputs/verification/detached-runtime-guard/result.json`

## 已知限制及保留内容

此目录保留了 8 条合成验收消息、合成输入、截图、下载结果、测试浏览器缓存及日志。自动清理 `data/wechat` 的操作被执行环境拦截，已停止，未重试绕过，因此不能称为无验收残留的空包。没有导入真实微信、真实业务记录或模型密钥。

如果需要手动清除这些合成导入记录，应先关闭此程序，再仅处理此运行目录下的 `data/wechat`；不要清除运行根、原工程数据或其他应用目录。合成输入保留在 `outputs/verification/native-inputs`，可以重新生成相同测试记录。现有导入保留策略为 7 天，到期后的下一次相关访问执行清理。

本轮未对真实微信进程取钥，也未读取真实账号数据库；自动取钥在用户当前微信版本上的兼容性仍需另行、明确授权的实测。本轮未提交模型任务、验证真实模型凭据或执行完整销售业务工作流。其他电脑、其他 Windows 用户和 macOS 不属于这次 EXE 验收范围。

## 本次修改范围

修改桌面启动器及 Windows 构建/启动脚本，增加私有打包脚本、运行标记、启动拒绝测试及原生 WebView2 验收脚本。后端增加启动实例标识，并让禁用调度器同时覆盖后台线程与页面初始化入口。应用版本及销售/招投标应用版本元数据同步为 0.20.1；数据库 schema 版本和迁移 SQL 未变。

未提交或推送代码，未发布安装包，未更改旧 EXE。
