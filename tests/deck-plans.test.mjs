import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,readdir,rm,symlink} from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {JSDOM} from 'jsdom';
import {validateCampaign} from '../packages/knowledge-model/model.mjs';
import {createStore} from '../apps/knowledge-web/storage.mjs';
import {inspectRoomImage,storeRoomImage,MAX_ROOM_IMAGE} from '../apps/knowledge-web/room-media.mjs';
import traces from '../tools/plans/farstar-traces.mjs';

const campaignFile=new URL('../content/campaign.json',import.meta.url);
const fixtureCampaign=async()=>{const campaign=JSON.parse(await readFile(campaignFile,'utf8'));campaign.ship.deckPlans=structuredClone(traces);for(const room of campaign.ship.rooms)delete room.media;return campaign;};
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII=','base64');

test('Six manually calibrated vector plans keep stable room IDs, reject bad geometry and preserve legacy fields on disk',async()=>{
  const root=await mkdtemp(path.join(os.tmpdir(),'darkstryder-decks-'));
  try{
    await mkdir(path.join(root,'content'));const campaign=await fixtureCampaign();
    assert.equal(campaign.ship.deckPlans.length,6);assert.equal(campaign.ship.deckPlans.flatMap(p=>p.regions).length,130);
    campaign.ship.customFuture={owner:'mastering'};campaign.ship.rooms[0].customField={preserved:true};campaign.ship.deckPlans[0].regions[0].customTrace={reference:'manual'};
    await writeFile(path.join(root,'content/campaign.json'),JSON.stringify(campaign));
    const store=createStore(root,path.join(root,'runtime')),before=await store.read('campaign'),edited=structuredClone(campaign),zone=edited.ship.deckPlans[0].regions.find(r=>r.roomId);
    zone.label='Senseurs calibrés';zone.points[0][0]+=.1;
    edited.ship.rooms[0].media=[{id:'test-media',url:'/runtime/media/rooms/test.png',caption:'Salle',provenance:'personal',future:{credit:'MJ'}}];
    await store.update('campaign',edited,before.revision);const saved=await store.read('campaign');
    assert.deepEqual(saved.data.ship.customFuture,campaign.ship.customFuture);assert.deepEqual(saved.data.ship.rooms[0].customField,{preserved:true});assert.deepEqual(saved.data.ship.rooms[0].media[0].future,{credit:'MJ'});
    assert.deepEqual(saved.data.ship.rooms.map(r=>r.id),campaign.ship.rooms.map(r=>r.id));assert.equal(saved.data.ship.deckPlans[0].regions.find(r=>r.roomId).label,'Senseurs calibrés');
    await assert.rejects(store.update('campaign',campaign,before.revision),error=>error.status===409);
    for(const mutate of [d=>d.ship.deckPlans[0].regions[0].points[0][0]=101,d=>d.ship.deckPlans[0].regions[0].points=[[1,1],[2,2]],d=>d.ship.deckPlans[0].regions[0].roomId='d3-r10',d=>d.ship.rooms[0].media[0].url='/runtime/media/../../campaign.json',d=>d.ship.rooms[0].media[0].url='javascript:alert(1)']){
      const invalid=structuredClone(saved.data);mutate(invalid);await assert.rejects(store.update('campaign',invalid,saved.revision));
    }
    assert.deepEqual((await store.read('campaign')).data,saved.data);
  }finally{await rm(root,{recursive:true,force:true});}
});

test('Room image uploads validate format, size and runtime path before writing',async()=>{
  const temp=await mkdtemp(path.join(os.tmpdir(),'darkstryder-room-images-')),runtime=path.join(temp,'runtime'),outside=path.join(temp,'outside');
  try{
    assert.equal(inspectRoomImage(png,'image/png').extension,'png');
    assert.throws(()=>inspectRoomImage(png,'image/jpeg'),/correspond/);assert.throws(()=>inspectRoomImage(Buffer.from('<svg onload="alert(1)">'),'image/svg+xml'),/Format/);
    assert.throws(()=>inspectRoomImage(png.subarray(0,33),'image/png'),/incomplète/);
    assert.throws(()=>inspectRoomImage(Buffer.alloc(MAX_ROOM_IMAGE+1),'image/png'),error=>error.status===413);
    const result=await storeRoomImage(runtime,png,'image/png');assert.match(result.url,/^\/runtime\/media\/rooms\/[\w-]+\.png$/);
    assert.deepEqual(await readFile(path.join(runtime,result.url.slice('/runtime/'.length))),png);
    await mkdir(outside);const otherRuntime=path.join(temp,'other-runtime');await mkdir(otherRuntime);await symlink(outside,path.join(otherRuntime,'media'));
    await assert.rejects(storeRoomImage(otherRuntime,png,'image/png'),error=>error.status===403);
    assert.deepEqual(await readdir(outside),[]);
    const thirdRuntime=path.join(temp,'third-runtime');await mkdir(path.join(thirdRuntime,'media'),{recursive:true});await symlink(outside,path.join(thirdRuntime,'media','rooms'));
    await assert.rejects(storeRoomImage(thirdRuntime,png,'image/png'),error=>error.status===403);assert.deepEqual(await readdir(outside),[]);
  }finally{await rm(temp,{recursive:true,force:true});}
});

