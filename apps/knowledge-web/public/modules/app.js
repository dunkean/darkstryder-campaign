import {load,D,M,entities,state,esc,nrm} from './store.js';
import * as occupancy from './occupancy.js';
import * as overview from './overview.js';
import * as crew from './crew.js';
import * as relations from './relations.js';
import * as random from './random.js';
import * as details from './details.js';
import {renderShip as renderShipList} from './ship.js';
import * as knowledge from './knowledge.js';
import { editRoomMedia,roomMediaHTML } from './deck-plans.js';

Object.assign(window,occupancy,overview,crew,relations,random,details,knowledge,{renderShipList});
window.openPerson=id=>{details.openPerson(id);document.getElementById('modalContent').insertAdjacentHTML('beforeend',`${M.find(p=>p.id===id)?.stats.generated?'<p class="inline-note">Caractéristiques générées : ces valeurs ne constituent pas des statistiques officielles du livre.</p>':''}<div class="detail-actions"><button class="primary" onclick="editPerson('${id}')">Modifier ce personnage</button></div>`);};
window.openRoom=id=>{details.openRoom(id);document.getElementById('modalContent').insertAdjacentHTML('beforeend',roomMediaHTML(D.ship.rooms.find(r=>r.id===id))+'<div class="detail-actions"><button id="roomMediaButton">Ajouter / modifier les images</button><button onclick="editDataset()">Modifier les données du vaisseau</button></div>');document.getElementById('roomMediaButton').onclick=()=>editRoomMedia(id);};
const renderers={overview:()=>{overview.renderOverview();document.getElementById('overview').insertAdjacentHTML('afterbegin',`<div class="toolbar"><h2>Base de campagne</h2><button onclick="editDataset()">Éditer les données FarStar</button></div><div class="landing">${[['Équipage',M.length,'crew'],['FarStar',D.ship.rooms.length+' salles','ship'],['Sources','16 livres','library'],['Fiches',entities.entities.length,'planet']].map(([label,n,id])=>`<button class="card" onclick="location.hash='${id}'"><b>${n}</b>${label}</button>`).join('')}</div>`);},ship:()=>knowledge.renderMap('ship'),crew:crew.renderCrew,hierarchy:knowledge.renderHierarchy,skills:crew.renderSkills,relations:relations.renderRelations,droids:relations.renderDroids,random:random.renderRandom,map:()=>knowledge.renderMap('map'),library:knowledge.renderLibrary};
for(const type of ['npc','faction','adventure','event','planet'])renderers[type]=()=>knowledge.renderEntities(type);
window.renderAll=()=>{knowledge.stopLibraryUpdates();(renderers[state.view]||renderers.overview)();};
function navigate(){
  const [view,id]=location.hash.slice(1).split('/');state.view=renderers[view]?view:'overview';
  document.querySelectorAll('.view').forEach(el=>el.classList.toggle('active',el.id===state.view));document.querySelectorAll('#nav button').forEach(el=>el.classList.toggle('active',el.dataset.view===state.view));window.renderAll();
  if(id)(view==='crew'?window.openPerson:knowledge.openEntity)(decodeURIComponent(id));
}
window.updateTime=()=>{
  const [hour,minute]=document.getElementById('hour').value.split(':').map(Number);state.hour=hour+minute/60;state.alert=document.getElementById('alert').checked;
  const {shift1Start:a,shift2Start:b}=entities.schedule,h=state.hour;
  state.mode=state.alert?'alert':(((h-a+24)%24)<((b-a+24)%24)?'shift1':'shift2');document.getElementById('mode').value=state.mode;window.renderAll();
};
document.getElementById('hour').onchange=window.updateTime;document.getElementById('alert').onchange=window.updateTime;
document.getElementById('mode').onchange=e=>{state.mode=e.target.value;state.alert=state.mode==='alert';document.getElementById('alert').checked=state.alert;window.renderAll();};
document.getElementById('schedule').onclick=knowledge.editSchedule;
document.getElementById('nav').onclick=e=>{const button=e.target.closest('[data-view]');if(button)location.hash=button.dataset.view;};
document.getElementById('newEntity').onclick=()=>knowledge.editEntity(null,['npc','faction','adventure','event','planet'].includes(state.view)?state.view:'npc');
document.getElementById('modal').onclick=e=>{if(e.target.id==='modal')details.closeModal();};
document.addEventListener('keydown',e=>{if(e.key==='Escape')details.closeModal();if((e.ctrlKey||e.metaKey)&&e.key==='k'){e.preventDefault();document.getElementById('globalSearch').focus();}});
document.getElementById('globalSearch').oninput=e=>{
  const q=nrm(e.target.value),result=document.getElementById('searchResults');if(!q){result.innerHTML='';return;}
  const rows=[...M.map(p=>({id:p.id,name:p.name,text:[p.name,p.species,p.section,p.history.summary].join(' '),kind:'crew'})),...entities.entities.map(p=>({id:p.id,name:p.name,text:[p.name,p.summary,p.body,...p.tags].join(' '),kind:p.type==='location'?'planet':p.type}))];
  result.innerHTML=rows.filter(r=>nrm(r.text).includes(q)).slice(0,20).map(r=>`<button class="search-result" data-kind="${esc(r.kind)}" data-id="${esc(r.id)}">${esc(r.name)}<span class="sub"> · ${esc(r.kind)}</span></button>`).join('')||'<span class="muted">Aucun résultat</span>';
};
document.getElementById('searchResults').onclick=e=>{const button=e.target.closest('button');if(button){const view=renderers[button.dataset.kind]?button.dataset.kind:'npc';location.hash=`${view}/${encodeURIComponent(button.dataset.id)}`;document.getElementById('searchResults').innerHTML='';}};
window.addEventListener('hashchange',navigate);
try{await load();window.updateTime();navigate();knowledge.feedback('Données locales chargées');}catch(error){knowledge.feedback(error.message,true);document.getElementById('overview').innerHTML=`<div class="error">${esc(error.message)}</div>`;}
