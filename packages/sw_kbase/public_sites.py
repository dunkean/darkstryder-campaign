"""Bounded collectors for public reference pages; no model or campaign writes."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
from html.parser import HTMLParser
import json
import re
from urllib.parse import urljoin, urlparse, parse_qs, urlencode
from urllib.robotparser import RobotFileParser

from .acquire import html_text
from .store import normalize
from .sections import category_kind


class OpenD6Cards(HTMLParser):
    """Locate exact original HTML slices of SSR reference cards, including tables."""
    def __init__(self, raw, classes=None):
        super().__init__(convert_charrefs=False)
        self.raw = raw
        self.lines = [0]
        self.lines.extend(match.end() for match in re.finditer('\n', raw))
        self.depth = 0
        self.cards = []
        self.start = None
        self.required_classes = set(classes or {'srd-no-break', 'content-box'})

    def absolute_offset(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if tag != 'div':
            return
        classes = dict(attrs).get('class', '').split()
        if self.depth:
            self.depth += 1
        elif self.required_classes <= set(classes):
            self.start = self.absolute_offset()
            self.depth = 1

    def handle_endtag(self, tag):
        if tag == 'div' and self.depth:
            self.depth -= 1
            if not self.depth:
                end = self.raw.index('>', self.absolute_offset()) + 1
                self.cards.append((self.start, end, self.raw[self.start:end]))
                self.start = None


def opend6_srd(snapshot):
    host = 'https://opend6.net'
    robots_data, _ = snapshot.fetch(host + '/robots.txt')
    robots = RobotFileParser()
    robots.parse(robots_data.decode('utf-8').splitlines())
    categories = {'skills': 'skill', 'advantages': 'rule', 'disadvantages': 'rule',
                  'special-abilities': 'rule', 'equipment': 'equipment_model',
                  'armor': 'equipment_model', 'weapons': 'equipment_model', 'powers': 'rule'}
    records, errors, counts = [], [], {}
    for category, kind in categories.items():
        url = host + '/srd/' + category
        if not robots.can_fetch('DarkStryder-local-reference', url):
            errors.append({'url': url, 'error': 'robots.txt disallows acquisition'})
            continue
        try:
            data, receipt = snapshot.fetch(url)
            raw = data.decode('utf-8-sig')
            if re.search(r'Page Not Found|Web Filter Violation|FortiGuard', raw, re.I):
                raise ValueError('Soft error or network-filter page; not reference content')
            parser = OpenD6Cards(raw)
            parser.feed(raw)
            if parser.depth or not parser.cards:
                raise ValueError('Expected complete OpenD6 reference cards were not found')
            occurrences = Counter()
            for index, (start, end, card) in enumerate(parser.cards):
                heading = re.search(r'<h[23]\b[^>]*>(.*?)</h[23]>', card, re.S | re.I)
                if not heading:
                    raise ValueError(f'Card {index} has no title')
                name = html_text(heading[1]).strip()
                text = html_text(card)
                source = re.search(r'Source:\s*([^\n]+)', text)
                edition = source[1].strip() if source else 'unspecified'
                base_key = '/'.join((category, normalize(edition), normalize(name)))
                occurrences[base_key] += 1
                key = base_key + ('/' + str(occurrences[base_key]) if occurrences[base_key] > 1 else '')
                fragment_sha = sha256(card.encode()).hexdigest()
                records.append({'key': key, 'name': name, 'body': '# ' + name + '\n\n' + text,
                    'kind': kind, 'categories': [category], 'url': url, 'format': 'html_card',
                    'universe': 'generic_d6', 'origin': 'open_d6_reference', 'receipt': receipt,
                    'unit': {'kind': 'html_card', 'index': index, 'offset_start': start,
                             'offset_end': end, 'offset_unit': 'characters', 'html_sha256': fragment_sha},
                    'payload': {'category': category, 'source_edition': edition, 'html': card,
                                'html_sha256': fragment_sha},
                    'scope': {'edition': edition, 'collection': 'OpenD6 SRD', 'section': 'reference'}})
            counts[category] = len(parser.cards)
        except Exception as exc:
            errors.append({'url': url, 'error': str(exc)})
    # Keep licensing evidence in the snapshot without pretending it is a game entity.
    for path in ['/srd', '/srd/ogl', '/ogl']:
        url = urljoin(host, path)
        if not robots.can_fetch('DarkStryder-local-reference', url):
            continue
        try:
            data, receipt = snapshot.fetch(url)
            raw = data.decode('utf-8-sig')
            main = re.search(r'<main\b[^>]*>(.*?)</main>', raw, re.S | re.I)
            text = html_text(main[1] if main else raw)
            if 'Page Not Found' in text:
                continue
            records.append({'key': path, 'name': 'OpenD6 ' + path.removeprefix('/'),
                'body': text, 'kind': 'mixed_document', 'categories': [], 'section': 'metadata',
                'url': url, 'format': 'html', 'universe': 'generic_d6',
                'origin': 'open_d6_reference', 'payload': {'index_or_license': True}, 'receipt': receipt})
        except Exception as exc:
            errors.append({'url': url, 'error': str(exc)})
    return snapshot.save('opend6-srd', records, status='complete_scope' if not errors else 'partial',
        category_counts=counts, errors=errors, scope='All eight public OpenD6.net SSR SRD card categories; generic D6, not Star Wars rules',
        tls_verified=True, robots_respected=True)


def html_main(raw):
    match = re.search(r'<main\b[^>]*>(.*?)</main>', raw, re.S | re.I)
    if not match:
        match = re.search(r'<article\b[^>]*>(.*?)</article>', raw, re.S | re.I)
    return match[1] if match else raw


def links(raw, base):
    class Anchors(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.hrefs=[]
        def handle_starttag(self,tag,attrs):
            value=dict(attrs).get('href')
            if tag=='a' and value:self.hrefs.append(value)
    parser=Anchors();parser.feed(raw)
    return {urljoin(base, value).split('#')[0] for value in parser.hrefs if not value.startswith(('#','mailto:','javascript:'))}


def resume_records(snapshot, name):
    path = snapshot.root/name/'records.jsonl'
    if not path.exists():
        return {}
    return {row['key']: row for row in (json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line)}


def rpggamer(snapshot):
    """Use the site's published D6 menus, never guess or enumerate numeric IDs."""
    source = 'rpggamer-d6'
    categories = {'Characters D6':'person', 'Creatures D6':'creature_type', 'Droids D6':'droid_model',
        'Equipment D6':'equipment_model','Planets D6':'location','Races D6':'species',
        'Starships D6':'craft_model','The Force D6':'force_power','Vehicles D6':'craft_model',
        'Weapons D6':'equipment_model','Supplements D6':'mixed_document'}
    entries, errors, category_counts = {}, [], {}
    for category, kind in categories.items():
        url = 'https://rpggamer.org/main.php?' + urlencode({'page':'menu.php','type':category})
        try:
            raw = snapshot.fetch(url)[0].decode('utf-8','replace')
            found = set()
            for target in links(raw, url):
                parsed = urlparse(target)
                query = parse_qs(parsed.query)
                native = (query.get('page') or [''])[0]
                if parsed.path != '/page.php' or not native.isdigit():
                    continue
                name = (query.get('name') or [native])[0]
                entry = entries.setdefault(native, {'name':name,'categories':[],'kind':kind})
                if category not in entry['categories']:
                    entry['categories'].append(category)
                found.add(native)
            category_counts[category] = len(found)
            if not found and category != 'Supplements D6':
                raise ValueError('No linked stat IDs found in the public menu')
        except Exception as exc:
            errors.append({'url':url,'error':str(exc)})
    if not entries:
        raise ValueError('No public D6 entry links discovered')
    records = {key:value for key,value in resume_records(snapshot,source).items() if key in entries}
    def detail(native):
        index = entries[native]
        url = 'https://rpggamer.org/page.php?' + urlencode({'page':native,'name':index['name']})
        try:
            data, receipt = snapshot.fetch(url)
        except RuntimeError as exc:
            if '403' not in str(exc):raise
            # The site's documented numeric-ID URL needs no decorative name.
            # Some legacy name query strings fail while that public URL works.
            url='https://rpggamer.org/page.php?'+urlencode({'page':native})
            data, receipt = snapshot.fetch(url)
        raw = data.decode('utf-8','replace')
        text = html_text(raw)
        start = text.find('Section of Site:')
        if start < 0:
            raise ValueError('Stat article marker missing; refusing menu/error content')
        end = text.find('Comments made about this Article!', start)
        body = text[start:end if end >= 0 else len(text)].strip()
        if len(body) < 60:
            raise ValueError('Stat article is unexpectedly short')
        name_field = re.search(r'^\s*(?:\|\s*)?Name:\s*(.+)$', body, re.M)
        name = name_field[1].strip() if name_field else index['name']
        metadata = {}
        for key in ['Section of Site','Belongs to Faction','Subtype','Era','Canon']:
            match = re.search(re.escape(key)+r':\s*([^|\n]+)', body)
            if match:
                metadata[key] = match[1].strip()
        foreign = re.search(r'star trek|warhammer|stargate|battlestar|babylon 5|farscape|firefly|robotech|zentraedi|last starfighter|doctor who|alien vs', metadata.get('Belongs to Faction',''), re.I)
        kind = index['kind']
        if kind == 'person' and re.search(r'template|archetype', metadata.get('Subtype',''), re.I):
            kind = 'archetype'
        return {'key':native,'name':name,'body':body,'aliases':[index['name']] if index['name']!=name else [],
            'kind':kind,'categories':index['categories'],'url':url,'format':'html_stat',
            'universe':'crossover' if foreign else 'star_wars','origin':'fan_conversion','receipt':receipt,
            'scope':{'section':'metadata' if kind=='mixed_document' else 'reference','edition':'D6',**metadata},
            'unit':{'kind':'rendered_stat_article','start_marker':'Section of Site:',
                    'end_marker':'Comments made about this Article!','text_sha256':sha256(body.encode()).hexdigest()},
            'payload':{'native_id':native,'index_name':index['name'],'metadata':metadata,'html':raw,
                       'markdown_sha256':sha256(body.encode()).hexdigest()}}
    pending = sorted(set(entries)-set(records),key=int)
    def save(status):
        return snapshot.save(source,(records[key] for key in sorted(records,key=int)),status=status,
            expected=len(entries),category_counts=category_counts,errors=errors,
            scope='All stat IDs linked from the eleven public D6 menus; D20 and other-system menus excluded')
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(detail,key):key for key in pending}
        for count,future in enumerate(as_completed(futures),1):
            key = futures[future]
            try:records[key] = future.result()
            except Exception as exc:errors.append({'native_id':key,'error':str(exc)})
            if count % 200 == 0:
                save('partial')
    return save('complete_scope' if not errors and len(records)==len(entries) else 'partial')


