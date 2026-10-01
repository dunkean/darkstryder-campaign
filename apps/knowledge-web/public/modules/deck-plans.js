import {D,roomById,assets,state,esc,save} from './store.js';
import {allRoomOccupancy,chip} from './occupancy.js';

const ui={planId:null,selected:null,zoom:1,labels:true,source:false,editing:false,draft:null,drawing:null};
const pointText=points=>points.map(p=>p.join(',')).join(' ');
const center=region=>region.labelAt||region.points.reduce((p,v)=>[p[0]+v[0]/region.points.length,p[1]+v[1]/region.points.length],[0,0]);
const status=(message,error=false)=>{const el=document.getElementById('saveStatus');el.textContent=message;el.style.color=error?'var(--red)':'var(--green)';};
const current=()=>D.ship.deckPlans?.find(p=>p.id===ui.planId);
const selected=()=>current()?.regions.find(r=>r.id===ui.selected);
async function persist(next){await save('campaign',next);D.ship=next.ship;for(const room of D.ship.rooms)roomById[room.id]=room;status('Plan et fiches enregistrés dans le JSON');}
const mediaUrl=url=>/^\/asset\/[\w-]+$/.test(url)||/^\/runtime\/media\/(?:[\w.-]+\/)*[\w.-]+$/.test(url)&&!url.split('/').includes('..');

export function roomMediaHTML(room){
  return `<div class="room-media">${(room.media||[]).filter(m=>mediaUrl(m.url)).map(m=>`<figure><a href="${esc(m.url)}" target="_blank" rel="noopener"><img src="${esc(m.url)}" alt="${esc(m.caption||room.label)}" loading="lazy"></a><figcaption>${esc(m.caption||'Illustration')} · ${esc(m.provenance==='official'?'source officielle':m.provenance==='interpretation'?'interprétation':'ajout personnel')}</figcaption></figure>`).join('')}</div>`;
}

