import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash,createHmac} from 'node:crypto';
import worker,{cleanup} from './worker.mjs';
import {imageType} from './image.mjs';

const host='https://photos.example.test', origin='https://shop.example.test', path='/apps/tlc-customer-uploads';
const shop='fictional.myshopify.com', store='gid://shopify/Shop/42', order='gid://shopify/Order/30';
const rev='a'.repeat(64), cart='b'.repeat(64), field='photo', line='gid://shopify/LineItem/50';
const secret='test-client-secret-not-real', token='fictional-dash-token-not-a-real-secret';
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC','base64');
const webp=Buffer.from('UklGRiIAAABXRUJQVlA4IBYAAAAwAQCdASoBAAEADsD+JaQAA3AAAAAA','base64');
const jpeg=Buffer.from('ffd8ffc0000b080001000101011100ffda0008010100003f003fffd9','hex');
const hash=value=>createHash('sha256').update(value).digest('hex');
const sign=(key,value)=>createHmac('sha256',key).update(value).digest('hex');
const days=n=>n*86_400_000;

class Bucket {
  data=new Map();
  async head(key){const object=this.data.get(key);return object?{...object}:null;}
  async get(key){const object=await this.head(key);return object?{...object,body:object.bytes,
    text:async()=>new TextDecoder().decode(object.bytes),json:async()=>JSON.parse(new TextDecoder().decode(object.bytes))}:null;}
  async put(key,data,options={}){
    if(options.onlyIf?.get?.('If-None-Match')==='*'&&this.data.has(key))return null;
    if(options.onlyIf?.etagMatches&&options.onlyIf.etagMatches!==this.data.get(key)?.etag)return null;
    const bytes=typeof data==='string'?new TextEncoder().encode(data):Uint8Array.from(data);
    const object={key,bytes,etag:hash(bytes),size:bytes.length,uploaded:new Date(),customMetadata:options.customMetadata,httpMetadata:options.httpMetadata};
    this.data.set(key,object);return object;
  }
  async delete(key){this.data.delete(key);}
  async list({prefix,limit,startAfter}){
    const objects=[...this.data.values()].filter(o=>o.key.startsWith(prefix)&&(!startAfter||o.key>startAfter)).sort((a,b)=>a.key.localeCompare(b.key));
    return {objects:objects.slice(0,limit),truncated:objects.length>limit};
  }
}
function environment(t,{redirectAt}={}){
  const env={TLC_UPLOADS:new Bucket(),UPLOAD_RATE:{limit:async()=>({success:true})},SHOP_DOMAIN:shop,SHOP_ID:store,
    STOREFRONT_ORIGIN:origin,PROXY_PATH:path,SHOPIFY_CLIENT_ID:'fictional-client-id',
    SHOPIFY_CLIENT_SECRET:secret,UPLOAD_SIGNING_KEY:'fictional-separate-upload-signing-key',DASHBOARD_UPLOAD_TOKEN:token,
    ABANDONED_DAYS:'30',ORDER_PHOTO_DAYS:'90'};
  t.mock.method(globalThis,'fetch',async (target,init)=>{
    assert.ok(target.startsWith('https://'+shop+'/admin/'));
    assert.equal(init.redirect,'manual');
    if(redirectAt && target.endsWith(redirectAt))return Response.redirect('https://unexpected.example.test/',307);
    if(target.endsWith('/access_token'))return Response.json({access_token:'fake',expires_in:3600});
    const {variables}=JSON.parse(init.body);
    if(!variables.variant)return Response.json({data:{shop:{id:store},currentAppInstallation:{accessScopes:[{handle:'read_products'},{handle:'write_app_proxy'}]}}});
    const config={enabled:true,revision:rev,photo_upload:{origin:host,path},
      fields:[{id:field,type:'file_upload',max_bytes:10_000_000,image_types:['image/png']}]};
    return Response.json({data:{shop:{id:store},productVariant:{id:'gid://shopify/ProductVariant/200',
      product:{id:'gid://shopify/Product/100',status:'ACTIVE',metafield:{type:'json',value:JSON.stringify(config)}}}}});
  });
  return env;
}
function issue(env, {nonce='c'.repeat(32), when=0, patch={}, tamper=false, signature=true, route='/proxy'}={}){
  const body=JSON.stringify({nonce,product:'100',variant:'200',field,cart,revision:rev,...patch});
  const q=new URLSearchParams({intent:hash(body),shop,path_prefix:path,logged_in_customer_id:'',
    timestamp:String(Math.floor(Date.now()/1000)+when)});
  q.set('signature',signature?sign(secret,[...q].map(([k,v])=>k+'='+v).sort().join('')):'0'.repeat(64));
  return worker.fetch(new Request(host+route+'?'+q,{method:'POST',body:tamper?'{}':body,
    headers:{'Content-Type':'application/json','Origin':origin}}),env);
}
const put=(env,ticket,body=png,mime='image/png',from=origin)=>worker.fetch(new Request(host+'/upload',{
  method:'POST',headers:{Authorization:'Bearer '+ticket,'Content-Type':mime,Origin:from},body}),env);