def rsc_values(raw):
    """Read split Flight records and byte-counted text, without executing scripts."""
    fragments = []
    for match in re.finditer(r'self\.__next_f\.push\((.*?)\)</script>', raw, re.S):
        try:
            fragment = json.loads(match[1])
        except ValueError:
            continue
        if len(fragment) < 2 or not isinstance(fragment[1], str):
            continue
        fragments.append(fragment[1])
    stream = ''.join(fragments).encode('utf-8')
    position, records = 0, {}
    while position < len(stream):
        header = re.match(rb'([0-9a-f]*):', stream[position:])
        if not header:
            raise ValueError('Malformed public Flight record boundary')
        native = header[1].decode('ascii')
        position += header.end()
        text_header = re.match(rb'T([0-9a-f]+),', stream[position:])
        if text_header:
            position += text_header.end()
            end = position + int(text_header[1], 16)
            if end > len(stream):
                raise ValueError('Incomplete public Flight text record')
            records[native] = stream[position:end].decode('utf-8')
            position = end
            continue
        end = stream.find(b'\n', position)
        if end < 0:
            raise ValueError('Incomplete public Flight JSON record')
        value = stream[position:end].decode('utf-8')
        position = end + 1
        if value.startswith(('[', '{', '"')):
            records[native] = json.loads(value)
    def resolve(value, trail=()):
        if isinstance(value, str) and re.fullmatch(r'\$[0-9a-f]+', value):
            key = value[1:]
            if key in records and key not in trail and len(trail) < 16:
                return resolve(records[key], trail + (key,))
        if isinstance(value, dict):
            return {key: resolve(child, trail) for key, child in value.items()}
        if isinstance(value, list):
            return [resolve(child, trail) for child in value]
        return value
    for native, value in records.items():
        yield native, resolve(value, (native,))


