const $=id=>document.getElementById(id);
const kindLabels={vehicle:'Véhicules',craft:'Vaisseaux',location:'Lieux',system:'Systèmes',stellar_route:'Routes stellaires',unknown:'Non classés',person:'Personnages',organization:'Organisations',species:'Espèces',creature:'Créatures',droid:'Droïdes',equipment:'Équipement',document:'Documents',scenario:'Scénarios',event:'Événements',rule:'Règles',skill:'Compétences',force_power:'Pouvoirs de la Force',archetype:'Archétypes'};
const kindLabel=kind=>kindLabels[kind]||kind;
let tab='browse',offset=0,limit=30,hasMore=false,selected='',recordFilter='',listSequence=0,browseKind='',detailSequence=0,readerSequence=0,workspaceState=null;
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);if(cls)n.className=cls;return n}
function add(parent,tag,text,cls){const n=el(tag,text,cls);parent.append(n);return n}
function clear(node){node.replaceChildren()}
function str(x){return x==null?'—':typeof x==='string'?x:JSON.stringify(x,null,2)}
function dl(parent,fields){const d=add(parent,'dl');for(const [key,value] of fields){add(d,'dt',key);add(d,'dd',str(value))}return d}
function box(parent,title,value){const d=add(parent,'details');add(d,'summary',title);add(d,'pre',str(value));return d}
function link(parent,label,url){const a=add(parent,'a',label);a.href=url;return a}
async function api(path){const response=await fetch(path);const data=await response.json();if(!response.ok)throw Error(data.message||data.error||'Erreur HTTP '+response.status);return data}
function params(){const p=new URLSearchParams({offset,limit});if(tab==='browse'){for(const [key,id] of [['section','section'],['duplicates','duplicates'],['source_id','source']])if($(id).value)p.set(key,$(id).value);if(browseKind)p.set('kind',browseKind);if($('q').value.trim())p.set('q',$('q').value.trim());return p}for(const [key,id] of [['source_id','source'],['scope','scope']]){const value=$(id).value.trim();if(value)p.set(key,value)}if(tab==='questions'){for(const [key,id] of [['task','task'],['status','status']])if($(id).value)p.set(key,$(id).value);if(recordFilter)p.set('record_id',recordFilter)}else if($('q').value.trim())p.set('q',$('q').value.trim());return p}
async function list(){const token=tab,sequence=++listSequence;const box=$('list');clear(box);$('loading').textContent='Chargement…';try{const path=tab==='browse'?'/review/api/browse/aggregates':'/review/api/'+tab;const data=await api(path+'?'+params());if(tab!==token||sequence!==listSequence)return;hasMore=data.has_more;$('page').textContent=data.items.length?`${offset+1}–${offset+data.items.length}${hasMore?' +':''}`:'0';$('prev').disabled=!offset;$('next').disabled=!hasMore;if(data.available===false)add(box,'p','Les agrégats sont en cours de préparation.','empty');else if(!data.items.length)add(box,'p','Aucun résultat.','empty');for(const item of data.items){const b=add(box,'button',undefined,'item');b.type='button';b.dataset.id=item.id;add(b,'strong',tab==='questions'?`${item.a_name||item.a} ${item.b_name?'↔ '+item.b_name:''}`:item.name);const line=add(b,'small');if(tab==='questions'){add(line,'span',item.task,'badge');add(line,'span',item.status,'badge');line.append(` ${item.dataset||''} · ${item.id}`)}else if(tab==='browse'){add(line,'span',item.kind,'badge');line.append(` ${item.member_count} documents · ${item.source_count} collections sources · ${item.universe||''}`);if(item.warnings?.length)add(line,'span',`⚠ ${item.warnings.length} avertissement(s)`,'badge')}else line.textContent=tab==='documents'?`${item.dataset} · ${item.kind} · ${item.body_characters} caractères`:`${item.dataset} · ${item.kind} · ${item.id}`;b.addEventListener('click',()=>choose(item.id));if(item.id===selected)b.classList.add('selected')}}catch(e){if(sequence===listSequence)add(box,'p',e.message,'error')}finally{if(sequence===listSequence)$('loading').textContent=''}}
async function choose(id){selected=id;const currentTab=tab,sequence=++detailSequence;readerSequence++;workspaceState=null;for(const b of document.querySelectorAll('.item'))b.classList.toggle('selected',b.dataset.id===id);const d=$('detail');clear(d);add(d,'p','Chargement…','muted');try{const path=currentTab==='browse'?'/review/api/workspace/'+encodeURIComponent(id)+'?limit=100':'/review/api/'+currentTab+'/'+encodeURIComponent(id);const data=await api(path);if(selected!==id||tab!==currentTab||sequence!==detailSequence)return;clear(d);if(tab==='questions')question(d,data);else if(tab==='entities')entityView(d,data);else if(tab==='browse')aggregateView(d,data);else documentView(d,data)}catch(e){if(selected===id&&tab===currentTab&&sequence===detailSequence){clear(d);add(d,'p',e.message,'error')}}}
function openEntity(id){tab='entities';recordFilter='';setTab();choose(id)}
function openAggregate(id){if(tab!=='browse'){tab='browse';recordFilter='';setTab()}choose(id)}
function docCard(parent,r){const c=add(parent,'div',undefined,'card');add(c,'h3',r.name||r.id);dl(c,[['Document',r.id],['Source',`${r.dataset||''} · ${r.source_title||''}`],['Type',r.kind],['Origine',r.origin],['Univers',r.universe],['Version',r.current_version],['Scope',r.scope],['Clé agrégat',r.aggregate_key]]);const b=add(c,'button','Lire le texte');b.onclick=()=>{tab='documents';recordFilter='';setTab();choose(r.id)};c.append(' ');const e=add(c,'button','Voir l’identité historique');e.onclick=()=>openEntity(r.entity_id);if(r.aggregate_id){c.append(' ');const a=add(c,'button','Agrégat');a.onclick=()=>openAggregate(r.aggregate_id)}c.append(' · ');link(c,'Fiche JSON','/records/'+encodeURIComponent(r.id));if(r.annotations?.length)box(c,'Annotations actuelles',r.annotations);return c}
const statusHelp={deferred_by_group:'Non résolue : abstention du groupe ; les candidats sont conservés.',resolved_by_group:'Occurrence classée « aucun candidat » ou usage générique.',covered_by_group:'Hypothèse couverte par une preuve issue du groupe.',auto_grouped:'Regroupement heuristique sans OpenJev.',auto_linked:'Lien extrait automatiquement du texte, sans OpenJev.',auto_typed:'Type attribué par règle, sans OpenJev.',dismissed_lexical_candidate:'Candidat écarté par règle lexicale, sans OpenJev.',superseded_text_mining:'Proposition remplacée par le remine du texte, sans OpenJev.',deferred_to_merger:'Question reportée au regroupement, sans OpenJev.'};
function passagesIn(value,out=[]){if(!value||out.length>=8)return out;if(Array.isArray(value)){for(const item of value)passagesIn(item,out)}else if(typeof value==='object'){for(const [key,item] of Object.entries(value)){if(typeof item==='string'&&/passage|excerpt|snippet|evidence|quote/i.test(key)&&item.length>15)out.push({champ:key,texte:item});else if(typeof item==='object')passagesIn(item,out)}}return out}
function question(d,q){add(d,'h1','Question · '+q.task);dl(d,[['Identifiant',q.id],['Statut',q.status],['Décision',q.decision],['Score',q.score],['Prédicat',q.predicate],['Mise à jour',q.updated_at],['Erreur',q.error]]);if(statusHelp[q.status])add(d,'p',statusHelp[q.status],'muted');for(const [label,id] of [['Groupe parent',q.parent_group_id],['Preuve de couverture',q.covered_by_proposal]])if(id){link(d,label,'/review#question='+encodeURIComponent(id));d.append(' · ')}if(q.reasons?.length)box(d,'Raisons de sélection',q.reasons);const o=q.openjev;if(o){add(d,'h2','Critères OpenJev de cette question');add(d,'p',o.instructions||'');if(o.unavailable)add(d,'p',o.unavailable,'error');const crit=add(d,'div',undefined,'card');add(crit,'h3','Critères de décision');dl(crit,Object.entries(o.criteria||{}));const evidence=passagesIn(o.context);if(evidence.length){add(d,'h3','Passages fournis');for(const p of evidence){add(d,'p',p.champ,'muted');add(d,'div',p.texte,'reader')}}box(d,'Contexte de la question (aperçu borné)',o.context)}if(q.result)box(d,q.automatic_heuristic?'Résultat de la règle':'Réponse et confiance',q.result);if(q.context)box(d,'Contexte de la proposition',q.context);if(q.input_versions?.length)box(d,'Versions utilisées',q.input_versions);add(d,'h2','Documents et annotations');const cards=add(d,'div',undefined,'cards');for(const r of q.documents||[])docCard(cards,r);if(!q.documents?.length)add(d,'p','Aucun document associé trouvé.','empty')}
function documentView(d,r){add(d,'h1',r.name);dl(d,[['Document',r.id],['Clé source',r.source_key],['Source',`${r.dataset} · ${r.source_title}`],['Origine de source',r.source_origin],['Type',r.kind],['Origine',r.origin],['Univers',r.universe],['Version',r.current_version],['Empreinte',r.sha256],['Alias',r.aliases],['Périmètre',r.scope],['Localisation documentaire',r.locator],['Longueur',`${r.body_characters} caractères`],['Clé agrégat',r.aggregate_key],['Règle de regroupement',r.aggregate_rule]]);link(d,'Fiche JSON','/records/'+encodeURIComponent(r.id));d.append(' · ');const e=add(d,'button','Voir l’identité historique');e.onclick=()=>openEntity(r.entity_id);if(r.aggregate_id){d.append(' ');const a=add(d,'button','Voir l’agrégat');a.onclick=()=>openAggregate(r.aggregate_id)}if(r.annotations?.length)box(d,'Annotations actuelles',r.annotations);const b=add(d,'button','Voir les questions de ce document');b.onclick=()=>{tab='questions';recordFilter=r.id;$('scope').value='';$('source').value='';$('task').value='';$('status').value='';setTab()};add(d,'h2','Texte source');const reader=add(d,'div',undefined,'reader');const controls=add(d,'div',undefined,'pager');const more=add(controls,'button','Charger les passages suivants');let passageOffset=0;more.onclick=async()=>{more.disabled=true;try{const data=await api('/passages?'+new URLSearchParams({record_id:r.id,offset:passageOffset,limit:5}));for(const p of data.passages)reader.append(document.createTextNode(p.text));passageOffset+=data.passages.length;more.hidden=!data.pagination.next_url;more.disabled=false}catch(e){add(reader,'p',e.message,'error')}};more.click()}
function entityView(d,e){add(d,'h1',e.name);dl(d,[['Entité',e.id],['Type',e.kind],['Fiche de référence',e.anchor_record_id],['Révision',e.revision],['Redirection',e.redirect_to]]);link(d,'Entité JSON','/entities/'+encodeURIComponent(e.id));add(d,'h2','Documents de l’entité');const cards=add(d,'div',undefined,'cards');for(const r of e.documents)docCard(cards,r);if(e.has_more){const b=add(d,'button','Charger davantage de fiches');b.onclick=async()=>{const next=await api('/review/api/entities/'+encodeURIComponent(e.id)+'?'+new URLSearchParams({offset:e.offset+e.limit,limit:e.limit}));for(const r of next.documents)docCard(cards,r);e.offset=next.offset;e.has_more=next.has_more;b.hidden=!e.has_more}}}
async function browseTypes(){const target=$('browse-types');clear(target);try{const data=await api('/review/api/browse/types?'+new URLSearchParams({section:$('section').value}));if(!data.available){add(target,'span','Classement en cours de préparation.','muted');return}const total=data.types.reduce((sum,t)=>sum+t.count,0);const label=add(target,'label','Type d’objet');label.htmlFor='browse-type-select';const select=add(target,'select');select.id='browse-type-select';let option=add(select,'option',`Tous · ${total}`);option.value='';const geography=add(select,'optgroup');geography.label='Géographie';const transport=add(select,'optgroup');transport.label='Vaisseaux et véhicules';const others=add(select,'optgroup');others.label='Autres objets';for(const t of data.types){const group=['location','system','stellar_route'].includes(t.kind)?geography:['craft','vehicle'].includes(t.kind)?transport:others;const o=add(group,'option',`${kindLabel(t.kind)} · ${t.count}`);o.value=t.kind}select.value=browseKind;select.onchange=()=>{browseKind=select.value;offset=0;list()}}catch(e){add(target,'span',e.message,'error')}}
function aggregateView(d,data){
  const state={id:data.id,data,docs:new Map(),expanded:new Set(),selectedDoc:null,mode:'paragraphs',filter:'all',paragraphOffset:0,passageOffset:0};
  workspaceState=state;
  const head=add(d,'div',undefined,'workspace-head');
  const title=add(head,'div');add(title,'h1',data.name||data.id);
  add(title,'p',`${kindLabel(data.kind)||'Type inconnu'} · ${data.counts?.records??0} fiches · ${data.counts?.source_collections??0} sources · ${data.counts?.web_records??0} pages web`,'muted');
  const statusLabels={planned:'Référence choisie · paragraphes en attente',scored:'Tri des paragraphes terminé',pending:'Référence choisie · décisions non publiées',complete:'Sélection publiée',running:'Sélection en cours',partial:'Sélection partielle',unavailable:'Sélection indisponible'};
  const selectedStatus=(!data.selection?.available?'Sélection non encore évaluée':statusLabels[data.selection.status]||'Sélection disponible')+
    (data.selection?.paused?' · Jev en pause':'');
  add(head,'span',selectedStatus,'badge');
  if(data.warnings?.length){const warning=add(d,'details',undefined,'workspace-warning');add(warning,'summary',`${data.warnings.length} point(s) à vérifier`);for(const value of data.warnings)add(warning,'p',str(value),'warning')}
  const panes=add(d,'div',undefined,'workspace');state.panes=panes;
  const sources=add(panes,'div',undefined,'workspace-sources');sources.setAttribute('aria-label','Documents sources');state.sources=sources;
  const reader=add(panes,'div',undefined,'workspace-reader');reader.setAttribute('aria-label','Lecture de la source');state.reader=reader;
  if(data.reference)state.docs.set(data.reference.id,data.reference);
  for(const doc of data.documents?.items||[])state.docs.set(doc.id,doc);
  state.nextOffset=(data.documents?.offset||0)+(data.documents?.items?.length||0);
  state.hasMore=!!data.documents?.has_more;
  renderWorkspaceSources(state);
  const first=data.reference||[...state.docs.values()][0];
  if(first)selectWorkspaceDoc(state,first.id);else add(reader,'p','Aucune fiche source pour cette entrée.','empty');
  const advanced=add(d,'details',undefined,'workspace-advanced');add(advanced,'summary',`Contexte technique et liens · ${data.counts?.legacy_blocks??0} anciens blocs de texte`);
  if(data.type_annotation)box(advanced,'Justification du classement par text mining',data.type_annotation);
  advanced.addEventListener('toggle',()=>{if(advanced.open&&!advanced.dataset.loaded){advanced.dataset.loaded='1';loadLegacyDetails(advanced,data.id,state)}});
}
function workspaceBadge(doc){
  if(doc.selection?.status==='stale')return 'Version modifiée';
  if(doc.is_reference||doc.selection?.status==='reference')return 'Référence';
  const counts=doc.selection?.counts||{};
  if(doc.selection?.status==='pending'||doc.selection?.status==='unavailable'||doc.selection?.status==='stale')return 'En attente';
  if((counts.add||0)>0)return 'Utile';
  if((counts.add_uncertain||0)>0)return 'Incertain';
  if((counts.skip_redundant||0)>0)return 'Redondant';
  return 'En attente';
}
function sourceName(doc){return doc.source_label||doc.source_group||doc.dataset||doc.name||doc.id}
function documentDecision(doc){const d=doc.documentary_decision;if(!d)return '';const labels={retain:'conservé',exclude_redundant:'redondant',hold:'identité à vérifier'};const reasons={exact_normalized_text:'copie exacte',all_units_covered:'information déjà couverte',exact_unit_coverage:'passages déjà couverts',protected_information:'variantes ou profils protégés',new_numeric_or_dice_information:'valeurs chiffrées différentes',identity_unresolved:'identité non résolue',different_entity:'autre identité',no_comparable_retained_unit:'aucun passage comparable',information_or_uncertainty_to_preserve:'information ou incertitude à conserver'};return `${labels[d.merge_selection]||d.merge_selection}${reasons[d.reason]?' · '+reasons[d.reason]:''}`}
function workLabel(work){const kind={homemade:'création amateur',compilation:'compilation',web_content:'contenu web',unresolved_citation:'citation à identifier',unclassified_content:'nature non déterminée',content:'contenu cité'}[work.kind];return [work.title,work.code,work.pages?`${work.page_basis==='pdf_page'?'pages PDF':'p.'} ${work.pages}`:'',kind].filter(Boolean).join(' · ')}
function sourceSubline(doc){const parts=[];const works=doc.provenance?.works||[];if(works.length)parts.push(works[0].title+(works.length>1?` (+${works.length-1})`:''));else if(doc.provenance)parts.push('Ouvrage non indiqué');if(doc.page_label)parts.push(doc.page_label);else if(doc.pages?.length)parts.push(str(doc.pages));if(doc.selection?.total_paragraphs!=null)parts.push(`${doc.selection.total_paragraphs} ${doc.selection.units_policy?.startsWith('document-sections/2')?'blocs':'paragraphes'}`);return parts.join(' · ')}
function renderWorkspaceSources(state){
  const pane=state.sources,previousScroll=pane.scrollTop;clear(pane);
  add(pane,'h2','Documents sources');
  if(state.data.reference){add(pane,'h3','Référence choisie');sourceButton(pane,state,state.data.reference)}
  const groups=new Map(),web=[];
  for(const doc of state.docs.values()){
    if(doc.id===state.data.reference?.id)continue;
    if(doc.source_type==='web'){web.push(doc);continue}
    const key=doc.source_group_id||doc.source_group||doc.id;
    if(!groups.has(key))groups.set(key,[]);
    groups.get(key).push(doc);
  }
  add(pane,'h3','Livres et autres sources');
  if(!groups.size)add(pane,'p','Aucune autre source chargée.','muted');
  for(const [key,docs] of groups){
    const sameBook=!!(state.data.reference?.source_group_id&&key===state.data.reference.source_group_id);
    if(docs.length===1&&!sameBook){sourceButton(pane,state,docs[0]);continue}
    const group=add(pane,'div',undefined,'source-group');
    const finished=docs.filter(d=>d.selection?.status==='scored').length;
    const heading=add(group,'button',`${sameBook?'Autres fiches du même livre':docs[0].source_group||sourceName(docs[0])} · ${docs.length} fiche(s) · ${finished}/${docs.length} triées`,'group-toggle');
    heading.setAttribute('aria-expanded',state.expanded.has(key)?'true':'false');
    const children=add(group,'div');children.hidden=!state.expanded.has(key);
    heading.onclick=()=>{children.hidden=!children.hidden;heading.setAttribute('aria-expanded',String(!children.hidden));if(children.hidden)state.expanded.delete(key);else state.expanded.add(key)};
    for(const doc of docs)sourceButton(children,state,doc,true);
  }
  if(web.length){add(pane,'h3','Pages web récupérées');for(const doc of web)sourceButton(pane,state,doc)}
  if(state.hasMore){const more=add(pane,'button','Charger d’autres sources','load-more');more.onclick=async()=>{more.disabled=true;try{const query=new URLSearchParams({offset:state.nextOffset,limit:100});const data=await api('/review/api/workspace/'+encodeURIComponent(state.id)+'?'+query);if(workspaceState!==state)return;for(const doc of data.documents?.items||[])state.docs.set(doc.id,doc);state.nextOffset+=(data.documents?.items?.length||0);state.hasMore=!!data.documents?.has_more;renderWorkspaceSources(state)}catch(e){more.disabled=false;add(pane,'p',e.message,'error')}}}
  pane.scrollTop=previousScroll;
}
function sourceButton(parent,state,doc,withinGroup=false){
  const button=add(parent,'button',undefined,'source-item');button.type='button';button.dataset.recordId=doc.id;
  button.title=sourceName(doc)+(doc.page_label?' · '+doc.page_label:'');
  button.setAttribute('aria-pressed',String(state.selectedDoc===doc.id));
  button.classList.toggle('is-reference',!!doc.is_reference);
  button.classList.toggle('selected',state.selectedDoc===doc.id);
  const line=add(button,'span',undefined,'source-item-head');add(line,'strong',withinGroup?`Autre fiche · ${doc.page_label||doc.name||'sans page'}`:sourceName(doc));add(line,'span',workspaceBadge(doc),'badge status-'+badgeClass(doc));
  const sub=withinGroup?[doc.name,doc.selection?.total_paragraphs!=null?`${doc.selection.total_paragraphs} paragraphes`:null].filter(Boolean).join(' · '):sourceSubline(doc);if(sub)add(button,'small',sub);
  button.onclick=()=>selectWorkspaceDoc(state,doc.id);
}
function selectWorkspaceDoc(state,id){
  if(workspaceState!==state)return;
  const doc=state.docs.get(id);if(!doc)return;
  state.selectedDoc=id;state.mode='paragraphs';state.filter='all';state.paragraphOffset=0;state.passageOffset=0;
  if(doc.source_type!=='web'&&id!==state.data.reference?.id)state.expanded.add(doc.source_group_id||doc.source_group||doc.id);
  renderWorkspaceSources(state);renderWorkspaceReader(state,doc);
}
function safeExternal(parent,label,value){
  if(!value)return;
  try{const url=new URL(value);if(!['http:','https:'].includes(url.protocol)||url.username||url.password)return;
    const anchor=link(parent,label,url.href);anchor.target='_blank';anchor.rel='noopener noreferrer';
  }catch{return}
}
function badgeClass(doc){return doc.selection?.status==='stale'?'stale':doc.is_reference?'reference':(doc.selection?.counts?.add?'add':doc.selection?.counts?.add_uncertain?'add_uncertain':doc.selection?.counts?.skip_redundant?'skip_redundant':'pending')}
function renderWorkspaceReader(state,doc){
  const panel=state.reader;clear(panel);
  const heading=add(panel,'div',undefined,'reader-heading');
  add(heading,'h2',sourceName(doc));
  add(heading,'span',workspaceBadge(doc),'badge status-'+badgeClass(doc));
  if(doc.name&&doc.name!==sourceName(doc))add(panel,'p',`Fiche : ${doc.name}`,'muted');
  const sourceTypes={extracted:'Fiche extraite',web:'Page web récupérée',structured:'Base structurée',transcription:'Transcription'};
  const location=[doc.page_label,sourceTypes[doc.source_type]||doc.source_type].filter(Boolean).join(' · ');
  if(location)add(panel,'p',location,'muted');
  if(doc.provenance){
    const provenance=add(panel,'div',undefined,'document-provenance');
    const origin=add(provenance,'p');add(origin,'strong','Origine : ');origin.append(document.createTextNode(doc.provenance.acquisition.label));
    const works=doc.provenance.works||[];
    if(!works.length)add(provenance,'p','Source documentaire : non indiquée dans les données récupérées.','muted');
    else{add(provenance,'strong','Source documentaire'+(works.length>1?'s':'')+' :');const list=add(provenance,'ul');for(const work of works.slice(0,3))add(list,'li',workLabel(work));if(works.length>3){const more=add(provenance,'details');add(more,'summary',`${works.length-3} autres citations`);const tail=add(more,'ul');for(const work of works.slice(3))add(tail,'li',workLabel(work));}}
    safeExternal(provenance,'Dépôt de la base d’origine',doc.provenance.acquisition.repository_url);
  }
  const selection=doc.selection||{};
  if(selection.units_policy?.startsWith('document-sections/2'))add(panel,'p','Découpage algorithmique en blocs cohérents. Les votes sur les anciens fragments sont archivés.','muted');
  if(selection.status==='pending'||selection.status==='unavailable'||selection.status==='stale')
    add(panel,'p',selection.status==='stale'?'Décisions périmées : nouvelle évaluation en attente.':selection.status==='unavailable'?'Sélection indisponible pour cette source.':'Non finalisé : aucune décision publiée pour ces paragraphes. Un cache partiel peut exister.','selection-note');
  else if(selection.counts){const c=selection.counts;add(panel,'p',doc.is_reference?`${c.reference||0} paragraphes dans la référence choisie.`:`${c.add||0} utiles · ${c.add_uncertain||0} incertains · ${c.skip_redundant||0} redondants${c.pending?' · '+c.pending+' en attente':''}`,'selection-note')}
  const decision=doc.documentary_decision;
  if(decision)add(panel,'p','Tri documentaire précédent : '+documentDecision(doc)+'.','previous-decision');
  const actions=add(panel,'div',undefined,'reader-actions');
  const paragraphButton=add(actions,'button','Paragraphes');const textButton=add(actions,'button','Texte source');
  paragraphButton.onclick=()=>{if(state.selectedDoc!==doc.id)return;state.mode='paragraphs';paragraphButton.classList.add('active');textButton.classList.remove('active');renderParagraphControls(state,doc)};
  textButton.onclick=()=>{if(state.selectedDoc!==doc.id)return;state.mode='text';textButton.classList.add('active');paragraphButton.classList.remove('active');loadSourceText(state,doc,true)};
  paragraphButton.classList.add('active');
  safeExternal(actions,'Ouvrir la page d’origine',doc.source_url);
  const meta=add(panel,'details',undefined,'reader-meta');add(meta,'summary','Métadonnées et identifiants');
  dl(meta,[['Document',doc.id],['Source',doc.dataset],['Groupe source',doc.source_group],['ID du groupe',doc.source_group_id],['Version',doc.current_version],['Type',doc.kind],['Origine',doc.origin],['Longueur',doc.body_characters],['Ancienne décision documentaire',decision]]);
  if(doc.type_annotation)box(meta,'Classement par text mining · preuves',doc.type_annotation);
  if(doc.provenance)box(meta,'Provenance · citations et preuves',doc.provenance);
  const content=add(panel,'div',undefined,'reader-content');state.readerContent=content;
  renderParagraphControls(state,doc);
}
function renderParagraphControls(state,doc){
  const area=state.readerContent;clear(area);
  const controls=add(area,'div',undefined,'paragraph-controls');
  const label=add(controls,'label','Afficher');label.htmlFor='paragraph-filter';
  const select=add(controls,'select');select.id='paragraph-filter';
  for(const [value,label] of [['all','Tous les paragraphes'],['retained','Retenus'],['reference','Référence'],['add','Utiles'],['add_uncertain','Incertains'],['skip_redundant','Redondants'],['pending','En attente']]){const option=add(select,'option',label);option.value=value}
  select.value=state.filter;
  select.onchange=()=>{state.filter=select.value;loadParagraphs(state,doc,true)};
  const content=add(area,'div');state.paragraphList=content;
  loadParagraphs(state,doc,true);
}
function paragraphBadge(status){return {reference:'Référence',add:'Utile',add_uncertain:'Incertain',skip_redundant:'Redondant',pending:'En attente'}[status]||status||'En attente'}
function scoreLine(scores){if(!scores||typeof scores!=='object')return '';
  const labels={yes:'Apport',no:'Redondance',unknown:'Incertitude'};
  return Object.entries(scores).filter(([,v])=>v!=null).map(([k,v])=>`${labels[k]||k} : ${Number(v).toLocaleString('fr-FR',{maximumFractionDigits:1})} %`).join(' · ')}