const service=(env,route,data,auth=token)=>worker.fetch(new Request(host+'/private/'+route,{
  method:data?'POST':'GET',headers:{Authorization:'Bearer '+auth,'Content-Type':'application/json'},
  ...(data?{body:JSON.stringify(data)}:{})}),env);
const item=(ref,patch={})=>({ref,product:'100',variant:'200',field,line,revision:rev,...patch});

test('signed short-lived Shopify authority, exact field/variant, bounded upload and replay',async t=>{
  const env=environment(t);
  assert.equal((await issue(env,{signature:false})).status,401);
  assert.equal((await issue(env,{when:-91})).status,401);
  assert.equal((await issue(env,{tamper:true})).status,401);
  assert.equal((await issue(env,{patch:{field:'not_configured'}})).status,403);
  assert.equal((await issue(env,{patch:{variant:'201'}})).status,403);
  const {ticket}=await (await issue(env)).json();
  const content=JSON.parse(atob(ticket.split('.')[0]));content.exp=1;
  const expired=btoa(JSON.stringify(content));
  assert.equal((await put(env,expired+'.'+sign(env.UPLOAD_SIGNING_KEY,'upload:'+expired))).status,401);
  assert.equal((await put(env,ticket,png,'image/png','https://other.example.test')).status,403);
  assert.equal((await put(env,ticket,png,'image/jpeg')).status,415);
  assert.equal((await put(env,ticket,Buffer.alloc(10_000_001))).status,413);
  assert.equal((await put(env,ticket,png.subarray(0,-1))).status,415);
  assert.equal(env.TLC_UPLOADS.data.size,0);
  const writes=await Promise.all([put(env,ticket),put(env,ticket)]);
  assert.deepEqual(writes.map(r=>r.status).sort(),[201,409]);
  const {ref}=await writes.find(r=>r.status===201).json();
  assert.match(ref,/^tlcp1_[0-9a-f]{64}$/);
  const retry=await (await issue(env)).json();
  assert.equal((await put(env,retry.ticket)).status,409);
  assert.equal([...env.TLC_UPLOADS.data.keys()].filter(k=>k.startsWith('photos/')).length,1);
  await env.TLC_UPLOADS.put('health/hourly-upload-budget',JSON.stringify({hour:Math.floor(Date.now()/3_600_000),bytes:200_000_000}));
  const extra=await (await issue(env,{nonce:'d'.repeat(32)})).json();
  assert.equal((await put(env,extra.ticket)).status,429);
});

test('distinct references, idempotent order binding, cart and line ownership, private deletion',async t=>{
  const env=environment(t);
  const photos=await Promise.all(['c','d'].map(async n=>{
    const {ticket}=await (await issue(env,{nonce:n.repeat(32)})).json();
    return (await (await put(env,ticket)).json()).ref;
  }));
  assert.notEqual(...photos);
  const items=[item(photos[0]),item(photos[1],{line:'gid://shopify/LineItem/51'})];
  const bind={shop:store,order,cart,items};
  const request={shop:store,order,line,ref:photos[0]};
  assert.deepEqual(await (await service(env,'bind',{...bind,cart:'f'.repeat(64)})).json(),{accepted:[]});
  assert.deepEqual(await (await service(env,'bind',{...bind,items:[item(photos[0],{field:'other'})]})).json(),{accepted:[]});
  assert.equal((await service(env,'photo',request)).status,404);
  assert.equal((await service(env,'bind',{...bind,items:[items[0],items[0]]})).status,400);
  for(let n=0;n<2;n++)assert.deepEqual(await (await service(env,'bind',bind)).json(),{accepted:photos});
  assert.equal((await service(env,'photo',request,'incorrect')).status,401);
  assert.equal((await service(env,'photo',{...request,ref:photos[1]})).status,404);
  assert.equal((await service(env,'photo',{...request,order:'gid://shopify/Order/31'})).status,404);
  assert.equal((await service(env,'photo',{...request,ref:'../private/secret'})).status,400);
  const response=await service(env,'photo',request);
  assert.equal(response.headers.get('Cache-Control'),'private, no-store');
  assert.deepEqual(Buffer.from(await response.arrayBuffer()),png);
  assert.equal((await worker.fetch(new Request(host+'/photos/'+photos[0]),env)).status,404);
  assert.equal((await service(env,'delete',request)).status,409);
  env.TLC_UPLOADS.data.get('bindings/'+photos[0]).uploaded=new Date(Date.now()-200_000);
  assert.equal((await service(env,'delete',request)).status,200);
  assert.equal((await service(env,'photo',request)).status,404);
});