def objects(value, pointer=''):
    if isinstance(value, dict):
        yield pointer, value
        for key, child in value.items():
            yield from objects(child, pointer+'/'+str(key).replace('~','~0').replace('/','~1'))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from objects(child, pointer+'/'+str(index))


def d6db(snapshot):
    if 'd6db' not in getattr(snapshot, 'robots_overrides', set()):
        raise ValueError('D6DB explicitly disallows AI agents in robots.txt; an explicit user exception is required')
    source = 'd6db-compendium'
    host = 'https://d6db.com'
    root = host+'/compendium'
    root_raw = snapshot.fetch(root)[0].decode('utf-8')
    categories = sorted({urlparse(url).path.split('/')[2] for url in links(root_raw,root)
        if re.fullmatch(r'/compendium/[a-z0-9-]+',urlparse(url).path)})
    kind_map = {'armor':'equipment_model','creatures':'creature_type','droids':'droid_model',
        'droid-attachments':'equipment_model','equipment':'equipment_model','force-powers':'force_power',
        'house-rules':'rule','magick':'rule','npcs':'person','planets':'location','ships':'craft_model',
        'skills':'skill','species':'species','templates':'archetype','traits':'rule','vehicles':'craft_model',
        'weapons':'equipment_model','droid-pregen-templates':'archetype'}
    entries, errors, api_checks, visited, advertised = {}, [], [], set(), {}
    queue = [host+'/compendium/'+category for category in categories]
    while queue:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            data, receipt = snapshot.fetch(url)
            raw = data.decode('utf-8')
            if '/sign-in' in receipt['final_url']:
                raise ValueError('Public route redirects to sign-in; excluded')
            category = urlparse(url).path.split('/')[2]
            if url == host + '/compendium/' + category:
                subtitle = re.search(r'<h1\b[^>]*>.*?</h1>\s*<p\b[^>]*>(.*?)</p>', raw, re.S | re.I)
                count = re.match(r'([\d,]+)\s+', html_text(subtitle[1])) if subtitle else None
                if count:
                    advertised[category] = int(count[1].replace(',', ''))
            page_links = links(raw,url)
            for target in page_links:
                path = urlparse(target).path
                target_query=parse_qs(urlparse(target).query)
                if urlparse(target).netloc != 'd6db.com':
                    continue
                if path==urlparse(url).path and set(target_query)&{'page','type','attribute','degree','category','faction'}:
                    if target not in visited:
                        queue.append(target)
                elif not path.startswith('/compendium/'+category+'/'):
                    continue
                elif re.fullmatch(r'/compendium/[^/]+/[^/]+/[^/]+',path):
                    if target not in visited:
                        queue.append(target)
                elif re.fullmatch(r'/compendium/[^/]+/[^/]+',path):
                    entries.setdefault(path, {'url':host+path,'category':category})
            # Client-side grids render only their first 50 cards in HTML. Their
            # public Flight props contain the complete native entry list.
            for _, value in rsc_values(raw):
                for _, node in objects(value):
                    if isinstance(node.get('slug'), str) and node.get('name'):
                        slug = node['slug']
                        if not re.fullmatch(r'[a-zA-Z0-9_-]+', slug):
                            raise ValueError('Unexpected public entry slug')
                        path = '/compendium/' + category + '/' + slug
                        entries.setdefault(path, {'url':host+path,'category':category,'index':node})
            api_urls = set()
            for match in re.finditer(r'self\.__next_f\.push\((.*?)\)</script>',raw,re.S):
                try:fragment=json.loads(match[1])
                except ValueError:continue
                if len(fragment)>1 and isinstance(fragment[1],str):
                    api_urls.update(re.findall(r'/api/compendium/search\?[^"\\\s<>]*',fragment[1]))
            for api in sorted(api_urls):
                query = parse_qs(urlparse(api).query,keep_blank_values=True)
                params = {key:values[0] for key,values in query.items()}
                params.update(q='',page=1)
                api_base = host+urlparse(api).path
                first, _ = snapshot.json(api_base,params)
                if not isinstance(first,dict) or 'items' not in first or 'totalPages' not in first:
                    raise ValueError('Unexpected public search response')
                page_items = list(first['items'])
                for page in range(2,int(first['totalPages'])+1):
                    result,_=snapshot.json(api_base,{**params,'page':page})
                    if result.get('total')!=first.get('total'):
                        raise ValueError('Search total changed during acquisition; retry snapshot')
                    page_items.extend(result['items'])
                if len({value['slug'] for value in page_items})!=int(first['total']):
                    raise ValueError('Public search pagination count mismatch')
                api_checks.append({'url':api_base+'?'+urlencode(params),'expected':first['total'],'retrieved':len(page_items)})
                for value in page_items:
                    path = '/compendium/'+category+'/'+value['slug']
                    entries.setdefault(path, {'url':host+path,'category':category,'index':value})
        except Exception as exc:
            errors.append({'url':url,'error':str(exc)})
    if not entries:
        raise ValueError('No public compendium entries discovered')
    discovered = Counter(value['category'] for value in entries.values())
    for category, expected in advertised.items():
        if discovered[category] != expected:
            errors.append({'category':category,'error':'Category count mismatch',
                           'expected':expected,'discovered':discovered[category]})
    records = {key:value for key,value in resume_records(snapshot,source).items()
               if key in entries and value.get('payload',{}).get('flight_parser_version') == 2}
    def detail(key):
        entry = entries[key]
        data,receipt=snapshot.fetch(entry['url'])
        raw=data.decode('utf-8')
        if '/sign-in' in receipt['final_url']:
            raise ValueError('Entry requires authentication; excluded')
        main=html_main(raw)
        heading=re.search(r'<h1\b[^>]*>(.*?)</h1>',main,re.S|re.I)
        if not heading:
            raise ValueError('Public entry heading missing')
        name=html_text(heading[1]).strip()
        if name.lower() in {'not found','404','sign in','compendium'}:
            raise ValueError('Error/index/sign-in page is not a compendium entry')
        body=html_text(main)
        payload={'url':entry['url'],'html':main,'outgoing_urls':sorted(links(main,entry['url'])),
                 'flight_parser_version':2}
        units=[]
        for block,value in rsc_values(raw):
            for pointer,obj in objects(value):
                if obj.get('name') and isinstance(obj.get('data'),dict) and obj.get('type'):
                    payload.setdefault('source_data',obj['data'])
                    payload.setdefault('source_object',obj)
                    units.append({'flight_block':block,'json_pointer':pointer})
        if payload.get('source_data'):
            body += '\n\n## Structured data supplied by the public page\n\n'+json.dumps(payload['source_data'],ensure_ascii=False,indent=2)
        if len(body)<30:
            raise ValueError('Empty public entry')
        payload['markdown_sha256']=sha256(body.encode()).hexdigest()
        return {'key':key,'name':name,'body':body,'kind':kind_map.get(entry['category'],'unknown'),
            'categories':[entry['category']],'url':entry['url'],'format':'html_flight',
            'origin':'fan_compilation','universe':'star_wars','receipt':receipt,'payload':payload,
            'unit':{'kind':'main_article_and_public_flight_props','props':units,'text_sha256':payload['markdown_sha256']},
            'scope':{'section':'reference','collection':'D6DB public compendium','edition':'2E R&E / fan reference'}}
    def save(status):
        return snapshot.save(source,(records[key] for key in sorted(records)),status=status,
            expected=len(entries),category_counts=dict(Counter(v['category'] for v in entries.values())),
            advertised_category_counts=advertised,
            downloaded_by_category=dict(Counter(row['categories'][0] for row in records.values())),
            errors=errors,pagination_checks=api_checks,index_pages=len(visited),
            robots_override='Explicit user instruction to override robots.txt for public D6DB acquisition; no login or access-control bypass',
            scope='Public compendium entries discovered from all category/group indexes and their native search pagination')
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(detail,key):key for key in sorted(set(entries)-set(records))}
        for count,future in enumerate(as_completed(futures),1):
            key=futures[future]
            try:records[key]=future.result()
            except Exception as exc:errors.append({'url':entries[key]['url'],'error':str(exc)})
            if count%200==0:
                save('partial')
    return save('complete_scope' if not errors and len(records)==len(entries) else 'partial')


