import fs from 'node:fs';
import vm from 'node:vm';
export const source=fs.readFileSync(new URL('../../app/static/assets/js/metadata-editor.js',import.meta.url),'utf8');
export const extract=(name,next)=>source.slice(source.indexOf(`  function ${name}(`),source.indexOf(`  function ${next}(`));
export const plain=value=>JSON.parse(JSON.stringify(value));
export const vocabulary={categories:[{key:'cards',value:'Cards',aliases:['Greeting cards']},{key:'gifts',value:'Gifts'},{key:'birthday',value:'Cards > Birthday'}],
  storefront_collections:[{key:'pixel',value:'Pixel',aliases:['Pixel Art']},{key:'spring',value:'Spring'}],
  tags:[{key:'anniversary',value:'Anniversary'}],attributes:[
    {key:'attr-material',value:'Material',aliases:['Substrate'],terms:[{key:'term-card',value:'Card',aliases:['Cardstock']},{key:'term-paper',value:'Paper'}]},
    {key:'attr-lead-time',value:'Lead Time',terms:[{key:'term-3-5-days',value:'3–5 Days'}]},
  ]};
export function editorContext({shared=false,simple=true,authored={},collection={attributes:{Material:['Card']},categories:['Greeting cards'],storefront_collections:['Pixel Art']}}={}){
  let view=structuredClone(authored.attributes || {});const lists={};
  const ctx=vm.createContext({structuredClone,registry:structuredClone(vocabulary),isShared:shared,
    authored:structuredClone(authored),originalDocument:structuredClone(authored),boot:{collection:structuredClone(collection)},
    simpleProduct:()=>simple,resolvedVariation:simple,driverNames:[],explicitVariation:false,attributeReplacementActive:false,
    overrideEnabled:()=>false,renderOrganisation:()=>{},setDirty:()=>{},showFeedback:()=>{throw Error('Unexpected unresolved assignment');},
    attributeValues:()=>structuredClone(view),renderAttributes:value=>{view=structuredClone(value);},
    listValues:kind=>lists[kind] ?? ctx.authored[kind] ?? ctx.boot.collection[kind] ?? [],
    renderList:(kind,values)=>{lists[kind]=values;}});
  vm.runInContext(extract('registryMatch','recognition')+extract('attributeDriverNames','renderAttributes')+
    extract('assignmentProjection','addOrganisationValue')+extract('addOrganisationValue','setDirty'),ctx);
  return {ctx,view:()=>view,eval:code=>vm.runInContext(code,ctx),rows:kind=>plain(vm.runInContext(`assignmentProjection('${kind}')`,ctx))};
}
