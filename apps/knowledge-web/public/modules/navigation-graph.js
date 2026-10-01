export function formatHours(hours){
  if(hours===null||hours===undefined)return 'inconnue';
  const minutes=Math.round(hours*60),days=Math.floor(minutes/1440),h=Math.floor(minutes%1440/60),m=minutes%60;
  return [days?`${days} j`:'',h?`${h} h`:'',m?`${m} min`:''].filter(Boolean).join(' ')||'0 h';
}
export function durationLabel(duration,factor=1){
  if(!duration)return 'Durée inconnue';
  const min=formatHours(duration.min*factor),max=formatHours(duration.max*factor);
  return duration.min===duration.max?min:`${min} – ${max}`;
}
// Find the quickest *documented* route by lower duration bound. The upper bound
// is the sum on that SAME path, not a second optimum or a guaranteed arrival.
export function findItinerary(nav,from,to,{factor=1,secondary=true,rift=true}={}){
  if(!Number.isFinite(factor)||factor<=0)throw Error('Classe d’hyperpropulsion invalide');
  const nodes=new Set(nav.nodes.map(n=>n.id));if(!nodes.has(from)||!nodes.has(to))return null;
  const dist=new Map([[from,0]]),previous=new Map(),pending=new Set(nodes);
  const edges=nav.routes.filter(r=>r.durationHours&&(secondary||r.kind!=='secondary')&&(rift||r.kind!=='rift'));
  while(pending.size){let current=null;for(const id of pending)if(dist.has(id)&&(current===null||dist.get(id)<dist.get(current)))current=id;
    if(current===null)break;pending.delete(current);if(current===to)break;
    for(const edge of edges){const next=edge.from===current?edge.to:edge.to===current?edge.from:null;if(!next||!pending.has(next))continue;const cost=dist.get(current)+edge.durationHours.min;
      if(!dist.has(next)||cost<dist.get(next)){dist.set(next,cost);previous.set(next,{id:current,edge});}}
  }
  if(!dist.has(to))return null;
  const routeIds=[],nodeIds=[to];let max=0,current=to;
  while(current!==from){const step=previous.get(current);if(!step)return null;routeIds.unshift(step.edge.id);nodeIds.unshift(step.id);max+=step.edge.durationHours.max;current=step.id;}
  return {routeIds,nodeIds,durationHours:{min:dist.get(to)*factor,max:max*factor},containsRift:routeIds.some(id=>edges.find(e=>e.id===id).kind==='rift')};
}
