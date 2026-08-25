# Codex CLI 与 Claude Code 使用说明

Agent4Market 支持三种入口，但只有一套业务内核：

- 桌面端：面向销售总监日常使用，显示任务过程、资料卡片和人工审批。
- Codex CLI：作为项目级自然语言入口，通过 `.agents/skills/agent4market-sales-director/` 发现技能。
- Claude Code：作为项目级自然语言入口，通过 `.claude/skills/agent4market-sales-director/` 发现技能，并读取根目录 `CLAUDE.md`。

Codex CLI 与 Claude Code 不复制 Pi 的业务工具，也不直接修改资料库。它们通过本机桥接创建、查询和调整 Agent4Market 任务，任务仍由 Pi、同一组 DAG 与受控适配器执行。写库、客户阶段变更、正式 PPT/Word 生成和外发仍需要用户在桌面工作台审批。

## 1. 安装编码助手

Agent4Market 安装包不会捆绑第三方账号、登录状态或编码助手本体。请按各自官方说明安装并登录：

- Codex CLI：[OpenAI Codex CLI](https://developers.openai.com/codex/cli/)。Windows 上若 `codex --version` 指向 Codex Desktop 的受保护目录并提示“拒绝访问”，需要另装官方 CLI；桌面程序本身不等于可从终端调用的 CLI。
- Claude Code：[Anthropic 安装说明](https://code.claude.com/docs/en/installation)。Windows 可使用官方原生安装器或 `winget install Anthropic.ClaudeCode`；macOS 可使用官方原生安装器或 Homebrew。

安装后在 Agent4Market 目录运行（macOS 若没有 `python` 命令，请把下文命令中的 `python` 换成 `python3`）：

```powershell
python -m agent_platform coding-agent doctor
```

`integration_ready=true` 表示两个项目技能副本与标准源一致。`hosts.codex.ready`、`hosts.claude.ready` 分别表示当前终端能否调用对应程序。`workbench.ready=false` 只表示 Agent4Market 尚未打开，不代表技能损坏。

## 2. 启动

先打开桌面端，使本机工作台与 Pi 智能核心运行。随后从 Agent4Market 安装目录启动编码助手。

Windows：

```powershell
.\scripts\start-coding-agent.ps1 -Agent codex
.\scripts\start-coding-agent.ps1 -Agent claude
```

macOS：

```bash
bash scripts/start-coding-agent.sh codex
bash scripts/start-coding-agent.sh claude
```

Codex CLI 中可显式输入：

```text
$agent4market-sales-director 帮我复盘本周三个重点客户的进展和下周动作
```

Claude Code 中可显式输入：

```text
/agent4market-sales-director 帮我根据已上传材料准备江苏某地政府合作方案
```

也可以直接描述销售任务；项目级技能允许两个助手按描述自动匹配。显式调用更适合首次验证。

## 3. 桥接命令

通常由技能自动调用，用户也可以手动诊断：

```powershell
python -m agent_platform coding-agent services
python -m agent_platform coding-agent projects
python -m agent_platform coding-agent tasks --limit 10
python -m agent_platform coding-agent status --task-id request-xxxxxxxxxxxx
python -m agent_platform coding-agent open
```

创建任务时优先使用 UTF-8 文件或标准输入，避免敏感业务文字出现在进程参数中：

```powershell
python -m agent_platform coding-agent submit `
  --service government-proposal `
  --project project-default `
  --request-file .\tmp\government-request.txt `
  --thinking high
```

若模型网关已经在桌面端配置，也可加 `--model agent4market-newapi/<模型编号>`。模型编号必须属于工作台当前配置的可用列表。

任务被智能核心接手后，可以排队补充事实或调整方向：

```powershell
python -m agent_platform coding-agent message `
  --task-id request-xxxxxxxxxxxx `
  --mode supplement `
  --content-file .\tmp\supplement.txt
```

`supplement` 是补充事实，`redirect` 是明确改变当前方向。两者都不能改写已完成节点、冻结载荷或审批结果。

## 4. 人工审批

桥接命令没有 `approve`。当 `status` 返回 `requires_approval=true`：

1. 打开桌面工作台中的对应任务；
2. 阅读自然语言待写入卡片、来源和风险提示；
3. 需要时逐条编辑或删除；
4. 由用户本人批准或驳回。

这不是功能缺失，而是产品安全边界：给编码助手一句“帮我做方案”不能等同于批准尚未看到的数据库变更或正式文件。

## 5. 当前限制

- 桌面端必须运行，编码助手才能创建和跟进业务任务；离线时仍可列出仓库中的服务定义。
- 编码助手是替代交互入口，不替代 Pi 执行内核。因此切换 Codex/Claude 不会迁移或复制任务数据。
- 微信会话整理必须先在桌面端导入、筛选并生成单次授权范围；编码助手不能自行扩大范围。
- Codex CLI 或 Claude Code 自身的模型、思考强度与 Agent4Market 任务执行模型是两层设置。桥接 `--model` / `--thinking` 控制的是后者。
- 当前只暴露销售总监服务；仓库中为兼容验证保留的其他 Profile 不会通过桥接列出。

## 6. 开发者同步检查

标准技能源位于 `integrations/coding-agents/agent4market-sales-director/`。修改后运行：

```powershell
python scripts/sync-coding-agent-skills.py
python scripts/sync-coding-agent-skills.py --check
```

CI 和单元测试会检查 Codex 与 Claude 的技能副本是否与标准源完全一致，避免双份维护漂移。