function renderParagraph(parent,state,doc,paragraph){
  const card=add(parent,'article',undefined,'paragraph-card paragraph-'+paragraph.status);
  card.classList.toggle('paragraph-short',(paragraph.body||'').length<190&&!String(paragraph.body||'').includes('\n'));
  const heading=add(card,'div',undefined,'paragraph-head');
  add(heading,'strong',paragraph.label||`Paragraphe ${paragraph.number??''}`);add(heading,'span',paragraphBadge(paragraph.status),'badge status-'+paragraph.status);
  if(paragraph.grouping_policy?.startsWith('document-sections/2')&&paragraph.parser_span_count>1)add(heading,'span',`${paragraph.parser_span_count} unités source regroupées`,'muted');
  const scores=scoreLine(paragraph.scores_pct);if(scores)add(card,'small',scores,'muted');
  add(card,'div',paragraph.body||'Texte vide.','paragraph-body');
  if(paragraph.body_truncated)add(card,'p',`Aperçu de ${paragraph.body_characters??'?'} caractères ; ouvrez « Texte source » pour lire la fiche entière.`,'muted');
  if(paragraph.decision?.reason)add(card,'p',str(paragraph.decision.reason),'decision-reason');
  if(paragraph.exact_proof)add(card,'p','Copie exacte d’un passage déjà couvert : preuve textuelle, indépendamment du score du modèle.','muted');
  const meta=add(card,'details',undefined,'paragraph-details');add(meta,'summary',paragraph.request_ids?.length?'Détails · contexte évalué':'Détails du passage');
  if(paragraph.request_ids?.length){for(const [i,id] of paragraph.request_ids.entries()){const button=add(meta,'button',`Voir le contexte évalué${paragraph.request_ids.length>1?' · lot '+(i+1):''}`);button.onclick=()=>loadRequestPreview(meta,doc.id,id,button,state)}}
  dl(meta,[['ID',paragraph.id],['Numéro',paragraph.number],['Position début',paragraph.offset_start],['Position fin',paragraph.offset_end],['Format',paragraph.format],['Chemin',paragraph.path],['Décision',paragraph.decision]]);
}
async function loadRequestPreview(parent,rid,requestId,button,state){
  button.disabled=true;
  try{const data=await api('/review/api/workspace/documents/'+encodeURIComponent(rid)+'/requests/'+encodeURIComponent(requestId));
    if(workspaceState!==state||state.selectedDoc!==rid)return;
    const detail=add(parent,'details');detail.open=true;add(detail,'summary','Demande et réponse · '+requestId);
    box(detail,'Contexte réellement évalué',data.request);box(detail,'Réponse publiée',data.response);
    if(data.display_truncated)add(detail,'p','Aperçu borné pour l’affichage.','muted');
  }catch(e){if(workspaceState===state)add(parent,'p',e.message,'error')}
}
async function loadParagraphs(state,doc,reset){
  if(workspaceState!==state||state.mode!=='paragraphs'||state.selectedDoc!==doc.id)return;
  const sequence=++readerSequence,content=state.paragraphList;
  if(reset){state.paragraphOffset=0;clear(content)}
  const wait=add(content,'p','Chargement des paragraphes…','muted');
  try{const query=new URLSearchParams({offset:state.paragraphOffset,limit:12,status:state.filter});
    const data=await api('/review/api/workspace/documents/'+encodeURIComponent(doc.id)+'/paragraphs?'+query);
    if(workspaceState!==state||state.selectedDoc!==doc.id||state.mode!=='paragraphs'||sequence!==readerSequence)return;
    wait.remove();
    if(reset&&!data.items.length)add(content,'p','Aucun paragraphe pour ce filtre.','empty');
    for(const paragraph of data.items)renderParagraph(content,state,doc,paragraph);
    state.paragraphOffset+=(data.items?.length||0);
    if(data.has_more){const more=add(content,'button','Charger les paragraphes suivants','load-more');more.onclick=()=>{more.remove();loadParagraphs(state,doc,false)}}
  }catch(e){if(sequence===readerSequence){wait.remove();add(content,'p',e.message,'error')}}
}
async function loadSourceText(state,doc,reset){
  if(workspaceState!==state||state.mode!=='text'||state.selectedDoc!==doc.id)return;
  const sequence=++readerSequence,content=state.readerContent;
  if(reset){state.passageOffset=0;clear(content);add(content,'p','Texte intégral de la fiche source.','muted');state.textArea=add(content,'div',undefined,'source-text')}
  const wait=add(content,'p','Chargement du texte…','muted');
  try{const query=new URLSearchParams({record_id:doc.id,offset:state.passageOffset,limit:5});const data=await api('/passages?'+query);
    if(workspaceState!==state||state.selectedDoc!==doc.id||state.mode!=='text'||sequence!==readerSequence)return;
    wait.remove();if(reset&&!data.passages.length)add(content,'p','Aucun texte source.','empty');
    for(const passage of data.passages)state.textArea.append(document.createTextNode(passage.text));
    state.passageOffset+=data.passages.length;
    if(data.pagination?.next_url){const more=add(content,'button','Charger la suite du texte','load-more');more.onclick=()=>{more.remove();loadSourceText(state,doc,false)}}
  }catch(e){if(sequence===readerSequence){wait.remove();add(content,'p',e.message,'error')}}
}
async function loadLegacyDetails(parent,id,state){
  const wait=add(parent,'p','Chargement du contexte technique…','muted');
  try{const data=await api('/review/api/browse/aggregates/'+encodeURIComponent(id));
    if(workspaceState!==state)return;wait.remove();
    dl(parent,[['Agrégat',data.id],['Clé commune',data.key],['Type',data.kind],['Univers',data.universe],['Fiche d’ancrage',data.anchor_record_id],['Exécution',data.run_id]]);
    if(data.aliases?.length)box(parent,'Alias techniques',data.aliases);
    const links=add(parent,'details');add(links,'summary','Liens entrants et sortants');
    links.addEventListener('toggle',()=>{if(links.open&&!links.dataset.loaded){links.dataset.loaded='1';aggregateSection(links,data,'outgoing','Sortants');aggregateSection(links,data,'incoming','Entrants')}});
    const curation=add(parent,'details');add(curation,'summary','Anciennes décisions documentaires');
    curation.addEventListener('toggle',()=>{if(curation.open&&!curation.dataset.loaded){curation.dataset.loaded='1';for(const doc of state.docs.values())if(doc.documentary_decision){const row=add(curation,'div',undefined,'card');add(row,'strong',sourceName(doc));dl(row,[['Document',doc.id],['Décision antérieure',doc.documentary_decision]])}}});
  }catch(e){if(workspaceState===state){wait.remove();add(parent,'p',e.message,'error')}}
}
async function nativeCuration(parent,a){
  const panel=add(parent,'section');add(panel,'h2','Tri Jev · nouveau contrat');
  add(panel,'p','Trois contrôles : même entité, apport utile à la couverture retenue, lien ambigu valide. Les anciens verdicts ne sont pas repris.','muted');
  try{const data=await api('/review/api/curation/jobs?'+new URLSearchParams({aggregate_id:a.id,limit:10}));
    if(data.paused)add(panel,'p','Curation en pause.','muted');
    if(!data.items.length){add(panel,'p','Aucun traitement du nouveau contrat pour cette entrée.','muted');return}
    for(const job of data.items){const card=add(panel,'div',undefined,'card');dl(card,[['Politique',job.policy],['Seuil de redondance',`${job.redundancy_comparison==='strictly_greater'?'>':'≥'} ${job.redundancy_threshold_pct} %`],['Traitement',job.kind],['Statut',job.status],['Appels du modèle',job.model_calls],['Cache natif',job.cache_hits],['Même entité (%)',job.entity_match_score_pct],['Lien valide (%)',job.link_score_pct],['Proposition de rattachement',job.correction_proposal]]);
      if(job.preview_only)add(card,'p','Préparation algorithmique uniquement : les comparaisons sémantiques sont en attente de Jev.','muted');
      if(job.preview_only){const b=add(card,'button','Voir les questions prévues et leurs passages');b.onclick=()=>{b.disabled=true;nativePreviews(card,job.id)}}
      if(job.has_error)add(card,'p','Traitement interrompu ; aucune sélection publiée.','warning');
      for(const source of job.selection||[]){const row=add(card,'div',undefined,'card');const b=add(row,'button',source.source_id);b.onclick=()=>{tab='documents';setTab();choose(source.source_id)};dl(row,[['Sélection pour le merge',source.merge_selection],['Motif',source.reason],['Même entité (%)',source.entity_score_pct],['Apport utile (%)',source.adds_information_score_pct],['Scores des choix',source.scores_pct],['Sources couvrantes',source.covered_by]]);if(source.unit_proofs?.length)box(row,'Décisions et preuves par passage (aperçu)',source.unit_proofs)}
      if(job.selection_total>(job.selection||[]).length)add(card,'p',`${job.selection_total} sélections conservées ; aperçu des 20 premières.`,'muted');
    }
  }catch(e){add(panel,'p',e.message,'error')}
}
function nativePreviews(parent,jobId){
  const panel=add(parent,'div');let position=0;const more=add(panel,'button','Charger les questions prévues');
  more.onclick=async()=>{more.disabled=true;try{const data=await api('/review/api/curation/previews?'+new URLSearchParams({job_id:jobId,offset:position,limit:5}));
    if(!data.items.length&&position===0)add(panel,'p','Aucune question sémantique prévue : traitement algorithmique ou protection des variantes.','muted');
    for(const q of data.items){const card=add(panel,'div',undefined,'card');dl(card,[['Questions',q.tasks],['Tokens complets par question',q.question_tokens],['État',q.reason]]);box(card,'Demande prévue : questions et preuves (aperçu borné)',q.request)}
    position+=data.items.length;more.hidden=!data.has_more;more.disabled=false;
  }catch(e){add(panel,'p',e.message,'error')}};more.click();
}
function mergedText(parent,a){
  const panel=add(parent,'section',undefined,'merged-text');add(panel,'h2','Texte agrégé');panel.scrollIntoView?.({block:'start'});
  add(panel,'p','Passages identiques stockés une seule fois. Les variantes et contradictions sont conservées pour le futur merge par LLM.','muted');
  const content=add(panel,'div');const more=add(panel,'button','Charger les passages agrégés');let position=0;
  more.onclick=async()=>{more.disabled=true;try{
    const data=await api('/aggregates/'+encodeURIComponent(a.id)+'/text?'+new URLSearchParams({offset:position,limit:10}));
    for(const block of data.items){const card=add(content,'div',undefined,'card');add(card,'pre',block.text,'evidence');
      for(const source of block.sources){const button=add(card,'button',`${source.dataset} · ${source.name}`);button.onclick=()=>{tab='documents';setTab();choose(source.record_id)}}
      if(block.more_sources)add(card,'p','D’autres occurrences de ce passage sont conservées dans la base.','muted');
    }position+=data.items.length;more.hidden=!data.has_more;more.disabled=false;
  }catch(e){add(panel,'p',e.message,'error')}};more.click();
}
let coverageLoaded=false;
async function loadCoverage(){
  if(coverageLoaded)return;coverageLoaded=true;const panel=$('coverage-content');
  try{const data=await api('/review/api/coverage');
    add(panel,'p',`${data.documents} documents · ${data.aggregates} clés · ${data.multiple_document_aggregates} regroupements de plusieurs documents`);
    for(const family of data.families)add(panel,'p',`${family.family} : ${family.documents} documents`);
    const statuses={complete:'complet',complete_scope:'périmètre ciblé complet',index_only:'index uniquement',partial:'partiel',blocked_local_network:'bloqué par le filtre réseau',tls_unavailable:'connexion TLS indisponible'};
    if(data.acquisition.sources?.length){add(panel,'h3','Acquisitions et manques');for(const s of data.acquisition.sources)add(panel,'p',`${s.source} : ${statuses[s.status]||s.status}${s.records!=null?' · '+s.records+' documents':''}`)}
  }catch(e){coverageLoaded=false;add(panel,'p',e.message,'error')}
}
function aggregateSection(d,a,key,title){
  add(d,'h2',title);const wrap=add(d,'div');const state=a[key]||{items:[],offset:0,limit:30,has_more:false};
  function render(items){for(const item of items){if(key==='members'){
    const c=add(wrap,'div',undefined,'card');dl(c,[['Règle',item.rule],['Clé de rapprochement',item.match_key],['Version retenue',item.version_id]]);
    if(item.document)docCard(c,item.document);else add(c,'p','Document absent de la base courante.','muted');
  }else{
    const row=add(wrap,'div',undefined,'linkrow');const neighbor=add(row,'button',item.other_name||item.other_id);neighbor.onclick=()=>openAggregate(item.other_id);
    add(row,'span',`${item.kind} · ${item.document_count} document(s), ${item.occurrences} occurrence(s)`);
    if(item.flags?.length)box(row,'Avertissements',item.flags);
    const evidence=add(row,'button','Voir les passages');evidence.onclick=()=>{evidence.disabled=true;showEvidence(row,item)};
  }}}
  render(state.items);
  if(state.has_more){const more=add(d,'button','Charger la suite');more.onclick=async()=>{more.disabled=true;try{const next=await api('/review/api/browse/aggregates/'+encodeURIComponent(a.id)+'?'+new URLSearchParams({section:key,offset:state.offset+state.limit,limit:state.limit}));render(next[key].items);state.offset=next[key].offset;state.has_more=next[key].has_more;more.hidden=!state.has_more;more.disabled=false}catch(e){add(d,'p',e.message,'error')}}}
}
async function showEvidence(row,item){
  const pane=add(row,'div',undefined,'card');add(pane,'h3','Passages justificatifs');let offset=0;
  const more=add(pane,'button','Charger les passages');
  more.onclick=async()=>{more.disabled=true;try{const query=new URLSearchParams({subject_id:item.subject_id,object_id:item.object_id,kind:item.kind,offset,limit:10});const data=await api('/review/api/browse/evidence?'+query);if(!data.items.length&&offset===0)add(pane,'p','Aucun passage enregistré.','muted');for(const ev of data.items){const c=add(pane,'div',undefined,'card');add(c,'div',ev.excerpt||'Passage absent.','evidence');dl(c,[['Document',ev.record_id],['Version',ev.version_id],['Position',ev.offset_start],['Règle',ev.rule]]);const b=add(c,'button','Lire le document');b.onclick=()=>{tab='documents';setTab();choose(ev.record_id)};if(ev.context&&Object.keys(ev.context).length)box(c,'Contexte',ev.context)}offset+=data.items.length;more.hidden=!data.has_more;more.disabled=false}catch(e){add(pane,'p',e.message,'error')}};
  more.click();
}
function setTab(){document.body.classList.toggle('browse-mode',tab==='browse');readerSequence++;detailSequence++;workspaceState=null;for(const name of ['browse','questions','entities','documents'])$('tab-'+name).classList.toggle('active',tab===name);$('task-label').hidden=tab!=='questions';$('status-label').hidden=tab!=='questions';$('query-label').hidden=tab==='questions';$('source-label').hidden=false;$('section-label').hidden=tab!=='browse';$('duplicates-label').hidden=tab!=='browse';$('scope-label').hidden=tab==='browse';$('browse-types').hidden=tab!=='browse';$('coverage').hidden=tab!=='browse';$('q').placeholder=tab==='browse'?'Début du nom ou alias':tab==='entities'?'Nom de la fiche d’ancrage':'Nom ou texte du document';$('list-title').textContent=tab==='questions'?(recordFilter?'Questions · document '+recordFilter:'Questions'):tab==='entities'?'Identités historiques':tab==='browse'?'Entrées':'Documents';$('clear-record').hidden=!recordFilter||tab!=='questions';offset=0;selected='';clear($('detail'));add($('detail'),'p','Choisissez un élément dans la liste.','empty');if(tab==='browse')browseTypes();list()}
async function init(){try{const f=await api('/review/api/filters');for(const s of f.sources){const o=add($('source'),'option',`${s.dataset} · ${s.title}`);o.value=s.id}for(const [id,values] of [['task',f.tasks],['status',f.statuses]])for(const value of values){const o=add($(id),'option',value);o.value=value}if(f.statuses.includes('uncertain'))$('status').value='uncertain'}catch(e){add($('list'),'p',e.message,'error')}if(!openHash())setTab()}
function openHash(){if(location.hash==='#browse'){tab='browse';setTab()}else if(location.hash.startsWith('#question=')){tab='questions';setTab();choose(decodeURIComponent(location.hash.slice(10)))}else if(location.hash.startsWith('#aggregate=')){tab='browse';setTab();choose(decodeURIComponent(location.hash.slice(11)))}else return false;return true}
window.addEventListener('hashchange',openHash);
$('coverage').addEventListener('toggle',()=>{if($('coverage').open)loadCoverage()});
$('section').onchange=()=>{browseKind='';offset=0;browseTypes();list()};$('duplicates').onchange=()=>{offset=0;list()};$('filters').onsubmit=e=>{e.preventDefault();offset=0;list()};$('clear-record').onclick=()=>{recordFilter='';setTab()};for(const name of ['browse','questions','entities','documents'])$('tab-'+name).onclick=()=>{tab=name;recordFilter='';setTab()};$('prev').onclick=()=>{offset=Math.max(0,offset-limit);list()};$('next').onclick=()=>{offset+=limit;list()};init();
