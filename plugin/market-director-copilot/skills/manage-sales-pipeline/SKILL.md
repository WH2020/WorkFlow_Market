---
name: manage-sales-pipeline
description: 管理销售客户机会、阶段、健康度、行动、风险和资源申请。用于客户跟进、销售漏斗分析、重点客户分析、客户档案更新、销售复盘、停滞机会识别、管理资源调动和周报数据准备。
---

# 销售管线管理

## 工作流

1. 读取 `data/sales/customers.csv`、`activities.csv` 和 `resource-requests.csv`。
2. 从用户资料或 `$review-sales-conversations` 输出中提取带证据的新事件。
3. 按 `references/pipeline-model.md` 更新阶段和健康度。阶段只能依据可观察事件前进或后退。
4. 为每个重点客户保持一个具体下一步、负责人和截止日期。
5. 资源申请按 `references/resource-request.md` 检查业务理由、时限和预期结果。
6. 更新前展示拟变更；涉及客户阶段、责任人或资源决策时优先让用户确认。

## 管理输出

- 本周新增、前进、后退和停滞机会。
- 红黄绿机会及其证据。
- 未来 14 天关键动作和到期承诺。
- 需要市场总监或 CEO 决策的资源事项。
- 销售辅导建议，聚焦具体行为和业务影响。

