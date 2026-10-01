"""Audit physical publication without exposing book text in logs."""
import hashlib,json,re,sys
from pathlib import Path
from common import write_json
ROOT=Path(__file__).resolve().parents[2]
def digest(path):
    with path.open('rb') as handle:return hashlib.file_digest(handle,'sha256').hexdigest()
def main():
    config=json.loads((ROOT/'config.local.json').read_text());runtime=Path(config['runtimeRoot']);target=ROOT/'content/transcriptions';errors=[];warnings=[];units=0;links=0
    sources=json.loads((ROOT/'catalog/sources.json').read_text());pending=0
    scope=json.loads((ROOT/'config/corpus-scope.json').read_text());skipped={entry['id'] for entry in scope.get('skipSources',[])}
    for position,source in enumerate(sources,1):
        if source['id'] in skipped:continue
        folder=target/source['id'];checkpoint=runtime/source['output']
        if not (folder/'transcription.json').is_file():
            pending+=1
            if '--completed-only' not in sys.argv:errors.append(source['id']+': missing publication')
            continue
        meta=json.loads((folder/'transcription.json').read_text());manifest=json.loads((folder/'manifest.json').read_text())
        if meta['sha256']!=source['sha256']:errors.append(source['id']+': bad provenance')
        warnings.extend({'sourceId':source['id'],**w} for w in manifest.get('warnings',[]))
        for name,sha in meta['fileHashes'].items():
            path=folder/name
            if path.is_symlink() or not path.is_file() or digest(path)!=sha:errors.append(source['id']+'/'+name+': bad physical Markdown');continue
            text=path.read_text()
            for relative in re.findall(r'!\[[^\]]*\]\(([^)]+)\)',text):
                if relative.startswith(('https:','http:','data:')):continue
                resource=(path.parent/relative).resolve()
                if not resource.is_relative_to(target.resolve()) or not resource.is_file():errors.append(source['id']+'/'+name+': broken media link')
                links+=1
        for number in range(1,source['pages']+1):
            if not (folder/'pages'/f'{number:04}.md').is_file():errors.append(source['id']+': missing unit '+str(number))
            for name in [f'structure/{number:04}.json',*([f'pages/{number:04}.jpg'] if source.get('format','pdf')=='pdf' else [])]:
                path=folder/name
                if path.is_symlink() or not path.is_file() or digest(path)!=digest(checkpoint/name):errors.append(source['id']+'/'+name+': missing/changed scan or structure')
            units+=1
        if (folder/'images').is_symlink():errors.append(source['id']+': images still linked, not copied')
        for image in (checkpoint/'images').glob('*'):
            copied=folder/'images'/image.name
            if not copied.is_file() or copied.is_symlink() or digest(image)!=digest(copied):errors.append(source['id']+': image differs '+image.name)
        if position%25==0 or position==len(sources):print(json.dumps({'auditedEntries':position,'totalEntries':len(sources),'integrityErrors':len(errors)}),flush=True)
    resources=target/'_resources/resources.json'
    if resources.exists():
        for image in json.loads(resources.read_text())['images']:
            copied=resources.parent/image['file']
            if not copied.resolve().is_relative_to(target.resolve()) or copied.is_symlink() or not copied.is_file() or digest(copied)!=image['sha256']:errors.append('Standalone resource invalid: '+image['file'])
    excluded=[source['id'] for source in sources if source['id'] in skipped]
    result={'sources':len(sources),'publishedSources':len(sources)-pending-len(excluded),'pendingSources':pending,'skippedSources':excluded,'units':units,'verifiedImageLinks':links,'integrityErrors':errors,'conversionWarnings':warnings}
    write_json(runtime/'corpus-audit.json',result);print(json.dumps({key:len(value) if isinstance(value,list) else value for key,value in result.items()}))
    if errors:raise RuntimeError('Corpus integrity audit failed')
if __name__=='__main__':main()