export function renderDeckPlans(){
  const plans=D.ship.deckPlans||[],root=document.getElementById('ship');
  if(!plans.length){root.innerHTML='<h2>Plans du FarStar</h2><p>Aucun tracé calibré enregistré.</p>';return;}
  if(!current()){ui.planId=plans[0].id;ui.selected=null;}
  const plan=current(),occ=allRoomOccupancy(),region=selected()||ui.draft,draft=ui.draft||region;
  const svg=renderSVG(plan,occ,draft);
  root.innerHTML=`<div class="toolbar"><h2>Plans interactifs du FarStar</h2><label>Pont <select id="deckSelect">${plans.map(p=>`<option value="${p.id}" ${p.id===plan.id?'selected':''}>${esc(p.deck)}</option>`).join('')}</select></label><button id="deckList">Liste des salles</button></div><div class="deck-tools"><label>Zoom <select id="deckZoom">${[1,1.5,2,3].map(n=>`<option value="${n}" ${ui.zoom===n?'selected':''}>${n}×</option>`).join('')}</select></label><label><input id="deckLabels" type="checkbox" ${ui.labels?'checked':''}> Noms</label><button id="deckSource" aria-expanded="${ui.source}">${ui.source?'Masquer':'Comparer avec'} l’original</button><button id="deckEdit" class="${ui.editing?'primary':''}">${ui.editing?'Terminer la calibration':'Calibrer les zones'}</button>${ui.editing?'<button id="deckDraw">+ Tracer une zone</button>':''}</div><p class="inline-note">Sélectionne une pièce pour ses affectations et ses images. Les contours sont vectoriels, relevés sur les six plans sources ; une zone en pointillés attend une association. ${ui.editing?'Déplace les sommets puis enregistre la zone ; les coordonnées sont des pourcentages du plan source.':''}</p><div class="deck-layout"><div><div class="deck-viewport" id="deckViewport">${svg}</div><div class="deck-legend"><span class="legend-room">Pièce associée</span><span class="legend-empty">Zone à associer</span><span class="legend-access">Circulation / équipement</span><span>Quart : ${esc(state.mode)} · nombre d’affectations, sans localisation simulée</span></div>${ui.drawing?'<div class="deck-tools"><span id="traceCount">'+ui.drawing.points.length+' sommets · clique sur le plan pour suivre le contour</span><button id="traceFinish">Terminer le polygone</button><button id="traceUndo">Retirer le dernier sommet</button><button id="traceCancel">Annuler</button></div>':''}${ui.source?`<figure class="deck-reference"><img src="/asset/${esc(plan.assetId)}" alt="Plan source de ${esc(plan.deck)}"><figcaption>Original de comparaison · <a href="/asset/${esc(plan.assetId)}" target="_blank" rel="noopener">Ouvrir en taille réelle</a>. L’image ne fait pas partie du plan interactif.</figcaption></figure>`:''}<details class="deck-room-list"><summary>Toutes les salles de ${esc(plan.deck)} (${D.ship.rooms.filter(r=>r.deck===plan.deck).length})</summary>${D.ship.rooms.filter(r=>r.deck===plan.deck).map(r=>`<button data-room="${r.id}">${esc(r.label)}${plan.regions.some(z=>z.roomId===r.id)?'':' · sans zone associée'}</button>`).join('')}</details></div><div class="deck-inspector" id="deckInspector">${region?inspector(plan,region,occ):'<h3>Sélectionne une pièce</h3><p>Clique dans un compartiment du plan ou choisis une salle dans la liste. Les équipements et les couloirs restent sélectionnables pour leur calibration.</p><p class="muted">Aucune association spatiale n’est déduite des affectations de l’équipage.</p>'}</div></div>`;
  root.querySelector('#deckSelect').onchange=e=>{ui.planId=e.target.value;ui.selected=null;ui.draft=null;ui.drawing=null;renderDeckPlans();};
  root.querySelector('#deckZoom').onchange=e=>{ui.zoom=Number(e.target.value);root.querySelector('.deck-svg').style.width=`${ui.zoom*100}%`;};
  root.querySelector('#deckLabels').onchange=e=>{ui.labels=e.target.checked;root.querySelector('.deck-svg').classList.toggle('hide-labels',!ui.labels);};
  root.querySelector('#deckSource').onclick=()=>{ui.source=!ui.source;renderDeckPlans();};
  root.querySelector('#deckEdit').onclick=()=>{ui.editing=!ui.editing;ui.draft=null;ui.drawing=null;renderDeckPlans();};
  root.querySelector('#deckList').onclick=()=>window.renderShipList();
  root.querySelector('#deckDraw')?.addEventListener('click',()=>{ui.selected=null;ui.draft=null;ui.drawing={points:[]};renderDeckPlans();});
  root.querySelector('#traceFinish')?.addEventListener('click',()=>{
    if(ui.drawing.points.length<3){status('Il faut au moins trois sommets',true);return;}
    ui.draft={id:crypto.randomUUID(),label:'Nouvelle zone',kind:'room',roomId:null,labelAt:null,points:ui.drawing.points};ui.selected=ui.draft.id;ui.drawing=null;renderDeckPlans();
  });
  root.querySelector('#traceUndo')?.addEventListener('click',()=>{ui.drawing.points.pop();renderDeckPlans();});
  root.querySelector('#traceCancel')?.addEventListener('click',()=>{ui.drawing=null;renderDeckPlans();});
  root.querySelectorAll('[data-room]').forEach(button=>button.onclick=()=>window.openRoom(button.dataset.room));
  bindSVG();
  if(region)bindInspector(plan,region);
}