def seawolf(snapshot):
    source='seawolf-star-wars'
    host='https://www.theswden.net'
    api=host+'/wp-json/wp/v2/'
    categories,receipt=snapshot.json(api+'categories',{'per_page':100,'page':1})
    for page in range(2,int(receipt.get('pages') or 1)+1):
        batch,_=snapshot.json(api+'categories',{'per_page':100,'page':page})
        categories.extend(batch)
    category_map={value['id']:html_text(value['name']) for value in categories}
    category_parents={value['id']:value['parent'] for value in categories}
    campaign_roots={key for key,name in category_map.items() if name.casefold() in
                    {'dragon wars','blood of shadows','shadows of deceit','darkstryder','lords of the black sands'}}
    def category_lineage(keys):
        lineage=set(keys)
        for _ in range(len(categories)):
            parents={category_parents.get(key,0) for key in lineage}-{0}
            if parents<=lineage:
                break
            lineage.update(parents)
        return lineage
    selected={value['id'] for value in categories
              if re.search(r'star\s*wars|darkstryder|kathol|farstar',html_text(value['name']),re.I)}
    while True:
        expanded=selected|{value['id'] for value in categories if value['parent'] in selected}
        if expanded==selected:
            break
        selected=expanded
    if not selected:
        raise ValueError('No public Star Wars category found')
    fields='id,parent,slug,link,title,content,excerpt,date_gmt,modified_gmt,categories'
    params={'per_page':100,'page':1,'orderby':'id','order':'asc','_fields':fields,'categories':','.join(map(str,sorted(selected)))}
    first,receipt=snapshot.json(api+'posts',params)
    rows=[(value,receipt,'posts') for value in first]
    expected_posts=int(receipt.get('total') or len(first))
    for page in range(2,int(receipt.get('pages') or 1)+1):
        batch,proof=snapshot.json(api+'posts',{**params,'page':page})
        rows.extend((value,proof,'posts') for value in batch)
    if len({value['id'] for value,_,_ in rows})!=expected_posts:
        raise ValueError('Star Wars post count does not match the public API total')
    page_params={'per_page':100,'page':1,'orderby':'id','order':'asc','_fields':fields}
    first,receipt=snapshot.json(api+'pages',page_params)
    pages=[(value,receipt) for value in first]
    expected_pages=int(receipt.get('total') or len(first))
    for page in range(2,int(receipt.get('pages') or 1)+1):
        batch,proof=snapshot.json(api+'pages',{**page_params,'page':page})
        pages.extend((value,proof) for value in batch)
    if len(pages)!=expected_pages:
        raise ValueError('Public page hierarchy incomplete')
    page_ids={value['id'] for value,_ in pages if re.search(r'star-wars|darkstryder|kathol|farstar',value['slug'],re.I)}
    while True:
        expanded=page_ids|{value['id'] for value,_ in pages if value.get('parent') in page_ids}
        if expanded==page_ids:
            break
        page_ids=expanded
    rows.extend((value,proof,'pages') for value,proof in pages if value['id'] in page_ids)
    records=[]
    resources=set()
    for value,proof,endpoint in rows:
        raw=value.get('content',{}).get('rendered','')
        body=html_text(raw)
        name=html_text(value['title']['rendered'])
        hints=[category_map.get(key,str(key)) for key in value.get('categories',[])]
        lineage=category_lineage(value.get('categories',[]))
        edition_hints=' '.join(category_map.get(key,'') for key in lineage)+' '+value['link']
        editions=sorted(set(re.findall(r'\bd(?:6|20)\b',edition_hints,re.I)))
        campaign=bool(lineage & campaign_roots or
                      re.search(r'darkstryder|kathol|farstar',' '.join(hints+[name,value['link']]),re.I))
        kind=category_kind(hints)
        if campaign and re.search(r"ship['’]?s log|chronicle",' '.join(hints),re.I):
            kind='scenario'
        if kind=='unknown':
            kind='scenario' if campaign else 'mixed_document'
        outgoing=sorted(links(raw,value['link']))
        resources.update(target for target in outgoing if re.search(r'astrog|swatlas|bgci|\.mdb\b|\.xlsx?\b|\.csv\b',target,re.I))
        records.append({'key':str(value['id']),'name':name,'body':body,'categories':hints,'kind':kind,
            'url':value['link'],'format':'wordpress','receipt':proof,'payload':{**value,'outgoing_urls':outgoing},
            'universe':'star_wars','origin':'fan_adaptation' if campaign else 'fan_compilation',
            'scope':{'section':'scenario' if campaign else 'reference','source_role':'third_party_fan_adaptation' if campaign else 'third_party_reference',
                     'category_ids':value.get('categories',[]),'category_lineage_ids':sorted(lineage),
                     'campaign_category_roots':sorted(lineage & campaign_roots),'collection':'Seawolf Star Wars public material'},
            'unit':{'kind':'wordpress_object','endpoint':endpoint,'native_id':value['id'],'field':'content.rendered',
                    'html_sha256':sha256(raw.encode()).hexdigest(),'markdown_sha256':sha256(body.encode()).hexdigest()}})
        records[-1]['scope']['edition']='/'.join(editions) if editions else 'unspecified'
    return snapshot.save(source,records,status='complete_scope',expected_posts=expected_posts,
        selected_pages=len(page_ids),all_public_pages=expected_pages,
        selected_categories={str(key):category_map[key] for key in sorted(selected)},
        astrogation_resource_urls=sorted(resources),errors=[],
        scope='All posts in explicitly named Star Wars/DarkStryder categories and descendants; Star Wars page subtrees. Not a mirror of unrelated RPG settings.')


