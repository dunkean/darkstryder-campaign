import { readFile, mkdir, rename, realpath, lstat, symlink } from 'node:fs/promises';
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
export async function publishTranscripts(projectRoot, { completedOnly = false } = {}) {
  const config = JSON.parse(await readFile(path.join(projectRoot, 'config.local.json'), 'utf8'));
  const runtime = await realpath(config.runtimeRoot);
  const sources = JSON.parse(await readFile(path.join(projectRoot, 'catalog/sources.json'), 'utf8'));
  const targetRoot = path.join(projectRoot, 'content/transcriptions');
  const ready = [], pending = [];
  for (const source of sources) {
    if (!/^src-[a-f0-9]{16}$/.test(source.id)) throw new Error('Identifiant de source invalide');
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
    if (imageInfo) {
      if (!imageInfo.isSymbolicLink() || await realpath(imageLink) !== await realpath(imageTarget)) throw new Error('Lien images existant incompatible');
    } else await symlink(imageTarget, imageLink, 'dir');
    for (const file of files) if (file.changed) await atomicWrite(file.output, file.bytes);
    await atomicWrite(path.join(destination, 'manifest.json'), manifestBytes);
    const metadata = {
      format: 'darkstryder-transcription', version: 1, sourceId: source.id,
      name: source.name, sha256: source.sha256, pdfPages: source.pages,
      engine: manifest.engine || manifest.model, provenance: 'extracted-evidence',
      fileHashes: Object.fromEntries(files.map(file => [file.name, digest(file.bytes)])),
    };
    await atomicWrite(metadataPath, JSON.stringify(metadata, null, 2) + '\n');
    totalPages += source.pages;
  }
  return { books: ready.length, pages: totalPages, pendingBooks: pending.length, destination: 'content/transcriptions' };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
  try { console.log(JSON.stringify(await publishTranscripts(projectRoot, { completedOnly: process.argv.includes('--completed-only') }))); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
