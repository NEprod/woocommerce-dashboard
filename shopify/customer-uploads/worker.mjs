import {imageType} from './image.mjs';

const MAX = 10_000_000, DAY = 86_400_000, enc = new TextEncoder();
const hex = bytes => Array.from(new Uint8Array(bytes), b=>b.toString(16).padStart(2,'0')).join('');
const unhex = value => Uint8Array.from(value.match(/../g), b=>parseInt(b,16));
const hash = /^[a-f0-9]{64}$/;
const fieldId = /^[a-z][a-z0-9_]{0,63}$/;
const reference = /^tlcp1_[a-f0-9]{64}$/;
const id = /^[1-9][0-9]{0,19}$/;
const createOnly = {onlyIf:new Headers({'If-None-Match':'*'})};
const headers = {'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer'};
const reply = (body,status=200,extra={}) => Response.json(body,{status,headers:{...headers,...extra}});
class Rejected extends Error { constructor(status) {super();this.status=status;} }
const demand = (ok,status=400) => {if (!ok) throw new Rejected(status);};
const gid = (kind, value) => typeof value==='string' && value.startsWith('gid://shopify/'+kind+'/') && id.test(value.slice(('gid://shopify/'+kind+'/').length));
const photo = ref => 'photos/'+ref;
const binding = ref => 'bindings/'+ref;
const mimeTypes = ['image/jpeg','image/png','image/webp'];
const HOUR = 3_600_000, SHOP_BYTES_PER_HOUR = 200_000_000;

function configured(e) {
  return e.TLC_UPLOADS && e.UPLOAD_RATE && /^[a-z0-9][a-z0-9-]*\.myshopify\.com$/.test(e.SHOP_DOMAIN||'')
    && /^https:\/\/[a-z0-9][a-z0-9.-]*$/.test(e.STOREFRONT_ORIGIN||'')
    && /^\/(apps|a|community|tools)\/[a-z0-9-]+$/.test(e.PROXY_PATH||'')
    && gid('Shop', e.SHOP_ID) && /^[A-Za-z0-9_-]{16,128}$/.test(e.SHOPIFY_CLIENT_ID||'')
    && e.SHOPIFY_CLIENT_SECRET?.length>=16 && e.UPLOAD_SIGNING_KEY?.length>=32 && e.DASHBOARD_UPLOAD_TOKEN?.length>=32
    && /^\d{1,4}$/.test(e.ABANDONED_DAYS||'') && +e.ABANDONED_DAYS>=14 && +e.ABANDONED_DAYS<=90
    && /^\d{1,4}$/.test(e.ORDER_PHOTO_DAYS||'') && +e.ORDER_PHOTO_DAYS>=7 && +e.ORDER_PHOTO_DAYS<=365;
}

async function hmac(secret, value, signature) {
  const key=await crypto.subtle.importKey('raw',enc.encode(secret),{name:'HMAC',hash:'SHA-256'},false,['sign','verify']);
  return signature ? crypto.subtle.verify('HMAC',key,unhex(signature),enc.encode(value))
    : hex(await crypto.subtle.sign('HMAC',key,enc.encode(value)));
}

async function bounded(request, max) {
  const declared=request.headers.get('Content-Length');
  if (declared!==null) demand(/^\d+$/.test(declared)&&Number(declared)<=max,413);
  demand(request.body);
  const reader=request.body.getReader();
  const timer=setTimeout(()=>{void reader.cancel().catch(()=>{});},25_000);
  const deadline=Date.now()+25_000;
  const parts=[]; let n=0;
  try {
    for (;;) {
      const {value,done}=await reader.read();
      if(done)break;
      n+=value.byteLength;
      if(n>max){await reader.cancel();throw new Rejected(413);}
      parts.push(value);
    }
  } finally {clearTimeout(timer);reader.releaseLock();}
  demand(Date.now()<deadline,408);
  demand(n && (declared===null||Number(declared)===n));
  const data=new Uint8Array(n);let offset=0;
  for(const part of parts){data.set(part,offset);offset+=part.length;}
  return data;
}

async function payload(req) {
  demand(req.headers.get('Content-Type')?.split(';')[0]==='application/json',415);
  try {return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(await bounded(req,16_384)));}
  catch(e){if(e instanceof Rejected)throw e;throw new Rejected(400);}
}