test('cleanup is bounded, protects completed orders, expires retained photos and arbitrates a bind race',async t=>{
  const env=environment(t), now=Date.now();
  const refs=['e','f','1','2'].map(x=>'tlcp1_'+x.repeat(64));
  for(const ref of refs){
    await env.TLC_UPLOADS.put('photos/'+ref,png);
    env.TLC_UPLOADS.data.get('photos/'+ref).uploaded=new Date(now-days(31));
  }
  for(const ref of refs.slice(1,3))await env.TLC_UPLOADS.put('bindings/'+ref,'bound',{customMetadata:{shop:store,order,line}});
  env.TLC_UPLOADS.data.get('bindings/'+refs[2]).uploaded=new Date(now-days(91));
  // Another worker claimed the object after cleanup's head, before its put.
  const put=env.TLC_UPLOADS.put.bind(env.TLC_UPLOADS);
  env.TLC_UPLOADS.put=async (key,data,opts)=>{
    if(key==='bindings/'+refs[3]&&data==='abandoned')await put(key,'bound',{customMetadata:{shop:store,order,line}});
    return put(key,data,opts);
  };
  await cleanup(env,now);
  assert.deepEqual(refs.map(r=>env.TLC_UPLOADS.data.has('photos/'+r)),[false,true,false,true]);
});

test('Shopify proxy root with or without a slash uses the same signed handlers',async t=>{
  const env=environment(t);
  function probe(route,age=0,valid=true,settings=env) {
    const q=new URLSearchParams({probe:'1',shop,path_prefix:path,logged_in_customer_id:'',
      timestamp:String(Math.floor(Date.now()/1000)-age)});
    q.set('signature',valid?sign(secret,[...q].map(([k,v])=>k+'='+v).sort().join('')):'0'.repeat(64));
    return worker.fetch(new Request(host+route+'?'+q),settings);
  }
  for(const route of ['/proxy','/proxy/']){
    assert.equal((await probe(route,0,false)).status,401);
    assert.equal((await probe(route,91)).status,401);
    assert.equal((await probe(route,0,true,{...env,SHOPIFY_CLIENT_SECRET:undefined})).status,503);
    assert.equal((await issue(env,{route,signature:false})).status,401);
    assert.equal((await issue(env,{route})).status,200);
  }
  assert.equal(env.TLC_UPLOADS.data.size,0);
  for(const route of ['/proxy','/proxy/']){
    const response=await probe(route);
    assert.equal(response.status,200);
    assert.equal(await response.text(),'{"proxy":"verified"}');
  }
  assert.equal((await probe('/proxy/unrelated')).status,404);
});

test('disabled/failed configuration and rate gate fail closed',async t=>{
  const env=environment(t);
  assert.equal((await service(env,'health')).status,503);
  await env.TLC_UPLOADS.put('health/shopify-proxy','ready',{customMetadata:{target:host+path}});
  assert.equal((await service(env,'health')).status,200);
  env.TLC_UPLOADS.data.get('health/shopify-proxy').uploaded=new Date(Date.now()-days(8));
  assert.equal((await service(env,'health')).status,503);
  assert.equal((await service({...env,TLC_UPLOADS:null},'health')).status,503);
  assert.equal((await service(env,'health',null,'wrong')).status,401);
  env.UPLOAD_RATE.limit=async()=>({success:false});
  assert.equal((await issue(env)).status,429);
  assert.equal(await cleanup({...env,SHOP_ID:''}),undefined);
  assert.equal(env.TLC_UPLOADS.data.size,1);
});

for(const endpoint of ['/access_token','/graphql.json']) {
  test('Shopify credentials are not forwarded through a redirect at '+endpoint,async t=>{
    const env=environment(t,{redirectAt:endpoint});
    const response=await issue(env);
    assert.equal(response.status,503);
    assert.equal((await response.json()).ticket,undefined);
    const destinations=globalThis.fetch.mock.calls.map(({arguments:[url]})=>new URL(url).pathname);
    assert.deepEqual(destinations,endpoint==='/access_token'
      ? ['/admin/oauth/access_token']
      : ['/admin/oauth/access_token','/admin/api/2026-10/graphql.json']);
  });
}

test('container checks accept only the three bounded raster types',()=>{
  assert.equal(imageType(png),'image/png');
  assert.equal(imageType(webp),'image/webp');
  assert.equal(imageType(jpeg),'image/jpeg');
  for(const bytes of [Buffer.alloc(32),png.subarray(0,-4),jpeg.subarray(0,-1),webp.subarray(0,-1),
    Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"/>')])assert.equal(imageType(bytes),null);
  const corrupt=Buffer.from(png);corrupt[44]^=1;
  assert.equal(imageType(corrupt),null);
});
