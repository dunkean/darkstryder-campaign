const inNode=typeof process!=='undefined' && Boolean(process.versions?.node);
const {marked}=await import(inNode?'marked':'/vendor/marked.js');
const {default:purify}=await import(inNode?'dompurify':'/vendor/purify.js');
const DOMPurify=inNode?purify(window):purify;
export function markdown(text,base=''){
  const container=document.createElement('div');container.innerHTML=DOMPurify.sanitize(marked.parse(text||''));
  if(base)container.querySelectorAll('img').forEach(img=>{const src=img.getAttribute('src');if(src&&!/^(https?:|data:|\/)/.test(src))img.src=new URL(src,new URL(base,location.origin)).href;});
  container.querySelectorAll('a').forEach(a=>{a.target='_blank';a.rel='noopener';});
  return container.innerHTML;
}
