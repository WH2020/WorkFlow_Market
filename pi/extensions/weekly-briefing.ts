export type WeeklyBriefingRow = Record<string, unknown>;

export type WeeklyBriefingInput = {
  period: { start: string; end: string };
  salespeople?: WeeklyBriefingRow[];
  customers?: WeeklyBriefingRow[];
  activities?: WeeklyBriefingRow[];
  resource_requests?: WeeklyBriefingRow[];
  sales_assets?: WeeklyBriefingRow[];
  actions?: WeeklyBriefingRow[];
  risks?: WeeklyBriefingRow[];
};

type Seller = {
  salesperson_id: string;
  name: string;
  source: "roster" | "record";
};

type Evidence = { type: string; id: string };

type ActionItem = {
  kind: "resource" | "action" | "customer_action" | "activity_action";
  text: string;
  account_id: string;
  account_name: string;
  due_at: string;
  evidence: Evidence;
};

const CLOSED_STATUSES = new Set(["completed", "closed", "resolved", "rejected", "cancelled", "canceled", "done", "已完成", "已关闭", "已拒绝", "已取消"]);
const ACTIVE_ASSET_STATUSES = new Set(["active", "approved", "published", "verified", "ready", "有效", "已发布", "已核验", "可用", "已批准"]);
const AUTHORIZED_ASSET_STATUSES = new Set(["approved", "authorized", "permitted", "已授权", "已批准", "可使用"]);

function value(row: WeeklyBriefingRow, ...keys: string[]): string {
  for (const key of keys) {
    const candidate = row[key];
    if (candidate !== undefined && candidate !== null && String(candidate).trim()) return String(candidate).trim();
  }
  return "";
}

function normalized(valueToNormalize: string): string {
  return valueToNormalize.normalize("NFKC").trim().replace(/\s+/gu, " ").toLocaleLowerCase("zh-CN");
}

function isClosed(status: string): boolean {
  return CLOSED_STATUSES.has(normalized(status));
}

function stableRows(rows: WeeklyBriefingRow[], idKeys: string[]): WeeklyBriefingRow[] {
  return [...rows].sort((left, right) => {
    const leftDate = value(left, "occurred_at", "requested_at", "due_at", "deadline", "updated_at", "created_at");
    const rightDate = value(right, "occurred_at", "requested_at", "due_at", "deadline", "updated_at", "created_at");
    if (leftDate !== rightDate) return rightDate.localeCompare(leftDate);
    return value(left, ...idKeys).localeCompare(value(right, ...idKeys));
  });
}

function safeId(valueToCheck: string): boolean {
  return /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/u.test(valueToCheck);
}

function uniqueBy<T>(rows: T[], key: (row: T) => string): T[] {
  const seen = new Set<string>();
  return rows.filter((row) => {
    const identity = key(row);
    if (!identity || seen.has(identity)) return false;
    seen.add(identity);
    return true;
  });
}

