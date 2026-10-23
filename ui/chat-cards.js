/* Server-owned, allowlisted components. Model text is never executable UI. */
(() => {
  const node = (tag, text, className) => {
    const result = document.createElement(tag);
    if (text !== undefined) result.textContent = String(text);
    if (className) result.className = className;
    return result;
  };
  const button = (text, handler) => {
    const result = node("button", text, "secondary"); result.type = "button";
    result.addEventListener("click", handler); return result;
  };
  const labeled = (text, input) => { const label = node("label", text); label.append(input); return label; };
  const choices = (values, current = "") => {
    const select = node("select");
    for (const [value, label] of Object.entries(values)) {
      const option = node("option", label); option.value = value; select.append(option);
    }
    select.value = current; return select;
  };

  class Cards {
    constructor(chat) {
      this.chat = chat;
      this.panel = document.getElementById("chat-memory-panel");
      this.open = document.getElementById("chat-memory-open");
      this.open?.addEventListener("click", () => {
        this.panel.hidden = !this.panel.hidden;
        this.open.setAttribute("aria-expanded", String(!this.panel.hidden));
        if (!this.panel.hidden) void this.loadPreferences();
      });
    }
    busy() { return Boolean(this.chat.running || this.chat.preparing); }
    controls() {
      document.querySelectorAll(".chat-assist-fieldset").forEach(field => {
        field.disabled = this.busy() || field.dataset.pending === "true";
      });
      if (this.open) this.open.disabled = this.busy();
      const toggle = document.getElementById("chat-use-preferences");
      if (toggle) toggle.disabled = this.busy();
    }
    async action(field, status, operation) {
      if (this.busy() || field.dataset.pending === "true") return;
      field.dataset.pending = "true"; field.disabled = true;
      status.textContent = "正在保存…"; status.classList.remove("error");
      try { await operation(); }
      catch (error) { status.textContent = error.message; status.classList.add("error"); }
      finally { delete field.dataset.pending; this.controls(); }
    }
    render(cards, host) {
      if (!Array.isArray(cards) || cards.length > 8) return;
      for (const card of cards) {
        try {
          let content;
          if (card.type === "project") content = this.project(card);
          else if (card.type === "preferences_used") content = this.used(card);
          else if (card.type === "preference_suggestion") content = this.suggestion(card);
          else if (card.type === "project_note") content = this.note(card);
          if (content) host.append(content);
        } catch { host.append(node("p", "辅助卡片暂不可用，仍可阅读文字回复。", "hint")); }
      }
      this.controls();
    }
    project(card) {
      const box = node("section", undefined, "chat-assist-card chat-project-card");
      box.setAttribute("aria-label", card.entity_type === "customer" ? "本轮客户资料" : "本轮项目资料");
      box.append(node("small", "本轮检索 · " + card.source), node("h3", card.name));
      const facts = node("dl", undefined, "chat-project-facts");
      for (const name of (card.entity_type === "customer" ? ["客户", "行业", "负责人", "阶段", "说明"] : ["客户", "负责人", "阶段", "说明"])) {
        facts.append(node("dt", name), node("dd", card.facts[name] || "待补充"));
      }
      box.append(facts);
      if (card.missing.length) box.append(node("p", "待补充：" + card.missing.join("、"), "hint"));
      if (card.source_changed) box.append(node("p", "来源有变化，需核对档案与原始资料。", "chat-assist-warning"));
      const detail = node("details");
      detail.append(node("summary", `查看引用记录与读取范围（${card.records.length} / ${card.record_count} 条）`));
      for (const record of card.records) {
        const row = node("article", undefined, "chat-source-record");
        row.append(node("p", record["内容"]), node("small", `${record["记录时间"]} · ${record["来源"] || "来源未记录"}`));
        detail.append(row);
      }
      detail.append(node("p", card.boundary, "hint"), node("small", "检索时间：" + card.retrieved_at));
      box.append(detail); return box;
    }
    used(card) {
      const box = node("details", undefined, "chat-assist-card chat-preferences-used");
      box.append(node("summary", "本轮使用了已保存的回答偏好"));
      const list = node("ul");
      for (const item of card.items) list.append(node("li", item));
      box.append(list, node("small", "仅影响表达方式；本轮要求优先。可在“回答偏好”中修改或忘记。"));
      return box;
    }
    suggestion(card) {
      const box = node("section", undefined, "chat-assist-card chat-preference-suggestion");
      box.dataset.preferenceKey = card.key; box.dataset.revision = card.revision;
      const field = node("fieldset", undefined, "chat-assist-fieldset");
      field.append(node("legend", "发现明确的回答偏好 · 尚未记住"));
      const select = choices(card.choices, card.value);
      const previous = node("p", "当前偏好：" + card.previous, "hint"); previous.dataset.previous = "true";
      const status = node("p", "只保存这项设置，不保存原话或推断个人画像。", "chat-assist-status");
      status.setAttribute("role", "status");
      const actions = node("div", undefined, "chat-assist-actions");
      const save = button("记住这个偏好", () => this.action(field, status, async () => {
        const result = await this.chat.post("/api/chat/preferences/save", {
          confirmed: true, revision: box.dataset.revision, key: card.key, value: select.value,
        });
        this.syncSuggestions(result);
        field.querySelector("legend").textContent = "回答偏好 · 已确认保存";
        status.textContent = "已记住，后续普通对话会使用；可随时修改或忘记。";
        save.disabled = true; select.disabled = true;
        if (this.panel && !this.panel.hidden) this.renderPreferences(result);
      }));
      actions.append(save, button("忽略", () => box.remove()));
      field.append(previous, labeled(card.label, select), actions, status); box.append(field);
      return box;
    }
    syncSuggestions(view) {
      document.querySelectorAll(".chat-preference-suggestion").forEach(box => {
        box.dataset.revision = view.revision;
        const key = box.dataset.preferenceKey, value = view.values[key]?.value;
        box.querySelector("[data-previous]").textContent = "当前偏好：" + (view.catalog[key]?.choices[value] || "尚未记住");
      });
    }
    note(card) {
      const kindLabel = card.entity_type === "customer" ? "客户" : "项目";
      const box = node("details", undefined, "chat-assist-card chat-project-note");
      box.append(node("summary", "补充 / 整理本轮信息，保存到「" + card.name + "」"));
      const form = node("form");
      const field = node("fieldset", undefined, "chat-assist-fieldset");
      field.append(node("legend", `待确认的${kindLabel}记录`));
      field.append(node("p", "下方为本轮用户原话，不是已核实事实。请整理为需要保留的内容；保存只追加记录，不改变客户阶段或正式台账。", "hint"));
      const text = node("textarea"); text.value = card.text; text.maxLength = 2000; text.required = true; text.rows = 4;
      text.placeholder = "填写经你核对的事实、行动或待确认事项（最多 2000 字）";
      const kind = choices({ followup: "跟进记录", action: "下一步行动", milestone: "里程碑" }, "followup");
      const due = node("input"); due.type = "date";
      const keepSource = node("input"); keepSource.type = "checkbox";
      const sourceLabel = labeled("同时保留本轮原话作为来源（最多 1200 字；修改草稿不会改变原话）", keepSource);
      sourceLabel.className = "chat-source-consent";
      const status = node("p", card.long_source ? "本轮原话超过 2000 字，请填写需要保留的摘要，不会自动截断为事实。" : "尚未保存。", "chat-assist-status");
      status.setAttribute("role", "status");
      const save = node("button", `确认保存到${kindLabel}`, "primary"); save.type = "submit";
      const revise = button("刷新档案版本", async () => {
        if (this.busy()) return;
        await this.action(field, status, async () => {
          const result = await this.chat.post(card.entity_type === "customer" ? "/api/chat/customer-context" : "/api/chat/project-context",
            card.entity_type === "customer" ? { entity_id: card.entity_id } : { message: card.name, selected_entity_id: card.entity_id });
          card.revision = result.revision;
          status.textContent = "已刷新档案版本，草稿保留；请核对后再次保存。";
        });
      });
      const actions = node("div", undefined, "chat-assist-actions"); actions.append(save, revise);
      actions.append(button("仅用于追问，不保存", async () => {
        if (this.busy() || !form.reportValidity()) return;
        document.getElementById("chat-input").value = `${card.name}：请结合以下补充继续回答。\n${text.value.trim()}${due.value ? "\n截止日期：" + due.value : ""}`;
        await this.chat.send();
      }));
      field.append(labeled("记录内容", text), labeled("记录类型", kind), labeled("截止日期（可选）", due), sourceLabel, actions, status);
      form.append(field); box.append(form);
      form.addEventListener("submit", event => {
        event.preventDefault(); if (!form.reportValidity()) return;
        void this.action(field, status, async () => {
          await this.chat.post("/api/chat/project-note", { confirmed: true, session_id: card.session_id,
            request_id: card.request_id, revision: card.revision, text: text.value.trim(), kind: kind.value, due: due.value,
            include_source_quote: keepSource.checked });
          status.textContent = `已保存到${kindLabel}档案，下次检索会带入最近记录；尚未纳入正式销售台账或销售行动简报。来源轮次和时间已保留。` + (keepSource.checked ? "已按勾选保留用户原话。" : "未额外保存原话。");
          save.disabled = true; revise.disabled = true; text.disabled = true; kind.disabled = true; due.disabled = true; keepSource.disabled = true;
        });
      });
      return box;
    }
    async loadPreferences() {
      if (!this.panel) return;
      this.panel.replaceChildren(node("p", "正在读取回答偏好…", "hint"));
      try { this.renderPreferences(await this.chat.post("/api/chat/preferences", {})); }
      catch (error) { this.panel.replaceChildren(node("p", error.message, "chat-assist-status error"), button("重新读取", () => void this.loadPreferences())); }
    }
    renderPreferences(view) {
      this.panel.replaceChildren(node("h3", "跨对话回答偏好"), node("p",
        "只记住你确认的表达偏好，不记录点击行为或推断个人画像。开启“应用回答偏好”后，这些设置会发送给所选对话模型；关闭后本轮不读取、不附带偏好。", "hint"));
      for (const [key, item] of Object.entries(view.catalog)) {
        const field = node("fieldset", undefined, "chat-assist-fieldset chat-preference-row");
        const select = choices({ "": "不记住", ...item.choices }, view.values[key]?.value || "");
        const status = node("span", "", "chat-assist-status"); status.setAttribute("role", "status");
        const apply = value => this.action(field, status, async () => {
          const result = await this.chat.post("/api/chat/preferences/save", { confirmed: true, revision: view.revision, key, value });
          view = result; select.value = value; this.syncSuggestions(result);
          status.textContent = value ? "已保存，下轮生效" : "已忘记；旧对话文字仍保留，可开始新对话";
        });
        field.append(labeled(item.label, select), button("保存", () => apply(select.value)), button("忘记", () => apply("")), status);
        this.panel.append(field);
      }
      this.panel.append(button("刷新偏好", () => void this.loadPreferences()), node("small", "完整聊天记录仍是临时会话；项目记录只有点击确认保存才会长期保留。"));
      this.controls();
    }
  }
  window.Agent4MarketChatCards = { create: chat => new Cards(chat) };
})();
