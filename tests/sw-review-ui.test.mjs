import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM} from 'jsdom';

const root = new URL('../packages/sw_kbase/', import.meta.url);
const [html, script] = await Promise.all([
  readFile(new URL('review_ui.html', root), 'utf8'),
  readFile(new URL('review_ui.js', root), 'utf8'),
]);

const doc = (id, label, group, type = 'extracted') => ({
  id, name: '<img src=x onerror=alert(1)>', dataset: 'phase3/book', kind: 'person',
  origin: 'book', current_version: 'ver-' + id, body_characters: 100,
  source_label: label, source_group_id: group, source_group: label,
  source_url: type === 'web' ? 'https://example.org/entry' : null,
  page_label: 'p. 12', pages: [12], source_type: type,
  is_reference: id === 'rec-ref', selection: {status: 'pending', counts: {pending: 1}, total_paragraphs: 1},
  documentary_decision: null,
  provenance: {status:'identified',acquisition:{label:type==='web'?'Wookieepedia':'Transcription locale · extraction'},works:[{title:'DarkStryder Campaign',code:'WEG40209',kind:'book',pages:'12',page_basis:'cited_page'}]},
});

const tick = () => new Promise(resolve => setTimeout(resolve, 0));
async function settle() { for (let i = 0; i < 7; i++) await tick(); }

