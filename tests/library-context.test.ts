import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import {readFileSync,mkdtempSync,mkdirSync,writeFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import ts from 'typescript';
import {readLocalPdf} from '../pi/extensions/source-readers.ts';
import {createTask} from '../pi/extensions/task-runtime.ts';
import {validLibraryContext,renderLibraryContext,type LibraryContext} from '../pi/extensions/library-context.ts';

const context:LibraryContext={entity_id:'entity-fixture',revision:'a'.repeat(64),scope_ids:['entity-fixture'],snapshot:{type:'customer',name:'合成客户'},files:[]};
test('library context is validated, copied and carried in the real runtime prompt',()=>{
  assert.equal(validLibraryContext(context),true);
  assert.equal(validLibraryContext({...context,files:[{path:'arbitrary'}]}),false);
  const task=createTask({sessionKey:'fixture',profileId:'sales-director',serviceId:'sales-review',request:'fixture',workflow:{id:'fixture',nodes:[{id:'draft',type:'agent',depends_on:[]}]},libraryContext:context});
  task.library_context!.snapshot.name='edited local clone';assert.equal(context.snapshot.name,'合成客户');
  assert.match(renderLibraryContext(context),/不是正式销售台账/);
  assert.match(renderLibraryContext(context),/微信仍须独立授权范围/);
  const source=readFileSync(new URL('../pi/extensions/vertical-workflow.ts',import.meta.url),'utf8');
  assert.match(source,/renderLibraryContext\(task.library_context\)/);
  assert.match(source,/library_context: task.library_context/);
});
test('intent routing does not interpret how-to questions or negative requests as execution',()=>{
  const sandbox=vm.createContext({window:{}});
  vm.runInContext(readFileSync(new URL('../ui/business-library.js',import.meta.url),'utf8'),sandbox);
  const detect=sandbox.window.Agent4MarketBusinessLibrary.detectIntent;
  for(const text of ['如何制作 PPT？','什么是销售复盘','不要制作 PPT','为什么需要复盘客户','聊一下业务想法'])assert.equal(detect(text),null);
  for(const [text,expected] of [['复盘本周客户进展','sales-review'],['做一份 8 页 PPT','presentation-studio'],['解读招标文件','pdf-import'],['整理客户微信群','wechat-review']])assert.equal(detect(text),expected);
});

test('PDF parsing enforces the frozen exact selection and byte digest',async()=>{
  const root=mkdtempSync(join(tmpdir(),'a4m-library-pdf-'));
  try {
    mkdirSync(join(root,'inputs'));
    const text='BT /F1 12 Tf 72 720 Td (Synthetic library evidence) Tj ET';
    const pdf=Buffer.from(['%PDF-1.4','1 0 obj','<< /Type /Page /Contents 2 0 R >>','endobj','2 0 obj',`<< /Length ${text.length} >>`,'stream',text,'endstream','endobj','%%EOF'].join('\n'));
    writeFileSync(join(root,'inputs/selected.pdf'),pdf);writeFileSync(join(root,'inputs/other.pdf'),pdf);
    const selectedFiles=[{path:'inputs/selected.pdf',sha256:createHash('sha256').update(pdf).digest('hex')}];
    await assert.rejects(()=>readLocalPdf(root,'inputs/other.pdf',{selectedFiles}),/不在本次/);
    await assert.rejects(()=>readLocalPdf(root,'inputs/selected.pdf',{selectedFiles:[]}),/不在本次/);
    const result=await readLocalPdf(root,'inputs/selected.pdf',{selectedFiles});assert.equal(result.content_sha256,selectedFiles[0].sha256);
    writeFileSync(join(root,'inputs/selected.pdf'),Buffer.concat([pdf,Buffer.from('\nchanged')]));
    await assert.rejects(()=>readLocalPdf(root,'inputs/selected.pdf',{selectedFiles}),/内容已变化/);
    const adapter=readFileSync(new URL('../pi/extensions/data-adapters.ts',import.meta.url),'utf8');
    assert.match(adapter,/selectedFiles: context\?\.selected_files/);
    const workflow=readFileSync(new URL('../pi/extensions/vertical-workflow.ts',import.meta.url),'utf8');
    assert.match(workflow,/selected_files: state.library_context\?\.files/);
  }finally{rmSync(root,{recursive:true,force:true});}
});

test('business group filters use explicit classification and stable entity links',()=>{
  const source=readFileSync(new URL('../ui/business-library.js',import.meta.url),'utf8');
  const parsed=ts.createSourceFile('library.js',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.JS);
  let method='';
  function visit(node:ts.Node){if(ts.isMethodDeclaration(node)&&node.name.getText(parsed)==='filterGroups')method=node.getText(parsed);ts.forEachChild(node,visit);}
  visit(parsed);assert.ok(method);
  const holder=vm.runInNewContext(`({${method}})`);
  const rows=[{conversation_id:'customer',chat_type:'group'},{conversation_id:'sales',chat_type:'group'},{conversation_id:'unknown',chat_type:'group'},{conversation_id:'person',chat_type:'direct'}];
  const state={groupFilter:'customer',groupEntity:'',data:{groups:{'wechat:customer':'customer','wechat:sales':'sales'},links:{'wechat:customer':['customer-id']}}};
  const filtered=()=>Array.from(holder.filterGroups.call(state,rows),(r:any)=>r.conversation_id);
  assert.deepEqual(filtered(),['customer']);state.groupFilter='sales';assert.deepEqual(filtered(),['sales']);
  state.groupFilter='unclassified';assert.deepEqual(filtered(),['unknown']);
  state.groupFilter='';state.groupEntity='customer-id';assert.deepEqual(filtered(),['customer']);
  state.groupEntity='another-id';assert.deepEqual(filtered(),[]);
});

test('legacy direct-file tasks freeze the clicked file, never stale chat selections',async()=>{
  const source=readFileSync(new URL('../ui/app.js',import.meta.url),'utf8');
  const parsed=ts.createSourceFile('app.js',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.JS);
  const declarations:string[]=[];
  function visit(node:ts.Node){if(ts.isFunctionDeclaration(node)&&['startLibraryTask','createTask'].includes(node.name?.text||''))declarations.push(node.getText(parsed));ts.forEachChild(node,visit);}
  visit(parsed);assert.equal(declarations.length,2);
  for(const previous of [{},{library_selection:{entity_id:'stale-entity',file_ids:['file-b']}}]){
    const requests:any[]=[];const errors:string[]=[];
    const sandbox=vm.createContext({selectedProfile:'sales-director',selectedProject:'project-default',selectedService:'pdf-import',model:{},
      serviceById:()=>({id:'pdf-import'}),projectById:(id:string)=>({project_id:id,status:'active'}),publicSearchReady:()=>true,
      workbench:{selection:()=>previous,created(){}},taskRuntimeSelection:()=>({}),selectedCustomerContextText:()=>{throw Error('stale customer context');},
      note:(text:string,error:boolean)=>{if(error)errors.push(text);},load:async()=>{},switchView(){},
      api:async(_url:string,options:{body:string})=>{requests.push(JSON.parse(options.body));return{request_id:'fixture'};},
    });
    vm.runInContext(declarations.join('\n'),sandbox);
    await sandbox.startLibraryTask({library_id:'file-a',path:'inputs/projects/project-a/f.pdf',origin_project_id:'project-a',project_id:'',title:'合成文件'},'pdf-import','读取');
    assert.deepEqual(errors,[]);assert.equal(requests.length,1);
    assert.deepEqual(requests[0].library_selection,{entity_id:'',file_ids:['file-a']});assert.equal(requests[0].project_id,'project-a');
  }
});
