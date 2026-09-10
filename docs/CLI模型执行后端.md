# CLI 模型执行后端

工作台可使用本机 Claude Code 或 Codex CLI 执行普通任务的模型推理，无需在工作台配置该 CLI 的 API Key。原有 API 供应商仍可同时保留，并可继续用于指定任务或角色。

这不同于已有的“编码助手对话入口”：后者只通过本机桥接创建、查看和调整任务；本功能让 CLI 真正参与 Pi 会话中的模型推理和工具选择，但不建立第二套业务执行引擎。

## 使用

1. 自行安装并在本机终端登录所选 CLI，确认所需模型对该账号可用。工作台不代为登录，不读、复制或展示登录文件。
2. 打开“设置 → 模型提供者”，选择 Claude Code 或 Codex CLI。检测只运行版本与帮助命令，不调用模型；“已安装”不表示登录有效。
3. Codex 会自动读取 CLI 模型目录，并按所选模型列出思考强度；选择后点击“保存并设为默认”。也可主动刷新目录。目录来自隔离的 `model/list` 查询，不读取真实登录或工作区，不验证账号权限或额度。Claude 仍手动填写完整模型 ID；两者程序路径都只能来自检测结果。
4. 关闭并重新打开应用，让智能核心载入新配置，再创建普通任务。任务的思考选项与模型联动；“默认”使用该 Codex 模型保存的强度。可在任务模型和角色模型中选择已配置的 CLI；切换默认值不会替换旧任务已冻结的模型或思考强度。

目录读取失败不会覆盖已有配置。历史手填 Codex 模型保留原样；需从新目录选择并保存，才启用思考强度。旧配置不会被自动替换成名称相近的模型。CLI 若返回当前 Pi `0.84.2` 尚不支持的档位（例如 `ultra`），界面会显示但禁用，不映射为其他档位。

保存 CLI 不会删除 API 实例或 API Key。移除某个 CLI 只移除该实例；原任务不会自动改用别的供应商。程序升级、启动路径或文件内容变化后，旧实例拒绝执行，需重新检测并登记新实例。

## 当前支持范围

| 项目 | 首版行为 |
| --- | --- |
| 已验证版本 | Claude Code `2.1.245`；Codex CLI `0.149.1`。其他版本先拒绝，待更新适配器并验收。 |
| 认证 | CLI 自行管理的本机登录；Codex 固定 ChatGPT 登录方式。工作台不验证账号权限或剩余额度。 |
| 服务 | Claude 官方服务、OpenAI 官方 Codex 服务；不继承工作台 API Key、代理、第三方端点或 CLI 定制目录。 |
| 输入与输出 | 文字；每轮最多一个工作台工具调用。暂不开放图片或 CLI 会话复用。 |
| 模型与思考 | Codex `model/list` 自动发现；只开放 CLI 与当前 Pi 都支持的档位。保存、任务启动和传输均校验；精确传入 `model_reasoning_effort`，不允许静默裁剪。Claude 及历史手填配置保持原有行为。 |
| 执行边界 | Pi 独占任务状态、DAG、业务工具、产物登记和审批。CLI 原生 Shell、读写文件、浏览器、插件、MCP、自主子代理不可用。 |
| 微信 | 微信会话整理及其授权范围不支持 CLI 后端；请为这类任务选择原有 API 模型。CLI 账号身份/组织边界尚不能随会话授权可靠冻结。 |
| 超时与取消 | 单次模型请求 180 秒；每个 Pi 进程内，每个 CLI 实例最多两个在途请求。Pi 中止请求时终止本次进程树，丢弃迟到结果；沿用工作台既有任务取消状态规则。 |
| 输出和费用 | 请求上限 1 MiB，输出上限 2 MiB，文本或工具参数各不超过 128 KiB。能取得时记录 token 用量；适配器中的零费用字段是未计价占位，不代表服务免费。 |

登录过期、模型无权限、CLI 版本不兼容、输出格式错误或工具参数不合法时，本次请求失败；不会改用其他模型或 API，也不会根据错误输出执行工具。用户可在 CLI 终端自行排查登录和模型访问，再返回工作台重试。

## 实现边界

