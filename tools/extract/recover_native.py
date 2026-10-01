"""Recover explicitly flagged native text omissions without another OCR run."""
import fcntl
import hashlib
import json
import shutil
import time
from pathlib import Path

import fitz
from common import NATIVE_TEXT_MARKER, append_native_text, rebuild, write_json


def main():
    project = Path(__file__).resolve().parents[2]
    config = json.loads((project / 'config.local.json').read_text())
    runtime = Path(config['runtimeRoot']).resolve()
    source_root = Path(config['sourceRoot']).resolve()
    locks = []
    for name in ['ocr-job.lock', 'ocr.lock']:
        handle = (runtime / name).open('a+')
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locks.append(handle)
    status = json.loads((runtime / 'ocr-status.json').read_text())
    if status.get('state') != 'complete':
        raise RuntimeError('Finish OCR before native-text recovery')
    backup = runtime / 'backups' / time.strftime('native-text-recovery-%Y%m%d-%H%M%S')
    recovered = []
    for source in json.loads((project / 'catalog/sources.json').read_text()):
        folder = (runtime / source['output']).resolve()
        if not folder.is_relative_to(runtime):
            raise RuntimeError('Output outside runtime')
        manifest = json.loads((folder / 'manifest.json').read_text())
        if manifest.get('sourceId') != source['id'] or manifest.get('sha256') != source['sha256'] or manifest.get('engineId') != 'docling-rapidocr-torch-cuda-v1':
            raise RuntimeError('Incompatible OCR provenance')
        selected = [p for p in manifest['pages'] if 'text-coverage-low' in p.get('flags', []) and not p.get('nativeTextSupplement')]
        if not selected:
            continue
        original = (source_root / source['locator']['path']).resolve()
        if not original.is_relative_to(source_root):
            raise RuntimeError('Original outside source root')
        digest = hashlib.sha256()
        with original.open('rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(chunk)
        if digest.hexdigest() != source['sha256']:
            raise RuntimeError('Original PDF changed')
        archived = backup / source['id']
        (archived / 'pages').mkdir(parents=True, exist_ok=False)
        for name in ['manifest.json', 'book.md', 'search.json']:
            shutil.copy2(folder / name, archived / name)
        with fitz.open(original) as document:
            for report in selected:
                number = report['page']
                page = folder / 'pages' / f'{number:04}.md'
                if page.is_symlink():
                    raise RuntimeError('Refusing linked page output')
                text = page.read_text()
                if NATIVE_TEXT_MARKER in text:
                    raise RuntimeError('Unrecorded native supplement requires review')
                native = document[number - 1].get_text().strip()
                if len(native) != report['nativeCharacters']:
                    raise RuntimeError('Native text differs from extraction report')
                shutil.copy2(page, archived / 'pages' / page.name)
                replacement = append_native_text(text, native)
                temp = page.with_suffix('.tmp')
                temp.write_text(replacement, encoding='utf8')
                temp.replace(page)
                report['flags'].append('native-text-layer-recovered')
                report['nativeTextSupplement'] = {
                    'origin': 'pdf-text-layer', 'uncorrected': True,
                    'characters': len(native), 'includedInDoclingStructure': False}
                recovered.append({'sourceId': source['id'], 'page': number, 'nativeCharacters': len(native)})
        write_json(folder / 'manifest.json', manifest)
        rebuild(folder, source)
    result = {'recoveredPages': recovered, 'backup': str(backup) if recovered else None}
    if recovered:
        write_json(backup / 'recovery-report.json', result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
