"""Fetch pinned Linux benchmark wheels using Windows' network, without installing there."""
import concurrent.futures, hashlib, json, sys, urllib.request
from pathlib import Path
from packaging.requirements import Requirement
from packaging.tags import cpython_tags, compatible_tags
from packaging.utils import parse_wheel_filename
from packaging.version import Version

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
platforms=[f'manylinux_2_{v}_x86_64' for v in range(35,4,-1)]+['manylinux2014_x86_64','manylinux2010_x86_64','manylinux1_x86_64','linux_x86_64']
tags=set(cpython_tags((3,13),platforms=platforms))|set(compatible_tags((3,13),interpreter='cp313',platforms=platforms))
env={'implementation_name':'cpython','implementation_version':'3.13.0','os_name':'posix','platform_machine':'x86_64','platform_python_implementation':'CPython','platform_release':'','platform_system':'Linux','platform_version':'','python_full_version':'3.13.0','python_version':'3.13','sys_platform':'linux','extra':''}
queue=[Requirement(r) for r in ['torch==2.14.0','torchvision==0.29.0','transformers==5.17.0','safetensors==0.8.0','pillow==12.3.0','huggingface-hub==1.31.0','flash-linear-attention==0.5.2']]
chosen={};extras={}
cache=out/'metadata';cache.mkdir(exist_ok=True)
def metadata(url):
    dest=cache/(hashlib.sha256(url.encode()).hexdigest()+'.json')
    if dest.exists():return json.loads(dest.read_text())
    try:
        with urllib.request.urlopen(url,timeout=30) as r:value=json.load(r)
    except Exception:
        print('Metadata failed: '+url,flush=True);raise
    dest.write_text(json.dumps(value));return value
while queue:
    req=queue.pop(0);key=req.name.lower().replace('_','-');old=chosen.get(key)
    if old:
        if Version(old['info']['version']) not in req.specifier:raise ValueError('Dependency conflict '+str(req))
        if set(req.extras)<=extras[key]:continue
        meta=old
    else:
        pinned=next((s.version for s in req.specifier if s.operator=='==' and '*' not in s.version),None)
        url=f'https://pypi.org/pypi/{key}/'+(pinned+'/' if pinned else '')+'json'
        meta=metadata(url)
        if Version(meta['info']['version']) not in req.specifier:
            versions=list(req.specifier.filter(meta['releases']))
            version=max(versions,key=Version)
            meta=metadata(f'https://pypi.org/pypi/{key}/{version}/json')
        chosen[key]=meta;extras[key]=set()
        print('resolved '+key+'=='+meta['info']['version'],flush=True)
    extras[key].update(req.extras)
    for raw in meta['info'].get('requires_dist') or []:
        child=Requirement(raw)
        if child.marker is None or any(child.marker.evaluate({**env,'extra':x}) for x in ['']+list(extras[key])):queue.append(child)
jobs=[]
for key,meta in chosen.items():
    candidates=[]
    for f in meta['urls']:
        if not f['filename'].endswith('.whl'):continue
        try:fit=parse_wheel_filename(f['filename'])[3]&tags
        except:continue
        if fit:candidates.append(f)
    if not candidates:raise ValueError('No compatible wheel '+key)
    jobs.append(candidates[0])
(out/'manifest.json').write_text(json.dumps(jobs,indent=2))
print(f'{len(jobs)} wheels, {sum(j["size"] for j in jobs)/1e9:.2f} GB',flush=True)
def fetch(f):
    target=out/f['filename'];sha=f['digests']['sha256']
    if target.exists() and hashlib.file_digest(target.open('rb'),'sha256').hexdigest()==sha:return
    if f['size']>20*1024*1024:
        def part(bounds):
            a,b=bounds;piece=target.with_suffix(f'.range-{a}')
            if piece.exists() and piece.stat().st_size==b-a+1:return piece
            for attempt in range(4):
                try:
                    request=urllib.request.Request(f['url'],headers={'Range':f'bytes={a}-{b}'})
                    with urllib.request.urlopen(request,timeout=60) as r,piece.open('wb') as dst:
                        if r.status!=206 or r.headers.get('Content-Range','').split('/')[0]!=f'bytes {a}-{b}':raise ValueError('Range not honored')
                        while block:=r.read(1024*1024):dst.write(block)
                    if piece.stat().st_size!=b-a+1:raise ValueError('Incomplete range')
                    return piece
                except Exception:
                    if attempt==3:raise
        chunks=[(a,min(a+64*1024*1024,f['size'])-1) for a in range(0,f['size'],64*1024*1024)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as workers:pieces=list(workers.map(part,chunks))
        partial=target.with_suffix('.part')
        with partial.open('wb') as dst:
            for piece in pieces:
                with piece.open('rb') as src:
                    while block:=src.read(1024*1024):dst.write(block)
        if hashlib.file_digest(partial.open('rb'),'sha256').hexdigest()!=sha:raise ValueError('SHA mismatch')
        partial.replace(target);print('wheel '+target.name,flush=True);return
    for attempt in range(3):
        try:
            partial=target.with_suffix('.part')
            with urllib.request.urlopen(f['url'],timeout=60) as r,partial.open('wb') as dst:
                while b:=r.read(1024*1024):dst.write(b)
            if hashlib.file_digest(partial.open('rb'),'sha256').hexdigest()!=sha:raise ValueError('SHA mismatch')
            partial.replace(target);print('wheel '+target.name,flush=True);return
        except Exception:
            if attempt==2:raise
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(fetch,jobs))
