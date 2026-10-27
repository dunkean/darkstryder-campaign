"""Resumable public API snapshots. No inference, authentication or executable downloads."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

USER_AGENT = 'DarkStryder-local-reference/1.2 (user-directed personal archive; OpenAI-assisted)'


class HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []; self.links = []; self.skip = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {'script', 'style', 'noscript', 'nav'}: self.skip += 1
        if self.skip: return
        if tag in {'p','div','br','li','tr','h1','h2','h3','h4','section','article'}: self.parts.append('\n')
        if tag == 'a' and attrs.get('href'): self.links.append(attrs['href'])
        if tag in {'td','th'}: self.parts.append(' | ')

    def handle_endtag(self, tag):
        if tag in {'script','style','noscript','nav'} and self.skip: self.skip -= 1
        if not self.skip and tag in {'p','div','li','tr','h1','h2','h3','h4','section','article'}: self.parts.append('\n')

    def handle_data(self, data):
        if not self.skip: self.parts.append(data)

    def text(self):
        return '\n'.join(line.strip() for line in re.sub(r'\n[ \t]*\n+', '\n\n', ''.join(self.parts)).splitlines()).strip()


def html_text(value):
    p = HTMLText(); p.feed(value or ''); return p.text()


class Snapshot:
    def __init__(self, root, proxy=None, min_interval=0):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)
        (self.root/'responses').mkdir(exist_ok=True)
        self.lock = threading.Lock(); self.receipts = {}
        self.min_interval = float(min_interval)
        self.pace_lock = threading.Lock()
        self.next_request = {}
        path = self.root/'manifest.jsonl'
        if path.exists():
            for line in path.read_text(encoding='utf-8').splitlines():
                try:
                    entry = json.loads(line)
                    if entry.get('status') == 200 and entry.get('path') and not entry.get('filtered'):
                        self.receipts[entry['url']] = entry
                except (ValueError, KeyError): pass
        self.proxy_session = None
        if proxy:
            parsed = urlparse(proxy)
            if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1','localhost'}
                    or not parsed.port or parsed.username or parsed.password or parsed.path not in {'','/'}
                    or parsed.query or parsed.fragment):
                raise ValueError('Dedicated acquisition proxy must be an unauthenticated loopback HTTP URL')
            import requests
            self.proxy_session = requests.Session()
            self.proxy_session.trust_env = False
            self.proxy_session.proxies = {'http': proxy, 'https': proxy}

    def proxied(self, url):
        # Dedicated public-site session: never use ambient credentials, cookies or proxies.
        allowed = {'rpggamer.org','theswden.net','rancorpit.com','d6db.com','opend6.net'}
        current = url
        for _ in range(6):
            parsed = urlparse(current)
            if (parsed.scheme != 'https' or parsed.username or parsed.password
                    or not any(parsed.hostname == host or (parsed.hostname or '').endswith('.'+host) for host in allowed)):
                raise ValueError('Proxy URL outside the authorized public-source hosts')
            response = self.proxy_session.get(current, timeout=(10,45), verify=True,
                allow_redirects=False, stream=True,
                headers={'User-Agent':USER_AGENT,
                         'Accept':'application/json,text/html,*/*'})
            with response:
                if response.status_code in {301,302,303,307,308}:
                    current = urljoin(current, response.headers['Location'])
                    continue
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > 100_000_000:
                        raise ValueError('Public response exceeds 100 MB bound')
                    chunks.append(chunk)
                return b''.join(chunks), current, response.status_code, {key.lower():value for key,value in response.headers.items()}
        raise ValueError('Public source exceeded redirect limit')

    def fetch(self, url):
        cached = self.receipts.get(url)
        if cached:
            path = self.root/cached['path']
            if path.is_file():
                data = path.read_bytes()
                if sha256(data).hexdigest() == cached['sha256']: return data, cached
        error = None
        for attempt in range(3):
            try:
                if self.min_interval:
                    host = urlparse(url).hostname
                    with self.pace_lock:
                        now = time.monotonic()
                        scheduled = max(now, self.next_request.get(host, now))
                        self.next_request[host] = scheduled + self.min_interval
                    time.sleep(max(0, scheduled - time.monotonic()))
                request = Request(url, headers={'User-Agent':USER_AGENT, 'Accept':'application/json,text/html,*/*'})
                if self.proxy_session:
                    data, final_url, status, headers = self.proxied(url)
                else:
                    with urlopen(request, timeout=60) as response:
                        data = response.read(30_000_001)
                        if len(data) > 30_000_000: raise ValueError('Response exceeds 30 MB bound')
                        final_url, status, headers = response.url, response.status, response.headers
                if b'FortiGuard Web Filtering' in data or b'Web Filter Violation' in data: raise ValueError('Organizational network filter response')
                entry = {'url':url,'final_url':final_url,'status':status,
                             'content_type':headers.get('Content-Type', headers.get('content-type','')),
                             'total':headers.get('X-WP-Total', headers.get('x-wp-total')), 'pages':headers.get('X-WP-TotalPages', headers.get('x-wp-totalpages')),
                             'retrieved_at':datetime.now(timezone.utc).isoformat(), 'bytes':len(data),
                             'sha256':sha256(data).hexdigest(), 'tls_verified':True,
                             'path':'responses/'+sha256(url.encode()).hexdigest()+'.data',
                             'transport':'dedicated_loopback_proxy' if self.proxy_session else 'ordinary_direct_access'}
                (self.root/entry['path']).write_bytes(data)
                self.record(entry); self.receipts[url] = entry
                return data, entry
            except Exception as e:
                error = e
                code = getattr(e,'code',None) or getattr(getattr(e,'response',None),'status_code',None)
                if code in {400,401,403,404,410,422} or 'CERTIFICATE_VERIFY_FAILED' in str(e): break
                if attempt < 2: time.sleep(2 * (attempt + 1))
        self.record({'url':url,'status':getattr(error,'code',None),'error':str(error),
                     'retrieved_at':datetime.now(timezone.utc).isoformat()})
        raise RuntimeError(str(error))

    def record(self, entry):
        with self.lock:
            with (self.root/'manifest.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(entry,ensure_ascii=False)+'\n')

    def atlas_search(self, form):
        """Submit only the public Seawolf atlas's read-only search form."""
        if self.proxy_session is None or set(form) != {'swas_options', 'criterion', 'value'}:
            raise ValueError('Atlas search requires the dedicated public proxy and explicit search fields')
        criterion = form['criterion']
        if criterion not in {'p_region', 'p_planetName'} or form['swas_options'] != criterion:
            raise ValueError('Unsupported public atlas search criterion')
        url = 'https://theswden.net/bgci4/swatlas/atlassearchresults'
        fields = {'swas_options': criterion, criterion: form['value'], 'submit': 'Search'}
        cache_key = url + '#public-form=' + urlencode(fields)
        cached = self.receipts.get(cache_key)
        if cached:
            path = self.root / cached['path']
            if path.is_file() and sha256(path.read_bytes()).hexdigest() == cached['sha256']:
                return path.read_bytes(), cached
        time.sleep(max(.2, self.min_interval))
        with self.proxy_session.post(url, data=fields, timeout=(10,45), verify=True,
                                     allow_redirects=False, stream=True,
                                     headers={'User-Agent': USER_AGENT}) as response:
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError('Unexpected redirect from the public atlas search')
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > 30_000_000:
                    raise ValueError('Atlas search response exceeds 30 MB bound')
                chunks.append(chunk)
            data = b''.join(chunks)
            if b'Web Filter Violation' in data or b'FortiGuard Web Filtering' in data:
                raise ValueError('Organizational network filter response')
            entry = {'url':cache_key,'final_url':url,'status':200,'method':'POST','form':fields,
                     'content_type':response.headers.get('content-type',''),
                     'retrieved_at':datetime.now(timezone.utc).isoformat(),'bytes':len(data),
                     'sha256':sha256(data).hexdigest(),'tls_verified':True,
                     'path':'responses/'+sha256(cache_key.encode()).hexdigest()+'.data',
                     'transport':'dedicated_loopback_proxy'}
        (self.root / entry['path']).write_bytes(data)
        self.record(entry); self.receipts[cache_key] = entry
        return data, entry

    def json(self, base, params=None):
        url = base + ('?' + urlencode(params) if params else '')
        data, receipt = self.fetch(url)
        result = json.loads(data.decode('utf-8-sig'))
        if isinstance(result,dict) and 'error' in result: raise ValueError(str(result['error']))
        return result, receipt

    def save(self, name, records, **metadata):
        folder = self.root/name; folder.mkdir(exist_ok=True)
        temporary = folder/'records.jsonl.tmp'
        count = 0
        with temporary.open('w',encoding='utf-8') as stream:
            for record in records:
                stream.write(json.dumps(record,ensure_ascii=False)+'\n'); count += 1
        temporary.replace(folder/'records.jsonl')
        result = {'source':name,'records':count,'model_calls':0,**metadata}
        (folder/'status.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False),flush=True)
        return result


def wordpress(snapshot, host='https://swrpggm.com', source='swrpggm'):
    api = host+'/wp-json/wp/v2/'
    categories, _ = snapshot.json(api+'categories',{'per_page':100})
    category_map = {r['id']:html_text(r['name']) for r in categories}
    records = []; totals = {}; errors = []
    fields = 'id,date_gmt,modified_gmt,slug,type,link,title,content,excerpt,categories,tags'
    for endpoint in ('posts','pages'):
        params = {'per_page':100,'page':1,'orderby':'id','order':'asc','_fields':fields}
        first, receipt = snapshot.json(api+endpoint,params)
        totals[endpoint] = int(receipt.get('total') or len(first))
        pages = int(receipt.get('pages') or 1); batches = [(first, receipt)]
        def fetch(page): return snapshot.json(api+endpoint,{**params,'page':page})
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(fetch,p):p for p in range(2,pages+1)}
            for i, future in enumerate(as_completed(futures),1):
                try: batches.append(future.result())
                except Exception as error: errors.append({'endpoint':endpoint,'page':futures[future],'error':str(error)})
                if i % 10 == 0: print(json.dumps({'stage':source,'endpoint':endpoint,'pages_received':i+1,'pages':pages}),flush=True)
        for batch, receipt in batches:
            for row in batch:
                body = html_text(row.get('content',{}).get('rendered',''))
                records.append({'key':str(row['id']),'name':html_text(row['title']['rendered']),
                    'body':body,'payload':row,'categories':[category_map.get(x,str(x)) for x in row.get('categories',[])],
                    'url':row['link'],'receipt':receipt,'format':'wordpress','modified':row.get('modified_gmt')})
    records.sort(key=lambda r:int(r['key']))
    return snapshot.save(source,records,status='complete' if not errors and len(records)==sum(totals.values()) else 'partial',
                         expected=totals,errors=errors,scope='All public posts and pages; HTML preserved in payload')