function renderSVG(plan,occ,draft){
  const regionMarkup=region=>{
    const r=draft?.id===region.id?draft:region,room=roomById[r.roomId],items=room?(occ[room.id]||[]):[],count=items.filter(x=>!x.extra).length,[x,y]=center(r);
    const textScale=plan.sourceSize.width/plan.sourceSize.height;
    const width=Math.max(...r.points.map(p=>p[0]))-Math.min(...r.points.map(p=>p[0])),limit=Math.max(6,Math.min(24,Math.floor(width/.55))),lines=[];
    for(const word of r.label.split(' ')){if(!lines.length||lines.at(-1).length+word.length+1>limit)lines.push(word);else lines[lines.length-1]+=' '+word;}
    if(lines.length>3)lines.splice(2,lines.length-2,lines.slice(2).join(' ').slice(0,limit-1)+'…');
    const height=Math.max(...r.points.map(p=>p[1]))-Math.min(...r.points.map(p=>p[1]));
    const showLabel=(r.kind==='room'&&width>=4.2&&height>=3.5)||r.id===ui.selected,labelY=y-textScale*.55*(lines.length-1),countY=showLabel?y+textScale*(.55*lines.length+1):y;
    return `<g><polygon data-region="${r.id}" points="${pointText(r.points)}" class="deck-region ${r.kind} ${r.roomId?'linked':'unlinked'} ${r.id===ui.selected?'selected':''}" tabindex="0" role="button" aria-label="${esc(r.label)}${room?', '+esc(room.label):', sans association'}"><title>${esc(r.label)}${room?' · '+esc(room.label):' · zone non associée'}${count?' · '+count+' affectation(s)':''}</title></polygon>${showLabel?`<text class="deck-zone-label" transform="translate(${x} ${labelY}) scale(1 ${textScale})">${lines.map((line,i)=>`<tspan x="0" dy="${i?1.1:0}">${esc(line)}</tspan>`).join('')}</text>`:''}${count?`<text class="deck-count" transform="translate(${x} ${countY}) scale(1 ${textScale})">${count}${items.some(x=>x.shared)?' ≈':''}</text>`:''}</g>`;
  };
  const traces=ui.drawing?.points||[];
  const handles=ui.editing&&draft?draft.points.map(([x,y],i)=>`<ellipse class="deck-handle" data-vertex="${i}" cx="${x}" cy="${y}" rx=".42" ry="${.42*plan.sourceSize.width/plan.sourceSize.height}" tabindex="0" role="button" aria-label="Sommet ${i+1}, utiliser les flèches pour ajuster"></ellipse>`).join(''):'';
  return `<svg class="deck-svg ${ui.labels?'':'hide-labels'} ${ui.drawing?'drawing':''}" style="width:${ui.zoom*100}%;aspect-ratio:${plan.sourceSize.width}/${plan.sourceSize.height}" viewBox="0 0 100 100" preserveAspectRatio="none" aria-label="Plan vectoriel de ${esc(plan.deck)}"><rect width="100" height="100" class="deck-bg"/><polygon points="${pointText(plan.outline)}" class="deck-hull"/>${plan.details.filter(d=>d.kind==='engine').map(d=>`<polygon points="${pointText(d.points)}" class="deck-engine"/>`).join('')}${plan.regions.filter(r=>r.kind==='corridor').map(regionMarkup).join('')}${plan.regions.filter(r=>r.kind==='room').map(regionMarkup).join('')}${plan.regions.filter(r=>r.kind==='access').map(regionMarkup).join('')}${ui.draft&&!plan.regions.some(r=>r.id===ui.draft.id)?regionMarkup(ui.draft):''}${plan.details.filter(d=>d.kind==='wall').map(d=>`<polyline points="${pointText(d.points)}" class="deck-wall"/>`).join('')}${traces.length?`<polyline points="${pointText(traces)}" class="deck-trace"/>${traces.map(([x,y])=>`<ellipse cx="${x}" cy="${y}" rx=".3" ry=".6" class="deck-trace-dot"/>`).join('')}`:''}${handles}</svg>`;
}

function inspector(plan,region,occ){
  const room=roomById[region.roomId],occupants=room?(occ[room.id]||[]):[];
  return `<div class="tiny code">${esc(region.id)}</div><h3>${esc(room?.label||region.label)}</h3><p class="muted">${esc(plan.deck)} · ${esc(region.kind==='room'?'Compartiment':region.kind==='corridor'?'Circulation':'Équipement / accès')}</p>${room?`<div class="deck-assignment">${occupants.map(chip).join('')||'<span class="muted">Aucune affectation pour ce quart.</span>'}</div><button id="deckRoomDetail">Fiche de la salle</button>${roomMediaHTML(room)}<button id="deckMediaEdit">Ajouter / modifier les images</button>`:'<p class="inline-note">Aucune salle associée. Le nom du tracé décrit la source ; il ne crée pas une salle canonique.</p>'}<form id="zoneEditor" class="editor"><label>Nom du tracé<input name="label" value="${esc(region.label)}" required maxlength="160"></label><label>Salle associée<select name="roomId"><option value="">— Non associée —</option>${D.ship.rooms.filter(r=>r.deck===plan.deck).map(r=>`<option value="${r.id}" ${r.id===region.roomId?'selected':''}>${esc(r.label)} · ${r.id}</option>`).join('')}</select></label>${ui.editing?`<label>Type<select name="kind">${[['room','Pièce'],['corridor','Circulation'],['access','Équipement / accès']].map(([id,label])=>`<option value="${id}" ${region.kind===id?'selected':''}>${label}</option>`).join('')}</select></label><details><summary>Coordonnées calibrées (%)</summary><label>Sommets [x, y]<textarea name="points" class="zone-points">${esc(JSON.stringify((ui.draft||region).points))}</textarea></label><label>Position du nom [x, y] ou null<input name="labelAt" value="${esc(JSON.stringify(region.labelAt??null))}"></label></details><p class="inline-note">Le glisser d’un sommet et les flèches du clavier modifient le brouillon jusqu’à son enregistrement.</p>`:''}<div id="zoneError" class="error" role="status"></div><button class="primary" type="submit">Enregistrer la zone</button>${ui.editing?'<button id="zoneCancel" type="button">Annuler les ajustements</button><button id="zoneDelete" type="button">Supprimer ce tracé</button>':''}</form>`;
}

