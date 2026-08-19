(() => {
  let model = null;
  let requestToken = null;
  let selectedProfile = null;
  let selectedService = null;

  const $ = (id) => document.getElementById(id);
  const label = {
    waiting_approval: "等待你的审批",
    running: "正在处理",
    requested: "等待 Pi 接手",
    completed: "已完成",
    cancelled: "已取消",
    rejected: "已驳回",
    failed: "处理失败",
  };

  const note = (message, error = false) => {
    $("notice").textContent = message;
    $("notice").style.color = error ? "#a12b32" : "#066b62";
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
  function isPresentationService(service = currentService()) {
    return Boolean(service && (
      service.id === "presentation-studio" || service.id === "weekly-deck" ||
      service.workflow === "shared.presentation.studio" || service.workflow === "shared.reporting.weekly-deck"
    ));
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
    box.replaceChildren(...currentProfile().services.map((service) => {
      const button = choice(service.display_name, service.description, service.id === selectedService);
      button.onclick = () => { selectedService = service.id; renderWorkflow(); renderServices(); renderTaskForm(); };
      return button;
    }));
  }

  function renderTaskForm() {
    const presentation = isPresentationService();
    $("generic-task-form").hidden = presentation;
    $("presentation-task-form").hidden = !presentation;
  if (presentation && selectedService === "weekly-deck") { $("ppt-scene").value = "weekly"; $("ppt-mode").value = "quick"; }
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
      badge.textContent = label[task.status] || task.status || "未知";
      if (Object.hasOwn(label, task.status)) badge.classList.add(task.status);
      template.querySelector(".task-meta").textContent = `节点：${task.waiting_node || task.current_node || "等待 Pi 接手"} · 版本 ${task.version ?? "-"}`;
      template.querySelector(".task-request").textContent = task.request || "";
      const actions = template.querySelector(".task-actions");
      const article = template.querySelector("article");
      const presentationRendered = renderPresentationReview(article, task);
      renderRawWriteIntent(article, task, presentationRendered);
      if (task.status === "waiting_approval") addAction(actions, task, "approve", task.pending_write ? "批准并生成" : "确认并继续");
      if (task.status === "waiting_approval") addAction(actions, task, "reject", "驳回");
      if (task.status === "waiting_approval") addAction(actions, task, "cancel", "取消任务");
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
    const box = $("data-summary");
    const all = [...model.data.knowledge, ...model.data.sales];
    box.replaceChildren(...all.map((item) => summaryRow("summary-row", item.path.split("/").pop(), item.exists ? `${item.records ?? "?"} 条 · ${item.updated_at || "未知时间"}` : "尚未创建")));
  }

  function renderOutputs() {
    const box = $("outputs");
    box.replaceChildren();
    if (!model.outputs.length) { box.textContent = "暂无可显示的产物。"; return; }
    model.outputs.forEach((item) => box.append(summaryRow("output-row", item.name, item.modified_at)));
  }

  function render() { renderServices(); renderTaskForm(); renderTasks(); renderData(); renderOutputs(); }

  async function createTask(request) {
    return api("/api/task-requests", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ profile_id: selectedProfile, service_id: selectedService, request }) });
  }

  function presentationBrief() {
    const topic = $("ppt-topic").value.trim();
    const duration = Number($("ppt-duration").value);
    const pages = Number($("ppt-pages").value);
    const outputName = $("ppt-output").value.trim();
    const expectedDecision = $("ppt-decision").value.trim() || "信息同步";
    if (!topic) throw new Error("请先填写 PPT 主题与任务说明。");
    if (topic.length > 240) throw new Error("PPT 主题不能超过 240 字。");
    if (expectedDecision.length > 500) throw new Error("期望决策不能超过 500 字。");
    if (!Number.isInteger(duration) || duration < 3 || duration > 120) throw new Error("演讲时长必须是 3–120 分钟的整数。");
    if (!Number.isInteger(pages) || pages < 4 || pages > 10) throw new Error("MVP 页数必须是 4–10 页。");
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.pptx$/u.test(outputName)) throw new Error("输出文件名格式无效，请使用安全 ASCII 文件名并以 .pptx 结尾。");
    return {
      schema_version: "1.0", scene: $("ppt-scene").value, mode: $("ppt-mode").value, topic,
      audience: $("ppt-audience").value.trim() || "待澄清", purpose: $("ppt-purpose").value.trim() || "待澄清",
      occasion: $("ppt-occasion").value.trim() || "待澄清", language: $("ppt-language").value,
      duration_minutes: duration, target_slides: pages, design_system: { token_id: $("ppt-style").value },
      source_scope: "public-web-and-profile-knowledge", confidentiality: $("ppt-confidentiality").value,
      expected_decision: expectedDecision, output_name: outputName,
    };
  }

  async function load() {
    model = await api("/api/bootstrap");
    requestToken = model.request_token;
    if (!selectedProfile || !model.profiles.some((item) => item.id === selectedProfile)) { selectedProfile = model.profiles[0]?.id; selectedService = currentProfile()?.default_service; }
    if (!currentProfile()?.services.some((item) => item.id === selectedService)) selectedService = currentProfile()?.default_service;
    render();
  }

  $("create").onclick = async () => {
    const request = $("request").value.trim();
    if (!request) { note("请先写下任务说明。", true); return; }
    try { const response = await createTask(request); $("request").value = ""; note(`任务已登记（${response.request_id}），等待 Pi 工作流接手。`); } catch (error) { note(error.message, true); }
  };

  $("create-presentation").onclick = async () => {
    try {
      const brief = presentationBrief();
      const response = await createTask(`[PRESENTATION_BRIEF]\n${JSON.stringify(brief, null, 2)}\n[/PRESENTATION_BRIEF]`);
      $("ppt-topic").value = "";
      note(`PPT 项目已登记（${response.request_id}），将先进入需求与证据阶段。`);
    } catch (error) { note(error.message, true); }
  };

  load().catch((error) => note(`无法读取工作台：${error.message}`, true));
  setInterval(() => load().catch(() => {}), 5000);
})();
