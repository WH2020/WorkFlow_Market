# Pi 使用说明

## 安装

支持 Windows 10/11 与 macOS 13+ 本机部署。先安装 Git、Python 3.11+ 和 Node.js 22.19+，再克隆仓库并运行对应平台安装器；安装器负责 pnpm、Pi 0.84.2+、锁定依赖、开源 LibreOffice、本地数据、项目 Package 和环境体检。

Windows PowerShell：

```powershell
git clone https://github.com/WH2020/WorkFlow_Market.git
cd WorkFlow_Market
.\scripts\setup-windows.ps1
.\Agent4Market.exe
```

macOS Terminal：

```bash
git clone https://github.com/WH2020/WorkFlow_Market.git
cd WorkFlow_Market
bash scripts/setup-macos.sh
bash scripts/start-macos.sh
```

完整的参数、权限和故障处理见 [Windows / macOS 安装部署](双平台安装部署.md)。安装后可运行 `python -m agent_platform doctor`（macOS 使用 `python3`）；需要强制核验 PPT 时增加 `--require-ppt`。

Windows 桌面发行版固定加载 `sales-director`，保留销售、研究、政府合作、文件和 PPT Skills，不加载产品研发 Skill，也不允许切换到其他总监角色。未进入 Profile 的旧版邮箱与聊天 Skill不会加载。第三方 Pi Package 具备本机执行权限，安装前应先审阅来源。

### 接入和选择模型

1. 打开桌面工作台顶部的“当前模型”。
2. 填写 NewAPI 网关根地址，例如 `https://ai.example.com`，不要附加 `/v1`、查询参数或账号密码。
3. 填写 API Key。使用 `127.0.0.1` 或局域网自建服务时，确认来源可信后勾选“允许本机或局域网网关”；公网 HTTP 会被拒绝。
4. 点击“获取可用模型”。工作台只调用该网关的 `/v1/models`，不接受重定向，并限制响应时间、类型和大小。
5. 从动态列表中选择模型，点击“保存模型选择”，然后关闭并重新打开 `Agent4Market`。

保存动作会再次验证模型仍在最新目录中，并把模型 ID、端点类型和保守能力默认值写入 Pi 可在启动前读取的 `models.json`；只有目录写入成功才会把它设为选中模型。Windows 密钥由当前 Windows 用户的 DPAPI 加密，macOS 密钥写入系统钥匙串；`models.json`、任务和 Git 中都不保存明文密钥。Pi 子进程启动时才临时获得密钥环境变量，并通过明确的 `--model agent4market-newapi/<model-id>` 使用所选模型。更换网关地址时必须重新输入密钥。

