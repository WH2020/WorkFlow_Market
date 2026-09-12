(() => {
  let model = null;
  let requestToken = null;
  let selectedProfile = null;
  let selectedService = null;
  let guidedRenderedService = null;
  let modelSettingsInitialized = false;
  let editingModels = new Map();
  let editingModelId = "";
  let discoveredModelOptions = [];
  let providerSettingsInitialized = false;
  let codingAssistantDetection = {};
  let codexModelCatalog = null;
  let codexCatalogLoading = false;
  let codexCatalogAttempt = "";
  let searchSettingsInitialized = false;
  let searchGatewaySettingsInitialized = false;
  let runtimeSettingsInitialized = false;
  let appUpdateState = null;
  let appUpdateRequestBusy = false;
  let appUpdateOpening = false;
  let appUpdateInstalling = false;
  let appUpdatePolling = false;
  let appUpdateLocalError = "";
  let appUpdateAutoEnabled = true;
  let appUpdateLastAutoAttempt = null;
  let mailSettingsInitialized = false;
  let taskRuntimeCatalogKey = "";
  let currentView = "home";
  const viewHistory = [];
  const viewScrollPositions = {};
  let selectedProject = "project-default";
  let noticeTimer = null;
  let activeConfirmDismiss = null;
  let schedulePanelInitialized = false;
  const libraryState = {
    entries: [], trash: [], stats: {}, version: "", expectedRevision: "", error: "", warning: "",
    selectedId: "", category: "", query: "", status: "", projectId: "", accountId: "",
    renderedKey: "", previewKey: "", loading: false, versionTargetId: "", editingId: "",
    specialFilter: "", accounts: [], bids: [],
  };
  let reimbursementMailMessages = [];
  const guidedDrafts = {};
  const guidedNotes = {};
  const taskMessageDrafts = {};
  const taskProgressScroll = {};
  const taskPlanScroll = {};
  const taskExecutionPlanExpansion = new Map();
  const taskWriteIntentState = {};
  const taskCardExpansion = new Map();
  const reimbursementBatchExpansion = new Map();
  let activeTool = "assistant";
  const assistantState = {
    messages: [],
    currentTaskId: null,
    isTyping: false,
  };
  const wechatState = {
    rows: [], selected: new Set(), selectedId: "", messages: [], revision: "", renderedKey: "",
    loading: false, loaded: false, error: "", previewLoading: false, previewGeneration: 0, creating: false,
  };
  const wxdecipherState = { files: [], busy: false, exporting: false, captureLoading: false };
  const wxmediaState = { busy: false, urls: [] };
  const customerState = {
    filters: { query: "", owner: "", region: "", industry: "", stage: "", health: "", updated: "" },
    rows: [], cursor: "", hasMore: false, loading: false, loaded: false, error: "", selectedId: "", detail: null,
    detailLoading: false, detailError: "", timeline: [], timelineCursor: "", timelineHasMore: false,
    timelineLoading: false, timelineError: "", activeTab: "overview",
    listGeneration: 0, detailGeneration: 0, timelineGeneration: 0,
    attention: [], attentionLoaded: false, attentionLoading: false, attentionError: "", attentionGeneration: 0,
    listController: null, detailController: null, timelineController: null, attentionController: null,
    recommendations: [], recommendationsLoading: false, recommendationsError: "", recommendationsGeneration: 0,
    signals: [], signalsLoading: false, signalsError: "", signalsGeneration: 0,
  };
  const bidState = {
    rows: [], dashboard: null, accounts: [], loading: false, loaded: false, error: "", selectedId: "",
    detail: null, timeline: [], detailLoading: false, detailError: "", activeTab: "overview",
    query: "", statuses: "", listController: null, detailController: null, generation: 0,
    detailGeneration: 0, renderedKey: "", detailRenderedKey: "", editingId: "",
  };
  const weeklyState = {
    data: null, periodKey: "", loading: false, error: "", generation: 0,
  };
  let customerRenderedKey = "";
  let customerDetailRenderedKey = "";
  let attentionRenderedKey = "";
  const thinkingLabels = { off: "关闭", minimal: "最少", low: "较低", medium: "标准", high: "深入", xhigh: "极深", max: "最大" };

  function captureTaskComposerFocus() {
    const active = document.activeElement;
    if (!(active instanceof HTMLTextAreaElement) || !active.matches(".task-message-composer textarea")) return null;
    return {
      taskId: active.dataset.taskId || "",
      selectionStart: active.selectionStart,
      selectionEnd: active.selectionEnd,
      selectionDirection: active.selectionDirection,
      scrollTop: active.scrollTop,
    };
  }

  function restoreTaskComposerFocus(snapshot) {
    if (!snapshot?.taskId) return;
    const textarea = [...document.querySelectorAll(".task-message-composer textarea")]
      .find((candidate) => candidate.dataset.taskId === snapshot.taskId);
    if (!textarea) return;
    textarea.focus({ preventScroll: true });
    textarea.setSelectionRange(snapshot.selectionStart, snapshot.selectionEnd, snapshot.selectionDirection);
    textarea.scrollTop = snapshot.scrollTop;
  }

  function confirmAction({
    title,
    message,
    confirmText = "确认",
    cancelText = "取消",
    tone = "primary",
    detail = "",
    inputValue = null,
    inputLabel = "",
    inputMultiline = false,
    inputMaxLength = 120,
    inputPlaceholder = "",
  }) {
    return new Promise((resolve) => {
      activeConfirmDismiss?.(false, true);
      const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      const overlay = document.createElement("div");
      overlay.className = "app-confirm-overlay";
      const dialog = document.createElement("section");
      dialog.className = `app-confirm-dialog ${tone === "danger" ? "danger" : ""}`;
      dialog.setAttribute("role", "alertdialog");
      dialog.setAttribute("aria-modal", "true");
      dialog.setAttribute("aria-labelledby", "app-confirm-title");
      dialog.setAttribute("aria-describedby", "app-confirm-message");
      const header = document.createElement("header");
      const icon = document.createElement("span");
      icon.className = "app-confirm-icon";
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = tone === "danger" ? "!" : "✓";
      const heading = document.createElement("div");
      const eyebrow = document.createElement("small");
      eyebrow.textContent = tone === "danger" ? "请谨慎确认" : "操作确认";
      const titleNode = document.createElement("h2");
      titleNode.id = "app-confirm-title";
      titleNode.textContent = title;
      heading.append(eyebrow, titleNode);
      const close = document.createElement("button");
      close.type = "button";
      close.className = "app-confirm-close";
      close.setAttribute("aria-label", "关闭确认窗口");
      close.textContent = "×";
      header.append(icon, heading, close);
      const body = document.createElement("div");
      body.className = "app-confirm-body";
      const messageNode = document.createElement("p");
      messageNode.id = "app-confirm-message";
      messageNode.textContent = message;
      body.append(messageNode);
      let editor = null;
      if (inputValue !== null) {
        const label = document.createElement("label");
        label.className = "app-confirm-input";
        const caption = document.createElement("strong"); caption.textContent = inputLabel || "填写内容";
        editor = document.createElement(inputMultiline ? "textarea" : "input");
        editor.value = String(inputValue);
        editor.maxLength = inputMaxLength;
        editor.placeholder = inputPlaceholder;
        label.append(caption, editor); body.append(label);
      }
      if (detail) {
        const technical = document.createElement("details");
        technical.className = "app-confirm-detail";
        const summary = document.createElement("summary");
        summary.textContent = "查看校验信息";
        const code = document.createElement("code");
        code.textContent = detail;
        technical.append(summary, code);
        body.append(technical);
      }
      const actions = document.createElement("footer");
      const cancel = document.createElement("button");
      cancel.type = "button";
      cancel.className = "secondary";
      cancel.textContent = cancelText;
      const confirm = document.createElement("button");
      confirm.type = "button";
      confirm.className = tone === "danger" ? "danger" : "primary";
      confirm.textContent = confirmText;
      actions.append(cancel, confirm);
      dialog.append(header, body, actions);
      overlay.append(dialog);
      document.body.append(overlay);
      document.body.classList.add("modal-open");
      let settled = false;
      const dismiss = (accepted, immediate = false) => {
        if (settled) return;
        settled = true;
        activeConfirmDismiss = null;
        overlay.classList.add("closing");
        const finish = () => {
          overlay.remove();
          document.body.classList.remove("modal-open");
          if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
          resolve(inputValue !== null ? (accepted ? editor.value.trim() : null) : Boolean(accepted));
        };
        if (immediate) finish(); else window.setTimeout(finish, 140);
      };
      activeConfirmDismiss = dismiss;
      close.addEventListener("click", () => dismiss(false));
      cancel.addEventListener("click", () => dismiss(false));
      confirm.addEventListener("click", () => dismiss(true));
      overlay.addEventListener("click", (event) => { if (event.target === overlay) dismiss(false); });
      overlay.addEventListener("keydown", (event) => {
        if (event.key === "Escape") { event.preventDefault(); dismiss(false); return; }
        if (event.key !== "Tab") return;
        const focusable = [close, ...(editor ? [editor] : []), cancel, confirm];
        const index = focusable.indexOf(document.activeElement);
        const next = event.shiftKey ? (index <= 0 ? focusable.length - 1 : index - 1) : (index + 1) % focusable.length;
        event.preventDefault();
        focusable[next].focus();
      });
      requestAnimationFrame(() => overlay.classList.add("visible"));
      queueMicrotask(() => (editor || cancel).focus({ preventScroll: true }));
    });
  }

  const viewTitles = {
    home: "工作台", chat: "自由聊天", work: "发起工作", tasks: "任务中心", sales: "客户与销售",
    bids: "智能招投标", knowledge: "资料库", weekly: "销售行动简报", outputs: "输出中心", projects: "项目空间",
    schedules: "每日定时任务", search: "自定义操作", tools: "工具栏", settings: "设置",
  };
  if (viewTitles[window.location.hash.slice(1)]) currentView = window.location.hash.slice(1);

  const staticChineseLabels = new Map([
    ["SALES DIRECTOR · LOCAL", "销售总监 · 本机运行"],
    ["GUIDED WORK", "引导式工作"],
    ["GUIDED TASK", "任务引导"],
    ["SALES PRESENTATION", "销售演示文稿"],
    ["TASK CENTRE", "任务中心"],
    ["CUSTOMERS & SALES", "客户与销售"],
    ["CUSTOMER OPERATIONS", "客户经营"],
    ["KNOWLEDGE BASE", "资料库"],
    ["WEEKLY REPORT", "每周汇报"],
    ["OUTPUT CENTRE", "输出中心"],
    ["PROJECT SPACE", "项目空间"],
    ["DAILY AUTOMATION", "每日自动任务"],
    ["CUSTOM ACTION", "自定义操作"],
    ["QUICK TOOLS", "快捷工具"],
    ["SETTINGS", "系统设置"],
    ["AI 工作台", "智能工作台"],
    ["PPT 工作室", "演示文稿工作室"],
    ["创建销售 PPT", "创建销售演示文稿"],
    ["要做什么 PPT？", "要制作什么演示文稿？"],
    ["开始制作 PPT", "开始制作演示文稿"],
    ["AI 核心", "智能核心"],
    ["显示 AI 核心调试窗口", "显示智能核心调试窗口"],
    ["连接 Brave Search API", "连接公开检索服务"],
    ["Brave Search API Key", "公开检索接口密钥"],
    ["申请 API Key", "申请接口密钥"],
    ["连接 NewAPI / OpenAI 兼容网关", "连接兼容模型网关"],
    ["API Key", "接口密钥"],
    ["已完成、已结束和已替代的任务保留在这里，可按原内容再次创建。", "历史任务默认折叠，可展开查看、再次创建或彻底删除记录。"],
  ]);

  function localizeStaticInterface() {
    document.title = "销售总监智能工作台";
    document.querySelectorAll(".section-kicker").forEach((element) => {
      const translated = staticChineseLabels.get(element.textContent.trim());
      if (translated) element.textContent = translated;
    });
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const tokenReplacements = [
      [/\bAI\b/gu, "智能助手"], [/\bPPT\b/gu, "演示文稿"], [/\bPDF\b/gu, "电子文档"],
      [/\bAPI Key\b/gu, "接口密钥"], [/\bBrave Search\b/gu, "公开检索服务"],
      [/\bWord\b/gu, "文字文档"], [/\bExcel\b/gu, "表格"], [/\bCSV\b/gu, "逗号分隔表格"],
    ];
    let node = walker.nextNode();
    while (node) {
      const value = node.nodeValue.trim();
      const translated = staticChineseLabels.get(value);
      if (translated) node.nodeValue = node.nodeValue.replace(value, translated);
      else tokenReplacements.forEach(([pattern, replacement]) => { node.nodeValue = node.nodeValue.replace(pattern, replacement); });
      node = walker.nextNode();
    }
  }

  const guidedServices = {
    "sales-review": {
      title: "客户推进与销售复盘",
      intro: "确定复盘对象和关注重点，助手会自动检查阶段、关键人、风险、资源和下一步动作。",
      instruction: "结合销售台账复盘客户进展，给出风险排序、资源需求、责任人和下一步动作；拟更新台账时先展示并等待审批。",
      fields: [
        { id: "scope", label: "复盘哪些客户或机会？", type: "text", required: true, placeholder: "例如：A 客户、华东区重点机会，或本周全部重点客户" },
        { id: "focus", label: "这次重点看什么？", type: "select", default: "阶段、风险与下一步动作", options: ["阶段、风险与下一步动作", "停滞原因与解阻动作", "资源缺口与协调事项", "销售过程与赢单复盘"] },
        { id: "changes", label: "最近有什么新变化？（可选）", type: "textarea", placeholder: "例如：客户预算确认；技术负责人要求补充试点方案。" },
      ],
      presets: [
        { label: "本周重点客户", values: { scope: "本周全部重点客户", focus: "阶段、风险与下一步动作" } },
        { label: "找出停滞机会", values: { scope: "近两周没有实质推进的销售机会", focus: "停滞原因与解阻动作" } },
        { label: "汇总资源申请", values: { scope: "当前需要跨部门支持的销售机会", focus: "资源缺口与协调事项" } },
      ],
    },
    "industry-research": {
      title: "客户与行业研究",
      intro: "告诉助手研究对象和用途，它会自动检索公开资料、核验证据并联系当前资料库形成结论。",
      instruction: "先检索和核验来源，再结合销售场景形成结论、机会、风险和建议动作；不确定信息明确标注待验证。",
      fields: [
        { id: "topic", label: "研究谁或什么方向？", type: "text", required: true, placeholder: "例如：某客户所在行业、脑机接口、具身智能或数据采集" },
        { id: "purpose", label: "研究结果用来做什么？", type: "select", default: "支持客户沟通与机会判断", options: ["支持客户沟通与机会判断", "形成内部行业简报", "准备销售方案或演示文稿", "识别竞品、合作方与风险"] },
        { id: "period", label: "优先关注的时间范围", type: "select", default: "近 12 个月，并补充关键历史背景", options: ["近 3 个月", "近 12 个月，并补充关键历史背景", "近 3 年趋势", "不限定，按相关性筛选"] },
      ],
      presets: [
        { label: "客户行业速览", values: { topic: "目标客户所在行业的近期变化与业务机会", purpose: "支持客户沟通与机会判断", period: "近 12 个月，并补充关键历史背景" } },
        { label: "竞品与合作方", values: { topic: "目标方向的主要竞品、合作方与差异化机会", purpose: "识别竞品、合作方与风险", period: "近 12 个月，并补充关键历史背景" } },
        { label: "前沿技术机会", values: { topic: "脑机、具身智能与数据采集方向的商业化进展", purpose: "形成内部行业简报", period: "近 12 个月，并补充关键历史背景" } },
      ],
    },
    "pdf-import": {
      title: "电子文档资料入库",
      intro: "填写已放入受控资料目录的电子文档路径，助手会按页提取、标注来源并生成待审批的知识记录。",
      instruction: "只读取指定的受控目录电子文档；保留页码与文件指纹，提取失败时停止，不把摘要当作已证实事实。",
      fields: [
        { id: "path", label: "电子文档相对路径", type: "text", required: true, placeholder: "例如：inputs/customer-report.pdf" },
        { id: "goal", label: "入库后主要怎么用？", type: "select", default: "提取可引用证据并写入资料库", options: ["提取可引用证据并写入资料库", "分析客户材料并提炼销售机会", "提取政策要点和政府合作依据", "形成文档摘要与待验证问题"] },
        { id: "focus", label: "重点关注（可选）", type: "text", placeholder: "例如：客户业务、预算、试点条件、政策支持或关键数据" },
      ],
      presets: [
        { label: "证据入库", values: { goal: "提取可引用证据并写入资料库" } },
        { label: "分析客户材料", values: { goal: "分析客户材料并提炼销售机会", focus: "客户需求、关键人、预算、时间表与下一步动作" } },
        { label: "提取政策依据", values: { goal: "提取政策要点和政府合作依据", focus: "支持方向、申报条件、主管部门与有效期" } },
      ],
    },
    "government-proposal": {
      title: "政府合作方案",
      intro: "先确定地区和合作方向，助手会结合公开政策、地方条件和内部资源形成可讨论的合作框架。",
      instruction: "形成政府合作方案，覆盖合作价值、参与方、试点路径、资源清单、风险、里程碑和待确认事项；引用政策时保留来源。",
      fields: [
        { id: "region", label: "面向哪个地区或部门？", type: "text", required: true, placeholder: "例如：苏州市、某高新区或当地科技主管部门" },
        { id: "direction", label: "合作方向或项目是什么？", type: "text", required: true, placeholder: "例如：具身智能数据采集基地、脑机接口应用示范" },
        { id: "goal", label: "这版方案要推动什么？", type: "select", default: "形成首轮沟通与试点合作框架", options: ["形成首轮沟通与试点合作框架", "准备政府拜访与会议沟通", "明确可申请政策和资源", "形成正式项目建议书框架"] },
      ],
      presets: [
        { label: "试点合作框架", values: { goal: "形成首轮沟通与试点合作框架" } },
        { label: "政府拜访版本", values: { goal: "准备政府拜访与会议沟通" } },
        { label: "政策资源地图", values: { goal: "明确可申请政策和资源" } },
      ],
    },
    "office-document": {
      title: "销售文件与方案",
      intro: "选择文件类型并说明对象和素材，助手会直接按用途组织结构和初稿，不必从空白文档开始。",
      instruction: "按使用对象和场合生成可审阅的销售文件，明确事实、假设、待确认项和下一步动作；正式文件生成前等待审批。",
      fields: [
        { id: "document", label: "要制作什么文件？", type: "select", default: "客户销售方案", options: ["客户销售方案", "内部资源协调单", "会议纪要与行动清单", "客户沟通邮件或函件", "项目阶段汇报"] },
        { id: "audience", label: "给谁使用或阅读？", type: "text", required: true, placeholder: "例如：客户技术负责人、公司技术团队、总经理办公会" },
        { id: "materials", label: "依据哪些现有信息？", type: "textarea", required: true, placeholder: "粘贴关键事实，或写明要结合的客户、任务、资料库资料和已有文件。" },
      ],
      presets: [
        { label: "客户方案", values: { document: "客户销售方案", audience: "客户业务负责人、技术负责人和决策人" } },
        { label: "资源协调单", values: { document: "内部资源协调单", audience: "销售、产品、技术与交付负责人" } },
        { label: "纪要与行动项", values: { document: "会议纪要与行动清单", audience: "参会人员与相关责任人" } },
      ],
    },
  };

  const $ = (id) => document.getElementById(id);
  const label = {
    waiting_approval: "等待你的审批",
    approval_pending: "正在执行审批",
    approval_stalled: "等待智能核心接管",
    running: "正在处理",
    requested: "等待智能核心接手",
    interrupted: "已中断",
    cancelling: "正在取消",
    resuming: "正在恢复",
    restarting: "正在重新开始",
    superseded: "已替代",
    completed: "已完成",
    cancelled: "已取消",
    rejected: "已驳回",
    failed: "处理失败",
  };

  const note = (message, error = false) => {
    $("notice").textContent = message;
    $("notice").style.color = error ? "#a12b32" : "#066b62";
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => { $("notice").textContent = ""; }, 5000);
  };

  async function api(path, options = {}) {
    const configured = { ...options, headers: { ...(options.headers || {}) } };
    if ((configured.method || "GET").toUpperCase() !== "GET") configured.headers["X-Director-Token"] = requestToken || "";
    const response = await fetch(path, configured);
    const data = await response.json();
    if (!response.ok) throw Object.assign(new Error(data.error || "操作未完成"), { code: data.code });
    return data;
  }

  function currentProfile() { return model.profiles.find((profile) => profile.id === selectedProfile); }
  function currentService() { return currentProfile()?.services.find((service) => service.id === selectedService); }
  function serviceById(serviceId) { return currentProfile()?.services.find((service) => service.id === serviceId); }

  function displayModelName(value) {
    const text = String(value || "").trim();
    if (!text) return "智能核心默认模型";
    return text.includes("/") ? text.slice(text.lastIndexOf("/") + 1) : text;
  }

  function displayTaskRequest(task) {
    const request = String(task?.request || "").trim();
    if (request.startsWith("【微信会话整理】")) {
      const title = request.match(/^整理范围：(.+)$/mu)?.[1] || "已选择的微信会话";
      const dates = request.match(/^日期范围：(.+)$/mu)?.[1] || "已授权日期";
      return `微信会话整理：${title}；${dates}`;
    }
    if (request.startsWith("[PRESENTATION_BRIEF]")) {
      try {
        const end = request.indexOf("[/PRESENTATION_BRIEF]");
        const brief = JSON.parse(request.slice("[PRESENTATION_BRIEF]".length, end).trim());
        return `演示文稿主题：${brief.topic || "未命名主题"}；受众：${brief.audience || "未指定"}；目标：${brief.expected_decision || brief.purpose || "待确认"}`;
      } catch { return "演示文稿制作任务"; }
    }
    if (request.startsWith("[PRESENTATION_PLAN_REVISION]")) return "演示文稿大纲修订任务";
    return request;
  }
  function displayStatus(task) { return task.display_status || task.status || ""; }
  function isHistoricalTask(task) { return ["completed", "cancelled", "rejected", "failed", "superseded"].includes(displayStatus(task)); }
  function projectById(projectId) { return model?.projects?.find((project) => project.project_id === projectId); }
  function selectedProjectRecord() { return projectById(selectedProject) || model?.projects?.[0]; }
  function isPresentationStudio(service = currentService()) { return ["presentation-studio", "presentation-studio-quick"].includes(service?.id) || service?.workflow === "shared.presentation.studio"; }
  function isWeeklyService(service = currentService()) { return service?.id === "weekly-deck" || service?.workflow?.startsWith("shared.reporting.weekly-deck"); }

  function updateBackButton() {
    $("go-back").hidden = viewHistory.length === 0;
    $("go-back").title = viewHistory.length ? `返回${viewTitles[viewHistory.at(-1)] || "上一页"}` : "没有可返回的页面";
  }

  function switchView(view, { record = true, restoreScroll = true } = {}) {
    if (!viewTitles[view]) return;
    const previousView = currentView;
    if (view !== previousView) {
      viewScrollPositions[previousView] = window.scrollY;
      if (record) {
        if (viewHistory.at(-1) !== previousView) viewHistory.push(previousView);
        if (viewHistory.length > 20) viewHistory.shift();
      }
    }
    currentView = view;
    if (window.location.hash !== `#${view}`) window.history.replaceState(null, "", `#${view}`);
    document.querySelectorAll("[data-page]").forEach((page) => page.classList.toggle("active", page.dataset.page === view));
    document.querySelectorAll(".nav-item[data-view]").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
    $("page-title").textContent = viewTitles[view];
    $("sidebar").classList.remove("open");
    if (view === "weekly") {
      const service = serviceById("weekly-deck");
      if (service) selectedService = service.id;
      renderTaskForm();
      loadWeeklyBriefing();
    }
    if (view === "work") renderServices();
    if (view === "bids" && !bidState.loaded && !bidState.loading) loadBids();
    if (view === "tools") {
      renderToolPanels();
      if (activeTool === "wechat" && model?.wechat?.configured && !wechatState.loaded && !wechatState.loading) {
        loadWechatConversations();
      }
    }
    updateBackButton();
    if (view !== previousView) requestAnimationFrame(() => window.scrollTo({ top: restoreScroll ? (viewScrollPositions[view] || 0) : 0, behavior: "auto" }));
  }

  function navigateBack() {
    const previous = viewHistory.pop();
    if (!previous) { updateBackButton(); return; }
    switchView(previous, { record: false, restoreScroll: true });
  }

  function openService(serviceId) {
    if (!serviceById(serviceId)) { note("当前销售总监版本未启用该服务。", true); return; }
    selectedService = serviceId;
    guidedRenderedService = null;
    if (serviceId === "wechat-review") { activeTool = "wechat"; switchView("tools"); }
    else if (serviceId === "weekly-deck") switchView("weekly");
    else switchView("work");
    renderWorkflow();
    renderServices();
    renderTaskForm();
  }

  function choice(title, description, selected) {
    const button = document.createElement("button");
    button.className = `choice ${selected ? "selected" : ""}`;
    const strong = document.createElement("strong");
    strong.textContent = title;
    const small = document.createElement("small");
    small.textContent = description;
    button.append(strong, small);
    return button;
  }

  function renderServices() {
    const box = $("services");
    const services = (currentProfile()?.services || []).filter((service) => !service.id.startsWith("bid-") && service.id !== "wechat-review" && !service.id.endsWith("-readonly") && service.id !== "presentation-studio-quick");
    box.replaceChildren(...services.map((service) => {
      const button = choice(service.display_name, service.description, service.id === selectedService);
      button.onclick = () => openService(service.id);
      return button;
    }));
  }

  function populateModelOptions(models, selectedModel = "") {
    const select = $("model-select");
    const options = [];
    if (!Array.isArray(models) || models.length === 0) {
      const empty = document.createElement("option"); empty.value = ""; empty.textContent = "请填写模型 ID 或发现模型"; options.push(empty);
      select.disabled = true; $("save-model-settings").disabled = true;
    } else {
      models.forEach((modelItem) => {
        const option = document.createElement("option"); option.value = modelItem.id;
        option.textContent = modelItem.owned_by ? `${modelItem.id} · ${modelItem.owned_by}` : modelItem.id;
        options.push(option);
      });
      select.disabled = false; $("save-model-settings").disabled = false;
    }
    select.replaceChildren(...options);
    if (selectedModel && options.some((option) => option.value === selectedModel)) select.value = selectedModel;
  }

  function modelProviders() {
    const settings = model?.model || {};
    return settings.providers || (settings.configured && settings.provider_id ? [{ ...settings, id: settings.provider_id, name: "NewAPI" }] : []);
  }

  const CLI_PROVIDER_TYPES = new Set(["claude-code", "codex-cli"]);

  function providerType(provider) {
    return CLI_PROVIDER_TYPES.has(provider?.vendor) ? provider.vendor
      : CLI_PROVIDER_TYPES.has(provider?.provider) ? provider.provider
        : CLI_PROVIDER_TYPES.has(provider?.provider_type) ? provider.provider_type
          : CLI_PROVIDER_TYPES.has(provider?.api) ? provider.api : "api";
  }

  function isCliProvider(provider) {
    return CLI_PROVIDER_TYPES.has(providerType(provider));
  }

  function apiModelProviders() {
    return modelProviders().filter((provider) => !isCliProvider(provider));
  }

  function cliProvider(type) {
    const settings = model?.model || {};
    const providers = modelProviders().filter((provider) => providerType(provider) === type);
    return providers.find((provider) => settings.default_model?.startsWith(provider.id + "/"))
      || providers.find((provider) => provider.status === "configured") || providers[0];
  }

  function matchingCliProvider(type, detection) {
    const executablePath = detection.executable_path || detection.path;
    return modelProviders().find((provider) => providerType(provider) === type
      && provider.status === "configured" && provider.executable_path === executablePath
      && Boolean(provider.version) && provider.version === detection.version
      && (!provider.launch_sha256 || !detection.launch_sha256 || provider.launch_sha256 === detection.launch_sha256));
  }

  function runnableModelProviders() {
    return modelProviders().filter((provider) => provider.status === "configured" && provider.enabled !== false
      && (isCliProvider(provider) || provider.has_api_key));
  }

  function saveEditedCapabilities() {
    if (!editingModelId) return;
    editingModels.set(editingModelId, {
      ...(editingModels.get(editingModelId) || {}), id: editingModelId, enabled: true,
      context_window: Number($("model-context-window").value), max_tokens: Number($("model-max-tokens").value),
      reasoning: $("model-reasoning").checked, image_input: $("model-image-input").checked,
      tools: $("model-tools").checked, metadata_source: "user",
    });
  }

  function showEditedCapabilities() {
    editingModelId = $("model-select").value;
    const item = editingModels.get(editingModelId) || {};
    $("model-context-window").value = item.context_window || 32000;
    $("model-max-tokens").value = item.max_tokens || 4096;
    $("model-reasoning").checked = Boolean(item.reasoning);
    $("model-image-input").checked = Boolean(item.image_input);
    $("model-tools").checked = item.tools !== false;
  }

  function enabledModelIds() {
    return [...new Set($("model-enabled-ids").value.split(/\s+/).map((value) => value.trim()).filter(Boolean))];
  }

  function refreshEditorModelOptions(preferred = "") {
    const catalog = new Map(discoveredModelOptions.map((item) => [item.id, item]));
    enabledModelIds().forEach((id) => catalog.set(id, editingModels.get(id) || { id }));
    populateModelOptions([...catalog.values()], preferred);
    showEditedCapabilities();
  }

  function fillProviderEditor(id) {
    const settings = model?.model || {};
    const provider = apiModelProviders().find((item) => item.id === id);
    $("model-instance").value = provider?.id || "";
    $("model-provider-name").value = provider?.name || "NewAPI";
    $("model-vendor").value = provider?.vendor || "newapi";
    $("model-base-url").value = provider?.base_url || "";
    $("model-base-url").readOnly = Boolean(provider);
    $("model-api").value = provider?.api || "openai-completions";
    $("model-api").disabled = Boolean(provider);
    $("model-endpoint-options").open = !provider || !["openai", "anthropic"].includes(provider.vendor);
    $("model-private-network").checked = Boolean(provider?.allow_private_network);
    $("model-api-key").value = "";
    $("model-api-key").placeholder = provider?.has_api_key ? "本实例已保存；留空则继续使用" : "请输入本实例 API Key";
    $("model-provider-enabled").checked = provider?.enabled !== false;
    $("model-make-default").checked = !settings.default_model || settings.default_model.startsWith((provider?.id || "") + "/");
    $("reset-model-settings").disabled = !provider;
    editingModels = new Map((provider?.models || []).map((item) => [item.id, { ...item }]));
    discoveredModelOptions = [...(provider?.discovered_models || []), ...(provider?.models || [])];
    editingModelId = "";
    $("model-enabled-ids").value = (provider?.models || []).filter((item) => item.enabled !== false).map((item) => item.id).join("\n");
    const selected = settings.default_model?.startsWith((provider?.id || "") + "/") ? settings.default_model.slice(provider.id.length + 1) : "";
    refreshEditorModelOptions(selected);
    for (const [control, role] of [["model-role-scout", "director-research-scout"], ["model-role-reviewer", "director-readonly-reviewer"]]) {
      const follow = document.createElement("option"); follow.value = ""; follow.textContent = "跟随主任务";
      const options = [follow];
      runnableModelProviders().forEach((item) => {
        (item.models || []).filter((entry) => entry.enabled !== false && entry.tools !== false).forEach((entry) => {
          const option = document.createElement("option"); option.value = item.id + "/" + entry.id;
          option.textContent = item.name + " · " + entry.id; options.push(option);
        });
      });
      $(control).replaceChildren(...options);
      $(control).value = settings.role_models?.[role] || "";
    }
  }

  function renderModelSettings(force = false) {
    const settings = model?.model || { configured: false, status: "unconfigured" };
    const panel = $("model-settings-panel");
    panel.classList.toggle("configured", settings.status === "configured");
    panel.classList.toggle("error", ["error", "unsupported_backend", "missing_default"].includes(settings.status));
    $("model-current").textContent = settings.error || (settings.default_model
      ? "默认：" + settings.default_model + " · " + modelProviders().length + " 个供应商"
      : modelProviders().length ? "请选择一个已启用的默认模型" : "尚未添加模型供应商");
    if (modelSettingsInitialized && !force) return;
    modelSettingsInitialized = true;
    const previous = $("model-instance").value;
    const empty = document.createElement("option"); empty.value = ""; empty.textContent = "新增供应商";
    const options = [empty];
    apiModelProviders().forEach((item) => {
      const option = document.createElement("option"); option.value = item.id;
      option.textContent = item.name + " · " + (item.status === "configured" ? "已配置" : item.status === "disabled" ? "已停用" : "缺少凭据");
      options.push(option);
    });
    $("model-instance").replaceChildren(...options);
    const preferred = [settings.saved_provider_id, previous, settings.provider_id]
      .find((id) => apiModelProviders().some((provider) => provider.id === id)) || "";
    fillProviderEditor(preferred);
  }

  function selectedProviderType() {
    return [...document.querySelectorAll('input[name="provider-type"]')].find((radio) => radio.checked)?.value || "newapi";
  }

  function selectProviderType(type) {
    const selected = CLI_PROVIDER_TYPES.has(type) ? type : "newapi";
    document.querySelectorAll('input[name="provider-type"]').forEach((radio) => { radio.checked = radio.value === selected; });
    $("api-provider-config").hidden = selected !== "newapi";
    $("claude-code-config").hidden = selected !== "claude-code";
    $("codex-cli-config").hidden = selected !== "codex-cli";
    const saved = CLI_PROVIDER_TYPES.has(selected) ? cliProvider(selected) : null;
    const detection = codingAssistantDetection[selected] || {};
    $("save-provider-settings").textContent = selected === "newapi" ? "管理 API 供应商" : "保存并设为默认";
    $("save-provider-settings").disabled = selected !== "newapi" && (!detection.available || detection.backend_available === false || !$(selected + "-path").value.trim());
    if (selected === "codex-cli") {
      $("save-provider-settings").disabled ||= codexCatalogLoading || !codexModelCatalog?.models.some((item) => item.id === $("codex-cli-model").value && item.supported_thinking_levels.includes($("codex-cli-thinking").value));
      if (detection.available && codexCatalogAttempt !== (detection.launch_sha256 || detection.path)) void loadCodexModels();
    }
    $("reset-cli-provider").hidden = selected === "newapi";
    $("reset-cli-provider").disabled = !saved;
  }

  function cliConfiguredModel(provider) {
    if (!provider) return "";
    const settings = model?.model || {};
    if (settings.default_model?.startsWith(provider.id + "/")) return settings.default_model.slice(provider.id.length + 1);
    return provider.selected_model || (provider.models || []).find((item) => item.enabled !== false)?.id || "";
  }

  function renderCodexThinking(preserve = false) {
    const control = $("codex-cli-thinking");
    const record = codexModelCatalog?.models.find((item) => item.id === $("codex-cli-model").value);
    const saved = cliProvider("codex-cli")?.models?.find((item) => item.id === record?.id);
    const previous = preserve ? control.value : "";
    const options = [];
    for (const effort of record?.cli_reasoning.supported_efforts || []) {
      const level = effort === "none" ? "off" : effort;
      const option = document.createElement("option"); option.value = level;
      option.disabled = !record.supported_thinking_levels.includes(level);
      option.textContent = (level === "off" ? "off（关闭思考）" : level) + (option.disabled ? "（当前工作台核心不支持）" : "");
      options.push(option);
    }
    const declared = record?.cli_reasoning.default_effort;
    const desired = previous || saved?.default_thinking_level || (declared === "none" ? "off" : declared);
    if (!record?.supported_thinking_levels.includes(desired)) {
      const placeholder = document.createElement("option"); placeholder.value = ""; placeholder.textContent = "请选择受支持的思考强度";
      options.unshift(placeholder);
    }
    control.replaceChildren(...options);
    control.value = record?.supported_thinking_levels.includes(desired) ? desired : "";
    control.disabled = !record || codexCatalogLoading;
  }

  function renderCodexCatalog(forceSaved = false) {
    const control = $("codex-cli-model");
    const saved = cliConfiguredModel(cliProvider("codex-cli"));
    const desired = (forceSaved ? saved : control.value || saved) || codexModelCatalog?.models.find((item) => item.is_default)?.id || "";
    const options = (codexModelCatalog?.models || []).map((item) => {
      const option = document.createElement("option"); option.value = item.id;
      option.textContent = item.display_name === item.id ? item.id : `${item.display_name} · ${item.id}`;
      option.disabled = item.supported_thinking_levels.length === 0;
      return option;
    });
    if (!options.some((option) => option.value === desired)) {
      const placeholder = document.createElement("option"); placeholder.value = desired;
      placeholder.textContent = desired ? `${desired}（原配置；尚未在目录中确认）` : "请选择目录中的模型";
      options.unshift(placeholder);
    }
    control.replaceChildren(...options); control.value = desired;
    control.disabled = !codexModelCatalog || codexCatalogLoading;
    renderCodexThinking(!forceSaved);
  }

  async function loadCodexModels(force = false) {
    const detection = codingAssistantDetection["codex-cli"] || {};
    const key = detection.launch_sha256 || detection.path;
    if (codexCatalogLoading || !detection.available || (!force && codexCatalogAttempt === key)) return;
    codexCatalogAttempt = key; codexCatalogLoading = true; codexModelCatalog = null;
    $("refresh-codex-models").disabled = true;
    $("codex-cli-catalog-status").textContent = "正在读取隔离的 Codex 模型目录（不会发起模型任务）…";
    renderCodexCatalog(); selectProviderType(selectedProviderType());
    try {
      const response = await api("/api/coding-assistants/models", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: "codex-cli", executable_path: detection.path }) });
      const current = codingAssistantDetection["codex-cli"] || {};
      if ((current.launch_sha256 || current.path) !== key) throw new Error("CLI 已变化，请重新读取模型目录");
      codexModelCatalog = response;
      $("codex-cli-catalog-status").textContent = `已读取 ${response.models.length} 个 CLI 目录模型；未读取登录文件、未验证账号权限。ultra 等核心不支持的档位不可选。`;
    } catch (error) {
      $("codex-cli-catalog-status").textContent = error.message + " 已有模型配置保持不变。";
    } finally {
      codexCatalogLoading = false; $("refresh-codex-models").disabled = false;
      renderCodexCatalog(); selectProviderType(selectedProviderType());
    }
  }

  function renderProviderSettings(force = false) {
    const settings = model?.model || {};
    const apiCount = apiModelProviders().length;
    const cliCount = modelProviders().filter(isCliProvider).length;
    $("provider-current").textContent = settings.error || `Pi 模型执行核心 · ${apiCount} 个 API 供应商 · ${cliCount} 个 CLI 供应商`;
    $("model-provider-panel").classList.toggle("configured", settings.status === "configured");
    $("newapi-status").textContent = apiCount ? `${apiCount} 个已保存实例` : "尚未配置";
    for (const type of CLI_PROVIDER_TYPES) {
      const saved = cliProvider(type);
      const detection = codingAssistantDetection[type] || {};
      const status = $(type + "-status");
      status.classList.toggle("available", Boolean(detection.available));
      status.classList.toggle("unavailable", !detection.available);
      if (saved) status.textContent = detection.available ? "已配置 · 登录状态未验证" : "已配置 · 当前未检测到程序";
      else if (detection.available) status.textContent = detection.backend_available === false
        ? "已安装 · 当前版本暂不支持直接执行"
        : `已安装${detection.version ? ` (${detection.version})` : ""} · 登录状态未验证`;
      else status.textContent = detection.reason || "未检测到";
      if (force || !providerSettingsInitialized) {
        $(type + "-path").value = detection.path || saved?.executable_path || "";
        if (type !== "codex-cli") $(type + "-model").value = cliConfiguredModel(saved);
      } else if (detection.path) {
        $(type + "-path").value = detection.path;
      }
      $(type + "-config-status").textContent = saved
        ? `已保存 ${cliConfiguredModel(saved) || "模型 ID"}；本机 CLI 自行管理登录，工作台未验证当前账号。`
        : "只登记检测到的程序路径和你填写的完整模型 ID；不会读取或验证 CLI 登录账号。";
    }
    renderCodexCatalog(force || !providerSettingsInitialized);
    if (!providerSettingsInitialized || force) {
      const defaultProvider = modelProviders().find((provider) => settings.default_model?.startsWith(provider.id + "/"));
      selectProviderType(providerType(defaultProvider));
    } else {
      selectProviderType(selectedProviderType());
    }
    providerSettingsInitialized = true;
  }

  async function detectCodingAssistants() {
    for (const type of CLI_PROVIDER_TYPES) {
      $(type + "-status").textContent = "检测中…";
      $(type + "-status").classList.remove("available");
      $(type + "-status").classList.add("unavailable");
    }
    try {
      const response = await api("/api/coding-assistants/detect");
      codingAssistantDetection = Object.fromEntries([...CLI_PROVIDER_TYPES].map((type) => [type, response[type] || {}]));
      codexCatalogAttempt = ""; codexModelCatalog = null;
      for (const type of CLI_PROVIDER_TYPES) {
        if (codingAssistantDetection[type].path) $(type + "-path").value = codingAssistantDetection[type].path;
        else if (!cliProvider(type)) $(type + "-path").value = "";
      }
      renderProviderSettings();
      return response;
    } catch (error) {
      codingAssistantDetection = {};
      for (const type of CLI_PROVIDER_TYPES) {
        $(type + "-status").textContent = "检测失败";
        $(type + "-status").classList.remove("available");
        $(type + "-status").classList.add("unavailable");
      }
      selectProviderType(selectedProviderType());
      throw error;
    }
  }

  async function saveProviderSettings() {
    const type = selectedProviderType();
    if (type === "newapi") {
      $("model-settings-panel").open = true;
      $("model-settings-panel").scrollIntoView({ behavior: "smooth", block: "start" });
      $("model-instance").focus();
      return;
    }
    const detection = codingAssistantDetection[type] || {};
    const executablePath = $(type + "-path").value.trim();
    const selectedModel = $(type + "-model").value.trim();
    const status = $(type + "-config-status");
    const selectedThinking = type === "codex-cli" ? $("codex-cli-thinking").value : undefined;
    if (type === "codex-cli" && !codexModelCatalog?.models.some((item) => item.id === selectedModel && item.supported_thinking_levels.includes(selectedThinking))) {
      status.textContent = "请从已读取的目录中选择模型及受支持的思考强度。"; return;
    }
    if (!detection.available || !executablePath) { status.textContent = "请先检测到本机 CLI；程序路径不能手动填写。"; return; }
    if (detection.backend_available === false) { status.textContent = "当前工作台版本尚未提供该 CLI 的直接执行后端。"; return; }
    if (!selectedModel || selectedModel.length > 200 || /\s|[\x00-\x1f\x7f]/u.test(selectedModel)) {
      status.textContent = "请填写不含空白字符的真实完整模型 ID（最多 200 个字符）。";
      return;
    }
    const button = $("save-provider-settings");
    button.disabled = true;
    status.textContent = "正在保存 CLI 模型配置…";
    try {
      const existing = matchingCliProvider(type, detection);
      const response = await api("/api/model-provider-choice", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: type, executable_path: executablePath, selected_model: selectedModel,
          ...(type === "codex-cli" ? { discovery_id: codexModelCatalog.discovery_id, selected_thinking_level: selectedThinking } : {}),
          ...(existing?.id ? { provider_id: existing.id } : {}) }),
      });
      model.model = response.model || response;
      providerSettingsInitialized = false;
      renderModelSettings(true); renderProviderSettings(true); renderTaskRuntimeOptions();
      status.textContent = response.message || "CLI 模型配置已保存；登录由本机 CLI 管理，工作台未验证当前账号。";
      note("CLI 模型已设为新任务默认值；请重启应用或智能核心后再创建任务。不会自动启动真实任务。");
    } catch (error) {
      status.textContent = error.message;
    } finally {
      button.disabled = false;
      selectProviderType(type);
    }
  }

  function renderSearchSettings(force = false) {
    const settings = model?.search || { configured: false, status: "unconfigured" };
    const gateway = model?.search_gateway || { status: "disabled" };
    const gatewayReady = gateway.status === "configured" && !gateway.restart_required;
    const panel = $("search-settings-panel");
    const ready = settings.status === "configured" && !settings.restart_required;
    panel.classList.toggle("configured", ready);
    panel.classList.toggle("error", settings.status === "error");
    if (settings.status === "error") $("search-current").textContent = `配置异常：${settings.error}`;
    else if (settings.restart_required) $("search-current").textContent = "配置已变更 · 关闭并重新打开应用后生效";
    else if (ready && settings.keyless) $("search-current").textContent = "免密公共检索已就绪 · 中文政策与资料发现可用";
    else if (ready) $("search-current").textContent = "专用公开检索已就绪 · 场景化检索可用";
    else $("search-current").textContent = "尚未配置 · 政策检索和公开调研暂不可用";
    $("public-search-status").textContent = ready
      ? gatewayReady
        ? "One Search 聚合网关已就绪；候选来源仍会继续核验正文。"
        : settings.keyless
        ? "免密公共检索已就绪；繁忙时可在设置中填写专用密钥。"
        : "专用公开检索已就绪。"
      : settings.restart_required
        ? "检索密钥已保存，请重启应用后使用。"
        : "公开检索尚未配置，点击后将前往设置。";
    $("start-public-search").textContent = ready ? "发起公开调研" : "配置公开检索";
    if (searchSettingsInitialized && !force) return;
    searchSettingsInitialized = true;
    $("search-api-key").value = "";
    $("search-api-key").placeholder = settings.has_api_key
      ? "已安全保存；留空可重新验证"
      : "可选：粘贴专用公开检索接口密钥";
    $("search-settings-status").textContent = settings.warning
      || (settings.keyless
        ? "当前使用免密共享公共额度；填写专用密钥可提高稳定性，正文仍会单独核验。"
        : "专用密钥只保存在本机；搜索结果仍需读取正文后才能作为证据。");
  }

  function renderSearchGatewayProviderOptions(settings) {
    const container = $("search-gateway-providers");
    const available = Array.isArray(settings.providers) ? settings.providers : [];
    const selected = new Set(Array.isArray(settings.selected_providers) ? settings.selected_providers : []);
    container.replaceChildren();

    const automaticLabel = document.createElement("label");
    automaticLabel.className = "gateway-provider-option automatic";
    const automatic = document.createElement("input");
    automatic.type = "checkbox";
    automatic.id = "search-gateway-provider-auto";
    automatic.checked = selected.size === 0;
    automaticLabel.append(automatic, document.createTextNode("自动使用全部可用来源（推荐）"));
    container.append(automaticLabel);

    available.forEach((provider) => {
      const label = document.createElement("label");
      label.className = "gateway-provider-option";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = provider;
      input.dataset.gatewayProvider = "true";
      input.checked = selected.has(provider);
      label.append(input, document.createTextNode(provider));
      container.append(label);
    });

    const sync = (selectDefault = false) => {
      const providerInputs = [...container.querySelectorAll("input[data-gateway-provider=true]")];
      const single = $("search-gateway-mode").value === "single";
      if (single) {
        automatic.checked = false;
        automatic.disabled = true;
        providerInputs.forEach((input) => { input.disabled = false; });
        const checked = providerInputs.filter((input) => input.checked);
        if (selectDefault && checked.length !== 1 && providerInputs.length) {
          providerInputs.forEach((input, index) => { input.checked = index === 0; });
        } else if (checked.length > 1) {
          checked.slice(1).forEach((input) => { input.checked = false; });
        }
        $("search-gateway-provider-help").textContent = providerInputs.length
          ? "单一来源模式只能选择一个。"
          : "请先用自动聚合完成一次连接，再选择单一来源。";
      } else {
        automatic.disabled = false;
        providerInputs.forEach((input) => { input.disabled = automatic.checked; });
        $("search-gateway-provider-help").textContent = available.length
          ? `已发现 ${available.length} 个可用来源；可自动全选，也可指定多个。`
          : "首次连接默认使用全部可用来源；连接成功后可在这里指定。";
      }
    };
    automatic.onchange = () => sync();
    container.querySelectorAll("input[data-gateway-provider=true]").forEach((input) => {
      input.onchange = () => {
        if ($("search-gateway-mode").value === "single" && input.checked) {
          container.querySelectorAll("input[data-gateway-provider=true]").forEach((other) => {
            if (other !== input) other.checked = false;
          });
        }
      };
    });
    $("search-gateway-mode").onchange = () => sync(true);
    sync();
  }

  function selectedSearchGatewayProviders() {
    const automatic = $("search-gateway-provider-auto");
    if (automatic?.checked) return [];
    return [...$("search-gateway-providers").querySelectorAll("input[data-gateway-provider=true]:checked")]
      .map((input) => input.value);
  }

  function renderSearchGatewaySettings(force = false) {
    const settings = model?.search_gateway || { configured: false, status: "disabled" };
    const panel = $("search-gateway-panel");
    const ready = settings.status === "configured" && !settings.restart_required;
    panel.classList.toggle("configured", ready);
    panel.classList.toggle("error", settings.status === "error" || settings.status === "missing_token");
    if (settings.status === "error") $("search-gateway-current").textContent = `配置异常：${settings.error}`;
    else if (settings.restart_required) $("search-gateway-current").textContent = "配置已变更 · 关闭并重新打开应用后生效";
    else if (ready) $("search-gateway-current").textContent = `已启用 · ${settings.mode === "parallel" ? "自动聚合" : settings.mode === "fallback" ? "稳定优先" : "单一来源"} · 最多 ${settings.max_results} 条`;
    else if (settings.status === "missing_token") $("search-gateway-current").textContent = "检索令牌不可用 · 请重新填写";
    else $("search-gateway-current").textContent = "未启用 · 继续使用原有公开检索";
    if (searchGatewaySettingsInitialized && !force) return;
    searchGatewaySettingsInitialized = true;
    $("search-gateway-url").value = settings.base_url || "";
    $("search-gateway-token").value = "";
    $("search-gateway-token").placeholder = settings.has_token
      ? "已安全保存；地址不变时可留空重新验证"
      : "只填写 osr_ 开头的业务令牌；不要填写 oak_ 管理凭据";
    $("search-gateway-mode").value = settings.mode || "parallel";
    $("search-gateway-max-results").value = String(settings.max_results || 8);
    $("search-gateway-private-network").checked = Boolean(settings.allow_private_network);
    renderSearchGatewayProviderOptions(settings);
    $("search-gateway-status").textContent = settings.status === "error"
      ? settings.error
      : ready
        ? `后续公开检索优先使用 ${settings.base_url}；已发现 ${(settings.providers || []).length} 个提供商。`
        : "Agent4Market 不会安装或管理 One Search 服务本身。";
  }

  function publicSearchReady(serviceId, guide = false) {
    if (!["industry-research", "industry-research-readonly", "government-proposal", "presentation-studio", "bid-discovery"].includes(serviceId)) return true;
    const settings = model?.search || {};
    const gateway = model?.search_gateway || {};
    const gatewayHealthy = !gateway.status || ["disabled", "configured"].includes(gateway.status);
    if (settings.status === "configured" && !settings.restart_required && !gateway.restart_required && gatewayHealthy) return true;
    if (guide) {
      switchView("settings");
      if (gateway.restart_required) $("search-gateway-panel").open = true;
      else { $("search-settings-panel").open = true; $("search-api-key").focus(); }
    }
    return false;
  }

  function renderTaskRuntimeOptions() {
    const settings = model?.model || { configured: false, status: "unconfigured" };
    const providers = runnableModelProviders();
    const available = providers.flatMap((provider) => (provider.models || []).filter((item) => item.enabled !== false && item.tools !== false).map((item) => ({ ...item, provider })));
    const catalogKey = JSON.stringify([settings.default_model, available.map((item) => [item.provider.id, item.id, item.cli_reasoning, item.default_thinking_level])]);
    if (catalogKey !== taskRuntimeCatalogKey) {
      taskRuntimeCatalogKey = catalogKey;
      const previous = $("task-model").value;
      let remembered = "";
      try { remembered = localStorage.getItem("agent4market.taskModel") || ""; } catch { /* Local storage is optional. */ }
      const options = [];
      const fallback = document.createElement("option");
      fallback.value = "";
      fallback.textContent = settings.default_model
        ? `默认：${settings.default_model}`
        : "尚未设置默认模型，请选择实例模型";
      options.push(fallback);
      providers.forEach((provider) => {
        const group = document.createElement("optgroup"); group.label = provider.name || provider.id;
        available.filter((item) => item.provider.id === provider.id).forEach((item) => {
          const option = document.createElement("option"); option.value = `${provider.id}/${item.id}`;
          option.textContent = item.id; group.append(option);
        });
        if (group.children.length) options.push(group);
      });
      $("task-model").replaceChildren(...options);
      const desired = previous || remembered;
      if (desired && [...$("task-model").options].some((option) => option.value === desired)) $("task-model").value = desired;
      $("task-model").disabled = available.length === 0;
    }
    if (!$("task-thinking").dataset.initialized) {
      let rememberedThinking = "";
      try { rememberedThinking = localStorage.getItem("agent4market.taskThinking") || ""; } catch { /* Local storage is optional. */ }
      if ([...$("task-thinking").options].some((option) => option.value === rememberedThinking)) {
        $("task-thinking").value = rememberedThinking;
      }
      $("task-thinking").dataset.initialized = "true";
      $("task-model").onchange = () => {
        try { localStorage.setItem("agent4market.taskModel", $("task-model").value); } catch { /* Local storage is optional. */ }
        renderTaskThinkingOptions();
      };
      $("task-thinking").onchange = () => {
        try { localStorage.setItem("agent4market.taskThinking", $("task-thinking").value); } catch { /* Local storage is optional. */ }
      };
    }
    renderTaskThinkingOptions();
  }

  function renderTaskThinkingOptions() {
    const key = $("task-model").value || model?.model?.default_model;
    const provider = modelProviders().find((item) => key?.startsWith(item.id + "/"));
    const record = provider?.models.find((item) => `${provider.id}/${item.id}` === key);
    const levels = isCliProvider(provider)
      ? record?.cli_reasoning ? ["off", "minimal", "low", "medium", "high", "xhigh", "max"].filter((level) => record.cli_reasoning.supported_efforts.includes(level === "off" ? "none" : level)) : ["off"]
      : ["off", "minimal", "low", "medium", "high", "xhigh", "max"];
    const defaultLevel = isCliProvider(provider) ? record?.default_thinking_level || "off" : "";
    const control = $("task-thinking"); const catalogKey = JSON.stringify([key, levels, defaultLevel]);
    if (control.dataset.catalogKey === catalogKey) return;
    const previous = control.value;
    const fallback = document.createElement("option"); fallback.value = "";
    fallback.textContent = defaultLevel ? `模型默认：${defaultLevel}` : "默认思考强度";
    control.replaceChildren(fallback, ...levels.map((level) => {
      const option = document.createElement("option"); option.value = level; option.textContent = level === "off" ? "off（关闭思考）" : level; return option;
    }));
    control.value = levels.includes(previous) ? previous : "";
    control.dataset.catalogKey = catalogKey;
    if (previous && !levels.includes(previous)) {
      try { localStorage.removeItem("agent4market.taskThinking"); } catch { /* Optional. */ }
      note("所选模型不支持之前的思考强度，已清除该选择；提交前可重新选择，留空使用显示的模型默认值。");
    }
  }

  function taskRuntimeSelection() {
    const requestedModel = $("task-model").value;
    const requestedThinking = $("task-thinking").value;
    return {
      ...(requestedModel ? { requested_model: requestedModel } : {}),
      ...(requestedThinking ? { requested_thinking_level: requestedThinking } : {}),
    };
  }

  function syncAppUpdateState(snapshot) {
    if (!snapshot || typeof snapshot !== "object") return;
    const baseline = appUpdateState?.instance_id === snapshot.instance_id && appUpdateState.revision > snapshot.revision ? appUpdateState : snapshot;
    if (!appUpdateState || appUpdateState.instance_id !== snapshot.instance_id || appUpdateState.revision < snapshot.revision) appUpdateLocalError = "";
    const installation = appUpdateState?.instance_id === snapshot.instance_id &&
      (appUpdateState.installation?.revision ?? -1) > (snapshot.installation?.revision ?? -1)
      ? appUpdateState.installation : snapshot.installation;
    appUpdateState = { ...baseline, installation };
  }

  function appUpdateTime(value) {
    const date = value ? new Date(value) : null;
    return date && Number.isFinite(date.getTime()) ? date.toLocaleString("zh-CN", { hour12: false }) : "尚未检查";
  }

  function renderAppUpdates() {
    syncAppUpdateState(model?.app_updates);
    const state = appUpdateState || { status: "not_checked", current_version: "unknown" };
    const busy = appUpdateRequestBusy || state.checking;
    const labels = { not_checked: "尚未检查更新", checking: "正在检查 GitHub 正式版本…",
      up_to_date: "已是当前发布的最新版本", available: `发现新版本 ${state.latest?.version || ""}`,
      current_ahead: "本机版本高于当前公开发布版本，不需要降级", error: state.error?.message || "检查更新失败，请重试。" };
    $("app-update-current-version").textContent = `当前版本 ${state.current_version === "unknown" ? "未知" : state.current_version}`;
    $("app-update-status").textContent = appUpdateLocalError || (appUpdateRequestBusy ? labels.checking : labels[state.status] || labels.not_checked);
    $("app-update-status").classList.toggle("error", Boolean(appUpdateLocalError) || state.status === "error");
    $("app-update-status").classList.toggle("available", state.update_available === true);
    $("app-update-checked-at").textContent = state.checked_at
      ? `最近检查：${appUpdateTime(state.checked_at)}${state.stale ? ` · 以下为上次成功检查的信息（${appUpdateTime(state.last_success_at)}）` : ""}${state.status === "error" && appUpdateAutoEnabled ? " · 自动重试已延后，避免频繁请求" : ""}`
      : "仅检查公共发布信息，不上传用户配置或业务数据。";
    $("app-updates-badge").hidden = !state.update_available;
    $("app-updates-button-label").textContent = state.update_available ? "发现新版本" : busy ? "正在检查更新" : "检查更新";
    $("app-updates-button").classList.toggle("has-update", state.update_available === true);
    $("app-update-release").hidden = !state.latest;
    $("app-update-release-name").textContent = state.latest ? `${state.latest.name} · ${state.latest.version}` : "";
    $("app-update-release-date").textContent = state.latest ? `发布时间：${appUpdateTime(state.latest.published_at)}${state.stale ? " · 缓存信息" : ""}` : "";
    $("app-update-release-notes").textContent = state.latest ? `${state.latest.notes || "发布者未填写更新说明。"}${state.latest.notes_truncated ? "\n\n更新说明较长，完整内容请查看发布页。" : ""}` : "";
    $("app-update-check").disabled = Boolean(busy) || state.retry_after_seconds > 0;
    $("app-update-check").textContent = busy ? "正在检查…" : state.retry_after_seconds > 0 ? `${state.retry_after_seconds} 秒后可重试` : "立即检查";
    $("app-update-open-release").disabled = !state.can_open_release || appUpdateOpening;
    $("app-update-open-release").textContent = appUpdateOpening ? "正在打开…" : "查看发布页";
    $("app-update-auto").checked = appUpdateAutoEnabled;
    const installation = state.installation || {};
    const updating = ["confirming", "downloading", "staging", "verifying", "ready", "installing", "starting"].includes(installation.phase);
    const phases = { confirming: "请在 macOS 系统确认框中批准本次程序更新…", downloading: "正在下载并校验更新包…", staging: "正在隔离目录准备新版，当前程序未被替换…",
      verifying: "正在核验新版程序文件…", ready: "准备完成，即将安全退出、更新并重启…", installing: "正在更新程序；窗口会暂时关闭并重新打开。",
      starting: "正在确认新版窗口和智能核心启动；完成前暂不开放业务操作。",
      cancelled: "更新已取消，当前程序和数据未改动。" };
    let installationText = installation.error?.message || phases[installation.phase];
    if (!installationText && installation.previous_result?.status === "rolled_back") installationText = `上次更新未完成，已恢复 ${installation.previous_result.from_version}；配置和数据仍在原目录。`;
    if (!installationText && installation.previous_result?.status === "complete") installationText = `已更新到 ${installation.previous_result.to_version}；配置和业务数据未迁移。`;
    const updateAssets = installation.platform === "macos" ? state.latest?.macos_assets : state.latest?.windows_assets;
    $("app-update-install-status").textContent = installationText || installation.unavailable_reason ||
      (state.update_available && !updateAssets ? "此发布没有可校验的当前平台更新附件，请查看发布页。" : "确认后下载并校验，仅更新程序；配置和业务数据保留在原目录。首次使用此功能仍需手动安装支持一键更新的版本。");
    $("app-update-install").disabled = !installation.can_start || appUpdateInstalling || Boolean(busy);
    $("app-update-install").textContent = updating ? "正在更新…" : "一键更新并重启";
    $("app-update-cancel").hidden = !installation.can_cancel;
    $("app-update-cancel").disabled = appUpdateInstalling;
    $("app-update-progress").hidden = installation.phase !== "downloading";
    $("app-update-progress").value = installation.total_bytes ? Math.min(100, Math.round(installation.received_bytes / installation.total_bytes * 100)) : 0;
    if (updating) $("app-update-check").disabled = true;
  }

  async function installAppUpdate() {
    const tag = appUpdateState?.latest?.tag;
    if (!tag || !appUpdateState?.installation?.can_start || appUpdateInstalling) return;
    appUpdateInstalling = true; renderAppUpdates();
    try {
      const confirmed = await confirmAction({ title: "更新程序并重启", message: `下载并安装 ${appUpdateState.latest.version}？仅更新程序，配置、密钥和业务数据保留在原目录。准备完成后工作台会退出并重新启动，请先保存所需的临时聊天。备份和暂存文件暂不自动清理，会占用额外磁盘空间。`,
        detail: "安装或启动校验失败时尝试恢复旧程序；遇到未知文件改动、备份损坏或程序仍占用，会停止并保留恢复材料。请先完成任务并停止生成。准备期间不能提交其他操作；不会自动安装以后的版本。", confirmText: "确认更新并重启" });
      if (!confirmed) return;
      const response = await api("/api/app-updates/install", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tag, confirmed: true }) });
      syncAppUpdateState(response);
      $("app-update-action-status").textContent = "已开始更新准备。";
    } catch (error) { $("app-update-action-status").textContent = `未开始更新：${error.message}`; }
    finally { appUpdateInstalling = false; renderAppUpdates(); }
  }

  async function cancelAppUpdate() {
    if (!appUpdateState?.installation?.can_cancel || appUpdateInstalling) return;
    appUpdateInstalling = true;
    try {
      syncAppUpdateState(await api("/api/app-updates/cancel", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }));
      $("app-update-action-status").textContent = "已请求取消，正在等待当前准备步骤停止；当前程序未改动。";
    } catch (error) { $("app-update-action-status").textContent = error.message; }
    finally { appUpdateInstalling = false; renderAppUpdates(); }
  }

  async function pollAppInstallation() {
    if (appUpdatePolling || !["confirming", "downloading", "staging", "verifying", "ready", "installing", "starting"].includes(appUpdateState?.installation?.phase)) return;
    appUpdatePolling = true;
    try {
      syncAppUpdateState(await api("/api/app-updates"));
      renderAppUpdates();
    } catch {
      $("app-update-install-status").textContent = "工作台连接已断开；若正在切换程序，请等待新版重新打开。若未重新打开，请从原快捷方式启动以恢复。";
    } finally { appUpdatePolling = false; }
  }

  async function checkAppUpdates(manual = false) {
    if (!requestToken || appUpdateRequestBusy || appUpdateState?.checking || appUpdateState?.retry_after_seconds > 0) return;
    if (!manual && (!appUpdateAutoEnabled || appUpdateState?.auto_retry_after_seconds > 0 || (appUpdateLastAutoAttempt !== null && Date.now() - appUpdateLastAutoAttempt < 300000))) return;
    appUpdateLastAutoAttempt = Date.now();
    appUpdateRequestBusy = true; appUpdateLocalError = "";
    renderAppUpdates();
    try {
      const response = await api("/api/app-updates/check", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ manual }) });
      syncAppUpdateState(response);
    } catch (error) {
      appUpdateLocalError = `无法检查更新：${error.message}`;
    } finally { appUpdateRequestBusy = false; renderAppUpdates(); }
  }

  async function openAppUpdateRelease() {
    const tag = appUpdateState?.latest?.tag;
    if (!tag || !appUpdateState.can_open_release || appUpdateOpening) return;
    appUpdateOpening = true; renderAppUpdates();
    try {
      const confirmed = await confirmAction({ title: "获取 Agent4Market 新版", message: `在系统浏览器中打开 ${appUpdateState.latest.version} 的 GitHub 发布页？这一步不会自动下载、安装或重启。`,
        detail: "请按发布说明操作，不要覆盖正在运行的程序；当前任务和数据保持不变。", confirmText: "打开发布页" });
      if (!confirmed) return;
      const response = await api("/api/app-updates/open-release", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tag }) });
      $("app-update-action-status").textContent = response.message;
    } catch (error) { $("app-update-action-status").textContent = error.message; }
    finally { appUpdateOpening = false; renderAppUpdates(); }
  }

  function initializeAppUpdates() {
    const settings = document.querySelector('[data-page="settings"]');
    const template = $("app-update-panel-template");
    settings.insertBefore(template.content.cloneNode(true), $("runtime-settings-panel"));
    try { appUpdateAutoEnabled = localStorage.getItem("agent4market-auto-updates") !== "false"; } catch { appUpdateAutoEnabled = true; }
    $("app-updates-button").onclick = () => {
      switchView("settings"); $("app-update-panel").scrollIntoView({ behavior: "smooth", block: "start" });
      checkAppUpdates(true);
    };
    $("app-update-check").onclick = () => checkAppUpdates(true);
    $("app-update-open-release").onclick = openAppUpdateRelease;
    $("app-update-install").onclick = installAppUpdate;
    $("app-update-cancel").onclick = cancelAppUpdate;
    $("app-update-auto").onchange = () => {
      appUpdateAutoEnabled = $("app-update-auto").checked;
      try { localStorage.setItem("agent4market-auto-updates", String(appUpdateAutoEnabled)); } catch { /* Session preference still applies. */ }
      if (appUpdateAutoEnabled) checkAppUpdates(false);
    };
    document.addEventListener("visibilitychange", () => { if (!document.hidden) checkAppUpdates(false); });
    window.addEventListener("online", () => checkAppUpdates(false));
    setInterval(() => checkAppUpdates(false), 60000);
    setInterval(pollAppInstallation, 2000);
  }

  function renderRuntimeSettings(force = false) {
    const runtime = model?.desktop_runtime || { status: "offline", label: "智能核心未连接", show_ai_core_window: false, log_tail: [] };
    const top = $("runtime-status");
    top.classList.toggle("offline", runtime.status === "offline");
    top.classList.toggle("working", runtime.status === "working");
    top.querySelector("b").textContent = runtime.label || "智能核心未连接";
    $("runtime-settings-status").textContent = runtime.heartbeat_at
      ? `${runtime.label} · 最近心跳 ${new Date(runtime.heartbeat_at).toLocaleTimeString("zh-CN", { hour12: false })}`
      : runtime.label;
    $("runtime-mode-badge").textContent = runtime.show_ai_core_window ? "独立调试窗口" : "嵌入运行";
    const lines = Array.isArray(runtime.log_tail) ? runtime.log_tail : [];
    $("ai-core-log").textContent = lines.length ? lines.join("\n") : "暂无运行记录。";
    if (runtimeSettingsInitialized && !force) return;
    runtimeSettingsInitialized = true;
    $("show-ai-core-window").checked = Boolean(runtime.show_ai_core_window);
  }

  function applyMailProviderPreset(force = false) {
    const settings = model?.mail || {};
    const preset = settings.presets?.[$("mail-provider").value] || {};
    const custom = $("mail-provider").value === "custom";
    $("mail-host").readOnly = !custom;
    if (!custom && (force || !$("mail-host").value)) $("mail-host").value = preset.host || "";
    $("mail-credential").placeholder = settings.configured
      ? `已安全保存；留空可继续使用（${preset.credential_label || "客户端授权码"}）`
      : `填写${preset.credential_label || "客户端授权码或应用专用密码"}，不要填写网页登录密码`;
  }

  function renderMailSettings(force = false) {
    const settings = model?.mail || { configured: false, status: "unconfigured", presets: {} };
    const panel = $("mail-settings-panel");
    panel.classList.toggle("configured", settings.configured && settings.status === "configured");
    panel.classList.toggle("error", settings.status === "error");
    if (settings.status === "error") $("mail-settings-current").textContent = `配置异常：${settings.error}`;
    else if (settings.configured) $("mail-settings-current").textContent = `已连接 ${settings.provider_label || "邮箱"} · ${settings.email_address}`;
    else $("mail-settings-current").textContent = "尚未连接邮箱";
    $("expense-mail-unconfigured").hidden = Boolean(settings.configured);
    $("expense-mail-workspace").hidden = !settings.configured;
    if (mailSettingsInitialized && !force) return;
    mailSettingsInitialized = true;
    $("mail-provider").value = settings.provider || "qq";
    $("mail-email-address").value = settings.email_address || "";
    $("mail-username").value = settings.username || "";
    $("mail-username").dataset.auto = !settings.username || settings.username === settings.email_address ? "true" : "false";
    $("mail-host").value = settings.host || "";
    $("mail-private-network").checked = Boolean(settings.allow_private_network);
    $("mail-credential").value = "";
    $("mail-settings-status").textContent = settings.configured
      ? "授权码已由本机系统保护；留空保存可重新验证现有连接。"
      : "连接时会进行一次只读登录验证。";
    applyMailProviderPreset(true);
  }

  function guidedControl(field, draft) {
    const control = document.createElement(field.type === "select" ? "select" : field.type === "textarea" ? "textarea" : "input");
    control.id = `guided-${field.id}`;
    control.dataset.fieldId = field.id;
    if (control.tagName === "INPUT") control.type = "text";
    if (field.type !== "select") control.maxLength = field.maxLength || (field.type === "textarea" ? 800 : 240);
    if (field.placeholder) control.placeholder = field.placeholder;
    if (field.type === "select") field.options.forEach((value) => {
      const option = document.createElement("option"); option.value = value; option.textContent = value; control.append(option);
    });
    if (draft[field.id] === undefined) draft[field.id] = field.default || "";
    control.value = draft[field.id];
    control.addEventListener("input", () => { draft[field.id] = control.value; });
    return control;
  }

  function renderGuidedForm(force = false) {
    const baseService = selectedService.replace(/-readonly$/u, "");
    const config = guidedServices[baseService];
    if (!config || (!force && guidedRenderedService === selectedService)) return;
    $("task-readonly-option").hidden = !["sales-review", "industry-research"].includes(baseService);
    guidedRenderedService = selectedService;
    const draft = guidedDrafts[selectedService] ||= {};
    $("task-form-title").textContent = `2. ${config.title}`;
    $("task-form-intro").textContent = config.intro;
    $("request-notes").value = guidedNotes[selectedService] || "";
    const fields = config.fields.map((field) => {
      const labelElement = document.createElement("label");
      labelElement.textContent = `${field.label}${field.required ? " *" : ""}`;
      labelElement.append(guidedControl(field, draft));
      return labelElement;
    });
    $("guided-fields").replaceChildren(...fields);
    const prompts = config.presets.map((preset) => {
      const button = document.createElement("button");
      button.type = "button"; button.className = "prompt-chip"; button.textContent = preset.label;
      button.onclick = () => { Object.assign(draft, preset.values); renderGuidedForm(true); };
      return button;
    });
    $("quick-prompts").replaceChildren(...prompts);
  }

  function formatDate(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }

  function currentWeek() {
    const today = new Date();
    const weekday = today.getDay() || 7;
    const monday = new Date(today); monday.setHours(0, 0, 0, 0); monday.setDate(today.getDate() - weekday + 1);
    const friday = new Date(monday); friday.setDate(monday.getDate() + 4);
    return { start: formatDate(monday), end: formatDate(friday) };
  }

  function weeklyEmpty(text) {
    const empty = document.createElement("div");
    empty.className = "weekly-empty";
    empty.textContent = text;
    return empty;
  }

  function weeklyEvidence(item) {
    const evidence = item?.evidence;
    if (!evidence?.type || !evidence?.id) return "";
    const types = { activity: "跟进", resource_request: "资源申请", action: "行动", account: "客户", risk: "风险", sales_asset: "销售资料" };
    return `${types[evidence.type] || "来源"} ${evidence.id}`;
  }

  function weeklyItemList(title, rows, formatter, emptyText) {
    const section = document.createElement("section");
    section.className = "weekly-card-section";
    const heading = document.createElement("h4"); heading.textContent = title; section.append(heading);
    if (!rows?.length) { const hint = document.createElement("p"); hint.className = "hint"; hint.textContent = emptyText; section.append(hint); return section; }
    const list = document.createElement("ul");
    rows.forEach((row) => {
      const item = document.createElement("li");
      const main = document.createElement("span"); main.textContent = formatter(row); item.append(main);
      const source = weeklyEvidence(row);
      const meta = [row.account_name, row.due_at || row.deadline, source].filter(Boolean).join(" · ");
      if (meta) { const small = document.createElement("small"); small.textContent = meta; item.append(small); }
      list.append(item);
    });
    section.append(list); return section;
  }

  function renderWeeklyBriefing() {
    const data = weeklyState.data;
    const badge = $("weekly-validation-badge");
    const status = $("weekly-preview-status");
    const metrics = $("weekly-manager-rollup");
    const sellerList = $("weekly-seller-briefs");
    const validationList = $("weekly-validation-messages");
    const unassignedList = $("weekly-unassigned-accounts");
    if (weeklyState.loading) {
      badge.textContent = "正在读取"; badge.className = "period-badge loading";
      status.textContent = "正在按销售人员、客户和来源记录整理本周数据…";
      metrics.replaceChildren(); sellerList.replaceChildren(weeklyEmpty("正在生成本地预览…"));
      validationList.replaceChildren(); unassignedList.replaceChildren(); return;
    }
    if (weeklyState.error || !data) {
      badge.textContent = "读取失败"; badge.className = "period-badge error";
      status.textContent = weeklyState.error || "暂时无法读取本周数据。";
      metrics.replaceChildren(); sellerList.replaceChildren(weeklyEmpty("请检查本地销售资料后重试。"));
      validationList.replaceChildren(); unassignedList.replaceChildren(); return;
    }
    const rollup = data.manager_rollup || {};
    const validation = data.validation || {};
    const badgeLabels = { ready: "数据就绪", limited: "部分可用", empty: "等待数据" };
    badge.textContent = badgeLabels[validation.status] || "已读取";
    badge.className = `period-badge ${validation.status || ""}`;
    status.textContent = `${data.period?.start || ""} 至 ${data.period?.end || ""} · ${data.source?.note || "本地只读预览"}`;
    const metricDefinitions = [
      ["销售人员", rollup.seller_count || 0, "已生成个人卡"],
      ["本周相关客户", rollup.account_count || 0, "按周期记录筛选"],
      ["有跟进客户", rollup.touched_account_count || 0, "存在明确跟进证据"],
      ["待推进资源", rollup.open_resource_count || 0, "未关闭的资源事项"],
    ];
    metrics.replaceChildren(...metricDefinitions.map(([labelText, count, description]) => {
      const card = document.createElement("article");
      const labelNode = document.createElement("span"); labelNode.textContent = labelText;
      const countNode = document.createElement("strong"); countNode.textContent = String(count);
      const descriptionNode = document.createElement("small"); descriptionNode.textContent = description;
      card.append(labelNode, countNode, descriptionNode); return card;
    }));
    const briefs = data.seller_briefs || [];
    $("weekly-seller-count").textContent = String(briefs.length);
    sellerList.classList.toggle("empty", briefs.length === 0);
    sellerList.replaceChildren(...(briefs.length ? briefs.map((brief) => {
      const card = document.createElement("details"); card.className = "weekly-seller-card"; card.open = briefs.length <= 3;
      const summary = document.createElement("summary");
      const identity = document.createElement("div");
      const avatar = document.createElement("span"); avatar.className = "weekly-avatar"; avatar.textContent = (brief.name || brief.salesperson_id || "销").slice(0, 1);
      const copy = document.createElement("div"); const name = document.createElement("strong"); name.textContent = brief.name || brief.salesperson_id;
      const meta = document.createElement("small"); meta.textContent = `${brief.account_count || 0} 个客户 · ${(brief.top_actions || []).length} 个优先动作 · ${(brief.account_updates || []).length} 条跟进`;
      copy.append(name, meta); identity.append(avatar, copy);
      const expand = document.createElement("b"); expand.textContent = "查看行动";
      summary.append(identity, expand); card.append(summary);
      const body = document.createElement("div"); body.className = "weekly-seller-body";
      if (brief.welcome_note) { const welcome = document.createElement("p"); welcome.className = "weekly-welcome"; welcome.textContent = brief.welcome_note; body.append(welcome); }
      body.append(
        weeklyItemList("本周优先动作", brief.top_actions, (row) => row.text, "本周没有已确认的待办动作。"),
        weeklyItemList("客户进展", (brief.account_updates || []).slice(0, 5), (row) => row.summary, "本周没有已记录的客户跟进。"),
        weeklyItemList("资源需求", (brief.resource_needs || []).slice(0, 5), (row) => row.summary, "没有待推进的资源申请。"),
        weeklyItemList("可用销售资料", brief.recommended_assets, (row) => `${row.title}${row.source_path ? `（${row.source_path}）` : ""}`, "没有通过授权和来源校验的推荐资料。"),
      );
      card.append(body); return card;
    }) : [weeklyEmpty("暂无可生成个人简报的销售人员。请先在销售人员名单或跟进记录中补充销售人员编号。")]));

    validationList.replaceChildren(...(validation.messages || []).map((message) => {
      const item = document.createElement("p"); item.textContent = message; return item;
    }));
    const unassigned = rollup.unassigned_accounts || [];
    if (unassigned.length) {
      const heading = document.createElement("h3"); heading.textContent = `待分配客户（${unassigned.length}）`;
      const list = document.createElement("ul");
      unassigned.forEach((row) => { const item = document.createElement("li"); item.textContent = `${row.name || row.account_id}${row.owner ? ` · 当前负责人字段：${row.owner}` : " · 未填写负责人"}`; list.append(item); });
      unassignedList.replaceChildren(heading, list);
    } else unassignedList.replaceChildren();
  }

  async function loadWeeklyBriefing({ force = false } = {}) {
    const period = currentWeek();
    const periodKey = `${period.start}:${period.end}`;
    if (weeklyState.loading || (!force && weeklyState.data && weeklyState.periodKey === periodKey)) return;
    const generation = ++weeklyState.generation;
    weeklyState.loading = true; weeklyState.error = ""; weeklyState.periodKey = periodKey; renderWeeklyBriefing();
    try {
      const data = await api(`/api/weekly-briefing?start=${encodeURIComponent(period.start)}&end=${encodeURIComponent(period.end)}`);
      if (generation !== weeklyState.generation) return;
      weeklyState.data = data;
    } catch (error) {
      if (generation !== weeklyState.generation) return;
      weeklyState.data = null; weeklyState.error = error.message;
    } finally {
      if (generation === weeklyState.generation) { weeklyState.loading = false; renderWeeklyBriefing(); }
    }
  }

  function renderTaskForm() {
    updatePresentationSourcePolicy();
    const presentation = isPresentationStudio();
    const weekly = isWeeklyService();
    $("generic-task-form").hidden = presentation || weekly;
    $("presentation-task-form").hidden = !presentation;
    $("weekly-task-form").hidden = !weekly;
    if (!presentation && !weekly) renderGuidedForm();
    if (weekly) { const period = currentWeek(); $("weekly-period").textContent = `${period.start} 至 ${period.end}`; }
  }

  function renderWorkflow(task) {
    const workflowId = task?.workflow_id || currentService()?.workflow;
    const workflow = model.workflows[workflowId];
    const box = $("workflow");
    if (!workflow) { box.textContent = "未找到可展示的工作流。"; return; }
    box.replaceChildren();
    const title = document.createElement("p");
    title.textContent = workflow.display_name;
    box.append(title);
    const flow = document.createElement("div");
    flow.className = "flow";
    const done = task?.completed_nodes || [];
    workflow.nodes.forEach((node) => {
      const item = document.createElement("span");
      item.className = `node ${done.includes(node.id) ? "done" : ""} ${(task?.current_node === node.id || task?.waiting_node === node.id) ? "current" : ""} ${node.type === "approval" ? "approval" : ""}`;
      item.textContent = node.display_name || "处理阶段";
      item.title = node.type_display_name || "处理阶段";
      flow.append(item);
    });
    box.append(flow);
  }

  function parseCanonicalPayload(task) {
    const text = task?.pending_write?.canonical_payload;
    if (typeof text !== "string") return null;
    try { const value = JSON.parse(text); return value && typeof value === "object" ? value : null; } catch { return null; }
  }

  const writeFieldLabels = {
    title: "资料标题", publisher: "发布机构", published_date: "发布日期", accessed_date: "读取日期",
    region: "地区", topic: "主题", source_type: "资料类型", quality: "来源质量",
    exposure_status: "访问方式", status: "核验状态", key_facts: "主要事实",
    interpretation: "分析说明", limitations: "限制与提醒", url: "来源链接",
    customer_name: "客户名称", customer_id: "客户编号", sector: "行业",
    owner: "负责人", stage: "销售阶段", health: "机会健康度", key_contact: "关键联系人",
    decision_maker: "决策人", budget_path: "预算路径", next_action: "下一步行动",
    next_action_due: "行动截止日期", last_evidence_date: "最近证据日期", risks: "风险",
    occurred_at: "发生时间", channel: "沟通渠道", activity_type: "活动类型", summary: "沟通摘要",
    commitment: "承诺事项", requested_at: "申请时间", resource_type: "资源类型",
    request_summary: "资源需求", business_reason: "业务原因", deadline: "截止日期",
    decision: "处理决定", decision_reason: "决定说明", asset_type: "资料类型", audience_role: "使用对象",
    sales_stage: "适用销售阶段", use_case: "使用场景", scope: "适用范围", version: "版本",
    authorization_status: "授权状态", deidentification_status: "脱敏状态", usage_feedback: "使用反馈",
    bid_id: "投标项目编号", workspace_project_id: "项目空间", account_id: "关联客户", opportunity_id: "关联机会",
    name: "项目名称", buyer: "采购人", tender_number: "招标编号", lot_name: "标段", deadline_at: "截止时间",
    budget_minor: "预算（分）", currency: "币种", current_stage: "当前阶段", go_no_go: "参投判断",
    decision_reason: "决策说明", milestone_type: "里程碑类型", due_at: "截止时间", evidence_json: "证据",
    category: "类别", mandatory: "是否强制", score_points: "分值", requirement_text: "招标要求原文",
    evidence_locator_json: "原文定位", verification_status: "核验状态", response_status: "响应状态",
    requirement_id: "招标要求编号", section_id: "章节编号", response_strategy: "应答策略",
    material_need: "材料需求", material_status: "材料状态", deviation: "偏差说明", field_name: "事实字段",
    value_text: "事实值", affected_sections_json: "影响章节", parent_section_id: "上级章节",
    order_index: "目录顺序", level: "标题层级", objective: "章节目标", content_markdown: "章节内容",
    input_sha256: "输入快照校验码", rule_id: "检查规则", rule_version: "规则版本", severity: "风险等级",
    finding: "发现的问题", recommendation: "处理建议", resolved_by: "解决人", resolved_at: "解决时间",
    risk_text: "风险说明", impact: "影响", likelihood: "可能性", mitigation_action: "缓解动作",
    decision_type: "决策类型", rationale: "决策依据", approved_by: "批准人", approval_task_id: "审批任务",
    payload_sha256: "审批内容校验码", decided_at: "决定时间", result: "投标结果", amount_minor: "金额（分）",
    competitor_notes: "竞争情况", lessons: "复盘结论",
  };

  const writeValueLabels = {
    verified: "已核验", pending: "待核验", superseded: "已被新版本替代",
    active: "启用", draft: "草稿", insert: "新增", update: "修改",
  };

  function readableWriteValue(field, value) {
    const text = String(value ?? "").trim();
    if (!text) return "未填写";
    return field === "status" || field.endsWith("_status") ? (writeValueLabels[text] || text) : text;
  }

  function appendWriteField(box, field, value) {
    const text = String(value ?? "").trim();
    if (!text) return;
    const row = document.createElement("div");
    row.className = `write-review-field ${field === "limitations" ? "warning" : ""}`;
    const label = document.createElement("strong");
    label.textContent = writeFieldLabels[field] || field.replaceAll("_", " ");
    const content = document.createElement(field === "url" ? "a" : "span");
    content.textContent = readableWriteValue(field, text);
    if (field === "url") {
      try {
        const url = new URL(text);
        if (url.protocol === "http:" || url.protocol === "https:") {
          content.href = url.toString();
          content.target = "_blank";
          content.rel = "noopener noreferrer";
        }
      } catch { /* Keep an invalid historic value as plain text. */ }
    }
    row.append(label, content);
    box.append(row);
  }

  function writeIntentCounts(mutations) {
    const inserts = mutations.filter((item) => item?.operation === "insert").length;
    const updates = mutations.filter((item) => item?.operation === "update").length;
    return [inserts ? `新增 ${inserts} 条` : "", updates ? `修改 ${updates} 条` : ""].filter(Boolean).join("、");
  }

  function stableWriteField(task, payload, mutation = null) {
    if (task.pending_write?.logical_tool === "knowledge.write") return "source_id";
    const table = task.pending_write?.logical_tool === "bid.write" ? mutation?.table : payload?.table;
    return { customers: "customer_id", activities: "activity_id", resource_requests: "request_id", sales_assets: "asset_id", bid_projects: "bid_id", bid_milestones: "milestone_id", bid_requirements: "requirement_id", bid_response_matrix: "response_id", bid_facts: "fact_id", bid_sections: "section_id", bid_checks: "check_id", bid_risks: "risk_id", bid_decisions: "decision_id", bid_outcomes: "outcome_id" }[table] || "";
  }

  async function submitWriteCardRevision(task, mutation, operation, changes) {
    const reply = await api(`/api/tasks/${encodeURIComponent(task.task_id)}/write-intent-revision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        version: task.version,
        intent_id: task.pending_write.intent_id,
        payload_sha256: task.pending_write.payload_sha256,
        operation,
        record_id: mutation.record_id,
        ...(changes ? { changes } : {}),
      }),
    });
    showNotice(reply.message || "待写入内容修改已提交");
    await load();
  }

  function openWriteCardEditor(task, payload, mutation) {
    document.querySelector(".write-edit-overlay")?.remove();
    const changes = mutation?.changes && typeof mutation.changes === "object" ? mutation.changes : {};
    const stableField = stableWriteField(task, payload, mutation);
    const editable = Object.entries(changes).filter(([field, value]) => field !== stableField && typeof value === "string");
    if (!editable.length) {
      showNotice("这张卡片没有可直接编辑的文字字段", true);
      return;
    }
    const overlay = document.createElement("div");
    overlay.className = "write-edit-overlay";
    overlay.setAttribute("role", "presentation");
    const form = document.createElement("form");
    form.className = "write-edit-dialog";
    form.setAttribute("role", "dialog");
    form.setAttribute("aria-modal", "true");
    const header = document.createElement("header");
    const heading = document.createElement("div");
    const title = document.createElement("h3");
    title.textContent = "编辑待写入卡片";
    const help = document.createElement("p");
    help.textContent = "保存后会生成新的校验码，并重新等待你确认；不会直接修改数据库。";
    heading.append(title, help);
    const close = document.createElement("button");
    close.type = "button";
    close.className = "write-edit-close";
    close.textContent = "关闭";
    header.append(heading, close);
    const fields = document.createElement("div");
    fields.className = "write-edit-fields";
    const controls = new Map();
    editable.forEach(([field, value]) => {
      const label = document.createElement("label");
      const caption = document.createElement("strong");
      caption.textContent = writeFieldLabels[field] || field.replaceAll("_", " ");
      const multiline = value.length > 80 || ["notes", "risks", "summary", "limitations", "interpretation", "next_action", "request_summary", "business_reason"].includes(field);
      const control = document.createElement(multiline ? "textarea" : "input");
      if (!multiline) control.type = "text";
      control.value = value;
      control.maxLength = 10000;
      control.dataset.field = field;
      controls.set(field, control);
      label.append(caption, control);
      fields.append(label);
    });
    const actions = document.createElement("div");
    actions.className = "write-edit-actions";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "secondary";
    cancel.textContent = "取消";
    const save = document.createElement("button");
    save.type = "submit";
    save.textContent = "保存并重新确认";
    actions.append(cancel, save);
    form.append(header, fields, actions);
    overlay.append(form);
    document.body.append(overlay);
    const dismiss = () => overlay.remove();
    close.addEventListener("click", dismiss);
    cancel.addEventListener("click", dismiss);
    overlay.addEventListener("click", (event) => { if (event.target === overlay) dismiss(); });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const revisedChanges = { ...changes };
      controls.forEach((control, field) => { revisedChanges[field] = control.value; });
      save.disabled = true;
      try {
        await submitWriteCardRevision(task, mutation, "edit", revisedChanges);
        dismiss();
      } catch (error) {
        showNotice(error.message, true);
        save.disabled = false;
      }
    });
    queueMicrotask(() => controls.values().next().value?.focus());
  }

  function addWriteCardActions(header, task, payload, mutation, totalCards) {
    const controls = document.createElement("div");
    controls.className = "write-card-actions";
    const operation = document.createElement("span");
    operation.className = "write-operation";
    operation.textContent = writeValueLabels[mutation?.operation] || "变更";
    const edit = document.createElement("button");
    edit.type = "button";
    edit.textContent = "编辑";
    const editableShape = mutation?.changes && typeof mutation.changes === "object"
      && Object.values(mutation.changes).every((value) => typeof value === "string");
    edit.disabled = Boolean(task.approval_request) || !editableShape;
    if (!editableShape) edit.title = "这是旧版异常卡片，只能删除或结束任务后重新生成";
    edit.addEventListener("click", () => openWriteCardEditor(task, payload, mutation));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "danger-text";
    remove.textContent = "删除";
    remove.disabled = Boolean(task.approval_request);
    remove.addEventListener("click", async () => {
      const lastCard = totalCards === 1;
      const accepted = await confirmAction({
        title: lastCard ? "删除最后一张待写入卡片？" : "删除这张待写入卡片？",
        message: lastCard
          ? "删除后，本次将不再写入任何内容，任务会安全结束。数据库中的已有记录不会受到影响。"
          : "这张卡片只会从本次待写入批次中移除，数据库中的已有记录不会被删除。",
        confirmText: lastCard ? "删除并结束任务" : "删除卡片",
        tone: "danger",
      });
      if (!accepted) return;
      remove.disabled = true;
      try { await submitWriteCardRevision(task, mutation, "remove"); }
      catch (error) { showNotice(error.message, true); remove.disabled = false; }
    });
    controls.append(operation, edit, remove);
    header.append(controls);
  }

  function renderWriteIntentReview(wrapper, task, parsedPayload, presentationRendered) {
    const review = document.createElement("section");
    review.className = "write-review";
    const heading = document.createElement("h3");
    heading.textContent = "确认后会发生什么";
    const intro = document.createElement("p");
    const mutations = Array.isArray(parsedPayload?.mutations) ? parsedPayload.mutations : [];
    if (task.pending_write.logical_tool === "knowledge.write") {
      intro.textContent = `将向资料库${writeIntentCounts(mutations) || "写入以下内容"}。正式写入前，你可以逐条核对来源、事实和限制。`;
    } else if (task.pending_write.logical_tool === "sales.write") {
      const tableLabels = { customers: "客户台账", activities: "客户活动记录", resource_requests: "资源申请", sales_assets: "销售资料库" };
      intro.textContent = `将更新${tableLabels[parsedPayload?.table] || "销售台账"}：${writeIntentCounts(mutations) || "写入以下内容"}。`;
    } else if (task.pending_write.logical_tool === "bid.write") {
      const tables = [...new Set(mutations.map((item) => bidTableLabels[item?.table] || item?.table).filter(Boolean))];
      intro.textContent = `将更新当前投标项目的${tables.join("、") || "业务记录"}：${writeIntentCounts(mutations) || "写入以下内容"}。每张卡片都可先编辑或删除，不会自动提交标书。`;
    } else if (task.pending_write.logical_tool === "artifact.deck.write") {
      intro.textContent = `将生成演示文稿“${parsedPayload?.output_name || "未命名演示文稿"}”。上方大纲和逐页内容就是本次审批范围。`;
    } else if (task.pending_write.logical_tool === "artifact.document.write") {
      intro.textContent = `将为投标项目生成可编辑文字文档“${parsedPayload?.output_name || "未命名标书"}”，包含 ${parsedPayload?.sections?.length || 0} 个章节和 ${parsedPayload?.sources?.length || 0} 个已登记来源；生成后会逐页渲染检查，且不会覆盖同名文件。`;
    } else {
      intro.textContent = presentationRendered ? "上方演示方案就是本次待确认内容。" : "确认后将按下列内容执行受控写入。";
    }
    review.append(heading, intro);

    if (mutations.length) {
      const cards = document.createElement("div");
      cards.className = "write-review-cards";
      const stateKey = `${task.task_id}:${task.pending_write.payload_sha256}`;
      const saved = taskWriteIntentState[stateKey] || (taskWriteIntentState[stateKey] = {});
      cards.addEventListener("scroll", () => { saved.reviewTop = cards.scrollTop; }, { passive: true });
      const knowledgeFields = [
        "publisher", "published_date", "accessed_date", "region", "topic", "source_type", "quality",
        "status", "key_facts", "interpretation", "limitations", "url",
      ];
        const ignoredFields = new Set(["source_id", "activity_id", "request_id", "asset_id", "bid_id", "milestone_id", "requirement_id", "response_id", "fact_id", "section_id", "check_id", "risk_id", "decision_id", "outcome_id", "updated_at", "created_at", "notes"]);
      mutations.forEach((mutation, index) => {
        const changes = mutation?.changes && typeof mutation.changes === "object" ? mutation.changes : {};
        const card = document.createElement("article");
        card.className = "write-review-card";
        const header = document.createElement("header");
        const title = document.createElement("strong");
        title.textContent = String(changes.title || changes.customer_name || changes.summary || changes.request_summary || changes.next_action || mutation?.record_id || `第 ${index + 1} 条`);
        header.append(title);
        addWriteCardActions(header, task, parsedPayload, mutation, mutations.length);
        card.append(header);
        const fields = task.pending_write.logical_tool === "knowledge.write"
          ? knowledgeFields
          : Object.keys(changes).filter((field) => !ignoredFields.has(field));
        fields.forEach((field) => appendWriteField(card, field, changes[field]));
        cards.append(card);
      });
      review.append(cards);
      queueMicrotask(() => {
        cards.scrollTop = Math.min(Number(saved.reviewTop) || 0, Math.max(0, cards.scrollHeight - cards.clientHeight));
      });
    }
    if (task.pending_write.logical_tool === "artifact.document.write" && Array.isArray(parsedPayload?.sections)) {
      const cards = document.createElement("div"); cards.className = "write-review-cards";
      parsedPayload.sections.forEach((section, index) => {
        const card = document.createElement("article"); card.className = "write-review-card";
        const header = document.createElement("header"); const title = document.createElement("strong"); title.textContent = `${index + 1}. ${section.title || "未命名章节"}`; const meta = document.createElement("span"); meta.className = "write-operation"; meta.textContent = `标题层级 ${section.level || 1}`; header.append(title, meta); card.append(header);
        const paragraphs = Array.isArray(section.paragraphs) ? section.paragraphs : []; appendWriteField(card, "content_markdown", paragraphs.join("\n\n").slice(0, 5000));
        if (Array.isArray(section.tables) && section.tables.length) appendWriteField(card, "source_type", `${section.tables.length} 个表格`);
        cards.append(card);
      }); review.append(cards);
      if (Array.isArray(parsedPayload.warnings) && parsedPayload.warnings.length) { const warning = document.createElement("div"); warning.className = "write-review-field warning"; const strong = document.createElement("strong"); strong.textContent = "生成前提醒"; const span = document.createElement("span"); span.textContent = parsedPayload.warnings.join("；"); warning.append(strong, span); review.append(warning); }
    }
    wrapper.append(review);
  }

  function presentationPlan(task) {
    const payload = parseCanonicalPayload(task);
    if (payload && Array.isArray(payload.slides)) return payload;
    const plan = task?.presentation_plan;
    if (plan && typeof plan === "object" && (Array.isArray(plan.slides) || Array.isArray(plan.outline))) return plan;
    return null;
  }

  function slideTitle(slide, index) {
    const candidates = [slide?.conclusion_title, slide?.title, slide?.part_title, slide?.name];
    return candidates.find((value) => typeof value === "string" && value.trim())?.trim() || `第 ${index + 1} 页`;
  }

  function slideSummary(slide) {
    const lead = typeof slide?.audience_takeaway === "string" ? slide.audience_takeaway : slide?.lead;
    if (typeof lead === "string" && lead.trim()) return lead.trim();
    if (Array.isArray(slide?.body)) return slide.body.filter((item) => typeof item === "string").slice(0, 2).join("；");
    if (Array.isArray(slide?.content)) return slide.content.filter((item) => typeof item === "string").slice(0, 2).join("；");
    return "等待补充逐页策划";
  }

  function slideSources(slide) {
    const refs = Array.isArray(slide?.sources) ? slide.sources : Array.isArray(slide?.evidence_refs) ? slide.evidence_refs : [];
    return refs.length;
  }

  function renderPresentationReview(article, task) {
    const plan = presentationPlan(task);
    if (!plan) return false;
    const slides = Array.isArray(plan.slides) ? plan.slides : plan.outline;
    const canRevise = Boolean(
      task.status === "waiting_approval" && !task.pending_write && task.presentation_plan &&
      typeof plan.plan_sha256 === "string" && plan.plan_sha256.length === 64
    );
    const review = document.createElement("section");
    review.className = "presentation-review";
    const header = document.createElement("div");
    header.className = "presentation-review-header";
    const title = document.createElement("h3");
    title.textContent = "演示文稿大纲";
    const meta = document.createElement("span");
    meta.className = "deck-meta";
    const modeLabels = { standard: "标准", quick: "快速", strict: "严格" };
    meta.textContent = `${slides.length} 页 · ${modeLabels[plan.mode || plan.brief?.mode] || "标准"}模式`;
    header.append(title, meta);
    review.append(header);
    const grid = document.createElement("div");
    grid.className = "sticky-grid";
    let draggedCard = null;
    slides.forEach((slide, index) => {
      const card = document.createElement("article");
      card.className = "sticky-card";
      card.dataset.slideId = slide?.slide_id || `slide-${index + 1}`;
      const number = document.createElement("span");
      number.className = "sticky-number";
      number.textContent = String(index + 1);
      const heading = document.createElement(canRevise ? "input" : "strong");
      if (canRevise) {
        heading.className = "sticky-title-input";
        heading.value = slideTitle(slide, index);
        heading.maxLength = 120;
        heading.setAttribute("aria-label", `第 ${index + 1} 页标题`);
        card.draggable = true;
        card.title = "拖动调整页面顺序";
        card.addEventListener("dragstart", () => { draggedCard = card; card.classList.add("dragging"); });
        card.addEventListener("dragend", () => {
          card.classList.remove("dragging");
          draggedCard = null;
          [...grid.children].forEach((item, itemIndex) => { item.querySelector(".sticky-number").textContent = String(itemIndex + 1); });
        });
        card.addEventListener("dragover", (event) => {
          event.preventDefault();
          if (draggedCard && draggedCard !== card) grid.insertBefore(draggedCard, card);
        });
      } else {
        heading.textContent = slideTitle(slide, index);
      }
      const summary = document.createElement("p");
      summary.textContent = slideSummary(slide);
      const sources = document.createElement("span");
      sources.className = "source-count";
      sources.textContent = `证据来源 ${slideSources(slide)}`;
      card.append(number, heading, summary, sources);
      if (Array.isArray(slide?.warnings) && slide.warnings.length) {
        const warning = document.createElement("p");
        warning.className = "plan-warning";
        warning.textContent = `待处理：${slide.warnings.length} 项`;
        card.append(warning);
      }
      grid.append(card);
    });
    review.append(grid);
    if (canRevise) {
      const revisionBar = document.createElement("div");
      revisionBar.className = "revision-bar";
      const help = document.createElement("span");
      help.textContent = "可拖动调序并修改标题；保存会创建新任务，旧任务和审批记录仍保留。";
      const revise = document.createElement("button");
      revise.className = "action revise";
      revise.textContent = "按此大纲创建修订任务";
      revise.onclick = async () => {
        const outline = [...grid.querySelectorAll(".sticky-card")].map((card) => ({
          slide_id: card.dataset.slideId,
          title: card.querySelector(".sticky-title-input").value.trim(),
        }));
        if (outline.some((item) => !item.title)) { note("每页标题都不能为空。", true); return; }
        if (!await confirmAction({
          title: "按当前大纲创建修订任务？",
          message: "当前页序和标题会用于创建一个新任务；旧任务将结束，但原任务和审批记录仍会保留。",
          confirmText: "创建修订任务",
        })) return;
        try {
          const reply = await api(`/api/tasks/${encodeURIComponent(task.task_id)}/presentation-revision`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ version: task.version, plan_sha256: plan.plan_sha256, outline }),
          });
          note(reply.message);
          await load();
        } catch (error) { note(error.message, true); await load(); }
      };
      revisionBar.append(help, revise);
      review.append(revisionBar);
    }
    article.insertBefore(review, article.querySelector(".task-actions"));
    return true;
  }

  function renderRawWriteIntent(article, task, presentationRendered) {
    if (!task.pending_write) return;
    const wrapper = document.createElement("div");
    wrapper.classList.add("task-details-section");
    const title = document.createElement("p");
    title.className = "write-intent-title";
    const toolLabels = {
      "knowledge.write": "写入资料库", "sales.write": "更新销售台账",
      "bid.write": "更新投标项目", "presentation.plan.write": "保存演示方案", "artifact.deck.write": "生成演示文稿",
      "artifact.document.write": "生成正式标书",
    };
    const writeStatusLabels = { prepared: "待确认", committing: "正在提交", committed: "已完成" };
    title.textContent = `待写入内容（${toolLabels[task.pending_write.logical_tool] || "受控写入"} · ${writeStatusLabels[task.pending_write.status] || "等待处理"}）`;
    wrapper.append(title);
    const parsedPayload = parseCanonicalPayload(task);
    renderWriteIntentReview(wrapper, task, parsedPayload, presentationRendered);

    const technical = document.createElement("details");
    technical.className = "raw-details";
    const stateKey = `${task.task_id}:${task.pending_write.payload_sha256}`;
    const saved = taskWriteIntentState[stateKey] || (taskWriteIntentState[stateKey] = {});
    technical.open = saved.technicalOpen === true;
    technical.addEventListener("toggle", () => { saved.technicalOpen = technical.open; });
    const summary = document.createElement("summary");
    summary.textContent = "查看技术明细与校验码";
    const hash = document.createElement("small");
    hash.className = "write-intent-hash";
    hash.textContent = `校验码：${task.pending_write.payload_sha256}`;
    const payload = document.createElement("pre");
    payload.className = "write-intent";
    payload.textContent = parsedPayload ? JSON.stringify(parsedPayload, null, 2) : (task.pending_write.canonical_payload || "");
    payload.addEventListener("scroll", () => { saved.rawTop = payload.scrollTop; saved.rawLeft = payload.scrollLeft; }, { passive: true });
    technical.append(summary, hash, payload);
    wrapper.append(technical);
    article.insertBefore(wrapper, article.querySelector(".task-actions"));
    queueMicrotask(() => {
      payload.scrollTop = Math.min(Number(saved.rawTop) || 0, Math.max(0, payload.scrollHeight - payload.clientHeight));
      payload.scrollLeft = Math.min(Number(saved.rawLeft) || 0, Math.max(0, payload.scrollWidth - payload.clientWidth));
    });
  }

  function approvalActionLabel(task) {
    if (task.pending_write?.logical_tool === "knowledge.write") return "批准写入资料库";
    if (task.pending_write?.logical_tool === "sales.write") return "批准更新销售台账";
    if (task.pending_write?.logical_tool === "bid.write") return "批准更新投标项目";
    if (task.pending_write?.logical_tool === "artifact.deck.write") return "批准并生成演示文稿";
    if (task.pending_write?.logical_tool === "artifact.document.write") return "批准并生成正式标书";
    return task.pending_write ? "批准执行" : "确认并继续";
  }

  function progressTime(value) {
    const date = new Date(value || "");
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  }

  function stepUpdatedTime(value) {
    const date = new Date(value || "");
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleString("zh-CN", {
      month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
    });
  }

  async function queueTaskPlanOperation(task, step, operation) {
    const operationCopy = {
      redirect: {
        title: "调整这个步骤",
        message: "请说明希望怎么调整。智能核心会重新评估尚未执行的工作，但不会改写已完成步骤或绕过人工审批。",
        confirm: "排队调整",
        placeholder: "例如：先对比三种合作模式，再给出推荐顺序和选择依据。",
      },
      insert_after: {
        title: "在这个步骤后增加工作",
        message: "请描述要增加的工作。智能核心会检查依赖关系，并把它纳入剩余计划。",
        confirm: "排队追加",
        placeholder: "例如：增加竞品对比，并列出信息缺口。",
      },
      pause_after: {
        title: "设置暂停点",
        message: "到达这个步骤的安全边界后暂停并等待你的新指令。暂停不会被视为批准。",
        confirm: "排队暂停",
        placeholder: "可补充暂停前必须交付的内容。",
      },
      cancel_step: {
        title: "申请取消这个步骤",
        message: "这不会立即删除工作流节点。智能核心会先判断它是否是必要步骤；必要步骤会保留并说明原因。",
        confirm: "提交申请",
        placeholder: "请说明取消原因和希望保留的结果。",
      },
      replan: {
        title: "重新规划剩余步骤",
        message: "请说明新的目标、优先级或限制。已完成步骤、已冻结内容、权限边界和人工审批保持不变。",
        confirm: "排队重排",
        placeholder: "例如：先给管理层结论，证据核验和附件整理随后完成。",
      },
    };
    const copy = operationCopy[operation];
    const target = step ? `\n目标步骤：${step.title}` : "";
    const content = await confirmAction({
      title: copy.title,
      message: `${copy.message}${target}`,
      confirmText: copy.confirm,
      inputValue: "",
      inputLabel: "调整说明",
      inputMultiline: true,
      inputMaxLength: 1200,
      inputPlaceholder: copy.placeholder,
      tone: operation === "cancel_step" ? "danger" : "primary",
    });
    if (content === null) return;
    if (!content) { note("请先填写调整说明。", true); return; }
    try {
      const response = await api(`/api/tasks/${encodeURIComponent(task.task_id)}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mode: operation === "supplement" ? "supplement" : "redirect",
          operation,
          ...(step ? { step_id: step.step_id } : {}),
          content,
        }),
      });
      note(response.message);
      await load();
    } catch (error) { note(error.message, true); }
  }

  function renderExecutionPlan(section, task, historical, effectiveStatus) {
    const plan = task.execution_plan;
    const steps = Array.isArray(plan?.steps) ? plan.steps : [];
    if (!steps.length) return;
    const details = document.createElement("details");
    details.className = "task-execution-plan";
    details.open = taskExecutionPlanExpansion.has(task.task_id)
      ? taskExecutionPlanExpansion.get(task.task_id)
      : true;
    details.addEventListener("toggle", () => taskExecutionPlanExpansion.set(task.task_id, details.open));
    const summary = document.createElement("summary");
    const summaryCopy = document.createElement("span");
    const title = document.createElement("strong");
    title.textContent = "任务执行步骤";
    const count = document.createElement("small");
    count.textContent = `已完成 ${plan.completed || 0} / ${plan.total || steps.length}`;
    summaryCopy.append(title, count);
    const summaryStatus = document.createElement("span");
    summaryStatus.className = `execution-plan-summary-status ${plan.stalled ? "stalled" : ""}`;
    summaryStatus.textContent = plan.stalled ? "较长时间未更新" : (plan.current_step_title ? `当前：${plan.current_step_title}` : "步骤已收口");
    summary.append(summaryCopy, summaryStatus);
    details.append(summary);

    const body = document.createElement("div");
    body.className = "task-execution-plan-body";
    const overview = document.createElement("div");
    overview.className = "execution-plan-overview";
    const track = document.createElement("div");
    track.className = "execution-plan-track";
    const fill = document.createElement("i");
    fill.style.width = `${Math.max(0, Math.min(100, Number(plan.percent) || 0))}%`;
    track.append(fill);
    const next = document.createElement("p");
    next.textContent = plan.next_step_title ? `下一步：${plan.next_step_title}` : "没有待处理的后续步骤。";
    overview.append(track, next);
    const canAdjust = !historical && !["interrupted", "resuming", "cancelling", "restarting"].includes(effectiveStatus);
    if (canAdjust) {
      const replan = document.createElement("button");
      replan.type = "button";
      replan.className = "execution-plan-replan";
      replan.textContent = "重新规划剩余步骤";
      replan.onclick = (event) => { event.stopPropagation(); queueTaskPlanOperation(task, null, "replan"); };
      overview.append(replan);
    }
    body.append(overview);

    const list = document.createElement("ol");
    list.className = "execution-step-list";
    const savedTop = Number(taskPlanScroll[task.task_id]) || 0;
    list.addEventListener("scroll", () => { taskPlanScroll[task.task_id] = list.scrollTop; }, { passive: true });
    const statusLabels = {
      pending: "待处理", in_progress: "正在处理", waiting_approval: "等待确认",
      completed: "已完成", interrupted: "已中断", skipped: "未执行", failed: "未完成",
    };
    steps.forEach((step) => {
      const item = document.createElement("li");
      item.className = `execution-step ${step.status || "pending"} ${step.stalled ? "stalled" : ""}`;
      item.dataset.stepId = step.step_id;
      const marker = document.createElement("span");
      marker.className = "execution-step-marker";
      marker.textContent = step.status === "completed" ? "✓" : String(step.position || "•");
      const copy = document.createElement("div");
      copy.className = "execution-step-copy";
      const heading = document.createElement("div");
      const name = document.createElement("strong");
      name.textContent = step.title || "处理步骤";
      const status = document.createElement("span");
      status.className = `execution-step-status ${step.status || "pending"}`;
      status.textContent = step.stalled ? "较长时间未更新" : (statusLabels[step.status] || "待处理");
      heading.append(name, status);
      const meta = document.createElement("small");
      const dependency = Array.isArray(step.dependency_titles) && step.dependency_titles.length
        ? ` · 前置：${step.dependency_titles.join("、")}`
        : "";
      meta.textContent = `${step.type_display_name || "处理阶段"} · 更新于 ${stepUpdatedTime(step.updated_at)}${dependency}`;
      copy.append(heading, meta);
      if (step.blocked_reason) {
        const blocked = document.createElement("p");
        blocked.className = "execution-step-reason";
        blocked.textContent = step.blocked_reason;
        copy.append(blocked);
      }
      if (step.pending_request_count) {
        const queued = document.createElement("small");
        queued.className = "execution-step-request";
        queued.textContent = `${step.pending_request_count} 项调整等待处理`;
        copy.append(queued);
      }
      item.append(marker, copy);

      const stepCanAdjust = canAdjust && !["skipped", "failed"].includes(step.status);
      if (stepCanAdjust) {
        const menu = document.createElement("details");
        menu.className = "execution-step-menu";
        const menuSummary = document.createElement("summary");
        menuSummary.textContent = "调整";
        const menuBody = document.createElement("div");
        const operations = [];
        if (!["completed"].includes(step.status)) operations.push(["redirect", "调整此步骤"]);
        operations.push(["insert_after", "在后面增加"]);
        if (["pending", "in_progress"].includes(step.status)) operations.push(["pause_after", "完成后暂停"]);
        if (["pending", "in_progress"].includes(step.status) && step.type !== "approval") operations.push(["cancel_step", "申请取消"]);
        operations.forEach(([operation, label]) => {
          const button = document.createElement("button");
          button.type = "button";
          button.dataset.operation = operation;
          button.textContent = label;
          button.onclick = (event) => {
            event.stopPropagation();
            menu.open = false;
            queueTaskPlanOperation(task, step, operation);
          };
          menuBody.append(button);
        });
        menu.append(menuSummary, menuBody);
        item.append(menu);
      }
      list.append(item);
    });
    body.append(list);
    details.append(body);
    section.append(details);
    queueMicrotask(() => {
      list.scrollTop = Math.min(savedTop, Math.max(0, list.scrollHeight - list.clientHeight));
    });
  }

  function renderTaskProgress(article, task, historical) {
    const section = document.createElement("section");
    section.className = "task-progress-panel";
    const header = document.createElement("div");
    header.className = "task-progress-header";
    const heading = document.createElement("div");
    const title = document.createElement("h3");
    title.textContent = "智能助手处理过程";
    const description = document.createElement("small");
    description.textContent = "显示阶段动作、判断依据和下一步，不展示隐藏的逐字思维链。";
    heading.append(title, description);
    const queue = document.createElement("span");
    queue.className = "message-queue-badge";
    queue.textContent = task.queued_message_count ? `${task.queued_message_count} 条消息排队中` : "消息队列空闲";
    header.append(heading, queue);
    section.append(header);
    const effectiveStatus = displayStatus(task);
    renderExecutionPlan(section, task, historical, effectiveStatus);

    const timeline = document.createElement("div");
    timeline.className = "task-progress-timeline";
    timeline.dataset.taskId = task.task_id;
    const previousScroll = taskProgressScroll[task.task_id];
    timeline.addEventListener("scroll", () => {
      const distanceFromBottom = timeline.scrollHeight - timeline.clientHeight - timeline.scrollTop;
      taskProgressScroll[task.task_id] = {
        top: timeline.scrollTop,
        followLatest: distanceFromBottom <= 24,
      };
    }, { passive: true });
    const events = Array.isArray(task.progress) ? task.progress.slice(-12) : [];
    if (!events.length) {
      const empty = document.createElement("p");
      empty.className = "hint";
      empty.textContent = "任务接手后，阶段进度会显示在这里。";
      timeline.append(empty);
    }
    events.forEach((event) => {
      const item = document.createElement("article");
      item.className = `progress-event ${event.kind || "system"} ${event.status || "done"}`;
      const marker = document.createElement("i");
      const copy = document.createElement("div");
      const eventHeader = document.createElement("div");
      eventHeader.className = "progress-event-header";
      const eventTitle = document.createElement("strong");
      eventTitle.textContent = event.title || "处理进度";
      const at = document.createElement("time");
      at.textContent = progressTime(event.at);
      eventHeader.append(eventTitle, at);
      const summary = document.createElement("p");
      summary.textContent = event.summary || "";
      copy.append(eventHeader, summary);
      if (event.basis) {
        const basis = document.createElement("small");
        basis.className = "progress-basis";
        basis.textContent = `依据：${event.basis}`;
        copy.append(basis);
      }
      if (event.next_step) {
        const next = document.createElement("small");
        next.className = "progress-next";
        next.textContent = `下一步：${event.next_step}`;
        copy.append(next);
      }
      if (event.status === "queued") {
        const queued = document.createElement("small");
        queued.className = "progress-queued";
        queued.textContent = "已排队，等待当前工具调用结束";
        copy.append(queued);
      }
      item.append(marker, copy);
      timeline.append(item);
    });
    section.append(timeline);

    if (!historical && !["interrupted", "resuming", "cancelling", "restarting"].includes(effectiveStatus)) {
      const composer = document.createElement("div");
      composer.className = "task-message-composer";
      const textarea = document.createElement("textarea");
      textarea.dataset.taskId = task.task_id;
      textarea.maxLength = 1200;
      textarea.placeholder = "继续补充客户信息，或告诉助手需要调整的方向…";
      textarea.value = taskMessageDrafts[task.task_id] || "";
      textarea.addEventListener("input", () => { taskMessageDrafts[task.task_id] = textarea.value; });
      const actions = document.createElement("div");
      actions.className = "task-message-actions";
      const submit = async (mode, button) => {
        const content = textarea.value.trim();
        if (!content) { note("请先输入要补充或调整的内容。", true); textarea.focus(); return; }
        button.disabled = true;
        try {
          const response = await api(`/api/tasks/${encodeURIComponent(task.task_id)}/messages`, {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode, content }),
          });
          taskMessageDrafts[task.task_id] = "";
          note(response.message);
          await load();
        } catch (error) { note(error.message, true); }
        finally { button.disabled = false; }
      };
      const supplement = document.createElement("button");
      supplement.className = "secondary";
      supplement.textContent = "排队补充";
      supplement.title = "保留当前方向，把信息加入下一处理步骤";
      supplement.onclick = () => submit("supplement", supplement);
      const redirect = document.createElement("button");
      redirect.className = "primary";
      redirect.textContent = "调整当前方向";
      redirect.title = "当前工具调用结束后，优先重新评估后续步骤";
      redirect.onclick = () => submit("redirect", redirect);
      actions.append(supplement, redirect);
      composer.append(textarea, actions);
      if (task.status === "waiting_approval") {
        const warning = document.createElement("small");
        warning.textContent = task.approval_request
          ? "你的审批已经提交；智能核心核验完成前不能重复操作。"
          : "当前正等待审批：消息可以补充上下文，但不会替代批准、驳回或演示文稿大纲修订。";
        composer.append(warning);
      }
      section.append(composer);
    }
    article.insertBefore(section, article.querySelector(".task-actions"));
    queueMicrotask(() => {
      const maximum = Math.max(0, timeline.scrollHeight - timeline.clientHeight);
      timeline.scrollTop = previousScroll?.followLatest === false
        ? Math.min(Math.max(0, Number(previousScroll.top) || 0), maximum)
        : maximum;
    });
  }

  function renderTasks() {
    const activeTasks = model.tasks.filter((task) => !isHistoricalTask(task));
    const historyTasks = model.tasks.filter(isHistoricalTask);
    const renderTaskCards = (box, tasks, historical) => {
      box.replaceChildren();
      box.classList.toggle("empty", tasks.length === 0);
      if (!tasks.length) {
        box.textContent = historical ? "暂无历史任务。" : "当前没有待处理任务。";
        return;
      }
      tasks.forEach((task) => {
      const template = $("task-template").content.cloneNode(true);
      const serviceName = currentProfile()?.services.find((service) => service.id === task.service_id)?.display_name;
      template.querySelector("strong").textContent = serviceName || task.service_id || "销售任务";
      const badge = template.querySelector(".status");
      const effectiveStatus = displayStatus(task);
      badge.textContent = label[effectiveStatus] || "未知状态";
      if (Object.hasOwn(label, effectiveStatus)) badge.classList.add(effectiveStatus);
      const scheduleMeta = task.schedule_id ? ` · 每日任务 ${task.scheduled_for || ""}` : "";
      const nodeLabel = historical ? "任务已结束" : (task.waiting_node_display_name || task.current_node_display_name || "等待智能核心接手");
      const effectiveModel = displayModelName(task.effective_model || task.requested_model);
      const effectiveThinking = thinkingLabels[task.effective_thinking_level || task.requested_thinking_level] || "默认";
      template.querySelector(".task-meta").textContent = `项目：${projectById(task.project_id)?.name || "日常工作"}${scheduleMeta} · 模型：${effectiveModel} · 思考：${effectiveThinking} · 节点：${nodeLabel} · 版本 ${task.version ?? "-"}`;
      template.querySelector(".task-request").textContent = displayTaskRequest(task);
      const actions = template.querySelector(".task-actions");
      const article = template.querySelector("article");
      renderTaskProgress(article, task, historical);
      const presentationRendered = renderPresentationReview(article, task);
      renderRawWriteIntent(article, task, presentationRendered);
      if (task.status === "waiting_approval" && !task.approval_request) addAction(actions, task, "approve", approvalActionLabel(task));
      if (task.status === "waiting_approval" && !task.approval_request) addAction(actions, task, "reject", "驳回");
      if (task.status === "waiting_approval" && !task.approval_request) addAction(actions, task, "cancel", "结束任务");
      if (effectiveStatus === "interrupted") addAction(actions, task, "resume", "继续任务");
      if (effectiveStatus === "interrupted") addRestartAction(actions, task, "重新开始");
      if (effectiveStatus === "interrupted") addAction(actions, task, "cancel", "结束任务");
      if (historical) addRestartAction(actions, task, "再次创建");
      const expanded = taskCardExpansion.has(task.task_id) ? taskCardExpansion.get(task.task_id) : !historical;
      article.classList.toggle("collapsed", !expanded);
      const collapse = document.createElement("button");
      collapse.type = "button";
      collapse.className = "task-collapse";
      collapse.textContent = expanded ? "收起详情" : "展开详情";
      collapse.setAttribute("aria-expanded", String(expanded));
      collapse.onclick = (event) => {
        event.stopPropagation();
        const nextExpanded = article.classList.contains("collapsed");
        article.classList.toggle("collapsed", !nextExpanded);
        taskCardExpansion.set(task.task_id, nextExpanded);
        collapse.textContent = nextExpanded ? "收起详情" : "展开详情";
        collapse.setAttribute("aria-expanded", String(nextExpanded));
        if (nextExpanded) renderWorkflow(task);
      };
      const titleControls = document.createElement("div");
      titleControls.className = "task-title-controls";
      badge.replaceWith(titleControls);
      titleControls.append(badge, collapse);
      if (historical) addDeleteAction(titleControls, task);
      article.onclick = (event) => { if (!event.target.closest("button,summary,input,textarea,select")) renderWorkflow(task); };
      box.append(template);
      });
    };
    renderTaskCards($("tasks"), activeTasks, false);
    renderTaskCards($("task-history"), historyTasks, true);
    renderWorkflow(activeTasks[0] || historyTasks[0]);
  }

  function addAction(box, task, decision, text) {
    const button = document.createElement("button");
    button.className = `action ${decision}`;
    button.textContent = text;
    button.onclick = async () => {
      const destructive = decision === "reject" || decision === "cancel";
      const accepted = await confirmAction({
        title: `${text}？`,
        message: decision === "approve" && task.pending_write
          ? "确认后将按照当前待写入卡片执行操作。请确保内容已经核对无误。"
          : destructive
            ? "确认后将结束当前处理，不会执行尚未批准的写入。"
            : "确认后，任务将进入下一处理阶段。",
        confirmText: text,
        tone: destructive ? "danger" : "primary",
        detail: decision === "approve" && task.pending_write ? task.pending_write.payload_sha256 : "",
      });
      if (!accepted) return;
      const body = { decision, version: task.version };
      if (decision === "approve" && task.pending_write) { body.intent_id = task.pending_write.intent_id; body.payload_sha256 = task.pending_write.payload_sha256; }
      try {
        const reply = await api(`/api/tasks/${encodeURIComponent(task.task_id)}/decision`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        note(reply.message);
        await load();
      } catch (error) { note(error.message, true); await load(); }
    };
    box.append(button);
  }

  function addRestartAction(box, task, text) {
    const button = document.createElement("button");
    button.className = "action restart";
    button.textContent = text;
    button.onclick = async () => {
      if (!await confirmAction({
        title: `${text}？`,
        message: "系统会复用原任务的说明、项目和服务创建一个全新任务，但不会复用旧审批。",
        confirmText: text,
      })) return;
      try {
        const reply = await api(`/api/tasks/${encodeURIComponent(task.task_id)}/restart`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ version: task.version }),
        });
        note(reply.message);
        await load();
      } catch (error) { note(error.message, true); await load(); }
    };
    box.append(button);
  }

  function addDeleteAction(box, task) {
    const button = document.createElement("button");
    button.className = "task-delete";
    button.textContent = "彻底删除";
    button.title = "永久删除这条历史任务记录";
    button.onclick = async (event) => {
      event.stopPropagation();
      const warning = "彻底删除后，任务卡、处理过程、排队消息和演示方案无法恢复。已生成文件、资料库和销售台账不会被删除。";
      if (!await confirmAction({
        title: "永久删除这条历史任务？",
        message: warning,
        confirmText: "永久删除",
        tone: "danger",
      })) return;
      button.disabled = true;
      try {
        const reply = await api(`/api/tasks/${encodeURIComponent(task.task_id)}/delete`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ version: task.version, confirmation: "永久删除" }),
        });
        taskCardExpansion.delete(task.task_id);
        note(reply.message);
        await load();
      } catch (error) { note(error.message, true); button.disabled = false; await load(); }
    };
    box.append(button);
  }

  function summaryRow(className, left, right) {
    const row = document.createElement("div");
    row.className = className;
    const name = document.createElement("span"); name.textContent = left;
    const detail = document.createElement("span"); detail.textContent = right;
    row.append(name, detail);
    return row;
  }

  const libraryCategoryLabels = {
    inbox: "待整理", customer: "客户与关键人", opportunity: "商机与方案", government: "政府与区域政策",
    bidding: "招投标", industry: "行业与竞争", sales_asset: "销售资产", company: "公司能力与资质",
    internal: "内部资源与负责人",
  };
  const libraryStatusLabels = {
    verified: "已核验", pending: "待核验", superseded: "已替代", rejected: "已拒绝", archived: "已归档",
  };
  const libraryKindLabels = {
    source: "来源记录", project_file: "项目文件", library_file: "资料库文件", artifact: "生成产物", url: "网页资料",
  };
  const libraryConfidentialityLabels = { internal: "内部使用", restricted: "限制传播", public: "可公开" };

  function libraryAccountName(identity) {
    const row = libraryState.accounts.find((item) => accountId(item) === identity);
    return row ? accountName(row) : identity;
  }

  function libraryBidName(bidId) {
    return libraryState.bids.find((item) => item.bid_id === bidId)?.name || bidId;
  }

  function libraryEntryTime(entry) {
    return entry.updated_at || entry.modified_at || entry.accessed_date || entry.published_date || entry.created_at || "";
  }

  function libraryTimestamp(value) {
    const text = String(value || "").trim();
    if (!text) return "时间未登记";
    const date = new Date(text);
    return Number.isNaN(date.getTime()) ? text : date.toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" });
  }

  function libraryCurrentPool() {
    return libraryState.category === "trash" ? libraryState.trash : libraryState.entries;
  }

  function librarySpecialMatch(entry) {
    if (!libraryState.specialFilter) return true;
    if (libraryState.specialFilter === "pending") return entry.status === "pending" || entry.category === "inbox";
    const now = Date.now();
    const moment = new Date(libraryState.specialFilter === "review" ? entry.review_at : (entry.created_at || entry.modified_at || entry.accessed_date)).getTime();
    if (!Number.isFinite(moment)) return false;
    if (libraryState.specialFilter === "review") return moment <= now + 30 * 86400000;
    if (libraryState.specialFilter === "week") return moment >= now - 7 * 86400000;
    return true;
  }

  function libraryFilteredEntries() {
    const query = libraryState.query.toLocaleLowerCase("zh-CN");
    const fields = ["title", "publisher", "topic", "region", "key_facts", "interpretation", "limitations", "important_quotes", "notes", "source_type"];
    return libraryCurrentPool().filter((entry) => {
      if (libraryState.category && libraryState.category !== "trash" && entry.category !== libraryState.category) return false;
      if (libraryState.status && entry.status !== libraryState.status) return false;
      if (libraryState.projectId && entry.project_id !== libraryState.projectId) return false;
      if (libraryState.accountId && entry.account_id !== libraryState.accountId) return false;
      if (!librarySpecialMatch(entry)) return false;
      if (!query) return true;
      const tags = Array.isArray(entry.tags) ? entry.tags.join(" ") : "";
      return fields.some((field) => String(entry[field] || "").toLocaleLowerCase("zh-CN").includes(query)) || tags.toLocaleLowerCase("zh-CN").includes(query);
    });
  }

  function libraryRow(entry) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `library-row ${entry.library_id === libraryState.selectedId ? "selected" : ""}`;
    const icon = document.createElement("span");
    icon.className = "library-row-icon";
    icon.textContent = entry.kind === "url" || entry.url ? "⌁" : entry.kind === "artifact" ? "⇩" : "▤";
    const copy = document.createElement("span");
    copy.className = "library-row-copy";
    const title = document.createElement("strong"); title.textContent = entry.title || "未命名资料";
    const meta = document.createElement("small");
    meta.textContent = [entry.publisher, entry.region, libraryTimestamp(libraryEntryTime(entry))].filter(Boolean).join(" · ");
    const relations = document.createElement("small");
    relations.className = "library-row-relations";
    relations.textContent = [entry.project_id && projectById(entry.project_id)?.name, entry.account_id && libraryAccountName(entry.account_id), entry.bid_id && libraryBidName(entry.bid_id)].filter(Boolean).join(" · ") || "尚未关联业务对象";
    copy.append(title, meta, relations);
    const badges = document.createElement("span"); badges.className = "library-row-badges";
    const category = document.createElement("b"); category.textContent = libraryCategoryLabels[entry.category] || "待整理";
    const status = document.createElement("b"); status.className = entry.status || "pending"; status.textContent = libraryStatusLabels[entry.status] || "待核验";
    badges.append(category, status);
    button.append(icon, copy, badges);
    button.onclick = () => { libraryState.selectedId = entry.library_id; libraryState.editingId = ""; libraryState.renderedKey = ""; renderLibrary(); };
    return button;
  }

  function libraryMetaLine(list, labelText, value) {
    if (value === undefined || value === null || String(value).trim() === "") return;
    const term = document.createElement("dt"); term.textContent = labelText;
    const description = document.createElement("dd"); description.textContent = String(value);
    list.append(term, description);
  }

  function libraryTextBlock(title, value, tone = "") {
    const section = document.createElement("section"); section.className = `library-text-block ${tone}`;
    const heading = document.createElement("strong"); heading.textContent = title;
    const body = document.createElement("p"); body.textContent = value;
    section.append(heading, body);
    return section;
  }

  function librarySelect(options, value) {
    const select = document.createElement("select");
    options.forEach(([optionValue, label]) => {
      const option = document.createElement("option"); option.value = optionValue; option.textContent = label; select.append(option);
    });
    if (value && !options.some(([optionValue]) => optionValue === value)) {
      const option = document.createElement("option"); option.value = value; option.textContent = `当前关联：${value}`; select.append(option);
    }
    select.value = value || "";
    return select;
  }

  function libraryEditor(entry) {
    const form = document.createElement("form"); form.className = "library-editor";
    const field = (labelText, control, className = "") => {
      const label = document.createElement("label"); if (className) label.className = className;
      const caption = document.createElement("span"); caption.textContent = labelText;
      label.append(caption, control); form.append(label); return control;
    };
    const title = document.createElement("input"); title.maxLength = 500; title.value = entry.title || ""; field("资料名称", title, "span-2");
    const category = field("资料分类", librarySelect([["inbox", "待整理"], ...Object.entries(libraryCategoryLabels).filter(([key]) => key !== "inbox")], entry.category));
    const status = field("核验状态", librarySelect(Object.entries(libraryStatusLabels), entry.status));
    const confidentiality = field("保密级别", librarySelect(Object.entries(libraryConfidentialityLabels), entry.confidentiality));
    const project = field("关联项目", librarySelect([["", "暂不关联"], ...(model.projects || []).filter((item) => item.status === "active").map((item) => [item.project_id, item.name])], entry.project_id));
    const account = field("关联客户", librarySelect([["", "暂不关联"], ...libraryState.accounts.map((item) => [accountId(item), accountName(item)])], entry.account_id));
    const bid = field("关联投标项目", librarySelect([["", "暂不关联"], ...libraryState.bids.map((item) => [item.bid_id, item.name])], entry.bid_id));
    const opportunity = document.createElement("input"); opportunity.maxLength = 128; opportunity.value = entry.opportunity_id || ""; opportunity.placeholder = "可选：商机编号"; field("商机编号", opportunity);
    const region = document.createElement("input"); region.maxLength = 200; region.value = entry.region || ""; field("地区", region);
    const topic = document.createElement("input"); topic.maxLength = 500; topic.value = entry.topic || ""; field("主题", topic);
    const publisher = document.createElement("input"); publisher.maxLength = 500; publisher.value = entry.publisher || ""; field("发布机构", publisher);
    const publishedDate = document.createElement("input"); publishedDate.maxLength = 40; publishedDate.value = entry.published_date || ""; publishedDate.placeholder = "YYYY-MM-DD"; field("发布日期", publishedDate);
    const reviewAt = document.createElement("input"); reviewAt.type = "date"; reviewAt.value = String(entry.review_at || "").slice(0, 10); field("下次复核日期", reviewAt);
    const tags = document.createElement("input"); tags.maxLength = 500; tags.value = (entry.tags || []).join("，"); tags.placeholder = "多个标签用逗号分隔"; field("标签", tags, "span-2");
    const notes = document.createElement("textarea"); notes.maxLength = 4000; notes.value = entry.notes || ""; notes.placeholder = "适用范围、使用限制或补充说明"; field("补充说明", notes, "span-2");
    const actions = document.createElement("div"); actions.className = "library-editor-actions span-2";
    const save = document.createElement("button"); save.type = "submit"; save.className = "primary"; save.textContent = "保存资料信息";
    const cancel = document.createElement("button"); cancel.type = "button"; cancel.className = "secondary"; cancel.textContent = "取消";
    cancel.onclick = () => { libraryState.editingId = ""; libraryState.previewKey = ""; renderLibraryPreview(); };
    actions.append(save, cancel); form.append(actions);
    form.onsubmit = async (event) => {
      event.preventDefault(); save.disabled = true;
      try {
        const payload = {
          library_id: entry.library_id, expected_version: entry.catalog_version || 0, title: title.value.trim(),
          category: category.value, status: status.value, confidentiality: confidentiality.value,
          project_id: project.value, account_id: account.value, bid_id: bid.value, opportunity_id: opportunity.value.trim(),
          region: region.value.trim(), topic: topic.value.trim(), publisher: publisher.value.trim(),
          published_date: publishedDate.value.trim(), review_at: reviewAt.value,
          tags: tags.value.split(/[，,]/u).map((item) => item.trim()).filter(Boolean), notes: notes.value.trim(),
        };
        const response = await api("/api/library/metadata", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
        note(response.message); libraryState.editingId = ""; await loadLibrary(true);
      } catch (error) { note(error.message, true); save.disabled = false; }
    };
    return form;
  }

  async function startLibraryTask(entry, serviceId, purpose) {
    const service = serviceById(serviceId);
    if (!service) { note("当前版本没有启用该业务服务。", true); return; }
    const linkedProject = entry.project_id && projectById(entry.project_id);
    if (linkedProject?.status === "active") selectedProject = linkedProject.project_id;
    selectedService = serviceId;
    const reference = entry.path || entry.url || entry.source_id || entry.library_id;
    const request = [
      `【资料库直接任务：${purpose}】`, `资料名称：${entry.title}`, `资料编号：${entry.library_id}`,
      `原件或来源：${reference}`, entry.account_id ? `关联客户：${libraryAccountName(entry.account_id)}（${entry.account_id}）` : "",
      entry.bid_id ? `关联投标项目：${libraryBidName(entry.bid_id)}（${entry.bid_id}）` : "",
      "请优先使用这份资料及其已登记业务关系；引用重要结论时回到原件或来源。资料不足时明确说明，不得把未核验内容写成事实。涉及台账写入或正式文件时继续等待人工确认。",
    ].filter(Boolean).join("\n");
    try {
      const response = await createTask(request); note(`任务已登记（${response.request_id}）。`); await load(); switchView("tasks");
    } catch (error) { note(error.message, true); }
  }

  function libraryTaskOptions(entry) {
    const options = [];
    if (["customer", "opportunity"].includes(entry.category)) options.push(["sales-review", "客户复盘"]);
    if (entry.category === "government") options.push(["government-proposal", "形成政府方案"]);
    if (entry.category === "bidding" && entry.bid_id) options.push(["bid-interpretation", "解读招标文件"]);
    if (entry.category === "industry") options.push(["industry-research", "继续行业调研"]);
    if (["sales_asset", "company", "internal", "inbox"].includes(entry.category)) options.push(["office-document", "整理与改写"]);
    if (["project_file", "library_file"].includes(entry.kind) && String(entry.path || "").toLocaleLowerCase("zh-CN").endsWith(".pdf") && entry.status !== "verified") options.push(["pdf-import", "提取页码证据"]);
    if (entry.category !== "inbox") options.push(["presentation-studio", "制作销售演示文稿"]);
    return options.filter(([serviceId], index) => options.findIndex(([candidate]) => candidate === serviceId) === index).slice(0, 3);
  }

  async function archiveLibraryEntry(entry) {
    if (!await confirmAction({
      title: "移入资料库回收站？",
      message: `“${entry.title}”将从日常检索中隐藏。原始文件或来源记录不会被永久删除，可随时恢复。`,
      confirmText: "移入回收站",
      tone: "danger",
    })) return;
    try {
      let response = await api("/api/library/archive", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ library_id: entry.library_id, expected_version: entry.catalog_version || 0 }) });
      if (response.requires_confirmation) {
        if (!await confirmAction({ title: "该资料已有引用", message: response.message, detail: (response.references || []).join("\n"), confirmText: "仍然移入回收站", tone: "danger" })) return;
        response = await api("/api/library/archive", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ library_id: entry.library_id, expected_version: entry.catalog_version || 0, acknowledge_references: true }) });
      }
      note(response.message); libraryState.selectedId = ""; await loadLibrary(true);
    } catch (error) { note(error.message, true); }
  }

  function renderLibraryPreview() {
    const pool = [...libraryState.entries, ...libraryState.trash];
    const entry = pool.find((item) => item.library_id === libraryState.selectedId);
    const preview = $("library-preview");
    const empty = $("library-preview-empty");
    if (!entry) { preview.hidden = true; empty.hidden = false; preview.replaceChildren(); return; }
    empty.hidden = true; preview.hidden = false;
    const key = `${libraryState.version}|${entry.library_id}|${entry.catalog_version}|${libraryState.editingId}`;
    if (key === libraryState.previewKey) return;
    libraryState.previewKey = key;
    const header = document.createElement("header"); header.className = "library-preview-header";
    const heading = document.createElement("div");
    const kicker = document.createElement("small"); kicker.textContent = libraryKindLabels[entry.kind] || "资料";
    const title = document.createElement("h2"); title.textContent = entry.title || "未命名资料";
    const badges = document.createElement("div"); badges.className = "knowledge-badges";
    [[libraryCategoryLabels[entry.category] || "待整理", "quality"], [libraryStatusLabels[entry.status] || "待核验", entry.status || "pending"]].forEach(([label, className]) => { const badge = document.createElement("span"); badge.className = `knowledge-badge ${className}`; badge.textContent = label; badges.append(badge); });
    heading.append(kicker, title, badges); header.append(heading); preview.replaceChildren(header);
    if (libraryState.editingId === entry.library_id) { preview.append(libraryEditor(entry)); return; }

    const relation = document.createElement("section"); relation.className = "library-relation-card";
    const relationTitle = document.createElement("strong"); relationTitle.textContent = "业务关联";
    const relationCopy = document.createElement("p");
    relationCopy.textContent = [entry.project_id && `项目：${projectById(entry.project_id)?.name || entry.project_id}`, entry.account_id && `客户：${libraryAccountName(entry.account_id)}`, entry.opportunity_id && `商机：${entry.opportunity_id}`, entry.bid_id && `投标：${libraryBidName(entry.bid_id)}`].filter(Boolean).join("\n") || "尚未关联客户、项目、商机或投标项目。";
    relation.append(relationTitle, relationCopy); preview.append(relation);
    if (entry.key_facts) preview.append(libraryTextBlock("主要事实", entry.key_facts));
    if (entry.interpretation) preview.append(libraryTextBlock("分析说明", entry.interpretation, "analysis"));
    if (entry.limitations) preview.append(libraryTextBlock("限制与提醒", entry.limitations, "warning"));
    if (entry.important_quotes) preview.append(libraryTextBlock("重要原文", entry.important_quotes, "quotes"));
    const metadata = document.createElement("dl"); metadata.className = "library-metadata";
    libraryMetaLine(metadata, "发布机构", entry.publisher); libraryMetaLine(metadata, "发布日期", entry.published_date);
    libraryMetaLine(metadata, "访问/更新时间", libraryTimestamp(libraryEntryTime(entry))); libraryMetaLine(metadata, "地区", entry.region);
    libraryMetaLine(metadata, "主题", entry.topic); libraryMetaLine(metadata, "资料类型", entry.source_type || libraryKindLabels[entry.kind]);
    libraryMetaLine(metadata, "保密级别", libraryConfidentialityLabels[entry.confidentiality] || entry.confidentiality);
    libraryMetaLine(metadata, "下次复核", entry.review_at); libraryMetaLine(metadata, "标签", (entry.tags || []).join("、"));
    libraryMetaLine(metadata, "资料编号", entry.library_id); if (metadata.children.length) preview.append(metadata);
    if (entry.notes) preview.append(libraryTextBlock("补充说明", entry.notes));
    if ((entry.versions || []).length) {
      const versions = document.createElement("section"); versions.className = "library-version-list";
      const headingText = document.createElement("strong"); headingText.textContent = `历史版本（${entry.versions.length}）`; versions.append(headingText);
      [...entry.versions].reverse().forEach((version, index) => {
        const row = document.createElement("div"); const name = document.createElement("span"); name.textContent = version.filename || version.version_id;
        const meta = document.createElement("small"); meta.textContent = `${index === 0 ? "当前版本 · " : ""}${fileSize(version.size || 0)} · ${libraryTimestamp(version.created_at)}`;
        row.append(name, meta); versions.append(row);
      }); preview.append(versions);
    }
    const primaryActions = document.createElement("div"); primaryActions.className = "library-preview-actions";
    if (entry.url || entry.path) {
      const open = document.createElement("button"); open.type = "button"; open.className = "primary"; open.textContent = entry.url ? "打开来源" : "打开原件";
      open.onclick = async () => { open.disabled = true; try { const response = await api("/api/library/open", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ library_id: entry.library_id }) }); note(response.message); } catch (error) { note(error.message, true); } finally { open.disabled = false; } };
      primaryActions.append(open);
    }
    if (!entry.deleted_at) {
      const edit = document.createElement("button"); edit.type = "button"; edit.className = "secondary"; edit.textContent = "编辑资料信息"; edit.onclick = () => { libraryState.editingId = entry.library_id; libraryState.previewKey = ""; renderLibraryPreview(); }; primaryActions.append(edit);
      if (entry.kind === "library_file") { const version = document.createElement("button"); version.type = "button"; version.className = "secondary"; version.textContent = "上传新版本"; version.onclick = () => { libraryState.versionTargetId = entry.library_id; $("library-version-input").click(); }; primaryActions.append(version); }
    }
    preview.append(primaryActions);
    if (!entry.deleted_at) {
      const taskActions = document.createElement("section"); taskActions.className = "library-task-actions";
      const taskTitle = document.createElement("strong"); taskTitle.textContent = "直接用于工作"; taskActions.append(taskTitle);
      const options = libraryTaskOptions(entry);
      options.forEach(([serviceId, label]) => { const button = document.createElement("button"); button.type = "button"; button.className = "secondary"; button.textContent = label; button.onclick = () => startLibraryTask(entry, serviceId, label); taskActions.append(button); });
      if (entry.category === "bidding" && !entry.bid_id) {
        const hint = document.createElement("small"); hint.textContent = "先编辑资料信息并关联投标项目，即可直接开始招标解读。"; taskActions.append(hint);
      }
      preview.append(taskActions);
      const danger = document.createElement("button"); danger.type = "button"; danger.className = "danger-outline library-archive"; danger.textContent = "移入资料库回收站"; danger.onclick = () => archiveLibraryEntry(entry); preview.append(danger);
    } else {
      const restore = document.createElement("button"); restore.type = "button"; restore.className = "primary library-restore"; restore.textContent = "恢复到资料库";
      restore.onclick = async () => { restore.disabled = true; try { const response = await api("/api/library/restore", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ library_id: entry.library_id, expected_version: entry.catalog_version }) }); note(response.message); await loadLibrary(true); } catch (error) { note(error.message, true); restore.disabled = false; } };
      preview.append(restore);
    }
  }

  function renderLibraryFilters() {
    const project = $("library-project-filter"); const currentProject = project.value || libraryState.projectId;
    project.replaceChildren(...[["", "全部项目"], ...(model.projects || []).map((item) => [item.project_id, item.name])].map(([value, label]) => { const option = document.createElement("option"); option.value = value; option.textContent = label; return option; }));
    project.value = currentProject;
    const account = $("library-account-filter"); const currentAccount = account.value || libraryState.accountId;
    account.replaceChildren(...[["", "全部客户"], ...libraryState.accounts.map((item) => [accountId(item), accountName(item)])].map(([value, label]) => { const option = document.createElement("option"); option.value = value; option.textContent = label; return option; }));
    account.value = currentAccount;
  }

  function renderLibrary() {
    if (!model) return;
    renderLibraryFilters();
    const stats = libraryState.stats || {};
    $("library-stat-pending").textContent = String(stats.pending || 0); $("library-stat-verified").textContent = String(stats.verified || 0);
    $("library-stat-review").textContent = String(stats.review_due || 0); $("library-stat-week").textContent = String(stats.this_week || 0);
    $("library-category-all").textContent = String(stats.total || 0); $("library-category-trash").textContent = String(stats.trash || 0);
    Object.keys(libraryCategoryLabels).forEach((category) => { const target = $(`library-category-${category}`); if (target) target.textContent = String(stats.categories?.[category] || 0); });
    document.querySelectorAll("[data-library-category]").forEach((button) => button.classList.toggle("active", button.dataset.libraryCategory === libraryState.category));
    const title = libraryState.category === "trash" ? "资料库回收站" : libraryState.category ? libraryCategoryLabels[libraryState.category] : "全部资料";
    $("library-list-title").textContent = title;
    const box = $("library-records"); const filtered = libraryFilteredEntries();
    const renderedKey = `${libraryState.version}|${libraryState.category}|${libraryState.query}|${libraryState.status}|${libraryState.projectId}|${libraryState.accountId}|${libraryState.specialFilter}|${libraryState.selectedId}|${libraryState.error}`;
    if (renderedKey !== libraryState.renderedKey) {
      libraryState.renderedKey = renderedKey; const scrollTop = box.scrollTop;
      $("library-count").textContent = String(filtered.length);
      $("library-list-status").textContent = libraryState.error ? `资料库暂时不可用：${libraryState.error}` : libraryState.warning || `显示 ${filtered.length} 条，共 ${libraryCurrentPool().length} 条`;
      box.classList.toggle("empty", !filtered.length);
      if (libraryState.error) { box.replaceChildren(); box.textContent = `资料库暂时无法读取：${libraryState.error}`; }
      else if (!filtered.length) { box.replaceChildren(); box.textContent = libraryState.category === "trash" ? "回收站为空。" : "没有符合当前条件的资料。可以上传文件或登记网页。"; }
      else box.replaceChildren(...filtered.map(libraryRow));
      queueMicrotask(() => { box.scrollTop = Math.min(scrollTop, Math.max(0, box.scrollHeight - box.clientHeight)); });
    }
    renderLibraryPreview();
  }

  async function loadLibrary(force = false) {
    if (libraryState.loading || (!force && libraryState.expectedRevision && libraryState.expectedRevision === model?.library_revision && libraryState.version)) return;
    libraryState.loading = true;
    try {
      const [snapshot, accounts, bids] = await Promise.all([
        api("/api/library"),
        api("/api/accounts?limit=100").catch(() => ({ rows: libraryState.accounts })),
        api("/api/bids?limit=100").catch(() => ({ rows: libraryState.bids })),
      ]);
      libraryState.entries = Array.isArray(snapshot.entries) ? snapshot.entries : [];
      libraryState.trash = Array.isArray(snapshot.trash) ? snapshot.trash : [];
      libraryState.stats = snapshot.stats || {}; libraryState.version = snapshot.version || String(Date.now());
      libraryState.warning = snapshot.warning || ""; libraryState.error = "";
      libraryState.accounts = Array.isArray(accounts.rows) ? accounts.rows : [];
      libraryState.bids = Array.isArray(bids.rows) ? bids.rows : [];
      libraryState.expectedRevision = model?.library_revision || "";
      if (libraryState.selectedId && ![...libraryState.entries, ...libraryState.trash].some((item) => item.library_id === libraryState.selectedId)) libraryState.selectedId = "";
    } catch (error) { libraryState.error = error.message; }
    finally { libraryState.loading = false; libraryState.renderedKey = ""; libraryState.previewKey = ""; renderLibrary(); }
  }

  function renderData() {
    const renderGroup = (box, items) => box.replaceChildren(...items.map((item) => summaryRow("summary-row", item.path.split("/").pop(), item.exists ? `${item.records ?? "?"} 条 · ${item.updated_at || "未知时间"}` : "尚未创建")));
    renderGroup($("sales-summary"), model.data.sales || []);
    renderLibrary();
  }

  function accountId(account) { return String(account?.account_id || account?.customer_id || account?.id || ""); }
  function accountName(account) { return String(account?.account_name || account?.customer_name || account?.name || "未命名客户"); }
  function accountField(account, ...names) { return names.map((name) => account?.[name]).find((value) => value !== undefined && value !== null && String(value).trim()) || ""; }
  function customerContextAccount() { return customerState.rows.find((row) => accountId(row) === customerState.selectedId) || customerState.detail?.account || null; }
  function selectedCustomerContextText() {
    const account = customerContextAccount();
    return account ? `\n【当前客户上下文】\n客户：${accountName(account)}\n客户编号：${accountId(account)}\n请优先结合该客户的全景、时间线、风险、行动与证据；信息不足时明确待确认。` : "";
  }
  function formatCustomerTime(value) {
    const text = String(value || "").trim();
    if (!text) return "暂无记录";
    const time = new Date(text);
    return Number.isNaN(time.getTime()) ? text : `${time.getFullYear()}-${String(time.getMonth() + 1).padStart(2, "0")}-${String(time.getDate()).padStart(2, "0")}`;
  }
  function freshnessText(value) {
    const time = new Date(value || "");
    if (Number.isNaN(time.getTime())) return "新鲜度待确认";
    const days = Math.max(0, Math.floor((Date.now() - time.getTime()) / 86400000));
    return days === 0 ? "今天更新" : days <= 7 ? `${days} 天前更新` : `${days} 天未更新`;
  }
  function customerHealth(value) {
    const raw = String(value || "").trim();
    const normalized = raw.toLowerCase();
    const text = ({ healthy: "健康", good: "健康", normal: "一般", watch: "需关注", risk: "有风险", at_risk: "有风险", red: "有风险" })[normalized] || raw || "未标注";
    const tone = /风险|关注|risk|watch|red/iu.test(raw) ? "risk" : /健康|good|healthy/iu.test(raw) ? "healthy" : "normal";
    return { text, tone };
  }
  const customerValueLabels = {
    activity: "互动", commitment: "承诺", task_link: "关联任务", write_receipt: "批准写入", sales_asset: "销售资料", artifact: "正式产出",
    overdue_action: "行动逾期", stale_account: "长期无互动", commitment_due: "承诺临期",
    missing_critical_field: "关键信息缺失", resource_deadline: "资源申请临期",
    verified: "已核验", pending: "待核验", missing_file: "来源文件缺失", rejected: "已否定",
    superseded: "已被取代", legacy_text: "历史文本", unknown: "待确认", open: "待处理", overdue: "已逾期",
    fulfilled: "已完成", completed: "已完成", cancelled: "已取消", committed: "已提交", linked: "已关联",
    ready: "可使用", active: "使用中", draft: "草稿", proposal: "方案阶段", discovery: "需求探索",
    qualification: "资格确认", negotiation: "商务谈判", won: "已赢单", lost: "已失单",
    customer_to_us: "客户对我方", us_to_customer: "我方对客户", mutual: "双方承诺",
    fact: "事实", analysis: "分析", hypothesis: "假设",
  };
  function customerDisplayValue(value) {
    const text = String(value ?? "").trim();
    return customerValueLabels[text.toLowerCase()] || text;
  }
  function accountOptionValues(fieldNames) {
    return [...new Set(customerState.rows.map((row) => String(accountField(row, ...fieldNames)).trim()).filter(Boolean))].sort((a, b) => a.localeCompare(b, "zh-CN"));
  }
  function updateCustomerFilterOptions() {
    const configs = [
      ["customer-owner-options", ["owner", "owner_name", "account_owner", "负责人"]], ["customer-region-options", ["region", "area", "地区"]],
      ["customer-industry-options", ["sector", "industry", "行业"]], ["customer-stage-options", ["lifecycle_stage", "stage", "sales_stage", "阶段"]], ["customer-health-options", ["health", "health_status", "健康度"]],
    ];
    configs.forEach(([id, fields]) => {
      const list = $(id); if (!list) return;
      list.replaceChildren(...accountOptionValues(fields).map((value) => new Option(value, value)));
    });
  }
  function customerQuery() {
    const params = new URLSearchParams();
    const filterMap = { query: "query", owner: "owner", region: "region", industry: "sector", stage: "lifecycle_stage", health: "health" };
    Object.entries(filterMap).forEach(([key, apiKey]) => { if (customerState.filters[key]) params.set(apiKey, customerState.filters[key]); });
    if (customerState.filters.updated) { const since = new Date(); since.setDate(since.getDate() - Number(customerState.filters.updated)); params.set("updated_since", since.toISOString()); }
    if (customerState.cursor) params.set("cursor", customerState.cursor);
    return params.toString();
  }
  async function loadCustomers({ append = false, force = false } = {}) {
    if (customerState.loading && !force) return;
    customerState.listController?.abort();
    const controller = new AbortController(); customerState.listController = controller;
    const generation = ++customerState.listGeneration;
    customerState.loading = true; customerState.error = ""; renderCustomerOperations();
    try {
      const response = await api(`/api/accounts?${customerQuery()}`, { signal: controller.signal });
      if (generation !== customerState.listGeneration) return;
      const rows = Array.isArray(response.rows) ? response.rows : Array.isArray(response.accounts) ? response.accounts : [];
      customerState.rows = append ? [...customerState.rows, ...rows.filter((row) => !customerState.rows.some((old) => accountId(old) === accountId(row)))] : rows;
      customerState.cursor = String(response.next_cursor || response.cursor || ""); customerState.hasMore = Boolean(response.has_more || customerState.cursor); customerState.loaded = true;
      updateCustomerFilterOptions();
    } catch (error) { if (error.name !== "AbortError" && generation === customerState.listGeneration) customerState.error = error.message || "客户列表暂时无法读取"; }
    finally { if (generation === customerState.listGeneration) { customerState.loading = false; renderCustomerOperations(); } }
  }
  async function loadAttention({ force = false } = {}) {
    if ((customerState.attentionLoaded || customerState.attentionLoading) && !force) return;
    customerState.attentionController?.abort(); const controller = new AbortController(); customerState.attentionController = controller;
    const generation = ++customerState.attentionGeneration;
    customerState.attentionLoading = true; customerState.attentionError = ""; renderAttention();
    try {
      const response = await api("/api/attention", { signal: controller.signal });
      if (generation !== customerState.attentionGeneration) return;
      customerState.attention = Array.isArray(response.rows) ? response.rows : Array.isArray(response.items) ? response.items : [];
      customerState.attentionLoaded = true; customerState.attentionError = "";
    } catch (error) { if (error.name !== "AbortError" && generation === customerState.attentionGeneration) { customerState.attentionError = error.message || "今日关注暂时无法读取"; customerState.attentionLoaded = true; } }
    finally { if (generation === customerState.attentionGeneration) { customerState.attentionLoading = false; renderAttention(); } }
  }
  async function loadGlobalRecommendations({ force = false } = {}) {
    if (customerState.recommendationsLoading && !force) return;
    const generation = ++customerState.recommendationsGeneration;
    customerState.recommendationsLoading = true; customerState.recommendationsError = ""; customerState.recommendations = [];
    try {
      const response = await api("/api/a4/recommendations");
      if (generation !== customerState.recommendationsGeneration) return;
      if (response.success && Array.isArray(response.data?.recommendations)) {
        customerState.recommendations = response.data.recommendations.filter((rec) => rec.status === "pending");
      } else {
        customerState.recommendationsError = response.error || "建议暂时无法读取";
      }
    } catch (error) {
      if (generation === customerState.recommendationsGeneration) {
        customerState.recommendationsError = error.message || "建议暂时无法读取";
      }
    } finally {
      if (generation === customerState.recommendationsGeneration) {
        customerState.recommendationsLoading = false;
        renderGlobalRecommendations();
      }
    }
  }
  async function loadCustomerRecommendations(accountId) {
    if (!accountId || customerState.recommendationsLoading) return;
    const generation = ++customerState.recommendationsGeneration;
    customerState.recommendationsLoading = true; customerState.recommendationsError = ""; customerState.recommendations = [];
    try {
      const response = await api(`/api/a4/recommendations?account_id=${encodeURIComponent(accountId)}`);
      if (generation !== customerState.recommendationsGeneration) return;
      if (response.success && Array.isArray(response.data?.recommendations)) {
        customerState.recommendations = response.data.recommendations.filter((rec) => rec.status === "pending");
      } else {
        customerState.recommendationsError = response.error || "建议暂时无法读取";
      }
    } catch (error) {
      if (generation === customerState.recommendationsGeneration) {
        customerState.recommendationsError = error.message || "建议暂时无法读取";
      }
    } finally {
      if (generation === customerState.recommendationsGeneration) {
        customerState.recommendationsLoading = false;
        renderCustomerDetail();
      }
    }
  }
  async function acceptRecommendation(recommendationId, userEdits = null) {
    try {
      const body = { recommendation_id: recommendationId };
      if (userEdits) body.user_edits = userEdits;
      const response = await api("/api/a4/recommendations/accept", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!response.success) throw new Error(response.error || "接受建议失败");
      await loadCustomerRecommendations(customerState.selectedId);
      return true;
    } catch (error) {
      await confirmAction({ title: "操作失败", message: error.message, confirmText: "知道了", tone: "danger" });
      return false;
    }
  }
  async function ignoreRecommendation(recommendationId, reason = null) {
    try {
      const body = { recommendation_id: recommendationId };
      if (reason) body.reason = reason;
      const response = await api("/api/a4/recommendations/ignore", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!response.success) throw new Error(response.error || "忽略建议失败");
      await loadCustomerRecommendations(customerState.selectedId);
      return true;
    } catch (error) {
      await confirmAction({ title: "操作失败", message: error.message, confirmText: "知道了", tone: "danger" });
      return false;
    }
  }
  async function loadCustomerSignals(selectedAccountId) {
    if (!selectedAccountId || customerState.signalsLoading) return;
    const generation = ++customerState.signalsGeneration;
    customerState.signalsLoading = true; customerState.signalsError = ""; customerState.signals = [];
    try {
      const accountData = customerState.rows.find((row) => accountId(row) === selectedAccountId);
      if (!accountData) {
        customerState.signalsError = "客户数据不存在";
        return;
      }
      const response = await api("/api/a4/evaluate-signals", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(accountData) });
      if (generation !== customerState.signalsGeneration) return;
      if (response.success && Array.isArray(response.data?.signals)) {
        customerState.signals = response.data.signals.filter((sig) => sig.severity === "high" || sig.severity === "medium");
      } else {
        customerState.signalsError = response.error || "信号暂时无法读取";
      }
    } catch (error) {
      if (generation === customerState.signalsGeneration) {
        customerState.signalsError = error.message || "信号暂时无法读取";
      }
    } finally {
      if (generation === customerState.signalsGeneration) {
        customerState.signalsLoading = false;
        renderCustomerDetail();
      }
    }
  }
  function customerTabFor(section) {
    return ({ activities: "timeline", commitments: "actions", actions: "actions", risks: "signals", resource_requests: "resources", sales_assets: "resources", artifacts: "resources", evidence_refs: "evidence", task_links: "evidence" })[String(section || "")] || (String(section || "") || "overview");
  }
  async function selectCustomer(id, { tab = "overview", focus = false } = {}) {
    const customerId = String(id || ""); if (!customerId) return;
    customerState.selectedId = customerId; customerState.activeTab = customerTabFor(tab); customerState.detail = null; customerState.timeline = []; customerState.timelineCursor = ""; customerState.timelineHasMore = false; customerState.detailError = ""; customerState.timelineError = ""; customerState.detailLoading = true; customerState.timelineLoading = true;
    customerState.detailController?.abort(); const controller = new AbortController(); customerState.detailController = controller; const generation = ++customerState.detailGeneration;
    customerState.timelineController?.abort(); customerState.timelineGeneration += 1;
    renderCustomerOperations(); renderCustomerContext();
    loadCustomerRecommendations(customerId);
    loadCustomerSignals(customerId);
    let focusAnchor = null;
    if (focus) queueMicrotask(() => { if (generation === customerState.detailGeneration && customerState.selectedId === customerId) { focusAnchor = $("customer-detail").hidden ? $("customer-detail-empty") : $("customer-detail"); focusAnchor?.focus?.({ preventScroll: true }); } });
    try {
      const [detail, timeline] = await Promise.allSettled([
        api(`/api/accounts/${encodeURIComponent(customerId)}/360`, { signal: controller.signal }), api(`/api/accounts/${encodeURIComponent(customerId)}/timeline`, { signal: controller.signal }),
      ]);
      if (generation !== customerState.detailGeneration) return;
      if (detail.status === "fulfilled") customerState.detail = detail.value; else if (detail.reason?.name !== "AbortError") customerState.detailError = detail.reason?.message || "客户全景暂时无法读取";
      if (timeline.status === "fulfilled") { customerState.timeline = Array.isArray(timeline.value.rows) ? timeline.value.rows : Array.isArray(timeline.value.timeline) ? timeline.value.timeline : []; customerState.timelineCursor = String(timeline.value.next_cursor || ""); customerState.timelineHasMore = Boolean(timeline.value.has_more && customerState.timelineCursor); } else if (timeline.reason?.name !== "AbortError") customerState.timelineError = timeline.reason?.message || "时间线暂时无法读取";
    } finally { if (generation === customerState.detailGeneration) { const keepDetailFocus = Boolean(focus && focusAnchor && document.activeElement === focusAnchor); customerState.detailLoading = false; customerState.timelineLoading = false; renderCustomerOperations(); renderCustomerContext(); if (keepDetailFocus) $("customer-detail")?.focus?.({ preventScroll: true }); } }
  }
  async function loadMoreCustomerTimeline() {
    if (!customerState.selectedId || !customerState.timelineCursor || customerState.timelineLoading) return;
    customerState.timelineController?.abort(); const controller = new AbortController(); customerState.timelineController = controller; const generation = ++customerState.timelineGeneration;
    const accountAtStart = customerState.selectedId; customerState.timelineLoading = true; customerState.timelineError = ""; renderCustomerDetail();
    try {
      const response = await api(`/api/accounts/${encodeURIComponent(accountAtStart)}/timeline?cursor=${encodeURIComponent(customerState.timelineCursor)}`, { signal: controller.signal });
      if (generation !== customerState.timelineGeneration || accountAtStart !== customerState.selectedId) return;
      const rows = Array.isArray(response.rows) ? response.rows : [];
      const known = new Set(customerState.timeline.map((item) => String(item.timeline_id || `${item.kind}:${item.evidence_id}`)));
      customerState.timeline.push(...rows.filter((item) => !known.has(String(item.timeline_id || `${item.kind}:${item.evidence_id}`))));
      customerState.timelineCursor = String(response.next_cursor || ""); customerState.timelineHasMore = Boolean(response.has_more && customerState.timelineCursor);
    } catch (error) { if (error.name !== "AbortError" && generation === customerState.timelineGeneration) customerState.timelineError = error.message || "更多时间线暂时无法读取"; }
    finally { if (generation === customerState.timelineGeneration) { customerState.timelineLoading = false; renderCustomerDetail(); } }
  }
  function renderCustomerContext() {
    const account = customerContextAccount(); const bar = $("customer-context-bar"); const inline = $("quick-customer-context"); const global = $("global-customer-context");
    bar.hidden = !account; inline.hidden = !account; global.hidden = !account;
    if (!account) return;
    const opportunity = customerState.detail?.sections?.opportunities?.[0];
    const stage = customerDisplayValue(accountField(opportunity, "stage") || accountField(account, "lifecycle_stage", "stage", "sales_stage", "阶段"));
    const metadata = [accountField(opportunity, "name", "opportunity_name"), accountField(account, "owner", "owner_name", "负责人"), stage, `更新于 ${formatCustomerTime(accountField(account, "updated_at", "last_updated_at"))}`].filter(Boolean);
    $("customer-context-name").textContent = accountName(account); $("quick-customer-context-name").textContent = `当前客户：${accountName(account)}`; $("global-customer-context-name").textContent = accountName(account);
    $("customer-context-meta").textContent = metadata.join(" · ") || "已带入后续工作";
  }
  function clearCustomerContext() {
    customerState.detailController?.abort(); customerState.timelineController?.abort(); customerState.detailGeneration += 1; customerState.timelineGeneration += 1;
    customerState.selectedId = ""; customerState.detail = null; customerState.timeline = []; customerState.timelineCursor = ""; customerState.timelineHasMore = false;
    customerState.detailLoading = false; customerState.timelineLoading = false; customerState.detailError = ""; customerState.timelineError = "";
    renderCustomerOperations(); renderCustomerContext();
  }
  function customerCard(account) {
    const id = accountId(account); const button = document.createElement("button"); button.type = "button"; button.className = `customer-row ${id === customerState.selectedId ? "selected" : ""}`; button.setAttribute("aria-pressed", String(id === customerState.selectedId));
    const header = document.createElement("div"); const name = document.createElement("strong"); name.textContent = accountName(account); const health = customerHealth(accountField(account, "health", "health_status", "健康度")); const badge = document.createElement("span"); badge.className = `customer-health ${health.tone}`; badge.textContent = health.text; header.append(name, badge);
    const meta = document.createElement("small"); meta.textContent = [accountField(account, "owner", "owner_name", "负责人") || "负责人待补充", customerDisplayValue(accountField(account, "lifecycle_stage", "stage", "sales_stage", "阶段")) || "阶段待补充", accountField(account, "region", "地区")].filter(Boolean).join(" · ");
    const metrics = document.createElement("div"); metrics.className = "customer-row-metrics"; const activity = accountField(account, "last_activity_at", "last_activity", "最近互动"); const actions = accountField(account, "open_actions", "action_count"); const risks = accountField(account, "open_risks", "risk_count");
    [["最近互动", formatCustomerTime(activity)], ["开放行动", actions || "0"], ["风险", risks || "0"], ["新鲜度", freshnessText(accountField(account, "updated_at", "last_updated_at", "last_activity_at"))]].forEach(([labelText, value]) => { const item = document.createElement("span"); item.textContent = `${labelText}：${value}`; metrics.append(item); });
    button.append(header, meta, metrics); button.onclick = () => selectCustomer(id, { focus: true }); return button;
  }
  function detailSection(title, rows, fields) {
    const section = document.createElement("section"); section.className = "customer-section"; const heading = document.createElement("h3"); heading.textContent = title; section.append(heading);
    if (!Array.isArray(rows) || !rows.length) { const empty = document.createElement("p"); empty.className = "hint"; empty.textContent = "暂无可显示记录。"; section.append(empty); return section; }
    const list = document.createElement("div"); list.className = "customer-detail-list"; rows.slice(0, 100).forEach((row) => { const item = document.createElement("article"); fields.forEach(([labelText, names, displayKind]) => { const value = accountField(row, ...names); if (value === "" || value === null || value === undefined) return; const line = document.createElement("p"); const key = document.createElement("strong"); key.textContent = `${labelText}：`; line.append(key); if (displayKind === "source-url") { const open = document.createElement("button"); open.type = "button"; open.className = "customer-source-link"; open.textContent = "打开来源"; open.title = String(value); open.onclick = async () => { open.disabled = true; try { const response = await api("/api/knowledge/source/open", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: String(value) }) }); note(response.message); } catch (error) { note(error.message, true); } finally { open.disabled = false; } }; line.append(open); } else line.append(document.createTextNode(customerDisplayValue(value))); item.append(line); }); if (!item.children.length) item.textContent = "该记录尚无可显示字段。"; list.append(item); }); section.append(list); return section;
  }
  function opportunityAmount(row) {
    const minimum = row?.amount_min_minor; const maximum = row?.amount_max_minor;
    if (minimum === null || minimum === undefined) { if (maximum === null || maximum === undefined) return ""; }
    const format = (value) => Number.isFinite(Number(value)) ? new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(Number(value) / 100) : "";
    const range = minimum !== null && minimum !== undefined && maximum !== null && maximum !== undefined ? `${format(minimum)}–${format(maximum)}` : format(minimum ?? maximum);
    return [row?.currency, range].filter(Boolean).join(" ");
  }
  function missingCustomerFacts(account, sections) {
    const missing = [];
    if (!accountField(account, "owner", "owner_name")) missing.push("负责人");
    if (!accountField(account, "lifecycle_stage", "stage")) missing.push("客户阶段");
    if (!accountField(account, "region")) missing.push("地区");
    if (!accountField(account, "sector", "industry")) missing.push("行业");
    if (!Array.isArray(sections.contacts) || !sections.contacts.length) missing.push("关键人");
    if (!Array.isArray(sections.opportunities) || !sections.opportunities.length) missing.push("销售机会");
    return missing;
  }
  function detailTabs() {
    const detail = customerState.detail || {}; const sections = detail.sections || {}; const account = detail.account || customerContextAccount() || {}; const tabs = [
      ["overview", "概览"], ["timeline", "时间线"], ["contacts", "关键人"], ["opportunities", "机会"], ["signals", "信号"], ["resources", "资源与资料"], ["evidence", "证据"],
    ];
    tabs.splice(4, 0, ["actions", "行动与承诺"]);
    const tabBox = $("customer-tabs"); tabBox.replaceChildren(...tabs.map(([id, text]) => { const button = document.createElement("button"); button.type = "button"; button.id = `customer-tab-${id}`; button.setAttribute("role", "tab"); button.setAttribute("aria-controls", "customer-tab-panel"); button.textContent = text; button.dataset.tab = id; const active = customerState.activeTab === id; button.className = active ? "active" : ""; button.setAttribute("aria-selected", String(active)); button.tabIndex = active ? 0 : -1; button.onclick = () => { customerState.activeTab = id; renderCustomerDetail(); }; button.onkeydown = (event) => { if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return; event.preventDefault(); const buttons = [...tabBox.querySelectorAll("button")]; let index = buttons.indexOf(button); if (event.key === "ArrowLeft") index = (index - 1 + buttons.length) % buttons.length; if (event.key === "ArrowRight") index = (index + 1) % buttons.length; if (event.key === "Home") index = 0; if (event.key === "End") index = buttons.length - 1; const targetId = buttons[index].dataset.tab; buttons[index].click(); queueMicrotask(() => tabBox.querySelector(`[data-tab="${targetId}"]`)?.focus({ preventScroll: true })); }; return button; }));
    const panel = $("customer-tab-panel"); panel.setAttribute("aria-labelledby", `customer-tab-${customerState.activeTab}`); panel.replaceChildren();
    if (customerState.activeTab === "overview") {
      const missing = missingCustomerFacts(account, sections);
      panel.append(
        detailSection("客户事实", [account], [["负责人", ["owner", "owner_name", "负责人"]], ["阶段", ["lifecycle_stage", "stage", "sales_stage", "阶段"]], ["行业", ["sector", "industry", "行业"]], ["地区", ["region", "地区"]], ["健康度", ["health", "health_status", "健康度"]], ["信息更新时间", ["updated_at", "last_updated_at"]], ["概况", ["summary"]]]),
        detailSection("当前机会", (sections.opportunities || []).slice(0, 3).map((row) => ({ ...row, amount_display: opportunityAmount(row) })), [["机会", ["name", "opportunity_name", "title"]], ["阶段", ["stage"]], ["负责人", ["owner"]], ["金额范围", ["amount_display"]], ["预计决策时间", ["expected_decision_at"]], ["下一阶段条件", ["next_stage_condition"]]]),
        detailSection("主要风险", (sections.risks || []).filter((row) => !["closed", "resolved", "cancelled"].includes(String(row.status || "").toLowerCase())).slice(0, 3), [["风险", ["risk_text", "risk", "summary"]], ["影响", ["impact"]], ["可能性", ["likelihood"]], ["负责人", ["owner"]], ["缓解动作", ["mitigation_action"]], ["状态", ["status"]]]),
        detailSection("首要行动", (sections.actions || []).filter((row) => !["completed", "cancelled"].includes(String(row.status || "").toLowerCase())).slice(0, 3), [["行动", ["action_text", "title", "summary"]], ["负责人", ["owner"]], ["截止日期", ["due_at"]], ["优先级", ["priority"]], ["状态", ["status"]]])
      );
      if (missing.length) panel.append(detailSection("待补充信息", [{ missing: missing.join("、") }], [["尚未登记", ["missing"]]]));
    } else if (customerState.activeTab === "timeline") {
      if (!customerState.timeline.length && customerState.timelineLoading) panel.textContent = "正在读取时间线…"; else { if (customerState.timelineError) { const error = document.createElement("p"); error.className = "customer-inline-error"; error.textContent = `时间线暂时无法读取：${customerState.timelineError}`; panel.append(error); } panel.append(detailSection("客户时间线", customerState.timeline, [["时间", ["event_at", "occurred_at", "created_at", "date"]], ["类型", ["kind", "activity_type", "type", "event_type"]], ["事项", ["title", "summary", "description"]], ["状态", ["status"]], ["证据类型", ["evidence_type"]], ["证据编号", ["evidence_id"]]])); if (customerState.timelineHasMore) { const more = document.createElement("button"); more.type = "button"; more.className = "secondary customer-timeline-more"; more.textContent = customerState.timelineLoading ? "正在加载…" : "加载更多时间线"; more.disabled = customerState.timelineLoading; more.onclick = loadMoreCustomerTimeline; panel.append(more); } }
    } else if (customerState.activeTab === "contacts") panel.append(detailSection("关键人", sections.contacts, [["姓名", ["display_name", "name", "contact_name"]], ["职位", ["title"]], ["角色", ["role"]], ["影响力", ["influence_level", "influence"]], ["决策关系", ["decision_role"]], ["身份状态", ["identity_status"]], ["邮箱", ["email"]], ["电话", ["phone"]]]));
    else if (customerState.activeTab === "opportunities") panel.append(detailSection("销售机会", (sections.opportunities || []).map((row) => ({ ...row, amount_display: opportunityAmount(row) })), [["机会", ["name", "opportunity_name", "title"]], ["阶段", ["stage", "sales_stage"]], ["健康度", ["health"]], ["负责人", ["owner"]], ["金额范围", ["amount_display"]], ["预计决策时间", ["expected_decision_at"]], ["下一阶段条件", ["next_stage_condition"]], ["赢单判断", ["win_hypothesis"]]]));
    else if (customerState.activeTab === "actions") panel.append(
      detailSection("我方行动", sections.actions, [["行动", ["action_text", "title", "summary"]], ["负责人", ["owner"]], ["截止日期", ["due_at"]], ["优先级", ["priority"]], ["状态", ["status"]], ["来源任务", ["source_task_id"]], ["完成依据", ["completion_evidence"]]]),
      detailSection("客户与双方承诺", sections.commitments, [["承诺", ["commitment_text", "title", "summary"]], ["方向", ["direction"]], ["截止日期", ["due_at"]], ["状态", ["status"]], ["来源互动", ["source_activity_id"]]])
    );
    else if (customerState.activeTab === "signals") panel.append(
      detailSection("确定性信号", sections.signals, [["信号", ["signal_type", "title", "summary"]], ["等级", ["severity", "level"]], ["状态", ["status"]], ["计算时间", ["last_seen_at", "updated_at"]], ["触发对象", ["subject_type"]], ["触发记录", ["subject_id"]], ["规则版本", ["rule_version"]]]),
      detailSection("已登记风险", sections.risks, [["风险", ["risk_text", "risk", "summary"]], ["类别", ["category"]], ["影响", ["impact"]], ["可能性", ["likelihood"]], ["负责人", ["owner"]], ["缓解动作", ["mitigation_action"]], ["状态", ["status"]]])
    );
    else if (customerState.activeTab === "resources") { panel.append(
      detailSection("资源请求", sections.resource_requests, [["需求", ["request_summary", "summary", "title"]], ["业务原因", ["business_reason"]], ["截止日期", ["deadline", "due_date"]], ["状态", ["status"]], ["负责人", ["owner", "owner_name"]], ["审批决定", ["decision"]], ["决定原因", ["decision_reason"]]]),
      detailSection("销售资料", sections.sales_assets, [["资料", ["title", "name", "asset_name"]], ["类型", ["asset_type", "type"]], ["使用场景", ["use_case"]], ["版本", ["version"]], ["状态", ["status"]], ["使用反馈", ["usage_feedback"]]]),
      detailSection("正式产出", sections.artifacts, [["路径", ["relative_path"]], ["类型", ["artifact_type"]], ["状态", ["status"]], ["任务编号", ["task_id"]], ["更新时间", ["updated_at"]]])
    ); }
    else panel.append(
      detailSection("证据引用", sections.evidence_refs, [["证据编号", ["evidence_ref_id"]], ["支持字段", ["field_name"]], ["内容类型", ["claim_kind"]], ["核验状态", ["verification_status"]], ["来源标题", ["source_title"]], ["发布机构", ["source_publisher"]], ["来源链接", ["source_url"], "source-url"], ["定位", ["locator_json"]], ["最后访问", ["source_accessed_date"]], ["说明", ["note"]]]),
      detailSection("关联任务", sections.task_links, [["任务编号", ["task_id"]], ["关联方式", ["relation_type"]], ["项目编号", ["project_id"]], ["更新时间", ["updated_at"]]])
    );
  }
  function customerQuickAction(serviceId, text, tab = "overview") {
    const button = document.createElement("button"); button.type = "button"; button.className = "secondary"; button.textContent = text;
    button.onclick = () => {
      if (!customerState.selectedId) return;
      const account = customerContextAccount(); const name = accountName(account);
      if (serviceId === "bid-create") {
        bidState.editingId = ""; $("create-bid").textContent = "创建并进入项目"; switchView("bids"); $("bid-create-panel").hidden = false; updateBidSelectors(); $("bid-account").value = customerState.selectedId;
        $("bid-name").value = `${name} 投标项目`; $("bid-buyer").value = name; $("bid-name").focus(); return;
      }
      if (serviceId === "sales-review") guidedDrafts[serviceId] = { scope: name, focus: "阶段、风险与下一步动作" };
      if (serviceId === "government-proposal") guidedDrafts[serviceId] = { region: accountField(account, "region", "地区"), direction: `${name} 合作机会` };
      if (serviceId === "industry-research") guidedDrafts[serviceId] = { topic: `${name} 所在行业与合作机会`, purpose: "支持客户沟通与机会判断", period: "近 12 个月，并补充关键历史背景" };
      if (serviceId === "office-document") guidedDrafts[serviceId] = { document: "内部资源协调单", audience: "销售、产品、技术与交付负责人", materials: `请结合当前客户“${name}”的客户全景与${tab === "resources" ? "资源需求" : "当前进展"}。` };
      openService(serviceId);
      if (serviceId === "presentation-studio") { $("ppt-topic").value = `为${name}制作客户推进与合作方案汇报`; $("ppt-audience").value = "客户决策人和销售管理层"; $("ppt-decision").value = "确认下一步行动、合作范围与所需资源"; }
      renderTaskForm();
    };
    return button;
  }
  function renderCustomerDetail() {
    const empty = $("customer-detail-empty"); const detailBox = $("customer-detail"); const account = customerState.detail?.account || customerContextAccount();
    const sectionKey = Object.entries(customerState.detail?.sections || {}).map(([name, rows]) => `${name}:${Array.isArray(rows) ? rows.length : 0}`).join("|");
    const detailKey = `${customerState.selectedId}|${accountField(account, "version")}|${accountField(account, "updated_at")}|${customerState.activeTab}|${customerState.detailLoading}|${customerState.detailError}|${customerState.timelineLoading}|${customerState.timelineError}|${customerState.timeline.length}|${customerState.timelineCursor}|${sectionKey}`;
    if (detailKey === customerDetailRenderedKey) return;
    customerDetailRenderedKey = detailKey;
    empty.hidden = Boolean(account); detailBox.hidden = !account;
    if (!account) { if (customerState.detailLoading) empty.querySelector("p").textContent = "正在读取客户全景…"; else if (customerState.detailError) empty.querySelector("p").textContent = `客户全景暂时无法读取：${customerState.detailError}`; return; }
    const sections = customerState.detail?.sections || {}; const primaryOpportunity = sections.opportunities?.[0]; const primaryRisk = (sections.risks || []).find((row) => !["closed", "resolved", "cancelled"].includes(String(row.status || "").toLowerCase())); const primaryAction = (sections.actions || []).find((row) => !["completed", "cancelled"].includes(String(row.status || "").toLowerCase()));
    $("customer-detail-name").textContent = accountName(account); $("customer-detail-meta").textContent = [accountField(primaryOpportunity, "name"), accountField(account, "owner", "owner_name", "负责人"), customerDisplayValue(accountField(primaryOpportunity, "stage") || accountField(account, "lifecycle_stage", "stage", "sales_stage", "阶段")), freshnessText(accountField(account, "updated_at", "last_updated_at"))].filter(Boolean).join(" · ") || "客户信息待补充";
    const status = $("customer-detail-status"); status.textContent = customerState.detailLoading ? "正在更新客户全景…" : customerState.detailError ? `部分信息暂时无法读取：${customerState.detailError}` : customerState.detail?.truncated_sections?.length ? `部分信息已截断：${customerState.detail.truncated_sections.join("、")}` : `主要风险：${accountField(primaryRisk, "risk_text") || "暂无已登记开放风险"} · 首要下一步：${accountField(primaryAction, "action_text") || "暂无已登记开放行动"}`;
    const actions = $("customer-quick-actions"); actions.replaceChildren(customerQuickAction("sales-review", "客户复盘"), customerQuickAction("bid-create", "创建投标项目"), customerQuickAction("government-proposal", "政府合作"), customerQuickAction("industry-research", "行业研究"), customerQuickAction("office-document", "资源协调/文件", "resources"), customerQuickAction("presentation-studio", "制作演示文稿"));
    renderCustomerSignals();
    renderCustomerRecommendations();
    detailTabs();
  }
  function renderCustomerSignals() {
    const container = $("customer-signals");
    if (!container) return;
    const signals = customerState.signals;
    const loading = customerState.signalsLoading;
    const error = customerState.signalsError;
    if (loading) {
      container.innerHTML = '<div class="signals-loading">正在读取信号…</div>';
      container.hidden = false;
      return;
    }
    if (error) {
      container.innerHTML = `<div class="signals-error">信号暂时无法读取：${error}</div>`;
      container.hidden = false;
      return;
    }
    if (!signals.length) {
      container.hidden = true;
      return;
    }
    container.hidden = false;
    const severityLabels = { high: "高优先级", medium: "中优先级", low: "低优先级" };
    container.replaceChildren(...signals.map((sig) => {
      const card = document.createElement("div");
      card.className = `signal-card signal-${sig.severity}`;
      const header = document.createElement("div");
      header.className = "signal-header";
      const badge = document.createElement("span");
      badge.className = `signal-badge signal-${sig.severity}`;
      badge.textContent = severityLabels[sig.severity] || sig.severity;
      const typeLabel = document.createElement("span");
      typeLabel.className = "signal-type";
      typeLabel.textContent = sig.signal_type;
      header.append(badge, typeLabel);
      const title = document.createElement("h3");
      title.textContent = sig.title;
      const description = document.createElement("p");
      description.textContent = sig.description;
      card.append(header, title, description);
      if (sig.suggested_actions && sig.suggested_actions.length > 0) {
        const actionsDiv = document.createElement("div");
        actionsDiv.className = "signal-actions";
        const ul = document.createElement("ul");
        sig.suggested_actions.forEach((action) => {
          const li = document.createElement("li");
          li.textContent = action;
          ul.append(li);
        });
        actionsDiv.append(ul);
        card.append(actionsDiv);
      }
      return card;
    }));
  }
  function renderCustomerRecommendations() {
    const container = $("customer-recommendations");
    if (!container) return;
    const recs = customerState.recommendations;
    const loading = customerState.recommendationsLoading;
    const error = customerState.recommendationsError;
    if (loading) {
      container.innerHTML = '<div class="recommendations-loading">正在读取建议…</div>';
      container.hidden = false;
      return;
    }
    if (error) {
      container.innerHTML = `<div class="recommendations-error">建议暂时无法读取：${error}</div>`;
      container.hidden = false;
      return;
    }
    if (!recs.length) {
      container.hidden = true;
      return;
    }
    container.hidden = false;
    const priorityLabels = { high: "高优先级", medium: "中优先级", low: "低优先级" };
    container.replaceChildren(...recs.slice(0, 3).map((rec) => {
      const card = document.createElement("div");
      card.className = `recommendation-card priority-${rec.priority}`;
      const header = document.createElement("div");
      header.className = "recommendation-header";
      const badge = document.createElement("span");
      badge.className = "recommendation-badge";
      badge.textContent = priorityLabels[rec.priority] || rec.priority;
      header.append(badge);
      const title = document.createElement("h4");
      title.textContent = rec.title;
      const description = document.createElement("p");
      description.textContent = rec.description;
      const actions = document.createElement("div");
      actions.className = "recommendation-actions";
      const acceptBtn = document.createElement("button");
      acceptBtn.type = "button";
      acceptBtn.className = "btn-accept";
      acceptBtn.textContent = "接受";
      acceptBtn.onclick = async () => {
        acceptBtn.disabled = true;
        await acceptRecommendation(rec.recommendation_id);
      };
      const editBtn = document.createElement("button");
      editBtn.type = "button";
      editBtn.className = "btn-edit";
      editBtn.textContent = "编辑";
      editBtn.onclick = async () => {
        const title = await confirmAction({
          title: "编辑建议",
          message: "请输入行动标题",
          inputValue: rec.title,
          inputLabel: "标题",
          inputMaxLength: 200,
          confirmText: "下一步",
        });
        if (!title) return;
        const description = await confirmAction({
          title: "编辑建议",
          message: "请输入行动描述",
          inputValue: rec.description,
          inputLabel: "描述",
          inputMultiline: true,
          inputMaxLength: 1000,
          confirmText: "确认接受",
        });
        if (!description) return;
        editBtn.disabled = true;
        await acceptRecommendation(rec.recommendation_id, { title, description });
      };
      const ignoreBtn = document.createElement("button");
      ignoreBtn.type = "button";
      ignoreBtn.className = "btn-ignore";
      ignoreBtn.textContent = "忽略";
      ignoreBtn.onclick = async () => {
        const reason = await confirmAction({
          title: "忽略建议",
          message: "请说明忽略原因（可选）",
          inputValue: "",
          inputLabel: "原因",
          inputMaxLength: 200,
          confirmText: "确认忽略",
          tone: "danger",
        });
        if (reason === false) return;
        ignoreBtn.disabled = true;
        await ignoreRecommendation(rec.recommendation_id, reason || null);
      };
      actions.append(acceptBtn, editBtn, ignoreBtn);
      card.append(header, title, description, actions);
      return card;
    }));
  }
  function renderCustomerOperations() {
    const list = $("customer-list"); if (!list) return;
    const beforeScroll = list.scrollTop; const rowKey = customerState.rows.map((row) => `${accountId(row)}:${accountField(row, "version")}:${accountField(row, "updated_at")}:${accountField(row, "open_actions")}:${accountField(row, "open_risks")}`).join("|"); const key = `${rowKey}|${customerState.selectedId}|${customerState.loading}|${customerState.error}`;
    if (key !== customerRenderedKey) { customerRenderedKey = key; list.classList.toggle("empty", !customerState.rows.length); if (customerState.error && !customerState.rows.length) { list.textContent = `客户列表暂时无法读取：${customerState.error}`; } else if (!customerState.rows.length && customerState.loading) list.textContent = "正在读取客户…"; else if (!customerState.rows.length) list.textContent = "没有符合筛选条件的客户。"; else list.replaceChildren(...customerState.rows.map(customerCard)); queueMicrotask(() => { list.scrollTop = Math.min(beforeScroll, Math.max(0, list.scrollHeight - list.clientHeight)); }); }
    $("customer-list-count").textContent = String(customerState.rows.length); $("customer-list-status").textContent = customerState.loading ? "正在更新客户…" : customerState.error ? `读取未完成：${customerState.error}` : customerState.loaded ? `已显示 ${customerState.rows.length} 个客户` : "尚未读取"; $("customer-load-more").hidden = !customerState.hasMore; $("customer-load-more").disabled = customerState.loading; renderCustomerDetail(); renderCustomerContext();
  }
  function renderAttention() {
    const box = $("home-attention"); if (!box) return;
    const attentionKey = `${customerState.attentionLoaded}|${customerState.attentionLoading}|${customerState.attentionError}|${customerState.attention.map((item) => `${item.focus_id || ""}:${item.event_at || ""}:${item.due_at || ""}`).join("|")}`;
    if (attentionKey === attentionRenderedKey) return;
    attentionRenderedKey = attentionKey; box.replaceChildren();
    if (customerState.attentionError) { box.className = "attention-list empty"; box.textContent = `今日关注暂时无法读取：${customerState.attentionError}`; return; }
    if (!customerState.attentionLoaded || customerState.attentionLoading) { box.className = "attention-list empty"; box.textContent = "正在读取今日关注…"; return; }
    if (!customerState.attention.length) { box.className = "attention-list empty"; box.textContent = "今天没有已识别的优先客户事项。"; return; }
    const targetLabels = { overview: "客户概览", timeline: "时间线", actions: "行动与承诺", signals: "信号与风险", resources: "资源与资料", evidence: "证据" };
    box.className = "attention-list"; customerState.attention.slice(0, 6).forEach((item) => { const id = accountId(item); const targetTab = customerTabFor(item.target_section); const card = document.createElement("button"); card.type = "button"; card.className = `attention-card ${String(item.severity || "") === "high" ? "high" : ""}`; const title = document.createElement("strong"); title.textContent = String(item.account_name || accountName(item)); const reason = document.createElement("p"); reason.textContent = accountField(item, "reason", "summary", "title") || "需要关注的客户事项"; const due = accountField(item, "due_at"); const meta = document.createElement("small"); meta.textContent = `${due ? `截止 ${formatCustomerTime(due)}` : `记录于 ${formatCustomerTime(accountField(item, "event_at", "updated_at"))}`} · 首要操作：查看${targetLabels[targetTab] || "客户信息"}`; card.append(title, reason, meta); card.onclick = async () => { switchView("sales"); await selectCustomer(id, { tab: targetTab, focus: true }); }; box.append(card); });
  }
  function renderGlobalRecommendations() {
    const container = $("home-recommendations");
    if (!container) return;
    const recs = customerState.recommendations;
    const loading = customerState.recommendationsLoading;
    const error = customerState.recommendationsError;
    if (loading) {
      container.innerHTML = '<div class="recommendations-loading">正在读取建议…</div>';
      container.hidden = false;
      return;
    }
    if (error) {
      container.innerHTML = `<div class="recommendations-error">建议暂时无法读取：${error}</div>`;
      container.hidden = false;
      return;
    }
    if (!recs.length) {
      container.hidden = true;
      return;
    }
    container.hidden = false;
    const priorityLabels = { high: "高优先级", medium: "中优先级", low: "低优先级" };
    container.replaceChildren(...recs.slice(0, 5).map((rec) => {
      const card = document.createElement("div");
      card.className = `recommendation-card priority-${rec.priority}`;
      const header = document.createElement("div");
      header.className = "recommendation-header";
      const badge = document.createElement("span");
      badge.className = `recommendation-badge priority-${rec.priority}`;
      badge.textContent = priorityLabels[rec.priority] || rec.priority;
      const accountLabel = document.createElement("span");
      accountLabel.style.fontSize = "11px";
      accountLabel.style.color = "#6d7788";
      accountLabel.textContent = rec.account_name;
      header.append(badge, accountLabel);
      const title = document.createElement("h4");
      title.textContent = rec.title;
      const description = document.createElement("p");
      description.textContent = rec.description;
      const actions = document.createElement("div");
      actions.className = "recommendation-actions";
      const acceptBtn = document.createElement("button");
      acceptBtn.type = "button";
      acceptBtn.className = "btn-accept";
      acceptBtn.textContent = "接受";
      acceptBtn.onclick = async () => {
        acceptBtn.disabled = true;
        await acceptRecommendation(rec.recommendation_id);
        await loadGlobalRecommendations({ force: true });
      };
      const editBtn = document.createElement("button");
      editBtn.type = "button";
      editBtn.className = "btn-edit";
      editBtn.textContent = "编辑";
      editBtn.onclick = async () => {
        const title = await confirmAction({
          title: "编辑建议",
          message: "请输入行动标题",
          inputValue: rec.title,
          inputLabel: "标题",
          inputMaxLength: 200,
          confirmText: "下一步",
        });
        if (!title) return;
        const description = await confirmAction({
          title: "编辑建议",
          message: "请输入行动描述",
          inputValue: rec.description,
          inputLabel: "描述",
          inputMultiline: true,
          inputMaxLength: 1000,
          confirmText: "确认接受",
        });
        if (!description) return;
        editBtn.disabled = true;
        await acceptRecommendation(rec.recommendation_id, { title, description });
        await loadGlobalRecommendations({ force: true });
      };
      const ignoreBtn = document.createElement("button");
      ignoreBtn.type = "button";
      ignoreBtn.className = "btn-ignore";
      ignoreBtn.textContent = "忽略";
      ignoreBtn.onclick = async () => {
        const reason = await confirmAction({
          title: "忽略建议",
          message: "请说明忽略原因（可选）",
          inputValue: "",
          inputLabel: "原因",
          inputMaxLength: 200,
          confirmText: "确认忽略",
          tone: "danger",
        });
        if (reason === false) return;
        ignoreBtn.disabled = true;
        await ignoreRecommendation(rec.recommendation_id, reason || null);
        await loadGlobalRecommendations({ force: true });
      };
      actions.append(acceptBtn, editBtn, ignoreBtn);
      card.append(header, title, description, actions);
      return card;
    }));
  }

  const bidStatusLabels = {
    draft: "待解读", interpreting: "解读中", decision_pending: "待参投决策", planning: "应答策划中",
    drafting: "标书编制中", checking: "复核中", delivery_pending: "待生成终稿", delivered: "已交付",
    closed: "已结束", no_bid: "不参投", cancelled: "已取消",
  };
  const bidStageLabels = {
    intake: "建档", interpretation: "解读", decision: "决策", planning: "策划",
    drafting: "编制", checking: "复核", delivery: "交付", retrospective: "复盘",
  };
  const bidStageOrder = Object.keys(bidStageLabels);
  const bidTableLabels = {
    bid_projects: "项目状态", bid_milestones: "里程碑", bid_requirements: "招标要求",
    bid_response_matrix: "应答矩阵", bid_facts: "事实基线", bid_sections: "标书章节",
    bid_checks: "合规检查", bid_risks: "项目风险", bid_decisions: "审批决策", bid_outcomes: "投标结果",
  };
  const bidNextActions = {
    draft: ["bid-interpretation", "解读招标文件", "从已上传原件提取强制要求、评分点、时间和递交规则。"],
    interpreting: ["bid-interpretation", "继续招标解读", "补齐文件定位和待核验事项，再提交结构化要求。"],
    decision_pending: ["bid-decision", "形成参投决策", "核对资格、资源、交付和商务风险，形成可审批建议。"],
    planning: ["bid-planning", "建立应答计划", "生成逐条应答矩阵、材料清单、负责人和统一事实基线。"],
    drafting: ["bid-drafting", "继续标书编制", "按已确认目录、事实和证据起草或完善章节。"],
    checking: ["bid-check", "执行合规复核", "先跑确定性规则，再检查一致性、重复和废标风险。"],
    delivery_pending: ["bid-delivery", "生成正式标书", "使用已批准章节生成可编辑文档，并完成逐页渲染检查。"],
    delivered: ["bid-retrospective", "记录结果与复盘", "登记投标结果、原因、可复用资产和改进动作。"],
    closed: ["bid-retrospective", "查看或补充复盘", "补充结果证据与下一次改进动作。"],
    no_bid: ["bid-retrospective", "复盘不参投原因", "沉淀放弃原因和后续机会判断标准。"],
    cancelled: ["bid-interpretation", "重新启动解读", "确认恢复后重新读取当前文件与项目状态。"],
  };
  const bidServiceActions = [
    ["bid-interpretation", "文件解读"], ["bid-decision", "参投决策"], ["bid-planning", "应答策划"],
    ["bid-drafting", "章节编制"], ["bid-check", "合规复核"], ["bid-delivery", "生成终稿"],
    ["bid-retrospective", "结果复盘"],
  ];

  function bidProject() { return bidState.detail?.project?.bid_id === bidState.selectedId ? bidState.detail.project : bidState.rows.find((row) => row.bid_id === bidState.selectedId) || null; }
  function bidDeadline(value) {
    if (!value) return "截止时间待登记";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN", { hour12: false });
  }
  function bidDisplay(value) {
    if (value === null || value === undefined || String(value).trim() === "") return "待补充";
    const text = String(value).trim();
    const mapped = { ...bidStatusLabels, ...bidStageLabels, pending: "待处理", verified: "已核验", rejected: "已否定", superseded: "已替代", open: "待处理", resolved: "已解决", approved: "已批准", ready: "已就绪", compliant: "已满足", unaddressed: "未响应", planned: "已计划", drafted: "已起草", deviation: "存在偏差", critical: "严重", high: "高", medium: "中", low: "低", info: "提示", go: "参投", no_go: "不参投", conditional: "有条件参投", qualification: "资格", technical: "技术", commercial: "商务", scoring: "评分", format: "格式", submission: "递交", contract: "合同", other: "其他" };
    return mapped[text] || text;
  }
  function bidReadableJson(value) {
    const text = String(value || "").trim();
    if (!text) return "";
    try {
      const parsed = JSON.parse(text);
      if (Array.isArray(parsed)) return parsed.length ? parsed.map((item) => typeof item === "string" ? item : Object.entries(item || {}).map(([key, child]) => `${key}：${child}`).join("，")).join("；") : "暂无证据";
      if (parsed && typeof parsed === "object") return Object.entries(parsed).map(([key, child]) => `${key}：${Array.isArray(child) ? child.join("、") : child}`).join("；");
    } catch { /* Historic text is shown as-is. */ }
    return text;
  }
  function bidFilterStatuses() {
    const value = bidState.statuses;
    if (value === "active") return ["draft", "interpreting", "decision_pending", "planning", "drafting", "checking", "delivery_pending"];
    return value ? [value] : [];
  }
  function updateBidSelectors() {
    const projects = (model?.projects || []).filter((item) => item.status === "active").map((item) => ({ value: item.project_id, label: item.name }));
    setSelectOptions($("bid-workspace-project"), projects, selectedProject);
    const options = [{ value: "", label: "暂不关联客户" }, ...bidState.accounts.map((account) => ({ value: accountId(account), label: accountName(account) }))];
    const selected = $("bid-account").value || customerState.selectedId;
    setSelectOptions($("bid-account"), options, selected);
  }
  async function loadBids({ force = false } = {}) {
    if (bidState.loading && !force) return;
    bidState.listController?.abort();
    const controller = new AbortController(); bidState.listController = controller;
    const generation = ++bidState.generation;
    bidState.loading = true; bidState.error = ""; renderBidding();
    const params = new URLSearchParams({ limit: "100" });
    if (bidState.query) params.set("query", bidState.query);
    const statuses = bidFilterStatuses(); if (statuses.length) params.set("statuses", statuses.join(","));
    try {
      const requests = [api(`/api/bids?${params}`, { signal: controller.signal }), api("/api/bids/dashboard", { signal: controller.signal })];
      if (!bidState.accounts.length) requests.push(api("/api/accounts?limit=100", { signal: controller.signal }));
      const [list, dashboard, accounts] = await Promise.all(requests);
      if (generation !== bidState.generation) return;
      bidState.rows = Array.isArray(list.rows) ? list.rows : [];
      bidState.dashboard = dashboard;
      if (accounts) bidState.accounts = Array.isArray(accounts.rows) ? accounts.rows : [];
      bidState.loaded = true;
      updateBidSelectors();
      if (!bidState.selectedId && bidState.rows.length) await selectBid(bidState.rows[0].bid_id, { focus: false });
      else if (bidState.selectedId && !bidState.rows.some((row) => row.bid_id === bidState.selectedId)) {
        bidState.selectedId = ""; bidState.detail = null; bidState.timeline = [];
      }
    } catch (error) {
      if (error.name !== "AbortError" && generation === bidState.generation) bidState.error = error.message || "投标项目暂时无法读取";
    } finally {
      if (generation === bidState.generation) { bidState.loading = false; renderBidding(); }
    }
  }
  async function selectBid(bidId, { focus = true, tab = null } = {}) {
    if (!bidId) return;
    const changed = bidState.selectedId !== String(bidId); bidState.selectedId = String(bidId); if (tab) bidState.activeTab = tab;
    if (changed) { bidState.detail = null; bidState.timeline = []; bidState.detailRenderedKey = ""; }
    bidState.detailLoading = true; bidState.detailError = ""; renderBidding();
    bidState.detailController?.abort(); const controller = new AbortController(); bidState.detailController = controller;
    const generation = ++bidState.detailGeneration;
    try {
      const [detail, timeline] = await Promise.all([
        api(`/api/bids/${encodeURIComponent(bidId)}/360`, { signal: controller.signal }),
        api(`/api/bids/${encodeURIComponent(bidId)}/timeline?limit=100`, { signal: controller.signal }),
      ]);
      if (generation !== bidState.detailGeneration || bidState.selectedId !== bidId) return;
      bidState.detail = detail; bidState.timeline = Array.isArray(timeline.rows) ? timeline.rows : [];
      const workspaceId = detail.project?.workspace_project_id;
      if (workspaceId && projectById(workspaceId)?.status === "active") selectedProject = workspaceId;
    } catch (error) {
      if (error.name !== "AbortError" && generation === bidState.detailGeneration) bidState.detailError = error.message || "投标项目详情暂时无法读取";
    } finally {
      if (generation === bidState.detailGeneration) {
        bidState.detailLoading = false; renderBidding(); renderProjectSelectors();
        if (focus) queueMicrotask(() => $("bid-detail")?.focus?.({ preventScroll: true }));
      }
    }
  }
  function bidProjectCard(project) {
    const card = document.createElement("button"); card.type = "button"; card.className = `bid-project-card ${project.bid_id === bidState.selectedId ? "selected" : ""}`;
    const header = document.createElement("header"); const title = document.createElement("strong"); title.textContent = project.name || "未命名投标项目"; const status = document.createElement("span"); status.className = `status ${["delivered", "closed"].includes(project.status) ? "completed" : ["no_bid", "cancelled"].includes(project.status) ? "cancelled" : "running"}`; status.textContent = bidStatusLabels[project.status] || project.status; header.append(title, status);
    const meta = document.createElement("small"); meta.textContent = [project.buyer, project.tender_number, bidDeadline(project.deadline_at)].filter(Boolean).join(" · ");
    const alerts = document.createElement("div"); alerts.className = "bid-card-alerts";
    [[project.mandatory_gap_count, "强制项缺口", "gap"], [project.material_gap_count, "材料缺口", "gap"], [project.high_risk_check_count, "高风险", "risk"]].forEach(([count, text, tone]) => { if (!Number(count)) return; const badge = document.createElement("span"); badge.className = `bid-mini-badge ${tone}`; badge.textContent = `${count} ${text}`; alerts.append(badge); });
    card.append(header, meta); if (alerts.children.length) card.append(alerts); card.onclick = () => selectBid(project.bid_id); return card;
  }
  function bidDataCard(row, titleFields, fieldMap, tone = "") {
    const card = document.createElement("article"); card.className = `bid-data-card ${tone}`;
    const header = document.createElement("header"); const title = document.createElement("strong"); title.textContent = titleFields.map((field) => row?.[field]).find((value) => value !== undefined && value !== null && String(value).trim()) || "未命名记录";
    const badge = document.createElement("span"); badge.className = "bid-mini-badge"; badge.textContent = bidDisplay(row?.severity || row?.status || row?.verification_status || ""); header.append(title, badge); card.append(header);
    const description = row?.finding || row?.requirement_text || row?.content_markdown || row?.risk_text || row?.response_strategy || row?.rationale || row?.lessons || row?.objective || "";
    if (description) { const body = document.createElement("p"); body.textContent = String(description).slice(0, 3000); card.append(body); }
    const list = document.createElement("dl");
    fieldMap.forEach(([labelText, field, kind]) => { const value = row?.[field]; if (value === null || value === undefined || String(value).trim() === "") return; const dt = document.createElement("dt"); dt.textContent = labelText; const dd = document.createElement("dd"); dd.textContent = kind === "json" ? bidReadableJson(value) : kind === "date" ? bidDeadline(value) : bidDisplay(value); list.append(dt, dd); });
    if (list.children.length) card.append(list); return card;
  }
  function bidEmpty(text) { const box = document.createElement("div"); box.className = "bid-empty"; box.textContent = text; return box; }
  function bidSectionList(rows, factory, emptyText) { const box = document.createElement("div"); box.className = "bid-section-list"; if (!rows?.length) box.append(bidEmpty(emptyText)); else rows.forEach((row) => box.append(factory(row))); return box; }
  function renderBidTab() {
    const panel = $("bid-tab-panel"); panel.replaceChildren(); const project = bidProject(); const sections = bidState.detail?.sections || {};
    if (!project) { panel.append(bidEmpty("选择项目后查看详情。")); return; }
    if (bidState.activeTab === "overview") {
      const metrics = document.createElement("div"); metrics.className = "bid-summary-grid";
      [[sections.requirements?.length || 0, "招标要求"], [sections.response_matrix?.length || 0, "应答条目"], [sections.sections?.length || 0, "标书章节"], [sections.documents?.length || 0, "已登记文件"], [sections.checks?.filter((item) => item.status === "open").length || 0, "开放检查"], [sections.risks?.filter((item) => ["open", "mitigating"].includes(item.status)).length || 0, "开放风险"]].forEach(([value, text]) => { const item = document.createElement("article"); const small = document.createElement("small"); small.textContent = text; const strong = document.createElement("strong"); strong.textContent = String(value); item.append(small, strong); metrics.append(item); });
      panel.append(metrics, bidSectionList([project], (row) => bidDataCard(row, ["name"], [["采购人", "buyer"], ["招标编号", "tender_number"], ["标段", "lot_name"], ["负责人", "owner"], ["截止时间", "deadline_at", "date"], ["参投状态", "go_no_go"], ["项目说明", "summary"]]), "暂无项目信息"));
    } else if (bidState.activeTab === "requirements") panel.append(bidSectionList(sections.requirements, (row) => bidDataCard(row, ["title", "requirement_id"], [["类别", "category"], ["是否强制", "mandatory"], ["分值", "score_points"], ["原文定位", "evidence_locator_json", "json"], ["核验状态", "verification_status"], ["响应状态", "response_status"], ["负责人", "owner"], ["截止时间", "due_at", "date"]]), "尚未提取招标要求。请先上传招标原件并执行文件解读。"));
    else if (bidState.activeTab === "matrix") panel.append(bidSectionList(sections.response_matrix, (row) => bidDataCard(row, ["material_need", "requirement_id", "response_id"], [["关联要求", "requirement_id"], ["章节", "section_id"], ["材料需求", "material_need"], ["材料状态", "material_status"], ["负责人", "owner"], ["截止时间", "due_at", "date"], ["偏差", "deviation"], ["状态", "status"]]), "尚未建立逐条应答矩阵。"));
    else if (bidState.activeTab === "sections") panel.append(bidSectionList(sections.sections, (row) => bidDataCard(row, ["title", "section_id"], [["目录顺序", "order_index"], ["层级", "level"], ["负责人", "owner"], ["状态", "status"], ["引用证据", "evidence_json", "json"]]), "尚未建立标书目录和章节。"));
    else if (bidState.activeTab === "checks") panel.append(bidSectionList(sections.checks, (row) => bidDataCard(row, ["finding", "rule_id"], [["规则", "rule_id"], ["等级", "severity"], ["状态", "status"], ["建议", "recommendation"], ["证据", "evidence_json", "json"]], `bid-check-${row.severity || "info"}`), "尚未执行合规检查。"));
    else if (bidState.activeTab === "files") panel.append(bidSectionList(sections.documents, (row) => {
      const card = bidDataCard(row, ["display_name", "document_id"], [["文件类型", "role"], ["登记状态", "source_status"], ["页数", "page_count"], ["文件指纹", "sha256"]]);
      const open = document.createElement("button"); open.type = "button"; open.className = "secondary"; open.textContent = "打开文件"; open.onclick = async () => { try { const response = await api("/api/bid-files/open", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ bid_id: project.bid_id, document_id: row.document_id }) }); note(response.message); } catch (error) { note(error.message, true); } }; card.append(open); return card;
    }, "当前项目还没有文件。请从下方上传招标文件或相关材料。"));
    else if (bidState.activeTab === "artifacts") panel.append(bidSectionList(sections.artifacts, (row) => {
      const card = bidDataCard(row, ["relative_path", "artifact_id"], [["产物类型", "artifact_type"], ["状态", "status"], ["文件指纹", "sha256"], ["生成任务", "task_id"]]);
      const open = document.createElement("button"); open.type = "button"; open.className = "secondary"; open.textContent = "打开产物"; open.onclick = async () => { try { const response = await api("/api/bid-artifacts/open", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ bid_id: project.bid_id, artifact_id: row.artifact_id }) }); note(response.message); } catch (error) { note(error.message, true); } }; card.append(open); return card;
    }, "尚未生成投标产物。"));
    else {
      const timeline = document.createElement("div");
      if (!bidState.timeline.length) timeline.append(bidEmpty("还没有投标项目事件。")); else bidState.timeline.forEach((row) => { const event = document.createElement("article"); event.className = "bid-event"; const title = document.createElement("strong"); title.textContent = row.title || "项目事件"; const meta = document.createElement("small"); meta.textContent = `${bidDeadline(row.created_at)} · ${row.actor || "系统"}`; const detail = document.createElement("p"); detail.textContent = bidReadableJson(row.detail_json) || "无补充说明"; event.append(title, meta, detail); timeline.append(event); }); panel.append(timeline);
    }
  }
  function renderBidDetail() {
    const project = bidProject(); const empty = $("bid-detail-empty"); const detail = $("bid-detail");
    const sectionsKey = Object.entries(bidState.detail?.sections || {}).map(([key, rows]) => `${key}:${Array.isArray(rows) ? rows.length : 0}:${rows?.[0]?.version || ""}`).join("|");
    const key = `${bidState.selectedId}|${project?.version || ""}|${project?.updated_at || ""}|${bidState.activeTab}|${bidState.detailLoading}|${bidState.detailError}|${sectionsKey}|${bidState.timeline.length}`;
    if (key === bidState.detailRenderedKey) return; bidState.detailRenderedKey = key;
    empty.hidden = Boolean(project); detail.hidden = !project;
    if (!project) { empty.querySelector("p").textContent = bidState.detailLoading ? "正在读取投标项目…" : bidState.detailError || "从左侧选择项目后查看完整流程。"; return; }
    $("bid-detail-name").textContent = project.name; $("bid-detail-meta").textContent = [project.buyer, project.tender_number, bidStatusLabels[project.status], bidDeadline(project.deadline_at)].filter(Boolean).join(" · ");
    const actions = $("bid-detail-actions"); const edit = document.createElement("button"); edit.type = "button"; edit.className = "secondary"; edit.textContent = "编辑项目信息"; edit.onclick = () => { bidState.editingId = project.bid_id; $("bid-name").value = project.name || ""; $("bid-workspace-project").value = project.workspace_project_id || selectedProject; $("bid-account").value = project.account_id || ""; $("bid-buyer").value = project.buyer || ""; $("bid-number").value = project.tender_number || ""; $("bid-deadline").value = project.deadline_at ? new Date(project.deadline_at).toLocaleString("sv-SE").slice(0, 16) : ""; $("bid-summary").value = project.summary || ""; $("create-bid").textContent = "保存项目信息"; $("bid-create-panel").hidden = false; $("bid-name").focus(); }; actions.replaceChildren(edit, ...bidServiceActions.map(([serviceId, text]) => { const button = document.createElement("button"); button.type = "button"; button.className = bidNextActions[project.status]?.[0] === serviceId ? "primary" : "secondary"; button.textContent = text; button.onclick = () => createBidStageTask(serviceId); return button; }));
    const currentStageIndex = Math.max(0, bidStageOrder.indexOf(project.current_stage)); $("bid-stage-steps").replaceChildren(...bidStageOrder.map((stage, index) => { const item = document.createElement("span"); item.className = `bid-stage-step ${index < currentStageIndex ? "done" : index === currentStageIndex ? "current" : ""}`; item.textContent = bidStageLabels[stage]; return item; }));
    const next = bidNextActions[project.status] || bidNextActions.draft; $("bid-next-title").textContent = next[1]; $("bid-next-help").textContent = next[2]; $("run-bid-next").textContent = next[1]; $("run-bid-next").onclick = () => createBidStageTask(next[0]);
    const tabs = [["overview", "项目总览"], ["requirements", "招标要求"], ["matrix", "应答矩阵"], ["sections", "标书章节"], ["checks", "合规检查"], ["files", "项目文件"], ["artifacts", "正式产物"], ["timeline", "项目时间线"]];
    const tabBox = $("bid-tabs"); tabBox.replaceChildren(...tabs.map(([id, text]) => { const button = document.createElement("button"); button.type = "button"; button.id = `bid-tab-${id}`; button.textContent = text; button.className = bidState.activeTab === id ? "active" : ""; button.setAttribute("role", "tab"); button.setAttribute("aria-selected", String(bidState.activeTab === id)); button.onclick = () => { bidState.activeTab = id; bidState.detailRenderedKey = ""; renderBidDetail(); }; return button; }));
    renderBidTab();
  }
  function renderBidding() {
    if (!$("bid-list")) return;
    const dashboard = bidState.dashboard || model?.bidding || {};
    $("bid-active-count").textContent = String(dashboard.active_count || 0); $("bid-decision-count").textContent = String(dashboard.decision_pending_count || 0); $("bid-risk-count").textContent = String(dashboard.high_risk_project_count || 0); $("bid-total-count").textContent = String(dashboard.project_count || 0);
    updateBidSelectors();
    const list = $("bid-list"); const scrollTop = list.scrollTop; const key = `${bidState.rows.map((row) => `${row.bid_id}:${row.version}:${row.high_risk_check_count}:${row.mandatory_gap_count}:${row.material_gap_count}`).join("|")}|${bidState.selectedId}|${bidState.loading}|${bidState.error}`;
    if (key !== bidState.renderedKey) { bidState.renderedKey = key; list.classList.toggle("empty", !bidState.rows.length); if (bidState.error && !bidState.rows.length) list.textContent = `投标项目暂时无法读取：${bidState.error}`; else if (bidState.loading && !bidState.rows.length) list.textContent = "正在读取投标项目…"; else if (!bidState.rows.length) list.textContent = "没有符合筛选条件的投标项目。"; else list.replaceChildren(...bidState.rows.map(bidProjectCard)); queueMicrotask(() => { list.scrollTop = Math.min(scrollTop, Math.max(0, list.scrollHeight - list.clientHeight)); }); }
    $("bid-list-count").textContent = String(bidState.rows.length); $("bid-list-status").textContent = bidState.loading ? "正在更新项目…" : bidState.error ? `读取未完成：${bidState.error}` : `已显示 ${bidState.rows.length} 个项目`; renderBidDetail();
  }
  async function createBidStageTask(serviceId, extra = "") {
    const project = bidProject(); if (!project) { note("请先选择一个投标项目。", true); return; }
    const service = serviceById(serviceId); if (!service) { note("当前版本未启用该投标能力。", true); return; }
    const operation = bidServiceActions.find(([id]) => id === serviceId)?.[1] || service.display_name;
    if (serviceId === "bid-check") {
      try {
        const checks = await api(`/api/bids/${encodeURIComponent(project.bid_id)}/checks/run`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
        extra = `${extra ? `${extra}\n` : ""}本次确定性检查已执行：开放问题 ${checks.open_count} 项，其中严重 ${checks.critical_count} 项、高风险 ${checks.high_count} 项；请读取最新检查记录后继续深度复核。`;
        await selectBid(project.bid_id, { focus: false, tab: "checks" });
      } catch (error) { note(`确定性检查未完成：${error.message}`, true); return; }
    }
    const request = [
      "【全流程智能招投标阶段任务】", `投标项目编号：${project.bid_id}`, `项目名称：${project.name}`,
      `当前状态：${bidStatusLabels[project.status] || project.status}`, `当前阶段：${bidStageLabels[project.current_stage] || project.current_stage}`,
      `本次操作：${operation}`, "请先使用投标项目读取工具取得完整且最新的项目数据；只基于已登记原件、已核验事实和可定位证据工作。",
      "需要更新招投标数据库或生成正式文档时，先用自然语言卡片展示精确变更并等待人工审批。不得自动登录、签章、报价、上传、提交或对外发送。",
      extra,
    ].filter(Boolean).join("\n");
    try {
      const response = await api("/api/task-requests", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ profile_id: selectedProfile, service_id: serviceId, project_id: project.workspace_project_id || selectedProject, request, ...taskRuntimeSelection() }) });
      note(`${operation}任务已登记（${response.request_id}）。`); await load(); switchView("tasks");
    } catch (error) { note(error.message, true); }
  }

  function renderOutputs() {
    const box = $("outputs");
    box.replaceChildren();
    if (!model.outputs.length) { box.textContent = "暂无可显示的产物。"; return; }
    model.outputs.forEach((item) => box.append(summaryRow("output-row", item.name, `${item.modified_at} · ${item.path}`)));
  }

  function setSelectOptions(select, items, value) {
    const previous = value || select.value;
    select.replaceChildren(...items.map((item) => {
      const option = document.createElement("option");
      option.value = item.value; option.textContent = item.label; option.disabled = Boolean(item.disabled);
      return option;
    }));
    if ([...select.options].some((option) => option.value === previous && !option.disabled)) select.value = previous;
  }

  function renderProjectSelectors() {
    const active = (model.projects || []).filter((project) => project.status === "active");
    if (!active.some((project) => project.project_id === selectedProject)) selectedProject = active[0]?.project_id || "project-default";
    const options = active.map((project) => ({ value: project.project_id, label: project.name }));
    setSelectOptions($("home-project"), options, selectedProject);
    setSelectOptions($("task-project"), options, selectedProject);
    setSelectOptions($("schedule-project"), options, selectedProject);
  }

  function fileSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  function fileRow(item) {
    const row = document.createElement("div"); row.className = "file-row";
    const copy = document.createElement("div");
    const title = document.createElement("strong"); title.textContent = item.name;
    const meta = document.createElement("small"); meta.textContent = `${item.path} · ${item.modified_at}`;
    copy.append(title, meta);
    const size = document.createElement("span"); size.className = "file-size"; size.textContent = fileSize(item.size || 0);
    const actions = document.createElement("div"); actions.className = "file-actions";
    const open = document.createElement("button"); open.className = "secondary"; open.textContent = "打开";
    open.onclick = () => openManagedFile(item);
    const rename = document.createElement("button"); rename.className = "secondary"; rename.textContent = "重命名";
    rename.onclick = () => renameManagedFile(item);
    const use = document.createElement("button"); use.className = "secondary"; use.textContent = item.name.toLowerCase().endsWith(".pdf") ? "电子文档入库" : "用于任务";
    use.onclick = () => {
      selectedProject = item.project_id;
      if (item.name.toLowerCase().endsWith(".pdf")) {
        guidedDrafts["pdf-import"] = { path: item.path, goal: "提取可引用证据并写入资料库", focus: "" };
        openService("pdf-import");
      } else {
        guidedDrafts["office-document"] ||= {};
        guidedDrafts["office-document"].materials = `请结合项目资料：${item.path}`;
        openService("office-document");
      }
    };
    const remove = document.createElement("button"); remove.className = "danger-outline"; remove.textContent = "删除";
    remove.onclick = () => trashManagedFile(item);
    actions.append(open, rename, use, remove);
    row.append(copy, size, actions);
    return row;
  }

  function managedFilePayload(item) {
    return {
      path: item.path, version: item.version,
      ...(item.batch_id ? { batch_id: item.batch_id, material_id: item.material_id } : {}),
    };
  }

  async function openManagedFile(item) {
    try {
      const response = await api("/api/files/open", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(managedFilePayload(item)) });
      note(response.message);
    } catch (error) { note(error.message, true); }
  }

  async function renameManagedFile(item) {
    const name = await confirmAction({
      title: "重命名文件", message: "文件类型不能改变；同名文件不会被覆盖。",
      confirmText: "保存名称", inputLabel: "新文件名", inputValue: item.name,
    });
    if (!name || name === item.name) return;
    try {
      const response = await api("/api/files/rename", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...managedFilePayload(item), name }) });
      note(response.message); await load();
    } catch (error) { note(error.message, true); }
  }

  async function trashManagedFile(item) {
    if (!await confirmAction({ title: "将文件移入回收站？", message: `${item.name}\n删除后可从工具栏的文件回收站恢复。`, confirmText: "移入回收站", tone: "danger" })) return;
    try {
      let response = await api("/api/files/trash", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(managedFilePayload(item)) });
      if (response.requires_confirmation) {
        if (!await confirmAction({ title: "该文件仍被引用", message: response.message, detail: response.references.join("\n"), confirmText: "仍然移入回收站", tone: "danger" })) return;
        response = await api("/api/files/trash", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...managedFilePayload(item), acknowledge_references: true }) });
      }
      note(response.message); await load();
    } catch (error) { note(error.message, true); }
  }

  function renderToolPanels() {
    document.querySelectorAll("[data-tool]").forEach((button) => {
      button.classList.toggle("active", button.dataset.tool === activeTool);
    });
    document.querySelectorAll("[data-tool-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.toolPanel !== activeTool;
    });
  }

  function wechatFilters() {
    return {
      date_from: $("wechat-date-from").value,
      date_to: $("wechat-date-to").value,
      chat_type: $("wechat-chat-type").value,
      query: $("wechat-query").value.trim(),
    };
  }

  function renderWechatSummary() {
    const summary = model?.wechat || { configured: false, status: "empty", conversation_count: 0, message_count: 0 };
    const box = $("wechat-summary");
    box.replaceChildren();
    const strong = document.createElement("strong");
    const detail = document.createElement("span");
    if (summary.status === "error") {
      strong.textContent = "会话索引暂不可用";
      detail.textContent = summary.error || "请重新打开应用后再试";
    } else if (!summary.configured || !summary.message_count) {
      strong.textContent = summary.status === "raw_expired" ? "原文已按期限清理" : "尚未导入会话";
      detail.textContent = "可解密数据库副本，或导入 JSON、JSONL、CSV；原文只在本机浏览";
    } else {
      strong.textContent = `本机已有 ${summary.conversation_count} 个会话、${summary.message_count} 条消息`;
      const latest = summary.batches?.[0];
      detail.textContent = latest?.expires_at
        ? `本批原文将在 ${new Date(latest.expires_at).toLocaleString("zh-CN", { hour12: false })} 后清理`
        : "原文按导入日起 7 天自动清理";
    }
    box.append(strong, detail);
    $("review-selected-wechat").disabled = wechatState.creating || wechatState.selected.size === 0 || !summary.message_count;
    $("review-today-wechat").disabled = wechatState.creating || !summary.message_count;
    $("wechat-export-selected").disabled = wxdecipherState.exporting || wechatState.selected.size === 0 || !summary.message_count;
    renderWxDecipherControls();
  }

  function wxDecipherStatus(message, error = false) {
    $("wxdecipher-status").textContent = message;
    $("wxdecipher-status").classList.toggle("error", error);
  }

  function renderWxDecipherControls() {
    const busy = wxdecipherState.busy || wxdecipherState.captureLoading;
    const unavailable = model?.wechat?.http_available === false;
    const blocked = busy || unavailable;
    $("choose-wechat-export").disabled = unavailable;
    $("wxdecipher-media-choose").disabled = unavailable || wxmediaState.busy;
    $("wxdecipher-run").disabled = blocked || !wxdecipherState.files.length || !$("wechat-ownership").checked || !$("wxdecipher-snapshot").checked;
    $("wxdecipher-run").textContent = busy ? "正在本机处理…" : "校验、解密并导入";
    $("wxdecipher-add-files").disabled = blocked;
    $("wxdecipher-clear-files").disabled = busy || !wxdecipherState.files.length;
    ["wxdecipher-key", "wxdecipher-self-id", "wxdecipher-cipher-mode", "wxdecipher-snapshot", "wxdecipher-wal-confirm", "wxdecipher-capture-confirm"].forEach((id) => { $(id).disabled = blocked; });
    const captureAllowed = $("wxdecipher-capture-confirm").checked && $("wechat-ownership").checked && model?.wechat?.decipher?.key_capture !== false;
    $("wxdecipher-process-refresh").disabled = blocked || !captureAllowed;
    $("wxdecipher-process").disabled = blocked || !captureAllowed;
    $("wxdecipher-files").querySelectorAll("input,button").forEach((item) => { item.disabled = blocked; });
    $("wxdecipher-console")?.setAttribute("aria-busy", String(busy));
    const capability = model?.wechat?.decipher;
    $("wxdecipher-dependencies").textContent = unavailable ? "当前平台的本机用户隔离尚未就绪，微信工具暂不开放。" : capability?.decrypt_available === false
      ? "加密库需要安装 requirements-wxdecipher.txt；明文 SQLite 可直接导入。" : "逐页校验 · 任一失败即停止整批导入";
  }

  function renderWxDecipherFiles() {
    const total = wxdecipherState.files.reduce((sum, entry) => sum + entry.file.size, 0);
    $("wxdecipher-file-count").textContent = wxdecipherState.files.length
      ? `已选 ${wxdecipherState.files.length} 个 · ${(total / 1024 / 1024).toFixed(1)} 兆字节` : "尚未选择数据库";
    $("wxdecipher-files").replaceChildren(...wxdecipherState.files.map((entry) => {
      const row = document.createElement("li");
      const name = document.createElement("span"); name.textContent = `${entry.file.name} · ${(entry.file.size / 1024 / 1024).toFixed(1)} MB`;
      const isWal = /-wal$/iu.test(entry.file.name);
      const key = document.createElement(isWal ? "span" : "input"); key.type = "password"; key.maxLength = 64; key.autocomplete = "off";
      if (isWal) key.textContent = "WAL · 使用配对主库的密钥";
      key.placeholder = "此文件专用密钥（可选）"; key.value = entry.key || "";
      key.setAttribute("aria-label", `${entry.file.name} 专用密钥`);
      key.oninput = () => { entry.key = key.value; };
      const remove = document.createElement("button"); remove.type = "button"; remove.className = "secondary";
      remove.textContent = "移除"; remove.setAttribute("aria-label", `移除 ${entry.file.name}`);
      remove.onclick = () => { entry.key = ""; wxdecipherState.files = wxdecipherState.files.filter((item) => item !== entry); renderWxDecipherFiles(); };
      row.append(name, key, remove); return row;
    }));
    renderWxDecipherControls();
  }

  async function runWxDecipher() {
    if (wxdecipherState.busy) return;
    if (!$("wechat-ownership").checked || !$("wxdecipher-snapshot").checked || !wxdecipherState.files.length) {
      wxDecipherStatus("请先选择数据库，并确认账号权限和静态副本。", true); return;
    }
    const key = $("wxdecipher-key").value.trim();
    const fileKeys = Object.fromEntries(wxdecipherState.files.filter((entry) => entry.key?.trim()).map((entry) => [entry.file.name, entry.key.trim()]));
    if ([key, ...Object.values(fileKeys)].some((value) => value && !/^[0-9a-f]{64}$/iu.test(value))) {
      wxDecipherStatus("密钥必须为 64 位十六进制；没有加密的 SQLite 副本可留空。", true); return;
    }
    const cipherMode = $("wxdecipher-cipher-mode").value;
    const accountLabel = $("wechat-account").value.trim() || "本机微信";
      const selfUsername = $("wxdecipher-self-id").value.trim();
      if (!/^[\w.@-]{1,128}$/u.test(selfUsername)) {
        wxDecipherStatus("请填写数据库使用的本人微信 ID（不是昵称），用于稳定区分账号，避免同名标签混合会话。", true); return;
      }
    const files = wxdecipherState.files.map((entry) => entry.file);
    const walFiles = files.filter((file) => /-wal$/iu.test(file.name));
    const dbNames = new Set(files.filter((file) => !/-wal$/iu.test(file.name)).map((file) => file.name.toLowerCase()));
    if (!dbNames.size || walFiles.some((file) => !dbNames.has(file.name.slice(0, -4).toLowerCase()))) {
      wxDecipherStatus("每个 WAL 必须有同批次同名主库，例如 message_0.db 与 message_0.db-wal。", true); return;
    }
    const walConfirmed = $("wxdecipher-wal-confirm").checked;
    if (walFiles.length && !walConfirmed) { wxDecipherStatus("请明确勾选允许对所选同快照 DB/WAL 进行离线重放。", true); return; }
    let autoCapture;
    if ($("wxdecipher-capture-confirm").checked) {
      try { autoCapture = { ...JSON.parse($("wxdecipher-process").value), confirmed: true }; }
      catch { wxDecipherStatus("请先列出并明确选择本次取钥的微信进程。", true); return; }
      if (!autoCapture.process_id || !autoCapture.created_at) { wxDecipherStatus("微信进程选择无效，请重新选择。", true); return; }
    }
    // No localStorage/sessionStorage, URL parameters or persisted job metadata.
    $("wxdecipher-key").value = "";
    wxdecipherState.files.forEach((entry) => { entry.key = ""; });
    wxdecipherState.busy = true; renderWxDecipherFiles();
    let sessionId = "";
    try {
      wxDecipherStatus("正在创建本机数据库导入会话…");
      const session = await api("/api/wechat/decipher/sessions", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ownership_confirmed: true, snapshot_confirmed: true, account_label: accountLabel, self_username: selfUsername }) });
      sessionId = session.session_id;
      for (let index = 0; index < files.length; index++) {
        wxDecipherStatus(`正在上传本机副本 ${index + 1}/${files.length}：${files[index].name}。暂未导入消息。`);
        await api("/api/wechat/decipher/upload", { method: "POST", headers: {
          "Content-Type": "application/octet-stream", "X-WXDecipher-Session": sessionId,
          "X-File-Name": encodeURIComponent(files[index].name),
        }, body: files[index] });
      }
      if (autoCapture) {
        const consent = await api("/api/wechat/decipher/capture-consent", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: sessionId, ...autoCapture }) });
        autoCapture.consent_token = consent.consent_token;
      }
      wxDecipherStatus("正在校验密钥、逐页解密和检查数据库，再转换为会话。大数据库需要一些时间，请勿重复提交。");
      const result = await api("/api/wechat/decipher/run", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, key, file_keys: fileKeys, cipher_mode: cipherMode, wal_replay_confirmed: walConfirmed, auto_capture: autoCapture }) });
      sessionId = "";
      $("wxdecipher-capture-confirm").checked = false;
      $("wxdecipher-process").value = "";
      const report = result.decipher || {};
      wxDecipherStatus([result.message, `本次转换 ${report.messages || 0} 条消息；${report.databases?.length || 0} 个数据库通过校验。`,
        ...(report.key_capture ? [`本次自动匹配 ${report.key_capture.verified_databases} 个数据库密钥（实验性兼容扫描；不代表其他版本已验证）。`] : []),
        ...(report.databases || []).filter((item) => item.wal).map((item) => `${item.source_name}：WAL 恢复 ${item.wal.applied_pages} 个页，取至第 ${item.wal.committed_frames} 个已提交帧。`),
        ...(report.warnings || [])].join("\n"));
      wxdecipherState.files = [];
      wechatState.loaded = false; wechatState.revision = ""; wechatState.selected.clear(); wechatState.selectedId = ""; wechatState.messages = [];
      $("wechat-date-from").value = ""; $("wechat-date-to").value = ""; $("wechat-query").value = ""; $("wechat-chat-type").value = "";
      $("wechat-model-sharing").checked = false;
      await load();
    } catch (error) {
      wxDecipherStatus(error.message || "数据库处理失败；未自动调用模型。", true);
    } finally {
      if (sessionId) {
        try { await api("/api/wechat/decipher/discard", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_id: sessionId }) }); }
        catch { wxDecipherStatus($("wxdecipher-status").textContent + "\n暂存清理未确认，工作台将在会话到期后重试清理。", true); }
      }
      $("wxdecipher-capture-confirm").checked = false;
      $("wxdecipher-process").value = "";
      wxdecipherState.busy = false; renderWxDecipherFiles();
    }
  }

  async function exportSelectedWechat() {
    if (wxdecipherState.exporting) return;
    const ids = [...wechatState.selected];
    if (!ids.length || ids.length > 50) { note("请明确选择 1–50 个会话后导出。", true); return; }
    const format = $("wechat-export-format").value;
    const filters = wechatFilters();
    const confirmed = await confirmAction({ title: "导出所选微信会话", message: `将按当前日期和关键词导出 ${ids.length} 个所选会话到本机文件，不调用模型。下载文件由你自行保管，不受工作台 7 天清理管理。${format === "csv" ? "CSV 会转义可能被表格软件当作公式的内容；需要原样保存请用 JSON/JSONL。" : ""}`, confirmText: "生成本地文件" });
    if (!confirmed) return;
    wxdecipherState.exporting = true; renderWechatSummary();
    try {
      const result = await api("/api/wechat/export", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_ids: ids, format, ...filters }) });
      const url = URL.createObjectURL(new Blob([result.content], { type: result.mime_type }));
      const link = document.createElement("a"); link.href = url; link.download = result.filename;
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      note(`已生成 ${result.message_count} 条消息的 ${format.toUpperCase()} 文件；未调用模型。`);
    } catch (error) { note(error.message, true); }
    finally { wxdecipherState.exporting = false; renderWechatSummary(); }
  }

  function clearWxMedia() {
    wxmediaState.urls.forEach((url) => URL.revokeObjectURL(url));
    wxmediaState.urls = [];
    $("wxdecipher-media-results").replaceChildren();
  }

  async function refreshWxProcesses() {
    if (wxdecipherState.busy || wxdecipherState.captureLoading) return;
    if (!$("wechat-ownership").checked || !$("wxdecipher-capture-confirm").checked) {
      wxDecipherStatus("请先确认本人账号，并明确允许本次只读取钥。", true); return;
    }
    wxdecipherState.captureLoading = true; renderWxDecipherControls();
    const select = $("wxdecipher-process");
    select.replaceChildren();
    try {
      const result = await api("/api/wechat/decipher/processes", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ownership_confirmed: true, capture_confirmed: true }) });
      const placeholder = document.createElement("option"); placeholder.value = ""; placeholder.textContent = "请选择本次取钥的微信进程"; select.append(placeholder);
      for (const item of result.processes || []) {
        const option = document.createElement("option"); option.value = JSON.stringify({ process_id: item.process_id, created_at: item.created_at });
        option.textContent = `${item.name} · PID ${item.process_id}`; select.append(option);
      }
      select.value = "";
      wxDecipherStatus(`${result.message}\n${result.processes?.length ? "请选择一个进程；点击导入后才会读取内存。" : "没有可选进程；请保持本人微信登录并使用普通用户工作台，或手动提供密钥。"}`);
    } catch (error) { wxDecipherStatus(error.message, true); }
    finally { wxdecipherState.captureLoading = false; renderWxDecipherControls(); }
  }

  async function restoreWxMedia(files) {
    if (wxmediaState.busy) return;
    const status = $("wxdecipher-media-status");
    if (!$("wechat-ownership").checked) { status.textContent = "请先确认本人账号且有权处理。"; return; }
    if (!files.length || files.length > 4 || files.some((file) => file.size < 1 || file.size > 16 * 1024 * 1024)) {
      status.textContent = "每次选择 1–4 个媒体副本，单个不超过 16 兆字节。"; return;
    }
    const imageKey = $("wxdecipher-image-key").value;
    const xorKey = $("wxdecipher-xor-key").value.trim();
    if (imageKey && !/^(?:[\x21-\x7e]{16}|[0-9a-f]{32})$/iu.test(imageKey)) {
      status.textContent = "图片密钥须为 16 个 ASCII 字符或 32 位十六进制。"; return;
    }
    if (xorKey && (!/^\d{1,3}$/u.test(xorKey) || Number(xorKey) > 255)) {
      status.textContent = "XOR 参数须为 0–255 的十进制整数或留空。"; return;
    }
    $("wxdecipher-image-key").value = "";
    clearWxMedia();
    wxmediaState.busy = true;
    ["wxdecipher-media-choose", "wxdecipher-media-clear", "wxdecipher-image-key", "wxdecipher-xor-key"].forEach((id) => { $(id).disabled = true; });
    let restored = 0;
    try {
      for (const [index, file] of files.entries()) {
        status.textContent = `本机正在恢复 ${index + 1}/${files.length}：${file.name}`;
        const card = document.createElement("article");
        const title = document.createElement("strong"); title.textContent = file.name; card.append(title);
        $("wxdecipher-media-results").append(card);
        try {
          const response = await fetch("/api/wechat/decipher/media", { method: "POST", headers: {
            "Content-Type": "application/octet-stream", "X-Wechat-Ownership": "true",
            "X-Director-Token": requestToken || "",
            "X-WXDecipher-Image-Key": imageKey, "X-WXDecipher-Xor-Key": xorKey,
          }, body: file });
          if (!response.ok) { const error = await response.json(); throw new Error(error.error || "媒体恢复失败"); }
          const result = JSON.parse(response.headers.get("X-WXDecipher-Media") || "{}");
          const url = URL.createObjectURL(await response.blob());
          wxmediaState.urls.push(url);
          if (result.previewable) { const image = document.createElement("img"); image.src = url; image.alt = `${file.name} 恢复预览`; card.append(image); }
          const detail = document.createElement("p");
          detail.textContent = `${result.decoder} · ${(result.bytes / 1024).toFixed(1)} KB${result.warning ? `。${result.warning}` : ""}`;
          const download = document.createElement("a"); download.href = url; download.download = result.filename; download.textContent = "下载恢复文件";
          card.append(detail, download); restored++;
        } catch (error) { const detail = document.createElement("p"); detail.textContent = error.message || "媒体恢复失败"; card.append(detail); }
      }
      status.textContent = `已恢复 ${restored}/${files.length} 个媒体副本；未修改源文件、未保存密钥、未调用模型。关闭页面或清除预览将释放结果，下载副本不受 7 天清理管理。`;
    } finally {
      wxmediaState.busy = false;
      ["wxdecipher-media-choose", "wxdecipher-media-clear", "wxdecipher-image-key", "wxdecipher-xor-key"].forEach((id) => { $(id).disabled = false; });
    }
  }

  function wechatTime(value) {
    const parsed = new Date(value);
    return Number.isFinite(parsed.getTime())
      ? parsed.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false })
      : String(value || "时间未知");
  }

  function updateWechatSelection() {
    $("wechat-selection-status").textContent = `已选 ${wechatState.selected.size} 个`;
    const visibleIds = wechatState.rows.map((row) => row.conversation_id);
    $("wechat-select-all").checked = visibleIds.length > 0 && visibleIds.every((id) => wechatState.selected.has(id));
    $("wechat-select-all").indeterminate = visibleIds.some((id) => wechatState.selected.has(id)) && !$("wechat-select-all").checked;
    $("review-selected-wechat").disabled = wechatState.creating || wechatState.selected.size === 0;
  }

  function renderWechatConversations() {
    const box = $("wechat-conversations");
    const previousScroll = box.scrollTop;
    box.classList.toggle("empty", wechatState.rows.length === 0);
    if (!wechatState.rows.length) {
      box.replaceChildren();
      box.textContent = wechatState.error || (wechatState.loading ? "正在读取会话…" : "当前筛选范围没有会话。");
      updateWechatSelection();
      return;
    }
    box.replaceChildren(...wechatState.rows.map((row) => {
      const card = document.createElement("article");
      card.className = `wechat-conversation-card${wechatState.selected.has(row.conversation_id) ? " selected" : ""}${wechatState.selectedId === row.conversation_id ? " previewing" : ""}`;
      card.tabIndex = 0;
      const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.checked = wechatState.selected.has(row.conversation_id);
      checkbox.setAttribute("aria-label", `选择${row.display_name}（${row.account_label || "历史导入"}${row.account_id ? " · " + row.account_id : ""}）`);
      checkbox.onchange = () => {
        if (checkbox.checked) wechatState.selected.add(row.conversation_id); else wechatState.selected.delete(row.conversation_id);
        renderWechatConversations(); renderWechatSummary();
      };
      const copy = document.createElement("div"); copy.className = "wechat-conversation-copy";
      const title = document.createElement("strong"); title.textContent = row.display_name || "未命名会话";
      const account = document.createElement("small"); account.className = "wechat-conversation-account";
      account.textContent = `${row.account_label || "历史导入"}${row.account_id ? " · " + row.account_id : " · 未绑定本人 ID"}`;
      const meta = document.createElement("small");
      const type = row.chat_type === "group" ? "群聊" : row.chat_type === "official" ? "公众号" : row.chat_type === "direct" ? "个人会话" : "其他";
      meta.textContent = `${type} · 保留 ${row.retained_messages} 条 · ${wechatTime(row.last_sent_at)}`;
      const preview = document.createElement("p"); preview.className = "wechat-conversation-preview"; preview.textContent = row.last_preview || "无文字预览";
      copy.append(title, account, meta, preview); card.append(checkbox, copy);
      const previewConversation = () => {
        wechatState.selectedId = row.conversation_id;
        renderWechatConversations();
        loadWechatMessages(row.conversation_id);
      };
      card.onclick = (event) => { if (event.target !== checkbox) previewConversation(); };
      card.onkeydown = (event) => { if ((event.key === "Enter" || event.key === " ") && event.target === card) { event.preventDefault(); previewConversation(); } };
      return card;
    }));
    box.scrollTop = previousScroll;
    updateWechatSelection();
  }

  function renderWechatMessages() {
    const box = $("wechat-messages");
    const selected = wechatState.rows.find((row) => row.conversation_id === wechatState.selectedId);
    $("wechat-preview-title").textContent = selected ? `${selected.display_name} · ${selected.account_id || selected.account_label || "历史导入"}` : "会话预览";
    box.classList.toggle("empty", wechatState.messages.length === 0);
    if (!wechatState.messages.length) {
      box.replaceChildren();
      box.textContent = wechatState.previewLoading ? "正在读取消息…" : "选择一个会话查看原文。";
      return;
    }
    box.replaceChildren(...wechatState.messages.map((message) => {
      const item = document.createElement("article"); item.className = `wechat-message${message.is_self ? " mine" : ""}`;
      const header = document.createElement("header");
      const sender = document.createElement("strong"); sender.textContent = message.sender_name || (message.is_self ? "我" : "对方");
      const time = document.createElement("time"); time.textContent = `${wechatTime(message.sent_at)} · ${message.kind || "消息"}`;
      const text = document.createElement("p"); text.textContent = message.content || `[${message.kind || "非文字消息"}]`;
      header.append(sender, time); item.append(header, text); return item;
    }));
    box.scrollTop = box.scrollHeight;
  }

  async function loadWechatMessages(conversationId) {
    const generation = ++wechatState.previewGeneration;
    wechatState.previewLoading = true; wechatState.messages = []; renderWechatMessages();
    try {
      const result = await api("/api/wechat/query", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "messages", conversation_id: conversationId, ...wechatFilters(), limit: 100 }),
      });
      if (generation !== wechatState.previewGeneration || conversationId !== wechatState.selectedId) return;
      wechatState.messages = result.rows || [];
    } catch (error) {
      if (generation === wechatState.previewGeneration) note(error.message, true);
    } finally {
      if (generation === wechatState.previewGeneration) { wechatState.previewLoading = false; renderWechatMessages(); }
    }
  }

  async function loadWechatConversations({ force = false } = {}) {
    if (wechatState.loading || (!force && wechatState.loaded && model?.wechat?.revision === wechatState.revision)) return;
    wechatState.loading = true; wechatState.error = ""; renderWechatConversations();
    try {
      const result = await api("/api/wechat/query", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "conversations", ...wechatFilters(), limit: 200, offset: 0 }),
      });
      wechatState.rows = result.rows || [];
      const available = new Set(wechatState.rows.map((row) => row.conversation_id));
      wechatState.selected = new Set([...wechatState.selected].filter((id) => available.has(id)));
      if (!available.has(wechatState.selectedId)) { wechatState.selectedId = ""; wechatState.messages = []; }
      wechatState.loaded = true;
      wechatState.revision = model?.wechat?.revision || wechatState.revision;
    } catch (error) {
      wechatState.rows = []; wechatState.error = error.message;
    } finally {
      wechatState.loading = false; renderWechatConversations(); renderWechatMessages(); renderWechatSummary();
    }
  }

  async function createWechatReview({ today = false } = {}) {
    if (!$("wechat-model-sharing").checked) throw new Error("请先确认允许当前任务使用所选会话文字。");
    const todayText = formatDate(new Date());
    const filters = today ? { date_from: todayText, date_to: todayText, query: "" } : wechatFilters();
    if (!filters.date_from || !filters.date_to) throw new Error("请选择开始和结束日期。");
    const conversationIds = today ? [] : [...wechatState.selected];
    if (!today && !conversationIds.length) throw new Error("请至少选择一个会话。");
    const runtime = taskRuntimeSelection();
    const modelKey = runtime.requested_model || model?.model?.default_model;
    const provider = modelProviders().find((item) => modelKey?.startsWith(item.id + "/") && item.status === "configured");
    const selectedModel = provider?.models.find((item) => modelKey === provider.id + "/" + item.id && item.enabled !== false && item.tools !== false);
    if (!provider || !selectedModel) throw new Error("整理微信前，请先配置并选择具体的 API 供应商和模型。");
    if (isCliProvider(provider)) throw new Error("微信会话整理首版不支持 Claude Code 或 Codex CLI；请改选可明确冻结接收方的 API 供应商模型。");
    runtime.requested_model = modelKey;
    const recipient = { provider_id: provider.id, base_url: provider.base_url, api: selectedModel.api || provider.api, model_id: selectedModel.id };
    const modelName = `${provider.name} · ${selectedModel.id}`;
    const confirmed = await confirmAction({
      title: "确认整理微信会话",
      message: today
        ? `将把今天全部账号中有消息的会话交给“${modelName}”整理。`
        : `将把 ${conversationIds.length} 个会话在 ${filters.date_from} 至 ${filters.date_to} 的所选内容交给“${modelName}”整理。`,
      detail: `账号：${today ? "全部已导入账号" : [...new Set(wechatState.rows.filter((row) => conversationIds.includes(row.conversation_id)).map((row) => row.account_id || row.account_label || "历史导入"))].join("、")}。接收地址：${recipient.base_url}；协议：${recipient.api}。云端处理会使文字离开本机；仅允许此供应商和模型处理所选范围，不自动更新台账。`,
      confirmText: "确认并开始",
    });
    if (!confirmed) return;
    const scope = await api("/api/wechat/scopes", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_id: selectedProject,
        conversation_ids: conversationIds,
        all_in_period: today,
        date_from: filters.date_from,
        date_to: filters.date_to,
        query: filters.query || "",
        model_sharing_confirmed: true,
        model_recipient: recipient,
      }),
    });
    const request = [
      "【微信会话整理】",
      `授权范围编号：${scope.scope_id}`,
      `整理范围：${scope.title}`,
      `日期范围：${scope.date_from} 至 ${scope.date_to}`,
      `会话数量：${scope.conversation_count}；消息数量：${scope.message_count}`,
      "请只读取该授权范围，按人员与群聊整理关键进展、明确承诺、客户异议、风险和下一步；每个重要判断保留 wechat://message/ 消息定位。不要自动更新销售台账，不要对外发送。",
    ].join("\n");
    const task = await api("/api/task-requests", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile_id: "sales-director", service_id: "wechat-review", project_id: selectedProject, request, ...runtime }),
    });
    note(`微信会话整理任务已登记（${task.request_id}）。`);
    await load(); switchView("tasks");
  }

  function reimbursementMaterialRow(item) {
    const row = document.createElement("div"); row.className = "reimbursement-material-row";
    const copy = document.createElement("div");
    const title = document.createElement("strong"); title.textContent = item.name;
    const location = item.location === "project" ? `已在项目：${projectById(item.project_id)?.name || item.project_id}` : "报销材料库";
    const meta = document.createElement("small"); meta.textContent = `${location} · ${fileSize(item.size || 0)} · ${item.modified_at}`;
    copy.append(title, meta);
    const actions = document.createElement("div"); actions.className = "file-actions";
    const open = document.createElement("button"); open.className = "secondary"; open.textContent = "打开"; open.onclick = () => openManagedFile(item);
    const rename = document.createElement("button"); rename.className = "secondary"; rename.textContent = "重命名"; rename.onclick = () => renameManagedFile(item);
    actions.append(open, rename);
    if (item.location !== "project") {
      const project = document.createElement("select");
      (model.projects || []).filter((entry) => entry.status === "active").forEach((entry) => {
        const option = document.createElement("option"); option.value = entry.project_id; option.textContent = entry.name; project.append(option);
      });
      project.value = selectedProject;
      const move = document.createElement("button"); move.className = "secondary"; move.textContent = "移到项目";
      move.onclick = async () => {
        const destination = projectById(project.value);
        if (!await confirmAction({ title: "移动到项目空间？", message: `${item.name}\n目标项目：${destination?.name || project.value}\n移动后报销材料库仍保留归档记录。`, confirmText: "移动文件" })) return;
        try {
          const response = await api("/api/reimbursements/move-to-project", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...managedFilePayload(item), project_id: project.value }) });
          note(response.message); await load();
        } catch (error) { note(error.message, true); }
      };
      actions.append(project, move);
    }
    const remove = document.createElement("button"); remove.className = "danger-outline"; remove.textContent = "删除"; remove.onclick = () => trashManagedFile(item);
    actions.append(remove); row.append(copy, actions); return row;
  }

  function renderReimbursementLibrary() {
    const library = model.reimbursements || { batches: [], trash: [], legacy_count: 0 };
    const migrate = $("migrate-legacy-expenses");
    migrate.hidden = !library.legacy_count;
    migrate.textContent = library.legacy_count ? `迁移旧材料（${library.legacy_count}）` : "迁移旧材料";
    const box = $("reimbursement-batches");
    box.classList.toggle("empty", !library.batches.length);
    if (!library.batches.length) { box.replaceChildren(); box.textContent = "还没有导入报销材料。"; }
    else box.replaceChildren(...library.batches.map((batch) => {
      const card = document.createElement("details"); card.className = "reimbursement-batch";
      card.open = reimbursementBatchExpansion.get(batch.batch_id) ?? true;
      card.addEventListener("toggle", () => reimbursementBatchExpansion.set(batch.batch_id, card.open));
      const summary = document.createElement("summary");
      const copy = document.createElement("div");
      const title = document.createElement("strong"); title.textContent = `${batch.material_count} 个材料`;
      const meta = document.createElement("small"); meta.textContent = `${batch.mailbox || "邮箱导入"} · ${batch.updated_at || batch.created_at}`;
      copy.append(title, meta);
      const remove = document.createElement("button"); remove.className = "danger-outline"; remove.type = "button"; remove.textContent = "删除整批";
      remove.onclick = async (event) => {
        event.preventDefault(); event.stopPropagation();
        if (!await confirmAction({ title: "删除整批报销材料？", message: `本批共 ${batch.material_count} 个材料，将整体移入回收站并可恢复。`, confirmText: "删除整批", tone: "danger" })) return;
        try { const response = await api(`/api/reimbursements/batches/${encodeURIComponent(batch.batch_id)}/trash`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); note(response.message); await load(); }
        catch (error) { note(error.message, true); }
      };
      summary.append(copy, remove);
      const materials = document.createElement("div"); materials.className = "reimbursement-material-list";
      if (batch.materials.length) materials.append(...batch.materials.map(reimbursementMaterialRow));
      else { materials.classList.add("empty"); materials.textContent = "本批次当前没有材料。"; }
      card.append(summary, materials); return card;
    }));
    const trash = $("file-trash-list");
    trash.classList.toggle("empty", !library.trash.length);
    if (!library.trash.length) { trash.replaceChildren(); trash.textContent = "回收站为空。"; }
    else trash.replaceChildren(...library.trash.map((entry) => {
      const name = entry.kind === "reimbursement-batch" ? `报销批次（${entry.material_count || 0} 个材料）` : (entry.stored_name || "文件");
      const row = summaryRow("file-row", name, `删除于 ${entry.trashed_at}`);
      const restore = document.createElement("button"); restore.className = "secondary"; restore.textContent = "恢复";
      restore.onclick = async () => { try { const response = await api(`/api/file-trash/${encodeURIComponent(entry.trash_id)}/restore`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); note(response.message); await load(); } catch (error) { note(error.message, true); } };
      row.append(restore); return row;
    }));
  }

  function projectCard(project) {
    const card = document.createElement("article");
    card.className = `project-card ${project.project_id === selectedProject ? "selected" : ""}`;
    const header = document.createElement("header");
    const titleBox = document.createElement("div");
    const title = document.createElement("strong"); title.textContent = project.name;
    const status = document.createElement("span"); status.className = `status ${project.status === "active" ? "completed" : "cancelled"}`; status.textContent = project.status === "active" ? "进行中" : "已归档";
    titleBox.append(title); header.append(titleBox, status);
    const description = document.createElement("p"); description.textContent = project.description || "尚未填写项目说明。";
    const metrics = document.createElement("div"); metrics.className = "project-metrics";
    [[project.task_count, "任务"], [project.file_count, "资料"], [project.artifact_count, "产物"]].forEach(([number, text]) => {
      const item = document.createElement("span"); const strong = document.createElement("strong"); strong.textContent = String(number || 0); item.append(strong, text); metrics.append(item);
    });
    const actions = document.createElement("div"); actions.className = "project-actions";
    const select = document.createElement("button"); select.className = "primary"; select.textContent = project.project_id === selectedProject ? "当前项目" : "进入项目"; select.disabled = project.status !== "active";
    select.onclick = () => { selectedProject = project.project_id; renderProjectSelectors(); renderProjects(); note(`已切换到项目：${project.name}`); };
    const task = document.createElement("button"); task.className = "secondary"; task.textContent = "发起工作"; task.disabled = project.status !== "active";
    task.onclick = () => { selectedProject = project.project_id; switchView("work"); renderProjectSelectors(); };
    actions.append(select, task);
    if (project.project_id !== "project-default") {
      const archive = document.createElement("button"); archive.className = "secondary"; archive.textContent = project.status === "active" ? "归档" : "恢复";
      archive.onclick = async () => {
        const target = project.status === "active" ? "archived" : "active";
        if (target === "archived" && !await confirmAction({
          title: "归档这个项目？",
          message: "归档后，该项目的每日任务会暂停；项目资料和历史记录仍会保留。",
          confirmText: "归档项目",
        })) return;
        try { await api(`/api/projects/${encodeURIComponent(project.project_id)}/status`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: target }) }); note(target === "active" ? "项目已恢复。" : "项目已归档，相关每日任务已暂停。"); await load(); } catch (error) { note(error.message, true); }
      };
      actions.append(archive);
    }
    card.append(header, description, metrics, actions);
    return card;
  }

  function renderProjects() {
    const list = $("project-list");
    const projects = model.projects || [];
    list.classList.toggle("empty", projects.length === 0);
    list.replaceChildren(...projects.map(projectCard));
    const files = (model.project_files || []).filter((item) => item.project_id === selectedProject);
    const fileBox = $("project-files");
    fileBox.classList.toggle("empty", files.length === 0);
    if (files.length) fileBox.replaceChildren(...files.map(fileRow));
    else { fileBox.replaceChildren(); fileBox.textContent = "当前项目还没有资料，可上传电子文档、文字文档、表格、逗号分隔文件、文本或演示文稿。"; }
    const artifactPaths = new Set((model.tasks || [])
      .filter((task) => task.project_id === selectedProject)
      .flatMap((task) => Array.isArray(task.artifacts) ? task.artifacts : [])
      .filter((path) => typeof path === "string" && path.startsWith("outputs/")));
    const projectOutputs = (model.outputs || []).filter((item) => artifactPaths.has(item.path) || item.project_id === selectedProject);
    const outputsBox = $("project-outputs");
    outputsBox.classList.toggle("empty", projectOutputs.length === 0);
    if (projectOutputs.length) outputsBox.replaceChildren(...projectOutputs.map((item) => summaryRow("output-row", item.name, item.modified_at)));
    else { outputsBox.replaceChildren(); outputsBox.textContent = "当前项目还没有正式产物。"; }
  }

  function renderSchedules() {
    const allowed = (currentProfile()?.services || []).filter((service) => !service.id.startsWith("bid-") && !["presentation-studio", "presentation-studio-quick", "weekly-deck", "pdf-import"].includes(service.id));
    setSelectOptions($("schedule-service"), allowed.map((service) => ({ value: service.id, label: service.display_name })), $("schedule-service").value || "sales-review");
    const list = $("schedule-list");
    const schedules = model.schedules || [];
    if (!schedulePanelInitialized) {
      $("schedule-create-panel").hidden = schedules.length > 0;
      schedulePanelInitialized = true;
    }
    list.classList.toggle("empty", schedules.length === 0);
    if (!schedules.length) { list.replaceChildren(); list.textContent = "暂无定时任务。可从上方三个模板开始。"; return; }
    list.replaceChildren(...schedules.map((schedule) => {
      const row = document.createElement("div"); row.className = "schedule-row summary-row";
      const time = document.createElement("span"); time.className = "schedule-time"; time.textContent = schedule.time_local;
      const copy = document.createElement("div"); copy.className = "schedule-copy";
      const title = document.createElement("strong"); title.textContent = schedule.name;
      const scheduleModel = schedule.requested_model || "默认模型";
      const scheduleThinking = thinkingLabels[schedule.requested_thinking_level] || "默认思考";
      const meta = document.createElement("small"); meta.textContent = `${projectById(schedule.project_id)?.name || "默认项目"} · ${serviceById(schedule.service_id)?.display_name || schedule.service_id} · ${scheduleModel} / ${scheduleThinking} · ${schedule.last_enqueued_date ? `最近排队 ${schedule.last_enqueued_date}` : "尚未执行"}`;
      const request = document.createElement("p"); request.textContent = schedule.request;
      copy.append(title, meta, request);
      const actions = document.createElement("div"); actions.className = "schedule-actions";
      const run = document.createElement("button"); run.className = "secondary"; run.textContent = "立即执行";
      run.onclick = async () => { try { const reply = await api(`/api/schedules/${encodeURIComponent(schedule.schedule_id)}/run`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); note(`已排队：${reply.request_id}`); await load(); } catch (error) { note(error.message, true); } };
      const toggle = document.createElement("button"); toggle.className = schedule.enabled ? "secondary" : "primary"; toggle.textContent = schedule.enabled ? "暂停" : "启用";
      toggle.onclick = async () => { try { await api(`/api/schedules/${encodeURIComponent(schedule.schedule_id)}/enabled`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: !schedule.enabled }) }); note(schedule.enabled ? "每日任务已暂停。" : "每日任务已启用。"); await load(); } catch (error) { note(error.message, true); } };
      actions.append(run, toggle); row.append(time, copy, actions); return row;
    }));
  }

  function renderDashboard() {
    const tasks = model.tasks || [];
    const activeTasks = tasks.filter((task) => !isHistoricalTask(task));
    const period = currentWeek();
    const inWeek = tasks.filter((task) => String(task.updated_at || task.created_at || "") >= period.start);
    const pending = tasks.filter((task) => task.status === "waiting_approval" && !task.approval_request);
    const running = tasks.filter((task) => ["running", "approval_pending"].includes(displayStatus(task)));
    const queued = tasks.filter((task) => displayStatus(task) === "requested");
    const interrupted = tasks.filter((task) => ["interrupted", "approval_stalled"].includes(displayStatus(task)));
    const completed = inWeek.filter((task) => task.status === "completed");
    $("home-pending").textContent = pending.length;
    $("home-running").textContent = running.length;
    $("home-completed").textContent = completed.length;
    $("approval-total").textContent = pending.length;
    $("nav-task-count").textContent = pending.length ? String(pending.length) : "";
    const hour = new Date().getHours();
    $("greeting").textContent = `${hour < 11 ? "早上好" : hour < 14 ? "中午好" : hour < 18 ? "下午好" : "晚上好"}，今天有 ${pending.length + running.length + queued.length + interrupted.length} 项需要处理`;
    $("week-label").textContent = `${period.start} 至 ${period.end}`;
    const denominator = inWeek.length;
    const progress = denominator ? Math.round(completed.length / denominator * 100) : 0;
    $("week-progress-label").textContent = `${progress}%`;
    $("week-progress-bar").style.width = `${progress}%`;
    const renderCompactTasks = (box, source, empty) => {
      box.classList.toggle("empty", source.length === 0);
      if (!source.length) { box.replaceChildren(); box.textContent = empty; return; }
      box.replaceChildren(...source.map((task) => {
        const row = document.createElement("button"); row.className = "compact-task";
        const copy = document.createElement("span");
        const title = document.createElement("strong"); title.textContent = serviceById(task.service_id)?.display_name || "销售任务";
        const request = document.createElement("small"); request.textContent = displayTaskRequest(task).replace(/\s+/gu, " ").slice(0, 60);
        copy.append(title, request);
        const effectiveStatus = displayStatus(task);
        const status = document.createElement("i"); status.className = `status ${effectiveStatus}`; status.textContent = label[effectiveStatus] || "未知状态";
        row.append(copy, status); row.onclick = () => { switchView("tasks"); renderWorkflow(task); }; return row;
      }));
    };
    renderCompactTasks($("home-approvals"), pending.slice(0, 6), "暂无待确认事项");
    renderCompactTasks($("home-recent"), activeTasks.slice(0, 5), "当前没有待处理任务");
  }

  function render() {
    renderAppUpdates();
    renderModelSettings(); renderProviderSettings(); renderSearchSettings(); renderSearchGatewaySettings(); renderTaskRuntimeOptions(); renderRuntimeSettings(); renderMailSettings(); renderProjectSelectors(); renderServices(); renderTaskForm(); renderTasks();
    renderData(); renderOutputs(); renderProjects(); renderSchedules(); renderDashboard(); renderReimbursementLibrary(); renderWechatSummary(); renderToolPanels(); renderCustomerOperations(); renderAttention(); renderBidding(); switchView(currentView);
  }

  async function createTask(request) {
    let serviceId = selectedService;
    if (["sales-review", "industry-research"].includes(serviceId) && $("task-readonly").checked) serviceId += "-readonly";
    const structured = request.startsWith("[PRESENTATION_BRIEF]");
    if (serviceId === "presentation-studio" && structured) {
      const brief = JSON.parse(request.slice("[PRESENTATION_BRIEF]".length, -"[/PRESENTATION_BRIEF]".length).trim());
      if (brief.mode === "quick" && brief.confidentiality === "internal" && brief.scene !== "government" && brief.source_scope === "profile-knowledge-only") serviceId = "presentation-studio-quick";
    }
    if (!publicSearchReady(serviceId, true)) {
      throw new Error(["error", "missing_token"].includes(model?.search_gateway?.status)
        ? "搜索聚合网关配置异常，请前往“设置 > 搜索聚合网关”修复或停用。"
        : model?.search_gateway?.restart_required
        ? "搜索聚合网关配置已变更，请关闭并重新打开销售总监智能工作台后再创建该任务。"
        : model?.search?.restart_required
        ? "公开检索配置已保存，请关闭并重新打开销售总监智能工作台后再创建该任务。"
        : "公开检索服务暂不可用，请前往“设置 > 公开检索”查看状态。");
    }
    return api("/api/task-requests", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile_id: selectedProfile, service_id: serviceId, project_id: selectedProject, request: structured ? request : `${request}${selectedCustomerContextText()}`, ...taskRuntimeSelection() }),
    });
  }

  function guidedRequest() {
    const config = guidedServices[selectedService.replace(/-readonly$/u, "")];
    if (!config) throw new Error("当前服务尚未配置引导表单。");
    const draft = guidedDrafts[selectedService] || {};
    config.fields.forEach((field) => {
      if (field.required && !String(draft[field.id] || "").trim()) throw new Error(`请填写“${field.label}”。`);
    });
    const lines = [`【${config.title}】`];
    config.fields.forEach((field) => {
      const value = String(draft[field.id] || "").trim();
      if (value) lines.push(`${field.label.replace(/（.*?）/gu, "")}：${value}`);
    });
    const notes = String(guidedNotes[selectedService] || "").trim();
    if (notes) lines.push(`补充说明：${notes}`);
    lines.push(`请执行：${config.instruction}`);
    if (["sales-review", "industry-research", "sales-review-readonly", "industry-research-readonly"].includes(selectedService) && $("task-readonly").checked) {
      lines.push("本次仅分析，不更新台账、不入库；直接交付结论与证据。需要保存时另行确认并建立写入任务。");
    }
    return lines.join("\n");
  }

  function autoOutputName(prefix, includeTime = true) {
    const now = new Date();
    const date = formatDate(now).replaceAll("-", "");
    const time = `${String(now.getHours()).padStart(2, "0")}${String(now.getMinutes()).padStart(2, "0")}${String(now.getSeconds()).padStart(2, "0")}`;
    return `${prefix}-${date}${includeTime ? `-${time}` : ""}.pptx`;
  }

  function presentationBrief() {
    const topic = $("ppt-topic").value.trim();
    const duration = Number($("ppt-duration").value);
    const pages = Number($("ppt-pages").value);
    const outputName = $("ppt-output").value.trim() || autoOutputName("sales-deck");
    const expectedDecision = $("ppt-decision").value.trim() || "确认下一步行动与所需资源";
    const scene = $("ppt-scene").value;
    const occasions = { weekly: "周五销售例会", industry: "客户与行业专题汇报", government: "政府合作沟通会", custom: "销售或客户方案汇报" };
    if (!topic) throw new Error("请先填写要制作的演示文稿主题。");
    if (topic.length > 240) throw new Error("演示文稿主题不能超过 240 字。");
    if (expectedDecision.length > 500) throw new Error("期望决策不能超过 500 字。");
    if (!Number.isInteger(duration) || duration < 3 || duration > 120) throw new Error("演讲时长必须是 3–120 分钟的整数。");
    if (!Number.isInteger(pages) || pages < 4 || pages > 10) throw new Error("首版页数必须是 4–10 页。");
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.pptx$/u.test(outputName)) throw new Error("输出文件名格式无效，请使用英文字母、数字、点、下划线或连字符，并以 .pptx 结尾。");
    return {
      schema_version: "1.0", scene, mode: $("ppt-mode").value, topic,
      audience: $("ppt-audience").value.trim() || "客户决策人和销售管理层",
      purpose: `推动${expectedDecision}`.slice(0, 180), occasion: occasions[scene], language: $("ppt-language").value,
      duration_minutes: duration, target_slides: pages, design_system: { token_id: $("ppt-style").value },
      source_scope: $("ppt-mode").value === "quick" && $("ppt-confidentiality").value === "internal" && scene !== "government" ? "profile-knowledge-only" : "public-web-and-profile-knowledge", confidentiality: $("ppt-confidentiality").value,
      expected_decision: expectedDecision, output_name: outputName,
    };
  }

  function updatePresentationSourcePolicy() {
    const localOnly = $("ppt-mode").value === "quick" && $("ppt-confidentiality").value === "internal" && $("ppt-scene").value !== "government";
    $("ppt-source-policy").textContent = localOnly ? "仅当前销售资料库；不检索网页；一次最终审批" : "公开网页 + 当前销售资料库；大纲与正式生成分别审批";
  }

  function weeklyBrief() {
    const period = currentWeek();
    const focus = $("weekly-focus").value.trim();
    const focusText = focus ? ` 特别关注：${focus}` : "";
    return {
      schema_version: "1.0", scene: "weekly", mode: "quick",
      topic: `${period.start} 至 ${period.end} 个性化销售行动简报：为每位销售生成优先动作，并汇总客户进展、资源需求、风险和待分配事项。${focusText}`,
      audience: "销售人员与销售管理层", purpose: "让每位销售明确下周动作，并帮助销售总监确认优先级与资源配置", occasion: "周五销售复盘与下周行动对齐", language: "zh-CN",
      duration_minutes: 15, target_slides: 6, design_system: { token_id: "management-report" },
      source_scope: "local-sales-records-and-authorized-assets", confidentiality: "internal",
      expected_decision: "确认每位销售的优先动作、待分配客户、资源配置和管理层关注项", output_name: autoOutputName("sales-action-brief", false),
    };
  }

  async function load() {
    try {
      model = await api("/api/bootstrap");
    } catch (error) {
      if (error.code !== "UPDATING") throw error;
      syncAppUpdateState(await api("/api/app-updates"));
      renderAppUpdates();
      if (appUpdateState?.installation?.phase === "starting") {
        switchView("settings");
        note("正在确认新版启动，完成后将自动恢复工作台。", false);
      }
      return;
    }
    const composerFocus = captureTaskComposerFocus();
    requestToken = model.request_token;
    if (!selectedProfile || !model.profiles.some((item) => item.id === selectedProfile)) { selectedProfile = model.profiles.find((item) => item.id === "sales-director")?.id || model.profiles[0]?.id; selectedService = currentProfile()?.default_service; }
    if (!currentProfile()?.services.some((item) => item.id === selectedService)) selectedService = currentProfile()?.default_service;
    if (!projectById(selectedProject) || projectById(selectedProject)?.status !== "active") selectedProject = model.projects?.find((item) => item.status === "active")?.project_id || "project-default";
    if (model.library_revision !== libraryState.expectedRevision || libraryState.error || !libraryState.version) await loadLibrary();
    render();
    restoreTaskComposerFocus(composerFocus);
    freeChat.update(model.model);
    if (currentView === "tools" && activeTool === "wechat" && model?.wechat?.configured && model.wechat.revision !== wechatState.revision) {
      loadWechatConversations({ force: true });
    }
    if (currentView === "sales" && !customerState.loaded && !customerState.loading) loadCustomers();
    if (!customerState.attentionLoaded) loadAttention();
    if (currentView === "home" && !customerState.recommendations.length && !customerState.recommendationsLoading) loadGlobalRecommendations();
  }

  $("create").onclick = async () => {
    try {
      const response = await createTask(guidedRequest());
      guidedDrafts[selectedService] = {};
      guidedNotes[selectedService] = "";
      renderGuidedForm(true);
      note(`任务已登记（${response.request_id}），等待智能核心接手。`);
    } catch (error) { note(error.message, true); }
  };

  $("request-notes").addEventListener("input", () => { guidedNotes[selectedService] = $("request-notes").value; });

  document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
  document.querySelector('[data-view="sales"]')?.addEventListener("click", () => { if (!customerState.loaded && !customerState.loading) loadCustomers(); });
  $("go-back").onclick = navigateBack;
  window.addEventListener("keydown", (event) => {
    if (event.altKey && event.key === "ArrowLeft" && viewHistory.length) { event.preventDefault(); navigateBack(); }
  });
  document.querySelectorAll("[data-service]").forEach((button) => button.addEventListener("click", () => openService(button.dataset.service)));
  $("menu-toggle").onclick = () => $("sidebar").classList.toggle("open");
  $("task-project").onchange = () => { selectedProject = $("task-project").value; $("schedule-project").value = selectedProject; };
  $("home-project").onchange = () => { selectedProject = $("home-project").value; $("task-project").value = selectedProject; $("schedule-project").value = selectedProject; renderProjects(); };
  $("schedule-project").onchange = () => { selectedProject = $("schedule-project").value; $("task-project").value = selectedProject; };
  $("library-query").addEventListener("input", () => { libraryState.query = $("library-query").value.trim(); libraryState.specialFilter = ""; libraryState.renderedKey = ""; renderLibrary(); });
  $("library-status-filter").addEventListener("change", () => { libraryState.status = $("library-status-filter").value; libraryState.specialFilter = ""; libraryState.renderedKey = ""; renderLibrary(); });
  $("library-project-filter").addEventListener("change", () => { libraryState.projectId = $("library-project-filter").value; libraryState.specialFilter = ""; libraryState.renderedKey = ""; renderLibrary(); });
  $("library-account-filter").addEventListener("change", () => { libraryState.accountId = $("library-account-filter").value; libraryState.specialFilter = ""; libraryState.renderedKey = ""; renderLibrary(); });
  document.querySelectorAll("[data-library-category]").forEach((button) => button.addEventListener("click", () => { libraryState.category = button.dataset.libraryCategory || ""; libraryState.specialFilter = ""; libraryState.selectedId = ""; libraryState.renderedKey = ""; libraryState.previewKey = ""; renderLibrary(); }));
  document.querySelectorAll("[data-library-stat]").forEach((button) => button.addEventListener("click", () => { const value = button.dataset.libraryStat; libraryState.category = ""; libraryState.status = value === "verified" ? "verified" : ""; libraryState.specialFilter = value === "verified" ? "" : value; $("library-status-filter").value = libraryState.status; libraryState.selectedId = ""; libraryState.renderedKey = ""; renderLibrary(); }));
  const customerFilterMap = [["customer-filter-query", "query", "input"], ["customer-filter-owner", "owner", "input"], ["customer-filter-region", "region", "input"], ["customer-filter-industry", "industry", "input"], ["customer-filter-stage", "stage", "input"], ["customer-filter-health", "health", "input"], ["customer-filter-updated", "updated", "change"]];
  let customerFilterTimer = null;
  customerFilterMap.forEach(([id, key, eventName]) => $(id)?.addEventListener(eventName, () => { customerState.filters[key] = $(id).value.trim(); customerState.cursor = ""; clearTimeout(customerFilterTimer); customerFilterTimer = setTimeout(() => loadCustomers({ force: true }), eventName === "input" ? 260 : 0); }));
  $("customer-filter-clear").onclick = () => { Object.keys(customerState.filters).forEach((key) => { customerState.filters[key] = ""; }); customerState.cursor = ""; customerFilterMap.forEach(([id]) => { $(id).value = ""; }); loadCustomers({ force: true }); };
  $("customer-refresh").onclick = async () => { const selected = customerState.selectedId; const tab = customerState.activeTab; customerState.cursor = ""; const refresh = loadCustomers({ force: true }); const generation = customerState.listGeneration; await refresh; if (selected && customerState.selectedId === selected && customerState.listGeneration === generation) await selectCustomer(selected, { tab }); };
  $("customer-load-more").onclick = () => { if (!customerState.loading && customerState.hasMore) loadCustomers({ append: true }); };
  $("customer-context-clear").onclick = clearCustomerContext; $("clear-quick-customer").onclick = clearCustomerContext; $("clear-global-customer").onclick = clearCustomerContext;
  $("customer-context-open").onclick = () => { if (customerState.selectedId) { switchView("sales"); $("customer-detail")?.scrollIntoView({ behavior: "smooth", block: "start" }); } };
  $("global-customer-open").onclick = () => { if (customerState.selectedId) { switchView("sales"); $("customer-detail")?.scrollIntoView({ behavior: "smooth", block: "start" }); } };
  $("refresh-attention").onclick = () => loadAttention({ force: true });
  $("refresh-recommendations").onclick = () => loadGlobalRecommendations({ force: true });
  $("quick-command-start").onclick = async () => {
    const input = $("quick-command").value.trim();
    if (!input) { note("请先输入希望完成的工作。", true); return; }
    await matchPlayInput(input);
  };
  $("quick-command").addEventListener("keypress", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      $("quick-command-start").click();
    }
  });
  $("close-play-matcher").onclick = () => {
    $("play-matcher-panel").hidden = true;
    $("quick-command").value = "";
  };
  async function matchPlayInput(userInput) {
    const panel = $("play-matcher-panel");
    const result = $("play-matcher-result");
    panel.hidden = false;
    result.innerHTML = '<div class="play-loading">正在识别工作意图…</div>';
    try {
      const accountId = customerState.selectedId || "";
      const response = await api("/api/a4/match-play", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_input: userInput, account_id: accountId || null })
      });
      if (!response.success) throw new Error(response.error || "意图识别失败");
      renderPlayResult(response.data, userInput);
    } catch (error) {
      result.innerHTML = `<div class="play-error">意图识别失败：${error.message}</div>`;
    }
  }
  function renderPlayResult(data, userInput) {
    const result = $("play-matcher-result");
    if (data.status === "no_match") {
      const suggestions = Array.isArray(data.suggestions) ? data.suggestions : [];
      result.innerHTML = `
        <div class="play-no-match">
          <p>${data.message || "无法匹配到已知工作流程"}</p>
          ${suggestions.length > 0 ? `
            <p>您可以尝试：</p>
            <ul>${suggestions.map(s => `<li>${s}</li>`).join("")}</ul>
          ` : ""}
        </div>
      `;
      return;
    }
    if (data.status === "need_more_info") {
      const questions = Array.isArray(data.questions) ? data.questions : [];
      result.innerHTML = `
        <div class="play-need-info">
          <h3>已匹配：${data.play || "未知流程"}</h3>
          <p>请补充以下信息：</p>
          ${questions.length > 0 ? `<ul>${questions.map(q => `<li>${q}</li>`).join("")}</ul>` : ""}
        </div>
      `;
      return;
    }
    if (data.status === "ready") {
      const signals = Array.isArray(data.signals) ? data.signals : [];
      const recommendations = Array.isArray(data.recommendations) ? data.recommendations : [];
      const estimatedDuration = data.execution_plan?.estimated_duration || "未知";
      result.innerHTML = `
        <div class="play-ready">
          <h3>${data.play || "工作流程"}</h3>
          <p>预计耗时：${estimatedDuration}</p>
          ${signals.length > 0 ? `
            <div class="play-context-signals">
              <strong>检测到 ${signals.length} 个信号</strong>
            </div>
          ` : ""}
          ${recommendations.length > 0 ? `
            <div class="play-context-recs">
              <strong>有 ${recommendations.length} 个待处理建议</strong>
            </div>
          ` : ""}
          <button class="btn-start-workflow" data-play-id="${data.play_id || ""}">开始执行</button>
        </div>
      `;
      result.querySelector(".btn-start-workflow")?.addEventListener("click", async () => {
        const playId = data.play_id;
        if (!playId) { note("工作流程标识缺失,无法启动。", true); return; }
        const services = { sales_review: "sales-review", government_proposal: "government-proposal", industry_research: "industry-research", presentation: "presentation-studio", resource_coordination: "office-document" };
        const service = services[playId];
        if (!service) { note("此建议暂无可执行的受控服务，请从服务列表选择。", true); return; }
        const button = result.querySelector(".btn-start-workflow");
        button.disabled = true;
        try {
          await runQuickCommand(userInput, service);
          $("play-matcher-panel").hidden = true;
          $("quick-command").value = "";
        } catch (error) { note(error.message, true); }
        finally { button.disabled = false; }
      });
    }
  }
  $("show-bid-form").onclick = () => { bidState.editingId = ""; $("create-bid").textContent = "创建并进入项目"; ["bid-name", "bid-buyer", "bid-number", "bid-deadline", "bid-summary"].forEach((id) => { $(id).value = ""; }); updateBidSelectors(); $("bid-create-panel").hidden = false; $("bid-name").focus(); };
  $("cancel-bid").onclick = () => { bidState.editingId = ""; $("create-bid").textContent = "创建并进入项目"; $("bid-create-panel").hidden = true; };
  $("create-bid").onclick = async () => {
    const name = $("bid-name").value.trim();
    if (!name) { note("请填写投标项目名称。", true); $("bid-name").focus(); return; }
    const button = $("create-bid"); button.disabled = true;
    try {
      const existing = bidState.editingId ? bidProject() : null;
      const endpoint = existing ? `/api/bids/${encodeURIComponent(existing.bid_id)}` : "/api/bids";
      const project = await api(endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        ...(existing ? { expected_version: existing.version } : { workspace_project_id: $("bid-workspace-project").value || selectedProject }),
        name, account_id: $("bid-account").value || null, buyer: $("bid-buyer").value.trim() || null,
        tender_number: $("bid-number").value.trim() || null, deadline_at: $("bid-deadline").value || null,
        summary: $("bid-summary").value.trim() || null,
      }) });
      bidState.selectedId = project.bid_id; ["bid-name", "bid-buyer", "bid-number", "bid-deadline", "bid-summary"].forEach((id) => { $(id).value = ""; }); $("bid-create-panel").hidden = true;
      const wasEditing = Boolean(existing); bidState.editingId = ""; $("create-bid").textContent = "创建并进入项目";
      note(`投标项目“${project.name}”已${wasEditing ? "更新" : "创建"}。`); await loadBids({ force: true }); await selectBid(project.bid_id, { focus: true, tab: wasEditing ? bidState.activeTab : "files" });
      if (!wasEditing && await confirmAction({ title: "现在上传招标文件？", message: "上传原件后，可直接启动文件解读，不需要再次填写项目背景。", confirmText: "选择招标文件" })) { $("bid-file-role").value = "tender"; $("bid-file-input").click(); }
    } catch (error) { note(error.message, true); } finally { button.disabled = false; }
  };
  let bidFilterTimer = null;
  $("bid-query").addEventListener("input", () => { bidState.query = $("bid-query").value.trim(); clearTimeout(bidFilterTimer); bidFilterTimer = setTimeout(() => loadBids({ force: true }), 260); });
  $("bid-status-filter").addEventListener("change", () => { bidState.statuses = $("bid-status-filter").value; loadBids({ force: true }); });
  $("refresh-bids").onclick = () => loadBids({ force: true });
  $("bid-discover").onclick = async () => {
    const scope = await confirmAction({ title: "发现采购机会", message: "填写产品方向、地区或采购人。助手会核验公告正文，并把值得跟进的机会列出；不会自动创建投标项目。", inputLabel: "检索范围", inputValue: "例如：具身智能数据采集，江苏省，近 30 天", confirmText: "开始检索" });
    if (!scope) return;
    if (!publicSearchReady("bid-discovery", true)) { note("请先恢复公开检索服务，再发起采购机会发现。", true); return; }
    try {
      const response = await api("/api/task-requests", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ profile_id: selectedProfile, service_id: "bid-discovery", project_id: selectedProject, request: `【采购机会发现】\n检索范围：${scope}\n请检索并打开采购公告正文，核验采购人、预算、报名与截止时间、资格要求和原始链接；区分事实、推断与待验证项。只输出机会清单和建议，不自动创建投标项目。`, ...taskRuntimeSelection() }) });
      note(`采购机会发现任务已登记（${response.request_id}）。`); await load(); switchView("tasks");
    } catch (error) { note(error.message, true); }
  };
  $("bid-upload").onclick = () => { if (!bidState.selectedId) { note("请先选择投标项目。", true); return; } $("bid-file-input").click(); };
  $("bid-file-input").onchange = async () => {
    const file = $("bid-file-input").files?.[0]; const project = bidProject();
    if (!file || !project) { $("bid-file-input").value = ""; return; }
    if (file.size <= 0 || file.size > 32 * 1024 * 1024) { note("单个投标资料必须为 1 字节至 32 兆字节。", true); $("bid-file-input").value = ""; return; }
    const role = $("bid-file-role").value;
    try {
      const response = await fetch("/api/bid-files", { method: "POST", headers: { "X-Director-Token": requestToken || "", "Content-Type": "application/octet-stream", "X-Bid-Id": project.bid_id, "X-Bid-Role": role, "X-File-Name": encodeURIComponent(file.name) }, body: file });
      const result = await response.json(); if (!response.ok) throw new Error(result.error || "投标资料上传失败");
      note(result.message); await loadBids({ force: true }); await selectBid(project.bid_id, { focus: false, tab: "files" });
      if (["tender", "addendum"].includes(role) && await confirmAction({ title: "资料已登记，立即开始解读？", message: "助手将只读取当前项目已登记的招标原件，提取要求后先展示待写入卡片，仍由你确认。", confirmText: "开始解读" })) await createBidStageTask("bid-interpretation");
    } catch (error) { note(error.message, true); } finally { $("bid-file-input").value = ""; }
  };
  $("open-library-directory").onclick = async () => {
    const button = $("open-library-directory"); button.disabled = true;
    try { const reply = await api("/api/data-directory/open", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); note(reply.message); }
    catch (error) { note(error.message, true); }
    finally { button.disabled = false; }
  };
  $("show-library-url").onclick = () => { $("library-url-panel").hidden = false; $("library-url").focus(); };
  $("cancel-library-url").onclick = () => { $("library-url-panel").hidden = true; };
  $("save-library-url").onclick = async () => {
    const button = $("save-library-url"); button.disabled = true;
    try {
      const response = await api("/api/library/urls", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: $("library-url").value.trim(), title: $("library-url-title").value.trim(), category: $("library-url-category").value,
          region: $("library-url-region").value.trim(), topic: $("library-url-topic").value.trim(), project_id: selectedProject,
          account_id: libraryState.accountId || customerState.selectedId || "",
        }),
      });
      $("library-url").value = ""; $("library-url-title").value = ""; $("library-url-region").value = ""; $("library-url-topic").value = ""; $("library-url-panel").hidden = true;
      libraryState.selectedId = response.entry.library_id; note(response.message); await loadLibrary(true);
    } catch (error) { note(error.message, true); }
    finally { button.disabled = false; }
  };

  $("show-project-form").onclick = () => { $("project-create-panel").hidden = false; $("project-name").focus(); };
  $("cancel-project").onclick = () => { $("project-create-panel").hidden = true; };
  $("create-project").onclick = async () => {
    try {
      const project = await api("/api/projects", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: $("project-name").value.trim(), description: $("project-description").value.trim() }),
      });
      selectedProject = project.project_id;
      $("project-name").value = ""; $("project-description").value = ""; $("project-create-panel").hidden = true;
      note(`项目空间“${project.name}”已创建。`); await load();
    } catch (error) { note(error.message, true); }
  };

  async function uploadProjectFile(file, uploadName = file?.name, projectId = selectedProject) {
    if (!file) return;
    if (file.size <= 0 || file.size > 32 * 1024 * 1024) throw new Error("单个资料必须为 1 字节至 32 兆字节。");
    const response = await fetch("/api/project-files", {
      method: "POST",
      headers: {
        "X-Director-Token": requestToken || "", "Content-Type": "application/octet-stream",
        "X-Project-Id": projectId, "X-File-Name": encodeURIComponent(uploadName),
      },
      body: file,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "资料上传失败");
    return data;
  }

  async function uploadLibraryFile(file, targetId = "") {
    if (!file) return null;
    if (file.size <= 0 || file.size > 32 * 1024 * 1024) throw new Error("单个资料必须为 1 字节至 32 兆字节。");
    const target = targetId ? [...libraryState.entries, ...libraryState.trash].find((item) => item.library_id === targetId) : null;
    const endpoint = targetId ? `/api/library-files/${encodeURIComponent(targetId)}/versions` : "/api/library-files";
    const response = await fetch(endpoint, {
      method: "POST",
      headers: {
        "X-Director-Token": requestToken || "", "Content-Type": "application/octet-stream",
        "X-File-Name": encodeURIComponent(file.name), "X-Project-Id": selectedProject,
        "X-Account-Id": libraryState.accountId || customerState.selectedId || "",
        "X-Library-Category": libraryState.category && !["trash", "inbox"].includes(libraryState.category) ? libraryState.category : "",
        "X-Library-Version": target ? String(target.catalog_version || 0) : "0",
      },
      body: file,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "资料上传失败");
    return data;
  }

  $("home-upload").onclick = () => $("library-file-input").click();
  $("library-upload").onclick = () => $("library-file-input").click();
  [$("project-upload"), $("project-quick-upload")].forEach((button) => { button.onclick = () => $("project-file-input").click(); });
  $("open-data-directory").onclick = async () => {
    const button = $("open-data-directory");
    button.disabled = true;
    try {
      const response = await api("/api/data-directory/open", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      note(response.message);
    } catch (error) { note(error.message, true); }
    finally { button.disabled = false; }
  };
  $("project-file-input").onchange = async () => {
    const file = $("project-file-input").files?.[0];
    try { const reply = await uploadProjectFile(file); note(reply.message); await load(); switchView("projects"); }
    catch (error) { note(error.message, true); }
    finally { $("project-file-input").value = ""; }
  };
  $("library-file-input").onchange = async () => {
    const input = $("library-file-input"); const file = input.files?.[0];
    try {
      const reply = await uploadLibraryFile(file); if (!reply) return;
      libraryState.selectedId = reply.entry.library_id; note(reply.message); await loadLibrary(true); switchView("knowledge");
      if (file.name.toLowerCase().endsWith(".pdf") && await confirmAction({ title: "资料已上传，立即提取证据？", message: "助手会按页读取当前 PDF，先展示可编辑的待写入内容，仍由你确认后才进入正式资料记录。", confirmText: "开始提取" })) {
        selectedService = "pdf-import";
        const task = await createTask(`【资料库 PDF 解读】\n文件：${reply.entry.path}\n资料编号：${reply.entry.library_id}\n请按页提取可引用证据，区分事实、分析和待验证内容，准备写入资料库。`);
        note(`解读任务已登记（${task.request_id}）。`); await load(); switchView("tasks");
      }
    } catch (error) { note(error.message, true); }
    finally { input.value = ""; }
  };
  $("library-version-input").onchange = async () => {
    const input = $("library-version-input"); const file = input.files?.[0]; const targetId = libraryState.versionTargetId;
    try { const reply = await uploadLibraryFile(file, targetId); if (!reply) return; libraryState.selectedId = targetId; note(reply.message); await loadLibrary(true); }
    catch (error) { note(error.message, true); }
    finally { input.value = ""; libraryState.versionTargetId = ""; }
  };

  function selectedReimbursementMessages() {
    return [...$("expense-mail-results").querySelectorAll("input[data-mail-index]:checked")]
      .map((checkbox) => reimbursementMailMessages[Number(checkbox.dataset.mailIndex)])
      .filter(Boolean)
      .map((message) => ({ uid: message.uid, message_key: message.message_key }));
  }

  function updateReimbursementSelection() {
    const selected = selectedReimbursementMessages();
    $("import-expense-mail").disabled = selected.length === 0 || selected.length > 20;
    $("import-expense-mail").textContent = selected.length ? `导入所选材料（${selected.length} 封）` : "导入所选材料";
    const checkboxes = [...$("expense-mail-results").querySelectorAll("input[data-mail-index]")];
    $("expense-mail-select-all").checked = checkboxes.length > 0 && checkboxes.every((checkbox) => checkbox.checked);
    $("expense-mail-select-all").indeterminate = selected.length > 0 && selected.length < checkboxes.length;
  }

  function renderReimbursementMailResults(messages) {
    reimbursementMailMessages = Array.isArray(messages) ? messages : [];
    const box = $("expense-mail-results");
    box.classList.toggle("empty", reimbursementMailMessages.length === 0);
    if (!reimbursementMailMessages.length) {
      box.replaceChildren();
      box.textContent = "没有找到含可导入附件的邮件。可以调整日期或关键词后重试。";
      updateReimbursementSelection();
      return;
    }
    box.replaceChildren(...reimbursementMailMessages.map((message, index) => {
      const card = document.createElement("article"); card.className = "mail-result-card";
      const heading = document.createElement("label"); heading.className = "mail-result-heading";
      const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.dataset.mailIndex = String(index);
      checkbox.addEventListener("change", updateReimbursementSelection);
      const copy = document.createElement("span");
      const subject = document.createElement("strong"); subject.textContent = message.subject || "无主题邮件";
      const meta = document.createElement("small");
      const parsedDate = message.received_at ? new Date(message.received_at) : null;
      const received = parsedDate && !Number.isNaN(parsedDate.getTime())
        ? parsedDate.toLocaleString("zh-CN", { hour12: false }) : "时间未知";
      meta.textContent = `${message.sender || "未知发件人"} · ${received}`;
      copy.append(subject, meta); heading.append(checkbox, copy);
      const attachments = document.createElement("div"); attachments.className = "mail-attachment-chips";
      (message.attachments || []).forEach((attachment) => {
        const chip = document.createElement("span"); chip.textContent = `${attachment.name} · ${fileSize(attachment.size || 0)}`; attachments.append(chip);
      });
      card.append(heading, attachments); return card;
    }));
    updateReimbursementSelection();
  }

  $("open-mail-settings").onclick = () => { switchView("settings"); $("mail-settings-panel").open = true; $("mail-email-address").focus(); };
  $("mail-provider").onchange = () => {
    if ($("mail-provider").value === "custom") $("mail-host").value = "";
    applyMailProviderPreset(true);
  };
  $("mail-email-address").addEventListener("input", () => {
    if (!$("mail-username").value.trim() || $("mail-username").dataset.auto === "true") {
      $("mail-username").value = $("mail-email-address").value.trim();
      $("mail-username").dataset.auto = "true";
    }
  });
  $("mail-username").addEventListener("input", () => { $("mail-username").dataset.auto = "false"; });
  $("save-mail-settings").onclick = async () => {
    const button = $("save-mail-settings"); button.disabled = true;
    $("mail-settings-status").textContent = "正在进行只读登录验证…";
    try {
      const response = await api("/api/mail-settings", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider: $("mail-provider").value, email_address: $("mail-email-address").value.trim(),
          username: $("mail-username").value.trim() || $("mail-email-address").value.trim(),
          host: $("mail-host").value.trim(), credential: $("mail-credential").value,
          allow_private_network: $("mail-private-network").checked,
        }),
      });
      model.mail = response; renderMailSettings(true); $("mail-settings-status").textContent = response.message; note(response.message);
    } catch (error) { $("mail-settings-status").textContent = error.message; }
    finally { button.disabled = false; }
  };
  $("reset-mail-settings").onclick = async () => {
    if (!await confirmAction({ title: "移除邮箱连接？", message: "本机保存的邮箱授权码会被清除，已导入项目的报销材料不会删除。", confirmText: "移除连接", tone: "danger" })) return;
    try {
      const response = await api("/api/mail-settings/reset", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      model.mail = response; reimbursementMailMessages = []; renderReimbursementMailResults([]); renderMailSettings(true); note(response.message);
    } catch (error) { note(error.message, true); }
  };
  $("search-expense-mail").onclick = async () => {
    const button = $("search-expense-mail"); button.disabled = true; $("expense-mail-status").textContent = "正在只读搜索收件箱…";
    try {
      const response = await api("/api/reimbursements/mail/search", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ date_from: $("expense-mail-from").value, date_to: $("expense-mail-to").value, query: $("expense-mail-query").value.trim() }),
      });
      renderReimbursementMailResults(response.messages);
      $("expense-mail-status").textContent = `找到 ${response.messages.length} 封含报销附件的邮件${response.skipped_large ? `，另跳过 ${response.skipped_large} 封超大邮件` : ""}。`;
    } catch (error) { $("expense-mail-status").textContent = error.message; note(error.message, true); }
    finally { button.disabled = false; }
  };
  $("expense-mail-select-all").onchange = () => {
    const checked = $("expense-mail-select-all").checked;
    [...$("expense-mail-results").querySelectorAll("input[data-mail-index]")].forEach((checkbox, index) => { checkbox.checked = checked && index < 20; });
    updateReimbursementSelection();
  };
  $("import-expense-mail").onclick = async () => {
    const selected = selectedReimbursementMessages();
    if (!selected.length || selected.length > 20) { note("请选择 1–20 封报销邮件。", true); return; }
    const button = $("import-expense-mail"); button.disabled = true; button.textContent = "正在导入附件…";
    try {
      const response = await api("/api/reimbursements/mail/import", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ selected }),
      });
      note(response.message); reimbursementMailMessages = []; renderReimbursementMailResults([]); await load(); switchView("tools");
    } catch (error) { note(error.message, true); }
    finally { button.disabled = false; updateReimbursementSelection(); }
  };
  $("migrate-legacy-expenses").onclick = async () => {
    const count = model.reimbursements?.legacy_count || 0;
    if (!count || !await confirmAction({
      title: "迁移旧报销材料？",
      message: `将复制并校验项目空间中的 ${count} 个旧报销附件，放入独立材料库。项目中的原件会继续保留。`,
      confirmText: "开始迁移",
    })) return;
    const button = $("migrate-legacy-expenses"); button.disabled = true;
    try {
      const response = await api("/api/reimbursements/migrate-legacy", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      note(response.message); await load();
    } catch (error) { note(error.message, true); }
    finally { button.disabled = false; }
  };

  const schedulePresets = {
    morning: { name: "上午重点客户扫描", time: "09:00", service: "sales-review", request: "检查重点客户的下一步动作、到期事项、停滞风险和资源缺口，形成今日优先级清单。" },
    resource: { name: "下午资源需求汇总", time: "16:30", service: "sales-review", request: "汇总当天新增或未解决的销售资源需求，按客户价值和紧迫程度排序，并给出协调建议。" },
    review: { name: "下班前销售复盘", time: "18:00", service: "sales-review", request: "复盘当天客户推进、承诺事项和风险，列出次日必须完成的动作及责任人。" },
  };
  $("show-schedule-form").onclick = () => { $("schedule-create-panel").hidden = false; $("schedule-name").focus(); };
  $("cancel-schedule").onclick = () => { $("schedule-create-panel").hidden = true; };
  document.querySelectorAll("[data-schedule-preset]").forEach((button) => button.addEventListener("click", () => {
    const preset = schedulePresets[button.dataset.schedulePreset];
    $("schedule-create-panel").hidden = false; $("schedule-name").value = preset.name; $("schedule-time").value = preset.time;
    $("schedule-service").value = preset.service; $("schedule-request").value = preset.request;
  }));
  $("create-schedule").onclick = async () => {
    try {
      const schedule = await api("/api/schedules", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: $("schedule-name").value.trim(), time_local: $("schedule-time").value,
          project_id: $("schedule-project").value, service_id: $("schedule-service").value,
          request: $("schedule-request").value.trim(), ...taskRuntimeSelection(),
        }),
      });
      $("schedule-name").value = ""; $("schedule-request").value = ""; $("schedule-create-panel").hidden = true;
      note(`每日任务“${schedule.name}”已保存。`); await load();
    } catch (error) { note(error.message, true); }
  };

  function renderSearchResults(results, truncated = false) {
    const box = $("search-results");
    box.classList.toggle("empty", results.length === 0);
    if (!results.length) { box.replaceChildren(); box.textContent = "没有找到匹配内容，可调整关键词或发起公开调研。"; return; }
    box.replaceChildren(...results.map((item) => {
      const card = document.createElement("article"); card.className = "search-result";
      const header = document.createElement("header");
      const title = document.createElement("strong"); title.textContent = item.title;
      const kind = document.createElement("span"); kind.textContent = item.kind;
      const meta = document.createElement("small"); meta.textContent = item.subtitle || item.reference;
      const snippet = document.createElement("p"); snippet.textContent = item.snippet || item.reference;
      header.append(title, kind); card.append(header, meta, snippet);
      card.onclick = async () => {
        if (item.project_id && projectById(item.project_id)?.status === "active") selectedProject = item.project_id;
        if (item.kind === "任务") switchView("tasks");
        else if (["项目", "项目文件"].includes(item.kind)) switchView("projects");
        else if (item.kind === "资料") {
          if (!libraryState.version) await loadLibrary(true);
          const entry = libraryState.entries.find((candidate) => [candidate.path, candidate.url, candidate.source_id, candidate.library_id].filter(Boolean).includes(item.reference));
          libraryState.selectedId = entry?.library_id || "";
          libraryState.category = ""; libraryState.query = ""; libraryState.status = ""; libraryState.specialFilter = "";
          libraryState.renderedKey = ""; libraryState.previewKey = "";
          $("library-query").value = ""; $("library-status-filter").value = "";
          switchView("knowledge"); renderLibrary();
        }
        else if (item.kind === "产物") switchView("outputs");
        else switchView("sales");
        renderProjectSelectors(); renderProjects();
      };
      return card;
    }));
    if (truncated) note("结果较多，目前显示前 60 条。", false);
  }

  async function runLocalSearch() {
    const query = $("search-query").value.trim();
    const scopes = [...document.querySelectorAll(".scope-row input:checked")].map((input) => input.value);
    try { const response = await api("/api/search", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ query, scopes }) }); renderSearchResults(response.results, response.truncated); }
    catch (error) { note(error.message, true); }
  }
  $("run-search").onclick = runLocalSearch;
  $("search-query").onkeydown = (event) => { if (event.key === "Enter") runLocalSearch(); };
  $("start-public-search").onclick = async () => {
    const query = $("search-query").value.trim();
    if (query.length < 2) { note("请先输入至少 2 个字的公开调研主题。", true); return; }
    if (!publicSearchReady("industry-research", true)) {
      note(model?.search?.restart_required ? "请重启应用，让智能核心加载检索密钥。" : "请先配置公开检索服务。", true);
      return;
    }
    openService("industry-research");
    guidedDrafts["industry-research"] = { topic: query, purpose: "支持客户沟通与机会判断", period: "近 12 个月，并补充关键历史背景" };
    renderGuidedForm(true);
    note("已带入公开调研主题；确认用途后即可开始。 ");
  };

  async function runQuickCommand(suppliedRequest, forcedService) {
    const request = String(suppliedRequest || $("quick-command").value).trim();
    if (!request) { note("请先写下希望助手完成的工作。", true); return; }
    if (!forcedService && /周报|周五|周总结/u.test(request)) { $("weekly-focus").value = request.slice(0, 120); openService("weekly-deck"); return; }
    if (forcedService === "presentation-studio" || (!forcedService && /PPT|演示|汇报材料/u.test(request))) { $("ppt-topic").value = request.slice(0, 240); openService("presentation-studio"); return; }
    const serviceId = forcedService || (/政府|园区|政策合作/u.test(request) ? "government-proposal" : /研究|行业|竞品|公开资料|调研/u.test(request) ? "industry-research" : /文件|方案|纪要|邮件/u.test(request) ? "office-document" : "sales-review");
    $("task-readonly").checked = /只分析|仅分析|只读|不(?:要|需要)?(?:自动)?(?:更新|写入|入库|保存)/u.test(request) || !/更新台账|写入|入库|保存到/u.test(request);
    selectedService = serviceId;
    try { const response = await createTask(`【工作台快速指令】\n${request}\n请根据当前项目空间、资料库和销售台账补齐必要背景；涉及写入或正式文件时先等待审批。`); $("quick-command").value = ""; note(`任务已登记（${response.request_id}）。`); await load(); switchView("tasks"); }
    catch (error) { note(error.message, true); }
  }

  const presentationPresets = {
    customer: { audience: "客户业务负责人、技术负责人和决策人", decision: "确认方案范围、验证计划和下一步商务安排", scene: "custom", style: "management-report" },
    review: { audience: "销售管理层", decision: "确认重点客户优先级、资源配置和下一步行动", scene: "weekly", style: "management-report" },
    government: { audience: "地方政府相关部门与项目决策人", decision: "确认合作方向、试点范围和推进机制", scene: "government", style: "government-program" },
  };
  document.querySelectorAll("[data-ppt-preset]").forEach((button) => {
    button.addEventListener("click", () => {
      const preset = presentationPresets[button.dataset.pptPreset];
      $("ppt-audience").value = preset.audience; $("ppt-decision").value = preset.decision;
      $("ppt-scene").value = preset.scene; $("ppt-style").value = preset.style;
      updatePresentationSourcePolicy();
      $("ppt-topic").focus();
    });
  });

  function modelPayload() {
    return {
      provider_id: $("model-instance").value || null,
      name: $("model-provider-name").value.trim(), vendor: $("model-vendor").value,
      api: $("model-api").value,
      base_url: $("model-base-url").value.trim(),
      api_key: $("model-api-key").value.trim(),
      allow_private_network: $("model-private-network").checked,
    };
  }

  ["ppt-mode", "ppt-confidentiality", "ppt-scene"].forEach((id) => $(id).addEventListener("change", updatePresentationSourcePolicy));

  $("model-instance").onchange = () => fillProviderEditor($("model-instance").value);
  $("add-model-provider").onclick = () => { fillProviderEditor(""); $("model-provider-name").focus(); };
  $("model-select").onchange = () => { saveEditedCapabilities(); showEditedCapabilities(); };
  $("model-enabled-ids").oninput = () => { saveEditedCapabilities(); refreshEditorModelOptions(editingModelId); };
  $("enable-model").onclick = () => {
    const id = $("model-select").value;
    if (!id) return;
    $("model-enabled-ids").value = [...new Set([...enabledModelIds(), id])].join("\n");
    saveEditedCapabilities(); refreshEditorModelOptions(id);
  };
  $("model-vendor").onchange = () => {
    if ($("model-instance").value) return;
    const vendor = $("model-vendor").value;
    $("model-endpoint-options").open = !["openai", "anthropic"].includes(vendor);
    if (vendor === "openai") {
      $("model-provider-name").value = "OpenAI"; $("model-base-url").value = "https://api.openai.com"; $("model-api").value = "openai-responses";
    } else if (vendor === "anthropic") {
      $("model-provider-name").value = "Anthropic"; $("model-base-url").value = "https://api.anthropic.com"; $("model-api").value = "anthropic-messages";
    } else {
      $("model-provider-name").value = vendor === "newapi" ? "NewAPI" : "自定义 API"; $("model-base-url").value = ""; $("model-api").value = "openai-completions";
    }
  };

  $("discover-models").onclick = async () => {
    const button = $("discover-models");
    button.disabled = true;
    $("model-discovery-status").textContent = "正在连接网关并读取模型…";
    try {
      const response = await api("/api/model-discovery", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(modelPayload()),
      });
      $("model-base-url").value = response.base_url;
      saveEditedCapabilities();
      discoveredModelOptions = response.models;
      refreshEditorModelOptions(editingModelId);
      $("model-discovery-status").textContent = response.message;
    } catch (error) {
      $("model-discovery-status").textContent = error.message + "；保留已配置目录，也可手动填写模型 ID。";
    } finally { button.disabled = false; }
  };

  $("save-model-settings").onclick = async () => {
    const selectedModel = $("model-select").value;
    if (!selectedModel) { $("model-discovery-status").textContent = "请先填写模型 ID，或发现后选择模型。"; return; }
    saveEditedCapabilities();
    const enabled = enabledModelIds();
    if (!enabled.includes(selectedModel)) { $("model-discovery-status").textContent = "请将当前模型加入已启用列表。"; return; }
    const button = $("save-model-settings");
    button.disabled = true;
    $("model-discovery-status").textContent = "正在验证并保存模型配置…";
    try {
      const response = await api("/api/model-settings", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...modelPayload(), selected_model: selectedModel,
          models: enabled.map((id) => ({ ...(editingModels.get(id) || {}), id, enabled: true })),
          discovered_models: [...new Map(discoveredModelOptions.map((item) => [item.id, { id: item.id, ...(item.owned_by ? { owned_by: item.owned_by } : {}) }])).values()].slice(0, 500),
          enabled: $("model-provider-enabled").checked, make_default: $("model-make-default").checked,
          role_models: Object.fromEntries([["director-research-scout", $("model-role-scout").value], ["director-readonly-reviewer", $("model-role-reviewer").value]].filter(([, value]) => value)),
        }),
      });
      model.model = response;
      renderModelSettings(true);
      renderProviderSettings(true);
      renderTaskRuntimeOptions();
      $("model-settings-panel").open = true;
      $("model-discovery-status").textContent = response.message;
      note("模型已保存；关闭并重新打开销售总监智能工作台后，后续任务将使用新模型。");
    } catch (error) {
      $("model-discovery-status").textContent = error.message;
      button.disabled = false;
    }
  };

  $("save-provider-settings").onclick = () => saveProviderSettings();
  $("refresh-codex-models").onclick = () => loadCodexModels(true);
  $("codex-cli-model").onchange = () => { renderCodexThinking(); selectProviderType(selectedProviderType()); };
  $("codex-cli-thinking").onchange = () => selectProviderType(selectedProviderType());

  $("detect-coding-assistants").onclick = async () => {
    const button = $("detect-coding-assistants");
    button.disabled = true;
    button.textContent = "检测中…";
    try {
      await detectCodingAssistants();
      button.textContent = "重新检测编码助手";
      note("CLI 安装检测完成；检测结果不代表已经登录。 ");
    } catch (error) {
      note(error.message, true);
      button.textContent = "重新检测编码助手";
    } finally {
      button.disabled = false;
    }
  };

  document.querySelectorAll('input[name="provider-type"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      selectProviderType(radio.value);
    });
  });

  $("reset-cli-provider").onclick = async () => {
    const type = selectedProviderType();
    const provider = CLI_PROVIDER_TYPES.has(type) ? cliProvider(type) : null;
    if (!provider) return;
    if (!await confirmAction({
      title: "移除此 CLI 供应商？",
      message: `将删除“${provider.name || type}”的本机工作台配置。CLI 自身的登录状态和文件不会改变，其他 API 与 CLI 供应商保持不变。`,
      confirmText: "移除此供应商", tone: "danger",
    })) return;
    const button = $("reset-cli-provider");
    button.disabled = true;
    try {
      const response = await api("/api/model-settings/reset", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider_id: provider.id }),
      });
      model.model = response.model || response;
      providerSettingsInitialized = false;
      renderModelSettings(true); renderProviderSettings(true); renderTaskRuntimeOptions();
      note("CLI 供应商已从工作台移除；其他配置未改变，重启应用或智能核心后生效。");
    } catch (error) {
      $(type + "-config-status").textContent = error.message;
    } finally { button.disabled = false; selectProviderType(selectedProviderType()); }
  };

  $("reset-model-settings").onclick = async () => {
    const providerId = $("model-instance").value;
    if (!providerId) return;
    if (!await confirmAction({
      title: "移除此供应商？",
      message: `将删除“${$("model-provider-name").value}”的本机配置和凭据，其他供应商不变。旧任务不会自动切换到其他模型。重启后生效。`,
      confirmText: "移除此供应商",
      tone: "danger",
    })) return;
    const button = $("reset-model-settings");
    button.disabled = true;
    try {
      const response = await api("/api/model-settings/reset", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider_id: providerId }),
      });
      model.model = response;
      renderModelSettings(true);
      renderProviderSettings(true); renderTaskRuntimeOptions();
      $("model-settings-panel").open = true;
      $("model-discovery-status").textContent = response.message;
      note("所选供应商配置和凭据已移除；请确认默认模型并重启应用。");
    } catch (error) {
      $("model-discovery-status").textContent = error.message;
    } finally { button.disabled = false; }
  };

  $("save-search-settings").onclick = async () => {
    const button = $("save-search-settings");
    button.disabled = true;
    $("search-settings-status").textContent = "正在连接公开检索服务并验证密钥…";
    try {
      const response = await api("/api/search-settings", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: $("search-api-key").value.trim() }),
      });
      model.search = response;
      renderSearchSettings(true);
      $("search-settings-panel").open = true;
      $("search-settings-status").textContent = response.message;
      note("专用检索密钥已安全保存；关闭并重新打开销售总监智能工作台后生效。");
    } catch (error) {
      $("search-settings-status").textContent = error.message;
    } finally { button.disabled = false; }
  };

  document.querySelector(".external-link").onclick = async (event) => {
    event.preventDefault();
    try {
      const response = await api("/api/search-settings/open-dashboard", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      note(response.message);
    } catch (error) { note(error.message, true); }
  };

  $("reset-search-settings").onclick = async () => {
    if (!await confirmAction({
      title: "删除专用检索密钥？",
      message: "密钥会从本机删除，关闭并重新打开应用后将自动切换到免密公共检索。",
      confirmText: "删除密钥",
      tone: "danger",
    })) return;
    const button = $("reset-search-settings");
    button.disabled = true;
    try {
      const response = await api("/api/search-settings/reset", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      model.search = response;
      renderSearchSettings(true);
      $("search-settings-panel").open = true;
      $("search-settings-status").textContent = response.message;
      note(response.message);
    } catch (error) { $("search-settings-status").textContent = error.message; }
    finally { button.disabled = false; }
  };

  $("save-search-gateway").onclick = async () => {
    const button = $("save-search-gateway");
    const maxResults = Number($("search-gateway-max-results").value);
    if (!Number.isInteger(maxResults) || maxResults < 1 || maxResults > 10) {
      $("search-gateway-status").textContent = "每次查询结果数必须是 1–10 的整数。";
      return;
    }
    const selectedProviders = selectedSearchGatewayProviders();
    if (!$("search-gateway-provider-auto")?.checked && selectedProviders.length === 0) {
      $("search-gateway-status").textContent = "请至少选择一个搜索来源，或启用自动使用全部来源。";
      return;
    }
    if ($("search-gateway-mode").value === "single" && selectedProviders.length !== 1) {
      $("search-gateway-status").textContent = "单一来源模式必须先选择一个可用搜索来源。";
      return;
    }
    button.disabled = true;
    $("search-gateway-status").textContent = "正在连接 One Search、验证令牌并读取提供商…";
    try {
      const response = await api("/api/search-gateway", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          base_url: $("search-gateway-url").value.trim(),
          token: $("search-gateway-token").value.trim(),
          mode: $("search-gateway-mode").value,
          max_results: maxResults,
          allow_private_network: $("search-gateway-private-network").checked,
          selected_providers: selectedProviders,
        }),
      });
      model.search_gateway = response;
      renderSearchGatewaySettings(true);
      $("search-gateway-panel").open = true;
      $("search-gateway-status").textContent = response.message;
      note("搜索聚合网关已保存；关闭并重新打开工作台后生效。");
    } catch (error) {
      $("search-gateway-status").textContent = error.message;
    } finally { button.disabled = false; }
  };

  $("reset-search-gateway").onclick = async () => {
    if (!await confirmAction({
      title: "停用搜索聚合网关？",
      message: "已保存的聚合网关令牌会从本机删除，关闭并重新打开应用后恢复使用原有公开检索。",
      confirmText: "停用网关",
      tone: "danger",
    })) return;
    const button = $("reset-search-gateway");
    button.disabled = true;
    try {
      const response = await api("/api/search-gateway/reset", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      model.search_gateway = response;
      renderSearchGatewaySettings(true);
      $("search-gateway-panel").open = true;
      $("search-gateway-status").textContent = response.message;
      note(response.message);
    } catch (error) { $("search-gateway-status").textContent = error.message; }
    finally { button.disabled = false; }
  };

  $("open-search-gateway").onclick = async () => {
    try {
      const response = await api("/api/search-gateway/open-dashboard", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      note(response.message);
    } catch (error) { $("search-gateway-status").textContent = error.message; }
  };

  $("save-runtime-settings").onclick = async () => {
    const button = $("save-runtime-settings");
    button.disabled = true;
    try {
      const response = await api("/api/desktop-settings", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ show_ai_core_window: $("show-ai-core-window").checked }),
      });
      model.desktop_runtime = { ...(model.desktop_runtime || {}), ...response };
      renderRuntimeSettings(true);
      note(response.message);
    } catch (error) { note(error.message, true); }
    finally { button.disabled = false; }
  };

  $("create-presentation").onclick = async () => {
    try {
      const brief = presentationBrief();
      const response = await createTask(`[PRESENTATION_BRIEF]\n${JSON.stringify(brief, null, 2)}\n[/PRESENTATION_BRIEF]`);
      $("ppt-topic").value = "";
      note(`演示文稿任务已登记（${response.request_id}），将先进入需求与证据阶段。`);
    } catch (error) { note(error.message, true); }
  };

  $("create-weekly").onclick = async () => {
    try {
      const response = await createTask(`[PRESENTATION_BRIEF]\n${JSON.stringify(weeklyBrief(), null, 2)}\n[/PRESENTATION_BRIEF]`);
      $("weekly-focus").value = "";
      note(`销售行动简报已登记（${response.request_id}），助手将冻结本周证据并生成个人简报与总监汇总。`);
    } catch (error) { note(error.message, true); }
  };
  $("refresh-weekly-preview").onclick = async () => {
    const button = $("refresh-weekly-preview"); button.disabled = true;
    try { await loadWeeklyBriefing({ force: true }); }
    finally { button.disabled = false; }
  };

  document.querySelectorAll("[data-tool]").forEach((button) => {
    button.addEventListener("click", () => {
      activeTool = button.dataset.tool || "wechat";
      renderToolPanels();
      if (activeTool === "wechat" && model?.wechat?.configured && !wechatState.loaded) loadWechatConversations();
    });
  });
  $("choose-wechat-export").onclick = () => {
    if (!$("wechat-ownership").checked) { note("请先确认这是本人账号且有权处理的聊天内容。", true); return; }
    $("wechat-file-input").click();
  };
  $("wxdecipher-add-files").onclick = () => $("wxdecipher-file-input").click();
  $("wxdecipher-file-input").onchange = () => {
    if (wxdecipherState.busy) return;
    const incoming = [...($("wxdecipher-file-input").files || [])];
    $("wxdecipher-file-input").value = "";
    if (!incoming.length) return;
    const candidate = [...wxdecipherState.files];
    for (const file of incoming) {
      if (!/\.(db|sqlite|sqlite3)(?:-wal)?$/iu.test(file.name) || file.size < (/-wal$/iu.test(file.name) ? 0 : 512) || file.size > 256 * 1024 * 1024) {
        wxDecipherStatus("只接受 .db / .sqlite / .sqlite3 主库及同名 -wal 副本；主库至少 512 字节，单个文件不超过 256 兆字节。", true); return;
      }
      if (candidate.some((entry) => entry.file.name.toLowerCase() === file.name.toLowerCase())) {
        wxDecipherStatus(`已选择同名文件 ${file.name}；本批次不能重复或混合多个账号。`, true); return;
      }
      candidate.push({ file, key: "" });
    }
    if (candidate.length > 32 || candidate.filter((entry) => !/-wal$/iu.test(entry.file.name)).length > 16 || candidate.reduce((sum, entry) => sum + entry.file.size, 0) > 1024 * 1024 * 1024) {
      wxDecipherStatus("每次最多 16 个主库及其 16 个 WAL，合计不超过 1 吉字节。", true); return;
    }
    wxdecipherState.files = candidate; renderWxDecipherFiles();
    wxDecipherStatus("文件已选择，尚未上传或解密。可继续添加联系人/会话库，再提供密钥并确认副本。");
  };
  $("wxdecipher-clear-files").onclick = () => {
    wxdecipherState.files.forEach((entry) => { entry.key = ""; });
    wxdecipherState.files = []; $("wxdecipher-key").value = "";
    renderWxDecipherFiles(); wxDecipherStatus("已清空选择，未改动原始文件。");
  };
  $("wxdecipher-run").onclick = runWxDecipher;
  $("wxdecipher-process-refresh").onclick = refreshWxProcesses;
  $("wxdecipher-capture-confirm").addEventListener("change", () => { if (!$("wxdecipher-capture-confirm").checked) $("wxdecipher-process").value = ""; renderWxDecipherControls(); });
  $("wxdecipher-media-choose").onclick = () => {
    if (!$("wechat-ownership").checked) { $("wxdecipher-media-status").textContent = "请先勾选本人账号授权。"; return; }
    $("wxdecipher-media-input").click();
  };
  $("wxdecipher-media-input").onchange = () => {
    const files = [...($("wxdecipher-media-input").files || [])]; $("wxdecipher-media-input").value = "";
    if (files.length) restoreWxMedia(files);
  };
  $("wxdecipher-media-clear").onclick = () => { if (!wxmediaState.busy) { clearWxMedia(); $("wxdecipher-image-key").value = ""; $("wxdecipher-media-status").textContent = "已清除本页媒体预览和下载链接；已下载的副本不受影响。"; } };
  $("wxdecipher-snapshot").addEventListener("change", renderWxDecipherControls);
  $("wechat-ownership").addEventListener("change", renderWxDecipherControls);
  $("wechat-export-selected").onclick = exportSelectedWechat;
  $("wechat-file-input").onchange = async () => {
    const file = $("wechat-file-input").files?.[0];
    if (!file) return;
    if (!/\.(json|jsonl|csv)$/iu.test(file.name)) { note("请选择 JSON、JSONL 或 CSV 结构化导出文件。", true); $("wechat-file-input").value = ""; return; }
    if (file.size <= 0 || file.size > 64 * 1024 * 1024) { note("微信导出文件必须为 1 字节至 64 兆字节。", true); $("wechat-file-input").value = ""; return; }
    const button = $("choose-wechat-export"); button.disabled = true; button.textContent = "正在本机导入…";
    try {
      const response = await fetch("/api/wechat/import", {
        method: "POST",
        headers: {
          "X-Director-Token": requestToken || "",
          "Content-Type": "application/octet-stream",
          "X-File-Name": encodeURIComponent(file.name),
          "X-Wechat-Ownership": "confirmed",
          "X-Wechat-Auto-Cleanup": "true",
          "X-Wechat-Retention-Days": "7",
          "X-Wechat-Account": encodeURIComponent($("wechat-account").value.trim() || "本机微信"),
          "X-Wechat-Self-Id": encodeURIComponent($("wxdecipher-self-id").value.trim()),
        },
        body: file,
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "微信会话导入失败");
      note(result.message);
      wechatState.loaded = false; wechatState.revision = ""; wechatState.selected.clear(); wechatState.selectedId = ""; wechatState.messages = [];
      await load();
    } catch (error) { note(error.message, true); }
    finally { button.disabled = false; button.textContent = "选择导出文件"; $("wechat-file-input").value = ""; }
  };
  let wechatFilterTimer = null;
  const refreshWechatFilters = () => {
    clearTimeout(wechatFilterTimer);
    wechatFilterTimer = setTimeout(() => { wechatState.loaded = false; loadWechatConversations({ force: true }); }, 280);
  };
  $("wechat-query").addEventListener("input", refreshWechatFilters);
  $("wechat-chat-type").addEventListener("change", refreshWechatFilters);
  $("wechat-date-from").addEventListener("change", refreshWechatFilters);
  $("wechat-date-to").addEventListener("change", refreshWechatFilters);
  $("refresh-wechat").onclick = () => loadWechatConversations({ force: true });
  $("wechat-select-all").onchange = () => {
    wechatState.rows.forEach((row) => {
      if ($("wechat-select-all").checked) wechatState.selected.add(row.conversation_id);
      else wechatState.selected.delete(row.conversation_id);
    });
    renderWechatConversations(); renderWechatSummary();
  };
  $("review-selected-wechat").onclick = async () => {
    wechatState.creating = true; renderWechatSummary();
    try { await createWechatReview(); } catch (error) { note(error.message, true); }
    finally { wechatState.creating = false; renderWechatSummary(); }
  };
  $("review-today-wechat").onclick = async () => {
    wechatState.creating = true; renderWechatSummary();
    try { await createWechatReview({ today: true }); } catch (error) { note(error.message, true); }
    finally { wechatState.creating = false; renderWechatSummary(); }
  };
  $("cleanup-wechat").onclick = async () => {
    const confirmed = await confirmAction({ title: "清理已到期微信原文", message: "只会删除已经超过 7 天保留期的应用副本和索引原文，不会触碰微信原始数据，也不会删除已生成报告。", confirmText: "清理到期内容" });
    if (!confirmed) return;
    try {
      const result = await api("/api/wechat/cleanup", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      note(`${result.message} 本次清理 ${result.purged_messages || 0} 条到期消息。`);
      wechatState.loaded = false; wechatState.revision = ""; await load();
    } catch (error) { note(error.message, true); }
  };

  const wechatToday = new Date();
  const wechatStart = new Date(); wechatStart.setDate(wechatToday.getDate() - 6);
  $("wechat-date-from").value = formatDate(wechatStart);
  $("wechat-date-to").value = formatDate(wechatToday);
  renderToolPanels();

  const reimbursementToday = new Date();
  const reimbursementStart = new Date(); reimbursementStart.setDate(reimbursementToday.getDate() - 30);
  $("expense-mail-from").value = formatDate(reimbursementStart);
  $("expense-mail-to").value = formatDate(reimbursementToday);

  async function loadAssistantStatus() {
    try {
      const response = await fetch("/api/coding-agent/doctor");
      const data = await response.json();
      const badge = $("assistant-status");
      if (data.integration_ready && data.workbench.ready) {
        badge.textContent = "集成就绪";
        badge.className = "period-badge success";
      } else {
        badge.textContent = "集成异常";
        badge.className = "period-badge error";
      }
    } catch (error) {
      $("assistant-status").textContent = "连接失败";
      $("assistant-status").className = "period-badge error";
    }
  }

  function addAssistantMessage(role, content, actions) {
    const conversation = $("assistant-conversation");
    const welcome = conversation.querySelector(".assistant-welcome");
    if (welcome) welcome.remove();
    const message = document.createElement("div");
    message.className = `assistant-message ${role}`;
    const avatar = document.createElement("div");
    avatar.className = "assistant-message-avatar";
    avatar.textContent = role === "user" ? "你" : "AI";
    const messageContent = document.createElement("div");
    messageContent.className = "assistant-message-content";
    messageContent.innerHTML = content.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>").replace(/`(.*?)`/g, "<code>$1</code>").replace(/\n/g, "<br>");
    if (actions && actions.length > 0) {
      const actionsDiv = document.createElement("div");
      actionsDiv.className = "assistant-message-actions";
      actions.forEach(action => {
        const btn = document.createElement("button");
        btn.className = action.primary ? "primary small" : "secondary small";
        btn.textContent = action.label;
        btn.onclick = () => handleAssistantAction(action);
        actionsDiv.appendChild(btn);
      });
      messageContent.appendChild(actionsDiv);
    }
    message.appendChild(avatar);
    message.appendChild(messageContent);
    conversation.appendChild(message);
    conversation.scrollTop = conversation.scrollHeight;
    assistantState.messages.push({ role, content, actions, timestamp: Date.now() });
  }

  function showAssistantTyping() {
    const conversation = $("assistant-conversation");
    const typing = document.createElement("div");
    typing.id = "assistant-typing-indicator";
    typing.className = "assistant-message assistant";
    typing.innerHTML = `<div class="assistant-message-avatar">AI</div><div class="assistant-message-content"><div class="assistant-typing"><span></span><span></span><span></span></div></div>`;
    conversation.appendChild(typing);
    conversation.scrollTop = conversation.scrollHeight;
  }

  function hideAssistantTyping() {
    const typing = $("assistant-typing-indicator");
    if (typing) typing.remove();
  }

  async function handleAssistantAction(action) {
    switch (action.type) {
      case "create_task":
        showAssistantTyping();
        try {
          const response = await fetch("/api/coding-agent/submit", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(action.params),
          });
          const data = await response.json();
          hideAssistantTyping();
          if (data.status === "ok") {
            addAssistantMessage("assistant", `✓ 任务已创建: ${data.task.task_id}\n状态: ${data.task.display_status}`, [
              { type: "view_task", label: "查看任务详情", task_id: data.task.task_id },
              { type: "supplement", label: "补充信息", task_id: data.task.task_id },
            ]);
            assistantState.currentTaskId = data.task.task_id;
          } else {
            addAssistantMessage("assistant", `创建失败：${data.error}`);
          }
        } catch (error) {
          hideAssistantTyping();
          addAssistantMessage("assistant", `网络错误：${error.message}`);
        }
        break;
      case "view_task":
        switchView("tasks");
        const taskCard = document.querySelector(`[data-task-id="${action.task_id}"]`);
        if (taskCard) taskCard.scrollIntoView({ behavior: "smooth", block: "center" });
        break;
      case "supplement":
        $("assistant-input").value = `补充信息：`;
        $("assistant-input").focus();
        break;
    }
  }

  $("assistant-send")?.addEventListener("click", async () => {
    const input = $("assistant-input");
    const userMessage = input.value.trim();
    if (!userMessage) return;
    addAssistantMessage("user", userMessage);
    input.value = "";
    input.style.height = "60px";
    showAssistantTyping();
    try {
      const response = await fetch("/api/coding-agent/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: userMessage, context: assistantState }),
      });
      const data = await response.json();
      hideAssistantTyping();
      if (data.status === "ok") {
        addAssistantMessage("assistant", data.reply, data.actions);
        if (data.task_id) assistantState.currentTaskId = data.task_id;
      } else {
        addAssistantMessage("assistant", `处理失败：${data.error}`, null);
      }
    } catch (error) {
      hideAssistantTyping();
      addAssistantMessage("assistant", `网络错误：${error.message}`, null);
    }
  });

  $("assistant-input")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      $("assistant-send").click();
    }
  });

  document.querySelectorAll(".assistant-shortcut").forEach(btn => {
    btn.addEventListener("click", async () => {
      const shortcut = btn.dataset.shortcut;
      switch (shortcut) {
        case "status":
          $("assistant-input").value = "查看最近任务状态";
          $("assistant-send").click();
          break;
        case "services":
          $("assistant-input").value = "列出所有可用服务";
          $("assistant-send").click();
          break;
        case "help":
          addAssistantMessage("assistant", `<strong>使用帮助</strong><br><br>你可以用自然语言描述需求，助手会自动：<br>1. 匹配合适的服务<br>2. 提取关键信息<br>3. 创建任务<br>4. 跟踪进展<br><br><strong>示例：</strong><br>• "研究华为的云计算战略"<br>• "为某区政府准备智慧城市方案"<br>• "生成本周销售简报"<br><br>需要审批的操作（写入台账、生成正式文件）仍在任务中心确认。`);
          break;
      }
    });
  });

  const freeChat = window.Agent4MarketFreeChat.create({ api, getToken: () => requestToken });
  initializeAppUpdates();
  localizeStaticInterface();
  load()
    .then(() => { checkAppUpdates(false); return detectCodingAssistants().catch(() => {}); })
    .catch((error) => note(`无法读取工作台：${error.message}`, true));
  setInterval(() => load().catch(() => {}), 3000);
})();
