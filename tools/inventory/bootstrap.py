"""One-time, non-destructive migration of the existing FarStar console."""
import base64
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = json.loads((ROOT/'config.local.json').read_text(encoding='utf-8-sig')) if (ROOT/'config.local.json').exists() else {}
DRIVE = Path(CONFIG.get('sourceRoot','D:/Drive_google/DarkStryder'))
RUNTIME = Path(CONFIG.get('runtimeRoot',str(ROOT.parent/'darkstryder_runtime')))

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

def main():
    for folder in ['apps/knowledge-web/public/modules','apps/knowledge-web/public/styles',
                   'apps/knowledge-web/src/pages','content/characters','content/locations',
                   'content/factions','content/ships','content/equipment','content/adventures',
                   'content/events','content/maps','catalog','packages/knowledge-model',
                   'tools/extract','tools/media','tools/export','legacy','docs','tests']:
        (ROOT/folder).mkdir(parents=True, exist_ok=True)
    for folder in ['extracted','media/portraits','backups','models','logs']:
        (RUNTIME/folder).mkdir(parents=True, exist_ok=True)
    legacy = ROOT/'legacy/farstar_campaign_console_v2_1.html'
    if not legacy.exists():
        legacy.write_bytes((ROOT/'farstar_campaign_console_v2_1.html').read_bytes())
    html = legacy.read_text(encoding='utf-8')
    data_path=ROOT/'farstar_campaign_data_v2_1.json'
    if not data_path.exists(): data_path=ROOT/'content/campaign.json'
    data = json.loads(data_path.read_text(encoding='utf-8'))
    target = ROOT/'content/campaign.json'
    if not target.exists(): write_json(target, data)
    thumbs = json.loads(re.search(r'<script id="thumbs"[^>]*>(.*?)</script>',html,re.S)[1])
    portraits = {}
    for key,value in thumbs.items():
        if not value.startswith('data:'): continue
        header,b64=value.split(',',1)
        ext='png' if 'png' in header else 'jpg'
        name=hashlib.sha256(key.encode()).hexdigest()[:16]+'.'+ext
        (RUNTIME/'media/portraits'/name).write_bytes(base64.b64decode(b64))
        portraits[key]='/runtime/media/portraits/'+name
    write_json(ROOT/'catalog/portraits.json',portraits)
    css = re.search(r'<style>(.*?)</style>',html,re.S)[1]
    (ROOT/'apps/knowledge-web/public/styles/legacy.css').write_text(css,encoding='utf-8')
    js = re.findall(r'<script>(.*?)</script>',html,re.S)[-1]
    # Split the original function bodies without reinterpreting their data.
    matches=list(re.finditer(r'function (\w+)\(',js))
    groups={'overview':['renderOverview'], 'crew':['renderCrew','showStats','renderSkills'],
            'relations':['renderRelations','renderDroids'],
            'random':['rand','randLocationFrom','extraRoom','generateRandom','roomIdsByLabel','roomIdByLabel','renderRandomSnapshot','renderRandom'],
            'details':['openPerson','openRoom','closeModal'],
            'ship':['renderShip'], 'occupancy':['currentAssignments','extraCycleForMode','extraFor','allRoomOccupancy','chip']}
    import_line="import { D, TH, M, byId, roomById, state, esc, nrm, portrait, portraitLarge, avatarHtml } from './store.js';\n"
    functions={}
    for i,m in enumerate(matches):
        end=matches[i+1].start() if i+1<len(matches) else len(js)
        body=js[m.start():end]
        if m[1]=='closeModal': body=body[:body.index("document.getElementById('modal').onclick")]
        if m[1]=='renderAll': continue
        functions[m[1]]=body.strip()
    for group,names in groups.items():
        # Runtime calls use explicit window bindings for legacy inline handlers.
        imports=import_line
        if group!='occupancy': imports+="import { currentAssignments, extraFor, allRoomOccupancy, chip, extraCycleForMode } from './occupancy.js';\n"
        text=imports+'\n'+'\n'.join('export '+functions[n] for n in names)+'\n'
        (ROOT/f'apps/knowledge-web/public/modules/{group}.js').write_text(text,encoding='utf-8')
    assets=[]
    paths=[*sorted((DRIVE/'Spaceship/Plans').glob('FS_Deck*')),
           DRIVE/'Atlas/Kathol_Sector.png', DRIVE/'Atlas/MapSecteurKathol.jpg',
           DRIVE/'Atlas/katholriftfk6.jpg',DRIVE/'Atlas/KatholOutBack1.jpg']
    for i,p in enumerate(paths):
        if p.exists(): assets.append({'id':f'asset-{i+1}','name':p.stem,'path':p.relative_to(DRIVE).as_posix(),'kind':'deck' if 'FS_Deck' in p.name else 'star-map'})
    write_json(ROOT/'catalog/assets.json',assets)
    write_json(ROOT/'config.local.json',{'sourceRoot':str(DRIVE),'runtimeRoot':str(RUNTIME)})
    entities=ROOT/'content/entities.json'
    if not entities.exists(): write_json(entities,{'schema_version':1,'entities':[],'schedule':{'shift1Start':0,'shift2Start':12},'mapPins':[],'roomPins':[]})
    print(f'Migrated {len(data["crew"]["members"])} crew members, {len(portraits)} portraits, {len(assets)} map/plan assets.')

if __name__=='__main__': main()