test('workspace keeps source hierarchy, escapes text, and ignores stale paragraphs', async () => {
  const dom = new JSDOM(html, {url: 'http://127.0.0.1:8765/review', runScripts: 'outside-only'});
  const {window} = dom;
  const calls = [];
  let releaseOld;
  let delayOld = false;
  const responses = {
    '/review/api/filters': {sources: [], tasks: [], statuses: []},
    '/review/api/coverage': {documents: 3, aggregates: 1, multiple_document_aggregates: 1, families: [], acquisition: {sources: []}},
    '/review/api/browse/types': {available: true, types: [{kind: 'person', count: 1}]},
    '/review/api/browse/aggregates': {available: true, items: [{id: 'agg-1', name: 'Kl’aal', kind: 'person', member_count: 3, source_count: 2, warnings: []}], has_more: false},
    '/review/api/workspace/agg-1': {id: 'agg-1', name: 'Kl’aal', kind: 'person', counts: {records: 3, source_collections: 2, web_records: 1, legacy_blocks: 731},
      selection: {available: true, status: 'pending', paused: true, reference_id: 'rec-ref'},
      reference: doc('rec-ref', 'WEG40209', 'book-1'),
      documents: {items: [doc('rec-ref', 'WEG40209', 'book-1'), doc('rec-other', 'WEG40209', 'book-1'), doc('rec-web', 'Wookieepedia · Kl’aal', 'web-1', 'web')], offset: 0, limit: 100, has_more: false}},
  };
  window.fetch = async url => {
    calls.push(url);
    const parsed = new URL(url, window.location.href);
    let data = responses[parsed.pathname];
    if (parsed.pathname.endsWith('/paragraphs')) {
      if (parsed.pathname === '/review/api/workspace/agg-1/paragraphs') {
        return {ok: true, json: async () => ({items: [], total: 0, selection_available: true, pending_documents: 2})};
      }
      const rid = parsed.pathname.split('/')[5];
      if (rid === 'rec-ref' && delayOld) await new Promise(resolve => { releaseOld = resolve; });
      const page = Number(parsed.searchParams.get('offset') || 0);
      const status = parsed.searchParams.get('status');
      data = {document: doc(rid, rid, rid), selection_available: true, offset: page, limit: 12,
        has_more: rid === 'rec-web' && status === 'all' && page === 0,
        items: [{id: 'p-' + rid + '-' + page, label: 'Paragraphe ' + (page + 1), number: page + 1, body: rid === 'rec-web' ? (page ? 'Suite du document' : '<script>danger</script>') : 'Ancienne réponse',
          body_truncated: false, body_characters: 20, status: 'pending', scores_pct: null, decision: null,
          exact_proof: false, request_ids: [], format: 'text', path: ''}]};
    }
    if (parsed.pathname === '/passages') data = {passages: [{text: 'Texte brut web'}], pagination: {next_url: null}};
    if (parsed.pathname === '/review/api/browse/aggregates/agg-1') data = {id:'agg-1', outgoing:{items:[]}, incoming:{items:[]}};
    if (!data) throw new Error('Unexpected fetch ' + url);
    return {ok: true, json: async () => data};
  };
  window.eval(script);
  await settle();
  assert.equal(window.document.querySelector('#tab-browse').classList.contains('active'), true);
  window.document.querySelector('.item').click();
  await settle();
  assert.ok(calls.some(x => x.includes('/review/api/workspace/agg-1?limit=100')));
  assert.match(window.document.querySelector('.workspace-sources').textContent, /Autres fiches du même livre/);
  assert.match(window.document.querySelector('.workspace-sources').textContent, /Pages web récupérées/);
  assert.match(window.document.querySelector('.workspace-head').textContent, /décisions non publiées/);
  assert.match(window.document.querySelector('.article-reference-text').textContent, /Texte brut web/);
  assert.equal(calls.some(x => x.includes('/documents/rec-ref/paragraphs')), false);
  assert.equal(window.document.querySelector('.article-originals').open, false);
  assert.match(window.document.querySelector('.document-provenance').textContent, /Origine : Transcription locale/);
  assert.match(window.document.querySelector('.document-provenance').textContent, /Source documentaire :DarkStryder Campaign/);
  assert.equal(window.document.querySelectorAll('.workspace-sources .source-item').length, 3);
  assert.equal(window.document.querySelector('.workspace-advanced').open, false);
  window.document.querySelector('.article-reference .article-source button').click();
  await settle();
  assert.equal(window.document.querySelector('.article-originals').open, true);
  assert.match(window.document.querySelector('.workspace-reader').textContent, /Non finalisé/);
  delayOld = true;
  window.document.querySelector('[data-record-id="rec-ref"]').click();
  await tick();
  window.document.querySelector('[data-record-id="rec-web"]').click();
  await settle();
  releaseOld();
  await settle();
  assert.match(window.document.querySelector('.workspace-reader').textContent, /Wookieepedia/);
  assert.match(window.document.querySelector('.workspace-reader .document-provenance').textContent, /Origine : Wookieepedia/);
  assert.match(window.document.querySelector('.workspace-reader .document-provenance').textContent, /WEG40209/);
  assert.match(window.document.querySelector('.paragraph-body').textContent, /<script>danger<\/script>/);
  assert.equal(window.document.querySelector('.paragraph-body script'), null);
  assert.equal(window.document.querySelector('.workspace-reader img'), null);
  assert.equal(window.document.querySelector('.paragraph-card small'), null);
  window.document.querySelector('.reader-content .load-more').click();
  await settle();
  assert.match(window.document.querySelector('.reader-content').textContent, /Suite du document/);
  const statusSelect = window.document.querySelector('.paragraph-controls select');
  statusSelect.value = 'pending';
  statusSelect.dispatchEvent(new window.Event('change'));
  await settle();
  assert.equal(window.document.querySelectorAll('.paragraph-card').length, 1);
  assert.ok(calls.some(x => x.includes('status=pending')));
  window.document.querySelector('.reader-actions button:nth-child(2)').click();
  await settle();
  assert.match(window.document.querySelector('.source-text').textContent, /Texte brut web/);
  assert.equal(window.document.querySelector('#tab-browse').classList.contains('active'), true);
  dom.window.close();
});