function bindInspector(plan,region){
  document.getElementById('deckRoomDetail')?.addEventListener('click',()=>window.openRoom(region.roomId));
  document.getElementById('deckMediaEdit')?.addEventListener('click',()=>editRoomMedia(region.roomId));
  document.getElementById('zoneEditor').onsubmit=async event=>{
    event.preventDefault();event.submitter.disabled=true;
    try{
      const form=new FormData(event.target),next=structuredClone(D),p=next.ship.deckPlans.find(p=>p.id===plan.id),value={...region,...(ui.draft||{}),label:form.get('label').trim(),roomId:form.get('roomId')||null};
      if(ui.editing){value.kind=form.get('kind');value.points=JSON.parse(form.get('points'));value.labelAt=JSON.parse(form.get('labelAt'));}
      const index=p.regions.findIndex(r=>r.id===value.id);if(index<0)p.regions.push(value);else p.regions[index]=value;
      await persist(next);ui.draft=null;renderDeckPlans();
    }catch(error){document.getElementById('zoneError').textContent=error.message;event.submitter.disabled=false;}
  };
  document.getElementById('zoneCancel')?.addEventListener('click',()=>{ui.draft=null;if(!selected())ui.selected=null;renderDeckPlans();});
  document.getElementById('zoneDelete')?.addEventListener('click',async()=>{
    if(!confirm('Supprimer ce tracé ? La salle et ses images sont conservées. Le JSON précédent est sauvegardé.'))return;
    try{const next=structuredClone(D);next.ship.deckPlans.find(p=>p.id===plan.id).regions=plan.regions.filter(r=>r.id!==region.id);await persist(next);ui.selected=null;ui.draft=null;renderDeckPlans();}catch(error){document.getElementById('zoneError').textContent=error.message;}
  });
}

