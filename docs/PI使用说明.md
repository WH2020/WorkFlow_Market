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

行业研究和政府合作方案需要公开检索。在本机环境变量中配置 `BRAVE_SEARCH_API_KEY` 后再启动 Pi；密钥不要放进 `.env`、JSON、截图或 Git。适配器只调用 Brave 的正式 Web Search API；未配置、配额不足或网络异常时会停在当前 DAG 节点。[官方认证说明](https://api-dashboard.search.brave.com/documentation/guides/authentication)。

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
/director-status
/director-approve
/director-reject approve_scope 需要补充异常流程
/director-cancel 暂停本次任务
```

`/director-run` 会创建一个受管任务；同一 Pi 会话同时只运行一个任务。Agent 和 Validator 节点由模型报告完成，逻辑 Tool 节点只能由匹配的适配器完成，Approval 只能由用户推进。若后续要写知识库或销售台账，Agent 必须先冻结完整批次；缺少冻结内容时，状态机不允许进入 Approval。任务状态同时记录在 Pi 会话和 `.pi/director-runtime/tasks/`，重启后可以恢复。普通自然语言请求仍可用于讨论；需要强制流程和审批时应使用 `/director-run` 或本地工作台。

## 本地工作台

另开一个终端，在仓库根目录执行：

```powershell
python ui/server.py
```

浏览器打开 `http://127.0.0.1:8765`。工作台只监听 `127.0.0.1`，提供岗位/服务选择、任务提交、阶段查看、审批、驳回、取消、台账状态摘要和最近产物入口。任务请求由 Pi 接手，页面本身不会执行模型、改客户阶段或发送文件。

工作台和 Pi 必须在同一仓库目录下运行。若 Pi 没有启动，请求会保持“等待 Pi 接手”；启动后运行时会轮询处理。

## 关联现有知识库

1. 保持现有原始资料和 `data/knowledge/source-register.csv` 原位。
2. 在任务中说明需要引用的目录、对象、日期或地域。
3. 主 Agent 通过 `shared.knowledge` 建立本次知识快照，区分事实、判断、假设和未知。
4. 读取结果带 `_record_version`；更新时必须携带该版本，记录已变化就拒绝覆盖。
5. 写入前冻结完整参数并生成校验码；工作台展示具体 JSON 与校验码，批准请求必须同时匹配任务版本、写入意图 ID 和校验码。
6. 批准后，同一 CSV 内 1–100 条变更一次校验、一次原子替换；任一记录失败则整批不写。跨表更新拆成不同节点和审批，不提供伪原子承诺。

可直接要求：

```text
请先关联现有知识库，列出与“某产品/某客户/某地区”有关的证据、冲突和过期信息，再开始任务。
```

## 数据适配器

- `knowledge.search` / `knowledge.write`：检索和带版本更新来源登记。
- `sales.read` / `sales.write`：一次读取一个或多个销售表；只允许新增和带版本更新，不允许删除、改主键或写未知字段。
- `web.search`：使用用户配置的 Brave Search API，只返回标题、URL、摘要和时间信息。
- 所有结构化数据文件继续位于本地 `data/`；适配器采用独占锁、同目录临时文件原子替换和 `.pi/director-runtime/commits/` 提交日志。若数据已替换但任务快照尚未推进，重试会按提交前后哈希完成恢复；哈希两边都不匹配时停止并要求人工核对。

## 当前限制与边界

- 硬门禁适用于 `/director-run` 或工作台创建的“受管任务”，不是操作系统沙箱；第三方 Pi 扩展和用户在受管任务之外执行的命令仍拥有其自身权限。
- 受管任务运行期间禁用通用 Bash，`data/` 结构化写入不能使用普通 `write` / `edit` 绕过；目前完整的 Word、Excel、PPT 生成仍取决于后续受控文件适配器，缺少时只交付结构化内容。
- 默认行业研究按 `web.search` → `knowledge.search` 顺序执行：先完成外部来源发现，再读取内部知识，避免把内部资料带入外部查询。仓库另有 `shared.research.frontier-subagent` 契约，但在安装隔离 Subagent 执行器前不会作为默认服务运行，也不能被模型静默完成。
- 当前 `web.search` 是来源发现适配器，只返回 Brave 提供的标题、URL、摘要和时间信息，不抓取网页正文。需要逐字核验的一手材料应由用户提供正文/PDF，或等后续加入受控 `web.open` 适配器；仅凭摘要形成的细节必须标记为“待验证”。
- 不读取个人微信聊天；需要分析的信息由用户上传或从已授权的结构化数据源提供。
- 不自动发送文件、提交审批、改变客户阶段或批准产品发布。
- 当前批量写入事务边界是一张 CSV；多张销售表不会在一个事务中同时提交。
- 锁文件采用失败即停策略，不会自动猜测并删除“陈旧锁”。如果 Pi 或工作台在持锁期间崩溃：先完全退出 Pi 和工作台，打开报错指向的单个 `*.lock` 核对其中 PID，确认该 PID 已不存在后，只删除这个精确锁文件；若任务处于 `committing`，不要删除 `.pi/director-runtime/commits/` 或业务 CSV，重启后重试相同写入，让提交日志按前后哈希恢复。无法确认 PID 或哈希不匹配时保留现场并人工核对。

## 开发校验

```powershell
python -m agent_platform validate
python -m unittest discover -s tests -p "test_agent_platform.py"
pnpm install --frozen-lockfile
pnpm check:types
pnpm test:runtime
python -m unittest ui.test_server
```

若本机 `python` 指向 Microsoft Store 占位程序，请改用已安装的 Python 3.11+ 可执行文件运行相同命令。
