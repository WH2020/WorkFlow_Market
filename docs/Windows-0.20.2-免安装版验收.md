# Windows 0.20.2 免安装版验收

日期：2026-09-10。需求：生成本机可以双击使用的完整免安装目录，不修改旧版或现有业务数据。

## 交付结果

- 目录：`<USERPROFILE>\Agent4Market-0.20.2-portable`。
- 入口：上述目录内的 `Agent4Market.exe`，无需运行 Setup。
- 桌面程序：0.20.2，11,222,528 字节，SHA-256 为 `e65d8292648fd2b4d44c0c9687ca9c4ad6ffd4b761ff9459b9b52676eb3b87ab`。
- 28,055 个原程序文件、801,489,895 字节，与此前验收的 0.20.2 安装包使用同一批代码及内置依赖。
- 发布清单 SHA-256：`7c945f7aa030f368afdee08a33e02a6fff364469998c59474648919cf88e9191`，随目录保留为 `runtime/install-manifest.json`。
- 构建记录：`runtime/portable-build.json`；用户说明：`免安装版使用说明.md`。

交付后仍须保留整个文件夹，不能单独移动 EXE。本轮不生成 ZIP，也不承诺解压到任意目录、共享盘或其他电脑后仍具备正确的私有 Windows 权限。

## 修改和安全边界

新增免安装构建工具 `scripts/build-windows-portable.py`、打包测试 `tests/test_windows_portable.py`、实际启动验收 `tests/windows-portable-acceptance.py` 和说明文件。没有修改应用业务代码、重新解析依赖、覆盖安装器或更新旧版本。

构建仅接受已校验的版本专用纯净载荷及固定 SHA-256 清单。先校验全部输入，再原子建立仅当前用户与 SYSTEM 可访问的新目录；现有目录即使为空也拒绝覆盖。复制范围严格限于清单，不递归复制源目录中的未登记文件、运行态或真实数据。

六个业务文件从公开空白示例初始化。所有程序文件、初始化文件和权限检查通过后，才将经过哈希校验的临时 EXE 原子发布为正式入口。未写注册表、快捷方式或系统 PATH；没有读取或迁移旧版数据、微信内容或 CLI 登录文件，也没有修改已有目录的权限。

启动验收会生成本地索引、日志、WebView 缓存，以及 `.pi/portable-verification-home` 合成账号目录。这些是本次新目录的首次运行状态，留在 `.pi` 等应用运行目录中，不是用户旧配置或开发机历史资料。

## 实际执行及结果

在项目目录执行：

```powershell
python -B -m pytest tests/test_windows_portable.py tests/test_windows_installer.py tests/test_windows_package.py plugin/market-director-copilot/tests/test_init_local_data.py -q
```

结果：23 passed，37 subtests passed。覆盖固定目标、拒绝现有目录/旧版输入、钉住清单、防路径穿越与大小写冲突、必需依赖、字节校验、不覆盖复制、Windows 私有目录原子创建及空白数据初始化。

构建和实际验收均由载荷内的 Python 3.11.9 执行，使用 `-I -B`；主要参数分别为：

```text
scripts/build-windows-portable.py --payload <USERPROFILE>/Agent4Market-0.20.2-payload-20260910 --manifest <PROJECT_ROOT>/outputs/releases/0.20.2/install-manifest.json
tests/windows-portable-acceptance.py --output <PROJECT_ROOT>/outputs/verification/windows-portable-20260910
```

验收事实：

- 全部 28,055 个程序文件在首次启动前后均通过哈希检查；EXE 与安装包同版同哈希。
- 包内 Python 路径隔离、原生依赖导入和根目录私有 ACL 通过。
- 桌面 `--self-test` 退出码为 0。
- 不带测试参数的普通启动通过：原生主窗口可见，AI 核心为 `idle`，有心跳，模型未配置、任务 0、微信消息 0，本地 HTTP 身份保护入口可用。
- 从中立工作目录启动，PATH 不依赖源码或 Codex 运行时；子进程 HOME/USERPROFILE/APPDATA/LOCALAPPDATA/TEMP/TMP 指向合成目录，没有提供真实凭据或提交云端任务。
- 仅关闭本次启动的可见主窗口，进程正常退出，8765 端口释放；没有强制结束旧程序。
- 实际再次构建同名目录被拒绝；六个初始业务文件哈希未变，卸载登记状态未变。

完整结果见 `outputs/verification/windows-portable-20260910/portable-acceptance.json`。其中 58.19 秒为包内运行时检查至最终复核的阶段用时，不包含首次完整清单校验或构建复制时间。新构建/验收脚本语法检查及 `git diff --check` 通过。独立只读复核确认发布顺序、最终 ACL、EXE/marker/清单/构建记录一致。

## 保留的限制

程序和 Python/Node 依赖已包含；系统仍需 Git、rg、fd 和 WebView2，生成演示文稿另需 LibreOffice。首次使用需配置模型。其他版本须先关闭，工作台共用本地端口 8765。

本机既有 CLI 暂存目录的权限仍不符合安全策略，CLI 模式会被拒绝；本轮未修复或放宽权限。真正使用外部 CLI 时会按设计使用用户自行管理的 CLI 登录，不能将本次合成 HOME 验收理解为外部 CLI 账号沙箱或登录验证。API 路径不经过该 CLI 暂存目录。

EXE 未代码签名。没有验证真实模型账号、订阅额度或云端任务，没有新增逐页面浏览器交互/截图验收，也未覆盖跨电脑、移动后权限、非 NTFS、低磁盘空间或杀毒软件差异。交付时应用已关闭，旧版及其数据未改动。
