(() => {
  let model = null;
  let requestToken = null;
  let selectedProfile = null;
  let selectedService = null;
  let guidedRenderedService = null;
  let modelSettingsInitialized = false;
  let searchSettingsInitialized = false;
  let searchGatewaySettingsInitialized = false;
  let runtimeSettingsInitialized = false;
  let mailSettingsInitialized = false;
  let taskRuntimeCatalogKey = "";
  let currentView = "home";
  const viewHistory = [];
  const viewScrollPositions = {};
  let selectedProject = "project-default";
  let noticeTimer = null;
  let activeConfirmDismiss = null;
  let schedulePanelInitialized = false;
  let knowledgeEntries = [];
  let knowledgeVersion = "";
  let knowledgeLoadError = "";
  let knowledgeTruncated = false;
  let knowledgeRenderedKey = "";
  let reimbursementMailMessages = [];
  const guidedDrafts = {};
  const guidedNotes = {};
  const taskMessageDrafts = {};
  const taskProgressScroll = {};
  const taskWriteIntentState = {};
  const taskCardExpansion = new Map();
  const knowledgeCardExpansion = new Map();
  const reimbursementBatchExpansion = new Map();
  const customerState = {
    filters: { query: "", owner: "", region: "", industry: "", stage: "", health: "", updated: "" },
    rows: [], cursor: "", hasMore: false, loading: false, loaded: false, error: "", selectedId: "", detail: null,
    detailLoading: false, detailError: "", timeline: [], timelineCursor: "", timelineHasMore: false,
    timelineLoading: false, timelineError: "", activeTab: "overview",
    listGeneration: 0, detailGeneration: 0, timelineGeneration: 0,
    attention: [], attentionLoaded: false, attentionLoading: false, attentionError: "", attentionGeneration: 0,
    listController: null, detailController: null, timelineController: null, attentionController: null,
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
        editor = document.createElement("input"); editor.value = String(inputValue); editor.maxLength = 120;
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
        const focusable = [close, cancel, confirm];
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
    home: "工作台", work: "发起工作", tasks: "任务中心", sales: "客户与销售",
    knowledge: "知识库", weekly: "周报中心", outputs: "输出中心", projects: "项目空间",
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
    ["KNOWLEDGE BASE", "知识库"],
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
      intro: "告诉助手研究对象和用途，它会自动检索公开资料、核验证据并联系当前知识库形成结论。",
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
        { id: "goal", label: "入库后主要怎么用？", type: "select", default: "提取可引用证据并写入知识库", options: ["提取可引用证据并写入知识库", "分析客户材料并提炼销售机会", "提取政策要点和政府合作依据", "形成文档摘要与待验证问题"] },
        { id: "focus", label: "重点关注（可选）", type: "text", placeholder: "例如：客户业务、预算、试点条件、政策支持或关键数据" },
      ],
      presets: [
        { label: "证据入库", values: { goal: "提取可引用证据并写入知识库" } },
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
        { id: "materials", label: "依据哪些现有信息？", type: "textarea", required: true, placeholder: "粘贴关键事实，或写明要结合的客户、任务、知识库资料和已有文件。" },
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
    if (!response.ok) throw new Error(data.error || "操作未完成");
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
  function isPresentationStudio(service = currentService()) { return service?.id === "presentation-studio" || service?.workflow === "shared.presentation.studio"; }
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
    }
    if (view === "work") renderServices();
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
    if (serviceId === "weekly-deck") switchView("weekly");
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
    const services = currentProfile()?.services || [];
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
      const empty = document.createElement("option"); empty.value = ""; empty.textContent = "请先获取模型"; options.push(empty);
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

  function renderModelSettings(force = false) {
    const settings = model?.model || { configured: false, status: "unconfigured" };
    const panel = $("model-settings-panel");
    panel.classList.toggle("configured", settings.configured && settings.status === "configured");
    panel.classList.toggle("error", settings.status === "error" || settings.status === "missing_key");
    if (settings.status === "error") $("model-current").textContent = `配置异常：${settings.error}`;
    else if (settings.status === "missing_key") $("model-current").textContent = `密钥不可用：${displayModelName(settings.selected_model)}，请重新填写并保存`;
    else if (settings.configured) $("model-current").textContent = `当前模型：${displayModelName(settings.selected_model)} · ${settings.base_url}`;
    else $("model-current").textContent = "沿用智能核心默认模型；尚未配置自定义模型网关";
    if (modelSettingsInitialized && !force) return;
    modelSettingsInitialized = true;
    $("model-base-url").value = settings.base_url || "";
    $("model-private-network").checked = Boolean(settings.allow_private_network);
    $("model-api-key").value = "";
    $("model-api-key").placeholder = settings.has_api_key ? "已保存；留空则继续使用" : "请输入网关接口密钥";
    populateModelOptions(settings.models || [], settings.selected_model || "");
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
    if (!["industry-research", "government-proposal", "presentation-studio"].includes(serviceId)) return true;
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
    const available = settings.configured && settings.status === "configured" ? (settings.models || []) : [];
    const provider = settings.provider_id || "";
    const catalogKey = JSON.stringify([provider, settings.selected_model || "", available.map((item) => item.id)]);
    if (catalogKey !== taskRuntimeCatalogKey) {
      taskRuntimeCatalogKey = catalogKey;
      const previous = $("task-model").value;
      let remembered = "";
      try { remembered = localStorage.getItem("agent4market.taskModel") || ""; } catch { /* Local storage is optional. */ }
      const options = [];
      const fallback = document.createElement("option");
      fallback.value = "";
      fallback.textContent = settings.configured && settings.selected_model
        ? `默认：${settings.selected_model}`
        : "智能核心默认模型";
      options.push(fallback);
      available.forEach((item) => {
        const option = document.createElement("option");
        option.value = `${provider}/${item.id}`;
        option.textContent = item.owned_by ? `${item.id} · ${item.owned_by}` : item.id;
        options.push(option);
      });
      $("task-model").replaceChildren(...options);
      const desired = previous || remembered;
      if (desired && options.some((option) => option.value === desired)) $("task-model").value = desired;
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
      };
      $("task-thinking").onchange = () => {
        try { localStorage.setItem("agent4market.taskThinking", $("task-thinking").value); } catch { /* Local storage is optional. */ }
      };
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
    const config = guidedServices[selectedService];
    if (!config || (!force && guidedRenderedService === selectedService)) return;
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

  function renderTaskForm() {
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

  function stableWriteField(task, payload) {
    if (task.pending_write?.logical_tool === "knowledge.write") return "source_id";
    return { customers: "customer_id", activities: "activity_id", resource_requests: "request_id", sales_assets: "asset_id" }[payload?.table] || "";
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
    const stableField = stableWriteField(task, payload);
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
      intro.textContent = `将向知识库${writeIntentCounts(mutations) || "写入以下内容"}。正式写入前，你可以逐条核对来源、事实和限制。`;
    } else if (task.pending_write.logical_tool === "sales.write") {
      const tableLabels = { customers: "客户台账", activities: "客户活动记录", resource_requests: "资源申请", sales_assets: "销售资料库" };
      intro.textContent = `将更新${tableLabels[parsedPayload?.table] || "销售台账"}：${writeIntentCounts(mutations) || "写入以下内容"}。`;
    } else if (task.pending_write.logical_tool === "artifact.deck.write") {
      intro.textContent = `将生成演示文稿“${parsedPayload?.output_name || "未命名演示文稿"}”。上方大纲和逐页内容就是本次审批范围。`;
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
      const ignoredFields = new Set(["source_id", "activity_id", "request_id", "asset_id", "updated_at", "created_at", "notes"]);
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
      "knowledge.write": "写入知识库", "sales.write": "更新销售台账",
      "presentation.plan.write": "保存演示方案", "artifact.deck.write": "生成演示文稿",
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
    if (task.pending_write?.logical_tool === "knowledge.write") return "批准写入知识库";
    if (task.pending_write?.logical_tool === "sales.write") return "批准更新销售台账";
    if (task.pending_write?.logical_tool === "artifact.deck.write") return "批准并生成演示文稿";
    return task.pending_write ? "批准执行" : "确认并继续";
  }

  function progressTime(value) {
    const date = new Date(value || "");
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
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

    const effectiveStatus = displayStatus(task);
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
      const warning = "彻底删除后，任务卡、处理过程、排队消息和演示方案无法恢复。已生成文件、知识库和销售台账不会被删除。";
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

  const knowledgeStatusLabels = {
    verified: "已核验", pending: "待核验", superseded: "已替代", rejected: "已拒绝",
  };

  function knowledgeTextSection(className, title, text) {
    const section = document.createElement("section");
    section.className = `knowledge-content ${className}`;
    const heading = document.createElement("strong");
    heading.textContent = title;
    const copy = document.createElement("p");
    copy.textContent = text;
    section.append(heading, copy);
    return section;
  }

  function knowledgeCard(entry) {
    const card = document.createElement("details");
    card.className = "knowledge-card";
    const cardId = entry.source_id || entry.url || entry.title;
    card.open = Boolean(knowledgeCardExpansion.get(cardId));
    card.addEventListener("toggle", () => knowledgeCardExpansion.set(cardId, card.open));

    const summary = document.createElement("summary");
    const summaryCopy = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = entry.title || "未命名知识条目";
    const meta = document.createElement("small");
    meta.textContent = [entry.publisher, entry.published_date || entry.accessed_date, entry.topic].filter(Boolean).join(" · ") || "暂无来源说明";
    summaryCopy.append(title, meta);
    const badges = document.createElement("div");
    badges.className = "knowledge-badges";
    const status = document.createElement("span");
    status.className = `knowledge-badge ${entry.status || "unknown"}`;
    status.textContent = knowledgeStatusLabels[entry.status] || entry.status || "未标注状态";
    badges.append(status);
    if (entry.quality) {
      const quality = document.createElement("span");
      quality.className = "knowledge-badge quality";
      quality.textContent = entry.quality;
      badges.append(quality);
    }
    summary.append(summaryCopy, badges);

    const body = document.createElement("div");
    body.className = "knowledge-card-body";
    if (entry.key_facts) body.append(knowledgeTextSection("facts", "主要事实", entry.key_facts));
    if (entry.interpretation) body.append(knowledgeTextSection("analysis", "分析说明", entry.interpretation));
    if (entry.limitations) body.append(knowledgeTextSection("warning", "限制与提醒", entry.limitations));
    if (entry.important_quotes) body.append(knowledgeTextSection("quotes", "重要原文", entry.important_quotes));

    const metadata = document.createElement("dl");
    metadata.className = "knowledge-metadata";
    const appendMeta = (labelText, value) => {
      if (!value) return;
      const term = document.createElement("dt"); term.textContent = labelText;
      const description = document.createElement("dd"); description.textContent = value;
      metadata.append(term, description);
    };
    appendMeta("发布机构", entry.publisher);
    appendMeta("发布日期", entry.published_date);
    appendMeta("访问日期", entry.accessed_date);
    appendMeta("地区", entry.region);
    appendMeta("资料类型", entry.source_type);
    appendMeta("知识编号", entry.source_id);
    if (metadata.children.length) body.append(metadata);

    if (entry.notes) {
      const notes = document.createElement("details");
      notes.className = "knowledge-notes";
      const notesSummary = document.createElement("summary"); notesSummary.textContent = "查看补充记录";
      const notesText = document.createElement("p"); notesText.textContent = entry.notes;
      notes.append(notesSummary, notesText);
      body.append(notes);
    }

    const actions = document.createElement("div");
    actions.className = "knowledge-actions";
    if (entry.url) {
      const open = document.createElement("button");
      open.className = "primary";
      open.type = "button";
      open.textContent = "打开来源";
      open.addEventListener("click", async () => {
        open.disabled = true;
        try {
          const reply = await api("/api/knowledge/source/open", {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: entry.url }),
          });
          note(reply.message);
        } catch (error) { note(error.message, true); }
        finally { open.disabled = false; }
      });
      const copy = document.createElement("button");
      copy.className = "secondary";
      copy.type = "button";
      copy.textContent = "复制链接";
      copy.addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(entry.url); note("来源链接已复制。"); }
        catch { note("无法自动复制，请展开补充记录查看链接。", true); }
      });
      actions.append(open, copy);
    } else {
      const noUrl = document.createElement("span");
      noUrl.className = "hint";
      noUrl.textContent = "这条记录没有登记公开来源链接。";
      actions.append(noUrl);
    }
    body.append(actions);
    card.append(summary, body);
    return card;
  }

  function renderKnowledge() {
    const box = $("knowledge-records");
    const query = $("knowledge-query").value.trim().toLocaleLowerCase("zh-CN");
    const status = $("knowledge-status-filter").value;
    const renderedKey = `${knowledgeVersion}|${query}|${status}|${knowledgeLoadError}`;
    if (renderedKey === knowledgeRenderedKey) return;
    knowledgeRenderedKey = renderedKey;
    const scrollTop = box.scrollTop;
    const fields = ["title", "publisher", "topic", "region", "key_facts", "interpretation", "limitations", "important_quotes", "notes"];
    const filtered = knowledgeEntries.filter((entry) => {
      if (status && entry.status !== status) return false;
      if (!query) return true;
      return fields.some((field) => String(entry[field] || "").toLocaleLowerCase("zh-CN").includes(query));
    });
    $("knowledge-count").textContent = String(filtered.length);
    box.classList.toggle("empty", filtered.length === 0);
    if (knowledgeLoadError) {
      box.replaceChildren();
      box.textContent = `知识条目暂时无法读取：${knowledgeLoadError}`;
    } else if (!filtered.length) {
      box.replaceChildren();
      box.textContent = query || status ? "没有符合筛选条件的知识条目。" : "知识库中还没有可显示的条目。";
    } else {
      box.replaceChildren(...filtered.map(knowledgeCard));
      if (knowledgeTruncated) {
        const warning = document.createElement("p");
        warning.className = "knowledge-truncated";
        warning.textContent = model?.data?.backend === "sqlite"
          ? "当前只显示最近 500 条；请使用上方搜索缩小范围。SQLite 主库不会直接交给外部软件编辑。"
          : "当前只显示最近 500 条；可使用搜索缩小范围，或打开知识库文件查看全部记录。";
        box.append(warning);
      }
    }
    queueMicrotask(() => { box.scrollTop = Math.min(scrollTop, Math.max(0, box.scrollHeight - box.clientHeight)); });
  }

  function renderData() {
    $("open-knowledge-file").textContent = model.data?.backend === "sqlite" ? "打开知识库所在目录" : "打开知识库文件";
    const renderGroup = (box, items) => {
      box.replaceChildren(...items.map((item) => summaryRow("summary-row", item.path.split("/").pop(), item.exists ? `${item.records ?? "?"} 条 · ${item.updated_at || "未知时间"}` : "尚未创建")));
    };
    renderGroup($("knowledge-summary"), model.data.knowledge || []);
    renderGroup($("sales-summary"), model.data.sales || []);
    renderKnowledge();
    const recentFiles = (model.project_files || []).slice(0, 8);
    const filesBox = $("knowledge-files");
    filesBox.classList.toggle("empty", recentFiles.length === 0);
    filesBox.replaceChildren(...recentFiles.map((item) => fileRow(item)));
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
  function customerTabFor(section) {
    return ({ activities: "timeline", commitments: "actions", actions: "actions", risks: "signals", resource_requests: "resources", sales_assets: "resources", artifacts: "resources", evidence_refs: "evidence", task_links: "evidence" })[String(section || "")] || (String(section || "") || "overview");
  }
  async function selectCustomer(id, { tab = "overview", focus = false } = {}) {
    const customerId = String(id || ""); if (!customerId) return;
    customerState.selectedId = customerId; customerState.activeTab = customerTabFor(tab); customerState.detail = null; customerState.timeline = []; customerState.timelineCursor = ""; customerState.timelineHasMore = false; customerState.detailError = ""; customerState.timelineError = ""; customerState.detailLoading = true; customerState.timelineLoading = true;
    customerState.detailController?.abort(); const controller = new AbortController(); customerState.detailController = controller; const generation = ++customerState.detailGeneration;
    customerState.timelineController?.abort(); customerState.timelineGeneration += 1;
    renderCustomerOperations(); renderCustomerContext();
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
    const actions = $("customer-quick-actions"); actions.replaceChildren(customerQuickAction("sales-review", "客户复盘"), customerQuickAction("government-proposal", "政府合作"), customerQuickAction("industry-research", "行业研究"), customerQuickAction("office-document", "资源协调/文件", "resources"), customerQuickAction("presentation-studio", "制作演示文稿")); detailTabs();
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
        guidedDrafts["pdf-import"] = { path: item.path, goal: "提取可引用证据并写入知识库", focus: "" };
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
    const allowed = (currentProfile()?.services || []).filter((service) => !["presentation-studio", "weekly-deck", "pdf-import"].includes(service.id));
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
    renderModelSettings(); renderSearchSettings(); renderSearchGatewaySettings(); renderTaskRuntimeOptions(); renderRuntimeSettings(); renderMailSettings(); renderProjectSelectors(); renderServices(); renderTaskForm(); renderTasks();
    renderData(); renderOutputs(); renderProjects(); renderSchedules(); renderDashboard(); renderReimbursementLibrary(); renderCustomerOperations(); renderAttention(); switchView(currentView);
  }

  async function createTask(request) {
    if (!publicSearchReady(selectedService, true)) {
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
      body: JSON.stringify({ profile_id: selectedProfile, service_id: selectedService, project_id: selectedProject, request: `${request}${selectedCustomerContextText()}`, ...taskRuntimeSelection() }),
    });
  }

  function guidedRequest() {
    const config = guidedServices[selectedService];
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
      source_scope: "public-web-and-profile-knowledge", confidentiality: $("ppt-confidentiality").value,
      expected_decision: expectedDecision, output_name: outputName,
    };
  }

  function weeklyBrief() {
    const period = currentWeek();
    const focus = $("weekly-focus").value.trim();
    const focusText = focus ? ` 特别关注：${focus}` : "";
    return {
      schema_version: "1.0", scene: "weekly", mode: "quick",
      topic: `${period.start} 至 ${period.end} 销售周报：自动汇总重点客户进展、资源需求、风险和下周行动。${focusText}`,
      audience: "销售管理层", purpose: "复盘本周销售推进并确认下周资源配置", occasion: "周五销售例会", language: "zh-CN",
      duration_minutes: 15, target_slides: 6, design_system: { token_id: "management-report" },
      source_scope: "public-web-and-profile-knowledge", confidentiality: "internal",
      expected_decision: "确认重点客户优先级、资源配置和下周行动", output_name: autoOutputName("sales-weekly", false),
    };
  }

  async function load() {
    model = await api("/api/bootstrap");
    const composerFocus = captureTaskComposerFocus();
    requestToken = model.request_token;
    if (!selectedProfile || !model.profiles.some((item) => item.id === selectedProfile)) { selectedProfile = model.profiles.find((item) => item.id === "sales-director")?.id || model.profiles[0]?.id; selectedService = currentProfile()?.default_service; }
    if (!currentProfile()?.services.some((item) => item.id === selectedService)) selectedService = currentProfile()?.default_service;
    if (!projectById(selectedProject) || projectById(selectedProject)?.status !== "active") selectedProject = model.projects?.find((item) => item.status === "active")?.project_id || "project-default";
    const sourceVersion = model.data?.knowledge?.[0]?.version || "missing";
    if (sourceVersion !== knowledgeVersion || knowledgeLoadError) {
      try {
        const library = await api("/api/knowledge");
        knowledgeEntries = Array.isArray(library.entries) ? library.entries : [];
        knowledgeVersion = library.version || sourceVersion;
        knowledgeTruncated = Boolean(library.truncated);
        knowledgeLoadError = "";
      } catch (error) {
        knowledgeLoadError = error.message;
        knowledgeVersion = sourceVersion;
      }
      knowledgeRenderedKey = "";
    }
    render();
    restoreTaskComposerFocus(composerFocus);
    if (currentView === "sales" && !customerState.loaded && !customerState.loading) loadCustomers();
    if (!customerState.attentionLoaded) loadAttention();
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
  $("knowledge-query").addEventListener("input", () => { knowledgeRenderedKey = ""; renderKnowledge(); });
  $("knowledge-status-filter").addEventListener("change", () => { knowledgeRenderedKey = ""; renderKnowledge(); });
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
  $("open-knowledge-file").onclick = async () => {
    try {
      const reply = await api("/api/knowledge/file/open", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      note(reply.message);
    } catch (error) { note(error.message, true); }
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

  [$("home-upload"), $("project-upload"), $("project-quick-upload")].forEach((button) => { button.onclick = () => $("project-file-input").click(); });
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
      card.onclick = () => {
        if (item.project_id && projectById(item.project_id)?.status === "active") selectedProject = item.project_id;
        if (item.kind === "任务") switchView("tasks");
        else if (["项目", "项目文件"].includes(item.kind)) switchView("projects");
        else if (item.kind === "知识") switchView("knowledge");
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

  async function runQuickCommand() {
    const request = $("quick-command").value.trim();
    if (!request) { note("请先写下希望助手完成的工作。", true); return; }
    if (/周报|周五|周总结/u.test(request)) { $("weekly-focus").value = request.slice(0, 120); openService("weekly-deck"); return; }
    if (/PPT|演示|汇报材料/u.test(request)) { $("ppt-topic").value = request.slice(0, 240); openService("presentation-studio"); return; }
    const serviceId = /政府|园区|政策合作/u.test(request) ? "government-proposal" : /研究|行业|竞品|公开资料|调研/u.test(request) ? "industry-research" : /文件|方案|纪要|邮件/u.test(request) ? "office-document" : "sales-review";
    selectedService = serviceId;
    try { const response = await createTask(`【工作台快速指令】\n${request}\n请根据当前项目空间、知识库和销售台账补齐必要背景；涉及写入或正式文件时先等待审批。`); $("quick-command").value = ""; note(`任务已登记（${response.request_id}）。`); await load(); switchView("tasks"); }
    catch (error) { note(error.message, true); }
  }
  $("quick-command-start").onclick = runQuickCommand;
  $("quick-command").onkeydown = (event) => { if (event.key === "Enter") runQuickCommand(); };

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
      $("ppt-topic").focus();
    });
  });

  function modelPayload() {
    return {
      base_url: $("model-base-url").value.trim(),
      api_key: $("model-api-key").value.trim(),
      allow_private_network: $("model-private-network").checked,
    };
  }

  $("discover-models").onclick = async () => {
    const button = $("discover-models");
    button.disabled = true;
    $("model-discovery-status").textContent = "正在连接网关并读取模型…";
    try {
      const response = await api("/api/model-discovery", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(modelPayload()),
      });
      $("model-base-url").value = response.base_url;
      populateModelOptions(response.models, model?.model?.selected_model || "");
      $("model-discovery-status").textContent = response.message;
    } catch (error) {
      populateModelOptions([]);
      $("model-discovery-status").textContent = error.message;
    } finally { button.disabled = false; }
  };

  $("save-model-settings").onclick = async () => {
    const selectedModel = $("model-select").value;
    if (!selectedModel) { $("model-discovery-status").textContent = "请先获取并选择一个模型。"; return; }
    const button = $("save-model-settings");
    button.disabled = true;
    $("model-discovery-status").textContent = "正在验证并保存模型配置…";
    try {
      const response = await api("/api/model-settings", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...modelPayload(), selected_model: selectedModel }),
      });
      model.model = response;
      renderModelSettings(true);
      $("model-settings-panel").open = true;
      $("model-discovery-status").textContent = response.message;
      note("模型已保存；关闭并重新打开销售总监智能工作台后，后续任务将使用新模型。");
    } catch (error) {
      $("model-discovery-status").textContent = error.message;
      button.disabled = false;
    }
  };

  $("reset-model-settings").onclick = async () => {
    if (!await confirmAction({
      title: "恢复默认模型？",
      message: "已保存的模型网关密钥会从本机删除，关闭并重新打开应用后生效。",
      confirmText: "恢复默认模型",
      tone: "danger",
    })) return;
    const button = $("reset-model-settings");
    button.disabled = true;
    try {
      const response = await api("/api/model-settings/reset", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      model.model = response;
      renderModelSettings(true);
      $("model-settings-panel").open = true;
      $("model-discovery-status").textContent = response.message;
      note("已恢复默认模型；关闭并重新打开销售总监智能工作台后生效。");
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
      note(`本周销售汇报已登记（${response.request_id}），助手将自动汇总本周记录。`);
    } catch (error) { note(error.message, true); }
  };

  const reimbursementToday = new Date();
  const reimbursementStart = new Date(); reimbursementStart.setDate(reimbursementToday.getDate() - 30);
  $("expense-mail-from").value = formatDate(reimbursementStart);
  $("expense-mail-to").value = formatDate(reimbursementToday);
  localizeStaticInterface();
  load().catch((error) => note(`无法读取工作台：${error.message}`, true));
  setInterval(() => load().catch(() => {}), 3000);
})();