class AtlasRows(HTMLParser):
    """Retain exact nested planet/group rows, each with its public native ID."""
    def __init__(self, raw):
        super().__init__(convert_charrefs=False)
        self.raw, self.active, self.rows = raw, [], []
        self.lines=[0]+[match.end() for match in re.finditer('\n',raw)]
    def absolute_offset(self):
        line,column=self.getpos()
        return self.lines[line-1]+column
    def handle_starttag(self,tag,attrs):
        if tag!='tr':return
        for row in self.active:row['depth']+=1
        match=re.fullmatch(r'(planet|group)-row2-(\d+)',dict(attrs).get('id',''))
        if match:self.active.append({'type':match[1],'id':match[2],'start':self.absolute_offset(),'depth':1})
    def handle_endtag(self,tag):
        if tag!='tr':return
        end=self.raw.index('>',self.absolute_offset())+1
        for row in self.active:row['depth']-=1
        for row in self.active:
            if row['depth']==0:self.rows.append({**row,'end':end,'html':self.raw[row['start']:end]})
        self.active=[row for row in self.active if row['depth']]


def seawolf_atlas(snapshot):
    """Acquire public search results by the site's published region/name filters."""
    from html import unescape
    source='seawolf-atlas'
    url='https://theswden.net/bgci4/swatlas/atlas'
    raw=snapshot.fetch(url)[0].decode('utf-8')
    def choices(name):
        select=re.search(r'<select[^>]*name="'+name+r'"[^>]*>(.*?)</select>',raw,re.S)
        if not select:raise ValueError('Public atlas search choices missing')
        return {unescape(value):html_text(label) for value,label in re.findall(r'<option[^>]*value="([^"]*)"[^>]*>(.*?)</option>',select[1],re.S) if value not in {'-','-1'}}
    regions,names=choices('p_region'),choices('p_planetName')
    records,errors,seen_names={},{},set()
    def retrieve(criterion,value):
        data,receipt=snapshot.atlas_search({'swas_options':criterion,'criterion':criterion,'value':value})
        page=data.decode('utf-8')
        if 'Search Star Wars Astrogation Database' not in html_text(page):
            raise ValueError('Public atlas search returned an error page')
        parser=AtlasRows(page);parser.feed(page)
        if parser.active:raise ValueError('Incomplete atlas result row')
        for row in parser.rows:
            fragment=row['html']
            heading=re.search(r'<h4\b[^>]*class="card-title"[^>]*>(.*?)</h4>',fragment,re.S)
            if not heading:raise ValueError('Atlas result title missing')
            name=html_text(heading[1])
            if row['type']=='planet':seen_names.add(name)
            key=row['type']+'/'+row['id']
            kind='location'
            group_type=re.search(r'<strong>Group Type:\s*</strong>\s*(.*?)</p>',fragment,re.S)
            if group_type:
                group=html_text(group_type[1])
                kind='species' if 'alien race' in group.lower() else category_kind([group])
                if kind=='unknown':kind='mixed_document'
            body=html_text(fragment)
            if key in records:
                if records[key]['body']!=body:
                    errors[key]='Native row content differs between public search results'
                continue
            records[key]={'key':key,'name':name,'body':body,'kind':kind,'categories':['Astrogation',row['type']],
                'url':receipt['final_url']+'#'+row['type']+'-row2-'+row['id'],'format':'html_row',
                'origin':'fan_compilation','universe':'star_wars','receipt':receipt,
                'scope':{'section':'reference','edition':'Star Wars D6 / lore compilation','collection':'Seawolf Star Wars Astrogation'},
                'payload':{'html':fragment,'native_type':row['type'],'native_id':row['id'],
                           'contains_native_keys':['group/'+native for native in re.findall(r'id="group-row2-(\d+)"',fragment)] if row['type']=='planet' else []},
                'unit':{'kind':'html_row','native_id':row['type']+'-row2-'+row['id'],
                        'offset_start':row['start'],'offset_end':row['end'],'offset_unit':'characters',
                        'html_sha256':sha256(fragment.encode()).hexdigest(),'markdown_sha256':sha256(body.encode()).hexdigest()}}
    for region in sorted(regions):
        try:retrieve('p_region',region)
        except Exception as exc:errors['region/'+region]=str(exc)
    missing=set(names.values())-seen_names-{'[Unknown]'}
    for value,name in sorted(names.items()):
        if name not in missing:continue
        try:retrieve('p_planetName',value)
        except Exception as exc:errors['name/'+value]=str(exc)
    missing=sorted(set(names.values())-seen_names-{'[Unknown]'})
    return snapshot.save(source,(records[key] for key in sorted(records)),
        status='complete_scope' if not errors and not missing else 'partial',
        errors=errors,missing_published_names=missing,regions=len(regions),published_planet_names=len(names)-1,
        category_counts=dict(Counter(row['payload']['native_type'] for row in records.values())),
        scope='All planet names published in the public atlas search choices, plus attached public group cards; source fields preserved without inventing coordinates')


