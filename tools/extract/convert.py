"""Local Chandra OCR 2 on CUDA: Markdown + images, resume per PDF page. No API."""
import argparse,hashlib,json,os,re,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
CONFIG=json.loads((ROOT/'config.local.json').read_text(encoding='utf8'))
RUNTIME=Path(CONFIG['runtimeRoot']);MODEL='datalab-to/chandra-ocr-2'
os.environ.setdefault('HF_HOME',str(Path.home()/'.cache/darkstryder/huggingface'))
os.environ.setdefault('HF_HUB_DISABLE_SYMLINKS_WARNING','1')
os.environ.setdefault('TORCH_DEVICE','cuda');os.environ.setdefault('TORCH_ATTN','sdpa')
os.environ.setdefault('OMP_NUM_THREADS','4')
from common import write_json,rebuild

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--collection',choices=['all','darkstryder','sourcebooks'],default='all')
    parser.add_argument('--limit-pages',type=int,default=0);parser.add_argument('--start-page',type=int,default=1)
    parser.add_argument('--max-tokens',type=int,default=12384);args=parser.parse_args()
    import torch,pymupdf
    from chandra.model import InferenceManager
    from chandra.model.schema import BatchInputItem
    if not torch.cuda.is_available():raise RuntimeError('CUDA unavailable; run tools/extract/install.ps1. CPU fallback disabled.')
    torch.set_num_threads(4)
    sources=json.loads((ROOT/'catalog/sources.json').read_text(encoding='utf8'))
    sources.sort(key=lambda s:(s['collection']!='darkstryder','WEG40209' not in s['name'],s['name']))
    sources=[s for s in sources if args.collection=='all' or s['collection']==args.collection]
    status={'state':'loading-model','engine':'Chandra OCR 2 / HuggingFace CUDA BF16 SDPA','model':MODEL,
            'gpu':torch.cuda.get_device_name(0),'totalPages':sum(s['pages'] for s in sources),'completedPages':0,'sources':{},'startedAt':time.strftime('%Y-%m-%dT%H:%M:%S')}
    def refresh():
        status['completedPages']=sum(len(list((RUNTIME/s['output']/'pages').glob('*.md'))) for s in sources)
        status['updatedAt']=time.strftime('%Y-%m-%dT%H:%M:%S');write_json(RUNTIME/'ocr-status.json',status)
    refresh();print('Loading',MODEL,'on',status['gpu'],flush=True);processed=0
    try:
        manager=InferenceManager(method='hf');assert next(manager.model.parameters()).is_cuda
        status['state']='running';refresh()
        for source in sources:
            folder=RUNTIME/source['output'];pages_dir=folder/'pages';images=folder/'images'
            for p in [pages_dir,images,folder/'structure']:p.mkdir(parents=True,exist_ok=True)
            loc=source['locator'];pdf=(Path(CONFIG['sourceRoot']) if loc['root']=='drive' else ROOT)/loc['path']
            with pdf.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
            if sha!=source['sha256']:raise RuntimeError('Source changed; rerun inventory: '+pdf.name)
            manifest=folder/'manifest.json';reports={}
            if manifest.exists():
                old=json.loads(manifest.read_text(encoding='utf8'))
                if old.get('model')!=MODEL:raise RuntimeError('Mixed OCR engines: archive previous outputs first')
                reports={str(p['page']):p for p in old.get('pages',[])}
            status['currentSource']=source['id'];status['sources'][source['id']]={'name':source['name'],'state':'running','pages':source['pages']};refresh()
            with pymupdf.open(pdf) as original:
                for number in range(args.start_page,source['pages']+1):
                    md_file=pages_dir/f'{number:04}.md'
                    if md_file.exists() and str(number) in reports:continue
                    status['currentPage']=number;refresh();before=time.monotonic()
                    image=original[number-1].get_pixmap(dpi=192,alpha=False).pil_image().convert('RGB')
                    with torch.inference_mode():
                        result=manager.generate([BatchInputItem(image=image,prompt_type='ocr_layout')],max_output_tokens=args.max_tokens,include_images=True,include_headers_footers=False)[0]
                    if result.error:raise RuntimeError(f'Generation failed: {source["id"]} p.{number}')
                    text=result.markdown
                    for name,picture in result.images.items():
                        picture.save(images/name,format='WEBP',quality=95)
                        text=text.replace(']('+name+')','](../images/'+name+')').replace('src="'+name+'"','src="../images/'+name+'"')
                    plain=re.sub(r'!\[[^\]]*\]\([^)]*\)','',text);plain=re.sub(r'<[^>]*>',' ',plain)
                    chars=len(' '.join(plain.split()));native_chars=len(original[number-1].get_text().strip());flags=[]
                    if result.token_count>=args.max_tokens:flags.append('possibly-truncated')
                    if chars<80:flags.append('low-text-or-illustration')
                    if native_chars>500 and chars<native_chars*.6:flags.append('text-coverage-low')
                    if '\ufffd' in text:flags.append('replacement-characters')
                    words=plain.split()
                    if len(words)>100 and len(set(words))/len(words)<.12:flags.append('possible-repetition')
                    image.thumbnail((1400,1800));image.save(pages_dir/f'{number:04}.jpg',quality=88)
                    write_json(folder/'structure'/f'{number:04}.json',{'sourceId':source['id'],'page':number,'model':MODEL,'chunks':result.chunks,'tokens':result.token_count,'flags':flags})
                    reports[str(number)]={'page':number,'characters':chars,'nativeCharacters':native_chars,'tokens':result.token_count,'images':list(result.images),'flags':flags,'seconds':round(time.monotonic()-before,2)}
                    temp=md_file.with_suffix('.tmp');temp.write_text(f'<!-- source: {source["id"]}; pdf_page: {number}; model: {MODEL} -->\n\n'+text+'\n',encoding='utf8');temp.replace(md_file)
                    write_json(manifest,{'sourceId':source['id'],'sha256':sha,'model':MODEL,'dpi':192,'pages':[reports[k] for k in sorted(reports,key=int)]})
                    processed+=1;refresh()
                    if processed%8==0 or number==source['pages']:rebuild(folder,source)
                    print(f'{source["name"]} p.{number}/{source["pages"]}: {chars} chars, {result.token_count} local tokens, {reports[str(number)]["seconds"]}s | {status["completedPages"]}/{status["totalPages"]}',flush=True)
                    if args.limit_pages and processed>=args.limit_pages:break
            rebuild(folder,source)
            status['sources'][source['id']]['state']='complete' if len(reports)==source['pages'] else 'partial';refresh()
            if args.limit_pages and processed>=args.limit_pages:break
        status['state']='complete' if status['completedPages']==status['totalPages'] else 'partial';refresh()
    except BaseException as error:
        status['state']='failed';status['error']=str(error);refresh();raise

if __name__=='__main__':
    if CONFIG.get('ocrEngine') == 'docling':
        if '--ocr-worker' in sys.argv:
            sys.argv.remove('--ocr-worker')
            from docling_backend import main as docling_main
            docling_main()
        else:
            from supervise import run
            sys.exit(run())
    else:
        main()
