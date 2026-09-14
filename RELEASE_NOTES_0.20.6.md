# Agent4Market v0.20.6

## 变更摘要

- 新增 macOS 微信进程发现：读取 PID、UID、启动时间、Bundle ID、版本、构建号与架构，并在工作台进程选择器中展示。
- 新增 Mach 只读内存适配，支持微信 macOS 4.1.x（适配档 `wechat-macos-4.1`）；未知 4.x 版本降级为通用实验性扫描。
- 保留数据库首页 HMAC 作为密钥候选的唯一验收条件，避免把未经验证的候选当作密钥。
- 明确 SIP 与 Hardened Runtime 拒绝时的诊断信息，不关闭 SIP、不重签名微信、不提权。
- 完善微信会话导入文档、HTTP 本地身份校验与 UI 展示，并补充单元测试与 UI 测试。

## 验证

- Python WXDecipher/HTTP/本地身份测试：75 项通过，3 项跳过。
- UI WXDecipher 测试：9 项通过。
- `python -m agent_platform validate`、`node --check ui/app.js`、`git diff --check` 通过。
- macOS 跨进程 Mach 读取在系统策略拒绝时按预期跳过；未修改系统安全策略。
