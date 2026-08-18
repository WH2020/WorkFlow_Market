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

行业研究和政府合作方案需要公开检索。在本机环境变量中配置 `BRAVE_SEARCH_API_KEY` 后再启动 Pi；密钥不要放进 `.env`、JSON、截图或 Git。适配器先调用 Brave 正式 Web Search API 发现来源，再由受控 `web.open` 读取选中的正文；读取连接固定到已核验的公网地址，DNS 与连接空闲各限 10 秒，网页下载及在线 PDF 提取合计限 30 秒，并拒绝重定向。未配置、配额不足、私网目标或网络异常时会停在当前 DAG 节点。[官方认证说明](https://api-dashboard.search.brave.com/documentation/guides/authentication)。

### PDF 与 PPT 运行时

本地 PDF 放在 Project 的 `inputs/` 或 `data/inbox/` 下，并在任务中写明精确相对路径。这两个目录不会提交到 Git。项目依赖中已包含 `pdfjs-dist`，解析器和本地文本层兜底都在同一个隔离子进程中运行，单次最多 45 秒、256 MiB、32 MiB 输入和指定页数/字符预算；兜底另有限制对象、引用、数据流和解压总量。`WORKFLOW_PDFJS_MODULE` 只用于显式覆盖解析模块；通常不需要设置。PDF.js 无法解析时的兜底结果会标为 `extraction_reliability=limited`，只能以 `pending` 待核验来源入库；扫描件仍需先转换为可检索 PDF。

周报 PPT 需要 Codex 工作区附带的演示文稿运行时。先通过 Codex 的工作区依赖加载器取得路径，再在启动 Pi 的同一终端设置：

```text
WORKFLOW_ARTIFACT_NODE          = Node.js executable
WORKFLOW_ARTIFACT_TOOL_PATH     = <Node.js packages>/@oai/artifact-tool
WORKFLOW_PRESENTATIONS_MARKER   = <Presentations Skill>/container_tools/mark_artifact_operation_started.mjs
WORKFLOW_ARTIFACT_PYTHON        = Python executable
WORKFLOW_SLIDES_TEST            = <Presentations Skill>/container_tools/slides_test.py
RUNTIME_NODE                    = Node.js executable
RUNTIME_NODE_MODULES            = Node.js packages
RUNTIME_BIN_DIR                 = Override binaries
```

这些值必须是本机绝对路径，不要提交到仓库。缺少任一 PPT 依赖时，`artifact.deck.write` 会停止并指出缺项；不会回退到 `python-pptx` 或生成伪 `.pptx`。

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
/director-run pdf-import 读取 inputs/某报告.pdf，按页提取证据并准备写入知识库
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
- `web.open`：一次读取最多 6 个搜索结果或用户明确提供的公开 URL；固定使用预先核验的公网地址，拒绝二次 DNS、重定向、私网地址、密钥参数和 8 MiB 以上正文，清除 HTML 中的脚本与样式。在线 PDF 自动转入 PDF 文本提取。
- `pdf.read`：只读取 `inputs/` 或 `data/inbox/` 下明确的普通 `.pdf`，最大 32 MiB，返回稳定来源 ID、文本、可靠度、截断状态和实际返回页码证据；文本层不足时失败停止。
- `weekly.snapshot`：按北京时间聚合当前 Profile 的任务/审计、知识新增和受管任务登记的产物；只有市场总监读取销售三张表，产品总监不会跨 Profile 读取销售数据。每类数据都返回命中数、返回数和截断标记。
- `artifact.deck.write`：接收已冻结并批准的 4–10 页结构化周报载荷；载荷的 Profile、period、`snapshot_sha256`、来源 URL 和本地来源 SHA-256 必须与当前任务持久化快照一致。在 task/intent 私有目录中使用 `@oai/artifact-tool` 构建，逐页生成 PNG/layout、检查 speaker notes 来源并运行官方 `slides_test.py`；QA 通过后才独占提交到 `outputs/`，不覆盖已有文件。
- 所有结构化数据文件继续位于本地 `data/`；适配器采用独占锁、同目录临时文件原子替换和 `.pi/director-runtime/commits/` 提交日志。若数据已替换但任务快照尚未推进，重试会按提交前后哈希完成恢复；哈希两边都不匹配时停止并要求人工核对。
- 网页/PDF 工具生成的 URL allow-set 与精确 `knowledge.write` mutation 按任务写入 `.pi/director-runtime/evidence/`。Pi 重启后会重新加载该 registry；工具来源 ID 不在本任务 registry 中、或 mutation 内容发生变化时，写入会被拒绝。

## 当前限制与边界

- 硬门禁适用于 `/director-run` 或工作台创建的“受管任务”，不是操作系统沙箱；第三方 Pi 扩展和用户在受管任务之外执行的命令仍拥有其自身权限。
- 受管任务运行期间禁用通用 Bash，`data/` 结构化写入不能使用普通 `write` / `edit` 绕过；当前只有周报 PPT 具备受控文件生成链路，通用 Word、Excel 和任意模板 PPT 尚未实现。
- 默认行业研究按 `web.search` → `web.open` → `knowledge.search` 顺序执行：先发现和读取外部来源，再读取内部知识，避免把内部资料带入外部查询。仓库另有 `shared.research.frontier-subagent` 契约，但在安装隔离 Subagent 执行器前不会作为默认服务运行，也不能被模型静默完成。
- `web.open` 只处理公开、无需登录的 HTML、纯文本和 PDF，不执行 JavaScript、不保留登录态、不绕过反爬或访问控制。`user_provided=true` 只允许用于用户在当前任务中直接给出的 URL；该语义依赖主 Agent 遵守工具契约。
- PDF 只提取已有文本层，不做 OCR。在线 PDF 的 PDF.js 解析失败时直接安全停止；本地受控目录文件可在同一受限子进程中使用标为 `limited` 的文本层兜底。主 Agent 进程不解析 PDF 数据流。扫描版、复杂字体映射或损坏 PDF 可能需要用户转换为可检索 PDF 后重试。
- Windows 下 Codex 附带的画布运行时可能在全部 PNG 写完后返回原生清理状态 `0xC0000409`。兼容运行器只接受这个精确状态，并同时校验渲染清单路径仍在私有 QA 目录、页数与 PNG 名称/数量/非空/唯一性一致、PPTX 内 notes 页数量正确且每页恰有一个 `[Sources]` 块，然后才继续执行官方 `slides_test.py`；正文构建失败、缺页、空页或来源备注异常仍会失败停止，结果中会明确标注降级 QA。
- HTML 与 PDF 正文都属于不可信输入；Agent 不执行其中的指令。当前网页正文抽取不是浏览器渲染器，复杂 JavaScript 页面可能需要用户另行导出资料。
- 产品总监周报当前聚合任务、知识来源和本 Profile 产物，不读取市场销售台账；更细的产品指标/路线图业务快照仍需后续专用适配器。
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
