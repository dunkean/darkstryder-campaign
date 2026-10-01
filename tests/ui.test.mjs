import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { JSDOM } from 'jsdom';

test('Every navigation view and crew detail renders without browser errors',async()=>{
  const html=await readFile(new URL('../apps/knowledge-web/dist/index.html',import.meta.url),'utf8');
  const campaign=JSON.parse(await readFile(new URL('../content/campaign.json',import.meta.url),'utf8'));
  const entities=JSON.parse(await readFile(new URL('../content/entities.json',import.meta.url),'utf8'));
  const assets=JSON.parse(await readFile(new URL('../catalog/assets.json',import.meta.url),'utf8'));
  const sources=JSON.parse(await readFile(new URL('../catalog/sources.json',import.meta.url),'utf8'));
  const portraits=JSON.parse(await readFile(new URL('../catalog/portraits.json',import.meta.url),'utf8'));
  const dom=new JSDOM(html,{url:'http://localhost:4321'});
  Object.assign(globalThis,{window:dom.window,document:dom.window.document,location:dom.window.location,FormData:dom.window.FormData});
  globalThis.fetch=async url=>({ok:true,json:async()=>String(url).includes('bootstrap')?{campaign,entities,assets,sources,portraits,revisions:{campaign:'test',entities:'test'}}:{state:'running',completedPages:4,totalPages:1614}});
  await import('../apps/knowledge-web/public/modules/app.js');
  for(const key of ['openPerson','openRoom','closeModal','showStats','generateRandom','roomIdByLabel','roomIdsByLabel','rand','randLocationFrom','extraRoom','renderRandomSnapshot'])globalThis[key]=dom.window[key];
  assert.match(document.getElementById('overview').textContent,/120/);
  for(const id of ['ship','crew','hierarchy','skills','relations','droids','random','map','library','npc','faction','event','adventure','planet']){
    location.hash=id;window.dispatchEvent(new dom.window.HashChangeEvent('hashchange'));assert.ok(document.getElementById(id).innerHTML.length>50,id);
  }
  window.openPerson(campaign.crew.members[0].id);assert.match(document.getElementById('modalContent').textContent,/Keleman Ciro/);assert.match(document.getElementById('modalContent').textContent,/Modifier ce personnage/);
  if(campaign.crew.members[0].stats.generated)assert.match(document.getElementById('modalContent').textContent,/Caractéristiques générées/);
  window.editPerson(campaign.crew.members[0].id);assert.ok(document.getElementById('personEditor'));
  window.editEntity(null,'planet');assert.equal(document.querySelector('[name=type]').value,'planet');
  dom.window.close();
});
