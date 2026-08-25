---
name: agent4market-sales-director
description: Use the local Agent4Market Sales Director workbench for customer progress, industry research, government cooperation, sales documents, presentations, weekly reports, bidding, or governed WeChat review. Route work through the existing task engine instead of editing its business data directly.
---

# Agent4Market 销售总监助手

把当前编码助手作为 Agent4Market 的对话入口；业务执行仍交给本机 Agent4Market 智能核心，以共享项目空间、资料库、销售台账、DAG、任务历史和审批记录。

## 开始前

1. 确定本机 Python 命令：Windows 通常是 `python`，macOS 通常是 `python3`。以下用 `<python>` 表示该命令。
2. 在项目根目录运行 `<python> -m agent_platform coding-agent doctor`。
3. 如果工作台未运行，告知用户先打开 `Agent4Market.exe`（Windows）或 `Agent4Market.app`（macOS）。不要自行启动隐藏进程，除非用户明确要求。
4. 运行 `<python> -m agent_platform coding-agent services` 获取当前销售总监服务。需要路由细节时读取 [服务路由](references/services.md)。

## 创建与跟进任务

1. 只询问会显著影响客户、时间范围、交付形式、项目空间或风险边界的信息；信息足够时直接选择服务。
2. 把完整需求写入临时 UTF-8 文本文件，或通过标准输入传递；敏感业务文字不要放在命令行参数中。
3. 创建任务：`<python> -m agent_platform coding-agent submit --service <服务编号> --project <项目编号> --request-file <文件路径>`。
4. 保留返回的 `task_id`，用 `<python> -m agent_platform coding-agent status --task-id <任务编号>` 查询进展。
5. 用户补充事实时使用 `message --mode supplement`；用户明确要求改变目标或方向时使用 `message --mode redirect`。不要通过修改 `.pi/` 文件伪造消息或状态。
6. 当返回 `requires_approval=true` 时，说明待确认事项并请用户在桌面工作台查看自然语言预览。不得代替用户批准写库、正式文件生成、客户阶段变更或任何外发操作。

## 数据和安全边界

- 不直接编辑 `data/`、`inputs/`、`outputs/`、`.pi/director-runtime/` 或资料库数据库来完成业务任务。
- 不调用工作台私有 HTTP 接口；只使用 `<python> -m agent_platform coding-agent ...` 桥接命令。
- 不读取未由用户在工作台选择并授权的微信会话，不连接微信进程、不扫描微信目录、不提取密钥。
- 不把本地客户资料、聊天内容、接口密钥或模型密钥带入公开搜索词或外部服务。
- 明确区分已证实事实、分析判断、待验证假设和未知信息；正式结论沿用 Agent4Market 的来源与审批规则。
- 工作台离线、任务尚未接手或审批未完成时，如实报告状态，不自行改写文件绕过阻塞。

## 可用桥接命令

- `doctor`：检查 Codex/Claude 技能、命令行入口和本机工作台状态。
- `services`：列出销售总监服务及对应工作流。
- `projects`：列出可用项目空间。
- `submit`：创建受管任务；可选任务模型和思考强度。
- `tasks` / `status`：查看任务与下一步动作，不输出冻结写入的原始载荷。
- `message`：向已接手任务补充信息或调整方向。
- `open`：在工作台已经运行时打开其界面。

桥接层故意不提供 `approve` 命令，确保编码助手不能把“用户让我做任务”解释为“用户已经批准具体写入内容”。
