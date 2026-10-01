export let D, TH, M, byId, roomById, entities, assets, sources, revisions;
export const state={mode:'shift1',hour:9,alert:false,view:'overview'};
export const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
export const nrm=s=>String(s??'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]+/g,' ').trim();
export const portrait=id=>TH[id]?`<img class="portrait" src="${TH[id]}" alt="">`:'<div class="portrait"></div>';
export const portraitLarge=id=>TH[id]?`<img class="portrait-lg" src="${TH[id]}" alt="">`:'';
export const avatarHtml=(id,title='')=>`<button class="avatar-button" onclick="openPerson('${id}')" title="${esc(title)}">${portrait(id)}</button>`;
export async function load(){
  const response=await fetch('/api/bootstrap');if(!response.ok)throw new Error('Chargement impossible');
  const data=await response.json();
  D=data.campaign;TH=data.portraits;entities=data.entities;assets=data.assets;sources=data.sources;revisions=data.revisions;
  M=D.crew.members;byId=Object.fromEntries(M.map(p=>[p.id,p]));roomById=Object.fromEntries(D.ship.rooms.map(r=>[r.id,r]));
}
export async function save(kind,value){
  const response=await fetch('/api/'+kind,{method:'PUT',headers:{'Content-Type':'application/json','If-Match':revisions[kind]},body:JSON.stringify(value)});
  const result=await response.json();if(!response.ok)throw new Error(result.error||'Échec sauvegarde');
  revisions[kind]=result.revision;
}
