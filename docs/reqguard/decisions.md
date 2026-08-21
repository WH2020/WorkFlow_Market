# Decisions

| ID | Status | Decision | Requirements | Modules |
|---|---|---|---|---|
| ADR-BID-001 | accepted | 对两个 AGPL 参考项目只做 clean-room 流程借鉴，不复制代码/提示词/模板/资产，保持主项目 MIT | REQ-001 | MOD-001 |
| ADR-BID-002 | accepted | 首版使用本地轻量架构和独立招投标 SQLite，不引入 Docker/PostgreSQL/Redis/MinIO/Celery/Electron | REQ-001 | MOD-001 |
| ADR-BID-003 | accepted | “全流程”止于本地可审阅交付和手工结果登记，不包含采购平台自动投递、签章或报价决策 | REQ-001 | MOD-001 |
