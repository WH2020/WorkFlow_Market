# Windows 0.20.2 本机 JSON 请求热修复

热修复标识：`0.20.2-a4-json-20260910`。这是指定旧基线的本机更新，不会重新发布或覆盖 GitHub 上的 0.20.2 安装包。

## 修复范围

- 为意图匹配、接受推荐、忽略推荐、客户信号评估这四个 JSON POST 请求补齐 `Content-Type: application/json`，避免被服务端以 HTTP 415「只接受 JSON 请求」拒绝。
- 修正客户信号加载函数的参数名遮蔽问题，使其能够正常调用 `accountId(row)`。
- 不改变本机用户校验、请求令牌校验或服务端 JSON 限制，不改变 GET 与二进制上传行为。
- 「交给助手」入口仍是已有工作流的意图匹配，不会因此变成自由聊天或模型连通性测试入口。

## 本机更新边界

更新脚本为 `scripts/apply-windows-a4-json-hotfix.py`。它仅接受当前 Windows 用户目录下的 `Agent4Market-0.20.2-portable`，校验固定旧基线后，只替换 `ui/app.js` 与 `runtime/install-manifest.json`。清单中的版本保持 0.20.2，只有前端文件记录和总字节数变化。

更新前必须完全退出工作台。脚本拒绝运行中的应用、非预期文件哈希、不安全目录权限、重解析点、硬链接和未完成的既有事务；不会为了继续更新而关闭进程或调整权限。配置、业务数据、输出与 CLI 登录文件不在更新或读取范围内。

旧版本备份保存在 `runtime/hotfix-backup-0.20.2-a4-json-20260910/before/`，事务日志与完成凭据保存在同一应用的 `runtime/` 下。安装清单最后替换；普通失败自动回滚，异常中断可显式恢复。

在仓库根目录，使用可信的 Windows Python 3.11 或以上解释器执行（不需要安装额外 Python 包）：

```powershell
python -I -B scripts/apply-windows-a4-json-hotfix.py
python -I -B scripts/apply-windows-a4-json-hotfix.py --apply
```

第一条只做预检，不修改应用；确认通过后才执行第二条。需要恢复时，先完全退出工作台，再执行：

```powershell
python -I -B scripts/apply-windows-a4-json-hotfix.py --recover
```

脚本依赖哈希固定的原更新引擎及本地旧包 `outputs/releases/0.20.2-app-updates-hotfix-20260910.zip`。不要手改哈希、复用旧包构建器生成新内容或删除恢复备份。若提示基线不同，应停止并重新核对目标版本。

## 验证记录（2026-09-10，Windows）

- 126 个 Python `unittest` 通过：UI 服务、A4 API、工作流匹配、历史更新包构建守卫、公开 payload 与本次热修复测试。
- 34 个 Node 前端测试通过，`tsc --noEmit` 通过。
- 前端测试执行实际函数，并用标准 `Request` 验证请求媒体类型；服务端测试覆盖四个路由、四种媒体类型及正确/错误令牌，共 32 个协议子场景。
- 本次更新测试覆盖精确的两文件变更、未知基线拒绝、运行中拒绝、替换失败回滚，以及三种中断状态从实际恢复入口连续恢复两次。
- 测试使用合成数据与替身，不调用真实模型，不执行用户业务操作。桌面端重新打开后的人工复测未包含在上述结果中。

通过测试不等于已完成本机部署；实际部署以脚本返回的完成状态、文件哈希和该热修复的完成凭据为准。
