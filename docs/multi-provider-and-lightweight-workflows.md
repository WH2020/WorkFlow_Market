# 多供应商与轻量工作流

本次修改保留 Tauri / Python / Pi 架构，Pi 仍是业务任务执行核心。没有更改现有正式业务 DAG，没有迁移或修改真实业务数据，也没有使用真实密钥调用模型。

## 使用方式

1. 在“设置 → 模型供应商与目录”新增实例。OpenAI、Anthropic 提供预设；NewAPI 和自定义兼容 API 在“接收地址与协议”中填写端点。
2. 每个实例单独填写 API Key。可发现模型，也可离线手动填写模型 ID；把需要的模型加入已启用列表。发现的 ID 缓存与启用列表分开，不自动把整个发现目录开放给任务。
3. 在高级选项填写当前模型的上下文、输出上限、图片和思考能力。能力不会从其他供应商的同名模型借用；默认值是运行配置，不是服务商能力认证。
4. 选择默认模型并保存，关闭后重新启动源码工作台。任务入口按供应商分组选择模型；修改配置不会让已有任务自动切换接收方。
5. 销售复盘、行业研究的“仅分析”选项会进入只读流程。内部、非政府、仅用资料库的快速 PPT 使用新的快速流程；其他 PPT 继续使用原流程。

Windows 原生 EXE 已完成离线 Release 构建。它仍依赖完整源码运行目录，不是包含 Python、Node 和业务资源的独立安装包；已经安装的发布版不会因源码变更而自动更新。

## 接入范围

- 支持多个独立实例，包括同一厂商的多个账号或网关。模型身份为 `provider_id/model_id`。
- 协议为 `openai-completions`、`openai-responses`、`anthropic-messages`，实际通信复用 Pi 适配器。
- 主任务和可选研究/复核角色的模型及接收方会被冻结。公开研究使用 fresh 上下文；fork 复核只能使用同一供应商、端点和协议。子代理在调用前核对合同，并拒绝未授权的 fallback 回执。
- Claude Code / Codex CLI 保留受控任务入口；其检测仅说明安装状态。旧 v2 CLI 配置会显示“尚不是 Pi 模型后端”，不会再因读取缺失的 `base_url` 崩溃，也不会覆盖可工作的 API 配置。

依据 OpenAI Docs 对 Codex Agent 会话、事件和审批接口的区分，本轮未把 CLI 包装成通用模型 API。真正的 CLI 执行后端仍需独立适配，未在本轮实现。

## 轻量流程及保留的边界

| 新服务 | Workflow ID | 变化 |
| --- | --- | --- |
| 销售复盘只读 | `market.sales.pipeline-review-readonly-v2` | 读取、分析、校验后完成，不要求写入意图 |
| 行业研究只读 | `shared.research.frontier-readonly-v2` | 保留公开研究与内部证据，校验后完成，不强制入库 |
| 内部快速 PPT | `shared.presentation.studio-quick-v2` | 9 节点、0 个网页工具节点、1 次正式生成审批；合并逐页策划与设计 |

快速 PPT 仍保存 outline/final 快照，并核对证据、规划哈希和完整渲染载荷；没有资料时不能扩大到网页检索或编造来源。快速模式只减少大纲审批，不减少正式生成审批和 QA。

原有 PPT、政府合作、投标和写入流程保持原 ID 与节点定义。新路由仅影响新建请求，不会原地重新解释旧任务的已完成节点。

## 配置与恢复

- v1 NewAPI 配置采用虚拟读取，明确保存时再写入 v3 列表；原实例身份和密钥保持兼容。
- Windows 凭据使用 DPAPI；macOS 使用钥匙串；其他平台沿用仅本机可读的凭据文件。实例之间不能通过交换凭据文件复用密钥。
- `model-recipient-bindings.json` 只保存非密钥身份记录；删除实例后仍保留，以阻止旧模型键重新指向其他接收地址或协议。
- 保存/删除遇到可捕获的 I/O 错误会回滚配置、凭据、Pi 目录和身份记录；删除失败后可重试。不声称提供跨磁盘与系统钥匙串的掉电事务保证。
- 大模型目录通过本地文件及 SHA-256 传给运行时，而非塞进一个环境变量。内容变更或校验失败会停止调用，不读取未核验的新地址。
- 微信范围授权绑定供应商、地址、协议及模型。旧未绑定范围必须重新确认；读取后会话不能再转给其他模型。
- 旧任务若缺少接收方字段，不能从当前配置推断原授权。原任务、审批及产物保留，工作台显示中断原因，可取消或明确重新创建；提交中的写入仍必须先按原恢复规则处理。
- 受管长会话使用本地状态检查点压缩，避免 Pi 内置远程摘要绕过普通请求边界。检查点保留请求、节点、审批意图元数据、产物和近期记录；未保存的长篇分析可能需要重新核验。

