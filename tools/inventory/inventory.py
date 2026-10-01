"""Catalog originals without copying them into Git. Content IDs survive renames."""
import hashlib
import json
from pathlib import Path
import pymupdf

ROOT=Path(__file__).resolve().parents[2]
CONFIG=json.loads((ROOT/'config.local.json').read_text(encoding='utf-8'))
DRIVE=Path(CONFIG['sourceRoot'])

def main():
    sources=[]
    selected=[DRIVE/'Material/DarkStryder SourceBook',DRIVE/'Material/SourceBooks',ROOT/'sourcebooks']
    for folder in selected:
        if not folder.exists(): continue
        for path in sorted(folder.rglob('*.pdf')):
            sha=hashlib.file_digest(path.open('rb'),'sha256').hexdigest()
            existing=next((s for s in sources if s['sha256']==sha),None)
            locator={'root':'drive' if path.is_relative_to(DRIVE) else 'project',
                     'path':path.relative_to(DRIVE if path.is_relative_to(DRIVE) else ROOT).as_posix()}
            if existing:
                existing['copies'].append(locator)
                continue
            with pymupdf.open(path) as doc:
                pages=len(doc)
                native=[len(p.get_text().strip()) for p in doc]
            sources.append({'id':'src-'+sha[:16],'name':path.stem,'sha256':sha,
                            'pages':pages,'locator':locator,'copies':[],
                            'collection':'darkstryder' if 'DarkStryder SourceBook' in str(folder) or folder==ROOT/'sourcebooks' else 'sourcebooks',
                            'native_text_pages':sum(n>100 for n in native),
                            'output':'extracted/src-'+sha[:16]})
    (ROOT/'catalog/sources.json').write_text(json.dumps(sources,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    inventory=[{'path':p.relative_to(DRIVE).as_posix(),'bytes':p.stat().st_size,'extension':p.suffix.lower()} for p in DRIVE.rglob('*') if p.is_file()]
    (ROOT/'catalog/inventory.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'sources':len(sources),'pages':sum(s['pages'] for s in sources),'files':len(inventory),'native_text_pages':sum(s['native_text_pages'] for s in sources)}))

if __name__=='__main__':
    from corpus import inventory
    inventory()