def wiki_record(page, receipt, api):
    revision = (page.get('revisions') or [{}])[0]
    body = revision.get('slots',{}).get('main',{}).get('*',revision.get('*',''))
    title = page['title']; revision_id = revision.get('revid')
    return {'key':str(page['pageid']),'name':title,'body':body,'payload':page,
            'categories':[x['title'].removeprefix('Category:') for x in page.get('categories',[])],
            'url':api.rsplit('/',1)[0]+'/index.php?oldid='+str(revision_id),
            'receipt':receipt,'revision_id':revision_id,'format':'mediawiki','namespace':page.get('ns',0)}


def wiki_pages(snapshot, api, titles=None, pageids=None):
    params={'action':'query','prop':'revisions|categories','rvprop':'ids|timestamp|content','rvslots':'main',
            'cllimit':'max','format':'json'}
    params['titles' if titles is not None else 'pageids']='|'.join(map(str,titles if titles is not None else pageids))
    data, receipt = snapshot.json(api,params)
    return [wiki_record(p,receipt,api) for p in data.get('query',{}).get('pages',{}).values() if 'missing' not in p and 'revisions' in p]


def episode_d6(snapshot):
    api = 'https://epd6.com/api.php'; pages = []
    for namespace in (0,14,10):
        params={'action':'query','list':'allpages','aplimit':500,'apnamespace':namespace,'format':'json'}
        while True:
            data,_=snapshot.json(api,params);pages.extend(data['query']['allpages'])
            if 'continue' not in data:break
            params.update(data['continue'])
    batches=[pages[i:i+40] for i in range(0,len(pages),40)]
    records=[];errors=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(wiki_pages,snapshot,api,pageids=[p['pageid'] for p in b]):n for n,b in enumerate(batches)}
        for i,future in enumerate(as_completed(futures),1):
            try:records.extend(future.result())
            except Exception as e:errors.append({'batch':futures[future],'error':str(e)})
            if i%20==0:print(json.dumps({'stage':'episode-d6','batches_received':i,'batches':len(batches),'records':len(records)}),flush=True)
    return snapshot.save('episode-d6',sorted(records,key=lambda r:int(r['key'])),
        status='complete' if not errors and len(records)==len(pages) else 'partial',expected=len(pages),errors=errors,
        scope='All main articles, categories and templates; current mirror, not an independent original source')


