import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,readFile,writeFile,rm} from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {JSDOM} from 'jsdom';
import {initialKatholAtlas} from '../tools/plans/kathol-atlas.mjs';
import {entitiesSchema} from '../packages/knowledge-model/model.mjs';
import {createStore} from '../apps/knowledge-web/storage.mjs';
import {storeRoomImage} from '../apps/knowledge-web/room-media.mjs';
import {durationLabel,findItinerary} from '../apps/knowledge-web/public/modules/navigation-graph.js';
const fixture=()=>{const seed=initialKatholAtlas();return {schema_version:1,entities:seed.entities,navigation:seed.navigation,schedule:{shift1Start:0,shift2Start:12},mapPins:[],roomPins:[]};};

test('Documented navigation preserves unknown distances, excludes untimed links and scales the same itinerary interval',()=>{
  const {navigation:nav}=fixture();entitiesSchema.parse(fixture());
  assert.equal(nav.routes.length,61);assert.ok(nav.routes.every(r=>r.distanceLy===null));
  assert.equal(durationLabel({min:600,max:912}),'25 j – 38 j');
  assert.equal(findItinerary(nav,'nav-kal-shebbol','nav-torize').durationHours.min,18);
  const result=findItinerary(nav,'nav-nah-malis','nav-yvara');assert.deepEqual(result.durationHours,{min:1272,max:2256});assert.equal(result.containsRift,true);
  assert.deepEqual(findItinerary(nav,'nav-nah-malis','nav-yvara',{factor:2}).durationHours,{min:2544,max:4512});
  assert.equal(findItinerary(nav,'nav-republic-2','nav-republic-3'),null);
  assert.equal(findItinerary(nav,'nav-kal-shebbol','nav-yvara',{secondary:false}),null);
  assert.equal(findItinerary(nav,'nav-kal-shebbol','nav-yvara',{rift:false}),null);
  assert.equal(findItinerary(nav,'nav-kathol','nav-yvara'),null);
  assert.throws(()=>findItinerary(nav,'nav-yvara','nav-demonsgate',{factor:NaN}));
});

