(function () {
  'use strict';
  const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
  async function api(url, value) {
    const response = await fetch(url, {method:value === undefined ? 'GET':'POST',credentials:'same-origin',
      headers:value === undefined ? {} : {'Content-Type':'application/json','X-CSRFToken':csrf()},
      ...(value === undefined ? {} : {body:JSON.stringify(value)})});
    const result = await response.json().catch(() => ({error:'The server returned an unreadable response. Your local edits are retained.'}));
    if (!response.ok) throw new Error(result.error || 'Request unavailable. Your local edits are retained.');
    return result;
  }
  const element = (tag,text,cls) => { const n=document.createElement(tag); if(text!=null)n.textContent=text; if(cls)n.className=cls; return n; };
  const button = (text,run) => {const n=element('button',text,'btn btn-sm btn-outline-primary');n.type='button';n.addEventListener('click',run);return n;};
  const clone = value => structuredClone(value || {});
  const merge = (a,b) => {const r=clone(a);Object.entries(b || {}).forEach(([k,v])=>{r[k]=v && typeof v==='object' && !Array.isArray(v) ? merge(r[k],v):structuredClone(v);});return r;};
  const readPath = (o,path) => path.split('.').reduce((v,k)=>v?.[k],o);
  function setPath(o,path,value) { const parts=path.split('.');let p=o;parts.slice(0,-1).forEach(k=>{p[k]=p[k] || {};p=p[k];});if(value===undefined)delete p[parts.at(-1)];else p[parts.at(-1)]=value; }
  const numeric = new Set(['tare_g','max_contents_g','max_gross_g','clearance_cm','protection_weight_g']);
  function packageData(form) {
    const result={};
    form.querySelectorAll('[name]').forEach(n=>{
      if(n.name==='csrf_token')return;
      let v=n.type==='checkbox' ? n.checked : n.value.trim();
      if(n.name==='protection')v=v.split(',').map(s=>s.trim()).filter(Boolean);
      if(['cost_pence','max_quantity'].includes(n.name))v=v==='' ? null:Number(v);
      if(numeric.has(n.name) || n.name.includes('_cm.'))v=v==='' ? null:v;
      setPath(result,n.name,v);
    });return result;
  }
  function populatePackage(form,data) {
    form.querySelectorAll('[name]').forEach(n=>{let value=readPath(data,n.name);if(n.name==='protection')value=(value || []).join(', ');if(n.type==='checkbox')n.checked=value===true;else n.value=value ?? '';});
  }
  function output(target,result) {
    target.replaceChildren();target.append(element('h2',result.state?.replaceAll('_',' ') || 'Packing result'));
    target.append(element('p',result.notice || 'Local packing only. No Shopify checkout rate has been changed.'));
    (result.warnings || []).forEach(v=>target.append(element('p',v,'alert alert-warning')));
    (result.parcels || []).forEach((p,index)=>{
      const card=element('article',null,'app-card');card.append(element('h3',`Parcel ${index+1}: ${p.package_name}`));
      card.append(element('p',`${['length','width','height'].map(k=>p.external_cm[k]).join(' × ')} cm · ${p.gross_weight_g} g gross · ${p.fit} fit · ${p.postal_category.replaceAll('_',' ')}`));
      const list=element('ul');const counts=new Map();p.items.forEach(i=>{const key=i.identity+'|'+i.sku;const row=counts.get(key) || {title:i.title,sku:i.sku,count:0};row.count++;counts.set(key,row);});
      counts.forEach(v=>list.append(element('li',`${v.title} · ${v.sku || ''} × ${v.count}`)));card.append(list);target.append(card);
    });
    (result.quotes || []).forEach(q=>target.append(element('p',`${q.name}: ${q.available ? '£'+(q.total_pence/100).toFixed(2):'Unavailable'} — ${q.reason} Source: ${q.source}`)));
    if(result.alternatives?.length){const details=element('details');details.append(element('summary','Alternative feasible plans'));
      result.alternatives.forEach(p=>details.append(element('p',p.parcels.map(parcel=>`${parcel.package_name}: ${parcel.gross_weight_g} g`).join(' + '))));target.append(details);}
    if(result.rejected_packages?.length){const details=element('details');details.append(element('summary','Rejected packages / measurements needed'));result.rejected_packages.forEach(p=>details.append(element('p',`${p.name}: ${p.reason}`)));target.append(details);}
    if(result.record_id)target.append(element('small','Retained local plan '+result.record_id));
  }
  async function inlinePackage(select,onSelect) {
    const template=document.getElementById('package-modal-template');
    if(!template)throw new Error('Package editor unavailable; save your draft before opening Packaging separately.');
    const returnFocus=document.activeElement,returnX=window.scrollX,returnY=window.scrollY;
    const dialog=template.content.firstElementChild.cloneNode(true);document.body.append(dialog);
    const form=dialog.querySelector('form'),feedback=form.querySelector('[data-package-feedback]');
    const close=()=>{dialog.close();dialog.remove();returnFocus?.focus({preventScroll:true});window.scrollTo(returnX,returnY);};
    dialog.querySelector('[data-package-cancel]').addEventListener('click',close);
    dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
    form.addEventListener('submit',async event=>{event.preventDefault();const submit=form.querySelector('button[type="submit"],button:not([type])');submit.disabled=true;
      try{const saved=await api('/packaging/new',packageData(form));await onSelect(saved);close();}
      catch(error){feedback.textContent=error.message;}finally{submit.disabled=false;}});
    dialog.showModal();form.querySelector('[name="name"]').focus();
  }
  const definitions=[
    ['packed_dimensions_cm.length','Packed length (cm)','number'],['packed_dimensions_cm.width','Packed width (cm)','number'],['packed_dimensions_cm.height','Packed height (cm)','number'],
    ['weight_g','Individual shipping weight (g)','number'],['measured','Packed size physically measured','boolean'],
    ['orientation','Permitted orientation',['fixed','flat','any']],['classification','Product class',['flat','rigid','general','rollable']],
    ['clearance_cm','Protective clearance each face (cm)','number'],['protection_weight_g','Protection per item (g)','number'],
    ['fragile','Fragile','boolean'],['may_share','May share a parcel','boolean'],['ship_separately','Must ship separately','boolean'],
    ['may_stack','May stack with full support','boolean'],['stack_limit','Maximum stack layers','integer'],['rollable','Rolling permitted (manual only)','boolean'],
    ['requirements','Required protection (comma separated)','list'],['selected_package_id','Selected solo package','package'],['override_reason','Selection / override reason','text']
  ];
  class Editor {
    constructor(root,{value,shared,onChange,getDocument}={}) {
      this.root=root;this.value=clone(value);this.shared=clone(shared);this.onChange=onChange || (()=>{});this.getDocument=getDocument;
      this.controls=[];this.render();
    }
    render() {
      this.root.replaceChildren();const details=element('details');details.append(element('summary','Packed measurements, protection & solo assignment'));const grid=element('div',null,'editor-field-grid');details.append(grid);this.root.append(details);
      definitions.forEach(([path,label,kind])=>{
        const wrap=element('div',null,'guided-field');const title=element('label',label);const inherit=element('select',null,'form-select');inherit.setAttribute('aria-label',label+' source');inherit.add(new Option('Inherit / unspecified','inherit'));inherit.add(new Option('Set this value','own'));
        const own=readPath(this.value,path),base=readPath(this.shared,path);inherit.value=own===undefined?'inherit':'own';
        const input=element(Array.isArray(kind) || ['boolean','package'].includes(kind)?'select':'input',null,'form-control');input.setAttribute('aria-label',label);input.dataset.packagingPath=path;
        if(input.tagName==='SELECT'){
          input.className='form-select';input.add(new Option(kind==='package'?'No selected package':'Choose value',''));
          const options=kind==='boolean'?[['true','Yes'],['false','No']]:Array.isArray(kind)?kind.map(v=>[v,v]):[];
          options.forEach(([v,t])=>input.add(new Option(t,v)));
        }else{input.type=['number','integer'].includes(kind)?'number':'text';if(input.type==='number'){input.min='0';input.step=kind==='integer'?'1':'any';}}
        const shown=own===undefined?base:own;input.value=kind==='list'?(shown || []).join(', '):shown==null?'':String(shown);input.disabled=inherit.value==='inherit';
        title.append(inherit,input);wrap.append(title);grid.append(wrap);
        const row={path,kind,input,inherit,original:own};this.controls.push(row);
        const changed=()=>{this.onChange();this.queueRecommendation?.();};
        inherit.addEventListener('change',()=>{input.disabled=inherit.value==='inherit';changed();});input.addEventListener('input',changed);input.addEventListener('change',changed);
        if(kind==='package'){
          const refresh=async selected=>{const choices=await api('/packaging/choices');const wanted=selected ?? own ?? base;input.replaceChildren(new Option('No selected package',''));choices.packages.forEach(p=>input.add(new Option(`${p.name} — ${p.readiness}`,p.id)));if(wanted&&!choices.packages.some(p=>p.id===wanted))input.add(new Option('Unavailable / archived package — resolve assignment',wanted));input.value=wanted || '';};
          refresh().catch(()=>wrap.append(element('p','Package choices unavailable. Existing assignment is retained.')));
          wrap.append(button('+ Add new package',()=>inlinePackage(input,async saved=>{await refresh(saved.id);inherit.value='own';input.disabled=false;this.onChange();}).catch(e=>wrap.append(element('p',e.message)))));
        }
      });
      let comparison=0;
      const result=element('div');const recommend=button('Recalculate recommendation',async()=>{
        const generation=++comparison;
        try{recommend.disabled=true;const documentValue=this.getDocument?this.getDocument():{packaging:merge(this.shared,this.get())};const answer=await api('/packaging/recommend',{document:documentValue});output(result,answer);
          if(generation!==comparison){result.replaceChildren();return;}
          if(answer.state==='verified' && answer.parcels?.length===1){result.append(button('Use recommendation',()=>{if(generation!==comparison)return;const row=this.controls.find(c=>c.path==='selected_package_id');row.inherit.value='own';row.input.disabled=false;row.input.value=answer.parcels[0].package_id;this.onChange();}));}
        }catch(e){if(generation===comparison)result.textContent=e.message;}finally{recommend.disabled=false;if(generation!==comparison)this.queueRecommendation();}
      });details.append(recommend,result);
      let timer;
      this.queueRecommendation=()=>{comparison++;clearTimeout(timer);timer=setTimeout(()=>{
        if(!recommend.disabled && details.isConnected)recommend.click();
      },400);};
      details.append(element('p','Assignment is local-only pending a supported reviewed Shopify package readback. Recommendations never overwrite manual selections. Rolling/soft-pack deformation requires manual handling.','field-helper'));
    }
    setEnabled(enabled) {
      this.controls.forEach(row=>{row.inherit.disabled=!enabled;row.input.disabled=!enabled || row.inherit.value==='inherit';});
      this.root.querySelectorAll('button').forEach(node=>{node.disabled=!enabled;});
    }
    get() {
      const result=clone(this.value);
      this.controls.forEach(({path,kind,input,inherit,original})=>{
        if(inherit.value==='inherit'){setPath(result,path,undefined);return;}
        let v=input.value;
        if(v==='' && kind!=='package'){if(original===undefined)setPath(result,path,undefined);return;}
        if(kind==='boolean')v=v==='true';else if(kind==='integer')v=Number(v);else if(kind==='list')v=v.split(',').map(s=>s.trim()).filter(Boolean);else if(kind==='package'&&v==='')v=null;
        // Preserve original representation when untouched (including numbers).
        if(original!==undefined && String(original)===String(v))v=original;
        setPath(result,path,v);
      });
      if(result.packed_dimensions_cm&&!Object.keys(result.packed_dimensions_cm).length)delete result.packed_dimensions_cm;
      return Object.keys(result).length?result:undefined;
    }
  }
  window.TLCPackaging={Editor,api,output,merge,packageData,populatePackage};

  const form=document.querySelector('[data-package-form]');
  if(form){const data=JSON.parse(document.getElementById('package-bootstrap').textContent || 'null') || {};populatePackage(form,data);
    const copy=document.querySelector('[data-native-package-copy]');if(copy)copy.textContent=JSON.stringify({name:data.name,type:data.type,dimensions:data.external_cm,weight_g:data.tare_g},null,2);
    form.addEventListener('submit',async event=>{event.preventDefault();const feedback=form.querySelector('[data-package-feedback]');try{const result=await api(form.dataset.saveUrl,{...packageData(form),version:data.version});window.location.assign(result.url);}catch(e){feedback.textContent=e.message;}});
    form.querySelector('[data-package-duplicate]')?.addEventListener('click',async()=>{try{const result=await api(`/packaging/${data.id}/duplicate`,{});window.location.assign(result.url);}catch(e){form.querySelector('[data-package-feedback]').textContent=e.message;}});
  }
  document.querySelector('[data-package-archive]')?.addEventListener('click',async event=>{const n=event.currentTarget;if(!confirm(`Archive this local package? ${n.dataset.dependentCount} current assignments will require attention. Historical plans and Shopify packages remain unchanged.`))return;try{await api(`/packaging/${n.dataset.packageArchive}/archive`,{confirm:true,version:Number(n.dataset.packageVersion)});window.location.assign('/packaging/');}catch(e){document.querySelector('[data-package-feedback]').textContent=e.message;}});
  const simulator=document.querySelector('[data-packing-simulator]');
  if(simulator){let basket=[],searchGeneration=0,packageChoices=[];const target=simulator.querySelector('[data-packing-basket]');
    const render=()=>{target.replaceChildren();basket.forEach((line,index)=>{const row=element('div',null,'packaging-basket-row');row.append(element('span',`${line.title} · ${line.sku} ${Object.values(line.options).join(' / ')}`));const qty=element('input',null,'form-control');qty.type='number';qty.min='1';qty.max='200';qty.value=line.quantity;qty.setAttribute('aria-label',`Quantity ${line.sku}`);qty.addEventListener('input',()=>line.quantity=Number(qty.value));row.append(qty,button('Remove',()=>{basket.splice(index,1);render();}));
      const choice=element('select',null,'form-select');choice.setAttribute('aria-label',`Analysis-only package override ${line.sku}`);choice.add(new Option('Use authored package policy',''));packageChoices.forEach(p=>choice.add(new Option(`${p.name} — ${p.readiness}`,p.id)));choice.value=line.package_override || '';choice.addEventListener('change',()=>{line.package_override=choice.value;});row.append(choice);target.append(row);});};
    api('/packaging/choices').then(value=>{packageChoices=value.packages;render();}).catch(()=>{});
    let timer;simulator.querySelector('[data-packing-search]').addEventListener('input',event=>{clearTimeout(timer);const query=event.target.value;const generation=++searchGeneration;timer=setTimeout(async()=>{try{const result=await api('/packaging/products?q='+encodeURIComponent(query));if(generation!==searchGeneration)return;const choices=simulator.querySelector('[data-packing-results]');choices.replaceChildren();result.products.forEach(p=>choices.append(button(`${p.title} · ${p.sku} ${Object.values(p.options).join(' / ')}`,()=>{basket.push({...p,quantity:1});render();})));}catch(e){simulator.querySelector('[data-packing-error]').textContent=e.message;}},200);});
    simulator.querySelector('[data-packing-run]').addEventListener('click',async event=>{const b=event.currentTarget;b.disabled=true;try{if(!basket.length)throw new Error('Add products to the basket first.');const result=await api('/packaging/simulate',{lines:basket,allow_estimated:simulator.querySelector('[data-packing-estimated]').checked});output(document.querySelector('[data-packing-output]'),result);}catch(e){simulator.querySelector('[data-packing-error]').textContent=e.message;}finally{b.disabled=false;}});
  }
  const rateForm=document.querySelector('[data-rate-form]');
  if(rateForm){const config=JSON.parse(document.getElementById('rates-bootstrap').textContent || 'null') || {};['source','handling_pence'].forEach(k=>{rateForm.elements[k].value=config[k] ?? (k==='handling_pence'?0:'');});rateForm.elements.include_packaging.checked=!!config.include_packaging;['effective_from','effective_until'].forEach(k=>{if(config[k])rateForm.elements[k].value=new Date(config[k]).toISOString().slice(0,16);});
    function band(value={}){const row=element('div',null,'editor-field-grid app-card');row.dataset.rateBand='';[['service',['tracked_24','tracked_48']],['format',['large_letter','small_parcel','medium_parcel']],['max_weight_g'],['max_length_cm'],['max_width_cm'],['max_height_cm'],['price_pence']].forEach(([k,options])=>{const label=element('label',k.replaceAll('_',' '));const n=element(options?'select':'input',null,options?'form-select':'form-control');n.dataset.bandField=k;if(options)options.forEach(v=>n.add(new Option(v.replaceAll('_',' '),v)));else{n.type='number';n.min='0';n.step=k==='price_pence'?'1':'any';n.required=true;}n.value=value[k] ?? (options?options[0]:'');label.append(n);row.append(label);});row.append(button('Remove band',()=>row.remove()));rateForm.querySelector('[data-rate-bands]').append(row);}
    (config.bands || []).forEach(band);rateForm.querySelector('[data-rate-add]').addEventListener('click',()=>band());
    rateForm.addEventListener('submit',async event=>{event.preventDefault();try{const value={currency:'GBP',country:'GB',source:rateForm.elements.source.value,handling_pence:Number(rateForm.elements.handling_pence.value),include_packaging:rateForm.elements.include_packaging.checked,bands:[]};['effective_from','effective_until'].forEach(k=>value[k]=rateForm.elements[k].value+':00Z');rateForm.querySelectorAll('[data-rate-band]').forEach(row=>{const b={};row.querySelectorAll('[data-band-field]').forEach(n=>b[n.dataset.bandField]=n.dataset.bandField==='price_pence'?Number(n.value):n.value);value.bands.push(b);});const saved=await api('/packaging/rates',value);rateForm.querySelector('[data-package-feedback]').textContent=saved.message;}catch(e){rateForm.querySelector('[data-package-feedback]').textContent=e.message;}});
  }
  document.querySelector('[data-order-packing]')?.addEventListener('click',async event=>{const n=event.currentTarget;n.disabled=true;try{output(document.querySelector('[data-order-packing-output]'),await api(n.dataset.orderPacking,{}));}catch(e){document.querySelector('[data-order-packing-output]').textContent=e.message;}finally{n.disabled=false;}});
})();
