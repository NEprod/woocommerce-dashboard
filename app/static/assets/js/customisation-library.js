(function () {
  "use strict";
  const root=document.querySelector('[data-customisation-library]');if(!root)return;
  const api=window.TLCCustomisations,url=root.dataset.url,select=root.querySelector('[data-library-select]');
  const name=root.querySelector('[data-library-name]'),description=root.querySelector('[data-library-description]'),archived=root.querySelector('[data-library-archived]'),json=root.querySelector('[data-library-json]'),message=root.querySelector('[data-library-message]');
  let current=null,dirty=false,busy=false;
  const designer=new api.Designer(root.querySelector('[data-customisation-designer]'),{value:{fields:{}},onChange:value=>{json.value=JSON.stringify(value,null,2);dirty=true;}});
  root.querySelector('[data-custom-template-controls]').hidden=true;
  function populate(item) {
    current=item;name.value=item?.name||'';description.value=item?.description||'';archived.checked=item?.archived||false;
    designer.value=structuredClone(item?.definition||{schema_version:2,fields:{}});designer.render();json.value=JSON.stringify(designer.get(),null,2);
    root.querySelector('[data-library-revision]').textContent=item?`Stable ID ${item.id} · revision ${item.revision}. Save creates a new pinned revision; existing assignments are unchanged.`:'New template — not saved.';
    dirty=false;
  }
  async function refresh() {
    const data=await api.request(url);select.replaceChildren(new Option('New template',''));data.items.forEach(item=>select.add(new Option(`${item.name} · r${item.revision}${item.archived?' · Archived':''}`,item.id)));if(current)select.value=current.id;
  }
  select.addEventListener('change',async()=>{try{if(dirty&&!window.confirm('Discard the unsaved template draft?')){select.value=current?.id||'';return;}populate(select.value?await api.request(`${url}/${encodeURIComponent(select.value)}`):null);}catch(error){message.textContent=error.message;}});
  for(const control of [name,description,archived])control.addEventListener('input',()=>dirty=true);
  root.querySelector('[data-library-new]').addEventListener('click',()=>{if(!dirty||window.confirm('Discard the unsaved template draft?')){populate(null);select.value='';}});
  root.querySelector('[data-library-duplicate]').addEventListener('click',()=>{const value={name:name.value+' (copy)',description:description.value,definition:designer.get()};populate(value);current=null;dirty=true;select.value='';message.textContent='Independent copy — save to assign a new stable template ID.';});
  root.querySelector('[data-library-load-json]').addEventListener('click',()=>{try{const value=JSON.parse(json.value);if(!value||Array.isArray(value)||typeof value!=='object'||'template' in value)throw new Error('Use a standalone customisation definition object.');designer.value=value;designer.render();dirty=true;message.textContent='Loaded into local draft; save validates before persistence.';}catch(error){message.textContent=error.message;}});
  root.querySelector('[data-library-save]').addEventListener('click',async()=>{
    if(busy)return;busy=true;const button=root.querySelector('[data-library-save]');button.disabled=true;
    try{
      const item=await api.request(current?`${url}/${encodeURIComponent(current.id)}`:url,{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':document.querySelector('meta[name="csrf-token"]')?.content||''},body:JSON.stringify({name:name.value,description:description.value,definition:designer.get(),archived:archived.checked,expected_revision:current?.revision})});
      populate(item);await refresh();message.textContent='Template saved locally. Previously assigned revisions and Shopify are unchanged.';
    }catch(error){message.textContent=error.message;}finally{busy=false;button.disabled=false;}
  });
  window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
  populate(null);refresh().catch(error=>message.textContent=error.message);
})();
