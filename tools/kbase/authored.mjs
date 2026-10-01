import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {openKBase} from '../../packages/kbase/store.mjs';
import {createStore} from '../../apps/knowledge-web/storage.mjs';
import {entries} from '../../content/kbase/darkstryder-original.mjs';

export function selectPassage(markdown,{start,end}) {
  const from=start?markdown.indexOf(start):markdown.indexOf('-->')+3;
  if(from<0)throw new Error('Source start anchor missing: '+start);
  const to=end?markdown.indexOf(end,from+(start?.length||0)):markdown.length;
  if(to<from)throw new Error('Source end anchor missing: '+end);
  const text=markdown.slice(from,to).trim();
  if(!text)throw new Error('Empty source selection');
  return text;
}
export function quoteUnits(text) {
  const units=[];
  for(let start=0;start<text.length;){
    let end=Math.min(start+1100,text.length);
    if(end<text.length){const space=text.lastIndexOf(' ',end);if(space>start)end=space;}
    const quote=text.slice(start,end).trim();if(quote)units.push(quote);start=end;
  }
  return units;
}
export async function authorOriginals(root,authoredEntries=entries){
  const local=JSON.parse(readFileSync(path.join(root,'config.local.json')));
  const kb=openKBase(root,local.runtimeRoot),site=createStore(root,local.runtimeRoot);
  const campaign=await site.read('campaign'),entities=await site.read('entities');
  const results=[];
  try{
    for(const entry of authoredEntries){
      const {sourceId,name,type='Location',summary,content=summary,passages,site:binding,family='WORLD',usage='darkstryder_scenario'}=entry;
      const evidence=passages.flatMap(p=>{
        const file=path.join(root,'content/transcriptions',sourceId,'pages',String(p.page).padStart(4,'0')+'.md');
        return quoteUnits(selectPassage(readFileSync(file,'utf8'),p)).map(quote=>({sourceId,page:p.page,quote,fields:['name','summary','content']}));
      });
      const node={key:'entry',name,type,family,usage,summary,content,tags:[],properties:{},scope:'source',visibility:'gm_only',evidence};
      const result=kb.importAuthoredBatch({sourceId,batchId:name,nodes:[node],segments:[{key:'passages',title:name,kind:type,nodeKeys:['entry'],evidence:evidence.map(e=>({...e,fields:['segment']}))}]});
      const candidates=kb.listNodes({q:name,limit:100}).nodes.filter(n=>n.name===name&&n.type===type);
      if(candidates.length!==1)throw new Error('Ambiguous authored node identity: '+name);
      const id=candidates[0].id;
      for(const bindingTarget of (binding?(Array.isArray(binding)?binding:[binding]):[])){
        const binding=bindingTarget;
        let target;
        if(binding.kind==='crew')target=campaign.data.crew.members.find(p=>p.id===binding.id);
        else if(binding.kind==='room')target=campaign.data.ship.rooms.find(r=>r.id===binding.id);
        else if(binding.kind==='ship')target=campaign.data.ship;
        else if(binding.kind==='entity')target=entities.data.entities.find(e=>e.id===binding.id);
        if(!target)throw new Error('Missing site binding: '+binding.id);
        target.kbaseIds=[...new Set([...(target.kbaseIds||[]),id])];
        target.referenceKnowledge=target.referenceKnowledge||[];
        const fingerprint=createHash('sha256').update(JSON.stringify({sourceId,name,summary,content,passages})).digest('hex');
        if(!target.referenceKnowledge.some(r=>r.fingerprint===fingerprint))target.referenceKnowledge.push({fingerprint,kbaseId:id,name,summary,content,sourceId,pages:[...new Set(passages.map(p=>p.page))],provenance:'official',usage,authoredBy:'manual',status:'source-reference',sourceSha256:kb.getNode(id).evidence.find(e=>e.sourceId===sourceId)?.sourceSha256});
      }
      results.push({name,id,...result});
    }
    // Existing edited text, stats, placements and unknown fields remain untouched.
    if(JSON.stringify(campaign.data)!==JSON.stringify((await site.read('campaign')).data))await site.update('campaign',campaign.data,campaign.revision);
    if(JSON.stringify(entities.data)!==JSON.stringify((await site.read('entities')).data))await site.update('entities',entities.data,entities.revision);
    return {authoredEntries:results.length,summary:kb.getSummary(),results};
  }finally{kb.close();}
}
if(process.argv[1]===fileURLToPath(import.meta.url)){
  const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
  const result=await authorOriginals(root);
  console.log(JSON.stringify({authoredEntries:result.authoredEntries,summary:result.summary},null,2));
}