`模型注册表 → Pi 自定义 provider → 隔离进程宿主 → CLI 单轮结构化输出 → Pi 工具校验/审批/执行 → 下一轮`

- 配置采用 v3 多供应商注册表；旧 v2 CLI 配置须用户重新检测、确认模型并保存，不自动执行。
- 每个实例绑定官方端点、协议、模型、程序路径、版本和启动文件 SHA-256。运行时清单也有摘要校验；登记/移除不改变历史接收方绑定。程序哈希按 1 MiB 分块异步读取，校验期间也可中止请求。操作系统、当前用户及安装目录须可信；摘要检查不能防御恶意本机程序在校验后替换可执行文件。
- 提示词和工作台工具定义通过 stdin 传递，不写入命令行或请求文件。只在新建私有运行目录写无任务内容的 schema 和 Codex 模型能力目录；正常结束即清理，异常遗留的本工具目录在后续启动时按 24 小时阈值清理。已有不安全目录只拒绝，不改权限。
- 模型目录查询使用空临时 HOME/CODEX_HOME 和阻断联网的代理，仅执行初始化与分页 `model/list`，不创建模型任务。仅保留模型 ID、名称、默认与支持的思考档位；目录缓存限于进程内 15 分钟并绑定程序摘要，不保存 CLI 原始响应。内置目录可见不代表该账号能调用。
- 主任务和已指定的 CLI 只读角色在创建任务时固定思考强度；子任务启动参数、不可变合同和结果回执相互校验。旧任务若缺少必要的 CLI 思考记录，会拒绝执行并提示重新开始，不从当前默认值猜测。
- Windows 使用 `STARTUPINFOEX` 的 Job List，在创建挂起进程时就原子加入关闭即杀进程树的 Job，再核验成员关系并恢复运行；包括极早取消在内，不存在创建后再附加 Job 的孤立进程窗口。POSIX 使用调用者持有的独立进程组。未在 macOS 做原生实机验收。
- Claude 使用安全模式、空原生工具集合和空 MCP 配置。Codex 使用严格配置、忽略定制、禁用原生功能，以及每次生成的模型能力目录。仅禁用 feature 不足以关闭已知 Codex 模型的 `apply_patch`；能力目录保留原始模型 ID，但移除客户端原生工具能力。
- 仅接收一个合法 JSON 结果，将工具请求转换成 Pi 工具事件并再次校验名称与参数；对象默认拒绝未声明字段，显式开放的字典字段按其 schema 校验。原生工具事件、残缺输出、多结果、超限内容均拒绝。错误信息不回显 CLI 原始 stderr 或私有上下文。

## 开发验证

默认测试只使用合成 CLI、合成数据和本地服务，不使用真实登录或云端模型：

```powershell
python -m pytest tests/test_cli_model_catalog.py tests/test_cli_provider.py tests/test_cli_process_host.py tests/test_model_provider.py tests/test_model_registry.py tests/test_environment.py ui/test_server.py tests/test_wechat_store.py -q
runtime/node/node.exe --test pi/tests/cli-model-provider.test.ts pi/tests/cli-provider-e2e.test.mjs tests/ui-model-settings.test.mjs
runtime/node/node.exe node_modules/typescript/bin/tsc --noEmit
```

`pi/tests/cli-native-offline.test.mjs` 是显式启用的原生 CLI 离线验收。先设置 `AGENT4MARKET_TEST_CLAUDE_PATH` / `AGENT4MARKET_TEST_CODEX_PATH` 为待验证的实际原生程序绝对路径，再运行 `node --test pi/tests/cli-native-offline.test.mjs`。它为每次测试建立空登录目录和回环服务：检查已知/未知模型 ID 不变、无可执行原生工具、不加载隐式项目资料、成功解析结构化结果；未设置路径时跳过。不应将其改为指向生产服务或真实认证。

进程闭环测试可设置 `AGENT4MARKET_TEST_PYTHON` 为普通原生 Python 或发行版私有 Python 的绝对路径；本机 Microsoft Store Python 入口在 Node 子进程启动中曾返回 `EPERM`，不要因此放宽环境隔离。浏览器回归脚本为 `tests/ui-cli-catalog-browser.mjs`，只连接合成配置服务。

