# Agent4Market 销售总监助手

当前产品只面向销售总监。默认使用中文，并遵守仓库根目录 `AGENTS.md` 中的数据、来源、微信、外发和审批边界。

处理客户推进、行业研究、政府合作、销售文件、PPT、周报、招投标或已授权微信会话整理时，优先使用项目技能 `/agent4market-sales-director`。通过 `python -m agent_platform coding-agent ...` 把任务交给本机 Agent4Market 智能核心，不直接编辑 `data/`、`inputs/`、`outputs/` 或 `.pi/director-runtime/` 来伪造业务结果。

编码助手可以列出服务和项目、创建任务、查询状态、补充信息或调整方向。具体写库、正式文件生成、客户阶段变更和任何外发仍必须由用户在桌面工作台查看自然语言预览后批准；不要替用户批准。

若工作台离线，明确提示用户先打开 Agent4Market。不得接入 WeFlow，不得连接微信进程、扫描微信目录、提取密钥或扩大用户在工作台选择的会话范围。