test('Native SVG room selection, association, calibration and media editing use revisioned JSON saves',async()=>{
  const html=await readFile(new URL('../apps/knowledge-web/dist/index.html',import.meta.url),'utf8'),campaign=await fixtureCampaign();
  const readCatalog=async name=>JSON.parse(await readFile(new URL(`../catalog/${name}.json`,import.meta.url),'utf8'));
  const entities=JSON.parse(await readFile(new URL('../content/entities.json',import.meta.url),'utf8'));
  const [assets,sources,portraits]=await Promise.all(['assets','sources','portraits'].map(readCatalog));
  const dom=new JSDOM(html,{url:'http://localhost:4321/#ship'}),writes=[];
  Object.assign(globalThis,{window:dom.window,document:dom.window.document,location:dom.window.location,FormData:dom.window.FormData});
  globalThis.fetch=async(url,options)=>{
    if(options?.method==='PUT'){const data=JSON.parse(options.body);validateCampaign(data);writes.push({data,revision:options.headers['If-Match']});return {ok:true,json:async()=>({revision:'revision-'+writes.length})};}
    return {ok:true,json:async()=>({campaign,entities,assets,sources,portraits,revisions:{campaign:'initial',entities:'initial'}})};
  };
  await import('../apps/knowledge-web/public/modules/app.js');
  const waitForWrite=async length=>{for(let i=0;i<20&&writes.length<length;i++)await new Promise(resolve=>setTimeout(resolve,5));assert.equal(writes.length,length);await new Promise(resolve=>setTimeout(resolve,5));};
  const change=(id,value)=>{const el=document.getElementById(id);el.value=value;el.dispatchEvent(new dom.window.Event('change'));};
  const submit=id=>{const form=document.getElementById(id),button=form.querySelector('[type=submit],button');form.dispatchEvent(new dom.window.SubmitEvent('submit',{bubbles:true,cancelable:true,submitter:button}));};
  assert.ok(document.querySelector('#ship svg'));assert.equal(document.querySelectorAll('#ship svg image,#deckViewport img').length,0);
  const zone=campaign.ship.deckPlans[0].regions.find(r=>r.roomId==='d1-r15');document.querySelector(`[data-region="${zone.id}"]`).dispatchEvent(new dom.window.MouseEvent('click',{bubbles:true}));
  assert.ok(document.getElementById('deckInspector').textContent.includes(campaign.ship.rooms.find(r=>r.id==='d1-r15').label));
  document.querySelector('#zoneEditor [name=label]').value='Soute avant validée';submit('zoneEditor');await waitForWrite(1);assert.equal(writes[0].revision,'initial');assert.equal(writes[0].data.ship.deckPlans[0].regions.find(r=>r.id===zone.id).label,'Soute avant validée');
  document.getElementById('deckEdit').click();const handle=document.querySelector('[data-vertex="0"]'),before=Number(handle.getAttribute('cx'));
  handle.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));submit('zoneEditor');await waitForWrite(2);assert.equal(writes[1].revision,'revision-1');assert.equal(writes[1].data.ship.deckPlans[0].regions.find(r=>r.id===zone.id).points[0][0],before+.1);
  document.getElementById('deckMediaEdit').click();document.querySelector('#roomMediaEditor [name=assetId]').value='asset-1';document.querySelector('#roomMediaEditor [name=caption]').value='Référence salle';submit('roomMediaEditor');await waitForWrite(3);
  assert.equal(writes[2].data.ship.rooms.find(r=>r.id==='d1-r15').media[0].url,'/asset/asset-1');assert.match(document.getElementById('modalContent').textContent,/Référence salle/);
  for(const plan of campaign.ship.deckPlans){change('deckSelect',plan.id);assert.equal(document.querySelectorAll('#ship [data-region]').length,plan.regions.length);}
  dom.window.close();
});