test('article opens the complete reference, selected extracts, discarded extracts and navigable links in order', async () => {
  const dom = new JSDOM(html, {url:'http://127.0.0.1:8765/review', runScripts:'outside-only'});
  const {window} = dom;
  let releaseLate;
  let delay = false;
  const source = doc('rec-outside-page', 'Seconde source', 'book-2');
  source.selection = {status:'scored', counts:{add:1, skip_redundant:1}, total_paragraphs:2};
  window.fetch = async url => {
    const p = new URL(url, window.location.href);
    let data;
    if(p.pathname.endsWith('/filters')) data={sources:[],tasks:[],statuses:[]};
    else if(p.pathname.endsWith('/types')) data={available:true,types:[]};
    else if(p.pathname==='/review/api/browse/aggregates') data={items:[{id:'agg-1',name:'Pilot',kind:'person',member_count:150,source_count:10}],has_more:false};
    else if(p.pathname==='/review/api/workspace/agg-1'||p.pathname==='/review/api/workspace/agg-2') {
      const id=p.pathname.split('/').at(-1);
      data={id,name:id,kind:'person',counts:{records:150,source_collections:10},selection:{available:true,status:'scored',paused:true},
        reference:doc('rec-ref','Référence livre','book-1'),documents:{items:[doc('rec-ref','Référence livre','book-1')],has_more:true}};
    }else if(p.pathname==='/passages') {
      const offset=Number(p.searchParams.get('offset'));
      data={version_id:'ver-rec-ref',passages:[{text:offset?'\nSuite exacte.':'Texte de référence <img src=x>'}],pagination:{next_url:offset?null:'/passages?offset=1'}};
    }else if(/\/workspace\/agg-[12]\/paragraphs$/.test(p.pathname)){
      const status=p.searchParams.get('status'),offset=Number(p.searchParams.get('offset'));
      if(delay&&p.pathname.includes('agg-1')&&offset)await new Promise(resolve=>{releaseLate=resolve});
      data={total:status==='retained'?2:1,selection_available:true,has_more:status==='retained'&&!offset,
        items:[{id:'p-'+status+offset,number:1,label:'Bloc 1',status:status==='retained'?'add_uncertain':'skip_redundant',
          document:source,body:'Le bloc entier contient aussi du texte à exclure.',partial:true,request_ids:[],
          parts:[{body:offset?'Réponse tardive ancienne entrée':status==='retained'?'Extrait conservé <script>attaque</script>':'Extrait redondant',status:status==='retained'?'add_uncertain':'skip_redundant',scores_pct:{yes:55,no:30,unknown:15}}]}]};
    }else if(p.pathname.startsWith('/review/api/browse/aggregates/')){
      const key=p.searchParams.get('section');data={id:'agg-1',[key]:{items:key==='outgoing'?[{other_id:'agg-2',other_name:'Autre article',kind:'mentions',document_count:1,occurrences:2}]:[],has_more:false}};
    }else throw Error('Unexpected fetch '+url);
    return {ok:true,json:async()=>data};
  };
  try{
    window.eval(script);await settle();window.document.querySelector('.item').click();await settle();
    assert.deepEqual([...window.document.querySelectorAll('.article-section>h2')].map(x=>x.textContent),['Référence','Paragraphes retenus','Passages écartés','Liens']);
    assert.equal(window.document.querySelector('.article-reference-text').textContent,'Texte de référence <img src=x>\nSuite exacte.');
    assert.match(window.document.querySelector('.article-retained').textContent,/Extrait conservé/);
    assert.match(window.document.querySelector('.article-retained').textContent,/Incertain/);
    assert.doesNotMatch(window.document.querySelector('.article-retained').textContent,/Le bloc entier|Extrait redondant/);
    assert.match(window.document.querySelector('.article-discarded').textContent,/Extrait redondant/);
    assert.match(window.document.querySelector('.article-retained .article-source').textContent,/Seconde source/);
    assert.equal(window.document.querySelectorAll('.article-view script,.article-view img').length,0);
    assert.doesNotMatch(window.document.querySelector('.workspace-head').textContent,/pause/);
    assert.equal(window.document.querySelector('.article-originals').open,false);
    delay=true;window.document.querySelector('.article-retained>.load-more').click();await tick();
    window.document.querySelector('.article-links .linkrow button').click();await settle();
    releaseLate();await settle();
    assert.match(window.document.querySelector('.workspace-head h1').textContent,/agg-2/);
    assert.doesNotMatch(window.document.querySelector('.article-retained').textContent,/Réponse tardive/);
  }finally{dom.window.close()}
});
