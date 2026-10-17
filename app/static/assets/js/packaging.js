(function () {
  'use strict';
  const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
  async function api(url,value) {
    const response=await fetch(url,{method:value===undefined?'GET':'POST',credentials:'same-origin',
      headers:value===undefined?{}:{'Content-Type':'application/json','X-CSRFToken':csrf()},
      ...(value===undefined?{}:{body:JSON.stringify(value)})});
    const result=await response.json().catch(()=>({error:'Unreadable response. Your local edits are retained.'}));
    if(!response.ok)throw new Error(result.error || 'Request unavailable. Your local edits are retained.');
    return result;
  }
  const element=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
  const button=(text,run)=>{const n=element('button',text,'btn btn-sm btn-outline-primary');n.type='button';n.addEventListener('click',run);return n;};
  const clone=value=>structuredClone(value || {});
  const idForm=document.querySelector('[data-package-id-form]');
  if(idForm){const retained=JSON.parse(document.getElementById('package-link-bootstrap').textContent);
    async function saveId(unlink){const feedback=idForm.querySelector('[data-package-id-feedback]');
      if(unlink&&!idForm.querySelector('[data-confirm-unlink]')?.checked){feedback.textContent='Confirm unlinking first. Remote assignments are not cleared.';return;}
      idForm.querySelectorAll('button').forEach(b=>b.disabled=true);
      try{const result=await api(idForm.dataset.url,{id:idForm.elements.shopify_package_id.value,expected:retained.expected,unlink,confirm:unlink});
        Object.assign(retained,result.package_link);idForm.elements.shopify_package_id.value=retained.id || '';
        idForm.querySelector('[data-package-id-status]').textContent=retained.status;
        const packageForm=document.querySelector('[data-package-form]');if(packageForm)packageForm.dataset.version=String(result.package_version);
        idForm.querySelectorAll('button').forEach(b=>b.disabled=false);idForm.querySelector('[data-unlink-package]').disabled=!retained.id;
        idForm.querySelector('[data-confirm-unlink]').checked=false;feedback.textContent='Saved locally. ID is not remotely verified. Unsaved package measurements are retained.';
      }catch(error){feedback.textContent=error.message;idForm.querySelectorAll('button').forEach(b=>b.disabled=false);}}
    idForm.addEventListener('submit',event=>{event.preventDefault();saveId(false);});
    idForm.querySelector('[data-unlink-package]')?.addEventListener('click',()=>saveId(true));
  }
  const merge=(a,b)=>{const r=clone(a);Object.entries(b || {}).forEach(([k,v])=>{if(k==='packed_dimensions'){delete r.packed_dimensions_cm;r[k]=clone(v);}else {if(k==='packed_dimensions_cm')delete r.packed_dimensions;r[k]=v && typeof v==='object' && !Array.isArray(v)?merge(r[k],v):structuredClone(v);}});return r;};
  const readPath=(o,path)=>path.split('.').reduce((v,k)=>v?.[k],o);
  function setPath(o,path,value){const parts=path.split('.');const key=parts.pop();let target=o;parts.forEach(k=>{target[k] ||= {};target=target[k];});if(value===undefined)delete target[key];else target[key]=value;}
  function packageData(form){const result={};form.querySelectorAll('[name]').forEach(n=>{
    let value=n.type==='checkbox'?n.checked:n.value;
    if(n.name==='protection')value=value.split(',').map(s=>s.trim()).filter(Boolean);
    if(n.name==='cost_pence')value=value===''?null:Number(value);
    setPath(result,n.name,value);
  });return result;}
  function populatePackage(form,data){form.querySelectorAll('[name]').forEach(n=>{let v=readPath(data,n.name);if(n.tagName==='SELECT' && v===undefined)return;if(n.name==='protection')v=(v || []).join(', ');if(n.type==='checkbox')n.checked=v===true;else n.value=v ?? '';});packageUnits(form);}
  function packageUnits(form){form.querySelectorAll('[data-package-unit]').forEach(select=>{
    if(select.dataset.bound)return;select.dataset.bound='yes';let previous=select.value;
    const lengths={cm:1,mm:.1,in:2.54},weights={g:1,kg:1000,oz:28.349523125,lb:453.59237};
    const isLength=select.dataset.packageUnit==='length',factors=isLength?lengths:weights;
    select.addEventListener('change',()=>{const ratio=factors[previous]/factors[select.value];
      form.querySelectorAll('input[name]').forEach(input=>{const name=input.name;
        const relevant=isLength?(name.startsWith('external_cm.')||name.startsWith('internal_cm.')||['external_diameter_cm','internal_diameter_cm','end_cap_allowance_cm','clearance_cm'].includes(name)):['tare_g','max_contents_g','max_gross_g','protection_weight_g'].includes(name);
        if(relevant&&input.value!=='')input.value=String(Number((Number(input.value)*ratio).toPrecision(14)));
      });form.querySelectorAll(isLength?'[data-length-unit]':'[data-weight-unit]').forEach(n=>n.textContent=select.value);previous=select.value;
    });
  });}
  function output(target,result){
    target.replaceChildren();target.append(element('p',result.notice));
    (result.warnings || []).forEach(v=>target.append(element('p',v,'alert alert-warning')));
    if(result.recommendation){const value=result.recommendation;target.append(element('p',`${value.name} · ${value.fit} fit · ${value.gross_weight_g} g gross. ${value.reason}`));}
    if(result.alternatives?.length){const d=element('details');d.append(element('summary','Alternative solo packages'));result.alternatives.forEach(p=>d.append(element('p',`${p.name} · ${p.gross_weight_g} g gross`)));target.append(d);}
    if(result.rejected_packages?.length){const d=element('details');d.append(element('summary','Fit checks / measurements needed'));result.rejected_packages.forEach(p=>d.append(element('p',`${p.name}: ${p.reason}`)));target.append(d);}
  }
  async function inlinePackage(onSaved){
    const template=document.getElementById('package-modal-template');
    if(!template)throw new Error('Package editor unavailable. Your draft is retained.');
    const returnFocus=document.activeElement,returnX=window.scrollX,returnY=window.scrollY;
    const dialog=template.content.firstElementChild.cloneNode(true);document.body.append(dialog);
    const form=dialog.querySelector('form'),feedback=form.querySelector('[data-package-feedback]');packageUnits(form);
    const close=()=>{dialog.close();dialog.remove();returnFocus?.focus({preventScroll:true});window.scrollTo(returnX,returnY);};
    dialog.querySelector('[data-package-cancel]').addEventListener('click',close);
    dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
    form.addEventListener('submit',async event=>{event.preventDefault();const submit=form.querySelector('button[type="submit"],button:not([type])');submit.disabled=true;
      try{const saved=await api('/packaging/new',packageData(form));await onSaved(saved);close();}
      catch(error){feedback.textContent=error.message;}finally{submit.disabled=false;}});
    dialog.showModal();form.querySelector('[name="name"]').focus();
  }
  const definitions=[
    ['packed_dimensions_cm.length','Packed length (cm)','number'],['packed_dimensions_cm.width','Packed width / roll diameter (cm)','number'],['packed_dimensions_cm.height','Packed thickness / roll diameter (cm)','number'],
    ['weight_g','Individual shipping weight (g)','number'],['measured','Prepared packed size physically measured','boolean'],
    ['method','Packing method',['flat','rolled','rigid','protected']],['orientation','Permitted orientation',['fixed','flat','any']],
    ['classification','Product class',['flat','rigid','general','rollable']],
    ['clearance_cm','Protection allowance each face (cm)','number'],['protection_weight_g','Individual protective material weight (g)','number'],
    ['fragile','Fragile','boolean'],['rollable','Rolling permitted','boolean'],
    ['requirements','Required protection (comma separated)','list'],['selected_package_id','Optional solo package','package'],['override_reason','Selection / override reason','text']
  ];
  const sizeFactors={mm:0.1,cm:1,in:2.54};
  function applyDimensionCopy(document,configurations,{overwrite=false}={}){
    const result=clone(document);
    configurations.forEach(config=>{
      let target=result;
      if(config.key){result.variation_modifiers ||= {};target=result.variation_modifiers[config.key] ||= {};}
      const old=target.packaging || {};
      if(!overwrite && (old.packed_dimensions || old.packed_dimensions_cm || config.packed?.packed_dimensions || config.packed?.packed_dimensions_cm))throw new Error('Confirm overwrite of existing packed measurements first.');
      target.packaging=clone(old);delete target.packaging.packed_dimensions_cm;
      target.packaging.packed_dimensions=clone(config.copy);
      target.packaging.measured=false;
    });
    return result;
  }
  async function copyDimensions(getDocument,onApply,getEffectiveDocument=getDocument){
    const original=getDocument();
    const effectiveDocument=getEffectiveDocument();
    const answer=await api('/packaging/dimension-copy-preview',{document:effectiveDocument});
    const returnFocus=document.activeElement,returnY=window.scrollY;
    const dialog=element('dialog',null,'packaging-dialog'),form=element('form');dialog.append(form);
    form.append(element('h2','Copy Product Dimensions → Packed Dimensions'),element('p','One-time copy only. Finished size, prices, weights and identities stay unchanged. Adjust for real preparation, then confirm physical measurement.'));
    answer.configurations.forEach(c=>{const size=c.copy,prior=c.packed?.packed_dimensions || c.packed?.packed_dimensions_cm;
      form.append(element('p',`${c.key || 'One size'}: ${prior?['length','width','height'].map(k=>prior[k] ?? 'Not set').join(' × ')+' '+(prior.unit || 'cm'):'Unconfigured'} → ${['length','width','height'].map(k=>size[k] ?? 'Not set').join(' × ')} ${size.unit}`));});
    const existing=answer.configurations.some(c=>c.packed?.packed_dimensions || c.packed?.packed_dimensions_cm);
    const label=element('label'),confirmOverwrite=element('input');confirmOverwrite.type='checkbox';label.append(confirmOverwrite,element('span',' Replace existing/inherited packed measurements for these size groups (variant-specific exceptions remain).'));if(existing)form.append(label);
    const feedback=element('p',null,'field-error');
    const close=()=>{dialog.close();dialog.remove();returnFocus?.focus({preventScroll:true});window.scrollTo(window.scrollX,returnY);};
    const apply=button('Copy into this unsaved draft',()=>{
      try{if(JSON.stringify(getDocument())!==JSON.stringify(original))throw new Error('The draft changed while this preview was open. Close and preview again.');
        const base=clone(original);
        if(answer.configurations.some(c=>c.key) && !base.variation_modifiers)base.variation_modifiers=clone(effectiveDocument.variation_modifiers);
        onApply(applyDimensionCopy(base,answer.configurations,{overwrite:!existing || confirmOverwrite.checked}));close();
      }catch(error){feedback.textContent=error.message;}
    });
    const actions=element('div',null,'page-actions');actions.append(apply,button('Cancel',close));form.append(feedback,actions);form.addEventListener('submit',e=>e.preventDefault());dialog.addEventListener('cancel',e=>{e.preventDefault();close();});document.body.append(dialog);dialog.showModal();
  }
  class Editor {
    constructor(root,{value,shared,onChange,getDocument,getAttributes}={}){this.root=root;this.value=clone(value);this.shared=clone(shared);this.onChange=onChange || (()=>{});this.getDocument=getDocument;this.getAttributes=getAttributes;this.controls=[];this.render();}
    render(){
      this.root.replaceChildren();const details=element('details');details.append(element('summary','Packed Shipping Dimensions · independent, optional solo package'));const grid=element('div',null,'editor-field-grid');details.append(grid);this.root.append(details);
      const ownSize=this.value.packed_dimensions,baseSize=this.shared.packed_dimensions;
      this.sizePath=ownSize || !this.value.packed_dimensions_cm && baseSize?'packed_dimensions':'packed_dimensions_cm';
      const unit=element('select',null,'form-select'),unitLabel=element('label','Packed measurement units');unit.setAttribute('aria-label','Packed measurement units');['mm','cm','in'].forEach(v=>unit.add(new Option(v,v)));unit.value=(ownSize || baseSize)?.unit || 'cm';unitLabel.append(unit);grid.append(unitLabel);this.unit=unit;
      const sourceSize=ownSize || this.value.packed_dimensions_cm || baseSize || this.shared.packed_dimensions_cm || {};
      this.assignmentStatus=element('p',null,'field-helper');details.append(this.assignmentStatus);
      const state=()=>{const row=this.controls.find(c=>c.kind==='package');if(!row)return;
        if(this.assignmentCheckbox)this.assignmentCheckbox.checked=!!row.input.value;
        this.assignmentStatus.textContent=row.inherit.value==='inherit'?(this.shared.selected_package_id?'Inherited package':'No package selected · advisory recommendations only'):
          !row.input.value?'Explicitly no package assigned · parent selection not inherited':this.value.assignment_kind==='accepted'?'Recommendation accepted':'Manually selected package';};
      definitions.forEach(([definitionPath,definitionLabel,kind])=>{
        const isSize=definitionPath.startsWith('packed_dimensions_cm.'),axis=definitionPath.split('.')[1];
        const path=isSize?this.sizePath+'.'+axis:definitionPath,label=isSize?definitionLabel.replace(' (cm)',''):definitionLabel;
        const wrap=element('div',null,'guided-field'),title=element('label',label),inherit=element('select',null,'form-select');
        inherit.setAttribute('aria-label',label+' source');inherit.add(new Option('Inherit / unspecified','inherit'));inherit.add(new Option('Set this value','own'));
        const own=isSize?(ownSize || this.value.packed_dimensions_cm)?sourceSize[axis]:undefined:readPath(this.value,path);
        const rawBase=isSize?(baseSize || this.shared.packed_dimensions_cm || {})[axis]:readPath(this.shared,path);
        const base=isSize && rawBase!==undefined && (baseSize?.unit || 'cm')!==unit.value?String(Number((Number(rawBase)*sizeFactors[baseSize?.unit || 'cm']/sizeFactors[unit.value]).toPrecision(12))):rawBase;inherit.value=own===undefined?'inherit':'own';
        const input=element(Array.isArray(kind) || ['boolean','package'].includes(kind)?'select':'input',null,'form-control');input.setAttribute('aria-label',label);input.dataset.packagingPath=path;
        if(input.tagName==='SELECT'){input.className='form-select';input.add(new Option(kind==='package'?'No Package Assigned':'Choose value',''));const choices=kind==='boolean'?[['true','Yes'],['false','No']]:Array.isArray(kind)?kind.map(v=>[v,v]):[];choices.forEach(([v,t])=>input.add(new Option(t,v)));}
        else{input.type=kind==='number'?'number':'text';if(input.type==='number'){input.min='0';input.step='any';}}
        const shown=own===undefined?base:own;input.value=kind==='list'?(shown || []).join(', '):shown==null?'':String(shown);input.disabled=inherit.value==='inherit';title.append(inherit,input);wrap.append(title);grid.append(wrap);
        const row={path,kind,input,inherit,original:own};this.controls.push(row);
        const changed=()=>{if(kind==='package'){this.value.assignment_kind='manual';state();}this.onChange();this.queueRecommendation?.();};
        inherit.addEventListener('change',()=>{input.disabled=inherit.value==='inherit';if(inherit.value==='inherit')input.value=base ?? '';changed();});input.addEventListener('input',changed);input.addEventListener('change',changed);
        if(kind==='package'){
          const assignment=element('label'),check=element('input');check.type='checkbox';check.setAttribute('aria-label','Use this package when shipped alone');assignment.append(check,element('span',' Use this package when shipped alone'));wrap.append(assignment);this.assignmentCheckbox=check;
          this.selectPackage=(id,kind='manual')=>{inherit.value='own';input.disabled=false;input.value=id || '';this.value.assignment_kind=kind;state();this.onChange();};
          check.addEventListener('change',()=>{if(!check.checked)this.selectPackage(null);else if(!input.value && this.currentRecommendation)this.selectPackage(this.currentRecommendation.package_id,'accepted');else if(!input.value){check.checked=false;this.assignmentStatus.textContent='Recalculate or choose a package first. Recommendations never assign automatically.';}else this.selectPackage(input.value,this.value.assignment_kind || 'manual');});
          const refresh=async()=>{const choices=await api('/packaging/choices');const wanted=input.value;input.replaceChildren(new Option('No Package Assigned',''));choices.packages.forEach(p=>input.add(new Option(`${p.name} — ${p.readiness}`,p.id)));if(wanted&&!choices.packages.some(p=>p.id===wanted))input.add(new Option('Unavailable / archived package',wanted));input.value=wanted;};
          this.refreshChoices=refresh;refresh().catch(()=>wrap.append(element('p','Choices unavailable. Existing assignment retained.')));
          wrap.append(button('No Package Assigned',()=>{inherit.value='own';input.disabled=false;input.value='';this.value.assignment_kind='manual';state();this.onChange();}));
          wrap.append(button('Decline recommendation',()=>this.selectPackage(null)));
          wrap.append(button('+ Add new package',()=>inlinePackage(async()=>{await refresh();this.queueRecommendation?.();}).catch(e=>wrap.append(element('p',e.message)))));
        }
      });state();
      unit.addEventListener('change',()=>{
        const previous=this.previousUnit || (ownSize || baseSize)?.unit || 'cm',factor=sizeFactors[previous]/sizeFactors[unit.value];
        this.controls.filter(r=>r.path.startsWith('packed_dimensions')).forEach(r=>{if(r.input.value!=='')r.input.value=String(Number((Number(r.input.value)*factor).toPrecision(12)));r.path='packed_dimensions.'+r.path.split('.')[1];r.input.dataset.packagingPath=r.path;r.inherit.value='own';r.input.disabled=false;});
        delete this.value.packed_dimensions_cm;this.sizePath='packed_dimensions';this.previousUnit=unit.value;this.onChange();this.queueRecommendation?.();
      });
      let comparison=0,timer;const result=element('div');const recalculate=button('Recalculate recommendation',async()=>{
        const generation=++comparison;try{recalculate.disabled=true;
          const doc=this.getDocument?this.getDocument():{packaging:merge(this.shared,this.get())};
          const answer=await api('/packaging/recommend',{document:doc,...(this.getAttributes?{attributes:this.getAttributes()}: {})});if(generation!==comparison)return;output(result,answer);this.currentRecommendation=answer.recommendation;
          const selected=doc.packaging?.selected_package_id;
          if(selected && answer.rejected_packages?.some(p=>p.id===selected))result.append(element('p','Your selected package is retained, but its fit needs review after these measurement changes. Choose another package or explicitly clear the selection.','alert alert-warning'));
          if(answer.recommendation)result.append(button('Use Recommendation',()=>{if(generation===comparison)this.selectPackage(answer.recommendation.package_id,'accepted');}));
          (answer.alternatives || []).forEach(p=>result.append(button('Use alternative: '+p.name,()=>{if(generation===comparison)this.selectPackage(p.package_id,'manual');})));
        }catch(e){if(generation===comparison)result.textContent=e.message;}finally{recalculate.disabled=false;if(generation!==comparison)this.queueRecommendation();}
      });details.append(recalculate,result);
      this.queueRecommendation=()=>{comparison++;this.currentRecommendation=null;clearTimeout(timer);timer=setTimeout(()=>{if(!recalculate.disabled && details.isConnected)recalculate.click();},400);};
      details.append(element('p','Optional selection stays local/manual until a real Shopify package association can be read back. Clearing locally does not prove a prior Shopify assignment cleared. Save Locally → Review & Sync handles other supported fields.','field-helper'));
    }
    setEnabled(enabled){this.unit.disabled=!enabled;if(this.assignmentCheckbox)this.assignmentCheckbox.disabled=!enabled;this.controls.forEach(r=>{r.inherit.disabled=!enabled;r.input.disabled=!enabled || r.inherit.value==='inherit';});this.root.querySelectorAll('button').forEach(n=>n.disabled=!enabled);}
    get(){const result=clone(this.value);this.controls.forEach(({path,kind,input,inherit,original})=>{
      if(inherit.value==='inherit'){setPath(result,path,undefined);return;}
      let v=input.value;if(v==='' && kind!=='package'){setPath(result,path,undefined);return;}
      if(kind==='boolean')v=v==='true';else if(kind==='list')v=v.split(',').map(s=>s.trim()).filter(Boolean);else if(kind==='package'&&v==='')v=null;
      if(original!==undefined && String(original)===String(v))v=original;setPath(result,path,v);
    });if(result.packed_dimensions_cm&&!Object.keys(result.packed_dimensions_cm).length)delete result.packed_dimensions_cm;
      if(result.packed_dimensions && Object.keys(result.packed_dimensions).some(k=>['length','width','height'].includes(k))){
        this.controls.filter(r=>r.path.startsWith('packed_dimensions.')).forEach(r=>{const axis=r.path.split('.')[1];if(r.input.value!=='' && result.packed_dimensions[axis]===undefined)result.packed_dimensions[axis]=r.input.value;});
        result.packed_dimensions.unit=this.unit.value;
      }
      else if(result.packed_dimensions && !Object.keys(result.packed_dimensions).length)delete result.packed_dimensions;
      if(!result.selected_package_id)delete result.assignment_kind;
      return Object.keys(result).length?result:undefined;}
  }
  class FinishedEditor {
    constructor(root,{value,shared,onChange}={}){
      this.value=value;this.shared=clone(shared);this.controls={};this.onChange=onChange || (()=>{});
      root.replaceChildren();const details=element('details');details.append(element('summary','Finished product · customer-facing size'));root.append(details);
      this.source=element('select',null,'form-select');this.source.setAttribute('aria-label','Finished dimensions source');this.source.add(new Option('Inherit / unspecified','inherit'));this.source.add(new Option('Set finished dimensions','own'));this.source.value=value===undefined?'inherit':'own';details.append(this.source);
      const grid=element('div',null,'editor-field-grid');details.append(grid);const shown=value || shared || {};
      [['length','Finished length'],['width','Finished width'],['height','Finished thickness / height'],['unit','Finished measurement unit'],['label','Size label (e.g. A4 or folded)']].forEach(([key,label])=>{
        const field=element('label',label),input=element(key==='unit'?'select':'input',null,'form-control');input.setAttribute('aria-label',label);
        if(key==='unit')['mm','cm','in'].forEach(v=>input.add(new Option(v,v)));else if(key!=='label'){input.type='number';input.min='0';input.step='any';}
        input.value=shown[key] ?? (key==='unit'?'mm':'');field.append(input);grid.append(field);this.controls[key]=input;
        input.addEventListener('input',()=>{this.preview();this.onChange();});input.addEventListener('change',()=>{this.preview();this.onChange();});
      });this.summary=element('p',null,'field-helper');details.append(this.summary,element('p','Original product millimetres are finished dimensions. This optional unit-explicit form remains compatible with existing saves. Finished changes never overwrite packed measurements; review shipping separately.','field-helper'));
      this.source.addEventListener('change',()=>{if(this.source.value==='own' && this.shared.unit)Object.entries(this.controls).forEach(([k,n])=>n.value=this.shared[k] ?? '');this.setEnabled(true);this.preview();this.onChange();});
      this.setEnabled(true);this.preview();
    }
    setEnabled(enabled){this.source.disabled=!enabled;Object.values(this.controls).forEach(n=>n.disabled=!enabled || this.source.value==='inherit');}
    get(){if(this.source.value==='inherit')return undefined;const result={};Object.entries(this.controls).forEach(([k,n])=>{if(n.value!=='')result[k]=this.value?.[k]!==undefined && String(this.value[k])===n.value?this.value[k]:n.value;});return result;}
    preview(){const value=this.get() || this.shared;this.summary.textContent=value.length&&value.width?`Finished size: ${value.label?value.label+' — ':''}${['length','width','height'].filter(k=>value[k]).map(k=>value[k]).join(' × ')} ${value.unit || 'unit required'}`:'Finished size unspecified. No packed measurements inferred.';}
  }
  window.TLCPackaging={Editor,FinishedEditor,api,output,merge,packageData,populatePackage,copyDimensions,applyDimensionCopy};
  const form=document.querySelector('[data-package-form]');
  if(form){const data=JSON.parse(document.getElementById('package-bootstrap').textContent || 'null') || {};populatePackage(form,data);
    const copy=document.querySelector('[data-native-package-copy]');if(copy)copy.textContent=JSON.stringify({name:data.name,type:data.type,dimensions:data.external_cm,diameter_cm:data.external_diameter_cm,weight_g:data.tare_g},null,2);
    form.addEventListener('submit',async e=>{e.preventDefault();try{const result=await api(form.dataset.saveUrl,{...packageData(form),version:Number(form.dataset.version || data.version)});window.location.assign(result.url);}catch(error){form.querySelector('[data-package-feedback]').textContent=error.message;}});
    form.querySelector('[data-package-duplicate]')?.addEventListener('click',async()=>{try{const r=await api(`/packaging/${data.id}/duplicate`,{});window.location.assign(r.url);}catch(e){form.querySelector('[data-package-feedback]').textContent=e.message;}});
  }
  document.querySelector('[data-package-archive]')?.addEventListener('click',async e=>{const n=e.currentTarget;if(!confirm(`Archive locally? ${n.dataset.dependentCount} assignments may need review. Shopify packages stay unchanged.`))return;try{await api(`/packaging/${n.dataset.packageArchive}/archive`,{confirm:true,version:Number(n.dataset.packageVersion)});window.location.assign('/packaging/');}catch(error){document.querySelector('[data-package-feedback]').textContent=error.message;}});
})();
