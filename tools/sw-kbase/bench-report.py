"""Render local benchmark evidence; missing results remain explicitly unmeasured."""
import json,sys
from pathlib import Path

out=Path(sys.argv[1]);fixture=json.loads((out/'cases.json').read_text(encoding='utf-8'))
models=[('perplexity','Perplexity Decider Flash 1.2','BF16 · moteur officiel optimisé'),
 ('finecat','FineCat NLI Ultra L','BF16 · lot de 4 paires référence/bloc'),
 ('winnow-e4b','Winnow E4B','Q8_0 · Ollaya / llama.cpp'),
 ('clef-flash','Clef Flash','poids BF16, calcul FP32 · Ollaya / ONNX')]
data={'fixture':fixture,'models':[]}
for key,name,note in models:
    path=out/(key+'.json')
    result=json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
    data['models'].append({'key':key,'name':name,'note':note,'result':result})
(out/'report-data.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
serialized=json.dumps(data,ensure_ascii=False).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
html='''<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SW D6 · 4 modèles, 3 documents</title><style>
*{box-sizing:border-box}body{font:15px system-ui;color:#d9e5f1;background:#10171f;margin:0;padding:24px;max-width:1550px;margin:auto}h1{font-size:25px}h2{font-size:21px}p{line-height:1.5}.muted,small{color:#9eb1c4}table{border-collapse:collapse;width:100%;margin:15px 0}th,td{padding:10px;border:1px solid #34465a;text-align:left}th{background:#1c2a39}td{vertical-align:top}.case{border:1px solid #34465a;border-radius:8px;margin:25px 0;padding:18px}.texts{display:grid;grid-template-columns:1fr 1fr;gap:18px}.body{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.5 system-ui;background:#15212c;padding:14px;max-height:650px;overflow:auto}.block{border-top:1px solid #34465a;margin-top:18px;padding-top:8px}.score{font-variant-numeric:tabular-nums;font-weight:600}.keep{color:#8bdfb0}.drop{color:#f1b276}a{color:#88c6ff}summary{cursor:pointer;color:#88c6ff}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:400px;overflow:auto}code{font-size:12px}nav{position:sticky;top:0;background:#10171ff0;padding:10px;display:flex;gap:25px;z-index:1}@media(max-width:850px){.texts{grid-template-columns:1fr}body{padding:12px}table{font-size:12px}th,td{padding:5px}}
</style><h1>SW D6 — comparaison de quatre modèles</h1>
<p>Trois comparaisons réelles de taille intermédiaire. Même référence complète et mêmes quatre blocs candidats par cas. Aucun résultat écrit dans la base de curation.</p>
<p class="muted">Temps par document complet : médiane de trois mesures après un échauffement par cas. Chargement et compilation séparés. Les appels HTTP incluent transport local et tokenisation ; FineCat inclut tokenisation, transfert et calcul d’un lot de quatre paires. Aucun texte tronqué.</p>
<p><b>JEV : U = apport utile, R = redondance, ? = incertitude.</b> FineCat : E = entailment (couverture évaluée), N = neutral (non établi), C = contradiction. Ces sorties NLI ne sont pas des scores d’apport utile. La suggestion « écarter » applique seulement le seuil strict R &gt; 80 %, ou E &gt; 80 % pour tester le prétri FineCat. Aucune décision n’est appliquée.</p>
<nav id="nav"></nav><div id="timing"></div><main id="cases"></main>
<p class="muted">Les scores affichés viennent de la première répétition mesurée ; les trois réponses brutes sont accessibles. Échantillon de 12 blocs destiné à ta revue qualitative, pas à établir une précision statistique. Perplexity Flash peut être sensible au regroupement des questions ; ce test utilise quatre questions liées au même document.</p>
<script id="data" type="application/json">DATA</script><script>
const d=JSON.parse(document.querySelector('#data').textContent), $=s=>document.querySelector(s);
function el(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e}
function modelCase(m,c){return m.result?.cases.find(x=>x.id===c.id)}
function probs(m,c,p){const r=modelCase(m,c)?.runs?.[0]?.raw;if(!r)return null;return m.key==='finecat'?r.probabilities?.[p.vote_id]:r.answers?.[p.vote_id]?.probabilities}
function table(headers){const t=el('table'),tr=el('tr');headers.forEach(x=>tr.append(el('th',x)));t.append(tr);return t}
const tt=table(['Modèle','Hux','Crossfire','Trask','Chargement / compilation']);
for(const m of d.models){const tr=el('tr');let td=el('td');td.append(el('b',m.name),el('br'),el('small',m.result?.backend||m.note));tr.append(td);for(const c of d.fixture.cases){const r=modelCase(m,c);tr.append(el('td',r?r.median_ms.toFixed(1)+' ms':'Pas mesuré'))}tr.append(el('td',m.result?`${m.result.load_seconds.toFixed(1)} s / ${(m.result.compile_warmup_seconds||0).toFixed(1)} s`:'En attente du signal'));tt.append(tr)}$('#timing').append(tt);
for(const c of d.fixture.cases){const a=el('a',c.name);a.href='#case'+c.id;$('#nav').append(a);const box=el('section',undefined,'case');box.id='case'+c.id;box.append(el('h2',c.name),el('p',`${c.reference.body.length} caractères de référence + ${c.candidate.body.length} de candidat · 4 blocs · Clef : ${c.preflight_clef.total_tokens} tokens complets`,'muted'));
 const texts=el('div',undefined,'texts');for(const [label,doc] of [['Référence choisie',c.reference],['Document candidat',c.candidate]]){const col=el('div');col.append(el('h3',label),el('p',doc.dataset+' · '+doc.name,'muted'));const detail=el('details');detail.append(el('summary','Provenance et identifiants'),el('pre',JSON.stringify({record_id:doc.id,version_id:doc.version_id,locator:doc.locator,source_id:doc.source_id,source_title:doc.source_title,sha256:doc.body_sha256},null,2)));col.append(detail,el('div',doc.body,'body'));texts.append(col)}box.append(texts);
 for(const p of c.paragraphs){const block=el('div',undefined,'block');block.append(el('h3',p.vote_id+' · '+p.label),el('div',p.body,'body'));const t=table(d.models.map(m=>m.name));const tr=el('tr');for(const m of d.models){const td=el('td'),v=probs(m,c,p);if(!v){td.textContent='Pas mesuré'}else{const keys=m.key==='finecat'?['entailment','neutral','contradiction']:['yes','no','unknown'];const names=m.key==='finecat'?['E','N','C']:['U','R','?'];td.append(el('div',keys.map((k,i)=>names[i]+' '+(100*v[k]).toFixed(1)+' %').join(' · '),'score'));const red=m.key==='finecat'?v.entailment:v.no;td.append(el('div',red>.8?'Suggestion : écarter':'Suggestion : conserver',red>.8?'drop':'keep'))}tr.append(td)}t.append(tr);block.append(t);box.append(block)}
 const raw=el('details');raw.append(el('summary','Questions envoyées, réponses brutes et trois temps par modèle'),el('pre',JSON.stringify({input_sha256:c.input_sha256,wire:c.wire,results:d.models.map(m=>({model:m.name,case:modelCase(m,c)}))},null,2)));box.append(raw);$('#cases').append(box)}
</script></html>'''.replace('DATA',serialized)
(out/'bench.html').write_text(html,encoding='utf-8')
print(out/'bench.html')
