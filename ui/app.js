(() => {
  let model = null;
  let requestToken = null;
  let selectedProfile = null;
  let selectedService = null;
  let guidedRenderedService = null;
  let modelSettingsInitialized = false;
  let currentView = "home";
  let selectedProject = "project-default";
  let noticeTimer = null;
  let schedulePanelInitialized = false;
  const guidedDrafts = {};
  const guidedNotes = {};

  const viewTitles = {
    home: "工作台", work: "发起工作", tasks: "任务中心", sales: "客户与销售",
    knowledge: "知识库", weekly: "周报中心", outputs: "输出中心", projects: "项目空间",
    schedules: "每日定时任务", search: "自定义搜索", settings: "设置",
  };
  if (viewTitles[window.location.hash.slice(1)]) currentView = window.location.hash.slice(1);

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
        { id: "purpose", label: "研究结果用来做什么？", type: "select", default: "支持客户沟通与机会判断", options: ["支持客户沟通与机会判断", "形成内部行业简报", "准备销售方案或 PPT", "识别竞品、合作方与风险"] },
        { id: "period", label: "优先关注的时间范围", type: "select", default: "近 12 个月，并补充关键历史背景", options: ["近 3 个月", "近 12 个月，并补充关键历史背景", "近 3 年趋势", "不限定，按相关性筛选"] },
      ],
      presets: [
        { label: "客户行业速览", values: { topic: "目标客户所在行业的近期变化与业务机会", purpose: "支持客户沟通与机会判断", period: "近 12 个月，并补充关键历史背景" } },
        { label: "竞品与合作方", values: { topic: "目标方向的主要竞品、合作方与差异化机会", purpose: "识别竞品、合作方与风险", period: "近 12 个月，并补充关键历史背景" } },
        { label: "前沿技术机会", values: { topic: "脑机、具身智能与数据采集方向的商业化进展", purpose: "形成内部行业简报", period: "近 12 个月，并补充关键历史背景" } },
      ],
    },
    "pdf-import": {
      title: "PDF 资料入库",
      intro: "填写已放入 inputs 或 data/inbox 的 PDF 路径，助手会按页提取、标注来源并生成待审批的知识记录。",
      instruction: "只读取指定的受控目录 PDF；保留页码与文件指纹，提取失败时停止，不把摘要当作已证实事实。",
      fields: [
        { id: "path", label: "PDF 相对路径", type: "text", required: true, placeholder: "例如：inputs/customer-report.pdf" },
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
        { id: "audience", label: "给谁使用或阅读？", type: "text", required: true, placeholder: "例如：客户 CTO、公司技术团队、总经理办公会" },
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
    running: "正在处理",
    requested: "等待 Pi 接手",
    interrupted: "已中断",
    cancelling: "正在取消",
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
  function displayStatus(task) { return task.display_status || task.status || ""; }
  function projectById(projectId) { return model?.projects?.find((project) => project.project_id === projectId); }
  function selectedProjectRecord() { return projectById(selectedProject) || model?.projects?.[0]; }
  function isPresentationStudio(service = currentService()) { return service?.id === "presentation-studio" || service?.workflow === "shared.presentation.studio"; }
  function isWeeklyService(service = currentService()) { return service?.id === "weekly-deck" || service?.workflow?.startsWith("shared.reporting.weekly-deck"); }

  function switchView(view) {
    if (!viewTitles[view]) return;
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
    else if (settings.status === "missing_key") $("model-current").textContent = `密钥不可用：${settings.provider_id}/${settings.selected_model}，请重新填写并保存`;
    else if (settings.configured) $("model-current").textContent = `${settings.provider_id}/${settings.selected_model} · ${settings.base_url}`;
    else $("model-current").textContent = "沿用 Pi 当前默认模型；尚未配置 NewAPI 网关";
    if (modelSettingsInitialized && !force) return;
    modelSettingsInitialized = true;
    $("model-base-url").value = settings.base_url || "";
    $("model-private-network").checked = Boolean(settings.allow_private_network);
    $("model-api-key").value = "";
    $("model-api-key").placeholder = settings.has_api_key ? "已保存；留空则继续使用" : "请输入网关 API Key";
    populateModelOptions(settings.models || [], settings.selected_model || "");
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
      item.textContent = node.id;
      item.title = node.type;
      flow.append(item);
    });
    box.append(flow);
  }

  function parseCanonicalPayload(task) {
    const text = task?.pending_write?.canonical_payload;
    if (typeof text !== "string") return null;
    try { const value = JSON.parse(text); return value && typeof value === "object" ? value : null; } catch { return null; }
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
    title.textContent = "PPT 大纲便利贴";
    const meta = document.createElement("span");
    meta.className = "deck-meta";
    meta.textContent = `${slides.length} 页 · ${plan.mode || plan.brief?.mode || "标准"}模式`;
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
        if (!confirm("确认保存当前顺序和标题，并结束旧任务、创建一个新的修订任务？")) return;
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
    const wrapper = document.createElement(presentationRendered ? "details" : "div");
    if (presentationRendered) {
      wrapper.className = "raw-details";
      const summary = document.createElement("summary");
      summary.textContent = "查看冻结载荷与校验码";
      wrapper.append(summary);
    }
    const title = document.createElement("p");
    title.className = "write-intent-title";
    title.textContent = `待写入内容（${task.pending_write.logical_tool} · ${task.pending_write.status}）`;
    const hash = document.createElement("small");
    hash.className = "write-intent-hash";
    hash.textContent = `校验码：${task.pending_write.payload_sha256}`;
    const payload = document.createElement("pre");
    payload.className = "write-intent";
    try { payload.textContent = JSON.stringify(JSON.parse(task.pending_write.canonical_payload), null, 2); } catch { payload.textContent = task.pending_write.canonical_payload || ""; }
    wrapper.append(title, hash, payload);
    article.insertBefore(wrapper, article.querySelector(".task-actions"));
  }

  function renderTasks() {
    const box = $("tasks");
    box.replaceChildren();
    if (!model.tasks.length) { box.textContent = "还没有任务。先选择服务并写下任务说明。"; renderWorkflow(); return; }
    model.tasks.forEach((task, index) => {
      const template = $("task-template").content.cloneNode(true);
      const serviceName = currentProfile()?.services.find((service) => service.id === task.service_id)?.display_name;
      template.querySelector("strong").textContent = serviceName || task.service_id || "销售任务";
      const badge = template.querySelector(".status");
      const effectiveStatus = displayStatus(task);
      badge.textContent = label[effectiveStatus] || effectiveStatus || "未知";
      if (Object.hasOwn(label, effectiveStatus)) badge.classList.add(effectiveStatus);
      const scheduleMeta = task.schedule_id ? ` · 每日任务 ${task.scheduled_for || ""}` : "";
      template.querySelector(".task-meta").textContent = `项目：${projectById(task.project_id)?.name || "日常工作"}${scheduleMeta} · 节点：${task.waiting_node || task.current_node || "等待 Pi 接手"} · 版本 ${task.version ?? "-"}`;
      template.querySelector(".task-request").textContent = task.request || "";
      const actions = template.querySelector(".task-actions");
      const article = template.querySelector("article");
      const presentationRendered = renderPresentationReview(article, task);
      renderRawWriteIntent(article, task, presentationRendered);
      if (task.status === "waiting_approval") addAction(actions, task, "approve", task.pending_write ? "批准并生成" : "确认并继续");
      if (task.status === "waiting_approval") addAction(actions, task, "reject", "驳回");
      if (task.status === "waiting_approval") addAction(actions, task, "cancel", "取消任务");
      if (effectiveStatus === "interrupted") addAction(actions, task, "cancel", "取消中断任务");
      article.onclick = (event) => { if (!event.target.closest("button,summary,input,textarea,select")) renderWorkflow(task); };
      box.append(template);
      if (index === 0) renderWorkflow(task);
    });
  }

  function addAction(box, task, decision, text) {
    const button = document.createElement("button");
    button.className = `action ${decision}`;
    button.textContent = text;
    button.onclick = async () => {
      const suffix = decision === "approve" && task.pending_write ? `\n\n本次批准将绑定校验码：\n${task.pending_write.payload_sha256}` : "";
      if (!confirm(`确认${text}？${suffix}`)) return;
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

  function summaryRow(className, left, right) {
    const row = document.createElement("div");
    row.className = className;
    const name = document.createElement("span"); name.textContent = left;
    const detail = document.createElement("span"); detail.textContent = right;
    row.append(name, detail);
    return row;
  }

  function renderData() {
    const renderGroup = (box, items) => {
      box.replaceChildren(...items.map((item) => summaryRow("summary-row", item.path.split("/").pop(), item.exists ? `${item.records ?? "?"} 条 · ${item.updated_at || "未知时间"}` : "尚未创建")));
    };
    renderGroup($("knowledge-summary"), model.data.knowledge || []);
    renderGroup($("sales-summary"), model.data.sales || []);
    const recentFiles = (model.project_files || []).slice(0, 8);
    const filesBox = $("knowledge-files");
    filesBox.classList.toggle("empty", recentFiles.length === 0);
    filesBox.replaceChildren(...recentFiles.map((item) => fileRow(item)));
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
    const use = document.createElement("button"); use.className = "secondary"; use.textContent = item.name.toLowerCase().endsWith(".pdf") ? "PDF 入库" : "用于任务";
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
    row.append(copy, size, use);
    return row;
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
        if (target === "archived" && !confirm("归档后，该项目的每日任务会暂停。继续吗？")) return;
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
    else { fileBox.replaceChildren(); fileBox.textContent = "当前项目还没有资料，可上传 PDF、Word、Excel、CSV、文本或 PPT。"; }
    const artifactPaths = new Set((model.tasks || [])
      .filter((task) => task.project_id === selectedProject)
      .flatMap((task) => Array.isArray(task.artifacts) ? task.artifacts : [])
      .filter((path) => typeof path === "string" && path.startsWith("outputs/")));
    const projectOutputs = (model.outputs || []).filter((item) => artifactPaths.has(item.path));
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
      const meta = document.createElement("small"); meta.textContent = `${projectById(schedule.project_id)?.name || "默认项目"} · ${serviceById(schedule.service_id)?.display_name || schedule.service_id} · ${schedule.last_enqueued_date ? `最近排队 ${schedule.last_enqueued_date}` : "尚未执行"}`;
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
    const period = currentWeek();
    const inWeek = tasks.filter((task) => String(task.updated_at || task.created_at || "") >= period.start);
    const pending = tasks.filter((task) => task.status === "waiting_approval");
    const running = tasks.filter((task) => displayStatus(task) === "running");
    const queued = tasks.filter((task) => displayStatus(task) === "requested");
    const completed = inWeek.filter((task) => task.status === "completed");
    $("home-pending").textContent = pending.length;
    $("home-running").textContent = running.length;
    $("home-completed").textContent = completed.length;
    $("approval-total").textContent = pending.length;
    $("nav-task-count").textContent = pending.length ? String(pending.length) : "";
    const hour = new Date().getHours();
    $("greeting").textContent = `${hour < 11 ? "早上好" : hour < 14 ? "中午好" : hour < 18 ? "下午好" : "晚上好"}，今天有 ${pending.length + running.length + queued.length} 项需要处理`;
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
        const request = document.createElement("small"); request.textContent = String(task.request || "").replace(/\s+/gu, " ").slice(0, 60);
        copy.append(title, request);
        const effectiveStatus = displayStatus(task);
        const status = document.createElement("i"); status.className = `status ${effectiveStatus}`; status.textContent = label[effectiveStatus] || effectiveStatus;
        row.append(copy, status); row.onclick = () => { switchView("tasks"); renderWorkflow(task); }; return row;
      }));
    };
    renderCompactTasks($("home-approvals"), pending.slice(0, 6), "暂无待确认事项");
    renderCompactTasks($("home-recent"), tasks.slice(0, 5), "暂无任务");
  }

  function render() {
    renderModelSettings(); renderProjectSelectors(); renderServices(); renderTaskForm(); renderTasks();
    renderData(); renderOutputs(); renderProjects(); renderSchedules(); renderDashboard(); switchView(currentView);
  }

  async function createTask(request) {
    return api("/api/task-requests", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile_id: selectedProfile, service_id: selectedService, project_id: selectedProject, request }),
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
    if (!topic) throw new Error("请先填写要制作的 PPT 主题。");
    if (topic.length > 240) throw new Error("PPT 主题不能超过 240 字。");
    if (expectedDecision.length > 500) throw new Error("期望决策不能超过 500 字。");
    if (!Number.isInteger(duration) || duration < 3 || duration > 120) throw new Error("演讲时长必须是 3–120 分钟的整数。");
    if (!Number.isInteger(pages) || pages < 4 || pages > 10) throw new Error("MVP 页数必须是 4–10 页。");
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.pptx$/u.test(outputName)) throw new Error("输出文件名格式无效，请使用安全 ASCII 文件名并以 .pptx 结尾。");
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
    requestToken = model.request_token;
    if (!selectedProfile || !model.profiles.some((item) => item.id === selectedProfile)) { selectedProfile = model.profiles.find((item) => item.id === "sales-director")?.id || model.profiles[0]?.id; selectedService = currentProfile()?.default_service; }
    if (!currentProfile()?.services.some((item) => item.id === selectedService)) selectedService = currentProfile()?.default_service;
    if (!projectById(selectedProject) || projectById(selectedProject)?.status !== "active") selectedProject = model.projects?.find((item) => item.status === "active")?.project_id || "project-default";
    render();
  }

  $("create").onclick = async () => {
    try {
      const response = await createTask(guidedRequest());
      guidedDrafts[selectedService] = {};
      guidedNotes[selectedService] = "";
      renderGuidedForm(true);
      note(`任务已登记（${response.request_id}），等待 Pi 工作流接手。`);
    } catch (error) { note(error.message, true); }
  };

  $("request-notes").addEventListener("input", () => { guidedNotes[selectedService] = $("request-notes").value; });

  document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
  document.querySelectorAll("[data-service]").forEach((button) => button.addEventListener("click", () => openService(button.dataset.service)));
  $("menu-toggle").onclick = () => $("sidebar").classList.toggle("open");
  $("task-project").onchange = () => { selectedProject = $("task-project").value; $("schedule-project").value = selectedProject; };
  $("home-project").onchange = () => { selectedProject = $("home-project").value; $("task-project").value = selectedProject; $("schedule-project").value = selectedProject; renderProjects(); };
  $("schedule-project").onchange = () => { selectedProject = $("schedule-project").value; $("task-project").value = selectedProject; };

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

  async function uploadProjectFile(file) {
    if (!file) return;
    if (file.size <= 0 || file.size > 32 * 1024 * 1024) throw new Error("单个资料必须为 1 字节至 32 MB。");
    const response = await fetch("/api/project-files", {
      method: "POST",
      headers: {
        "X-Director-Token": requestToken || "", "Content-Type": "application/octet-stream",
        "X-Project-Id": selectedProject, "X-File-Name": encodeURIComponent(file.name),
      },
      body: file,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "资料上传失败");
    return data;
  }

  [$("home-upload"), $("project-upload")].forEach((button) => { button.onclick = () => $("project-file-input").click(); });
  $("project-file-input").onchange = async () => {
    const file = $("project-file-input").files?.[0];
    try { const reply = await uploadProjectFile(file); note(reply.message); await load(); switchView("projects"); }
    catch (error) { note(error.message, true); }
    finally { $("project-file-input").value = ""; }
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
          request: $("schedule-request").value.trim(),
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
      note("模型已保存；关闭并重新打开 Agent4Market 后，后续任务将使用新模型。");
    } catch (error) {
      $("model-discovery-status").textContent = error.message;
      button.disabled = false;
    }
  };

  $("reset-model-settings").onclick = async () => {
    if (!confirm("恢复 Pi 默认模型？已保存的 NewAPI 密钥会从本机删除，重启应用后生效。")) return;
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
      note("已恢复默认模型；关闭并重新打开 Agent4Market 后生效。");
    } catch (error) {
      $("model-discovery-status").textContent = error.message;
    } finally { button.disabled = false; }
  };

  $("create-presentation").onclick = async () => {
    try {
      const brief = presentationBrief();
      const response = await createTask(`[PRESENTATION_BRIEF]\n${JSON.stringify(brief, null, 2)}\n[/PRESENTATION_BRIEF]`);
      $("ppt-topic").value = "";
      note(`PPT 项目已登记（${response.request_id}），将先进入需求与证据阶段。`);
    } catch (error) { note(error.message, true); }
  };

  $("create-weekly").onclick = async () => {
    try {
      const response = await createTask(`[PRESENTATION_BRIEF]\n${JSON.stringify(weeklyBrief(), null, 2)}\n[/PRESENTATION_BRIEF]`);
      $("weekly-focus").value = "";
      note(`本周销售汇报已登记（${response.request_id}），助手将自动汇总本周记录。`);
    } catch (error) { note(error.message, true); }
  };

  load().catch((error) => note(`无法读取工作台：${error.message}`, true));
  setInterval(() => load().catch(() => {}), 5000);
})();
