# Context Map

## CTX-BID 招投标业务域

- Profile: software-app
- Owns: 投标项目、招标文件索引、要求、响应、章节、检查、决策、交付版本和审计事件
- Related expert regions: EXP-SW-BID-LIFECYCLE
- Upstream: 用户上传文件、受控公开搜索、现有知识库、客户/销售机会只读上下文
- Downstream: 受管 Pi 工作流、DOCX/PDF 交付、周报与客户时间线的只读链接
- Forbidden coupling:
  - 不修改销售存储后端激活指针
  - 不将外部文件内容作为系统指令执行
  - 不从页面绕过 Approval 写入 AI 生成事实
  - 不自动发送、盖章、报价或提交采购平台
