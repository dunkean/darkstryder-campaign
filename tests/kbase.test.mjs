import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, symlinkSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { openKBase } from '../packages/kbase/store.mjs';

const hash = value => createHash('sha256').update(value).digest('hex');

function fixture(t) {
  const root = mkdtempSync(path.join(os.tmpdir(), 'darkstryder-kbase-'));
  const runtime = path.join(root, 'runtime');
  const transRoot = path.join(root, 'content', 'transcriptions');
  mkdirSync(transRoot, { recursive: true });
  writeFileSync(path.join(root, 'config.local.json'), JSON.stringify({ runtimeRoot:runtime }));
  mkdirSync(path.join(root, 'config'), { recursive:true });
  writeFileSync(path.join(root, 'config', 'corpus-scope.json'), JSON.stringify({
    include:[{path:'Material/DarkStryder SourceBook',collection:'darkstryder',provenance:'published-source'}],
    skipSources:[{id:'src-13b156cf43fd4f8a',reason:'excluded'}]
  }));
  const sources = [
    { id:'src-aaaaaaaaaaaaaaaa',name:'Test source',collection:'darkstryder',status:'complete',units:2,unitLabel:'page PDF',locator:{path:'Material/DarkStryder SourceBook/test.pdf'},markdown:'src-aaaaaaaaaaaaaaaa/book.md' },
    { id:'src-13b156cf43fd4f8a',name:'Skipped source',collection:'darkstryder',status:'complete',units:1,unitLabel:'page PDF',locator:{path:'Material/DarkStryder SourceBook/skip.pdf'},markdown:'src-13b156cf43fd4f8a/book.md' }
  ];
  writeFileSync(path.join(transRoot,'index.json'),JSON.stringify({version:1,sources}));
  for (const [idx,source] of sources.entries()) {
    const dir=path.join(transRoot,source.id); mkdirSync(path.join(dir,'pages'),{recursive:true});
    const text = idx === 0 ? ['Leia dirige la flotte rebelle.','Leia coordonne aussi les opérations rebelles.'] : ['Texte exclu.'];
    for (let i=0;i<text.length;i++) writeFileSync(path.join(dir,'pages',`${String(i+1).padStart(4,'0')}.md`),text[i]);
    writeFileSync(path.join(dir,'transcription.json'),JSON.stringify({sourceId:source.id,sha256:hash(`pdf-${source.id}`),unitLabel:'page PDF',locator:source.locator,evidenceProvenance:'published-source'}));
  }
  t.after(() => rmSync(root,{recursive:true,force:true}));
  return { root, runtime, transRoot };
}

function extraction(job, page, quote='Leia dirige la flotte rebelle.') {
  return { jobId:job.id, notes:[], nodes:[{key:'leia',type:'Character',family:'WORLD',usage:'darkstryder_scenario',name:'Leia',
    summary:'Leia dirige la flotte.',content:'Elle coordonne la flotte rebelle.',tags:['flotte'],scope:'source',visibility:'private',properties:{role:'dirigeante'},
    evidence:[{sourceId:job.sourceId,page,quote,fields:['name','summary','content','tag:flotte','property:role']}]}], relationships:[],
    segments:[{key:'chapter',title:'La flotte rebelle',kind:'scene',nodeKeys:['leia'],evidence:[{sourceId:job.sourceId,page,quote,fields:['segment']}]}] };
}

test('sync and planning include only physical page files and exclude configured PDFs', t => {
  const {root,runtime}=fixture(t); const kb=openKBase(root,runtime);
  try {
    const synced=kb.syncSources();
    assert.deepEqual(synced,{sources:1,pages:2,skipped:3});
    const jobs=kb.planJobs({maxChars:100,maxPages:1,sourceIds:['src-aaaaaaaaaaaaaaaa']});
    assert.equal(jobs.length,2);
    assert.ok(jobs.every(job=>job.pages.length===1 && path.isAbsolute(job.pages[0].path)));
    assert.equal(jobs[0].pages[0].sha256,hash(jobs[0].pages[0].markdown));
    assert.equal(kb.getSources().length,1);
  } finally { kb.close(); }
});

