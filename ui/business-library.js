/* Three-entry shell and persistent dossiers. All mutations use authenticated APIs. */
(() => {
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const labels = {sales:"销售",customer:"客户",project:"项目",files:"文件",wechat:"微信会话"};
  const kinds = {followup:"跟进",action:"行动",milestone:"里程碑"};
  const button = (text, action, id="", cls="secondary") => `<button type="button" class="${cls}" data-hub-action="${action}" data-id="${esc(id)}">${esc(text)}</button>`;
  function detectPlan(text) {
    const steps = [];
    const clauses = text.replace(/(?:但是|不过|而是|改为|然后|接着|之后)|(?:再|并且|并)(?=制作|生成|做|整理|分析|复盘|研究|调研|输出)/gu, "，").split(/[，,。；;！!？?\n]+/u);
    for (const clause of clauses) {
      const part = clause.trim();
      if (!part || /如何|怎么|怎样|什么是|为什么|能否解释|请解释|什么意思|是什么/u.test(part)) continue;
      const action = part.search(/制作|生成|做|准备|输出|整理|转成|改成|压缩成|解读|分析|提取|总结|复盘|回顾|研究|调研/u);
      const negative = part.search(/不要|不用|无需|不必|暂不|先不|不想|不需要|别/u);
      if (negative >= 0 && (action < 0 || negative < action)) continue;
      let intent = null;
      if (/(微信|群聊|聊天记录|会话).*(整理|复盘|总结|分析)|(整理|复盘|总结|分析).*(微信|群聊|聊天记录|会话)/u.test(part)) intent = "wechat-review";
      else if (/(制作|生成|做|准备|输出|整理|转成|改成|压缩成).*(ppt|演示文稿|幻灯片)/iu.test(part)) intent = "presentation-studio";
      else if (/(生成|制作|整理).*(周报|行动简报)/u.test(part)) intent = "weekly-deck";
      else if (/(解读|分析|提取|总结).*(招标|标书|采购文件)|(招标|标书|采购文件).*(解读|分析|提取|总结)/u.test(part)) intent = "bid-interpretation";
      else if (/(复盘|回顾).+|(客户|销售|进展).*(复盘|回顾)/u.test(part)) intent = "sales-review";
      else if (/(解读|分析|提取|总结).*(文档|文件|资料|pdf)/iu.test(part)) intent = "pdf-import";
      else if (/(研究|调研).*(行业|客户|竞品|趋势)/u.test(part)) intent = "industry-research";
      if (intent) {
        const existing = steps.at(-1)?.service_id === intent ? steps.at(-1) : null;
        if (existing) existing.request += "；" + part;
        else steps.push({service_id:intent, request:part});
      }
    }
    return steps;
  }
  function detectIntent(text) { const steps=detectPlan(text);return steps.length===1?steps[0].service_id:null; }

  class BusinessLibrary {
    constructor(bridge) {
      this.b=bridge; this.data=null; this.tab="customer"; this.id=""; this.detail="overview";
      this.query=""; this.archive="active"; this.owner=""; this.context=""; this.files=new Set(); this.sources=new Set();
      this.groupFilter=""; this.groupEntity=""; this.loading=null; this.pending=[];
      this.autoProject=true; this.projectMatch=null; this.projectChoice=null;
      this.mount();
    }
    mount() {
      const page=document.querySelector('[data-page="knowledge"]');
      const originals=document.createElement("div"); originals.id="hub-file-library";
      originals.append(...page.children); page.append(originals);
      const hub=document.createElement("div"); hub.id="business-library";page.prepend(hub);
      const wx=document.createElement("div"); wx.id="hub-wechat"; wx.hidden=true;page.append(wx);
      this.wxAnchors=[];
      for(const id of ["wechat-tool-panel","wechat-browser-panel"]) {const panel=$(id),anchor=document.createComment(id);panel.before(anchor);this.wxAnchors.push([panel,anchor]);}
      hub.addEventListener("click",event=>this.action(event));
      hub.addEventListener("input",event=>{if(event.target.id==="hub-query"){this.query=event.target.value;this.renderList();}});
      hub.addEventListener("change",event=>{
        if(event.target.id==="hub-owner"){this.owner=event.target.value;this.renderList();}
        if(event.target.id==="hub-archive"){this.archive=event.target.value;this.renderList();}
        if(event.target.dataset.hubSource){event.target.checked?this.sources.add(event.target.dataset.hubSource):this.sources.delete(event.target.dataset.hubSource);}
      });
      const dialog=document.createElement("dialog");dialog.id="hub-dialog";dialog.setAttribute("aria-label","业务档案编辑");document.body.append(dialog);
      dialog.addEventListener("click",event=>this.action(event));
      dialog.addEventListener("submit",event=>event.preventDefault());
      const context=document.createElement("section");context.id="hub-chat-scope";context.className="hub-context";
      $("chat-form").prepend(context);context.addEventListener("click",event=>this.action(event));this.renderContext();
      const filters=document.createElement("div");filters.className="hub-group-filters";
      filters.innerHTML='<label>业务分类<select id="hub-group-filter"><option value="">全部会话</option><option value="customer">客户群</option><option value="sales">销售群</option><option value="other">其他群</option><option value="unclassified">未分类群</option></select></label><label>关联档案<select id="hub-group-entity"><option value="">全部档案</option></select></label><small>仅筛选当前已加载的会话，不按群名推断分类；勾选范围独立保留。</small>';
      $("wechat-conversations").closest(".wechat-browser-grid").before(filters);
      $("hub-group-filter").onchange=()=>{this.groupFilter=$("hub-group-filter").value;this.b.renderWechat();};
      $("hub-group-entity").onchange=()=>{this.groupEntity=$("hub-group-entity").value;this.b.renderWechat();};
      const prepare=document.createElement("button");prepare.type="button";prepare.className="primary";prepare.id="hub-prepare-wechat";prepare.textContent="准备聊天记录（复制 → 解析副本）";
      prepare.onclick=async()=>{prepare.disabled=true;try{await this.b.prepareWechat();}finally{prepare.disabled=false;}};
      $("wechat-summary").before(prepare);
      const progress=document.createElement("section");progress.id="hub-pending";progress.className="panel";progress.hidden=true;
      $("chat-task-heading").after(progress);
      const back=document.createElement("div");back.className="hub-flow-return";
      back.innerHTML=button("← 继续对话","chat")+button("选择本次资料与背景","scope");
      const flowSelect=document.createElement("select");flowSelect.id="hub-flow-type";flowSelect.setAttribute("aria-label","调整工作流类型");flowSelect.innerHTML='<option value="sales-review">客户复盘</option><option value="pdf-import">资料解读</option><option value="bid-interpretation">招标文件解读</option><option value="presentation-studio">PPT 制作</option><option value="industry-research">行业研究</option><option value="wechat-review">微信整理</option><option value="weekly-deck">销售简报</option>';
      flowSelect.onchange=()=>{if(this.b.changeFlow?.(flowSelect.value))return;this.b.startFlow(flowSelect.value,this.lastRequest||"",[...this.files].map(id=>this.b.files().find(f=>f.library_id===id)).filter(Boolean));};back.append(flowSelect);
      back.addEventListener("click",event=>this.action(event));document.querySelector('[data-page="work"]').prepend(back);
      this.render();
    }
    post(payload){return this.b.api("/api/business-library",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...payload,revision:this.data?.revision,confirmed:true})});}
    async refresh(){
      if(this.loading)return this.loading;
      const generation=this.generation||0;
      this.loading=(async()=>{try{const data=await this.b.api("/api/business-library");if(generation!==(this.generation||0))return;this.data=data;this.error="";this.render();this.renderContext();this.b.renderWechat();}catch(e){this.error=e.message;this.render();}finally{this.loading=null;}})();return this.loading;
    }
    async save(payload){this.generation=(this.generation||0)+1;this.data=await this.post(payload);this.render();this.renderContext();this.b.renderWechat();return this.data;}
    entity(id=this.id){return this.data?.entities.find(e=>e.id===id);}
    name(id){return this.entity(id)?.name||"未分配";}
    async onView(view,old){
      const parent={home:"chat",work:"chat",tasks:"chat",weekly:"chat",tools:"knowledge",sales:"knowledge",projects:"knowledge",bids:"knowledge",outputs:"chat",schedules:"chat"}[view]||view;
      document.querySelectorAll('#sidebar nav .nav-item').forEach(n=>{n.classList.toggle("active",n.dataset.view===parent);n.toggleAttribute("aria-current",n.dataset.view===parent);});
      if(view==="knowledge" && (!this.data || old!==view))await this.refresh();
      if(view==="tools")for(const [panel,anchor] of this.wxAnchors)if(anchor.nextSibling!==panel)anchor.after(panel);
      else if(view==="knowledge"&&this.tab==="wechat")this.mountWechat();
    }
    mountWechat(){const target=$("hub-wechat");for(const [panel] of this.wxAnchors){if(panel.parentElement!==target)target.append(panel);panel.hidden=false;}}
    async openWechat(){this.tab="wechat";this.id="";this.b.show("knowledge");this.b.activateWechat();this.render();await this.refresh();await this.b.loadWechat();}
    render(){
      const hub=$("business-library");if(!hub)return;
      document.body.dataset.libraryTab=this.tab;
      hub.innerHTML=`<div class="page-heading"><div><p class="section-kicker">业务档案与资料</p><h1>资料库</h1><p>销售、客户、项目相互关联；文件与微信保留原始来源。</p></div>${button("刷新档案","refresh")}</div><div class="hub-tabs" role="tablist" aria-label="资料库分类">${Object.entries(labels).map(([key,label])=>`<button role="tab" type="button" aria-selected="${this.tab===key}" data-hub-action="tab" data-id="${key}">${label}</button>`).join("")}</div>`;
      $("hub-file-library").hidden=this.tab!=="files";
      $("hub-wechat").hidden=this.tab!=="wechat";
      if(this.error)hub.insertAdjacentHTML("beforeend",`<p class="hub-error" role="alert">${esc(this.error)}。可刷新重试；未改动原始资料。</p>`);
      if(!this.data){hub.insertAdjacentHTML("beforeend",'<p role="status">正在读取业务档案…</p>');return;}
      if(this.tab==="wechat"){this.mountWechat();return;}
      if(this.tab==="files"){
        hub.insertAdjacentHTML("beforeend",`<details class="panel"><summary>文件与业务对象关联</summary>${button("批量追加关联","batch-link")}<div class="hub-source-list">${this.sourceRows(this.fileSources())}</div></details>`);return;
      }
      if(this.id && this.entity()){hub.insertAdjacentHTML("beforeend",this.entityDetail());return;}
      hub.insertAdjacentHTML("beforeend",`<div class="hub-toolbar"><label class="grow">搜索档案<input id="hub-query" value="${esc(this.query)}" placeholder="名称、团队或关键词" maxlength="100"></label><label>负责人<select id="hub-owner">${this.ownerOptions(this.owner,"全部")}</select></label><label>状态<select id="hub-archive">${[["active","在用"],["archived","已归档"],["all","全部"]].map(([v,l])=>`<option value="${v}" ${this.archive===v?"selected":""}>${l}</option>`).join("")}</select></label>${button("＋ 新增"+labels[this.tab],"new","","primary")}</div><section class="panel" id="hub-list"></section><p class="hint">这里保存的档案修改和行动记录尚未纳入正式销售台账或销售行动简报。关联历史来源的档案保留原编号；历史负责人需核对关联。</p>`);
      this.renderList();
    }
    renderList(){
      const box=$("hub-list");if(!box||!this.data)return;
      const rows=this.data.entities.filter(e=>e.type===this.tab&&(this.archive==="all"||!!e.archived===(this.archive==="archived"))&&(!this.owner||e.owner===this.owner||e.type==="sales"&&e.id===this.owner)&&[e.name,e.aliases,e.team,e.industry,e.description,this.name(e.owner)].join(" ").toLowerCase().includes(this.query.toLowerCase()));
      box.innerHTML=rows.length?rows.map(e=>`<article class="hub-entity-row"><div class="hub-avatar">${esc(e.name.slice(0,1))}</div><div class="grow"><h3>${esc(e.name)}</h3><p>${esc(e.type==="sales"?e.team||"团队待补充":e.type==="project"?this.name(e.customer)+" · "+this.name(e.owner):e.industry||"行业待补充")} · ${e.source_ref?"关联历史来源":"本地档案"}</p><small>${esc(e.description)}</small></div><span class="hub-badge">${esc(e.archived?"已归档":e.stage||"待补充")}</span>${button("查看档案","open",e.id)}</article>`).join(""):'<p class="empty">没有符合筛选的档案，可调整筛选或新增。</p>';
    }
    ownerOptions(value,empty="暂不分配"){return `<option value="">${empty}</option>`+(this.data?.entities||[]).filter(e=>e.type==="sales"&&(!e.archived||e.id===value)).map(e=>`<option value="${esc(e.id)}" ${value===e.id?"selected":""}>${esc(e.name)}</option>`).join("");}
    customerOptions(value){return '<option value="">暂不关联</option>'+this.data.entities.filter(e=>e.type==="customer"&&(!e.archived||e.id===value)).map(e=>`<option value="${esc(e.id)}" ${value===e.id?"selected":""}>${esc(e.name)}</option>`).join("");}
    entityDetail(){
      const e=this.entity(), tasks=this.b.tasks().filter(t=>t.library_context?.scope_ids?.includes(e.id)), related=this.data.entities.filter(x=>x.id!==e.id&&(x.owner===e.id||x.customer===e.id||x.id===e.owner||x.id===e.customer));
      let body="";
      if(this.detail==="overview")body=`<div class="hub-grid"><section class="panel"><h2>基本信息</h2><dl class="hub-kv"><dt>${e.type==="sales"?"团队":"负责人"}</dt><dd>${esc(e.type==="sales"?e.team||"待补充":this.name(e.owner))}</dd><dt>阶段</dt><dd>${esc(e.stage)}</dd>${e.original_owner?`<dt>历史台账负责人</dt><dd>${esc(e.original_owner)}（未自动匹配）</dd>`:""}<dt>说明</dt><dd>${esc(e.description||"暂无")}</dd></dl><p class="hint">${e.source_ref?"原始来源："+esc(e.source_ref):"本地新建档案；不是正式销售台账记录。"}</p>${e.source_changed?'<p class="hub-error">来源已有更新：未手动改写的字段已同步，手动覆盖仍保留。请核对来源与本地修改；保存其他字段不会消除本提示。</p>':""}</section><section class="panel"><h2>业务关系</h2>${related.map(x=>`<div class="hub-relation"><span>${labels[x.type]}</span>${button(x.name,"open",x.id,"text-button")}</div>`).join("")||'<p class="hint">暂无关联对象。</p>'}${e.type==="customer"&&!e.archived?button("为此客户新建项目","new-project",e.id):""}<p class="hint">关联关系不等于模型读取授权。</p></section></div>${e.type==="customer"?`<section class="panel"><div class="panel-heading"><h2>联系人</h2>${!e.archived?button("新增联系人","contact"):""}</div>${(e.contacts||[]).map(c=>`<div class="hub-relation"><b>${esc(c.name)}</b><span>${esc(c.role)} · ${esc(c.note)}</span></div>`).join("")||'<p class="hint">暂无联系人。</p>'}</section>`:""}`;
      if(this.detail==="records")body=`<section class="panel"><div class="panel-heading"><h2>跟进、行动与里程碑</h2>${!e.archived?button("新增记录","record","","primary"):""}</div>${(e.records||[]).map(r=>`<article class="hub-record"><div class="grow"><span class="hub-badge">${kinds[r.kind]}${r.done?" · 已完成":""}</span><p>${esc(r.text)}</p><small>${esc(r.created_at.slice(0,10))} · ${esc(this.name(r.owner))}${r.due?" · 截止 "+esc(r.due):""} · ${esc(r.source)}</small></div>${r.task_id?button("来源工作","task",r.task_id):""}${r.kind!=="followup"&&!e.archived?button(r.done?"重新打开":"标记完成","toggle",r.id):""}</article>`).join("")||'<p class="empty">暂无记录。</p>'}</section><details class="panel"><summary>变更历史（${e.history?.length||0}）</summary>${(e.history||[]).map(h=>`<div class="hub-history"><b>${esc(h.operation)}</b><small>${esc(h.at)}</small><p>${esc(Object.keys(h.after||{}).map(k=>`${k}：${this.displayField(k,h.before?.[k])} → ${this.displayField(k,h.after[k])}`).join("；"))}</p></div>`).join("")}</details>`;
      if(this.detail==="sources")body=`<section class="panel"><div class="panel-heading"><h2>关联文件与微信群</h2>${button("关联已有资料","entity-sources")}</div>${this.sourceRows(this.allSources().filter(f=>(this.data.links[f.id]||[]).includes(e.id)))}</section><p class="hint">这里只保存引用。微信原文仍按原有保留期限清理；过期群聊不会自动重读或复制。</p>`;
      if(this.detail==="work")body=`<section class="panel"><h2>工作与成果</h2>${tasks.map(t=>`<article class="hub-record"><div class="grow"><b>${esc(t.service_id)}</b><p>${esc(t.request?.slice(0,140))}</p><small>${esc(t.task_id)} · ${esc(t.display_status||t.status)} · ${(t.artifacts||[]).length} 份产物</small></div>${button("查看工作","task",t.task_id)}${t.status==="completed"&&t.library_context?.entity_id===e.id&&!e.archived?button("核对后保存行动","task-record",t.task_id):""}</article>`).join("")||'<p class="empty">暂无围绕此档案开展的工作。</p>'}</section>`;
      return button("← 返回"+labels[e.type]+"列表","back")+`<div class="hub-entity-heading"><div><p class="section-kicker">${labels[e.type]}档案</p><h2>${esc(e.name)}</h2><span class="hub-badge">${e.archived?"已归档":esc(e.stage)}</span></div><div class="hub-actions">${!e.archived&&!e.source_unavailable?button("编辑档案","edit")+button("围绕此对象发起对话","entity-chat",e.id,"primary"):""}${button(e.archived?"恢复档案":"归档","archive")}</div></div><div class="hub-tabs">${[["overview","概况"],["records","跟进与行动"],["sources","关联资料"],["work","工作与成果"]].map(([k,l])=>`<button type="button" aria-pressed="${this.detail===k}" data-hub-action="detail" data-id="${k}">${l}</button>`).join("")}</div>${body}`;
    }
    displayField(key,value){return ["owner","customer"].includes(key)?this.name(value):value??"未填写";}
    fileSources(){return this.b.files().filter(f=>f.status!=="archived").map(f=>({id:"file:"+f.library_id,name:f.title,kind:"文件",file:f}));}
    allSources(){return [...this.fileSources(),...this.b.wechatRows().filter(g=>g.chat_type==="group").map(g=>({id:"wechat:"+g.conversation_id,name:g.display_name,kind:"微信群"}))];}
    sourceRows(list){return list.length?list.map(f=>`<div class="hub-source-row"><input type="checkbox" aria-label="选择${esc(f.name)}" data-hub-source="${esc(f.id)}" ${this.sources.has(f.id)?"checked":""}><div class="grow"><b>${esc(f.name)}</b><p>${esc(f.kind)} · ${(this.data.links[f.id]||[]).map(id=>button(this.name(id),"open",id,"text-button")).join(" / ")||"未关联"}</p></div>${button("管理关联","source-link",f.id)}${f.file?button("带入对话","use-file",f.file.library_id):""}</div>`).join(""):'<p class="empty">暂无关联资料。微信群须先在“微信会话”中加载，过期内容不可读取。</p>';}
    dialog(title,html,action){$("hub-dialog").innerHTML=`<form id="hub-form"><div class="panel-heading"><h2>${esc(title)}</h2>${button("取消","close")}</div>${html}<p id="hub-dialog-error" class="hub-error" role="alert"></p><div class="hub-actions">${button("确认保存",action,"","primary")}</div></form>`;$("hub-dialog").showModal();}
    edit(type,id="",customer=""){
      this.editing=id;this.editType=type;this.createdWorkspace=null;const e=this.entity(id)||{name:"",owner:this.entity(customer)?.owner||"",customer,stage:"待补充"};
      this.dialog((id?"编辑":"新增")+labels[type]+"档案",`<label>名称<input id="hub-name" required maxlength="80" value="${esc(e.name)}"></label>${type==="sales"?`<label>团队<input id="hub-team" maxlength="128" value="${esc(e.team)}"></label>`:`<label>负责人<select id="hub-edit-owner">${this.ownerOptions(e.owner)}</select></label>`}${type==="customer"?`<label>行业<input id="hub-industry" maxlength="128" value="${esc(e.industry)}"></label>`:""}${type==="project"?`<label>所属客户<select id="hub-customer">${this.customerOptions(e.customer)}</select></label><label>项目类型<select id="hub-project-type"><option ${e.project_type==="普通项目"?"selected":""}>普通项目</option><option ${e.project_type==="投标项目"?"selected":""}>投标项目</option></select></label>`:""}<label>阶段 / 状态<input id="hub-stage" maxlength="128" value="${esc(e.stage)}"></label><label>说明<textarea id="hub-description" maxlength="1000">${esc(e.description)}</textarea></label><p class="hint">保存到本地扩展档案，不直接改写历史销售台账。新项目会同时建立项目空间。</p>`,"save-entity");
      if(type==="project")$("hub-name").closest("label").insertAdjacentHTML("afterend",`<label>简称 / 别名<input id="hub-aliases" maxlength="128" value="${esc(e.aliases||"")}" placeholder="多个简称用逗号分隔，用于对话检索"></label>`);
    }
    recordDialog(taskId=""){
      this.recordTask=taskId;const e=this.entity();
      this.dialog(taskId?"核对工作结果后保存行动":"新增记录",`<p>写入：${esc(e.name)}${taskId?" · 来源 "+esc(taskId):""}</p><label>类型<select id="hub-record-kind">${Object.entries(kinds).map(([v,l])=>`<option value="${v}" ${taskId&&v==="action"?"selected":""}>${l}</option>`).join("")}</select></label><label>内容<textarea id="hub-record-text" required maxlength="2000" placeholder="请填写已核对的跟进事实或下一步行动；不会自动提取为事实。"></textarea></label><label>负责人<select id="hub-record-owner">${this.ownerOptions(e.owner)}</select></label><label>截止日期<input id="hub-record-due" type="date"></label><p class="hint">保存到业务档案，尚未纳入正式销售台账或销售行动简报；不自动改变阶段或对外发送。</p>`,"save-record");
    }
    linksDialog(refs,append=false){this.linkRefs=refs;this.appendLinks=append;this.dialog(append?"批量追加关联":"管理资料关联",`<p>${refs.length} 份资料。${append?"保留已有关系，只追加。":"取消勾选会解除引用，不删除原件。"}</p><div class="hub-options">${this.data.entities.map(e=>`<label><input type="checkbox" data-link-id="${esc(e.id)}" ${!append&&(this.data.links[refs[0]]||[]).includes(e.id)?"checked":""}>${labels[e.type]} · ${esc(e.name)}</label>`).join("")}</div>`,"save-links");}
    renderContext(){
      const box=$("hub-chat-scope");if(!box)return;const e=this.entity(this.context),match=this.projectMatch;
      box.innerHTML=`<span>${e?esc(labels[e.type]+"："+e.name):match?.project?esc("项目："+match.project.name):"未关联档案"}</span><span>工作流已选 ${this.files.size} 份资料</span>${button("选择资料与背景","scope")}${button("清空范围","clear-scope","","text-button")}<label><input id="hub-auto-project" type="checkbox" ${this.autoProject?"checked":""}>对话使用档案背景</label><small>${!this.autoProject?"本轮普通问答不读取档案。":e?.type==="customer"?"普通问答使用所选客户的基本信息与最近 8 条记录；切换客户请重新选择。":e?.type==="sales"?"销售人员档案仅供工作流使用；普通问答可按名称检索项目。":"按名称检索项目，只带入匹配档案与最近 8 条记录。"} 文件正文、微信原文未读取；已选文件仅供任务确认使用。</small>`;
      $("hub-auto-project").onchange=()=>{this.autoProject=$("hub-auto-project").checked;this.projectMatch=null;this.projectChoice=null;this.renderContext();};
      if(match?.status==="ambiguous")box.insertAdjacentHTML("beforeend",`<div class="hub-project-matches" role="status"><b>请确认你说的是哪个项目（${match.candidate_count} 个候选）</b>${match.candidates.map(p=>`<div>${button(p.name,"choose-project",p.entity_id)}<small>${esc(p.customer||"未关联客户")} · ${esc(p.owner||"未分配负责人")}</small></div>`).join("")}${match.candidate_count>12?'<p>仅显示前 12 个，请补充完整名称或在资料库手动选择。</p>':""}<small>尚未把本轮问题或项目资料发送给模型。</small></div>`);
      if(match?.status==="not_found"&&match.showMissing)box.insertAdjacentHTML("beforeend",`<small>未找到明确项目，不会套用其他项目的信息。可补充完整名称，或${button("前往项目资料库","project-library","","text-button")}。</small>`);
      if(match?.status==="matched")box.insertAdjacentHTML("beforeend",`<small>本轮背景：${match.project.entity_type==="customer"?"客户":"项目"}档案 · ${esc(match.project.name)}；实际读取范围会随回复展示。</small>`);
      if($("chat-input").disabled)box.querySelectorAll("button,input").forEach(control=>{control.disabled=true;});
    }
    async prepareChat(text){
      if(!this.autoProject)return {};
      const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
      try{
        const current=this.entity(this.context),choice=this.projectChoice?.text===text?this.projectChoice.id:"";
        const customer = current?.type === "customer";
        const result=await this.b.api(customer?"/api/chat/customer-context":"/api/chat/project-context",{method:"POST",signal:controller.signal,headers:{"Content-Type":"application/json"},body:JSON.stringify(customer?{entity_id:current.id}:{message:text,current_entity_id:current?.type==="project"?current.id:"",...(choice?{selected_entity_id:choice}:{})})});
        if(customer)result.project=result.customer;
        const previousMatch=this.projectMatch;
        this.projectMatch={...result,showMissing:/项目/u.test(text)};this.projectQuery=text;
        if(result.status==="ambiguous"){this.renderContext();return {blocked:true};}
        if(result.status==="matched"){
          if(!this.data||!this.entity(result.project.entity_id))await this.refresh();
        }
        return {
          ...(result.status==="matched"?{[customer?"customer_selection":"project_selection"]:{entity_id:result.project.entity_id,revision:result.revision}}:{}),
          commit:()=>{
            if(result.status==="matched"){
              this.b.selectProject(result.project.workspace_id||"project-default");
              if(this.context!==result.project.entity_id)this.files.clear();
              this.context=result.project.entity_id;this.b.clearLegacyContext();
            }else if(current?.type==="project"){
              this.b.selectProject("project-default");this.context="";this.files.clear();this.b.clearLegacyContext();
            }
            this.projectChoice=null;this.renderContext();
          },
          cancel:()=>{this.projectMatch=previousMatch;this.projectChoice=null;this.renderContext();}
        };
      }finally{clearTimeout(timer);}
    }
    selection(){return this.data&&(this.context||this.files.size)?{library_selection:{entity_id:this.context,revision:this.data.revision,file_ids:[...this.files]}}:{};}
    scopeDialog(){this.dialog("本次工作的资料与背景",`<label>关联对象<select id="hub-context"><option value="">不关联</option>${this.data.entities.filter(e=>!e.archived&&!e.source_unavailable).map(e=>`<option value="${esc(e.id)}" ${this.context===e.id?"selected":""}>${labels[e.type]} · ${esc(e.name)}</option>`).join("")}</select></label><p class="hint">文件需逐项选择，关联档案不会自动勾选其全部资料。本地正文目前通过受控 PDF 工具读取，其他格式仅作为来源引用；不支持的格式请先转换为 PDF。微信群请单独选择范围。</p><div class="hub-options">${this.fileSources().map(f=>`<label><input type="checkbox" data-context-file="${esc(f.file.library_id)}" ${this.files.has(f.file.library_id)?"checked":""}>${esc(f.name)}</label>`).join("")}</div>`,"save-scope");}
    route(text){
      const steps=detectPlan(text);if(!steps.length)return false;
      this.lastRequest=text;
      if(this.b.reviewPlan&&steps.every(s=>!["wechat-review","bid-interpretation"].includes(s.service_id))){this.b.reviewPlan(steps,text);return true;}
      if(steps.length>1){this.b.note("这项需求涉及专用资料授权，请先分别核对微信或投标范围，再发起后续任务。",true);return true;}
      $("hub-flow-type").value=steps[0].service_id;this.b.startFlow(steps[0].service_id,text,[...this.files].map(id=>this.b.files().find(f=>f.library_id===id)).filter(Boolean));return true;
    }
    filterGroups(rows){return rows.filter(g=>(!this.groupFilter||(g.chat_type==="group"&&(this.groupFilter==="unclassified"?!this.data?.groups["wechat:"+g.conversation_id]:this.data?.groups["wechat:"+g.conversation_id]===this.groupFilter)))&&(!this.groupEntity||(this.data?.links["wechat:"+g.conversation_id]||[]).includes(this.groupEntity)));}
    decorateGroup(card,row){
      if(row.chat_type!=="group")return;
      const controls=document.createElement("div");controls.className="hub-group-actions";
      const select=document.createElement("select");select.setAttribute("aria-label",row.display_name+"业务分类");select.innerHTML='<option value="">未分类</option><option value="customer">客户群</option><option value="sales">销售群</option><option value="other">其他群</option>';
      select.value=this.data?.groups["wechat:"+row.conversation_id]||"";
      select.onchange=async()=>{if(!select.value)return;select.disabled=true;try{if(!this.data)await this.refresh();await this.save({operation:"classify",sources:["wechat:"+row.conversation_id],category:select.value});}catch(e){this.b.note(e.message,true);this.b.renderWechat();}};
      const link=document.createElement("button");link.type="button";link.className="text-button";link.textContent="关联档案";link.onclick=async()=>{if(!this.data)await this.refresh();if(this.data)this.linksDialog(["wechat:"+row.conversation_id]);};
      controls.onclick=event=>event.stopPropagation();controls.append(select,link);card.append(controls);
    }
    update(){
      const picker=$("hub-group-entity"),signature=JSON.stringify(this.data?.entities.map(e=>[e.id,e.name]));
      if(signature!==this.pickerSignature&&this.data){this.pickerSignature=signature;picker.innerHTML='<option value="">全部档案</option>'+this.data.entities.map(e=>`<option value="${esc(e.id)}">${labels[e.type]} · ${esc(e.name)}</option>`).join("");picker.value=this.groupEntity;}
      const pending=[...new Map([...this.pending,...(this.b.pendingRequests?.()||[])].map(r=>[r.request_id,r])).values()].filter(r=>!this.b.tasks().some(t=>t.task_id===r.request_id));$("hub-pending").hidden=!pending.length;
      $("hub-pending").innerHTML='<h2>等待智能核心接手</h2>'+pending.map(r=>`<p>${esc(r.request_id)} · ${esc(r.service_id)} · 请求已保存；尚未开始运行</p>`).join("");
      if(this.detail==="work"&&this.id)this.render();
    }
    created(request){this.pending.push(request);this.update();this.b.show("tasks");}
    async action(event){
      const btn=event.target.closest("[data-hub-action]");if(!btn)return;const a=btn.dataset.hubAction,id=btn.dataset.id;btn.disabled=true;
      if($("chat-input").disabled&&["choose-project","scope","save-scope","clear-scope","entity-chat","use-file"].includes(a)){btn.disabled=false;return;}
      try{
        if(a==="close")$("hub-dialog").close();
        else if(a==="refresh")await this.refresh();
        else if(a==="tab"){this.tab=id;this.id="";this.query="";this.owner="";this.archive="active";this.sources.clear();if(id==="wechat")await this.openWechat();else this.render();}
        else if(a==="open"){const e=this.entity(id);if(!e)throw Error("档案不存在，请刷新。");this.id=id;this.tab=e.type;this.detail="overview";this.owner="";this.query="";this.b.show("knowledge");this.render();}
        else if(a==="back"){this.id="";this.render();}
        else if(a==="detail"){this.detail=id;this.sources.clear();this.render();}
        else if(a==="new")this.edit(this.tab);
        else if(a==="new-project")this.edit("project","",id);
        else if(a==="edit")this.edit(this.entity().type,this.id);
        else if(a==="save-entity"){
          if(!$("hub-form").reportValidity())return;
          const fields={name:$("hub-name").value.trim(),stage:$("hub-stage").value.trim(),description:$("hub-description").value.trim()};
          for(const [field,dom] of [["aliases","hub-aliases"],["owner","hub-edit-owner"],["customer","hub-customer"],["team","hub-team"],["industry","hub-industry"],["project_type","hub-project-type"]])if($(dom))fields[field]=$(dom).value;
          if(!fields.name)throw Error("名称不能为空。");
          if(this.editType==="project"&&!this.editing){
            const project=this.createdWorkspace||(this.createdWorkspace=await this.b.createProject(fields.name,fields.description));if(this.loading)await this.loading;await this.refresh();
            this.editing=this.data.entities.find(e=>e.workspace_id===project.project_id)?.id;
            if(!this.editing)throw Error("项目空间已创建，但档案暂未加载。请取消并刷新，不要重复新建。");
          }
          const result=await this.save({operation:"save",id:this.editing||undefined,type:this.editType,fields});this.id=result.saved_id;this.tab=this.editType;this.detail="overview";$("hub-dialog").close();this.render();
        }
        else if(a==="archive"){
          const e=this.entity();if(!await this.b.confirm({title:(e.archived?"恢复":"归档")+e.name,message:e.type==="project"?"项目空间状态将同步更新。归档停用其定时任务，恢复不会自动重新启用；已有资料和记录保留。":"归档保留记录和关联，不自动转交负责人；之后可恢复。"}))return;
          if(e.type==="project"){await this.b.projectStatus(e.workspace_id,e.archived?"active":"archived");await this.refresh();}else await this.save({operation:e.archived?"restore":"archive",id:e.id});
        }
        else if(a==="contact")this.dialog("新增联系人",'<label>姓名<input id="hub-contact-name" required maxlength="80"></label><label>角色<input id="hub-contact-role" maxlength="100"></label><label>备注<input id="hub-contact-note" maxlength="200"></label>',"save-contact");
        else if(a==="save-contact"){if(!$("hub-form").reportValidity())return;await this.save({operation:"contact",id:this.id,name:$("hub-contact-name").value,role:$("hub-contact-role").value,note:$("hub-contact-note").value});$("hub-dialog").close();}
        else if(a==="record"||a==="task-record")this.recordDialog(a==="task-record"?id:"");
        else if(a==="save-record"){if(!$("hub-form").reportValidity())return;await this.save({operation:"record",id:this.id,kind:$("hub-record-kind").value,text:$("hub-record-text").value,owner:$("hub-record-owner").value,due:$("hub-record-due").value,task_id:this.recordTask});$("hub-dialog").close();this.b.note("已保存到业务档案；尚未纳入正式销售台账或销售行动简报。");}
        else if(a==="toggle"){const r=this.entity().records.find(r=>r.id===id);await this.save({operation:"toggle",id:this.id,record_id:id,done:!r.done});}
        else if(a==="source-link")this.linksDialog([id]);
        else if(a==="batch-link"){if(!this.sources.size)throw Error("请先勾选资料。");this.linksDialog([...this.sources],true);}
        else if(a==="save-links"){await this.save({operation:"link",sources:this.linkRefs,entity_ids:[...$("hub-dialog").querySelectorAll('[data-link-id]:checked')].map(n=>n.dataset.linkId),append:this.appendLinks});$("hub-dialog").close();}
        else if(a==="entity-sources"){this.dialog("关联已有资料",`<div class="hub-options">${this.allSources().map(f=>`<label><input type="checkbox" data-entity-source="${esc(f.id)}" ${(this.data.links[f.id]||[]).includes(this.id)?"checked":""}>${esc(f.name)}</label>`).join("")}</div><p class="hint">只调整本档案引用，其他档案的关系保持不变。</p>`,"save-entity-sources");}
        else if(a==="save-entity-sources"){
          const choices=[...$("hub-dialog").querySelectorAll('[data-entity-source]')].map(n=>({id:n.dataset.entitySource,checked:n.checked}));
          await this.save({operation:"entity-links",id:this.id,sources:choices.map(c=>c.id),selected:choices.filter(c=>c.checked).map(c=>c.id)});$("hub-dialog").close();
        }
        else if(a==="entity-chat"){this.context=id;this.files.clear();this.b.clearLegacyContext();this.b.selectProject(this.entity(id)?.workspace_id||"project-default");this.b.show("chat");this.renderContext();$("chat-input").focus();}
        else if(a==="use-file"){this.files.add(id);this.b.show("chat");this.renderContext();}
        else if(a==="scope"){if(!this.data)await this.refresh();if(this.data)this.scopeDialog();}
        else if(a==="save-scope"){const ids=[...$("hub-dialog").querySelectorAll('[data-context-file]:checked')].map(n=>n.dataset.contextFile);if(ids.length>20)throw Error("每次最多选择 20 份资料。");this.context=$("hub-context").value;this.files=new Set(ids);this.b.clearLegacyContext();this.b.selectProject(this.entity(this.context)?.workspace_id||"project-default");$("hub-dialog").close();this.renderContext();}
        else if(a==="clear-scope"){this.context="";this.files.clear();this.projectMatch=null;this.projectChoice=null;this.b.clearLegacyContext();this.renderContext();}
        else if(a==="choose-project"){if($("chat-input").value.trim()!==this.projectQuery)throw Error("问题已修改，请重新发送以检索项目。");this.projectChoice={text:this.projectQuery,id};await this.b.sendChat();}
        else if(a==="project-library"){this.tab="project";this.id="";this.b.show("knowledge");await this.refresh();}
        else if(a==="chat")this.b.show("chat");
        else if(a==="task")this.b.openTask(id);
      }catch(error){if($("hub-dialog").open)$("hub-dialog-error").textContent=error.message;this.b.note(error.message,true);}
      finally{btn.disabled=false;}
    }
  }
  window.Agent4MarketBusinessLibrary={create:bridge=>new BusinessLibrary(bridge),detectIntent,detectPlan};
})();
