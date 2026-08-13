---
name: manage-sales-pipeline
description: 管理销售客户机会、阶段、健康度、行动、风险、资源申请和销售材料资产。用于客户跟进、销售漏斗分析、重点客户分析、客户档案更新、销售复盘、停滞机会识别、销售材料请求、按买方角色或销售阶段选择材料、管理资源调动和周报数据准备。
---

# 销售管线管理

## 工作流

1. 从 Project 根目录读取 `data/sales/customers.csv`、`activities.csv`、`resource-requests.csv` 和 `sales-assets.csv`；文件缺失时从 Project 根目录运行 `python plugin/market-director-copilot/scripts/init_local_data.py --project .`，从公开空白模板初始化，不覆盖已有数据。
2. 从用户资料或 `$review-sales-conversations` 输出中提取带证据的新事件。
3. 按 `references/pipeline-model.md` 更新阶段和健康度。阶段只能依据可观察事件前进或后退。
4. 为每个重点客户保持一个具体下一步、负责人和截止日期。
5. 资源申请按 `references/resource-request.md` 检查业务理由、时限和预期结果。
6. 涉及销售材料请求、复用或更新时，读取 `references/sales-collateral-routing.md`，按销售阶段、买方角色和使用场景选择资产；生成任务路由到 `$build-market-decks` 或 `$create-office-documents`。
7. 写入前重新读取相关台账并展示拟变更；使用稳定唯一 ID，不并发修改同一文件。发现文件在分析后已变化时停止写入，合并最新内容后重新确认。涉及客户阶段、责任人、资源决策或正式启用销售材料时优先让用户确认。

## 管理输出

- 本周新增、前进、后退和停滞机会。
- 红黄绿机会及其证据。
- 未来 14 天关键动作和到期承诺。
- 需要市场总监或 CEO 决策的资源事项。
- 当前机会所需、可复用、待更新和已过期的销售材料。
- 销售辅导建议，聚焦具体行为和业务影响。

