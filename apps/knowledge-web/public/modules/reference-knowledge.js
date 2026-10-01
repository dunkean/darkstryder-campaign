import {esc} from './store.js';
import {markdown} from './markdown.js';

export function referenceKnowledgeHTML(target){
  const entries=target?.referenceKnowledge||[];
  if(!entries.length)return '';
  return '<section class="reference-knowledge"><h3>Dossier sourcé · DarkStryder original</h3><p class="muted">Référence MJ : les possibilités et évolutions du scénario ne sont pas l’état actuel de la partie.</p>'+entries.map(r=>`<details><summary>${esc(r.name)} — ${esc(r.summary)}</summary><div class="markdown">${markdown(r.content||'')}</div><div>${(r.pages||[]).map(page=>`<button onclick="openSource('${esc(r.sourceId)}',${Number(page)})">Source · page PDF ${Number(page)}</button>`).join(' ')} <button onclick="openKBaseNode('${esc(r.kbaseId)}')">Fiche KBase et extraits exacts</button></div></details>`).join('')+'</section>';
}
