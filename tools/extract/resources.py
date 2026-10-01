"""Preserve standalone local visual evidence, including orphaned offline-site images."""
import hashlib,json
from pathlib import Path
from PIL import Image
from common import write_json

ROOT=Path(__file__).resolve().parents[2]
def publish_resources():
    config=json.loads((ROOT/'config.local.json').read_text());runtime=Path(config['runtimeRoot']).resolve();drive=Path(config['sourceRoot']).resolve()
    report=json.loads((runtime/'corpus-inventory.json').read_text());out=ROOT/'content/transcriptions/_resources/images';out.mkdir(parents=True,exist_ok=True)
    if out.is_symlink() or not out.resolve().is_relative_to((ROOT/'content/transcriptions').resolve()):raise RuntimeError('Resource output outside project')
    records={};warnings=[]
    for entry in report['entries']:
        if entry['kind']!='media':continue
        loc=entry['locator'];base=drive if loc['root']=='drive' else runtime;original=(base/loc['path']).resolve()
        if not original.is_relative_to(base):raise RuntimeError('Resource input outside configured root')
        try:
            with Image.open(original) as picture:picture.verify();fmt=picture.format.lower()
            ext={'jpeg':'jpg','tiff':'tif'}.get(fmt,fmt);data=original.read_bytes();sha=hashlib.sha256(data).hexdigest()
            if sha!=entry['sha256']:raise RuntimeError('Resource changed since inventory')
            name=sha+'.'+ext;target=out/name
            if target.is_symlink():raise RuntimeError('Linked resource output')
            if target.exists():
                if hashlib.sha256(target.read_bytes()).hexdigest()!=sha:raise RuntimeError('Local resource edits preserved')
            else:target.write_bytes(data)
            record=records.setdefault(sha,{'sha256':sha,'file':'images/'+name,'origins':[]});record['origins'].append(loc)
        except (OSError,ValueError) as error:warnings.append({'locator':loc,'error':str(error)})
    write_json(out.parent/'resources.json',{'version':1,'provenance':'unreviewed-source-media','images':list(records.values()),'warnings':warnings})
    print(json.dumps({'uniqueSourceImages':len(records),'warnings':len(warnings)}))
if __name__=='__main__':publish_resources()