let adminSession;
async function admin(env,query,variables={}) {
  const now=Date.now(), host='https://'+env.SHOP_DOMAIN;
  if (adminSession?.env!==env || now>=adminSession.until) {
    const r=await fetch(host+'/admin/oauth/access_token',{method:'POST',redirect:'manual',
      signal:AbortSignal.timeout(4500),body:new URLSearchParams({grant_type:'client_credentials',
        client_id:env.SHOPIFY_CLIENT_ID,client_secret:env.SHOPIFY_CLIENT_SECRET})});
    demand(r.ok,503);
    const value=await r.json();
    demand(typeof value.access_token==='string' && Number.isFinite(value.expires_in),503);
    adminSession={env,token:value.access_token,until:now+Math.max(0,value.expires_in-60)*1000};
  }
  const r=await fetch(host+'/admin/api/2026-10/graphql.json',{method:'POST',redirect:'manual',signal:AbortSignal.timeout(4500),
    headers:{'Content-Type':'application/json','X-Shopify-Access-Token':adminSession.token},body:JSON.stringify({query,variables})});
  demand(r.ok,503);
  const value=await r.json();
  demand(!value.errors?.length && value.data?.shop?.id===env.SHOP_ID,503);
  return value.data;
}

async function verifyProxy(env,url,proof) {
  const q=url.searchParams, allowed=['signature','shop','path_prefix','timestamp','logged_in_customer_id',proof];
  demand(url.search.length<=1000 && [...q.keys()].every(k=>allowed.includes(k) && q.getAll(k).length===1)
    && q.get('shop')===env.SHOP_DOMAIN && q.get('path_prefix')===env.PROXY_PATH
    && /^\d{10}$/.test(q.get('timestamp')||'') && /^(?:[0-9]{1,20})?$/.test(q.get('logged_in_customer_id')??'_')
    && hash.test(q.get('signature')||''),401);
  const now=Math.floor(Date.now()/1000), timestamp=Number(q.get('timestamp'));
  demand(timestamp>=now-90 && timestamp<=now+10,401);
  const signed=[...q].filter(([k])=>k!=='signature').map(([k,v])=>k+'='+v).sort().join('');
  demand(await hmac(env.SHOPIFY_CLIENT_SECRET,signed,q.get('signature')),401);
  return timestamp;
}

async function probe(req,env,url) {
  await verifyProxy(env,url,'probe');
  demand(url.searchParams.get('probe')==='1',401);
  demand((await env.UPLOAD_RATE.limit({key:'probe:'+env.SHOP_ID})).success,429);
  const key='health/shopify-proxy', stamp=url.origin+env.PROXY_PATH;
  const last=await env.TLC_UPLOADS.head(key);
  if(last?.customMetadata?.target!==stamp || Date.now()-last.uploaded.getTime()>60_000)
    await env.TLC_UPLOADS.put(key,'ready',{customMetadata:{target:stamp}});
  return reply({proxy:'verified'});
}

async function proxy(req,env,url) {
  const timestamp=await verifyProxy(env,url,'intent');
  const q=url.searchParams;
  demand(hash.test(q.get('intent')||''),401);
  const origin=req.headers.get('Origin');
  demand(origin===null || origin===env.STOREFRONT_ORIGIN,403);
  demand((await env.UPLOAD_RATE.limit({key:'issue:'+env.SHOP_ID})).success,429);
  demand(req.headers.get('Content-Type')?.split(';')[0]==='application/json',415);
  const bytes=await bounded(req,2048);
  demand(hex(await crypto.subtle.digest('SHA-256',bytes))===q.get('intent'),401);
  let p;try {p=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));} catch {throw new Rejected(400);}
  demand(p && id.test(p.product) && id.test(p.variant) && fieldId.test(p.field)
    && hash.test(p.cart) && hash.test(p.revision) && /^[a-f0-9]{32}$/.test(p.nonce));
  demand((await env.UPLOAD_RATE.limit({key:'grant:'+p.cart})).success,429);
  const v=await admin(env,`query TLCPhotoField($variant: ID!) {shop {id}
    productVariant(id:$variant) {id product {id status metafield(namespace:"$app:tlc_customisations",key:"configuration"){type value}}}}`,
      {variant:'gid://shopify/ProductVariant/'+p.variant});
  const variant=v.productVariant,m=variant?.product?.metafield;
  demand(variant?.id==='gid://shopify/ProductVariant/'+p.variant && variant.product.id==='gid://shopify/Product/'+p.product
    && variant.product.status==='ACTIVE' && m?.type==='json',403);
  const config=JSON.parse(m.value);
  const f=config.fields?.find(f=>f.id===p.field);
  demand(config.enabled===true && config.revision===p.revision && f?.type==='file_upload'
    && Number.isInteger(f.max_bytes) && f.max_bytes>0 && f.max_bytes<=MAX
    && Array.isArray(f.image_types) && f.image_types.length && f.image_types.every(t=>mimeTypes.includes(t))
    && config.photo_upload?.origin===url.origin && config.photo_upload.path===env.PROXY_PATH,403);
  // The request body is independently signed by its digest in the proxy query.
  // Retransmission yields the same object ID, not another storage allocation.
  const ref='tlcp1_'+await hmac(env.UPLOAD_SIGNING_KEY,'object:'+q.get('intent'));
  const ticket={ref,shop:env.SHOP_ID,product:p.product,variant:p.variant,field:p.field,cart:p.cart,
    revision:p.revision,limit:f.max_bytes,types:f.image_types,exp:timestamp+180};
  const content=btoa(JSON.stringify(ticket));
  return reply({ticket:content+'.'+await hmac(env.UPLOAD_SIGNING_KEY,'upload:'+content)});
}

