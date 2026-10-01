import json, math

NATIVE_TEXT_MARKER = '<!-- native-text-supplement: pdf-text-layer; uncorrected -->'

def append_native_text(text, native_text):
    """Keep evidence discarded by layout detection, without editorial correction."""
    if NATIVE_TEXT_MARKER in text:
        return text
    native_text = '\n'.join(line.rstrip() for line in native_text.strip().splitlines())
    return (text.rstrip() + '\n\n' + NATIVE_TEXT_MARKER + '\n\n'
            '### Couche texte native du PDF — non corrigée\n\n'
            'Complément conservé car la détection de mise en page a omis du texte. '
            'Cette couche peut contenir des erreurs OCR historiques ; vérifier le scan. '
            'Elle ne figure pas dans la structure Docling et peut répéter le texte ci-dessus.\n\n'
            + native_text.strip() + '\n')

def write_json(path,data):
    def clean(value):
        if isinstance(value,float) and not math.isfinite(value):return None
        if isinstance(value,dict):return {k:clean(v) for k,v in value.items()}
        if isinstance(value,list):return [clean(v) for v in value]
        return value
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(clean(data),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8');temp.replace(path)

def rebuild(folder,source):
    label=source.get('unitLabel','page PDF')
    index=[];parts=[f'# {source["name"]}\n\nSource: `{source["id"]}` · unités : {label}.\n']
    for page in sorted((folder/'pages').glob('*.md')):
        text=page.read_text(encoding='utf8');number=int(page.stem)
        index.append({'page':number,'text':text});parts.append(f'\n## {label.capitalize()} {number}\n\n'+text.replace('../images/','images/'))
    book=folder/'book.md';temp=book.with_suffix('.tmp')
    temp.write_text('\n'.join(parts),encoding='utf8');temp.replace(book)
    write_json(folder/'search.json',index)
