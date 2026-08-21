# Requirements

## Metadata

- Project: Agent4Market 销售总监智能助手
- Project profile: software-app
- Active profiles: software-app
- Requirements version: REQ-v1
- Status: confirmed

## Requirement Index

| ID | Profile | Title | Type | Priority | Status | Related Modules | Related Tests |
|---|---|---|---|---|---|---|---|
| REQ-001 | software-app | 本地全流程智能招投标 | functional / security / compatibility | high | confirmed | MOD-001 | TEST-BID-001～010 |

## REQ-001 本地全流程智能招投标

- Status: confirmed
- Profile: software-app
- Type: functional / security / compatibility
- Priority: high
- Source: user，2026-08-21 当前任务；既有产品边界来自本线程已确认的销售总监单角色、本地优先、人工审批和不自动外发要求
- Alignment keywords: software-app full tender bid bidding lifecycle Agent4Market sales-director desktop Windows macOS Approval evidence DOCX PDF clean-room MIT
- Description: 在现有销售总监桌面 Agent 中增加独立的智能招投标业务域，覆盖从商机/公告登记、招标文件导入、招标解读、投标决策、响应策划、章节生成、合规检查、交付文档生成到归档复盘的本地闭环，并与客户、销售项目、知识来源、受管任务和产出建立可追溯关联。
- Platform: Windows x64 与 macOS Intel/Apple Silicon；继续使用现有 Tauri + 本地 Python + Pi 轻本体，不要求 Docker、PostgreSQL、Redis、MinIO、Celery 或独立 Electron 应用。
- Actors: 首版仅销售总监本人；评审人、编写人、法务、财务等作为项目责任角色和检查项记录，不实现多人账号或在线协同。
- User Flow:
  1. 从招投标工作台登记公告或创建投标项目，可选择关联已有客户。
  2. 上传明确文件，或通过现有受控公开搜索发现公告；不得绕过网站访问规则或验证码。
  3. Agent 提取招标事实、时间节点、资格/废标条款、评分点、交付物和证据定位，用户确认后进入投标决策。
  4. 用户确认投/不投；投标项目生成任务分解、响应矩阵、章节大纲和责任项。
  5. Agent 只基于已核验招标证据和本地知识生成章节草案，缺失事实明确标记待补充。
  6. 在交付前执行确定性规则检查与 AI 辅助复核，风险逐项确认、豁免或退回修改。
  7. 用户批准冻结版本后生成可编辑 DOCX；本机具备 LibreOffice 时可额外生成 PDF 预览并完成版面检查。
  8. 提交状态、结果和复盘由用户手动登记；系统不自动投递、盖章、签名、报价或访问采购账号。
- Permissions:
  - 查询、解析和草拟可自动执行，但只能读取受控项目目录、已登记来源和当前客户上下文。
  - AI 产生的项目事实、状态迁移、响应矩阵、合规结论和最终文档必须形成冻结写入意图并经过人工 Approval。
  - 用户明确发起的本地文件上传和表单编辑视为用户操作，需提供确认、版本记录以及可恢复删除；任何对外发送和外部系统写入均不在首版范围。
- Data:
  - 独立本地 SQLite 招投标库，不改变现有销售 CSV/SQLite 后端的激活状态；通过稳定 `account_id`/`opportunity_id` 软关联。
  - 核心实体包括投标项目、来源文件、关键日期、要求/评分项、响应项、章节、检查项、风险、决策、版本、产出和审计事件。
  - 正式业务文件、数据库和生成结果默认被 Git 忽略；示例数据必须明确标注且不得包含真实客户信息。
- API Contract:
  - 本机只读 GET API 提供项目列表、项目全景、时间线、检查汇总和统计。
  - 变更 API 必须校验项目版本、允许的状态迁移、请求大小、路径边界和幂等键；AI 写入由受管工具适配器提交，不允许页面伪造 Approval。
  - 所有错误返回稳定中文错误码和可恢复建议，不把异常堆栈、密钥或完整敏感正文返回页面。
