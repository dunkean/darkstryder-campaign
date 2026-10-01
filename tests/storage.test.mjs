import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, readdir, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createStore } from '../apps/knowledge-web/storage.mjs';

test('Writes persist, backups preserve previous data, stale and invalid writes are rejected',async()=>{
  const root=await mkdtemp(path.join(os.tmpdir(),'darkstryder-test-')),runtime=path.join(root,'runtime');
  try{
    await mkdir(path.join(root,'content'));const initial={schema_version:1,entities:[],schedule:{shift1Start:0,shift2Start:12},mapPins:[],roomPins:[]};
    await writeFile(path.join(root,'content/entities.json'),JSON.stringify(initial));
    const store=createStore(root,runtime),before=await store.read('entities');
    const next={...initial,entities:[{id:'test-planet',type:'planet',name:'Test',summary:'',body:'',tags:[],links:[],visibility:'mj',provenance:'personal',sources:[],properties:{}}]};
    const saved=await store.update('entities',next,before.revision);assert.notEqual(saved.revision,before.revision);assert.equal((await store.read('entities')).data.entities[0].name,'Test');
    const backups=await readdir(path.join(runtime,'backups'));assert.equal(backups.length,1);assert.deepEqual(JSON.parse(await readFile(path.join(runtime,'backups',backups[0]),'utf8')),initial);
    await assert.rejects(store.update('entities',initial,before.revision),error=>error.status===409);
    await assert.rejects(store.update('entities',{...next,entities:[{id:'oops'}]},saved.revision));
    assert.equal((await store.read('entities')).data.entities.length,1);
    const [one,two]=await Promise.allSettled([store.update('entities',{...next,schedule:{shift1Start:1,shift2Start:13}},saved.revision),store.update('entities',{...next,schedule:{shift1Start:2,shift2Start:14}},saved.revision)]);
    assert.equal(one.status,'fulfilled');assert.equal(two.status,'rejected');assert.equal(two.reason.status,409);
  }finally{await rm(root,{recursive:true,force:true});}
});

test('Campaign edits preserve unknown legacy fields in JSON and backups',async()=>{
  const root=await mkdtemp(path.join(os.tmpdir(),'darkstryder-campaign-test-')),runtime=path.join(root,'runtime');
  try{
    await mkdir(path.join(root,'content'));
    const campaign=JSON.parse(await readFile(new URL('../content/campaign.json',import.meta.url),'utf8'));
    campaign.customReference={edition:'personal',future:{version:3}};
    campaign.crew.members[0].customNotes={approved:true};
    await writeFile(path.join(root,'content/campaign.json'),JSON.stringify(campaign));
    const store=createStore(root,runtime),before=await store.read('campaign');
    const edited=structuredClone(before.data);edited.crew.members[0].name+=' (édition)';
    await store.update('campaign',edited,before.revision);
    const saved=(await store.read('campaign')).data;
    assert.deepEqual(saved.customReference,campaign.customReference);
    assert.deepEqual(saved.crew.members[0].customNotes,campaign.crew.members[0].customNotes);
    const invalid=structuredClone(saved);
    invalid.crew.members[0].assignments.shift1=[{room_ids:['virtual-undeclared']}];
    await assert.rejects(store.update('campaign',invalid,(await store.read('campaign')).revision),/Pièce inconnue/);
    const [backup]=await readdir(path.join(runtime,'backups'));
    assert.deepEqual(JSON.parse(await readFile(path.join(runtime,'backups',backup),'utf8')),campaign);
  }finally{await rm(root,{recursive:true,force:true});}
});
