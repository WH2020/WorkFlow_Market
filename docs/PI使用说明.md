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

不配置此项时，应用沿用 Pi 当前默认模型。首版界面只管理一个 NewAPI/OpenAI 兼容网关；动态发现、端点判断和本地目录设计借鉴了 MIT 许可项目 [pi-provider-newapi](https://github.com/ttimasdf/pi-provider-newapi)，但没有捆绑其扩展运行时。当前不会自动读取价格比例；未明确声明能力的模型按文本、非推理、128K 上下文和 32K 最大输出的保守默认值注册，实际限额仍以网关和模型为准。执行任务时会按模型 ID 对照 Pi 内置目录补充已知模型的推理能力；未知或确实不支持推理的模型会把思考强度裁剪为“关闭”。

### 为单次任务选择模型和思考强度

工作台顶栏的“本次模型”和“思考强度”作用于下一次新建任务，也会写入新建的每日定时任务。模型列表只包含已通过当前 NewAPI 配置发现并保存的模型，浏览器不能提交目录外的任意模型。思考强度支持默认、关闭、最少、较低、标准、深入、极深和最大。

Pi 接手请求后会先切换模型，再设置思考等级，最后才创建受管任务并发送任务提示；切换或保存失败时会回滚本次运行配置。任务卡显示的是最终生效值，而不是只显示用户请求值。模型能力不足时 Pi 会自动降低等级，例如非推理模型显示为“关闭”。历史任务再次创建、中断任务重新开始以及 PPT 大纲修订会保留原任务的显式选择；“默认”不会固定某个模型，而是在每次任务开始时恢复应用启动时的默认配置。

行业研究、政府合作方案和需要公开资料的演示文稿开箱即可检索。未配置密钥时，应用调用 Keenable 官方免密公共接口；其共享池有独立限流，繁忙时稍后重试即可。需要更稳定的普通/广泛检索时，可进入工作台“设置 > 公开检索”，保存可选的 Brave Search API Key 并重启。Windows 密钥由当前用户的 DPAPI 加密，macOS 密钥写入系统钥匙串；任务、日志和 Git 中均不保存明文密钥。已有自动化部署也可继续使用环境变量 `BRAVE_SEARCH_API_KEY`。

### 可选：接入 One Search 聚合网关

1. 先在本机、局域网或可信服务器单独部署 [One Search](https://github.com/CncCbz/one-search)。Agent4Market 不安装 Docker 服务，也不管理 One Search 的提供商密钥、缓存和审计库。
2. 在 One Search 中创建权限受限的 `osr_` 外部检索令牌。不要把 `oak_` 管理员超级凭据填入 Agent4Market；界面和后端都会拒绝它。
3. 打开“设置 > 搜索聚合网关”，填写根地址（不要附加 `/v1/search`）、`osr_` 令牌、聚合方式和每次最多结果数。本机或局域网地址需显式勾选允许。
4. 点击“测试并启用”。工作台会调用 `/v1/providers` 验证地址、令牌和提供商目录；不接受重定向，并限制响应时间、类型和大小。保存后重启应用。
5. 启用后，受控 `web.search` 优先调用 `/v1/search`；结果保留具体上游提供商名称，但仍只是候选来源，必须继续由 `web.open` 核验正文。网关失败会直接提示修复或停用，不会把同一查询静默发送到 Brave 或免密公共接口。

聚合方式对应 One Search 的 `parallel`（并行）、`fallback`（依次尝试）和 `single`（单提供商）。Windows 令牌由当前用户的 DPAPI 加密，macOS 写入系统钥匙串；Pi 子进程启动时才临时得到 `ONE_SEARCH_BASE_URL`、`ONE_SEARCH_API_TOKEN`、`ONE_SEARCH_MODE`、`ONE_SEARCH_MAX_RESULTS` 和私网许可。运行时每次连接都会重新解析网关主机，并把连接固定到已核验地址；公网 HTTP、未授权私网、未指定地址、多播地址和保留地址会被拒绝。能力与流程参考了 [One Search 文章](https://linux.do/t/topic/2581154)；管理员令牌边界见 [官方说明](https://github.com/CncCbz/one-search/blob/main/docs/admin-api-key.md)。

`web.search` 提供 `auto`、`broad`、`official`、`chinese_policy` 和 `recent` 五种场景，可选 `site`、发布日期区间、每项数量和摘要长度。`auto` 会根据政策、时效、标准/论文等关键词选择场景；中文政策默认限定 `gov.cn`，近期信息默认使用 30 天采集窗口。广泛发现优先专用 Brave（已配置时）；中文政策、站点或日期限定优先免密公共检索；未配置专用密钥时其他场景也自动使用免密接口。跨查询 URL 会去重，官方/学术域名只作为本地排序提示。所有搜索结果均标记为 `discovery_only`，摘要不能作为事实证据，必须由受控 `web.open` 读取选中的正文。正文连接固定到已核验的公网地址，DNS 与连接空闲各限 10 秒，网页下载及在线 PDF 提取合计限 30 秒，并拒绝重定向。公开查询若疑似含邮箱、手机号、身份证号、口令或密钥会在发送前被拒绝。[Keenable 免密接口与限流](https://docs.keenable.ai/authentication)，[Brave 官方认证说明](https://api-dashboard.search.brave.com/documentation/guides/authentication)。

场景路由与上下文控制借鉴了 [Tavily、Exa、Keenable 的搜索服务对比](https://linux.do/t/topic/2669063)：广泛发现、官方/技术资料、中文政策和站点筛选不应使用同一组默认参数。当前发行版没有同时捆绑三套额外密钥，而是先用 Brave 专用通道与 Keenable 免密接口覆盖销售总监的主要场景；后续如增加新提供商，也必须继续复用相同的敏感查询拦截、结果上限和正文核验边界。

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

正常使用时双击仓库根目录的 `Agent4Market.exe`，桌面窗口会自动启动本地工作台和默认隐藏的嵌入式销售总监 Pi 核心，不会打开系统浏览器或 PowerShell。可在“设置 → AI 核心”查看状态与最近运行记录；如需排查底层启动问题，可启用独立调试窗口并重启应用。以下命令只用于开发诊断：

```powershell
python ui/server.py
```

macOS 使用：

```bash
python3 ui/server.py
```

工作台只监听 `127.0.0.1`，界面不显示市场总监或产品总监。首页先显示待确认、进行中和本周完成，再提供六个高频入口和一条自然语言快捷指令；选择“PPT 工作室”时只有主题必填，“周报中心”只需按需补充一个本周重点。任务请求由 Pi 接手，界面本身不会改客户阶段、批准写入或发送文件。“进行中”必须同时有对应 Pi 会话的实时心跳；旧会话遗留的 `running` 任务显示为“已中断”且不计入进行中，用户可在任务中心选择“取消中断任务”，再由 Pi 状态机完成审计收口。

每个当前任务都有“AI 处理过程”和消息输入框。时间线显示任务阶段、已执行动作、可公开的判断依据、下一步、审批状态和用户消息送达状态，不显示模型隐藏提示词或逐字思维链。“排队补充”保留当前方向并加入新信息；“调整当前方向”要求 Pi 在当前工具调用结束后、下一次模型调用前重新评估尚未执行的步骤。两者都不会修改已完成节点，也不会替代批准、驳回、PPT 大纲修订或重新审批。任务已结束后请使用“再次创建”，不要向历史任务追加消息。

工作台每 3 秒同步任务状态，但会保留当前消息输入框的焦点、选区、光标和内部滚动位置，不会在输入过程中抢走焦点。历史任务默认折叠；“彻底删除”始终显示在卡片标题区，一次确认即可执行。服务端仍要求固定删除确认值，并且只允许删除已结束任务；任务卡、过程、排队消息和演示方案不可恢复，已生成文件、知识库和销售台账不会随卡片删除。

### 项目空间

1. 在左侧打开“项目空间”，按客户、销售机会或政府合作项目新建空间。
2. 从首页或项目页选择当前项目；之后创建的任务、快速指令和每日任务都会记录 `project_id`。
3. 可上传 `.pdf`、`.docx`、`.xlsx`、`.csv`、`.txt`、`.md`、`.pptx`，单文件最大 32 MiB。文件只写入 `inputs/projects/<project_id>/`，拒绝路径穿透、符号链接和覆盖同名文件。
4. PDF 可直接点击“PDF 入库”，自动带入受控相对路径；其他格式首版只作为任务引用材料，不宣称已完成 Word/Excel 全文解析。

“项目空间”是本地归档与上下文分组，不是多人权限隔离。归档项目会暂停其每日任务，但不会删除历史任务、资料或产物。

### 每日定时任务

“每日定时任务”支持名称、执行时间、项目、服务和任务说明，也提供上午客户扫描、下午资源汇总、下班前复盘三个模板。保存时会同时冻结顶栏当前选择的模型和思考强度。调度器每 20 秒检查一次：应用运行且已到时间时创建受管请求；错过时间后当天重新打开应用会补排一次；同一计划同一天最多自动排队一次。也可点击“立即执行”。

这不是操作系统后台服务：应用关闭时不会运行，跨天后不会补做昨天的任务。调度器只负责创建请求，后续仍经过相同 DAG；知识库/销售台账写入、正式 PPT 和任何外发都不会自动批准。

### 自定义搜索

- “搜索本地”即时匹配项目、任务、来源登记、销售四张台账、项目资料和输出产物，返回受限摘要，不把整个 CSV 行直接送到页面。
- “发起公开调研”会把搜索词带入“客户与行业研究”，由用户确认用途后进入受控公开研究；不会绕开来源校验，也不会把内部台账内容自动拼入公开搜索词。
- 搜索结果中的项目或任务可以直接跳回对应工作区；当前最多返回 60 条，正式业务数据仍只保存在本机。

PPT 工作室首版的资料范围固定为“公开网页 + 当前 Profile 知识库”：DAG 先执行公开检索与正文读取，再读取内部知识。本地 PDF 应先通过“PDF 资料入库”进入知识库。界面不会提供尚未生效的“仅本地/仅公开”开关；不要把秘密、客户口令或未公开项目代号写入会用于公开检索的主题。后续如需这两种范围，必须用权限和节点都独立的受控工作流实现。

大纲写入后以便利贴展示。用户可编辑结论式标题、拖拽排序，然后点击“按此大纲创建修订任务”。为防止旧审批被复用，工作台不会修改原 plan；它会驳回旧大纲并创建一个新的受管任务，新任务重新建立证据、写 plan、确认大纲和批准正式生成。`.pi/director-runtime/presentation-plans/` 是运行时受控目录，不要手工编辑其中 JSON；哈希不一致时运行时会拒绝继续。

工作台和 Pi 必须在同一仓库目录下运行。若 Pi 没有启动，请求会保持“等待 Pi 接手”；启动后运行时会轮询处理。

### 中断、继续与历史任务

- “进行中”必须同时满足任务状态为 `running`，且对应 Pi 会话仍持续写入新鲜心跳；旧会话遗留的 `running` 会显示为“已中断”，不会计入进行中。
- “继续任务”保留已完成节点并把任务绑定到当前 Pi 会话，从最后一个安全节点继续。若任务在批准写入后、真正提交前中断，运行时会撤回该次批准并返回原审批节点，不能沿用旧会话的写入授权；`committing` 状态必须先按提交日志恢复，不能直接继续。
- “重新开始”复用原任务说明、项目和服务创建全新任务，不复制完成节点、证据快照、PPT plan、写入意图或 Approval。原中断任务由 Pi 完成取消后标为“已替代”。
- “结束任务”由 Pi 执行正式取消。已完成、已结束、已驳回、失败和已替代任务只出现在历史任务区；历史卡片可“再次创建”，也可在折叠状态下“一次确认”彻底删除本地任务记录。删除不会移除已经生成的文件、知识库记录或销售台账变更。
- 同一任务的继续、结束和重新开始都绑定当前 `version`，并与 Pi 共用独占锁；版本变化、活动心跳存在、写入正在提交或已有生命周期请求时会拒绝操作。

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
- `web.search`：按广泛发现、官方资料、中文政策或近期信息检索候选来源；支持站点、发布日期、数量和摘要长度限制，跨查询 URL 去重，并在发送前拒绝疑似敏感查询。未配置专用密钥时使用官方免密公共接口。返回标题、URL、受限摘要、时间与启发式来源类别，统一标记为尚未核验。
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
