"""Fetch linked public Seawolf files, then extract native PDF text offline."""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlparse, unquote

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'packages'))
from sw_kbase.acquire import Snapshot


def download(output, articles, proxy):
    snapshot=Snapshot(output,proxy=proxy,min_interval=.5)
    documents={}
    for line in articles.open(encoding='utf-8'):
        row=json.loads(line)
        for target in row.get('payload',{}).get('outgoing_urls',[]):
            parsed=urlparse(target)
            if not re.search(r'\.pdf$',parsed.path,re.I):continue
            if parsed.hostname not in {'seawolfsden.net','www.seawolfsden.net','theswden.net','www.theswden.net'}:continue
            # The current site's /files/ serves its legacy attachments unchanged.
            path=parsed.path.replace('/filles/','/files/',1)
            url='https://www.theswden.net'+path
            doc=documents.setdefault(url,{'url':url,'legacy_urls':[],'parents':[]})
            doc['legacy_urls'].append(target)
            doc['parents'].append({'key':row['key'],'name':row['name'],'scope':row.get('scope',{})})
    errors=[]
    for url,document in sorted(documents.items()):
        try:
            data,receipt=snapshot.fetch(url)
            if not data.startswith(b'%PDF-'):raise ValueError('Response is not a PDF')
            document['receipt']=receipt
        except Exception as exc:errors.append({'url':url,'error':str(exc)})
    report={'expected_files':len(documents),'documents':list(documents.values()),'errors':errors,'model_calls':0}
    (output/'seawolf-files-downloads.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'expected_files':len(documents),'downloaded':sum('receipt' in d for d in documents.values()),'errors':errors}),flush=True)


def extract(output):
    from pypdf import PdfReader
    report=json.loads((output/'seawolf-files-downloads.json').read_text(encoding='utf-8'))
    snapshot=Snapshot(output);records=[];errors=list(report['errors']);low_text=[];files=[]
    for document in report['documents']:
        if 'receipt' not in document:continue
        receipt=document['receipt'];path=output/receipt['path'];data=path.read_bytes()
        if sha256(data).hexdigest()!=receipt['sha256']:raise ValueError('PDF response hash mismatch')
        try:
            reader=PdfReader(path)
            if reader.is_encrypted:raise ValueError('Encrypted PDF; excluded')
            filename=unquote(urlparse(document['url']).path.rsplit('/',1)[-1])
            title=filename.removesuffix('.pdf')
            campaign=any(parent['scope'].get('section')=='scenario' for parent in document['parents'])
            texts=[page.extract_text() or '' for page in reader.pages]
            explicit_d20=bool(re.search(r'\bd20\b','\n'.join(texts[:3]),re.I))
            files.append({'url':document['url'],'pages':len(texts),'text_pages':sum(bool(t.strip()) for t in texts)})
            for index,body in enumerate(texts,1):
                if len(body.strip())<30:low_text.append({'url':document['url'],'page':index,'characters':len(body.strip())})
                page_sha=sha256(body.encode()).hexdigest()
                records.append({'key':urlparse(document['url']).path+':'+str(index),
                    'name':title+' — page '+str(index),'body':body,'kind':'scenario' if campaign else 'mixed_document',
                    'categories':['Seawolf attachments'],'url':document['url']+'#page='+str(index),'format':'pdf_native_text',
                    'universe':'star_wars','origin':'fan_adaptation' if campaign else 'fan_compilation','receipt':receipt,
                    'scope':{'section':'scenario' if campaign else 'source','collection':title,
                             'edition':'d20 (explicit PDF mention)' if explicit_d20 else 'unspecified; parent edition hints retained',
                             'source_role':'third_party_attachment','review_status':'unreviewed'},
                    'payload':{'file_name':filename,'legacy_urls':document['legacy_urls'],'parent_articles':document['parents'],
                               'pdf_sha256':receipt['sha256'],'extractor':'pypdf native text; no OCR/model'},
                    'unit':{'kind':'pdf_page','page':index,'total_pages':len(texts),'markdown_sha256':page_sha,
                            'page_index_base':1,'low_native_text':len(body.strip())<30}})
        except Exception as exc:errors.append({'url':document['url'],'error':str(exc)})
    return snapshot.save('seawolf-files',records,status='complete_scope' if not errors and not low_text else 'partial',
        expected_files=report['expected_files'],downloaded_files=len(files),files=files,errors=errors,
        low_native_text_pages=low_text,ocr_started=False,
        scope='All PDF attachments linked by the selected public Star Wars articles; native text by exact PDF page; low-text pages remain explicit')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--articles',type=Path)
    parser.add_argument('--proxy')
    parser.add_argument('--extract',action='store_true')
    args=parser.parse_args()
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    if args.extract:extract(args.output)
    elif args.articles and args.proxy:download(args.output,args.articles,args.proxy)
    else:parser.error('Download requires --articles and the dedicated --proxy; extraction requires --extract')
