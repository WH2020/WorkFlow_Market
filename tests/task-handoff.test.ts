import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { createTask } from '../pi/extensions/task-runtime.ts';
import { validTaskHandoff, matchesHandoff, renderTaskHandoff, type TaskHandoff } from '../pi/extensions/task-handoff.ts';

const draft:TaskHandoff={schema:1,plan_id:'a'.repeat(32),steps:[{service_id:'sales-review',request:'复盘客户'},{service_id:'presentation-studio',request:'制作8页PPT'}],step:0,scope_id:'',brief:'受众：管理层；重点：风险',previous:null,previous_result:''};
test('handoff schema rejects malformed, oversized and cross-service context, accepts legacy requests',()=>{
  assert.equal(validTaskHandoff(undefined),true);assert.equal(validTaskHandoff(draft),true);
  assert.equal(matchesHandoff(draft,'sales-review-readonly'),true);
  assert.equal(matchesHandoff(draft,'presentation-studio'),false);
  assert.equal(matchesHandoff(draft,'sales-review','other'),false);
  for(const bad of [null,[],{...draft,step:true},{...draft,step:2},{...draft,brief:'x'.repeat(6001)},{...draft,brief:'\ud800'},
    {...draft,previous_result:'first step cannot cite another task'},{...draft,steps:[{service_id:'wechat-review',request:'x'}]},
    {...draft,steps:[{service_id:[],request:'x'}]},{...draft,unexpected:'secret'},
    {...draft,step:1,previous:{task_id:'../other',version:1},previous_result:'x'}])assert.equal(validTaskHandoff(bad),false,JSON.stringify(bad).slice(0,100));
  assert.equal(validTaskHandoff({...draft,brief:'🙂'.repeat(6000)}),true);
  assert.equal(validTaskHandoff({...draft,step:1,previous:{task_id:'request-previous',version:1},previous_result:'已核对结果'}),true);
});
test('native task owns a copied handoff and its prompt restricts execution to the current step',()=>{
  const task=createTask({sessionKey:'fixture',profileId:'sales-director',serviceId:'sales-review',request:'fixture',workflow:{id:'fixture',nodes:[{id:'draft',type:'agent',depends_on:[]}]},handoff:draft});
  task.handoff!.brief='edited';assert.notEqual(task.handoff!.brief,draft.brief);
  assert.match(renderTaskHandoff(draft),/只执行第 1 项/);assert.match(renderTaskHandoff(draft),/不等同于业务事实/);
  const source=readFileSync(new URL('../pi/extensions/vertical-workflow.ts',import.meta.url),'utf8');
  assert.match(source,/renderTaskHandoff\(task.handoff\)/);assert.match(source,/handoff: request.handoff/);
});
test('composite routing preserves order and ignores instructions negated by the user',()=>{
  const sandbox=vm.createContext({window:{}});vm.runInContext(readFileSync(new URL('../ui/business-library.js',import.meta.url),'utf8'),sandbox);
  const plan=(text:string)=>JSON.parse(JSON.stringify(sandbox.window.Agent4MarketBusinessLibrary.detectPlan(text)));
  assert.deepEqual(plan('先复盘客户，然后制作8页PPT'),[{service_id:'sales-review',request:'先复盘客户'},{service_id:'presentation-studio',request:'制作8页PPT'}]);
  assert.deepEqual(plan('请把方案改成PPT').map((s:any)=>s.service_id),['presentation-studio']);
  assert.deepEqual(plan('不要复盘客户，然后不要制作PPT'),[]);
  assert.deepEqual(plan('先研究行业趋势，再制作PPT').map((s:any)=>s.service_id),['industry-research','presentation-studio']);
});
test('conversation requirements and model suggestions remain visibly distinct without silent truncation',()=>{
  const sandbox=vm.createContext({window:{}});vm.runInContext(readFileSync(new URL('../ui/work-plan.js',import.meta.url),'utf8'),sandbox);
  const brief=sandbox.window.Agent4MarketWorkPlan.conversationBrief([{role:'user',text:'受众是管理层'},{role:'assistant',text:'早期建议'},{role:'user',text:'重点是风险'},{role:'assistant',text:'建议供核对'}],'制作8页PPT');
  for(const value of ['受众是管理层','重点是风险','制作8页PPT','建议供核对','不视为已确认事实'])assert.ok(brief.includes(value));
  assert.ok(!brief.includes('早期建议'));
  assert.ok(sandbox.window.Agent4MarketWorkPlan.conversationBrief([{role:'user',text:'a'.repeat(7000)}],'制作PPT').length>7000);
});