程序检测、单元/HTTP/进程闭环验收不能证明用户账号已登录、拥有某模型权限或还有额度；正式账号验证需用户自行发起。当前源代码变更不会自动覆盖已安装应用，发布前仍需重新打包或应用经过校验的程序文件热修复。

### 2026-09-09 验收记录

环境：Windows x64、Node `24.19.0`、Python `3.11`；原生 CLI 为上表固定版本。全部业务输入与上游服务均为合成数据/回环服务。

| 验收组 | 结果 |
| --- | --- |
| Python 模型配置、进程宿主、启动环境、HTTP 路由、微信授权回归 | 135 passed，30 subtests passed |
| CLI 单元/真实 SDK 进程闭环、模型设置与微信界面逻辑 | 39/39 |
| 既有任务状态机、审批、API 传输、资料/销售/招投标适配器等运行时回归 | 119/119 |
| 原生 Claude、Codex 已知模型与自定义模型 ID 离线验收 | 3/3 |
| TypeScript `--noEmit`、JS 语法、`git diff --check` | 通过；Git 仅提示既有 CRLF 换行转换 |
| `python -m agent_platform validate` | 通过：14 插件、26 工作流、33 服务 |

另外通过隔离的真实 HTTP 服务保存两种 CLI，刷新后确认三供应商共存、默认值生效、API 实例保留。Windows 宿主专项测试确认：进程创建返回时已在 Job 中且仍挂起，只关闭 Job 即可终止；杀死宿主也会回收孙进程。

完整回归发现并修复两处旧 TypeScript 存储版本常量与现有 `0.20.1` 迁移清单不一致的问题；只对齐应用版本常量，没有修改 SQL、数据库结构或用户数据。

未覆盖：真实账号登录/模型权限/额度和云端调用；macOS 原生运行；浏览器视觉验收（Browser 连接失败，已用界面逻辑与 HTTP 测试覆盖相关行为）；新安装包构建。若测试中断，测试工具的合成临时目录可能残留，不包含真实账号或业务内容。

### 2026-09-10 模型目录与思考强度热修复验收

本次新增验证替代上节在当日仍未覆盖的浏览器交互项；上节计数保留为历史记录。

- Python 目录解析、分页/超时、注册表、宿主、HTTP、环境与微信授权回归：147 passed，49 subtests passed。
- Node 任务/子任务恢复、API 与 CLI SDK 进程闭环、模型设置及已有运行时回归：159/159。
- 本机 Edge + Playwright：模型自动加载、档位联动、ultra 禁用、准确保存、任务默认值、刷新持久化和失败保留，共 7 项；未产生 JavaScript 错误。应用内 Browser 连接不兼容，改用独立无头浏览器，全部连接合成服务。
- Codex 0.149.1：空 HOME 下 `model/list` 查询成功；已知与自定义模型的离线回环测试 2/2，确认已知模型实际收到 `high` 且原生工具仍关闭。未运行真实账号或云端任务。
- TypeScript `--noEmit`、JS 语法和插件/工作流校验通过。

运行时思考强度与任务记录不符、Pi 意外裁剪，以及缺少必要思考记录的旧 CLI 角色任务均在发送前拒绝。SDK 进程闭环使用发行版私有 Python；本机 Store Python 的启动 `EPERM` 不应被误报为协议失败。

仍未覆盖真实登录/权限/额度、macOS 原生验收，以及单条父工作流 → 真实 pi-subagents 前台子进程 → 合成 CLI 的完整贯通测试；该链路各段已有独立验证。热修复只包含程序文件，应用前须关闭正在运行的便携版并校验、备份原程序，不读取或迁移用户配置及业务数据。

官方行为依据：[Codex App Server 模型目录](https://learn.chatgpt.com/docs/app-server)、[Codex 非交互执行](https://learn.chatgpt.com/docs/non-interactive-mode)、[Codex 配置 schema](https://learn.chatgpt.com/docs/config-schema.json)、[Claude Code 无界面执行](https://code.claude.com/docs/en/headless)。版本固定和原生工具限制另外通过本机离线传输实验验证，并非仅依赖提示词声明。
