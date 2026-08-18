# Pi 使用说明

## 安装

先安装 Pi `0.84.2` 或更高版本，再克隆仓库并把它作为实际工作 Project。这样本地知识库、销售台账、模板和输出目录都在当前目录：

```powershell
git clone https://github.com/WH2020/WorkFlow_Market.git
cd WorkFlow_Market
python -m agent_platform validate
python plugin\market-director-copilot\scripts\init_local_data.py --project .
pi install -l .
pi
```

Pi Package 会按当前 Profile 动态加载所需 Skills；切换 Profile 时自动重载资源。产品总监不会加载市场/销售 Skill，市场总监也不会加载产品 Skill。未进入 Profile 的旧版邮箱与聊天 Skill 不会加载。第三方 Pi Package 具备本机执行权限，安装前应先审阅来源。

## 选择岗位

默认岗位为 `market-director`：

```text
/director-profile
/director-profile market-director
/director-profile product-director
```

也可在启动 Pi 前设置 `WORKFLOW_AGENT_PROFILE` 为上述 Profile ID。

## 选择服务

```text
/director-services
/director-run industry-research 调研近三个月国内具身智能数据采集政策与项目
/director-run government-proposal 为某地形成脑机接口试点合作框架
/director-run product-discovery 核验一线工程师远程诊断工具的机会
/director-run product-prd 为设备告警闭环形成可验收 PRD
/director-run product-metrics 设计该功能的指标、埋点和灰度实验
/director-run release-review 评审 1.2 版本是否可以进入灰度
```

命令会调用对应 Skill，并把 Profile 和 Workflow ID 一起交给主 Agent。普通自然语言请求也可以由主 Agent 路由；显式命令更适合希望固定流程的任务。

## 关联现有知识库

1. 保持现有原始资料和 `data/knowledge/source-register.csv` 原位。
2. 在任务中说明需要引用的目录、对象、日期或地域。
3. 主 Agent 通过 `shared.knowledge` 建立本次知识快照，区分事实、判断、假设和未知。
4. 任务完成后先给出拟更新清单；关键业务状态经确认后再写入相应本地台账。

可直接要求：

```text
请先关联现有知识库，列出与“某产品/某客户/某地区”有关的证据、冲突和过期信息，再开始任务。
```

## 当前限制

- 当前交互界面使用 Pi TUI；独立桌面图形界面尚未实现。
- DAG 已有契约、校验和确定性规划骨架，但 Approval 目前是提示与计划约束，不是能拦截任意工具调用的运行时安全状态机。
- DAG 中的 `knowledge.search`、`knowledge.write`、`sales.read` 等是逻辑能力 ID，尚未全部注册为 Pi 工具适配器；没有适配器时 Agent 必须停在该节点并报告。
- 不读取个人微信聊天；需要分析的信息由用户上传或从已授权的结构化数据源提供。
- 不自动发送文件、提交审批、改变客户阶段或批准产品发布。

## 开发校验

```powershell
python -m agent_platform validate
python -m unittest discover -s tests -p "test_agent_platform.py"
```

若本机 `python` 指向 Microsoft Store 占位程序，请改用已安装的 Python 3.11+ 可执行文件运行相同命令。