- UI States: 招投标首页、项目列表、项目工作区、阶段导航、文件与证据、解读、投标决策、响应矩阵、章节、检查、交付和复盘；每页覆盖加载、空状态、保存中、待确认、阻塞、部分完成、冲突和失败恢复。
- Error States:
  - 文件不支持、文本无法提取、证据缺失、模型不可用、搜索不可用、数据库忙、版本冲突、阶段门禁未通过、文档生成或渲染失败时必须停止对应阶段，不得伪造完成。
  - 资格/废标、报价、日期、签章和最终提交相关结论只能作为辅助检查，不得宣称法律或采购合规保证。
- Compatibility:
  - 现有客户经营、政府合作、研究、知识库、PPT、报销和任务中心行为保持不变。
  - 不复制或派生 BidMaster-Pro、OpenBidKit_Yibiao 的 AGPL-3.0 代码、提示词、模板或资源；仅按公开功能描述进行独立 clean-room 实现，当前仓库继续采用 MIT。
- Performance: 10,000 个投标项目概要或 100,000 条要求/检查记录的本地基准下，项目列表和单项目全景暖缓存 P95 目标不高于 2 秒；大文件解析和文档生成作为可取消的后台受管任务执行。
- Acceptance:
  1. 用户能在不离开销售总监工作台的情况下完成“登记 → 解读 → 决策 → 策划 → 生成 → 检查 → 交付 → 复盘”全流程。
  2. 每个关键招标事实和评分/废标项可追溯到文件、页码或段落；无证据项不得显示为已确认。
  3. 未通过阶段门禁、存在未处置高风险项或未获得最终 Approval 时，系统不能生成“可提交”状态的交付包。
  4. 至少覆盖资格、废标、签字盖章、保证金、有效期、截止时间、响应完整性、评分点覆盖、金额/日期/名称一致性、附件和格式等确定性检查类别；规则版本可追溯。
  5. 生成 DOCX 可编辑、可重新打开；启用 PDF 预览时完成逐页渲染、溢出和空页检查。
  6. Windows 与 macOS CI 通过安装、自检、数据迁移、API、DAG、文档生成和回归测试。
- Verification: ReqGuard 校验；迁移与恢复测试；API 契约测试；DAG/Approval 越权测试；文件路径与大小安全测试；DOCX 结构/渲染测试；UI 静态和交互回归；Windows/macOS 构建。
- Regression: 不允许修改既有销售数据后端指针、自动启动个人微信、自动外发文件、绕过 Approval、扩大本地路径读取范围或使旧服务入口失效。
- Non-goals:
  - 首版不实现多人 RBAC、在线协同编辑、电子签章、采购平台自动登录/投递、自动报价决策、付费 OCR 默认上传、爬虫绕过和“零风险中标”承诺。
  - 首版不引入参考项目的后端服务栈，也不追求与其界面或内部代码兼容。

## Execution Protocol

- Target profile: software-app
- Target expert region: EXP-SW-BID-LIFECYCLE
- Target requirement: REQ-001
- Target module: MOD-001
- Allowed files: `agent_platform/`、`pi/`、`vertical_plugins/`、`profiles/sales-director/`、`ui/`、`contracts/`、`scripts/`、`tests/`、`docs/`、版本与打包清单，以及受控示例文件
- Forbidden changes: 真实 `data/`、`inputs/`、`outputs/` 内容；个人微信；外部自动投递；参考项目 AGPL 代码/资产；现有 stash；销售后端激活状态
- Required tests: TEST-BID-001～010，现有完整 Python/TypeScript/UI/PPT/桌面回归，Windows/macOS CI
- Validation evidence: 测试日志、迁移回执、文档 QA、GitHub Actions 运行记录、安装自检和哈希
- Rollback risk: 新模块使用独立数据库与插件入口；回滚代码不删除招投标数据库和项目文件，旧安装可忽略新增目录
