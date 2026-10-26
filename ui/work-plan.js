/* Reviewed chat requirements travel through ordinary task requests. Nothing auto-starts. */
(() => {
  const $ = id => document.getElementById(id);
  const services = {"sales-review":"客户复盘", "industry-research":"行业研究", "pdf-import":"资料解读", "presentation-studio":"PPT 制作", "weekly-deck":"销售简报"};
  const base = id => id.replace(/-(readonly|quick)$/u, "");
  const el = (tag, text, cls) => { const node=document.createElement(tag); if(text)node.textContent=text;if(cls)node.className=cls;return node; };
  const button = (text, action) => { const node=el("button",text,"secondary");node.type="button";node.onclick=action;return node; };
  const copy = value => JSON.parse(JSON.stringify(value));
  function conversationBrief(messages, request) {
    const requirements=messages.filter(m=>m.role==="user").map((m,i)=>`${i+1}. ${m.text}`);
    const suggestion=messages.findLast(m=>m.role==="assistant")?.text;
    return [requirements.length?`此前的用户要求（请删除已取消或过时的内容）：\n${requirements.join("\n")}`:"",
      `本次要求：${request}`, suggestion?`助手最近的建议（待核对，不视为已确认事实）：\n${suggestion}`:""].filter(Boolean).join("\n\n");
  }
  class WorkPlan {
    constructor(bridge) {
      this.b=bridge;this.draft=null;this.records=[];this.generation=0;this.continuing=false;
      this.cards=el("section",null,"work-plans");this.cards.id="chat-work-plans";this.cards.setAttribute("aria-label","连续工作计划");this.cards.hidden=true;
      $("chat-work-progress").before(this.cards);
      this.editor=el("section",null,"panel work-plan-editor");this.editor.id="task-handoff-editor";this.editor.hidden=true;
      const manual=button("整理为任务",()=>{
        if($("chat-input").disabled)return;
        const text=$("chat-input").value.trim()||"根据前面的讨论整理任务";
        const steps=window.Agent4MarketBusinessLibrary.detectPlan(text);
        this.start(steps.length&&steps.every(s=>services[s.service_id])?steps:[{service_id:"sales-review",request:text}],text,true);
      });
      manual.id="chat-to-task";$("chat-new").before(manual);
    }
    clearDraft() { this.generation++;this.draft=null;this.editor.hidden=true;this.render(); }
    changeService(service) {
      const d=this.draft;if(!d)return false;
      if(d.previous||!services[service]){
        $("hub-flow-type").value=d.steps[d.step].service_id;
        this.b.note("要改为计划外的工作，请先选择“不带入草稿，单独创建任务”。",true);return true;
      }
      d.steps[d.step].service_id=service;this.render();this.open();return true;
    }
    start(steps, text, manual=false) {
      const messages=this.b.messages();
      if(!manual&&steps.length===1&&!messages.length){this.clearDraft();this.b.open(steps[0].service_id,text);return;}
      if(steps.length>3){this.b.note("一次最多整理 3 步，请先拆出当前最需要的工作。",true);return;}
      this.generation++;
      this.draft={schema:1,plan_id:crypto.randomUUID().replaceAll("-",""),steps:copy(steps),step:0,
        scope_id:this.b.scope(),brief:conversationBrief(messages,text),previous_result:"",previous:null};
      this.editor.hidden=true;this.render();
      if(steps.length===1&&!manual)this.open();
      else {this.b.showChat();this.cards.scrollIntoView({block:"nearest"});}
    }
    editSteps(container) {
      const d=this.draft;
      d.steps.forEach((step,index)=>{
        const row=el("div",null,"work-plan-step"),label=el("label",`第 ${index+1} 步`),select=el("select");select.setAttribute("aria-label",`第 ${index+1} 步工作类型`);
        for(const [id,name] of Object.entries(services)){const option=el("option",name);option.value=id;select.append(option);}select.value=step.service_id;
        select.onchange=()=>{step.service_id=select.value;this.editor.hidden=true;};
        const input=el("textarea");input.rows=2;input.value=step.request;input.setAttribute("aria-label",`第 ${index+1} 步要求`);
        input.oninput=()=>{step.request=input.value;};label.append(select);row.append(label,input);
        if(d.steps.length>1)row.append(button("移除这一步",()=>{d.steps.splice(index,1);this.editor.hidden=true;this.render();}));
        container.append(row);
      });
      if(d.steps.length<3)container.append(button("添加一步",()=>{d.steps.push({service_id:"presentation-studio",request:"根据上一步结果制作 PPT"});this.editor.hidden=true;this.render();}));
    }
    render() {
      this.cards.replaceChildren();
      if(this.draft){
        const card=el("article",null,"panel work-plan-draft");card.append(el("h3",this.draft.previous?"下一步已准备，等待核对":"先核对这次要做的工作"));
        if(!this.draft.previous)this.editSteps(card);
        else card.append(el("p",`第 ${this.draft.step+1} 步：${services[this.draft.steps[this.draft.step].service_id]}`));
        card.append(el("p","每一步单独启动；完成后可以带着结果准备下一步。草稿尚未保存。","hint"),
          button("核对需求并打开任务",()=>this.open()),button("放弃草稿",()=>this.clearDraft()));this.cards.append(card);
      }
      const plans=new Map();
      for(const record of this.records){
        const d=record.handoff;if(!d||d.steps.length<2)continue;
        const previous=plans.get(d.plan_id);
        if(!previous||d.step>previous.handoff.step||d.step===previous.handoff.step&&
          (previous.superseded_by_task_id===record.task_id||record.created_at>previous.created_at))plans.set(d.plan_id,record);
      }
      for(const record of [...plans.values()].sort((a,b)=>String(b.created_at).localeCompare(String(a.created_at)))){
        const d=record.handoff,complete=record.status==="completed",last=d.step===d.steps.length-1;
        const card=el("article",null,"panel work-plan-saved");card.dataset.planId=d.plan_id;
        card.append(el("h3",d.steps.map(s=>services[s.service_id]).join(" → ")),
          el("p",complete&&last?"计划已完成":`第 ${d.step+1} / ${d.steps.length} 步 · ${complete?"已完成，可准备下一步":record.task_id?"查看任务了解进展":"已登记，等待智能核心接手"}`));
        const taskId=record.task_id||record.request_id;
        card.append(button("查看当前任务",()=>this.b.openTask(taskId)));
        if(complete&&!last&&!record.superseded_by_task_id){
          const next=button(`带入结果，准备第 ${d.step+2} 步`,async()=>{
            const label=next.textContent;next.disabled=true;next.textContent="正在读取上一步结果…";card.querySelector("[role=alert]")?.remove();
            try{await this.continue(record);}catch(e){const warning=el("p",e.message);warning.setAttribute("role","alert");card.append(warning);this.b.note(e.message,true);}
            finally{next.disabled=false;next.textContent=label;}
          });card.append(next);
        }
        this.cards.append(card);
      }
      this.cards.hidden=!this.cards.childElementCount;
    }
    open() {
      const d=this.draft;if(!d)return;
      try{
        this.validateSteps(d);
        if(this.b.scope()!==d.scope_id)throw Error("资料范围已改变，请返回对话重新整理任务，避免混用项目。");
        const step=d.steps[d.step];this.b.open(step.service_id,step.request);this.showEditor();
      }catch(e){this.b.note(e.message,true);}
    }
    showEditor() {
      const d=this.draft;if(!d)return;
      const service=d.steps[d.step].service_id;
      const target=$(service==="presentation-studio"?"presentation-task-form":service==="weekly-deck"?"weekly-task-form":"generic-task-form");
      target.prepend(this.editor);this.editor.hidden=false;this.editor.replaceChildren();
      this.editor.append(el("h3",`带入第 ${d.step+1} 步的需求`),el("p","请修改已过时的要求。点击本页开始按钮后，这些内容会保存到任务，并交给所选任务模型。","hint"));
      const field=(title,key,id)=>{
        const label=el("label",title),input=el("textarea");input.id=id;input.rows=6;input.value=d[key];
        const count=el("small",null,"hint");const update=()=>{d[key]=input.value;count.textContent=`${Array.from(input.value).length} / 6000 字${input.value.length>6000?"，请精简后开始；不会自动截断":""}`;};
        input.oninput=update;update();label.append(input,count);this.editor.append(label);
      };
      field("已讨论的要求与待核对建议","brief","task-handoff-brief");
      if(d.previous){this.editor.append(el("p",`上一步来源：${d.previous.task_id} · 版本 ${d.previous.version}；默认带入最后一条可见回复，可结合任务结果编辑。`,"hint"));field("上一步结果（保留后续工作需要的内容）","previous_result","task-handoff-result");}
      this.editor.append(button("返回对话",()=>this.b.showChat()),button("不带入草稿，单独创建任务",()=>this.clearDraft()));
    }
    validateSteps(d) {
      if(d.steps.length<1||d.steps.length>3||d.steps.some(s=>!services[s.service_id]||!s.request.trim()||Array.from(s.request).length>1000))throw Error("请填写每一步的要求，每步最多 1000 字，计划最多 3 步。");
    }
    submission(service, selection) {
      const d=this.draft;if(!d||this.editor.hidden)return {};
      if(base(service)!==d.steps[d.step].service_id)throw Error("工作类型已改变，请重新核对计划，或选择不带入草稿。");
      if((selection?.library_selection?.entity_id||"")!==d.scope_id)throw Error("资料范围已改变，请重新整理任务草稿。");
      this.validateSteps(d);
      if([d.brief,d.previous_result].some(text=>Array.from(text).length>6000))throw Error("带入内容超过 6000 字，请在草稿中精简后开始；未自动截断。");
      if(new TextEncoder().encode(JSON.stringify(d)).length>32*1024)throw Error("带入需求与结果合计超过 32 KiB，请精简后开始；未自动截断。");
      if(d.previous&&!d.previous_result.trim())throw Error("请填写已核对的上一步结果。");
      return {handoff:copy(d),handoff_confirmed:true};
    }
    created(record) {
      if(!record.handoff)return;
      this.records=[record,...this.records.filter(r=>(r.task_id||r.request_id)!==record.request_id)];this.clearDraft();
    }
    update(tasks,pending) {
      this.records=[...tasks,...pending].filter(r=>{
        const d=r.handoff;
        return d?.schema===1&&typeof d.plan_id==="string"&&Array.isArray(d.steps)&&d.steps.length>0&&d.steps.length<=3
          &&Number.isInteger(d.step)&&d.step>=0&&d.step<d.steps.length&&d.steps.every(s=>s&&services[s.service_id]&&typeof s.request==="string");
      });
      const signature=JSON.stringify(this.records);
      if(signature===this.signature)return;this.signature=signature;this.render();
    }
    async continue(record) {
      if(this.draft)throw Error("已有未提交的草稿，请先完成或放弃草稿。");
      if(this.continuing)throw Error("正在准备下一步，请稍候。");
      this.continuing=true;const generation=this.generation,isCurrent=()=>generation===this.generation;
      try {
      const d=record.handoff;let after="",last="",source="";
      for(let page=0;page<100;page++){
        const result=await this.b.api(`/api/tasks/${encodeURIComponent(record.task_id)}/conversation${after?`?after=${encodeURIComponent(after)}`:""}`);
        if(!isCurrent())return;
        if(result.status!=="ready"||result.reset||source&&source!==result.source_id)throw Error("上一项任务的回复暂不可完整读取，请稍后重试。");
        source=result.source_id;last=result.messages.at(-1)?.text||last;
        if(!result.has_more)break;
        if(!result.next_after||result.next_after===after||page===99)throw Error("回复过长，请先在任务中整理结果再继续。");
        after=result.next_after;
      }
      if(!last.trim())throw Error("上一项任务尚无可带入的可见回复，请稍后重试。");
      await this.b.restoreScope(record,isCurrent);
      if(!isCurrent())return;
      this.draft={...copy(d),step:d.step+1,previous:{task_id:record.task_id,version:record.version},previous_result:last};
      this.render();this.open();
      }finally{this.continuing=false;}
    }
  }
  window.Agent4MarketWorkPlan={create:bridge=>new WorkPlan(bridge),conversationBrief};
})();
