import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from '../apps/knowledge-web/server.mjs';

test('Local HTTP serves the app and catalog, confines file paths and rejects foreign writes',async()=>{
  const server=createServer();await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const base=`http://127.0.0.1:${server.address().port}`;
  try{
    const page=await fetch(base+'/');assert.equal(page.status,200);assert.match(await page.text(),/DARKSTRYDER/);
    const data=await (await fetch(base+'/api/bootstrap')).json();assert.equal(data.campaign.crew.members.length,120);assert.ok(data.sources.length>=16);assert.ok(data.sources.every(source=>/^src-[a-f0-9]{16}$/.test(source.id)));
    for(const url of ['/modules/app.js','/styles/app.css','/vendor/marked.js','/vendor/purify.js'])assert.equal((await fetch(base+url)).status,200,url);
    const denied=await fetch(base+'/api/entities',{method:'PUT',headers:{Origin:'https://evil.example','Content-Type':'application/json'},body:'{}'});assert.equal(denied.status,403);
    const traversal=await fetch(base+'/%2e%2e%2f%2e%2e%2fcontent/campaign.json');assert.equal(traversal.status,403);
    assert.equal((await fetch(base+'/api/source?id=missing&page=1')).status,404);
  }finally{await new Promise(resolve=>server.close(resolve));}
});
