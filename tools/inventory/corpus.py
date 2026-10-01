"""Explicit offline scope, stable hashes, and bounded archive inspection."""
import hashlib
import json
import stat
import zipfile
from collections import Counter
from pathlib import Path
import pymupdf
from openpyxl import load_workbook

ROOT=Path(__file__).resolve().parents[2]
def digest(path):
    with path.open('rb') as handle:return hashlib.file_digest(handle,'sha256').hexdigest()

def inventory(project=ROOT):
    config=json.loads((project/'config.local.json').read_text(encoding='utf-8-sig'))
    scope=json.loads((project/'config/corpus-scope.json').read_text())
    drive=Path(config['sourceRoot']).resolve();runtime=Path(config['runtimeRoot']).resolve()
    previous=json.loads((project/'catalog/sources.json').read_text()) if (project/'catalog/sources.json').exists() else []
    sources={s['sha256']:s for s in previous};entries=[];archives=[]
    supported=set(scope['documentExtensions']);media=set(scope['mediaExtensions'])
    def register(path,group,locator,origin=None):
        resolved=path.resolve();base=drive if locator['root']=='drive' else runtime
        if not resolved.is_relative_to(base):raise RuntimeError('Input outside configured root')
        sha=digest(path);suffix=path.suffix.lower();kind='document' if suffix in supported else 'media' if suffix in media else 'auxiliary'
        if suffix=='.php':
            with path.open('rb') as handle:
                if handle.read(3)==b'\xff\xd8\xff':kind='media'
        entries.append({'locator':locator,'sha256':sha,'bytes':path.stat().st_size,'kind':kind,'collection':group['collection'],**({'archive':origin} if origin else {})})
        if kind!='document':return
        if sha in sources:
            source=sources[sha]
            if locator!=source['locator'] and locator not in source.get('copies',[]):source.setdefault('copies',[]).append(locator)
            if group['collection'] not in source.setdefault('collections',[source['collection']]):source['collections'].append(group['collection'])
            return
        fmt='html' if suffix in ['.html','.htm'] else suffix[1:]
        pages=1;native=0
        if fmt=='pdf':
            with pymupdf.open(path) as doc:pages=len(doc);native=sum(len(p.get_text().strip())>100 for p in doc)
        elif fmt=='xlsx':
            workbook=load_workbook(path,read_only=True,data_only=False);pages=len(workbook.worksheets);workbook.close()
        source={'id':'src-'+sha[:16],'name':path.stem,'sha256':sha,'format':fmt,'pages':pages,
                'unitLabel':'page PDF' if fmt=='pdf' else 'feuille' if fmt=='xlsx' else 'segment documentaire',
                'locator':locator,'copies':[],'collection':group['collection'],'collections':[group['collection']],
                'evidenceProvenance':group['provenance'],'native_text_pages':native,'output':'extracted/src-'+sha[:16]}
        if origin:source['archive']=origin
        sources[sha]=source
    for group in scope['include']:
        folder=(drive/group['path']).resolve()
        if not folder.is_relative_to(drive):raise RuntimeError('Scope outside source root')
        if not folder.exists():raise RuntimeError('Missing scope: '+group['path'])
        for path in sorted(folder.rglob('*')):
            if not path.is_file():continue
            locator={'root':'drive','path':path.relative_to(drive).as_posix()};register(path,group,locator)
            if path.suffix.lower()!='.zip':continue
            archive_sha=digest(path);archive_folder=runtime/'imports/archives'/archive_sha
            with zipfile.ZipFile(path) as archive:
                members=archive.infolist()
                if sum(m.file_size for m in members)>100*1024*1024:raise RuntimeError('Archive exceeds 100 MiB limit')
                for member in members:
                    if member.is_dir():continue
                    target=(archive_folder/member.filename).resolve()
                    if not target.is_relative_to(archive_folder) or stat.S_ISLNK(member.external_attr>>16):raise RuntimeError('Unsafe archive member')
                    if member.file_size>32*1024*1024:raise RuntimeError('Archive member exceeds 32 MiB')
                    data=archive.read(member);sha=hashlib.sha256(data).hexdigest()
                    origin={'path':locator['path'],'member':member.filename,'archiveSha256':archive_sha}
                    archives.append({**origin,'sha256':sha,'bytes':len(data),'duplicateOf':sources.get(sha,{}).get('id')})
                    target.parent.mkdir(parents=True,exist_ok=True)
                    if target.exists():
                        if digest(target)!=sha:raise RuntimeError('Archive output changed locally')
                    else:target.write_bytes(data)
                    register(target,group,{'root':'runtime','path':target.relative_to(runtime).as_posix()},origin)
    ordered=list(sources.values())
    for source in ordered:source.setdefault('format','pdf');source.setdefault('unitLabel','page PDF')
    runtime.mkdir(parents=True,exist_ok=True)
    report={'version':1,'scope':scope,'entries':entries,'archives':archives,'sources':len(ordered),'units':sum(s['pages'] for s in ordered),'formats':dict(Counter(s['format'] for s in ordered))}
    (project/'catalog/sources.json').write_text(json.dumps(ordered,ensure_ascii=False,indent=2)+'\n')
    (runtime/'corpus-inventory.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({key:report[key] for key in ['sources','units','formats']}))

if __name__=='__main__':inventory()
