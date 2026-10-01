import {z} from 'zod';
const id=z.string().regex(/^[\p{L}\p{N}_-]+$/u).max(100);
const point=z.tuple([z.number().finite().min(0).max(100),z.number().finite().min(0).max(100)]);
const source=z.object({sourceId:id,page:z.number().int().positive()}).passthrough();
const localURL=z.string().max(600).refine(s=>(/^\/asset\/[\w-]+$/.test(s)||/^\/runtime\/media\/(?:[\w.-]+\/)*[\w.-]+$/.test(s))&&!s.split('/').includes('..'),'Média local invalide');
export const placeMediaSchema=z.array(z.object({id,url:localURL,caption:z.string().max(500),provenance:z.enum(['official','personal','interpretation'])}).passthrough()).max(100);
const duration=z.object({min:z.number().finite().positive(),max:z.number().finite().positive()}).passthrough().refine(d=>d.max>=d.min,'Intervalle de durée inversé');
export const navigationSchema=z.object({
  version:z.literal(1),id,name:z.string().min(1),referenceAssetId:id,
  sourceSize:z.object({width:z.number().int().positive(),height:z.number().int().positive()}).passthrough(),
  measurementCanvas:z.object({width:z.number().positive(),height:z.number().positive()}).passthrough(),
  coordinateMeaning:z.string(),notes:z.string(),
  nodes:z.array(z.object({id,label:z.string().trim().min(1).max(200),entityIds:z.array(id),position:point.nullable(),region:z.string(),kind:z.enum(['planet','system','station','unknown','location','gateway']),referenceAssetId:id,provenance:z.enum(['official','personal','interpretation'])}).passthrough()).max(1000),
  routes:z.array(z.object({id,from:id,to:id,kind:z.enum(['trade','secondary','rift']),points:z.array(point).min(2).max(500),labelAt:point.optional(),durationHours:duration.nullable(),distanceLy:z.number().finite().positive().nullable(),hyperdriveClass:z.literal(1),provenance:z.enum(['official','personal','interpretation']),referenceAssetId:id.nullable(),sources:z.array(source),notes:z.string().max(5000)}).passthrough()).max(3000),
  views:z.array(z.object({id,name:z.string(),box:z.tuple([z.number().finite().nonnegative(),z.number().finite().nonnegative(),z.number().finite().positive(),z.number().finite().positive()])}).passthrough()).min(1),
  regions:z.array(z.object({name:z.string(),points:z.array(point).min(3),labelAt:point}).passthrough())
}).passthrough();
export function checkNavigation(data,ctx){
  for(const entity of data.entities){if(entity.properties.media!==undefined){const result=placeMediaSchema.safeParse(entity.properties.media);if(!result.success)ctx.addIssue({code:'custom',message:'Médias invalides : '+entity.id});}}
  const nav=data.navigation;if(!nav)return;
  const entities=new Set(data.entities.map(e=>e.id)),nodes=new Map(nav.nodes.map(n=>[n.id,n])),ids=new Set();
  for(const node of nav.nodes){if(ids.has(node.id))ctx.addIssue({code:'custom',message:'Repère dupliqué'});ids.add(node.id);for(const id of node.entityIds)if(!entities.has(id))ctx.addIssue({code:'custom',message:'Fiche cartographique inconnue : '+id});}
  for(const route of nav.routes){
    if(ids.has(route.id)||route.from===route.to||!nodes.get(route.from)?.position||!nodes.get(route.to)?.position)ctx.addIssue({code:'custom',message:'Route ou extrémité invalide'});ids.add(route.id);
    for(const [end,p]of [[route.from,route.points[0]],[route.to,route.points.at(-1)]]){const position=nodes.get(end)?.position;if(position&&(position[0]!==p[0]||position[1]!==p[1]))ctx.addIssue({code:'custom',message:'Tracé détaché de son système'});}
  }
}