def wookieepedia(snapshot):
    api='https://starwars.fandom.com/api.php'
    seeds=['The DarkStryder Campaign (series)','The DarkStryder Campaign','The Kathol Outback','The Kathol Rift',
           'Endgame (adventure)','DarkStryder Campaign','Kathol sector','Kathol Outback','Kathol Rift','FarStar','Kentor Sarne']
    initial=wiki_pages(snapshot,api,titles=seeds)
    # All direct article references from campaign books and hubs. No whole-Wookieepedia crawl.
    titles=set(seeds)
    for row in initial:
        for match in re.finditer(r'\[\[([^\]|#]+)',row['body']):
            title=match[1].strip()
            if title and ':' not in title:titles.add(title)
    categories=['Battles of the DarkStryder Campaign','Individuals of the FarStar crew','Planets in the Kathol sector',
                'Planets in the Kathol Rift','Planets in the Kathol Outback','Starships of Moff Sarne\'s forces']
    for title in categories:
        params={'action':'query','list':'categorymembers','cmtitle':'Category:'+title,'cmlimit':500,'cmnamespace':0,'format':'json'}
        while True:
            data,_=snapshot.json(api,params)
            titles.update(r['title'] for r in data['query']['categorymembers'])
            if 'continue' not in data:break
            params.update(data['continue'])
    values=sorted(titles);records=[];errors=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures={pool.submit(wiki_pages,snapshot,api,titles=values[i:i+20]):i for i in range(0,len(values),20)}
        for future in as_completed(futures):
            try:records.extend(future.result())
            except Exception as e:errors.append({'batch_start':futures[future],'error':str(e)})
    unique={r['key']:r for r in records}
    return snapshot.save('wookieepedia-darkstryder',unique.values(),status='complete_scope' if not errors else 'partial',
                         queried_titles=len(values),errors=errors,scope='DarkStryder books/hubs and every directly linked article; explicit Kathol/FarStar categories')