test('Atlas saves and media stay on disk, retain unknown fields and reject stale revisions, dangling links and malformed geometry',async()=>{
  const root=await mkdtemp(path.join(os.tmpdir(),'kathol-atlas-'));
  try{
    await mkdir(path.join(root,'content'));const original=fixture();original.futureMastering={version:2};original.navigation.nodes[0].unknownField={preserve:true};
    await writeFile(path.join(root,'content/entities.json'),JSON.stringify(original));const store=createStore(root,path.join(root,'runtime')),before=await store.read('entities'),next=structuredClone(original);
    next.navigation.routes[0].notes='Annotation de test';await store.update('entities',next,before.revision);const saved=await store.read('entities');
    assert.deepEqual(saved.data.futureMastering,original.futureMastering);assert.deepEqual(saved.data.navigation.nodes[0].unknownField,{preserve:true});
    await assert.rejects(store.update('entities',original,before.revision),e=>e.status===409);
    for(const change of [d=>d.navigation.nodes[0].position=[101,0],d=>d.navigation.routes[0].to='missing',d=>d.navigation.routes[0].points[0]=[10,10],d=>d.navigation.routes[0].durationHours={min:12,max:1},d=>d.navigation.nodes[0].entityIds=['missing'],d=>d.entities[0].properties.media=[{id:'bad',url:'javascript:alert(1)',caption:'',provenance:'personal'}]]){
      const invalid=structuredClone(saved.data);change(invalid);await assert.rejects(store.update('entities',invalid,saved.revision));
    }
    assert.deepEqual((await store.read('entities')).data,saved.data);
    const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII=','base64');
    const media=await storeRoomImage(path.join(root,'runtime'),png,'image/png','places');assert.match(media.url,/^\/runtime\/media\/places\//);assert.deepEqual(await readFile(path.join(root,'runtime',media.url.slice(9))),png);
    await assert.rejects(storeRoomImage(path.join(root,'runtime'),png,'image/png','../escape'),e=>e.status===400);
  }finally{await rm(root,{recursive:true,force:true});}
});

test('SVG atlas opens systems, calculates routes and persists route, calibration and planet media through revisioned saves',async()=>{
  const html=await readFile(new URL('../apps/knowledge-web/dist/index.html',import.meta.url),'utf8'),data=fixture(),campaign=JSON.parse(await readFile(new URL('../content/campaign.json',import.meta.url))),assets=JSON.parse(await readFile(new URL('../catalog/assets.json',import.meta.url)));
  const dom=new JSDOM(html,{url:'http://localhost:4321/#map'}),writes=[];
  Object.assign(globalThis,{window:dom.window,document:dom.window.document,location:dom.window.location,FormData:dom.window.FormData,MouseEvent:dom.window.MouseEvent});
  globalThis.fetch=async(url,options)=>{
    if(options?.method==='PUT'){const value=JSON.parse(options.body);entitiesSchema.parse(value);writes.push(value);assert.equal(options.headers['If-Match'],writes.length===1?'initial':'rev-'+(writes.length-1));return {ok:true,json:async()=>({revision:'rev-'+writes.length})};}
    return {ok:true,json:async()=>({campaign,entities:data,assets,sources:[],portraits:{},revisions:{entities:'initial',campaign:'campaign'}})};
  };
  await import('../apps/knowledge-web/public/modules/app.js');
  const submit=id=>{const form=document.getElementById(id);form.dispatchEvent(new dom.window.SubmitEvent('submit',{bubbles:true,cancelable:true,submitter:form.querySelector('button[type=submit],button')}));};
  const settle=async count=>{for(let i=0;i<40&&writes.length<count;i++)await new Promise(r=>setTimeout(r,5));assert.equal(writes.length,count);await new Promise(r=>setTimeout(r,10));};
  assert.ok(document.querySelector('#map svg'));assert.equal(document.querySelectorAll('#stellarSVG image').length,0);
  document.querySelector('svg [data-stellar-node="nav-episol"]').dispatchEvent(new dom.window.MouseEvent('click',{bubbles:true}));assert.match(document.getElementById('stellarInspector').textContent,/Dayark/);
  document.querySelector('svg [data-stellar-route="route-kal-shebbol--torize"]').dispatchEvent(new dom.window.MouseEvent('click',{bubbles:true}));document.querySelector('#stellarRouteEditor [name=notes]').value='Route annotée';submit('stellarRouteEditor');await settle(1);assert.equal(writes[0].navigation.routes[0].notes,'Route annotée');
  document.getElementById('stellarCalibrate').click();document.querySelector('#stellarNodeEditor [name=x]').value='70';submit('stellarNodeEditor');await settle(2);const n=writes[1].navigation.nodes.find(n=>n.id==='nav-episol');assert.equal(n.position[0],70);assert.ok(writes[1].navigation.routes.filter(r=>r.to===n.id).every(r=>r.points.at(-1)[0]===70));
  document.querySelector('[data-stellar-media="place-dayark"]').click();document.querySelector('#stellarMediaForm [name=asset]').value='asset-planet-aaris';document.querySelector('#stellarMediaForm [name=caption]').value='Carte de test personnelle';submit('stellarMediaForm');await settle(3);assert.equal(writes[2].entities.find(e=>e.id==='place-dayark').properties.media[0].provenance,'personal');
  window.closeModal();submit('stellarPlanner');assert.match(document.getElementById('stellarResult').textContent,/Rift est instable/);assert.ok(document.querySelectorAll('#stellarSVG .stellar-route.selected').length>1);
  const view=document.getElementById('stellarView');view.value='rift';view.dispatchEvent(new dom.window.Event('change'));assert.equal(document.getElementById('stellarSVG').getAttribute('viewBox'),'375 1540 415 500');
  document.getElementById('stellarNewNode').click();document.querySelector('#stellarNewNodeForm [name=name]').value='Monde personnel non localisé';submit('stellarNewNodeForm');await settle(4);assert.equal(writes[3].navigation.nodes.at(-1).position,null);assert.equal(writes[3].entities.at(-1).provenance,'personal');
  window.closeModal();document.getElementById('stellarNewRoute').click();document.querySelector('#stellarNewRouteForm [name=from]').value='nav-torize';document.querySelector('#stellarNewRouteForm [name=to]').value='nav-yvara';submit('stellarNewRouteForm');await settle(5);assert.equal(writes[4].navigation.routes.at(-1).durationHours,null);assert.equal(writes[4].navigation.routes.at(-1).provenance,'personal');dom.window.close();
});