async function reserveBytes(env,n) {
  const key='health/hourly-upload-budget', hour=Math.floor(Date.now()/HOUR);
  for(let i=0;i<4;i++) {
    const last=await env.TLC_UPLOADS.get(key);
    const saved=last?await last.json():null;
    demand(!saved || Number.isSafeInteger(saved.hour) && Number.isSafeInteger(saved.bytes) && saved.bytes>=0,503);
    demand(!saved || saved.hour<=hour,429);
    const bytes=(saved?.hour===hour?saved.bytes:0)+n;
    demand(bytes<=SHOP_BYTES_PER_HOUR,429);
    const onlyIf=last?{etagMatches:last.etag}:createOnly.onlyIf;
    if(await env.TLC_UPLOADS.put(key,JSON.stringify({hour,bytes}),{onlyIf}))return;
  }
  throw new Rejected(429);
}

async function upload(req,env,cors) {
  demand(cors['Access-Control-Allow-Origin'],403);
  const token=req.headers.get('Authorization')?.match(/^Bearer ([a-zA-Z0-9+/=]{1,1500})\.([a-f0-9]{64})$/);
  demand(token && await hmac(env.UPLOAD_SIGNING_KEY,'upload:'+token[1],token[2]),401);
  const {exp,ref,shop,product,variant,field,cart,revision,limit,types}=JSON.parse(atob(token[1]));
  demand(shop===env.SHOP_ID && exp>=Date.now()/1000 && reference.test(ref),401);
  demand((await env.UPLOAD_RATE.limit({key:'cart:'+cart})).success,429);
  demand(types.includes(req.headers.get('Content-Type')),415);
  demand(!await env.TLC_UPLOADS.head(photo(ref)),409);
  const data=await bounded(req,limit);
  const type=imageType(data);
  demand(type && type===req.headers.get('Content-Type'),415);
  await reserveBytes(env,data.byteLength);
  demand(exp>=Date.now()/1000,401);
  const stored=await env.TLC_UPLOADS.put(photo(ref),data,{...createOnly,httpMetadata:{contentType:type},
    customMetadata:{shop,product,variant,field,cart,revision}});
  demand(stored,409);
  return reply({ref},201,cors);
}

async function serviceAuth(req,env) {
  // WebCrypto comparison avoids a data-dependent string comparison on a bearer.
  const supplied=req.headers.get('Authorization')||'';
  demand(supplied.startsWith('Bearer ') && supplied.length<=512
    && await hmac(env.UPLOAD_SIGNING_KEY,supplied,await hmac(env.UPLOAD_SIGNING_KEY,'Bearer '+env.DASHBOARD_UPLOAD_TOKEN)),401);
}

