# ADR-BID-001 Clean-room 借鉴招投标开源项目

- Status: accepted
- Date: 2026-08-21
- Requirements: REQ-001
- Modules: MOD-001

## Context

用户指定 BidMaster-Pro 与 OpenBidKit_Yibiao 作为功能和流程参考。两个项目当前 `LICENSE` 均为 GNU AGPL v3；BidMaster-Pro README 中一处声称 MIT，但仓库许可证文件和 GitHub 识别均为 AGPL-3.0。Agent4Market 当前采用 MIT 并公开发布。

## Decision

只参考公开可观察的产品能力和通用业务流程，独立定义需求、数据模型、接口、提示边界、UI 和实现。不得复制、翻译改写或移植参考项目的源代码、提示词、Skill、模板、图标、截图、检查规则文本或其他受版权保护资产；不得把它们作为运行时依赖或子模块打包。

可借鉴的通用思想包括：分阶段流水线、阶段门禁、证据化解读、要求/评分响应矩阵、可恢复后台任务、知识复用、生成后检查和可编辑文档交付。这些思想必须按 Agent4Market 已有 Pi、Approval、SQLite、Tauri 和本地安全边界重新实现。

## Alternatives

- 直接集成或 fork：会使当前发布物和网络使用面临 AGPL 对应义务，并引入第二套桌面/服务栈；不采用。
- 将参考项目作为独立本机服务：仍增加安装、端口、数据同步、升级和许可证告知复杂度；首版不采用。
- 仅做外链：不能形成当前 Agent 的客户上下文、审批和周报闭环；不采用。

## Consequences

- 主仓库继续保持 MIT，新增实现可以与现有架构统一测试和发布。
- 不承诺与参考项目 API、数据、模板或功能细节兼容。
- 新增功能必须保留来源调研记录和独立设计证据；许可证判断属于工程风险控制，不构成法律意见。
