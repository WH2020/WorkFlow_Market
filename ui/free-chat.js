/* Plain dialogue stays tool-free; an optional local intent router opens governed forms. */
(() => {
  const $ = (id) => document.getElementById(id);
  const levels = ["off", "minimal", "low", "medium", "high", "xhigh", "max"];
  const labels = { off: "关闭", minimal: "最少", low: "较低", medium: "标准", high: "深入", xhigh: "极深", max: "最大" };
  class FreeChat {
    constructor({ api, getToken, routeWork, onNewChat, prepareMessage, confirmProjectChange }) {
      this.api = api; this.getToken = getToken;
      this.routeWork = routeWork; this.onNewChat = onNewChat;
      this.prepareMessage=prepareMessage;this.confirmProjectChange=confirmProjectChange;this.preparing=false;this.projectId="";
      this.session = null; this.version = 0; this.running = null; this.blocked = false; this.catalog = [];
      this.cards = window.Agent4MarketChatCards?.create(this);
      $("chat-form").addEventListener("submit", (event) => { event.preventDefault(); void this.send(); });
      $("chat-input").addEventListener("input", () => this.controls());
      $("chat-input").addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); void this.send(); }
      });
      $("chat-model").addEventListener("change", () => { this.renderThinking(); this.controls(); });
      $("chat-stop").addEventListener("click", () => void this.stop());
      $("chat-new").addEventListener("click", () => void this.newChat());
      document.querySelectorAll("[data-chat-prompt]").forEach(button => button.addEventListener("click", () => {
        if (this.running || this.preparing) return;
        $("chat-input").value = button.dataset.chatPrompt; this.controls(); $("chat-input").focus();
      }));
      window.addEventListener("pagehide", () => {
        if (this.session) void fetch("/api/chat/close", { method: "POST", keepalive: true,
          headers: { "Content-Type": "application/json", "X-Director-Token": this.getToken() || "" },
          body: JSON.stringify({ session_id: this.session }) }).catch(() => {});
        this.running?.controller.abort();
      });
    }
    status(text, error = false) { $("chat-status").textContent = text; $("chat-status").classList.toggle("error", error); }
    update(settings = {}) {
      const choices = (settings.providers || []).filter((provider) => provider.enabled !== false && provider.status === "configured")
        .flatMap((provider) => (provider.models || []).filter((item) => item.enabled !== false).map((item) => ({ ...item,
          key: `${provider.id}/${item.id}`, label: `${provider.name || provider.id} · ${item.display_name || item.id}`, api: provider.api })));
      const signature = JSON.stringify([settings.default_model, choices]);
      if (signature === this.signature) return;
      this.signature = signature; this.catalog = choices;
      const selected = $("chat-model").value || settings.default_model || choices[0]?.key || "";
      $("chat-model").replaceChildren();
      for (const item of choices) {
        const option = document.createElement("option"); option.value = item.key; option.textContent = item.label;
        $("chat-model").append(option);
      }
      if (!choices.length || this.session && !choices.some((item) => item.key === selected)) {
        const option = document.createElement("option"); option.value = this.session ? selected : "";
        option.textContent = this.session ? "本段对话的模型已不可用" : "尚未配置可用模型";
        $("chat-model").append(option);
      }
      $("chat-model").value = choices.some((item) => item.key === selected) || this.session ? selected : choices[0]?.key || "";
      if (!this.session) this.renderThinking();
      if (!this.running && !this.session) this.status(choices.length ? "准备好了，发送第一条消息吧。" : "请先点击“模型设置”配置一个可用模型。", !choices.length);
      this.controls();
    }
    renderThinking() {
      const item = this.catalog.find((entry) => entry.key === $("chat-model").value);
      const supported = item?.cli_reasoning ? levels.filter((level) => item.cli_reasoning.supported_efforts.includes(level === "off" ? "none" : level)) : item?.reasoning ? levels : ["off"];
      $("chat-thinking").replaceChildren();
      for (const level of supported) {
        const option = document.createElement("option"); option.value = level; option.textContent = labels[level];
        $("chat-thinking").append(option);
      }
      $("chat-thinking").value = item?.default_thinking_level || (item?.reasoning ? "medium" : "off");
    }
    controls() {
      const busy = Boolean(this.running)||this.preparing, hasModel = this.catalog.some((item) => item.key === $("chat-model").value);
      $("chat-model").disabled = busy || Boolean(this.session) || !this.catalog.length;
      $("chat-thinking").disabled = busy || Boolean(this.session) || !hasModel;
      $("chat-send").disabled = busy || this.blocked || (!hasModel && !this.routeWork) || !$("chat-input").value.trim() || !this.getToken();
      $("chat-input").disabled = busy;
      $("chat-stop").hidden = !this.running;
      $("chat-stop").disabled = Boolean(this.running?.stopping);
      $("chat-new").disabled = busy;
      document.querySelectorAll("#hub-chat-scope button, #hub-chat-scope input").forEach(control=>{control.disabled=busy;});
      this.cards?.controls();
    }
    message(role, text) {
      $("chat-welcome")?.remove();
      const article = document.createElement("article"); article.className = `free-chat-message ${role}`;
      const speaker = document.createElement("strong"); speaker.className = "free-chat-speaker"; speaker.textContent = role === "user" ? "你" : "助手";
      const content = document.createElement("div"); content.className = "free-chat-text"; content.textContent = text;
      article.append(speaker, content); $("chat-conversation").append(article); this.scroll();
      return { article, content };
    }
    scroll() { const area = $("chat-reading-area"); area.scrollTop = area.scrollHeight; }
    post(path, payload) { return this.api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }); }
    async newChat() {
      if (this.running||this.preparing) return;
      this.preparing=true;this.controls();
      try{
        if(this.session)await this.post("/api/chat/close", { session_id: this.session });
        this.session = null; this.version = 0; this.blocked = false;this.projectId="";
        this.onNewChat?.();
        $("chat-conversation").replaceChildren(); $("chat-input").value = "";
        this.renderThinking();this.status("新对话已准备好。可以选择模型后发送消息。");
      }catch(error){this.status(`暂时无法关闭对话：${error.message}`, true);}
      finally{this.preparing=false;this.controls();$("chat-input").focus();}
    }
    async stop() {
      const run = this.running;
      if (!run || run.stopping) return;
      run.stopping = true; this.controls(); this.status("正在停止生成…");
      if (this.session) {
        try { await this.post("/api/chat/cancel", { session_id: this.session, request_id: run.id }); }
        catch { this.blocked = true; run.controller.abort(); }
      } else { run.controller.abort(); this.blocked = true; }
    }
    async send() {
      const text = $("chat-input").value.trim();
      if (this.running || this.preparing || this.blocked || !text || !this.getToken()) return;
      if (text.length > 8000) { this.status("每条消息最多 8000 字。", true); return; }
      let projectSelection={},prepared={},contextId="";
      this.preparing=true;this.controls();this.status("正在准备问题与档案背景…");
      try{
        prepared=await this.prepareMessage?.(text)||{};
        if(prepared.blocked){this.status("请先选择项目，再发送问题。");return;}
        projectSelection=prepared.customer_selection?{customer_selection:prepared.customer_selection}:prepared.project_selection?{project_selection:prepared.project_selection}:{};
        const nextProject=contextId=(prepared.customer_selection||prepared.project_selection)?.entity_id||"";
        if(this.session&&this.version>0&&nextProject!==this.projectId){
          if(!await this.confirmProjectChange?.()){prepared.cancel?.();this.status("已取消切换，本轮问题未发送。原对话仍保留。");return;}
          await this.post("/api/chat/close",{session_id:this.session});
          this.session=null;this.version=0;this.projectId="";
          $("chat-conversation").replaceChildren();
        }
        prepared.commit?.();
      }catch(error){prepared.cancel?.();this.status(`档案资料检索未完成：${error.message}。可重试，或关闭档案背景后普通对话。`,true);return;}
      finally{this.preparing=false;this.controls();}
      if (this.routeWork?.(text)) {
        this.message("user", text); this.message("assistant", "已展开相应工作流。请核对背景、资料和模型，确认开始后运行。");
        $("chat-input").value = ""; this.controls(); return;
      }
      if (!$("chat-model").value) { this.status("普通问答需要先配置可用模型。", true); return; }
      const run = { id: crypto.randomUUID().replaceAll("-", ""), controller: new AbortController(), stopping: false, meta: false, done: false, knownError: false };
      this.running = run;
      const user = this.message("user", text), reply = this.message("assistant", "正在思考…");
      let answer = "", reader, timeout;
      $("chat-input").value = ""; this.controls(); this.status("正在生成回复…");
      try {
        timeout = setTimeout(() => run.controller.abort(), 195000);
        const response = await fetch("/api/chat/messages", { method: "POST", signal: run.controller.signal,
          headers: { "Content-Type": "application/json", "X-Director-Token": this.getToken() },
          body: JSON.stringify({ ...(this.session ? { session_id: this.session } : {}), request_id: run.id,
            version: this.version, message: text, requested_model: $("chat-model").value, requested_thinking_level: $("chat-thinking").value,
            ui_version: 1, use_preferences: $("chat-use-preferences")?.checked !== false, ...projectSelection }) });
        if (!response.ok) {
          run.knownError = true;
          const error = await response.json();
          if (["SESSION_EXPIRED", "RECIPIENT_CHANGED", "TURN_CONFLICT", "CONTEXT_LIMIT", "PROJECT_CHANGED"].includes(error.code)) this.blocked = true;
          throw new Error(error.error || "消息发送失败");
        }
        if (!response.body || !response.headers.get("Content-Type")?.startsWith("application/x-ndjson")) throw new Error("回复格式无效");
        reader = response.body.getReader();
        const decoder = new TextDecoder("utf-8", { fatal: true });
        let buffer = "";
        while (true) {
          const chunk = await reader.read();
          buffer += decoder.decode(chunk.value || new Uint8Array(), { stream: !chunk.done });
          if (buffer.length > 1024 * 1024) throw new Error("回复超过显示上限");
          let index;
          while ((index = buffer.indexOf("\n")) >= 0) {
            const line = buffer.slice(0, index); buffer = buffer.slice(index + 1);
            if (!line) continue;
            const event = JSON.parse(line);
            if (run.done) throw new Error("回复状态无效");
            if (event.type === "meta") {
              if (run.meta || !/^[a-f0-9]{32}$/u.test(event.session_id) || event.request_id !== run.id || event.version !== this.version || event.model !== $("chat-model").value) throw new Error("对话状态无效");
              run.meta = true; this.session = event.session_id;run.projectId=event.project_id||"";
              if(run.projectId!==contextId)throw new Error("档案范围校验失败");
              this.status(`${event.model_label} · 正在生成回复…`);
            } else if (event.type === "ui" && run.meta && !run.ui) {
              run.ui = true;
              const context = document.createElement("div"); context.className = "chat-assist-context";
              reply.article.insertBefore(context, reply.content); this.cards?.render(event.cards, context);
            } else if (event.type === "delta" && run.meta && typeof event.text === "string") {
              answer += event.text;
              if (answer.length > 128 * 1024) throw new Error("回复超过显示上限");
              const area = $("chat-reading-area"), follow = area.scrollHeight - area.scrollTop - area.clientHeight < 90;
              reply.content.textContent = answer; if (follow) this.scroll();
            } else if (event.type === "done" && run.meta && event.version === this.version + 1) {
              run.done = true; this.version = event.version;this.projectId=run.projectId;
              const area = $("chat-reading-area"), follow = area.scrollHeight - area.scrollTop - area.clientHeight < 90;
              this.cards?.render(event.cards, reply.article);
              if (follow) this.scroll();
              this.status(event.limited ? "回复已达到本轮长度上限，可以继续追问。" : "回复完成，可以继续对话。");
            } else if (event.type === "error") {
              run.knownError = true; throw new Error(event.error || "模型请求未完成");
            } else if (event.type !== "ping") throw new Error("回复格式无效");
          }
          if (chunk.done) { if (buffer.trim() || !run.done) throw new Error("连接中断，回复尚未完成"); break; }
        }
      } catch (error) {
        if (!run.knownError) this.blocked = true; // Lost terminal state cannot silently replay a committed turn.
        user.article.classList.add("failed"); reply.article.classList.add("failed");
        reply.content.textContent = answer || "本轮未收到完整回复。";
        const note = document.createElement("small"); note.className = "free-chat-message-note";
        note.textContent = this.blocked ? "对话状态需要重置，请点击“新对话”。" : "本轮未加入后续对话，可修改问题后重新发送。";
        reply.article.append(note);
        $("chat-input").value = text;
        this.status(error.name === "AbortError" ? "请求已中断，请开始新对话后重试。" : error.message, true);
      } finally {
        clearTimeout(timeout);
        if (!run.done) { run.controller.abort(); try { await reader?.cancel(); } catch { /* The connection may already be closed. */ } }
        this.running = null; this.controls(); $("chat-input").focus();
      }
    }
  }
  window.Agent4MarketFreeChat = { create: (options) => new FreeChat(options) };
})();