function bindSVG(){
  const svg=document.querySelector('#ship .deck-svg');
  const position=event=>{const rect=svg.getBoundingClientRect();return [+(Math.max(0,Math.min(100,100*(event.clientX-rect.left)/rect.width))).toFixed(3),+(Math.max(0,Math.min(100,100*(event.clientY-rect.top)/rect.height))).toFixed(3)];};
  const choose=id=>{const next=current().regions.find(r=>r.id===id)||ui.draft;ui.selected=id;ui.draft=ui.editing?structuredClone(next):null;renderDeckPlans();};
  svg.onclick=event=>{if(ui.drawing){ui.drawing.points.push(position(event));renderDeckPlans();return;}const shape=event.target.closest('[data-region]');if(shape)choose(shape.dataset.region);};
  svg.onkeydown=event=>{
    const vertex=event.target.dataset.vertex;
    if(vertex!==undefined&&['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)){
      event.preventDefault();ui.draft||=structuredClone(selected());const point=ui.draft.points[Number(vertex)],step=event.shiftKey?.5:.1;
      point[0]=Math.max(0,Math.min(100,point[0]+(event.key==='ArrowLeft'?-step:event.key==='ArrowRight'?step:0)));
      point[1]=Math.max(0,Math.min(100,point[1]+(event.key==='ArrowUp'?-step:event.key==='ArrowDown'?step:0)));updateDraft();return;
    }
    if(event.key==='Enter'||event.key===' '){const shape=event.target.closest('[data-region]');if(shape){event.preventDefault();choose(shape.dataset.region);}}
  };
  svg.onpointerdown=event=>{
    const handle=event.target.closest('[data-vertex]');if(!handle)return;
    event.preventDefault();event.stopPropagation();ui.draft||=structuredClone(selected());const index=Number(handle.dataset.vertex);handle.setPointerCapture?.(event.pointerId);
    const move=e=>{ui.draft.points[index]=position(e);updateDraft();};
    const up=()=>{handle.removeEventListener('pointermove',move);handle.removeEventListener('pointerup',up);handle.removeEventListener('pointercancel',up);};
    handle.addEventListener('pointermove',move);handle.addEventListener('pointerup',up);handle.addEventListener('pointercancel',up);
  };
}

function updateDraft(){
  const svg=document.querySelector('#ship .deck-svg'),draft=ui.draft;
  const shape=svg.querySelector(`[data-region="${draft.id}"]`);shape.setAttribute('points',pointText(draft.points));
  svg.querySelectorAll('[data-vertex]').forEach((el,i)=>{el.setAttribute('cx',draft.points[i][0]);el.setAttribute('cy',draft.points[i][1]);});
  document.querySelector('[name=points]').value=JSON.stringify(draft.points);
}

export function editRoomMedia(id){
  const room=roomById[id];if(!room)return;
  const modal=document.getElementById('modalContent');
  modal.innerHTML=`<h2>Images · ${esc(room.label)}</h2><p class="inline-note">Les fichiers sont conservés dans le stockage local runtime ; la salle conserve leurs références dans campaign.json.</p>${roomMediaHTML(room)}<form id="roomMediaEditor" class="editor"><label>Importer une image<input name="file" type="file" accept="image/png,image/jpeg,image/webp,image/gif"></label><label>Ou référence locale existante<select name="assetId"><option value="">— Choisir —</option>${assets.map(a=>`<option value="${a.id}">${esc(a.name)}</option>`).join('')}</select></label><label>Ou chemin d’une image runtime<input name="url" placeholder="/runtime/media/…"></label><label>Légende<input name="caption" maxlength="500"></label><label>Provenance<select name="provenance"><option value="personal">Ajout personnel</option><option value="interpretation">Interprétation</option><option value="official">Source officielle</option></select></label><div id="roomMediaError" class="error" role="status"></div><button class="primary">Ajouter l’image à cette salle</button></form>${(room.media||[]).length?`<h3>Références enregistrées</h3>${room.media.map(m=>`<div class="media-edit-row"><span>${esc(m.caption||m.url)}</span><button data-remove-media="${esc(m.id)}">Retirer de la salle</button></div>`).join('')}`:''}`;
  document.getElementById('modal').classList.add('open');
  document.getElementById('roomMediaEditor').onsubmit=async event=>{
    event.preventDefault();event.submitter.disabled=true;
    try{
      const f=new FormData(event.target),file=f.get('file'),inputs=Number(Boolean(file?.size))+Number(Boolean(f.get('assetId')))+Number(Boolean(f.get('url').trim()));
      if(inputs!==1)throw new Error('Choisis un fichier ou une seule référence locale.');
      let url=f.get('assetId')?'/asset/'+f.get('assetId'):f.get('url').trim();
      if(file?.size){
        if(file.size>8*1024*1024)throw new Error('Image trop volumineuse (8 Mo maximum).');
        const response=await fetch('/api/room-media',{method:'POST',headers:{'Content-Type':file.type},body:file});const result=await response.json();if(!response.ok)throw new Error(result.error);url=result.url;
      }
      if(!mediaUrl(url))throw new Error('Utilise un média du catalogue ou un chemin /runtime/media/.');
      const next=structuredClone(D),edited=next.ship.rooms.find(r=>r.id===id);edited.media||=[];edited.media.push({id:crypto.randomUUID(),url,caption:f.get('caption'),provenance:f.get('provenance')});
      await persist(next);editRoomMedia(id);if(state.view==='ship')renderDeckPlans();
    }catch(error){document.getElementById('roomMediaError').textContent=error.message;event.submitter.disabled=false;}
  };
  modal.querySelectorAll('[data-remove-media]').forEach(button=>button.onclick=async()=>{
    try{const next=structuredClone(D),edited=next.ship.rooms.find(r=>r.id===id);edited.media=edited.media.filter(m=>m.id!==button.dataset.removeMedia);await persist(next);editRoomMedia(id);if(state.view==='ship')renderDeckPlans();}catch(error){document.getElementById('roomMediaError').textContent=error.message;}
  });
}
