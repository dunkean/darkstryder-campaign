import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {state} from '../apps/knowledge-web/public/modules/store.js';

test('KBase separates usage, displays escaped source excerpts and sends revisioned editorial edits',async()=>{
  const dom=new JSDOM('<section id="kbase"></section><div id="modal"><div id="modalContent"></div></div><p id="saveStatus"></p>',{url:'http://localhost:4321'});
  const previous={window:globalThis.window,document:globalThis.document,FormData:globalThis.FormData,fetch:globalThis.fetch};
  Object.assign(globalThis,{window:dom.window,document:dom.window.document,FormData:dom.window.FormData});state.view='kbase';
  const {renderKBase,openKBaseNode}=await import('../apps/knowledge-web/public/modules/kbase.js');
  const node={id:'kb-test',name:'FarStar',type:'Ship',family:'WORLD',usage:'darkstryder_scenario',summary:'Corvette de la campagne',content:'Description extraite.',tags:[],reviewStatus:'unreviewed',revision:0,properties:{},claims:[],relationships:[],evidence:[{sourceId:'src-1678b1eb8c4bd691',page:3,quote:'<script>source text</script>',fields:['summary']}]};
  let write,source;
  window.openSource=(id,page)=>{source={id,page};};
  globalThis.fetch=async(url,options)=>{
    let data;
    if(String(url).includes('/summary'))data={nodes:1,relationships:0,evidence:1,jobs:{complete:1,pending:20}};
    else if(String(url).includes('/nodes?'))data={nodes:[node],total:1};
    else if(options?.method==='PUT'){write={url,options};data={...node,revision:1};}
    else data=node;
    return{ok:true,json:async()=>data};
  };
  try{
    await renderKBase();assert.match(document.getElementById('kbaseResults').textContent,/Scénario DarkStryder/);
    await openKBaseNode(node.id);
    assert.equal(document.querySelector('#modalContent blockquote').textContent,'<script>source text</script>');assert.equal(document.querySelector('#modalContent script'),null);
    document.querySelector('[data-evidence-source]').click();assert.deepEqual(source,{id:'src-1678b1eb8c4bd691',page:3});
    document.getElementById('kbaseEdit').click();const form=document.getElementById('kbaseEditor');form.querySelector('[name=summary]').value='Résumé personnel';
    await form.onsubmit({preventDefault(){},target:form,submitter:form.querySelector('button')});
    assert.equal(Number(write.options.headers['If-Match']),0);
    assert.equal(JSON.parse(write.options.body).summary,'Résumé personnel');
    assert.equal(JSON.parse(write.options.body).reviewStatus,'unreviewed');
  }finally{Object.assign(globalThis,previous);state.view='overview';dom.window.close();}
});