test('evidence is checked, imports are atomic and repeat mentions keep stable source identity', t => {
  const {root,runtime}=fixture(t); const kb=openKBase(root,runtime);
  try {
    const planned=kb.planJobs({maxChars:100,maxPages:1,sourceIds:['src-aaaaaaaaaaaaaaaa']});
    const first=kb.claimNextJob({sourceIds:['src-aaaaaaaaaaaaaaaa']});
    assert.ok(first);
    assert.throws(()=>kb.importExtraction(first.id,extraction(first,first.pages[0].page,'citation inventée')),/does not match/);
    assert.equal(kb.getSummary().nodes,0);
    kb.failJob(first.id,'invalid pilot payload');
    assert.equal(kb.retryFailedJobs({sourceIds:['src-aaaaaaaaaaaaaaaa']}),1);
    const retry=kb.claimNextJob({sourceIds:['src-aaaaaaaaaaaaaaaa']});
    kb.importExtraction(retry.id,extraction(retry,retry.pages[0].page,retry.pages[0].markdown));
    const second=kb.claimNextJob({sourceIds:['src-aaaaaaaaaaaaaaaa']});
    kb.importExtraction(second.id,extraction(second,second.pages[0].page,second.pages[0].markdown));
    const listed=kb.listNodes({q:'Leia'});
    assert.equal(listed.total,1);
    assert.equal(listed.nodes.length,1);
    const node=kb.getNode(listed.nodes[0].id);
    assert.equal(node.claims.length,10);
    assert.ok(node.evidence.length>=10);
    assert.equal(node.reviewStatus,'unreviewed');
    assert.equal(kb.importExtraction(second.id,extraction(second,second.pages[0].page,second.pages[0].markdown)).idempotent,true);
    const exported=kb.exportGraph();
    assert.equal(exported.nodes.length,1);
    assert.equal(exported.segments.length,2);
    assert.equal(exported.summary.stateNodes,0);
    assert.ok(exported.nodes[0].claims.every(claim=>claim.evidence.markdownPath.startsWith('content/transcriptions/')));
    assert.equal(JSON.stringify(exported).includes(runtime),false);
    assert.equal(planned.length,2);
  } finally { kb.close(); }
});

test('oversized physical pages are split into bounded, offset-pinned job segments', t => {
  const {root,runtime,transRoot}=fixture(t);
  const longText='ABCD0123EFGH4567IJKL8901MNOPQRSTUV';
  writeFileSync(path.join(transRoot,'src-aaaaaaaaaaaaaaaa','pages','0001.md'),longText);
  const kb=openKBase(root,runtime);
  try {
    const jobs=kb.planJobs({maxChars:8,maxPages:6,sourceIds:['src-aaaaaaaaaaaaaaaa']});
    const parts=jobs.flatMap(job=>job.pages).filter(part=>part.page===1);
    assert.ok(parts.length>2);
    assert.ok(parts.every(part=>part.markdown.length<=8));
    assert.deepEqual(parts.map(part=>[part.offsetStart,part.offsetEnd]),[[0,8],[8,16],[16,24],[24,32],[32,longText.length]]);
    assert.equal(parts.map(part=>part.markdown).join(''),longText);
    assert.ok(parts.every(part=>part.sha256===hash(longText)));
  } finally { kb.close(); }
});

test('editorial updates use optimistic revisions and never alter raw claims', t => {
  const {root,runtime}=fixture(t); const kb=openKBase(root,runtime);
  try {
    kb.planJobs({sourceIds:['src-aaaaaaaaaaaaaaaa']});
    const job=kb.claimNextJob({sourceIds:['src-aaaaaaaaaaaaaaaa']});
    kb.importExtraction(job.id,extraction(job,job.pages[0].page,job.pages[0].markdown));
    const node=kb.listNodes().nodes[0];
    const edited=kb.updateNode(node.id,{summary:'Résumé éditorial vérifié.',reviewStatus:'reviewed'},0);
    assert.equal(edited.revision,1);
    assert.equal(edited.summary,'Résumé éditorial vérifié.');
    assert.equal(edited.claims.find(claim=>claim.field==='summary').value,'Leia dirige la flotte.');
    assert.throws(()=>kb.updateNode(node.id,{content:'écrasement'},0),/Revision conflict/);
    assert.equal(kb.getNode(node.id).claims.find(claim=>claim.field==='summary').value,'Leia dirige la flotte.');
  } finally { kb.close(); }
});

test('page hash is rechecked at import and page symlinks cannot escape transcription root', t => {
  const {root,runtime,transRoot}=fixture(t); const kb=openKBase(root,runtime);
  try {
    kb.planJobs({sourceIds:['src-aaaaaaaaaaaaaaaa']});
    const job=kb.claimNextJob({sourceIds:['src-aaaaaaaaaaaaaaaa']});
    const pagePath=path.join(transRoot,job.sourceId,'pages','0001.md');
    const original=readFileSync(pagePath,'utf8');
    writeFileSync(pagePath,`${original} modified`);
    assert.throws(()=>kb.importExtraction(job.id,extraction(job,job.pages[0].page,original)),/page changed/);
  } finally { kb.close(); }
  const fixture2=fixture(t); const outside=path.join(fixture2.root,'outside.md'); writeFileSync(outside,'outside evidence');
  const escapedPage=path.join(fixture2.transRoot,'src-aaaaaaaaaaaaaaaa','pages','0003.md'); symlinkSync(outside,escapedPage);
  const kb2=openKBase(fixture2.root,fixture2.runtime);
  try { assert.throws(()=>kb2.planJobs({sourceIds:['src-aaaaaaaaaaaaaaaa']}),/Unsafe page file path/); } finally { kb2.close(); }
});
