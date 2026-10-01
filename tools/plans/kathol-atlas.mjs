// Initial manual transcription of the local assembled MapSecteurKathol.jpg.
// Measurement canvas: 798 × 2048 (the original is 1782 × 4573).
// These are diagram coordinates, NOT spatial coordinates or a distance scale.
// Never reapply this seed over edited entities/navigation.
const MAIN='src-1678b1eb8c4bd691',OUT='src-a3a28719eed85a70',RIFT='src-e76020e4b32da626',END='src-c86af6abdc3184c5';
const pos=(x,y)=>[+(x/798*100).toFixed(4),+(y/2048*100).toFixed(4)];
const ref=(sourceId,page)=>({sourceId,page});
export function initialKatholAtlas(){
  const entities=[],nodes=[],routes=[];
  function place(id,name,x,y,region,type='planet',summary='',source=ref(MAIN,102),properties={}){
    const entityId='place-'+id;
    entities.push({id:entityId,type,name,summary,body:'',tags:['Kathol',region],links:[],visibility:'mj',provenance:'official',sources:[source],properties:{region,...properties,media:[]}});
    nodes.push({id:'nav-'+id,label:name,entityIds:[entityId],position:x===null?null:pos(x,y),region,kind:type==='location'?'station':type,referenceAssetId:'asset-8',provenance:'official'});
    return nodes.at(-1);
  }
  place('kal-shebbol',"Kal’Shebbol",558,99,'sector','planet','Capitale du secteur de Kathol ; point de référence des cartes locales.');
  place('brolsam','Brolsam',475,102,'sector','planet','Système agricole proche de Kal’Shebbol.');
  place('torize','Torize',501,174,'sector','planet','Monde industriel de la route commerciale de Trition ; atmosphère de type II.');
  place('aaris','Aaris',421,188,'sector','planet','Monde exploré dans « The Saga of the Aaris ».',ref(MAIN,139));
  place('kolatill','Kolatill',479,223,'sector','planet','Centre de fabrication de répulseurs sur la route de Trition.');
  place('charis','Charis',411,260,'sector','planet','Escale figurant sur la carte du secteur.',ref(MAIN,104));
  place('corjain','Corjain',474,272,'sector','planet','Monde minier à l’atmosphère toxique ; colonies souterraines.',ref(MAIN,104));
  place('oon-tien','Oon Tien',548,257,'sector','planet','Installations de réparation et défenses impériales.',ref(MAIN,104));
  place('gandle-ott','Gandle Ott',359,289,'sector','planet','Dernier grand monde industriel du secteur ; situé dans le système Ott.',ref(MAIN,104),{systemName:'Ott'});
  place('mairne','Mairne',402,309,'sector','system','Petit système fournisseur de ressources pour Gandle Ott.',ref(MAIN,104));
  place('peirs','Peirs',343,317,'sector','system','Petit système fournisseur de ressources pour Gandle Ott.',ref(MAIN,104));
  place('shintel','Shintel',310,329,'sector','planet','Monde visité dans « Traitor in Our Midst ».',ref(MAIN,153));
  place('ivatch','Ivatch',327,354,'sector','system','Petit système fournisseur de ressources pour Gandle Ott.',ref(MAIN,104));
  place('tanquilla','Tanquilla Beach',255,320,'sector','location','Port clandestin figurant sur les cartes du secteur.',ref(MAIN,157));
  place('pembric','Pembric II',249,375,'sector','planet','Monde figurant sur la carte du secteur.',ref(MAIN,99));
  place('galtea','Galtea',230,458,'sector','planet','Point de départ de la Galtea Run vers Timbra Ott.',ref(OUT,6));
  place('sebiris','Sebiris',305,483,'sector','planet','Monde détaillé dans l’addendum du livre principal.',ref(MAIN,190));
  place('timbra-ott','Timbra Ott',209,882,'outback','planet','Ancienne colonie pénitentiaire, au bout de la Galtea Run.',ref(OUT,6));
  place('dolstan','Dolstan',489,783,'outback','planet','Monde agricole dominant de la Ligue Pimbrellan.',ref(OUT,6));
  place('bresan','Bresan',427,803,'outback','planet','Colonie de la Ligue Pimbrellan.',ref(OUT,6));
  place('swedlan','Swedlan',535,845,'outback','planet','Colonie de la Ligue Pimbrellan.',ref(OUT,6));
  place('sapella','Sapella',158,969,'outback','planet','Colonie isolée, à l’écart de la route principale vers Jangelle.',ref(OUT,7));
  place('binaros','Binaros',158,1101,'outback','planet','Monde de jungle, ruines et ancien poste de recherche impérial.',ref(OUT,26));
  const episol=place('episol','Episol',427,1183,'republic','system','Système de Dayark, siège du gouvernement de la République de Kathol.',ref(OUT,39));
  place('dayark','Dayark',null,null,'republic','planet','Lune agricole de la géante gazeuse Nepe, dans le système Episol.',ref(OUT,39),{systemId:'place-episol',parentId:'place-nepe'});
  place('nepe','Nepe',null,null,'republic','planet','Géante gazeuse dont Dayark est un satellite ; système Episol.',ref(OUT,39),{systemId:'place-episol'});
  episol.entityIds.push('place-dayark','place-nepe');
  place('jangelle','Jangelle',328,1119,'outback','planet','Colonie autonome liée à la République de Kathol par un pacte de défense.',ref(OUT,7));
  place('pitann','Pitann',462,1231,'republic','planet','Monde désertique et minier de la République de Kathol.',ref(OUT,8));
  place('ehjenla','Ehjenla',280,1342,'outback','planet','Monde sortant d’un âge glaciaire ; peuplé par les Tuhgri.',ref(OUT,8));
  place('qumock','Qu’mock',90,1441,'outback','location','Construction artificielle associée à la Confédération Qektoth sur la carte.',ref(OUT,12));
  place('uukaablis','Uukaablis',119,1472,'outback','planet','Monde des Uukaabliens, réputés pour leurs connaissances médicales.',ref(OUT,9));
  place('shatuun','Shatuun',513,1459,'outback','planet','Monde montagneux soumis à de violents orages électriques.',ref(OUT,9));
  place('exocron','Exocron',593,1443,'outback','planet','Monde dissimulé par une nébuleuse ; absent des fragments de carte obtenus initialement par le FarStar.',ref(OUT,9));
  const nah=place('nah-malis','Nah’Malis',572,1577,'rift','system','Système de Danoor, à la lisière du Rift.',ref(OUT,9));
  place('danoor','Danoor',null,null,'rift','planet','Ancien avant-poste scientifique du Rift, en reconstruction après un impact d’astéroïde.',ref(OUT,9),{systemId:'place-nah-malis'});nah.entityIds.push('place-danoor');
  place('qmaere','Q’Maere',576,1665,'rift','planet','Monde de l’installation pénitentiaire Q’Maere.',ref(RIFT,25));
  place('construct','Construction extraterrestre',636,1708,'rift','location','Structure artificielle figurant sur la carte du Rift.',ref(RIFT,60));
  place('yvara','Yvara',674,1958,'rift','planet','Monde des Yvarema ; montagnes, plaines fongiques et réseaux fluviaux.',ref(RIFT,74));
  place('demonsgate','Demonsgate',659,1995,'rift','planet','Monde figurant sur la carte du Rift.',ref(RIFT,9));
  place('kathol','Kathol',null,null,'beyond','planet','Monde de DarkStryder dans Endgame ; aucune position ajoutée à la carte faute de relevé source.',ref(END,64));
  place('blue-swirl','Blue Swirl',null,null,'rift','location','Zone relativement claire du Rift, surnommée par les opérateurs de senseurs ; Yvara s’y trouve. Position non relevée indépendamment.',ref(RIFT,71));
  place('karideph','Karideph',null,null,'external','system','Destination extérieure : amas de Minos. La carte indique 10 jours depuis Kal’Shebbol, sans position du système dans Kathol.',ref(MAIN,99));
  nodes.push({id:'nav-minos-exit',label:'Vers Karideph · Minos',entityIds:['place-karideph'],position:pos(590,22),region:'external',kind:'gateway',referenceAssetId:'asset-8',provenance:'official',coordinateNote:'Emplacement de la flèche de sortie du diagramme, PAS du système Karideph.'});
  // Ten systems are drawn in the Republic; eight have no name on the chart.
  for(const [i,x,y] of [[1,393,1226],[2,501,1142],[3,583,1164],[4,606,1198],[5,653,1220],[6,583,1284],[7,548,1231],[8,508,1192]])nodes.push({id:'nav-republic-'+i,label:'Système non nommé R'+i,entityIds:[],position:pos(x,y),region:'republic',kind:'unknown',referenceAssetId:'asset-8',provenance:'official'});
  function route(a,b,min,max=min,kind='secondary',bends=[],sourceAssetId='asset-8',notes=''){
    const from='nav-'+a,to='nav-'+b,A=nodes.find(n=>n.id===from),B=nodes.find(n=>n.id===to);
    if(!A||!B)throw Error('Unknown trace endpoint');
    routes.push({id:'route-'+a+'--'+b,from,to,kind,points:[A.position,...bends.map(([x,y])=>pos(x,y)),B.position],durationHours:min===null?null:{min,max},distanceLy:null,hyperdriveClass:1,provenance:'official',referenceAssetId:sourceAssetId,sources:[],notes});
  }
  route('kal-shebbol','torize',18,18,'trade',[[530,130]]);
  route('kal-shebbol','minos-exit',240,240,'trade',[[590,53]],'asset-8','Sortie vers Karideph (amas de Minos) : 10 jours indiqués sur la carte. La flèche ne donne pas la position spatiale de Karideph.');
  route('kal-shebbol','brolsam',30,30,'trade',[[527,129],[503,110]]);
  route('brolsam','aaris',81,81,'secondary',[[458,123],[424,145]]);
  route('aaris','kolatill',16,16,'secondary',[[442,207]]);
  route('torize','kolatill',28,28,'trade',[[489,200]]);
  route('aaris','charis',27,27,'secondary',[[413,232]]);
  route('kolatill','charis',21,21,'trade',[[445,246]]);
  route('torize','oon-tien',14,14,'secondary',[[523,205],[533,237]]);
  route('kolatill','oon-tien',19,19,'secondary',[[509,241]]);
  route('kolatill','corjain',5);
  route('charis','corjain',14);
  route('corjain','oon-tien',11,11,'secondary',[[505,269]]);
  route('charis','gandle-ott',15,15,'trade');
  route('charis','mairne',12);route('gandle-ott','mairne',8);route('gandle-ott','peirs',7);
  route('peirs','shintel',5);route('peirs','ivatch',30);route('shintel','ivatch',8);
  route('shintel','tanquilla',24);route('shintel','pembric',72);route('tanquilla','pembric',19);
  route('pembric','galtea',32,32,'secondary',[[240,421]]);route('pembric','sebiris',62,62,'secondary',[[261,412],[285,446]]);route('galtea','sebiris',27);
  route('galtea','timbra-ott',342,342,'secondary',[[233,550],[227,680],[221,800]],'asset-10');
  route('sebiris','dolstan',345,345,'secondary',[[312,505],[370,539],[388,574],[410,600],[413,662],[412,689],[449,737]],'asset-10');
  route('timbra-ott','bresan',314,314,'secondary',[[268,862],[339,845],[361,830],[402,822]],'asset-10');route('bresan','dolstan',49,49,'secondary',[],'asset-10');route('dolstan','swedlan',76,76,'secondary',[],'asset-10');
  route('timbra-ott','sapella',131,131,'secondary',[],'asset-10');route('sapella','binaros',148,148,'secondary',[],'asset-outback-2');
  route('timbra-ott','jangelle',308,308,'secondary',[[250,924],[256,974],[277,1005],[285,1046],[303,1068]],'asset-outback-2');
  route('binaros','jangelle',170,170,'secondary',[[192,1095],[233,1112]],'asset-outback-2');route('jangelle','episol',105,105,'secondary',[],'asset-outback-3');
  route('episol','republic-1',51,51,'secondary',[],'asset-outback-3');route('episol','pitann',48,48,'secondary',[],'asset-outback-3');
  for(const [a,b] of [['republic-1','pitann'],['episol','republic-2'],['episol','republic-8'],['republic-2','republic-8'],['republic-2','republic-3'],['republic-3','republic-4'],['republic-4','republic-5'],['republic-5','republic-6'],['republic-6','republic-7'],['republic-7','republic-4'],['republic-7','republic-8'],['republic-7','pitann'],['republic-8','pitann']])route(a,b,null,null,'secondary',[],'asset-outback-3','Route dessinée ; durée non fournie sur la carte.');
  route('republic-1','ehjenla',158,158,'secondary',[[358,1263],[319,1320]],'asset-outback-2');
  route('ehjenla','uukaablis',211,211,'secondary',[[252,1363],[235,1396],[170,1434]],'asset-outback-2');route('uukaablis','qumock',50,50,'secondary',[],'asset-outback-2');
  route('uukaablis','shatuun',318,318,'secondary',[[196,1498],[232,1492],[288,1510],[327,1518],[356,1505],[423,1499],[467,1484]],'asset-outback-3');
  route('shatuun','exocron',99,99,'secondary',[[561,1444]],'asset-outback-3');route('shatuun','nah-malis',182,182,'secondary',[[542,1473],[540,1494],[560,1505],[546,1534],[558,1555]],'asset-outback-3');
  route('nah-malis','qmaere',168,504,'rift',[[585,1605],[624,1633],[620,1645],[564,1654]],'asset-9','1–3 semaines ; tracé représentatif susceptible de changer.');
  route('qmaere','construct',504,840,'rift',[[558,1684],[614,1693],[633,1680],[588,1701],[566,1728],[603,1738]],'asset-9','3–5 semaines ; corridors instables.');
  route('construct','yvara',600,912,'rift',[[644,1733],[592,1742],[574,1774],[545,1780],[636,1781],[642,1796],[592,1810],[573,1830],[596,1843],[683,1838],[704,1856],[696,1869],[628,1864],[604,1879],[606,1906],[639,1918],[620,1938],[669,1946]],'asset-9','3 semaines 4 jours – 5 semaines 3 jours ; tracé représentatif, pas une garantie de passage.');
  route('yvara','demonsgate',24,96,'rift',[[679,1979],[650,1980]],'asset-9','1–4 jours.');
  // Label positions are also measured from the reference, not astronomical data.
  const durationPositions={
    'aaris--kolatill':[440,193],'torize--kolatill':[460,201],
    'kolatill--charis':[435,237],'torize--oon-tien':[536,217],
    'kolatill--oon-tien':[514,234],'kolatill--corjain':[479,251],
    'charis--corjain':[440,284],'corjain--oon-tien':[513,266],
    'charis--gandle-ott':[371,266],'charis--mairne':[419,298],
    'gandle-ott--mairne':[375,306],'gandle-ott--peirs':[333,299],
    'peirs--shintel':[321,338],'peirs--ivatch':[350,350],
    'shintel--ivatch':[300,357],'shintel--tanquilla':[278,319],
    'shintel--pembric':[276,357],'tanquilla--pembric':[228,350]
  };
  for(const r of routes)if(durationPositions[r.id.slice(6)])r.labelAt=pos(...durationPositions[r.id.slice(6)]);
  for(const e of entities){const slug=e.id.slice(6);if(['kal-shebbol','torize','kolatill','brolsam','aaris','charis','corjain','gandle-ott','mairne','oon-tien','tanquilla'].includes(slug))e.properties.media.push({id:'media-local-'+slug,url:'/asset/asset-planet-'+slug,caption:'Support visuel du dossier local ; attribution personnelle, carte de surface canonique non attestée.',provenance:'personal'});}
  return {entities,navigation:{version:1,id:'kathol-atlas',name:'Secteur de Kathol',referenceAssetId:'asset-8',sourceSize:{width:1782,height:4573},measurementCanvas:{width:798,height:2048},coordinateMeaning:'Pourcentages relevés sur un diagramme non à l’échelle ; pas de coordonnées spatiales.',notes:'Carte MJ. Systèmes mineurs omis dans les sources. Durées de référence en classe ×1 ; conditions locales et astrogation peuvent les modifier. La République compte 14 mondes dans 10 systèmes (Outback p.39) ; les noms absents ne sont pas inventés.',nodes,routes,
    views:[{id:'all',name:'Carte assemblée complète',box:[0,0,798,2048]},{id:'sector',name:'Secteur principal',box:[140,0,510,520]},{id:'outback-north',name:'Outback · Marcol Void',box:[140,435,440,600]},{id:'outback-south',name:'Outback · République / lisière',box:[30,1050,690,560]},{id:'rift',name:'Kathol Rift',box:[375,1540,415,500]}],
    regions:[{name:'Marcol Void',points:[[240,530],[393,570],[446,722],[433,760],[272,775],[225,723]].map(([x,y])=>pos(x,y)),labelAt:pos(315,651)},{name:'Ligue Pimbrellan',points:[[406,775],[466,750],[510,760],[548,814],[548,857],[437,849],[395,820]].map(([x,y])=>pos(x,y)),labelAt:pos(462,827)},{name:'République de Kathol',points:[[387,1174],[464,1121],[548,1133],[625,1174],[680,1220],[628,1298],[552,1308],[488,1279],[379,1242]].map(([x,y])=>pos(x,y)),labelAt:pos(522,1260)},{name:'Confédération Qektoth',points:[[33,1358],[69,1351],[99,1370],[107,1396],[92,1448],[44,1454],[17,1417]].map(([x,y])=>pos(x,y)),labelAt:pos(69,1387)},{name:'Kathol Rift',points:[[461,1479],[513,1520],[572,1497],[617,1429],[677,1453],[713,1522],[773,1475],[790,1524],[782,2034],[395,2036],[399,1786],[376,1733],[420,1648],[386,1604],[434,1546]].map(([x,y])=>pos(x,y)),labelAt:pos(650,1760)}]}};
}
