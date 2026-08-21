# Traceability

| Requirement | Profile | Module | Code | Test | Evidence | Status | Notes |
|---|---|---|---|---|---|---|---|
| REQ-001 | software-app | MOD-001 | `agent_platform/bid_store.py`, `agent_platform/bid_migrations/`, `ui/server.py` | TEST-BID-001～005 | `tests/test_bid_store.py`, `tests/test_bid_api.py` | verified | 独立 SQLite、版本写入、API、文件哈希、路径隔离、生命周期和时间线 |
| REQ-001 | software-app | MOD-001 | `pi/extensions/bid-store.ts`, `pi/extensions/task-runtime.ts`, `pi/extensions/data-adapters.ts`, `vertical_plugins/market/bidding/` | TEST-BID-006～007 | `pi/tests/bid-store.test.ts`, `tests/task-runtime.test.ts`, 平台校验 | verified | DAG、Approval、载荷冻结、幂等回执与确定性检查 |
| REQ-001 | software-app | MOD-001 | `pi/extensions/document-artifact.ts`, `pi/artifacts/build-bid-document.mjs`, `pi/artifacts/validate-and-render-document.mjs` | TEST-BID-008 | `pi/tests/bid-document-builder.test.mjs` 与本机 8 页 LibreOffice/PDF.js 逐页 QA | verified | 可编辑 DOCX、来源附录、哈希、无覆盖、真实渲染与稳定重试快照 |
| REQ-001 | software-app | MOD-001 | `ui/index.html`, `ui/app.js`, `ui/styles.css` | TEST-BID-009 | `ui/test_server.py`, `tests/test_bid_api.py`, JavaScript 语法检查 | verified | 项目列表、八阶段导航、上下文自动带入、上传、检查、文件/产物打开和审批卡片 |
| REQ-001 | software-app | MOD-001 | `package.json`, `desktop/src-tauri/`, setup/build scripts and CI | TEST-BID-010 | Python 108/108、UI 50/50、Runtime 90/90、SQLite 门禁、Windows 0.15.0 构建/安装/自检/真实启动 | in_progress | Windows 本机已验证；macOS 与远端 Windows 矩阵仍待 GitHub Actions 验证 |
