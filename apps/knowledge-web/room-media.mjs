import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {mkdir,writeFile,rename,realpath} from 'node:fs/promises';

export const MAX_ROOM_IMAGE=8*1024*1024;
export function inspectRoomImage(data,declaredType){
  const fail=(message,status=400)=>{throw Object.assign(new Error(message),{status});};
  if(data.length>MAX_ROOM_IMAGE)fail('Image trop volumineuse (8 Mo maximum)',413);
  let type,extension;
  if(data.length>=33&&data.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10]))&&data.toString('ascii',12,16)==='IHDR'){
    let offset=8,idat=false,ended=false;
    while(offset+12<=data.length){const length=data.readUInt32BE(offset),kind=data.toString('ascii',offset+4,offset+8);if(length>data.length-offset-12)fail('Structure PNG invalide');if(offset===8&&(length!==13||!data.readUInt32BE(16)||!data.readUInt32BE(20)))fail('Dimensions PNG invalides');if(kind==='IDAT'&&length)idat=true;offset+=12+length;if(kind==='IEND'){if(length!==0||offset!==data.length)fail('Structure PNG invalide');ended=true;break;}}
    if(!idat||!ended)fail('Structure PNG incomplète');type='image/png';extension='png';
  }
  else if(data.length>=4&&data[0]===255&&data[1]===216&&data[2]===255&&data.at(-2)===255&&data.at(-1)===217){type='image/jpeg';extension='jpg';}
  else if(data.length>=14&&['GIF87a','GIF89a'].includes(data.toString('ascii',0,6))&&data.readUInt16LE(6)>0&&data.readUInt16LE(8)>0&&data.at(-1)===59){type='image/gif';extension='gif';}
  else if(data.length>=20&&data.toString('ascii',0,4)==='RIFF'&&data.toString('ascii',8,12)==='WEBP'&&['VP8 ','VP8L','VP8X'].includes(data.toString('ascii',12,16))&&data.readUInt32LE(4)===data.length-8&&data.readUInt32LE(16)<=data.length-20){type='image/webp';extension='webp';}
  else fail('Format image invalide : PNG, JPEG, WebP ou GIF requis');
  if(declaredType!==type)fail('Le type annoncé ne correspond pas au fichier image');
  return {type,extension};
}
export async function storeRoomImage(runtime,data,declaredType){
  const {extension}=inspectRoomImage(data,declaredType),name=`${randomUUID()}.${extension}`;
  await mkdir(runtime,{recursive:true});const root=await realpath(runtime);
  const media=path.join(root,'media');await mkdir(media,{recursive:true});const mediaResolved=await realpath(media);
  if(!mediaResolved.startsWith(root+path.sep))throw Object.assign(new Error('Chemin média interdit'),{status:403});
  const rooms=path.join(mediaResolved,'rooms');await mkdir(rooms,{recursive:true});const folder=await realpath(rooms);
  if(!folder.startsWith(root+path.sep))throw Object.assign(new Error('Chemin média interdit'),{status:403});
  const destination=path.join(folder,name),temporary=destination+'.tmp';
  await writeFile(temporary,data,{flag:'wx'});await rename(temporary,destination);
  return {url:'/runtime/media/rooms/'+name};
}