不配置此项时，应用沿用 Pi 当前默认模型。首版界面只管理一个 NewAPI/OpenAI 兼容网关；动态发现、端点判断和本地目录设计借鉴了 MIT 许可项目 [pi-provider-newapi](https://github.com/ttimasdf/pi-provider-newapi)，但没有捆绑其扩展运行时。当前不会自动读取价格比例；未明确声明能力的模型按文本、非推理、128K 上下文和 32K 最大输出的保守默认值注册，实际限额仍以网关和模型为准。

行业研究和政府合作方案需要公开检索。在本机环境变量中配置 `BRAVE_SEARCH_API_KEY` 后再启动 Pi；密钥不要放进 `.env`、JSON、截图或 Git。适配器先调用 Brave 正式 Web Search API 发现来源，再由受控 `web.open` 读取选中的正文；读取连接固定到已核验的公网地址，DNS 与连接空闲各限 10 秒，网页下载及在线 PDF 提取合计限 30 秒，并拒绝重定向。未配置、配额不足、私网目标或网络异常时会停在当前 DAG 节点。[官方认证说明](https://api-dashboard.search.brave.com/documentation/guides/authentication)。

### PDF 与 PPT 运行时

本地 PDF 放在 Project 的 `inputs/` 或 `data/inbox/` 下，并在任务中写明精确相对路径。这两个目录不会提交到 Git。项目依赖中已包含 `pdfjs-dist`，解析器和本地文本层兜底都在同一个隔离子进程中运行，单次最多 45 秒、256 MiB、32 MiB 输入和指定页数/字符预算；兜底另有限制对象、引用、数据流和解压总量。`WORKFLOW_PDFJS_MODULE` 只用于显式覆盖解析模块；通常不需要设置。PDF.js 无法解析时的兜底结果会标为 `extraction_reliability=limited`，只能以 `pending` 待核验来源入库；扫描件仍需先转换为可检索 PDF。

周报和 PPT 工作室是独立工具链，不需要 Codex Desktop：

```text
PptxGenJS                       = 可编辑 PPTX 生成（项目锁定依赖）
LibreOffice                     = 真实 Office 渲染为 PDF（安装器检测/安装）
PDF.js + @napi-rs/canvas        = 逐页 PNG 与拼图（项目锁定依赖）
JSZip + 项目 QA                 = 页数、notes、来源、边界、重叠和文本容量检查
WORKFLOW_LIBREOFFICE_PATH       = 可选的 soffice 明确绝对路径
WORKFLOW_CJK_FONT               = platform native CJK font
WORKFLOW_LATIN_FONT             = Latin font
```

Windows 默认使用 `Microsoft YaHei`，macOS 默认使用 `PingFang SC`；两者的拉丁字体默认是 `Arial`。启动入口通过 `python -m agent_platform launch` 每次重新核验路径，并只向本次 Pi 子进程注入 LibreOffice 路径和字体；不会生成或执行包含路径的 shell 脚本。缺少任一项目依赖或 LibreOffice 时，`artifact.deck.write` 会停止并指出缺项；不会生成伪 `.pptx`。

## 岗位

桌面发行版岗位固定为 `sales-director`：

```text
/director-profile
```

桌面程序同时设置 `WORKFLOW_AGENT_PROFILE` 和发行版锁；即使通过 Pi 命令也不能切换到其他角色。

## 选择服务

```text
/director-services
/director-run industry-research 调研近三个月国内具身智能数据采集政策与项目
/director-run pdf-import 读取 inputs/某报告.pdf，按页提取证据并准备写入知识库
/director-run government-proposal 为某地形成脑机接口试点合作框架
/director-run presentation-studio 为总经理制作一份 6 页脑机接口行业研判，采用技术研究风格
/director-status
/director-approve
/director-reject approve_scope 需要补充异常流程
/director-cancel 暂停本次任务
```

`/director-run` 会创建一个受管任务；同一 Pi 会话同时只运行一个任务。Agent 和 Validator 节点由模型报告完成，逻辑 Tool 节点只能由匹配的适配器完成，Approval 只能由用户推进。行业研究会自动调用一次“公开研究员”，政府合作方案会在初稿后自动调用一次“只读复核员”，用户无需另填表或手工选择角色。若后续要写知识库或销售台账，Agent 必须先冻结完整批次；缺少冻结内容时，状态机不允许进入 Approval。任务状态同时记录在 Pi 会话和 `.pi/director-runtime/tasks/`，重启后可以恢复。普通自然语言请求仍可用于讨论；需要强制流程和审批时应使用 `/director-run` 或本地工作台。

## 本地工作台

正常使用时双击仓库根目录的 `Agent4Market.exe`，桌面窗口会自动启动本地工作台和销售总监 Pi 终端，不会打开系统浏览器。以下命令只用于开发诊断：

```powershell
python ui/server.py
```

macOS 使用：

```bash
python3 ui/server.py
```

工作台只监听 `127.0.0.1`，提供销售服务选择、任务提交、阶段查看、审批、驳回、取消、台账状态摘要和最近产物入口；界面不显示市场总监或产品总监。选择“PPT 工作室”或“周五销售汇报”时，窗口会切换为结构化表单。任务请求由 Pi 接手，界面本身不会执行模型、改客户阶段或发送文件。

PPT 工作室首版的资料范围固定为“公开网页 + 当前 Profile 知识库”：DAG 先执行公开检索与正文读取，再读取内部知识。本地 PDF 应先通过“PDF 资料入库”进入知识库。界面不会提供尚未生效的“仅本地/仅公开”开关；不要把秘密、客户口令或未公开项目代号写入会用于公开检索的主题。后续如需这两种范围，必须用权限和节点都独立的受控工作流实现。

大纲写入后以便利贴展示。用户可编辑结论式标题、拖拽排序，然后点击“按此大纲创建修订任务”。为防止旧审批被复用，工作台不会修改原 plan；它会驳回旧大纲并创建一个新的受管任务，新任务重新建立证据、写 plan、确认大纲和批准正式生成。`.pi/director-runtime/presentation-plans/` 是运行时受控目录，不要手工编辑其中 JSON；哈希不一致时运行时会拒绝继续。

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
- `weekly.snapshot`：按北京时间聚合销售总监任务/审计、知识新增、销售三张表和受管任务登记的产物；每类数据都返回命中数、返回数和截断标记。
- `presentation.plan.write`：按任务保存 outline/final 两阶段 plan，使用连续版本、证据上下文 SHA-256 和 plan SHA-256 防止跨任务接管或静默修改。final 必须延续已确认 brief、大纲、周期和来源上下文；要改大纲需创建新任务重新确认。
- `artifact.deck.write`：接收已冻结并批准的 4–10 页结构化载荷；周报绑定当前 `weekly.snapshot`，PPT 工作室绑定当前 final plan 与证据 registry。经营管理、政企合作、前沿研究三套设计令牌确定配色、标签与视觉语气，逐页 `layout_intent` 决定几何版式；两者都随冻结载荷进入构建器。在 task/intent 私有目录中使用 PptxGenJS 构建可编辑 PPTX，经 LibreOffice 转为真实渲染 PDF，再由 PDF.js 逐页生成 PNG；项目 QA 同时检查 layout、文本容量、画布边界、文本框重叠、页数和 speaker notes 来源。全部通过后才独占提交到 `outputs/`，不覆盖已有文件。
- 所有结构化数据文件继续位于本地 `data/`；适配器采用独占锁、同目录临时文件原子替换和 `.pi/director-runtime/commits/` 提交日志。若数据已替换但任务快照尚未推进，重试会按提交前后哈希完成恢复；哈希两边都不匹配时停止并要求人工核对。
- 网页/PDF 工具生成的 URL allow-set 与精确 `knowledge.write` mutation 按任务写入 `.pi/director-runtime/evidence/`。Pi 重启后会重新加载该 registry；工具来源 ID 不在本任务 registry 中、或 mutation 内容发生变化时，写入会被拒绝。

## 当前限制与边界

- 硬门禁适用于 `/director-run` 或工作台创建的“受管任务”，不是操作系统沙箱；第三方 Pi 扩展和用户在受管任务之外执行的命令仍拥有其自身权限。
- 受管任务运行期间禁用通用 Bash，`data/` 结构化写入不能使用普通 `write` / `edit` 绕过；当前周报和 PPT 工作室具备受控 PPT 生成链路，通用 Word、Excel 及企业母版自动解析尚未实现。
- 行业研究默认按“公开研究员 Subagent → `knowledge.search` → 主 Agent 综合 → 来源校验”执行。公开研究员运行在独立 Pi 子进程中，只能调用受管 `web.search` / `web.open`，并且只能打开本任务检索结果或用户明确提供的公开 URL；检索词若疑似包含邮箱、手机号、身份证号、密钥或口令会被拒绝。政府合作方案的复核员没有工具，只检查草案的目标、证据、假设、边界与风险。Subagent 不能写数据库、台账或文件，不能审批、外发、后台运行或再派生下级 Agent；其结果带合同、任务、来源和 SHA-256 回执，并由主 Agent 决定是否采纳。
- `web.open` 只处理公开、无需登录的 HTML、纯文本和 PDF，不执行 JavaScript、不保留登录态、不绕过反爬或访问控制。`user_provided=true` 只允许用于用户在当前任务中直接给出的 URL；该语义依赖主 Agent 遵守工具契约。
- PDF 只提取已有文本层，不做 OCR。在线 PDF 的 PDF.js 解析失败时直接安全停止；本地受控目录文件可在同一受限子进程中使用标为 `limited` 的文本层兜底。主 Agent 进程不解析 PDF 数据流。扫描版、复杂字体映射或损坏 PDF 可能需要用户转换为可检索 PDF 后重试。
- PPT 视觉 QA 以当前电脑上的 LibreOffice 渲染为准；不同平台字体度量可能造成换行或文件哈希差异，因此每次生成都重新渲染全部页面。LibreOffice 转换失败、页数不符、预览缺失、来源备注异常、元素越界、预计文本溢出或文本框重叠都会失败停止，不提供“仅结构通过”的降级提交。
- HTML 与 PDF 正文都属于不可信输入；Agent 不执行其中的指令。当前网页正文抽取不是浏览器渲染器，复杂 JavaScript 页面可能需要用户另行导出资料。
- 当前桌面发行版只提供销售总监，不加载产品总监服务或产品研发 Skills；政府合作能力保留。
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
pnpm test:ppt-e2e
python -m unittest ui.test_server
```

macOS 将上述 `python` 改为 `python3`。若 Windows 的 `python` 指向 Microsoft Store 占位程序，请改用已安装的 Python 3.11+ 可执行文件运行相同命令。公共 CI 在 Windows 与 macOS 上执行相同的校验，并在两端真实生成、渲染和检查 PPT；不需要 Codex Desktop。
