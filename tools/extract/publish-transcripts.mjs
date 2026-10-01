import { readFile, mkdir, rename, realpath, lstat, symlink, readdir, unlink } from 'node:fs/promises';
import { createHash, randomUUID } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { writeFile } from 'node:fs/promises';

const digest = value => createHash('sha256').update(value).digest('hex');
const inside = (root, candidate) => candidate.startsWith(root + path.sep);
const optionalRead = async filename => {
  try { return await readFile(filename); } catch (error) { if (error.code === 'ENOENT') return null; throw error; }
};
async function atomicWrite(filename, value) {
  const temp = `${filename}.${randomUUID()}.tmp`;
  await writeFile(temp, value);
  await rename(temp, filename);
}

// Runtime is the OCR checkpoint authority. These copies are evidence for authoring,
// separate from edited entities, and retain the original page/image references.
export async function publishTranscripts(projectRoot, { completedOnly = false, copyMedia = false } = {}) {
  const config = JSON.parse(await readFile(path.join(projectRoot, 'config.local.json'), 'utf8'));
  const runtime = await realpath(config.runtimeRoot);
  const sources = JSON.parse(await readFile(path.join(projectRoot, 'catalog/sources.json'), 'utf8'));
  const scopeBytes = await optionalRead(path.join(projectRoot, 'config/corpus-scope.json'));
  const skipped = new Map((scopeBytes ? JSON.parse(scopeBytes).skipSources || [] : []).map(entry => [entry.id, entry.reason]));
  const targetRoot = path.join(projectRoot, 'content/transcriptions');
  const ready = [], pending = [];
  for (const source of sources) {
    if (!/^src-[a-f0-9]{16}$/.test(source.id)) throw new Error('Identifiant de source invalide');
    if (skipped.has(source.id)) continue;
    const folder = path.resolve(runtime, source.output);
    if (!inside(runtime, folder)) throw new Error('Chemin OCR hors runtime');
    const manifestBytes = await optionalRead(path.join(folder, 'manifest.json'));
    if (!manifestBytes) { pending.push(source.id); continue; }
    const manifest = JSON.parse(manifestBytes);
    const pages = new Set(manifest.pages?.map(page => page.page));
    if (manifest.sourceId !== source.id || manifest.sha256 !== source.sha256) throw new Error(`Provenance incompatible : ${source.id}`);
    if (pages.size !== source.pages || Array.from({ length: source.pages }, (_, i) => i + 1).some(page => !pages.has(page))) {
      pending.push(source.id); continue;
    }
    const resolved = await realpath(folder);
    if (!inside(runtime, resolved)) throw new Error('Lien OCR hors runtime');
    // The last checkpoint can precede rebuilding the assembled Markdown.
    const book = await optionalRead(path.join(resolved, 'book.md'));
    if (!book) { pending.push(source.id); continue; }
    if (manifest.engineId && (book.toString().match(/^## (?:Page PDF|Page pdf|Feuille|Segment documentaire) \d+$/gm) || []).length !== source.pages) {
      pending.push(source.id); continue;
    }
    ready.push({ source, folder: resolved, manifest, manifestBytes });
  }
  if (pending.length && !completedOnly) throw new Error(`${pending.length} livre(s) attendent la fin de l'OCR ; aucune migration effectuée`);
  await mkdir(targetRoot, { recursive: true });
  const targetResolved = await realpath(targetRoot);
  if (!inside(await realpath(projectRoot), targetResolved)) throw new Error('Destination hors projet');
  let totalPages = 0;
  for (const { source, folder, manifest, manifestBytes } of ready) {
    const destination = path.join(targetRoot, source.id);
    await mkdir(destination, { recursive: true });
    if (!inside(targetResolved, await realpath(destination))) {
      throw new Error('Destination de transcription hors projet');
    }
    await mkdir(path.join(destination, 'pages'), { recursive: true });
    if (!inside(targetResolved, await realpath(path.join(destination, 'pages')))) throw new Error('Pages de transcription hors projet');
    const metadataPath = path.join(destination, 'transcription.json');
    const oldMetadata = await optionalRead(metadataPath);
    const previous = oldMetadata ? JSON.parse(oldMetadata) : null;
    if (previous && (previous.sourceId !== source.id || previous.sha256 !== source.sha256)) throw new Error(`Destination incompatible : ${source.id}`);
    const names = ['book.md', ...Array.from({ length: source.pages }, (_, i) => `pages/${String(i + 1).padStart(4, '0')}.md`)];
    const files = [];
    for (const name of names) {
      const input = path.join(folder, name), output = path.join(destination, name);
      if (!inside(folder, await realpath(input))) throw new Error('Markdown hors dossier OCR');
      const bytes = await readFile(input), existing = await optionalRead(output);
      if (existing && digest(existing) !== digest(bytes) && digest(existing) !== previous?.fileHashes?.[name]) {
        throw new Error(`Transcription modifiée localement, conservée : ${source.id}/${name}`);
      }
      // Never follow an output symlink, even when its text matches the source.
      if (existing && (await lstat(output)).isSymbolicLink()) throw new Error('Markdown cible est un lien symbolique');
      files.push({ name, output, bytes, changed: !existing || digest(existing) !== digest(bytes) });
    }
    const imageTarget = path.join(folder, 'images'), imageLink = path.join(destination, 'images');
    if (!inside(folder, await realpath(imageTarget))) throw new Error('Images hors dossier OCR');
    let imageInfo;
    try { imageInfo = await lstat(imageLink); } catch (error) { if (error.code !== 'ENOENT') throw error; }
    if(copyMedia){
      if(imageInfo?.isSymbolicLink()){
        if(await realpath(imageLink)!==await realpath(imageTarget))throw new Error('Lien images existant incompatible');
        // Replace only our validated link, never its runtime target. Checkpoints remain intact.
        const staged=imageLink+'.'+randomUUID()+'.tmp';await mkdir(staged);
        await copyDirectory(imageTarget,staged,folder);await unlink(imageLink);await rename(staged,imageLink);
      }else{
        await mkdir(imageLink,{recursive:true});
        if(!inside(destination,await realpath(imageLink)))throw new Error('Destination images hors transcription');
        if(!previous?.mediaCopied||previous?.manifestHash!==digest(manifestBytes))await copyDirectory(imageTarget,imageLink,folder);
      }
      if(!previous?.mediaCopied||previous?.manifestHash!==digest(manifestBytes)){
        for(const page of manifest.pages){
          const scan=`${String(page.page).padStart(4,'0')}.jpg`,scanPath=path.join(folder,'pages',scan);
          if(await optionalRead(scanPath))await copyEvidence(scanPath,path.join(destination,'pages',scan),folder);
        }
        const structure=path.join(folder,'structure');
        try{await lstat(structure);await mkdir(path.join(destination,'structure'),{recursive:true});await copyDirectory(structure,path.join(destination,'structure'),folder);}catch(error){if(error.code!=='ENOENT')throw error;}
      }
    }else if (imageInfo) {
      if (!imageInfo.isSymbolicLink() || await realpath(imageLink) !== await realpath(imageTarget)) throw new Error('Lien images existant incompatible');
    } else await symlink(imageTarget, imageLink, 'dir');
    for (const file of files) if (file.changed) await atomicWrite(file.output, file.bytes);
    await atomicWrite(path.join(destination, 'manifest.json'), manifestBytes);
    const metadata = {
      format: 'darkstryder-transcription', version: 1, sourceId: source.id,
      name: source.name, sha256: source.sha256, pdfPages: !source.format || source.format==='pdf' ? source.pages : null,
      engine: manifest.engine || manifest.model, provenance: 'extracted-evidence',
      sourceFormat:source.format||'pdf', units:source.pages, unitLabel:source.unitLabel||'page PDF',
      locator:source.locator, copies:source.copies||[], collections:source.collections||[source.collection],
      evidenceProvenance:source.evidenceProvenance||'published-source',
      mediaCopied:copyMedia||previous?.mediaCopied||false,manifestHash:digest(manifestBytes),
      fileHashes: Object.fromEntries(files.map(file => [file.name, digest(file.bytes)])),
    };
    await atomicWrite(metadataPath, JSON.stringify(metadata, null, 2) + '\n');
    totalPages += source.pages;
  }
  const index={version:1,scope:'config/corpus-scope.json',sources:sources.map(source=>({id:source.id,name:source.name,format:source.format||'pdf',collection:source.collection,collections:source.collections||[source.collection],locator:source.locator,copies:source.copies||[],units:source.pages,unitLabel:source.unitLabel||'page PDF',status:skipped.has(source.id)?'skipped':ready.some(r=>r.source.id===source.id)?'complete':'pending',...(skipped.has(source.id)?{skipReason:skipped.get(source.id)}:{markdown:`${source.id}/book.md`})}))};
  await atomicWrite(path.join(targetRoot,'index.json'),JSON.stringify(index,null,2)+'\n');
  return { books: ready.length, pages: totalPages, pendingBooks: pending.length, skippedBooks:sources.filter(source=>skipped.has(source.id)).length, destination: 'content/transcriptions' };
}

async function copyEvidence(input,output,inputRoot){
  if(!inside(inputRoot,await realpath(input)))throw new Error('Fichier média hors extraction');
  const bytes=await readFile(input),existing=await optionalRead(output);
  if(existing){if((await lstat(output)).isSymbolicLink())throw new Error('Média cible lié');if(digest(existing)!==digest(bytes))throw new Error('Média modifié localement, conservé : '+output);return;}
  await atomicWrite(output,bytes);
}
async function copyDirectory(input,output,inputRoot){
  if(!inside(inputRoot,await realpath(input)))throw new Error('Dossier média hors extraction');
  if(!(await lstat(output)).isDirectory()||(await lstat(output)).isSymbolicLink())throw new Error('Dossier cible invalide');
  for(const entry of await readdir(input,{withFileTypes:true})){
    if(!entry.isFile())throw new Error('Entrée média non régulière');
    await copyEvidence(path.join(input,entry.name),path.join(output,entry.name),inputRoot);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
  try { console.log(JSON.stringify(await publishTranscripts(projectRoot, { completedOnly: process.argv.includes('--completed-only'),copyMedia:!process.argv.includes('--linked-media') }))); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
