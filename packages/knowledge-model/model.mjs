import { z } from 'zod';

export const entityTypes = ['npc','planet','location','faction','event','adventure','ship','equipment'];
const validIdentifier=/^[\p{L}\p{N}_-]+$/u;
const identifier=z.string().regex(validIdentifier).max(100);
const coordinate=z.tuple([z.number().finite().min(0).max(100),z.number().finite().min(0).max(100)]);
const mediaURL=z.string().max(600).refine(value=>(/^\/asset\/[\w-]+$/.test(value)||/^\/runtime\/media\/(?:[\w.-]+\/)*[\w.-]+$/.test(value))&&!value.split('/').includes('..'),'Référence média locale invalide');
const roomMediaSchema=z.array(z.object({id:identifier,url:mediaURL,caption:z.string().max(500).default(''),provenance:z.enum(['official','personal','interpretation'])}).passthrough()).max(100);
export const deckPlanSchema=z.object({
  id:identifier,deck:z.string(),assetId:identifier,
  sourceSize:z.object({width:z.number().int().positive(),height:z.number().int().positive()}).passthrough(),
  outline:z.array(coordinate).min(3).max(500),
  regions:z.array(z.object({id:identifier,label:z.string().trim().min(1).max(160),roomId:identifier.nullable(),kind:z.enum(['room','corridor','access']),points:z.array(coordinate).min(3).max(500),labelAt:coordinate.nullable().optional()}).passthrough()).max(500),
  details:z.array(z.object({kind:z.enum(['engine','wall']),points:z.array(coordinate).min(2).max(500)}).passthrough()).max(500)
}).passthrough();
export function validateDeckPlans(ship){
  const ids=new Set(),rooms=new Map(ship.rooms.map(r=>[r.id,r]));
  for(const room of ship.rooms){
    if(room.media!==undefined){roomMediaSchema.parse(room.media);const mediaIds=new Set();for(const media of room.media){if(mediaIds.has(media.id))throw new Error('Identifiant média dupliqué');mediaIds.add(media.id);}}
  }
  if(ship.deckPlans===undefined)return;
  z.array(deckPlanSchema).max(100).parse(ship.deckPlans);
  for(const plan of ship.deckPlans){
    if(ids.has(plan.id)||!ship.decks.includes(plan.deck))throw new Error('Plan invalide ou identifiant dupliqué');ids.add(plan.id);
    for(const region of plan.regions){
      if(ids.has(region.id))throw new Error('Identifiant de zone dupliqué');ids.add(region.id);
      if(region.roomId!==null&&rooms.get(region.roomId)?.deck!==plan.deck)throw new Error('Salle inconnue ou située sur un autre pont');
      let area=0;for(let i=0;i<region.points.length;i++){const a=region.points[i],b=region.points[(i+1)%region.points.length];area+=a[0]*b[1]-b[0]*a[1];}
      if(Math.abs(area)<.00001)throw new Error('Zone invalide : polygone sans surface');
    }
  }
}
export const entitySchema = z.object({
  id: identifier,
  type: z.enum(entityTypes),
  name: z.string().trim().min(1).max(200),
  summary: z.string().default(''),
  body: z.string().default(''),
  tags: z.array(z.string()).default([]),
  links: z.array(identifier).default([]),
  visibility: z.enum(['mj','joueurs']).default('mj'),
  provenance: z.enum(['official','personal','interpretation']).default('personal'),
  sources: z.array(z.object({sourceId:identifier,page:z.number().int().positive()})).default([]),
  properties: z.record(z.string(),z.unknown()).default({})
});
const pin = z.object({id:identifier,assetId:identifier,entityId:identifier.optional(),roomId:identifier.optional(),x:z.number().min(0).max(100),y:z.number().min(0).max(100)});
export const entitiesSchema = z.object({
  schema_version:z.literal(1), entities:z.array(entitySchema),
  schedule:z.object({shift1Start:z.number().min(0).max(23),shift2Start:z.number().min(0).max(23)}).refine(s=>s.shift1Start!==s.shift2Start,'Les deux quarts doivent commencer à des heures distinctes'),
  mapPins:z.array(pin), roomPins:z.array(pin)
}).superRefine((data,ctx)=>{
  const ids=new Set();
  for(const e of data.entities){if(ids.has(e.id))ctx.addIssue({code:'custom',message:'Identifiant dupliqué: '+e.id});ids.add(e.id);}
});
export function validateCampaign(data){
  if(!data || !Array.isArray(data.crew?.members) || !Array.isArray(data.ship?.rooms) || !Array.isArray(data.ship?.decks) || !Array.isArray(data.droids?.catalog) || !data.operations || !data.skills?.rankings || !Array.isArray(data.history?.groups)) throw new Error('Structure de campagne invalide');
  const ids=new Set(), rooms=new Set(data.ship.rooms.map(r=>r.id));
  // The 2.1 console declares patrol and virtual security locations in posts.
  // They are assignment targets, without inventing physical rooms or map pins.
  for(const post of data.operations.posts||[])for(const id of post.room_ids||[]){
    if(typeof id==='string' && /^(outside|virtual)-/.test(id) && validIdentifier.test(id))rooms.add(id);
  }
  for(const r of data.ship.rooms)if(!validIdentifier.test(r.id)||typeof r.label!=='string'||!data.ship.decks.includes(r.deck))throw new Error('Pièce invalide');
  validateDeckPlans(data.ship);
  for(const p of data.crew.members){
    if(typeof p.id!=='string' || !validIdentifier.test(p.id) || typeof p.name!=='string' || !p.name.trim() || ids.has(p.id) || !p.history || !p.stats || !p.assignments) throw new Error('Personnage invalide ou identifiant dupliqué');
    ids.add(p.id);
    for(const mode of ['shift1','shift2','alert']) for(const a of p.assignments[mode]||[]) for(const id of a.room_ids||[]) if(!rooms.has(id))throw new Error('Pièce inconnue: '+id);
  }
  for(const p of data.crew.members)for(const r of p.relations||[])if(!ids.has(r.person_id))throw new Error('Relation inconnue: '+r.person_id);
  return data;
}