## 验证

运行环境：Windows、Python、Node.js 24.19.0、Pi coding-agent 0.84.2。

```powershell
python -B -m unittest discover -s tests -p 'test_*.py' -q
python -B -m unittest ui.test_server -q
npm run check:types
npm run test:runtime
npm run test:provider-e2e
npm run test:ui-models
python -B -m agent_platform validate
node --check ui/app.js
```

覆盖同名模型隔离、真实 Pi 目录/鉴权解析（离线假密钥）、三个协议、500 模型目录、配置错误回滚、模型键删除重建、角色/任务冻结、旧任务取消恢复、压缩边界、真实 Pi ExtensionRunner/Agent 循环中的取消与脱敏、微信授权以及只读/快速 PPT 审批边界。

2026-09-08 续验：运行时回归 **118/118**、页面模型逻辑 **2/2**、TypeScript 检查通过。新加入的 15 项协议端到端用例已并入 `test:runtime`，也可单独运行 `test:provider-e2e`。它们使用真实 Python 配置、Pi SDK 和本地 HTTP/SSE 模拟服务，覆盖三个协议的流式工具调用、结果回传、取消、鉴权失败，以及接收方授权匹配/漂移。漂移时本地服务器收到 **0 个请求**；匹配时首轮正常完成。页面测试同时核对所访问的控件在实际 HTML 中恰好存在一次。

模拟事件依据 OpenAI Docs 的[流式响应](https://developers.openai.com/api/docs/guides/streaming-responses)和[函数调用事件](https://developers.openai.com/api/docs/guides/function-calling)核对，并使用项目锁定的 Pi 0.84.2 适配器实测。模拟服务成功不代表真实供应商的配额、网关兼容性或模型能力已通过验收。

`test:ui-models` 使用实际页面函数和 DOM 测试替身，不等于真实浏览器或视觉验收。本轮浏览器连接因插件兼容性错误未能初始化；没有执行真实供应商端到端调用、macOS 钥匙串实机测试，也未测量耗时、Token 或费用收益。

可运行 `python -B -m tests.workbench_ui_fixture` 进行后续页面验证：它使用临时项目、假凭据和离线发现，不启动调度器或 AI 核心。

## Windows 构建记录

本机 Node 24.19.0、Python 3.11.9、Rust 1.93.1、MSVC/Windows SDK 可用。以下命令成功，未下载或安装依赖，耗时 3 分 31 秒：

```powershell
cargo build --manifest-path desktop/src-tauri/Cargo.toml --release --locked --offline --target-dir outputs/verification/windows-native-20260908-4f812dd7f5ce458e86f31f0ce5b5631e
```

- 产物：`outputs/verification/windows-native-20260908-4f812dd7f5ce458e86f31f0ce5b5631e/release/Agent4Market.exe`
- 版本：0.20.0；大小：11,122,688 字节。
- SHA-256：`0aced88e56b914355b646283d933674803bb9087ecfe258a8a66190c4667c354`。
- 没有覆盖原 `Agent4Market.exe`，没有运行安装器或发布产物。
- 启动自检尚未执行：所需 8765 端口已有进程监听。原构建脚本的默认自检还会在真实项目中启动服务并初始化数据，不能直接用于隔离验收。

后续自检应先由用户关闭现有工作台，再把 EXE 放入仓库外、包含运行依赖的隔离副本执行 `--self-test`。仅改变工作目录不够：启动器会优先从 EXE 所在目录向上查找项目。浏览器技能连接恢复后，再完成实际窗口的视觉与交互验收。