def d6db_library(snapshot):
    """Keep all publicly linked library units, with book/website-unit provenance."""
    if 'd6db' not in getattr(snapshot,'robots_overrides',set()):
        raise ValueError('The explicit D6DB robots exception is required')
    host='https://d6db.com'
    source='d6db-library'
    data,_=snapshot.fetch(host+'/library');raw=data.decode('utf-8')
    books={}
    category='unspecified'
    for match in re.finditer(r'<h2\b[^>]*>(.*?)</h2>|<a\b[^>]*href="(/library/[^"/#?]+)"[^>]*>(.*?)</a>',raw,re.S|re.I):
        if match[1] is not None:
            category=re.sub(r'\s+\d+$','',html_text(match[1])).strip()
        else:
            slug=match[2].split('/')[-1]
            title=html_text(match[3]).removeprefix('NO COVER').strip()
            books[slug]={'title':title,'category':category}
    if not books:raise ValueError('Public library book index missing')
    expected=re.search(r'([\d,]+)\s+source books and supplements',html_text(raw))
    if expected and int(expected[1].replace(',',''))!=len(books):
        raise ValueError('Public book index count mismatch')
    records={key:row for key,row in resume_records(snapshot,source).items() if row.get('format')=='html_library_unit'}
    errors=[];targets={};page_checks={}
    def book_index(slug):
        url=host+'/library/'+slug
        data,proof=snapshot.fetch(url);page=data.decode('utf-8')
        if '/sign-in' in proof['final_url']:raise ValueError('Library book requires sign-in')
        children=sorted(target for target in links(page,url)
                        if re.fullmatch('/library/'+re.escape(slug)+r'/[^/]+',urlparse(target).path))
        if not children:
            inline=OpenD6Cards(page,{'px-5','py-5'});inline.feed(page)
            if inline.depth or len(inline.cards)!=1 or not re.search(r'<h[123]\b[^>]*\bid="',inline.cards[0][2]):
                raise ValueError('No public reading units or complete inline book found')
            children=[url]
        return slug,children,proof,page
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures={pool.submit(book_index,slug):slug for slug in books}
        for future in as_completed(futures):
            try:
                slug,children,proof,page=future.result()
                for child in children:targets[child]=slug
                key='/library/'+slug+'#index'
                index_text=books[slug]['title']+'\n\n'+books[slug]['category']
                records[key]={'key':key,'name':books[slug]['title'],'body':index_text,
                    'kind':'mixed_document','categories':[books[slug]['category']],'section':'metadata',
                    'format':'html','url':host+'/library/'+slug,'receipt':proof,'universe':'star_wars','origin':'fan_compilation',
                    'scope':{'section':'metadata','collection':books[slug]['title'],'source_role':'library_book_index'},
                    'payload':{**books[slug],'outgoing_urls':children},
                    'unit':{'kind':'library_book_index','slug':slug,'markdown_sha256':sha256(index_text.encode()).hexdigest()}}
            except Exception as exc:errors.append({'stage':'book_index','book':futures[future],'error':str(exc)})
    def detail(url):
        slug=targets[url];book=books[slug]
        data,proof=snapshot.fetch(url);page=data.decode('utf-8')
        if '/sign-in' in proof['final_url']:raise ValueError('Library reading unit requires sign-in')
        parser=OpenD6Cards(page,{'px-5','py-5'});parser.feed(page)
        if parser.depth or len(parser.cards)!=1:raise ValueError('Expected exactly one complete library reading fragment')
        start,end,fragment=parser.cards[0]
        heading=re.search(r'<h1\b[^>]*>(.*?)</h1>',fragment,re.S|re.I)
        if not heading:
            heading=re.search(r'<h([123])\b[^>]*\bid="[^"]+"[^>]*>(.*?)</h\1>',fragment,re.S|re.I)
        if not heading:raise ValueError('Public reading unit heading missing')
        chapter=html_text(heading[2] if heading.lastindex==2 else heading[1]);body=html_text(fragment)
        if len(body)<30:raise ValueError('Empty library reading unit')
        page_number=re.search(r'Page\s*(\d+)\s*of\s*(\d+)',html_text(page))
        public_unit={'index':int(page_number[1]),'total':int(page_number[2])} if page_number else {}
        scenario=bool(book['category'].casefold()=='adventures'
                      or re.search(r'darkstryder|^classic campaigns$',book['title'],re.I))
        anthology=book['category'].casefold()=='adventure journals'
        return {'key':urlparse(url).path,'name':book['title']+' — '+chapter,'body':body,
            'kind':'scenario' if scenario else 'mixed_document','categories':[book['category']],
            'format':'html_library_unit','url':url,'receipt':proof,'universe':'star_wars','origin':'fan_compilation',
            'scope':{'section':'source' if anthology else 'scenario' if scenario else 'reference','collection':book['title'],
                     'source_role':'public_library_transcription','edition':'as published by D6DB; not independently verified',
                     'library_category':book['category'],'partition_requires_review':anthology},
            'payload':{'book_slug':slug,'book_title':book['title'],'unit_title':chapter,'html':fragment,
                       'outgoing_urls':sorted(links(fragment,url)),'website_unit':public_unit,
                       'navigation_urls':sorted(target for target in links(page,url)
                                                if re.fullmatch('/library/'+re.escape(slug)+r'/[^/]+',urlparse(target).path))},
            'unit':{'kind':'library_website_unit','book_slug':slug,'website_unit':public_unit,
                    'offset_start':start,'offset_end':end,'offset_unit':'characters',
                    'html_sha256':sha256(fragment.encode()).hexdigest(),'markdown_sha256':sha256(body.encode()).hexdigest(),
                    'not_original_pdf_pagination':True}}
    def save(status):
        return snapshot.save(source,(records[key] for key in sorted(records)),status=status,
            expected_books=len(books),expected_reading_units=len(targets),
            reading_units=sum(row['format']=='html_library_unit' for row in records.values()),
            errors=errors,page_checks=page_checks,
            robots_override='Explicit user exception for public D6DB only; no authenticated routes or access-control bypass',
            scope='All books linked by the public library index and all reading URLs linked by their contents; website units, not guessed PDF pages')
    # The book TOC can omit reading units which have no chapter anchor. Walk
    # the website's own previous/next links too, until its unit count closes.
    attempted=set()
    count=0
    while True:
        for record in list(records.values()):
            for target in record.get('payload',{}).get('navigation_urls',[]):
                targets.setdefault(target,record['payload']['book_slug'])
        pending=[url for url in targets if url not in attempted and
                 (urlparse(url).path not in records or 'navigation_urls' not in records[urlparse(url).path].get('payload',{}))]
        if not pending:break
        attempted.update(pending)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures={pool.submit(detail,url):url for url in pending}
            for future in as_completed(futures):
                try:
                    record=future.result();records[record['key']]=record
                except Exception as exc:errors.append({'url':futures[future],'error':str(exc)})
                count+=1
                if count%50==0:save('partial')
    for slug in books:
        units=[row for row in records.values() if row.get('payload',{}).get('book_slug')==slug]
        totals={row['payload']['website_unit'].get('total') for row in units}-{None}
        indexes={row['payload']['website_unit'].get('index') for row in units}-{None}
        page_checks[slug]={'retrieved':len(units),'advertised_totals':sorted(totals),'indexes':sorted(indexes)}
        if len(totals)>1 or totals and indexes!=set(range(1,next(iter(totals))+1)):
            errors.append({'book':slug,'error':'Public library website-unit count mismatch',**page_checks[slug]})
    return save('complete_scope' if not errors else 'partial')