async function staff(req,env,path) {
  await serviceAuth(req,env);
  if(path==='/private/health' && req.method==='GET') {
    const probe=await env.TLC_UPLOADS.head('health/shopify-proxy');
    demand(probe?.customMetadata?.target===new URL(req.url).origin+env.PROXY_PATH
      && Date.now()-probe.uploaded.getTime()<DAY*7,503);
    const data=await admin(env,'query TLCUploadReadiness {shop {id} currentAppInstallation {accessScopes {handle}}}');
    const scopes=data.currentAppInstallation?.accessScopes?.map(s=>s.handle);
    demand(scopes?.includes('read_products') && scopes.includes('write_app_proxy'),503);
    return reply({ready:true,shop:env.SHOP_DOMAIN,store:env.SHOP_ID,path:env.PROXY_PATH});
  }
  demand(req.method==='POST' && ['/private/bind','/private/photo','/private/delete'].includes(path),404);
  const p=await payload(req);
  demand(p && p.shop===env.SHOP_ID && gid('Order',p.order));
  const same = (meta,line) => meta?.shop===p.shop && meta.order===p.order && meta.line===line;
  if(path==='/private/bind') {
    demand(hash.test(p.cart) && Array.isArray(p.items) && p.items.length>0 && p.items.length<=10);
    const seen=new Set(), slots=new Set(), accepted=[];
    for(const i of p.items) {
      demand(i && reference.test(i.ref) && gid('LineItem',i.line) && id.test(i.product)
        && id.test(i.variant) && fieldId.test(i.field) && hash.test(i.revision) && !seen.has(i.ref)
        && !slots.has(i.line+':'+i.field));
      seen.add(i.ref);slots.add(i.line+':'+i.field);
    }
    for(const i of p.items) {
      const object=await env.TLC_UPLOADS.head(photo(i.ref)), m=object?.customMetadata;
      if(!m || m.shop!==p.shop || m.cart!==p.cart || ['product','variant','field','revision'].some(k=>m[k]!==i[k]))continue;
      const key=binding(i.ref), old=await env.TLC_UPLOADS.head(key);
      if(old){if(same(old.customMetadata,i.line))accepted.push(i.ref);continue;}
      if(Date.now()-object.uploaded.getTime()>=+env.ABANDONED_DAYS*DAY)continue;
      const claim={shop:p.shop,order:p.order,line:i.line};
      if(await env.TLC_UPLOADS.put(key,'bound',{...createOnly,customMetadata:claim})
        || same((await env.TLC_UPLOADS.head(key))?.customMetadata,i.line))accepted.push(i.ref);
    }
    return reply({accepted});
  }
  demand(reference.test(p.ref) && gid('LineItem',p.line));
  const receipt=await env.TLC_UPLOADS.head(binding(p.ref));
  demand(same(receipt?.customMetadata,p.line),404);
  if(path==='/private/delete'){
    // A just-issued upload ticket must expire before deleting the replay fence.
    demand(Date.now()-receipt.uploaded.getTime()>=180_000,409);
    await env.TLC_UPLOADS.delete(photo(p.ref));await env.TLC_UPLOADS.delete(binding(p.ref));return reply({deleted:true});
  }
  demand(Date.now()-receipt.uploaded.getTime()<+env.ORDER_PHOTO_DAYS*DAY,404);
  const object=await env.TLC_UPLOADS.get(photo(p.ref)), type=object?.httpMetadata?.contentType;
  demand(object && mimeTypes.includes(type) && object.size<=MAX,404);
  return new Response(object.body,{headers:{...headers,'Content-Type':type,'Content-Length':String(object.size)}});
}

export async function cleanup(env,now=Date.now()) {
  if(!configured(env))return;
  const cursorKey='health/cleanup-cursor';
  const last=await env.TLC_UPLOADS.get(cursorKey);
  const startAfter=(await last?.text())||undefined;
  const page=await env.TLC_UPLOADS.list({prefix:'photos/',limit:10,startAfter});
  for(const object of page.objects) {
    const ref=object.key.slice(7);
    if(!reference.test(ref))continue;
    const key=binding(ref), claim=await env.TLC_UPLOADS.head(key);
    if(!claim) {
      if(now-object.uploaded.getTime()<+env.ABANDONED_DAYS*DAY)continue;
      // The same conditional-create key arbitrates cleanup versus order import.
      if(!await env.TLC_UPLOADS.put(key,'abandoned',createOnly))continue;
    } else if(claim.customMetadata?.order && now-claim.uploaded.getTime()<+env.ORDER_PHOTO_DAYS*DAY)continue;
    await env.TLC_UPLOADS.delete(object.key);
    await env.TLC_UPLOADS.delete(key);
  }
  await env.TLC_UPLOADS.put(cursorKey,page.truncated?page.objects.at(-1).key:'');
  return page.truncated;
}

export default {
  async fetch(req,env) {
    const url=new URL(req.url);
    const cors=req.headers.get('Origin')===env.STOREFRONT_ORIGIN && env.STOREFRONT_ORIGIN
      ? {'Access-Control-Allow-Origin':env.STOREFRONT_ORIGIN,'Vary':'Origin'}:{};
    try {
      demand(configured(env),503);
      if(url.pathname.startsWith('/private/'))return await staff(req,env,url.pathname);
      // Shopify forwards the storefront proxy root with a trailing slash.
      if(url.pathname==='/proxy' || url.pathname==='/proxy/') {
        if(req.method==='GET')return await probe(req,env,url);
        if(req.method==='POST')return await proxy(req,env,url);
      }
      if(url.pathname==='/upload') {
        if(req.method==='OPTIONS' && cors['Access-Control-Allow-Origin'])return new Response(null,{status:204,
          headers:{...headers,...cors,'Access-Control-Allow-Methods':'POST','Access-Control-Allow-Headers':'Authorization, Content-Type'}});
        if(req.method==='POST')return await upload(req,env,cors);
      }
      return reply({error:'Not found'},404);
    }catch(e){
      const status=e instanceof Rejected?e.status:503;
      return reply({error:status===413?'Image exceeds the allowed file size.':status===415?'Choose a valid JPEG, PNG or WebP image.':
        status===429?'Too many upload attempts. Wait a minute and try again.':'Photo service could not complete this request.'},
        status,url.pathname==='/upload'?cors:{});
    }
  },
  scheduled(_event,env,ctx){ctx.waitUntil((async()=>{for(let page=0;page<12;page++)if(!await cleanup(env))break;})());}
};