def french_reference(snapshot, source):
    """Campaign-scoped French enrichment, rather than unrelated full encyclopedias."""
    anakin = source == 'anakinworld-darkstryder'
    host = 'https://www.anakinworld.com' if anakin else 'https://www.starwars-holonet.com'
    seeds = ['/encyclopedie/'+x for x in ['darkstryder','secteur-kathol','farstar','campagne-contre-le-moff-kentor-sarne']] if anakin else [
        '/encyclopedie/liste-'+x+'.html' for x in ['personnages','vaisseaux','planetes','geographie','organisations','especes','creatures','droides','histoire']]
    records = {}; errors = []; found = set(); seed_set = set(host+p for p in seeds)
    def get(url, index=False):
        data, receipt = snapshot.fetch(url); raw = data.decode('utf-8','replace')
        title = re.search(r'<h1\b[^>]*>(.*?)</h1>',raw,re.S|re.I)
        if not title: title = re.search(r'<title\b[^>]*>(.*?)</title>',raw,re.S|re.I)
        main = re.search(r'<main\b[^>]*>(.*?)</main>',raw,re.S|re.I)
        content = main[1] if main else raw
        parser=HTMLText();parser.feed(content)
        links={urljoin(url,x).split('#')[0] for x in parser.links if '/encyclopedie/' in x and '/listes/' not in x and '/liste-' not in x and not x.startswith('#')}
        links={x for x in links if urlparse(x).netloc==urlparse(host).netloc}
        row={'key':urlparse(url).path,'name':html_text(title[1]).split(' - Anakinworld')[0].split(' — Anakinworld')[0] if title else urlparse(url).path,
             'body':parser.text(),'payload':{'url':url,'outgoing_urls':sorted(links),'index':index},'url':url,
             'receipt':receipt,'format':'html','categories':[],'section':'metadata' if index else 'reference'}
        return row, links
    for url in seed_set:
        try:
            row, links=get(url,not anakin);records[url]=row
            if anakin:found.update(links)
            else:found.update(x for x in links if re.search(r'kathol|darkstryder|farstar|sarne|adrimetrum|dajus|tofarain|exocron|gandle|aaris|kal-shebbol',x,re.I))
        except Exception as e:errors.append({'url':url,'error':str(e)})
    # The selected DarkStryder articles contribute one further bounded link layer.
    for depth in range(2 if not anakin else 1):
        targets=sorted(found-set(records));found=set()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures={pool.submit(get,u):u for u in targets}
            for future in as_completed(futures):
                try:
                    row,links=future.result();records[futures[future]]=row
                    if depth==0 and not anakin:found.update(links)
                except Exception as e:errors.append({'url':futures[future],'error':str(e)})
    status='complete_scope' if not errors else 'partial'
    if records and all(r['payload'].get('index') for r in records.values()):status='index_only'
    return snapshot.save(source,records.values(),status=status,errors=errors,
                         scope='DarkStryder/Kathol campaign hubs and linked French reference articles; not a full encyclopedia mirror')


def acquire(root, selected=None):
    from .public_sites import opend6_srd
    snapshot=Snapshot(root)
    collectors={'swrpggm':wordpress,'episode-d6':episode_d6,'wookieepedia-darkstryder':wookieepedia,
                'anakinworld-darkstryder':lambda s:french_reference(s,'anakinworld-darkstryder'),
                'holonet-darkstryder':lambda s:french_reference(s,'holonet-darkstryder'),
                'opend6-srd':opend6_srd}
    result=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(collectors[n],snapshot):n for n in (selected or collectors)}
        for future in as_completed(futures):
            try:result.append(future.result())
            except Exception as e:
                result.append(snapshot.save(futures[future],[],status='failed',errors=[str(e)]))
    return result