export function buildPersonalizedWeeklyBriefing(input: WeeklyBriefingInput): Record<string, unknown> {
  const customers = stableRows(input.customers ?? [], ["customer_id", "account_id"]);
  const activities = stableRows(input.activities ?? [], ["activity_id"]);
  const resources = stableRows(input.resource_requests ?? [], ["request_id"]);
  const assets = stableRows(input.sales_assets ?? [], ["asset_id"]);
  const actions = stableRows(input.actions ?? [], ["action_id"]);
  const risks = stableRows(input.risks ?? [], ["risk_id"]);
  const sellers = new Map<string, Seller>();
  const aliases = new Map<string, string>();
  const rosterNames = new Map<string, string>();

  const addSeller = (salespersonId: string, name: string, source: Seller["source"]): string | undefined => {
    const id = salespersonId.trim();
    if (!safeId(id)) return undefined;
    const existing = sellers.get(id);
    if (!existing) sellers.set(id, { salesperson_id: id, name: name.trim() || id, source });
    const seller = sellers.get(id)!;
    aliases.set(normalized(id), id);
    aliases.set(normalized(seller.name), id);
    return id;
  };

  for (const row of input.salespeople ?? []) {
    const rosterId = value(row, "salesperson_id");
    if (safeId(rosterId)) rosterNames.set(rosterId, value(row, "name") || rosterId);
    const active = row.active === true || normalized(value(row, "active")) === "true" || value(row, "active") === "1";
    if (!active) continue;
    addSeller(rosterId, value(row, "name"), "roster");
  }

  const explicitSalespersonIds = [...activities, ...resources]
    .map((row) => value(row, "salesperson_id"))
    .filter(Boolean)
    .sort();
  for (const salespersonId of explicitSalespersonIds) {
    if (!aliases.has(normalized(salespersonId))) addSeller(salespersonId, rosterNames.get(salespersonId) ?? salespersonId, "record");
  }

  const resolveSeller = (candidate: string): string | undefined => aliases.get(normalized(candidate));
  const customerById = new Map<string, WeeklyBriefingRow>();
  for (const customer of customers) {
    const accountId = value(customer, "customer_id", "account_id");
    if (accountId) customerById.set(accountId, customer);
  }
  const accountName = (accountId: string): string => value(customerById.get(accountId) ?? {}, "customer_name", "name") || accountId || "未关联客户";
  const sellerAccounts = new Map<string, Set<string>>([...sellers.keys()].map((id) => [id, new Set<string>()]));
  const accountSellers = new Map<string, Set<string>>();
  const assignAccount = (sellerId: string | undefined, accountId: string): void => {
    if (!sellerId || !accountId || !sellers.has(sellerId)) return;
    sellerAccounts.get(sellerId)!.add(accountId);
    if (!accountSellers.has(accountId)) accountSellers.set(accountId, new Set());
    accountSellers.get(accountId)!.add(sellerId);
  };

  for (const customer of customers) {
    const accountId = value(customer, "customer_id", "account_id");
    assignAccount(resolveSeller(value(customer, "owner")), accountId);
  }
  for (const row of [...activities, ...resources]) {
    const accountId = value(row, "customer_id", "account_id");
    const explicit = value(row, "salesperson_id");
    assignAccount(resolveSeller(explicit) ?? resolveSeller(value(row, "owner")), accountId);
  }
  for (const row of actions) assignAccount(resolveSeller(value(row, "owner")), value(row, "customer_id", "account_id"));

  const sellerForRow = (row: WeeklyBriefingRow): string | undefined => {
    const explicit = resolveSeller(value(row, "salesperson_id", "owner"));
    if (explicit) return explicit;
    const linked = accountSellers.get(value(row, "customer_id", "account_id"));
    return linked?.size === 1 ? [...linked][0] : undefined;
  };

  const actionItems = new Map<string, ActionItem[]>([...sellers.keys()].map((id) => [id, []]));
  const addAction = (sellerId: string | undefined, item: ActionItem): void => {
    if (!sellerId || !actionItems.has(sellerId) || !item.text) return;
    actionItems.get(sellerId)!.push(item);
  };
  for (const row of resources) {
    const status = value(row, "status");
    if (isClosed(status)) continue;
    const accountId = value(row, "customer_id", "account_id");
    const requestId = value(row, "request_id");
    if (!requestId) continue;
    addAction(sellerForRow(row), {
      kind: "resource", text: `推动资源申请：${value(row, "request_summary") || "未命名申请"}`,
      account_id: accountId, account_name: accountName(accountId), due_at: value(row, "deadline"),
      evidence: { type: "resource_request", id: requestId },
    });
  }
  for (const row of actions) {
    if (isClosed(value(row, "status"))) continue;
    const accountId = value(row, "customer_id", "account_id");
    const actionId = value(row, "action_id");
    if (!actionId) continue;
    addAction(sellerForRow(row), {
      kind: "action", text: value(row, "action_text"), account_id: accountId,
      account_name: accountName(accountId), due_at: value(row, "due_at"), evidence: { type: "action", id: actionId },
    });
  }
  for (const customer of customers) {
    const accountId = value(customer, "customer_id", "account_id");
    const nextAction = value(customer, "next_action");
    if (!nextAction) continue;
    const linked = accountSellers.get(accountId) ?? new Set<string>();
    for (const sellerId of linked) addAction(sellerId, {
      kind: "customer_action", text: nextAction, account_id: accountId, account_name: accountName(accountId),
      due_at: value(customer, "next_action_due"), evidence: { type: "account", id: accountId },
    });
  }
  for (const row of activities) {
    const nextAction = value(row, "next_action");
    const activityId = value(row, "activity_id");
    if (!nextAction || !activityId) continue;
    const accountId = value(row, "customer_id", "account_id");
    addAction(sellerForRow(row), {
      kind: "activity_action", text: nextAction, account_id: accountId, account_name: accountName(accountId),
      due_at: value(row, "next_action_due"), evidence: { type: "activity", id: activityId },
    });
  }

  let filteredAssetCount = 0;
  let unassignedAssetCount = 0;
  const sellerAssets = new Map<string, Array<Record<string, unknown>>>([...sellers.keys()].map((id) => [id, []]));
  for (const row of assets) {
    const assetId = value(row, "asset_id");
    const sourcePath = value(row, "source_path");
    const sourceStatus = normalized(value(row, "source_status"));
    const eligible = Boolean(assetId && sourcePath) && ACTIVE_ASSET_STATUSES.has(normalized(value(row, "status"))) &&
      AUTHORIZED_ASSET_STATUSES.has(normalized(value(row, "authorization_status"))) &&
      (!sourceStatus || sourceStatus === "verified" || sourceStatus === "已核验");
    if (!eligible) { filteredAssetCount += 1; continue; }
    const accountId = value(row, "customer_id", "account_id");
    const linked = accountId ? accountSellers.get(accountId) : undefined;
    const explicitOwner = resolveSeller(value(row, "owner"));
    const targets = linked?.size ? [...linked] : explicitOwner ? [explicitOwner] : [];
    if (targets.length === 0) { unassignedAssetCount += 1; continue; }
    for (const sellerId of targets) sellerAssets.get(sellerId)!.push({
      asset_id: assetId, title: value(row, "title") || assetId, asset_type: value(row, "asset_type"),
      account_id: accountId, account_name: accountId ? accountName(accountId) : "通用资料",
      source_path: sourcePath, evidence: { type: "sales_asset", id: assetId },
    });
  }

  const sellerBriefs = [...sellers.values()].sort((left, right) => left.name.localeCompare(right.name, "zh-CN") || left.salesperson_id.localeCompare(right.salesperson_id)).map((seller) => {
    const accountIds = [...(sellerAccounts.get(seller.salesperson_id) ?? [])].sort();
    const updates = activities.filter((row) => sellerForRow(row) === seller.salesperson_id).map((row) => {
      const accountId = value(row, "customer_id", "account_id");
      return {
        activity_id: value(row, "activity_id"), account_id: accountId, account_name: accountName(accountId),
        occurred_at: value(row, "occurred_at"), channel: value(row, "channel"), activity_type: value(row, "activity_type"),
        summary: value(row, "summary"), evidence: { type: "activity", id: value(row, "activity_id") },
      };
    }).filter((row) => row.activity_id && row.summary).slice(0, 12);
    const resourceNeeds = resources.filter((row) => sellerForRow(row) === seller.salesperson_id && !isClosed(value(row, "status"))).map((row) => {
      const accountId = value(row, "customer_id", "account_id");
      return {
        request_id: value(row, "request_id"), account_id: accountId, account_name: accountName(accountId),
        summary: value(row, "request_summary"), resource_type: value(row, "resource_type"), deadline: value(row, "deadline"),
        status: value(row, "status") || "待处理", evidence: { type: "resource_request", id: value(row, "request_id") },
      };
    }).filter((row) => row.request_id).slice(0, 12);
    const topActions = uniqueBy(actionItems.get(seller.salesperson_id) ?? [], (row) => `${row.evidence.type}:${row.evidence.id}:${row.text}`)
      .sort((left, right) => {
        const rank = (row: ActionItem): number => row.kind === "resource" ? 0 : row.kind === "action" ? 1 : 2;
        return rank(left) - rank(right) || (left.due_at || "9999-12-31").localeCompare(right.due_at || "9999-12-31") || left.text.localeCompare(right.text, "zh-CN");
      }).slice(0, 3);
    return {
      salesperson_id: seller.salesperson_id, name: seller.name, seller_source: seller.source,
      account_count: accountIds.length, account_ids: accountIds, top_actions: topActions,
      account_updates: updates, resource_needs: resourceNeeds,
      recommended_assets: uniqueBy(sellerAssets.get(seller.salesperson_id) ?? [], (row) => String(row.asset_id)).slice(0, 6),
      ...(accountIds.length === 0 ? { welcome_note: "本周期尚未找到可确认归属的客户；请先在销售人员名单、客户负责人或跟进记录中补充对应关系。" } : {}),
    };
  });

  const unmatchedAccounts = customers.filter((row) => {
    const accountId = value(row, "customer_id", "account_id");
    return accountId && !(accountSellers.get(accountId)?.size);
  });
  const touchedAccountIds = new Set(activities.map((row) => value(row, "customer_id", "account_id")).filter(Boolean));
  const openResources = resources.filter((row) => !isClosed(value(row, "status")));
  const needsAttention: Array<Record<string, unknown>> = [];
  for (const row of openResources.slice(0, 10)) needsAttention.push({
    kind: "resource", text: `资源申请待推进：${value(row, "request_summary") || "未命名申请"}`,
    account_id: value(row, "customer_id", "account_id"), due_at: value(row, "deadline"),
    evidence: { type: "resource_request", id: value(row, "request_id") },
  });
  for (const row of risks.filter((risk) => !isClosed(value(risk, "status"))).slice(0, 10)) needsAttention.push({
    kind: "risk", text: value(row, "risk_text"), account_id: value(row, "customer_id", "account_id"),
    evidence: { type: "risk", id: value(row, "risk_id") },
  });
  for (const row of customers.filter((customer) => value(customer, "risks")).slice(0, 10)) needsAttention.push({
    kind: "risk", text: value(row, "risks"), account_id: value(row, "customer_id", "account_id"),
    evidence: { type: "account", id: value(row, "customer_id", "account_id") },
  });

  const messages: string[] = [];
  if (sellerBriefs.length === 0) messages.push("没有找到启用的销售人员，也没有从本周期跟进或资源申请中识别到销售人员编号。请先维护销售人员名单。");
  if (unmatchedAccounts.length) messages.push(`${unmatchedAccounts.length} 个客户无法确认销售归属，已保留在总监视图的“待分配”中，没有自动猜测负责人。`);
  if (filteredAssetCount) messages.push(`${filteredAssetCount} 份销售资料因未授权、状态无效、未核验或缺少真实文件路径，未进入个人推荐。`);
  if (unassignedAssetCount) messages.push(`${unassignedAssetCount} 份有效资料没有明确客户或销售负责人，仅保留在资料库中，未自动分发。`);
  if (activities.length === 0) messages.push("本周期没有已记录的销售跟进；个人简报将只显示现有客户动作和资源事项。");
  if (messages.length === 0) messages.push("销售归属、跟进记录和可推荐资料均通过确定性校验，可以生成个人简报与总监汇总。");
  const status = sellerBriefs.length === 0 && customers.length === 0 && activities.length === 0 && resources.length === 0
    ? "empty" : unmatchedAccounts.length || filteredAssetCount || sellerBriefs.length === 0 ? "limited" : "ready";

  return {
    schema_version: "1.0", mode: "personalized-sales-action-brief", period: input.period,
    manager_rollup: {
      seller_count: sellerBriefs.length, account_count: customers.length, touched_account_count: touchedAccountIds.size,
      open_resource_count: openResources.length,
      seller_summaries: sellerBriefs.map((brief) => ({
        salesperson_id: brief.salesperson_id, name: brief.name, account_count: brief.account_count,
        action_count: brief.top_actions.length, update_count: brief.account_updates.length, resource_count: brief.resource_needs.length,
      })),
      unassigned_accounts: unmatchedAccounts.map((row) => ({
        account_id: value(row, "customer_id", "account_id"), name: value(row, "customer_name", "name"), owner: value(row, "owner"),
      })),
      needs_attention: needsAttention.slice(0, 20),
    },
    seller_briefs: sellerBriefs,
    validation: {
      status, filtered_asset_count: filteredAssetCount, unassigned_asset_count: unassignedAssetCount,
      unmapped_account_count: unmatchedAccounts.length, messages,
    },
  };
}
