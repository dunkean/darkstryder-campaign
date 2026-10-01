import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { readFile, stat, realpath } from 'node:fs/promises';
import { createReadStream } from 'node:fs';
import { createStore } from './storage.mjs';

const ROOT=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
let local={};try{local=JSON.parse(await readFile(path.join(ROOT,'config.local.json'),'utf8'));}catch{}
const DRIVE=process.env.DARKSTRYDER_SOURCE_ROOT||local.sourceRoot;
const RUNTIME=process.env.DARKSTRYDER_RUNTIME_ROOT||local.runtimeRoot||path.resolve(ROOT,'../darkstryder_runtime');
const store=createStore(ROOT,RUNTIME);
const readJson=async rel=>JSON.parse(await readFile(path.join(ROOT,rel),'utf8'));
const json=(res,status,data)=>{res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(data));};
const types={'.html':'text/html; charset=utf-8','.css':'text/css','.js':'text/javascript','.mjs':'text/javascript','.json':'application/json','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp','.svg':'image/svg+xml','.pdf':'application/pdf','.md':'text/plain; charset=utf-8'};
async function serveFile(req,res,base,relative){
  const baseResolved=path.resolve(base),candidate=path.resolve(base,relative);
  if(!candidate.startsWith(baseResolved+path.sep))throw Object.assign(new Error('Chemin interdit'),{status:403});
  const root=await realpath(base),resolved=await realpath(candidate);
  if(!resolved.startsWith(root+path.sep)||resolved===root)throw Object.assign(new Error('Chemin interdit'),{status:403});
  const info=await stat(resolved);if(!info.isFile())throw Object.assign(new Error('Fichier absent'),{status:404});
  res.writeHead(200,{'Content-Type':types[path.extname(resolved).toLowerCase()]||'application/octet-stream','Content-Length':info.size,'X-Content-Type-Options':'nosniff'});
  if(req.method==='HEAD')return res.end();createReadStream(resolved).pipe(res);
}
async function body(req){let text='';for await(const chunk of req){text+=chunk;if(Buffer.byteLength(text)>8*1024*1024)throw Object.assign(new Error('Données trop volumineuses'),{status:413});}try{return JSON.parse(text);}catch{throw Object.assign(new Error('JSON invalide'),{status:400});}}

export function createServer(){return http.createServer(async(req,res)=>{
  try{
    const host=req.headers.host||'';if(!/^(localhost|127\.0\.0\.1)(:\d+)?$/.test(host))return json(res,403,{error:'Serveur local uniquement'});
    if(req.headers.origin && req.headers.origin!==`http://${host}`)return json(res,403,{error:'Origine interdite'});
    const url=new URL(req.url,`http://${host}`),route=decodeURIComponent(url.pathname);
    if(route==='/vendor/marked.js')return await serveFile(req,res,path.join(ROOT,'node_modules/marked/lib'),'marked.esm.js');
    if(route==='/vendor/purify.js')return await serveFile(req,res,path.join(ROOT,'node_modules/dompurify/dist'),'purify.es.mjs');
    if(route==='/api/bootstrap' && req.method==='GET'){
      const [campaign,entities,portraits,assets,sources]=await Promise.all([store.read('campaign'),store.read('entities'),readJson('catalog/portraits.json'),readJson('catalog/assets.json'),readJson('catalog/sources.json')]);
      return json(res,200,{campaign:campaign.data,entities:entities.data,portraits,assets,sources,revisions:{campaign:campaign.revision,entities:entities.revision}});
    }
    if(['/api/campaign','/api/entities'].includes(route)&&req.method==='PUT')return json(res,200,await store.update(route.split('/').at(-1),await body(req),req.headers['if-match']));
    if(route==='/api/ocr-status'&&req.method==='GET'){
      let status={state:'not-started'};try{status=JSON.parse(await readFile(path.join(RUNTIME,'ocr-status.json'),'utf8'));}catch{}
      return json(res,200,status);
    }
    if(route==='/api/source'&&req.method==='GET'){
      const source=(await readJson('catalog/sources.json')).find(s=>s.id===url.searchParams.get('id'));
      if(!source)return json(res,404,{error:'Source inconnue'});
      const page=Number(url.searchParams.get('page')||1);
      if(!Number.isInteger(page)||page<1||page>source.pages)return json(res,400,{error:'Page invalide'});
      const folder=path.join(RUNTIME,source.output);
      let markdown;try{markdown=await readFile(path.join(folder,'pages',`${String(page).padStart(4,'0')}.md`),'utf8');}catch{return json(res,404,{error:'Cette page attend son OCR'});}
      let extraction;try{
        const manifest=JSON.parse(await readFile(path.join(folder,'manifest.json'),'utf8'));
        const report=manifest.pages?.find(report=>report.page===page);
        if(report)extraction={engine:manifest.engine||manifest.model,flags:report.flags||[]};
      }catch{}
      return json(res,200,{markdown,source,page,extraction,image:`/runtime/${source.output}/pages/${String(page).padStart(4,'0')}.jpg`});
    }
    if(route==='/api/source-search'&&req.method==='GET'){
      const q=(url.searchParams.get('q')||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().trim();
      if(q.length<3)return json(res,200,[]);
      const results=[];
      for(const s of await readJson('catalog/sources.json')){
        let index;try{index=JSON.parse(await readFile(path.join(RUNTIME,s.output,'search.json'),'utf8'));}catch{continue;}
        for(const entry of index){const pos=entry.text.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().indexOf(q);if(pos>=0)results.push({sourceId:s.id,name:s.name,page:entry.page,snippet:entry.text.slice(Math.max(0,pos-70),pos+200)});if(results.length>=60)break;}
        if(results.length>=60)break;
      }return json(res,200,results);
    }
    if(route.startsWith('/asset/')&&['GET','HEAD'].includes(req.method)){
      const asset=(await readJson('catalog/assets.json')).find(a=>a.id===route.slice(7));
      if(!asset)return json(res,404,{error:'Média inconnu'});return await serveFile(req,res,DRIVE,asset.path);
    }
    if(route.startsWith('/original/')&&['GET','HEAD'].includes(req.method)){
      const s=(await readJson('catalog/sources.json')).find(a=>a.id===route.slice(10));
      if(!s)return json(res,404,{error:'Source inconnue'});return await serveFile(req,res,s.locator.root==='drive'?DRIVE:ROOT,s.locator.path);
    }
    if(route.startsWith('/runtime/')&&['GET','HEAD'].includes(req.method)){
      const relative=route.slice(9);if(!/^(media|extracted)\//.test(relative))return json(res,403,{error:'Chemin interdit'});
      return await serveFile(req,res,RUNTIME,relative);
    }
    if(['GET','HEAD'].includes(req.method)&&!route.startsWith('/api/'))return await serveFile(req,res,path.join(ROOT,'apps/knowledge-web/dist'),route==='/'?'index.html':route.slice(1));
    json(res,404,{error:'Route inconnue'});
  }catch(error){json(res,error.status|| (error.code==='ENOENT'?404:error.name==='ZodError'||/invalide|inconnue/.test(error.message)?400:500),{error:error.message});}
});}
if(process.argv[1] && path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  const port=Number(process.env.PORT||4321);createServer().listen(port,'127.0.0.1',()=>console.log(`DarkStryder : http://localhost:${port}`));
}
