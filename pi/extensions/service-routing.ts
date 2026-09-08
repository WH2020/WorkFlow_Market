const PREFIX = "[PRESENTATION_BRIEF]";
const SUFFIX = "[/PRESENTATION_BRIEF]";

function briefFromRequest(request: string): Record<string, unknown> {
  const start = request.indexOf(PREFIX);
  const end = request.indexOf(SUFFIX);
  if (start < 0 || end < start || request.indexOf(PREFIX, start + PREFIX.length) >= 0) {
    throw new Error("内部快速 PPT 需要完整的结构化需求，请在工作台填写");
  }
  const value = JSON.parse(request.slice(start + PREFIX.length, end).trim()) as Record<string, unknown>;
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("演示文稿需求无效");
  return value;
}

export function assertQuickPresentationRequest(request: string): void {
  const brief = briefFromRequest(request);
  if (brief.schema_version !== "1.0" || brief.mode !== "quick" || brief.confidentiality !== "internal" ||
      !["weekly", "industry", "custom"].includes(String(brief.scene)) || brief.source_scope !== "profile-knowledge-only") {
    throw new Error("快速路径仅适用于内部、非政府场景且只使用资料库的 quick 任务");
  }
}

export function routeNewServiceId(serviceId: string, request: string): string {
  if (["sales-review", "industry-research"].includes(serviceId) && /只分析|仅分析|只读|不(?:要|需要)?(?:自动)?(?:更新|写入|入库|保存)/u.test(request)) {
    return `${serviceId}-readonly`;
  }
  if (serviceId === "presentation-studio" && request.includes(PREFIX) && briefFromRequest(request).source_scope === "profile-knowledge-only") {
    assertQuickPresentationRequest(request);
    return "presentation-studio-quick";
  }
  if (serviceId === "presentation-studio-quick") assertQuickPresentationRequest(request);
  return serviceId;
}
