import json, math

def write_json(path,data):
    def clean(value):
        if isinstance(value,float) and not math.isfinite(value):return None
        if isinstance(value,dict):return {k:clean(v) for k,v in value.items()}
        if isinstance(value,list):return [clean(v) for v in value]
        return value
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(clean(data),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8');temp.replace(path)

def rebuild(folder,source):
    index=[];parts=[f'# {source["name"]}\n\nSource: `{source["id"]}` · numérotation des pages du PDF.\n']
    for page in sorted((folder/'pages').glob('*.md')):
        text=page.read_text(encoding='utf8');number=int(page.stem)
        index.append({'page':number,'text':text});parts.append(f'\n## Page PDF {number}\n\n'+text.replace('../images/','images/'))
    book=folder/'book.md';temp=book.with_suffix('.tmp')
    temp.write_text('\n'.join(parts),encoding='utf8');temp.replace(book)
    write_json(folder/'search.json',index)
