import { mkdir,readFile,writeFile } from 'node:fs/promises';
import path from 'node:path';
const root=process.cwd(),config=JSON.parse(await readFile('config.local.json','utf8'));
const [campaign,entities]=await Promise.all(['campaign','entities'].map(name=>readFile(path.join(root,'content',name+'.json'),'utf8').then(JSON.parse)));
const out=path.join(config.runtimeRoot,'exports');await mkdir(out,{recursive:true});
await writeFile(path.join(out,'knowledge-v1.json'),JSON.stringify({format:'darkstryder-knowledge',version:1,exportedAt:new Date().toISOString(),campaign,entities},null,2));
console.log(path.join(out,'knowledge-v1.json'));
