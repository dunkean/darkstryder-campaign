import {esc,state} from './store.js';
import {markdown} from './markdown.js';

const usages={darkstryder_scenario:'Scénario DarkStryder',reference_lore:'Lore de référence',rules:'Règles',inspiration:'Inspiration',campaign_customization:'Personnalisation de campagne'};
async function request(url,options){
  const response=await fetch(url,options),data=await response.json();
  if(!response.ok)throw new Error(data.error||'KBase indisponible');return data;
}
export async function renderKBase(){
  const root=document.getElementById('kbase');
  root.innerHTML=`<div class="toolbar"><h2>KBase structurée</h2><button id="kbaseRefresh">Actualiser</button><a href="/api/kbase/export" download="kbase-v1.json">Exporter le graphe</a></div><p class="inline-note">Extractions sourcées à relire. Le scénario original, les ajouts et l’inspiration restent distincts. Aucun état réel de partie n’est importé.</p><p id="kbaseProgress" role="status">Chargement…</p><form id="kbaseSearch" class="toolbar"><input name="q" placeholder="Nom, résumé, contenu…" aria-label="Rechercher dans la KBase"><select name="family" aria-label="Famille"><option value="">Toutes les familles</option>${['WORLD','NARRATIVE','RULES','INSPIRATION'].map(f=>`<option>${f}</option>`).join('')}</select><select name="usage" aria-label="Usage"><option value="">Tous les usages</option>${Object.entries(usages).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select><button>Chercher</button></form><p id="kbaseCount"></p><div id="kbaseResults" class="entity-grid"></div>`;
  async function search(event){
    event?.preventDefault();
    const query=new URLSearchParams(new FormData(document.getElementById('kbaseSearch')));query.set('limit','60');
    try{
      const data=await request('/api/kbase/nodes?'+query),nodes=Array.isArray(data)?data:data.nodes||[];
      if(state.view!=='kbase')return;
      document.getElementById('kbaseCount').textContent=`${data.total??nodes.length} résultat(s) · ${nodes.length} affiché(s)`;
      document.getElementById('kbaseResults').innerHTML=nodes.map(n=>`<button class="card entity-card" data-kbase-node="${esc(n.id)}"><span class="tag">${esc(n.family)} · ${esc(n.type)}</span><h3>${esc(n.name)}</h3><p>${esc(n.summary)}</p><span class="muted">${esc(usages[n.usage]||n.usage)} · ${esc(n.reviewStatus||n.status||'à relire')}</span></button>`).join('')||'<p class="muted">Aucune entrée pour ce filtre. Le traitement automatique enrichit progressivement la base.</p>';
      document.querySelectorAll('[data-kbase-node]').forEach(button=>button.onclick=()=>openKBaseNode(button.dataset.kbaseNode));
    }catch(error){if(state.view==='kbase')document.getElementById('kbaseCount').textContent=error.message;}
  }
  document.getElementById('kbaseSearch').onsubmit=search;
  document.getElementById('kbaseRefresh').onclick=renderKBase;
  try{
    const summary=await request('/api/kbase/summary');
    if(state.view!=='kbase')return;
    const counts=summary.counts||summary,jobs=summary.jobs||{};
    document.getElementById('kbaseProgress').textContent=`${counts.nodes??0} nœuds · ${counts.relationships??0} relations · ${counts.evidence??counts.citations??0} références · lots terminés ${jobs.complete??0}, en attente ${jobs.pending??0}, erreurs ${jobs.failed??0}`;
    if(summary.extractionStage)document.getElementById('kbaseProgress').textContent+=` · ${summary.extractionStage.status==='awaiting-human-audit'?'Pause pour audit des deux premiers livres':summary.extractionStage.status==='blocked-quota'?'Arrêt : quota Luna atteint':summary.extractionStage.status}`;
    await search();
  }catch(error){if(state.view==='kbase')document.getElementById('kbaseProgress').textContent=error.message;}
}
export async function openKBaseNode(id){
  try{
    const n=await request('/api/kbase/node?id='+encodeURIComponent(id)),groups=new Map();
    for(const citation of n.evidence||n.citations||[]){
      const key=JSON.stringify([citation.sourceId,citation.page,citation.quote]);
      if(!groups.has(key))groups.set(key,{...citation,fields:[]});
      const item=groups.get(key);item.fields=[...new Set([...item.fields,...citation.fields||[]])];
    }
    const evidence=[...groups.values()];
    document.getElementById('modalContent').innerHTML=`<p class="tiny code">${esc(n.id)} · ${esc(n.family)} / ${esc(n.type)}</p><h2>${esc(n.name)}</h2><p>${esc(usages[n.usage]||n.usage)} · ${esc(n.reviewStatus||n.status||'à relire')}</p><p>${esc(n.summary)}</p><div class="markdown">${markdown(n.content||'')}</div><h3>Assertions et propriétés extraites</h3><pre class="source-text">${esc(JSON.stringify(n.properties||{},null,2))}</pre><h3>Sources et passages justificatifs</h3>${evidence.map(e=>`<section><button data-evidence-source="${esc(e.sourceId)}" data-evidence-page="${esc(e.page)}">${esc(e.sourceName||e.sourceId)} · ${esc(e.unitLabel||'page PDF')} ${esc(e.page)}</button><p class="muted">Champs justifiés : ${esc((e.fields||[]).join(', '))}</p><blockquote>${esc(e.quote)}</blockquote></section>`).join('')||'<p class="muted">Voir les assertions individuelles ci-dessous.</p>'}<details><summary>Assertions sourcées et relations</summary><pre class="source-text">${esc(JSON.stringify({claims:n.claims||[],relationships:n.relationships||[]},null,2))}</pre></details><div class="detail-actions"><button id="kbaseEdit">Modifier la rédaction / relecture</button></div>`;
    document.getElementById('modal').classList.add('open');
    const additions=[...new Set((n.claims||[]).filter(c=>c.field==='content'&&typeof c.value==='string'&&c.value!==n.content).map(c=>c.value))];
    if(additions.length)document.querySelector('#modalContent details').insertAdjacentHTML('beforebegin',`<details><summary>Compléments extraits au fil des lectures (${additions.length}) — à relire</summary>${additions.map(text=>`<div class="markdown">${markdown(text)}</div>`).join('<hr>')}</details>`);
    document.querySelectorAll('[data-evidence-source]').forEach(button=>button.onclick=()=>window.openSource(button.dataset.evidenceSource,Number(button.dataset.evidencePage)));
    document.getElementById('kbaseEdit').onclick=()=>editKBaseNode(n);
  }catch(error){document.getElementById('saveStatus').textContent=error.message;}
}
function editKBaseNode(n){
  document.getElementById('modalContent').innerHTML=`<h2>Rédaction : ${esc(n.name)}</h2><p class="inline-note">Les modifications personnelles sont enregistrées séparément des assertions et citations extraites. Une relecture ne transforme pas un ajout fan en canon.</p><form id="kbaseEditor" class="editor"><label>Résumé<textarea name="summary">${esc(n.summary)}</textarea></label><label>Contenu Markdown<textarea name="content">${esc(n.content)}</textarea></label><label>Tags, séparés par des virgules<input name="tags" value="${esc((n.tags||[]).join(', '))}"></label><label>Relecture<select name="reviewStatus"><option value="unreviewed">À relire</option><option value="reviewed" ${n.reviewStatus==='reviewed'?'selected':''}>Relu</option></select></label><p id="kbaseEditError" class="error"></p><button>Enregistrer</button></form>`;
  document.getElementById('kbaseEditor').onsubmit=async event=>{
    event.preventDefault();event.submitter.disabled=true;
    try{
      const form=new FormData(event.target),patch={summary:form.get('summary'),content:form.get('content'),tags:String(form.get('tags')).split(',').map(s=>s.trim()).filter(Boolean),reviewStatus:form.get('reviewStatus')};
      await request('/api/kbase/node?id='+encodeURIComponent(n.id),{method:'PUT',headers:{'Content-Type':'application/json','If-Match':n.revision},body:JSON.stringify(patch)});
      await openKBaseNode(n.id);if(state.view==='kbase')await renderKBase();
    }catch(error){document.getElementById('kbaseEditError').textContent=error.message;event.submitter.disabled=false;}
  };
}
