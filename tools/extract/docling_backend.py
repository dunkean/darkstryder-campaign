"""Sequential, resumable local CUDA extraction using the existing Biosense stack.

Native PDF text is preserved; scanned regions use RapidOCR English. Heron layout
and TableFormer accurate run on CUDA. Every page has Markdown, scan and structure.
"""
import argparse
import fcntl
import gc
import hashlib
import importlib.metadata
import json
import logging
import os
import re
import signal
import time
from pathlib import Path
from common import rebuild, write_json

ROOT = Path(__file__).resolve().parents[2]
CONFIG = json.loads((ROOT / 'config.local.json').read_text(encoding='utf-8-sig'))
ENGINE = 'docling-rapidocr-torch-cuda-v1'
os.environ.setdefault('OMP_NUM_THREADS', '2')
os.environ.setdefault('MKL_NUM_THREADS', '2')
os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY', '1')


def timestamp():
    return time.strftime('%Y-%m-%dT%H:%M:%S%z')


def manifest_reports(folder, source):
    manifest = folder / 'manifest.json'
    if not manifest.exists():
        if any((folder / 'pages').glob('*.md')):
            raise RuntimeError(f'Unmanifested outputs require review: {source["id"]}')
        return {}
    old = json.loads(manifest.read_text(encoding='utf-8-sig'))
    if old.get('engineId') != ENGINE or old.get('sha256') != source['sha256']:
        raise RuntimeError(f'Archive previous engine outputs before conversion: {source["id"]}')
    return {str(p['page']): p for p in old.get('pages', [])
            if (folder / 'pages' / f'{p["page"]:04}.md').exists()
            and (folder / 'structure' / f'{p["page"]:04}.json').exists()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--collection', choices=['all', 'darkstryder', 'sourcebooks'], default='all')
    parser.add_argument('--source', help='Single stable source ID')
    parser.add_argument('--start-page', type=int, default=1)
    parser.add_argument('--limit-pages', type=int, default=0)
    parser.add_argument('--runtime-root', type=Path, help='Separate output root for smoke tests')
    parser.add_argument('--batch-pages', type=int, default=1)
    args = parser.parse_args()
    if args.batch_pages != 1 or args.start_page < 1 or args.limit_pages < 0:
        parser.error('Use one page per batch and non-negative limits')
    runtime = (args.runtime_root or Path(CONFIG['runtimeRoot'])).resolve()
    runtime.mkdir(parents=True, exist_ok=True)
    lock = (runtime / 'ocr.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError('Another OCR process owns this output directory')
    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()))
    lock.flush()
    sources = json.loads((ROOT / 'catalog/sources.json').read_text(encoding='utf-8-sig'))
    sources.sort(key=lambda s: (s['collection'] != 'darkstryder', 'WEG40209' not in s['name'], s['name']))
    selected = [s for s in sources if (args.collection == 'all' or s['collection'] == args.collection)
                and (not args.source or s['id'] == args.source)]
    if not selected:
        parser.error('No sources selected')
    reports_by_source = {s['id']: manifest_reports(runtime / s['output'], s) for s in selected}
    status = {'state': 'loading-model', 'engine': 'Docling + RapidOCR PyTorch CUDA / TableFormer accurate',
              'engineId': ENGINE, 'pid': os.getpid(), 'totalPages': sum(s['pages'] for s in selected),
              'completedPages': 0, 'sources': {}, 'startedAt': timestamp(), 'errors': []}
    for source in selected:
        count = len(reports_by_source[source['id']])
        status['sources'][source['id']] = {'name': source['name'], 'pages': source['pages'],
            'completedPages': count, 'state': 'complete' if count == source['pages'] else 'pending'}

    def refresh():
        for source in selected:
            status['sources'][source['id']]['completedPages'] = len(reports_by_source[source['id']])
        status['completedPages'] = sum(s['completedPages'] for s in status['sources'].values())
        status['updatedAt'] = timestamp()
        write_json(runtime / 'ocr-status.json', status)

    def stop(signum, _frame):
        raise KeyboardInterrupt(f'Signal {signum}')

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    refresh()
    processed = 0
    try:
        import torch
        import pymupdf
        from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions, TableFormerMode
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling_core.types.doc import ImageRefMode
        from docling.models.stages.ocr.rapid_ocr_model import RapidOcrModel
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA unavailable; no silent CPU fallback')
        torch.set_num_threads(2)
        torch.set_num_interop_threads(2)
        torch.cuda.set_per_process_memory_fraction(0.35)
        torch.backends.cudnn.benchmark = False
        status['gpu'] = torch.cuda.get_device_name(0)
        versions = {name: importlib.metadata.version(name)
                    for name in ['docling', 'docling-core', 'torch', 'rapidocr', 'transformers']}
        status['versions'] = versions
        refresh()
        model_root = Path(CONFIG['runtimeRoot']) / 'models' / 'rapidocr'
        model_root = RapidOcrModel.download_models(backend='torch', lang='english', local_dir=model_root)
        models = RapidOcrModel._models_by_language['english']['torch']
        model_paths = {name: str(model_root / value['path']) for name, value in models.items()}
        options = PdfPipelineOptions(
            artifacts_path=Path(CONFIG.get('doclingArtifactsRoot', Path.home() / '.cache/docling/models')),
            accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CUDA, num_threads=2),
            do_ocr=True, do_table_structure=True,
            generate_picture_images=True, generate_page_images=True, images_scale=2,
            ocr_batch_size=1, layout_batch_size=1, table_batch_size=1, queue_max_size=2,
            document_timeout=300, enable_remote_services=False,
            ocr_options=RapidOcrOptions(backend='torch', lang=['english'],
                                       force_full_page_ocr=False, text_score=0.4, **model_paths))
        options.table_structure_options.mode = TableFormerMode.ACCURATE
        logging.basicConfig(level=logging.WARNING)
        converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
        status['state'] = 'running'
        refresh()
        print(f'{status["engine"]} | {status["gpu"]} | {status["totalPages"]} pages; sequential checkpoints', flush=True)
        for source in selected:
            folder = runtime / source['output']
            pages_dir, images, structure = folder / 'pages', folder / 'images', folder / 'structure'
            for directory in [pages_dir, images, structure]:
                directory.mkdir(parents=True, exist_ok=True)
            locator = source['locator']
            base = Path(CONFIG['sourceRoot']) if locator['root'] == 'drive' else ROOT
            pdf = (base / locator['path']).resolve()
            if not pdf.is_relative_to(base.resolve()):
                raise RuntimeError('Source path escapes configured root')
            with pdf.open('rb') as stream:
                sha = hashlib.file_digest(stream, 'sha256').hexdigest()
            if sha != source['sha256']:
                raise RuntimeError('Source changed; rerun inventory: ' + pdf.name)
            reports = reports_by_source[source['id']]
            current = status['sources'][source['id']]
            status['currentSource'] = source['id']
            current['state'] = 'running'
            refresh()
            with pymupdf.open(pdf) as original:
                for number in range(args.start_page, source['pages'] + 1):
                    if str(number) in reports:
                        continue
                    status['currentPage'] = number
                    refresh()
                    available = next(int(line.split()[1]) * 1024 for line in Path('/proc/meminfo').read_text().splitlines()
                                     if line.startswith('MemAvailable:'))
                    if available < 4 * 1024 ** 3:
                        raise RuntimeError('Less than 4 GiB available host RAM; safe stop, resume later')
                    before = time.monotonic()
                    result = converter.convert(pdf, page_range=(number, number))
                    if result.status.value != 'success':
                        raise RuntimeError(f'Conversion incomplete: {source["id"]} p.{number}: {result.status}')
                    doc = result.document
                    # Structure and page directories have equal depth: ../images works in either.
                    md_temp = structure / f'{number:04}.md.tmp'
                    doc.save_as_markdown(md_temp, artifacts_dir=Path('../images'), image_mode=ImageRefMode.REFERENCED)
                    text = md_temp.read_text(encoding='utf-8')
                    md_temp.unlink()
                    json_temp = structure / f'{number:04}.json.tmp'
                    doc.save_as_json(json_temp, artifacts_dir=Path('../images'), image_mode=ImageRefMode.REFERENCED)
                    json_temp.replace(structure / f'{number:04}.json')
                    plain = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', text)
                    chars = len(' '.join(re.sub(r'<[^>]*>', ' ', plain).split()))
                    native_chars = len(original[number - 1].get_text().strip())
                    flags = []
                    if chars < 80:
                        flags.append('low-text-or-illustration')
                    if native_chars > 500 and chars < native_chars * 0.6:
                        flags.append('text-coverage-low')
                    if '\ufffd' in text:
                        flags.append('replacement-characters')
                    confidence = result.confidence.pages.get(number) if result.confidence and result.confidence.pages else None
                    if confidence and confidence.layout_score is not None and confidence.layout_score < 0.85:
                        flags.append('layout-low-confidence')
                    if confidence and confidence.ocr_score is not None and confidence.ocr_score < 0.85:
                        flags.append('ocr-low-confidence')
                    original[number - 1].get_pixmap(dpi=120, alpha=False).pil_save(pages_dir / f'{number:04}.jpg', format='JPEG', quality=85)
                    md_file = pages_dir / f'{number:04}.md'
                    temp = md_file.with_suffix('.tmp')
                    temp.write_text(f'<!-- source: {source["id"]}; pdf_page: {number}; engine: {ENGINE} -->\n\n' + text + '\n', encoding='utf-8')
                    temp.replace(md_file)
                    reports[str(number)] = {'page': number, 'characters': chars, 'nativeCharacters': native_chars,
                        'tables': len(doc.tables), 'images': len(doc.pictures), 'flags': flags,
                        'seconds': round(time.monotonic() - before, 2),
                        'confidence': confidence.model_dump(mode='json') if confidence else None}
                    write_json(folder / 'manifest.json', {'sourceId': source['id'], 'sha256': sha,
                        'engineId': ENGINE, 'engine': status['engine'], 'versions': versions,
                        'languages': ['english'], 'nativeTextPreserved': True, 'tableMode': 'accurate',
                        'pages': [reports[k] for k in sorted(reports, key=int)]})
                    processed += 1
                    status['gpuMemoryMiB'] = {'allocated': round(torch.cuda.memory_allocated() / 1024 ** 2),
                                             'reserved': round(torch.cuda.memory_reserved() / 1024 ** 2)}
                    status['lastPageSeconds'] = reports[str(number)]['seconds']
                    refresh()
                    if processed % 4 == 0 or number == source['pages']:
                        rebuild(folder, source)
                    print(f'{source["name"]} p.{number}/{source["pages"]}: {chars} chars, {len(doc.tables)} tables, {reports[str(number)]["seconds"]}s | {status["completedPages"]}/{status["totalPages"]}', flush=True)
                    del result, doc
                    gc.collect()
                    torch.cuda.empty_cache()
                    if args.limit_pages and processed >= args.limit_pages:
                        break
            rebuild(folder, source)
            current['state'] = 'complete' if len(reports) == source['pages'] else 'partial'
            refresh()
            if args.limit_pages and processed >= args.limit_pages:
                break
        status['state'] = 'complete' if status['completedPages'] == status['totalPages'] else 'partial'
        refresh()
    except KeyboardInterrupt as error:
        status['state'] = 'interrupted'
        status['error'] = str(error)
        refresh()
        print('Interrupted; committed pages will be reused on restart', flush=True)
    except BaseException as error:
        status['state'] = 'failed'
        status['error'] = str(error)
        refresh()
        raise


if __name__ == '__main__':
    main()
