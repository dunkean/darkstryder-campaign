import { readFile, writeFile, mkdir, rename } from 'node:fs/promises';
import { createHash, randomUUID } from 'node:crypto';
import path from 'node:path';
import { entitiesSchema, validateCampaign } from '../../packages/knowledge-model/model.mjs';

export const revision=text=>createHash('sha256').update(text).digest('hex');
export function createStore(root,runtime){
  const files={campaign:path.join(root,'content/campaign.json'),entities:path.join(root,'content/entities.json')};
  let queue=Promise.resolve();
  async function read(kind){const text=await readFile(files[kind],'utf8');return {data:JSON.parse(text),revision:revision(text)};}
  async function update(kind,data,expected){
    const work=queue.then(async()=>{
      if(!files[kind])throw Object.assign(new Error('Fichier inconnu'),{status:404});
      const old=await read(kind);
      if(!expected || old.revision!==expected)throw Object.assign(new Error('Les données ont changé. Recharge la page avant de sauvegarder.'),{status:409});
      const validated=kind==='entities'?entitiesSchema.parse(data):validateCampaign(data);
      const text=JSON.stringify(validated,null,2)+'\n';
      const backupDir=path.join(runtime,'backups');await mkdir(backupDir,{recursive:true});
      await writeFile(path.join(backupDir,`${kind}-${Date.now()}-${randomUUID()}.json`),JSON.stringify(old.data,null,2)+'\n');
      const temp=files[kind]+'.'+randomUUID()+'.tmp';
      await writeFile(temp,text,'utf8');await rename(temp,files[kind]);
      return {revision:revision(text)};
    });
    queue=work.catch(()=>{});return work;
  }
  return {read,update};
}
