import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile, realpath, rm, access } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { publishTranscripts } from '../tools/extract/publish-transcripts.mjs';

test('Completed OCR is published with provenance and images while local edits are preserved', async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), 'darkstryder-transcripts-'));
  const project = path.join(temporary, 'project'), runtime = path.join(temporary, 'runtime');
  const id = 'src-0123456789abcdef', pendingId = 'src-fedcba9876543210';
  const folder = path.join(runtime, 'extracted', id);
  try {
    await mkdir(path.join(project, 'catalog'), { recursive: true });
    await mkdir(path.join(folder, 'pages'), { recursive: true });
    await mkdir(path.join(folder, 'images'));
    await writeFile(path.join(project, 'config.local.json'), JSON.stringify({ runtimeRoot: runtime }));
    const source = { id, name: 'Livre test', sha256: 'a'.repeat(64), pages: 1, output: `extracted/${id}` };
    const sources = [source, { ...source, id: pendingId, output: `extracted/${pendingId}` }];
    await writeFile(path.join(project, 'catalog/sources.json'), JSON.stringify(sources));
    await writeFile(path.join(folder, 'manifest.json'), JSON.stringify({ sourceId: id, sha256: source.sha256, engine: 'test', pages: [{ page: 1, flags: [] }] }));
    const page = `<!-- source: ${id}; pdf_page: 1 -->\n\n![Image](../images/test.png)\nTexte transcrit.\n`;
    const book = `# Livre test\n\nSource: ${id}\n\n## Page PDF 1\n![Image](images/test.png)\n`;
    await writeFile(path.join(folder, 'pages/0001.md'), page);
    await writeFile(path.join(folder, 'book.md'), book);
    await assert.rejects(publishTranscripts(project), /aucune migration effectuée/);
    await assert.rejects(access(path.join(project, 'content/transcriptions', id)), { code: 'ENOENT' });
    const first = await publishTranscripts(project, { completedOnly: true });
    assert.equal(first.books, 1);assert.equal(first.pages, 1);assert.equal(first.pendingBooks, 1);
    const destination = path.join(project, 'content/transcriptions', id);
    assert.equal(await readFile(path.join(destination, 'pages/0001.md'), 'utf8'), page);
    assert.equal(await readFile(path.join(destination, 'book.md'), 'utf8'), book);
    assert.equal(await realpath(path.join(destination, 'images')), await realpath(path.join(folder, 'images')));
    const metadata = JSON.parse(await readFile(path.join(destination, 'transcription.json'), 'utf8'));
    assert.equal(metadata.sourceId, id);assert.equal(metadata.sha256, source.sha256);assert.equal(metadata.provenance, 'extracted-evidence');
    await publishTranscripts(project, { completedOnly: true });
    await writeFile(path.join(destination, 'pages/0001.md'), 'Correction éditoriale locale');
    await assert.rejects(publishTranscripts(project, { completedOnly: true }), /modifiée localement, conservée/);
    assert.equal(await readFile(path.join(destination, 'pages/0001.md'), 'utf8'), 'Correction éditoriale locale');
    assert.equal(await readFile(path.join(folder, 'pages/0001.md'), 'utf8'), page);
    await writeFile(path.join(project, 'catalog/sources.json'), JSON.stringify([{ ...source, output: '../outside-runtime' }]));
    await assert.rejects(publishTranscripts(project), /Chemin OCR hors runtime/);
  } finally { await rm(temporary, { recursive: true, force: true }); }
});
