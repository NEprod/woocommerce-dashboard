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
  let packageIdRequest;
  if(idForm){const retained=JSON.parse(document.getElementById('package-link-bootstrap').textContent) || {id:null,expected:[]};
    packageIdRequest=()=>({id:idForm.querySelector('[name="shopify_package_id"]').value,expected:retained.expected,
      unlink:!!idForm.querySelector('[data-confirm-unlink]')?.checked,confirm:!!idForm.querySelector('[data-confirm-unlink]')?.checked});
    async function saveId(unlink){const feedback=idForm.querySelector('[data-package-id-feedback]');
      const parent=document.querySelector('[data-package-form]');
      if(parent?.dataset.saving==='yes'){feedback.textContent='A local save is already in progress.';return;}
      if(unlink&&!idForm.querySelector('[data-confirm-unlink]')?.checked){feedback.textContent='Confirm unlinking first. Remote assignments are not cleared.';return;}
      if(parent)parent.dataset.saving='yes';
      idForm.querySelectorAll('button').forEach(b=>b.disabled=true);
      try{const result=await api(idForm.dataset.url,{id:idForm.querySelector('[name="shopify_package_id"]').value,expected:retained.expected,unlink,confirm:unlink});
        Object.assign(retained,result.package_link);idForm.querySelector('[name="shopify_package_id"]').value=retained.id || '';
        idForm.querySelector('[data-package-id-status]').textContent=retained.status;
        const packageForm=document.querySelector('[data-package-form]');if(packageForm)packageForm.dataset.version=String(result.package_version);
        idForm.querySelectorAll('button').forEach(b=>b.disabled=false);idForm.querySelector('[data-unlink-package]').disabled=!retained.id;
        idForm.querySelector('[data-confirm-unlink]').checked=false;feedback.textContent='Saved locally. ID is not remotely verified. Unsaved package measurements are retained.';
      }catch(error){feedback.textContent=error.message;idForm.querySelectorAll('button').forEach(b=>b.disabled=false);}
      finally{if(parent)delete parent.dataset.saving;}}
    idForm.querySelector('[data-save-package-id]')?.addEventListener('click',()=>saveId(false));
    idForm.querySelector('[data-unlink-package]')?.addEventListener('click',()=>saveId(true));
  }
  const merge=(a,b)=>{const r=clone(a);Object.entries(b || {}).forEach(([k,v])=>{if(k==='packed_dimensions'){delete r.packed_dimensions_cm;r[k]=clone(v);}else {if(k==='packed_dimensions_cm')delete r.packed_dimensions;r[k]=v && typeof v==='object' && !Array.isArray(v)?merge(r[k],v):structuredClone(v);}});return r;};
  const readPath=(o,path)=>path.split('.').reduce((v,k)=>v?.[k],o);
  function setPath(o,path,value){const parts=path.split('.');const key=parts.pop();let target=o;parts.forEach(k=>{target[k] ||= {};target=target[k];});if(value===undefined)delete target[key];else target[key]=value;}
  function packageData(form){const result={};form.querySelectorAll('[name]').forEach(n=>{
    if(n.name==='shopify_package_id')return;
    if(n.closest?.('[data-package-types]')?.hidden)return;
    let value=n.type==='checkbox'?n.checked:n.value;
    setPath(result,n.name,value);
  });return result;}
  function populatePackage(form,data){form.tlcPackageOriginal=clone(data);form.querySelectorAll('[name]').forEach(n=>{if(n.name==='shopify_package_id')return;let v=readPath(data,n.name);if(n.tagName==='SELECT' && v===undefined)return;if(n.type==='checkbox')n.checked=v===true;else n.value=v ?? '';});packageUnits(form);}
  function packageTypeFields(form){const select=form.querySelector?.('[name="type"]');if(!select)return;
    const update=()=>{const kind=select.value;
      form.querySelectorAll('[data-package-types]').forEach(n=>n.hidden=!n.dataset.packageTypes.split(' ').includes(kind));
      form.querySelectorAll('[data-package-length-label]').forEach(n=>n.textContent=kind==='TUBE'?'External tube length':'External length');
      form.querySelectorAll('[data-package-internal-length-label]').forEach(n=>n.textContent=kind==='TUBE'?'Internal usable tube length':'Internal usable length');
      form.querySelectorAll('[data-package-height-label]').forEach(n=>n.textContent=kind==='ENVELOPE'?'Internal usable height / maximum contents thickness':'Internal usable height');
      const warning=form.querySelector('[data-package-thickness-warning]'),legacy=form.tlcPackageOriginal?.max_contents_thickness_mm;
      const review=form.querySelector('[data-package-legacy-thickness]');if(review)review.hidden=kind!=='ENVELOPE'||legacy==null;
      if(warning){warning.textContent=legacy==null?'':`Historical value: ${legacy} mm (${Number(legacy)/10} cm). It is not applied automatically. Current stored internal height: ${form.tlcPackageOriginal?.internal_cm?.height ?? 'unknown'} cm. Conflicting values need review, not an automatic winner.`;}
    };
    if(!select.dataset.typeBound){select.dataset.typeBound='yes';select.addEventListener('change',update);
      form.querySelector('[data-transfer-package-thickness]')?.addEventListener('click',()=>{
        const legacy=form.tlcPackageOriginal?.max_contents_thickness_mm,input=form.querySelector('[name="internal_cm.height"]'),unit=form.querySelector('[name="dimension_unit"]').value;
        if(legacy==null)return;
        if(!confirm(`Transfer historical ${legacy} mm to internal usable height (${Number(legacy)/10} cm), replacing the entered value? Save Locally will archive the old values; no Shopify change occurs.`))return;
        input.value=String(Number((Number(legacy)/10/({cm:1,mm:.1,in:2.54}[unit])).toPrecision(14)));form.tlcReviewedHeight=Number(legacy)/10;
      });
      form.querySelector('[data-confirm-package-thickness]')?.addEventListener('click',()=>{
        const input=form.querySelector('[name="internal_cm.height"]'),unit=form.querySelector('[name="dimension_unit"]').value;
        if(!confirm(`Confirm entered internal height ${input.value || 'unknown'} ${unit} and archive the historical thickness rule? Unknown capacity will not produce an automatic fit. Save Locally to persist; no Shopify change occurs.`))return;
        form.tlcReviewedHeight=input.value===''?null:Number((Number(input.value)*({cm:1,mm:.1,in:2.54}[unit])).toPrecision(14));
      });
    }update();
  }
  function packageSaveData(form){const value=packageData(form),old=form.tlcPackageOriginal;
    if(value.type==='ENVELOPE' && old?.max_contents_thickness_mm!=null && value.internal_cm && 'height' in value.internal_cm){
      const entered=value.internal_cm.height,proposed=entered===''||entered==null?null:Number((Number(entered)*({cm:1,mm:.1,in:2.54}[value.dimension_unit || 'cm'])).toPrecision(14));
      const previous=old.internal_cm?.height!=null?Number(old.internal_cm.height):null;
      const reviewed=Object.hasOwn(form,'tlcReviewedHeight')&&form.tlcReviewedHeight===proposed;
      if(proposed!==previous || reviewed){
        if(!reviewed && !confirm(`Confirm ${entered || 'unknown'} ${value.dimension_unit || 'cm'} internal usable height as the sole contents limit and archive historical ${old.max_contents_thickness_mm} mm? Previous values remain in history. No Shopify change occurs.`))throw new Error('Save cancelled. Your edits are retained.');
        value.confirm_envelope_thickness_change=true;
      }
    }return value;
  }
  function packageUnits(form){packageTypeFields(form);form.querySelectorAll('[data-package-unit]').forEach(select=>{
    if(select.dataset.bound)return;select.dataset.bound='yes';let previous=select.value;
    const lengths={cm:1,mm:.1,in:2.54},weights={g:1,kg:1000,oz:28.349523125,lb:453.59237};
    const isLength=select.dataset.packageUnit==='length',factors=isLength?lengths:weights;
    select.addEventListener('change',()=>{const ratio=factors[previous]/factors[select.value];
      form.querySelectorAll('input[name]').forEach(input=>{const name=input.name;
        const relevant=isLength?(name.startsWith('external_cm.')||name.startsWith('internal_cm.')||['external_diameter_cm','internal_diameter_cm'].includes(name)):name==='tare_g';
        if(relevant&&input.value!=='')input.value=String(Number((Number(input.value)*ratio).toPrecision(14)));
      });form.querySelectorAll(isLength?'[data-length-unit]':'[data-weight-unit]').forEach(n=>n.textContent=select.value);previous=select.value;
    });
  });}
  function output(target,result){
    target.replaceChildren();target.append(element('p',result.notice));
    if(result.warnings?.length)target.append(element('p',[...new Set(result.warnings)].join(' '),'alert alert-warning'));
    const gross=value=>value==null?'Gross weight unknown':`${value} g gross`;
    if(result.recommendation){const value=result.recommendation;target.append(element('p',`${value.name} · ${value.fit} · ${gross(value.gross_weight_g)}. ${value.reason}`));}
    if(result.alternatives?.length){const d=element('details');d.append(element('summary','Alternative solo packages'));result.alternatives.forEach(p=>d.append(element('p',`${p.name} · ${p.fit || 'Inspect fit'} · ${gross(p.gross_weight_g)}`)));target.append(d);}
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
      try{const saved=await api('/packaging/new',packageSaveData(form));await onSaved(saved);close();}
      catch(error){feedback.textContent=error.message;}finally{submit.disabled=false;}});
    dialog.showModal();form.querySelector('[name="name"]').focus();
  }
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
    });
    return result;
  }
  async function copyDimensions(getDocument,onApply,getEffectiveDocument=getDocument){
    const original=getDocument();
    const effectiveDocument=getEffectiveDocument();
    const answer=await api('/packaging/dimension-copy-preview',{document:effectiveDocument});
    const returnFocus=document.activeElement,returnY=window.scrollY;
    const dialog=element('dialog',null,'packaging-dialog'),form=element('form');dialog.append(form);
    form.append(element('h2','Copy Product Dimensions → Packed Dimensions'),element('p','One-time copy only. Finished size, prices, weights and identities stay unchanged. Adjust for real preparation; Save Metadata persists this draft.'));
    answer.configurations.forEach(c=>{const size=c.copy,prior=c.packed?.packed_dimensions || c.packed?.packed_dimensions_cm;
      form.append(element('p',`${c.key || 'One size'}: ${prior?['length','width','height'].map(k=>prior[k] ?? 'Not set').join(' × ')+' '+(prior.unit || 'cm'):'Unconfigured'} → ${['length','width','height'].map(k=>size[k] ?? 'Not set').join(' × ')} ${size.unit}`));});
    const existing=answer.configurations.some(c=>c.packed?.packed_dimensions || c.packed?.packed_dimensions_cm);
    const label=element('label'),confirmOverwrite=element('input');confirmOverwrite.type='checkbox';label.append(confirmOverwrite,element('span',' Replace existing/inherited packed measurements for these size groups (variant-specific exceptions remain).'));if(existing)form.append(label);
    const feedback=element('p',null,'field-error');
    const close=()=>{dialog.close();dialog.remove();returnFocus?.focus({preventScroll:true});window.scrollTo(window.scrollX,returnY);};
    const apply=button('Copy into this unsaved draft',()=>{
      try{if(JSON.stringify(getDocument())!==JSON.stringify(original))throw new Error('The draft changed while this preview was open. Close and preview again.');
        const base=clone(original);
        onApply(applyDimensionCopy(base,answer.configurations,{overwrite:!existing || confirmOverwrite.checked}));close();
      }catch(error){feedback.textContent=error.message;}
    });
    const actions=element('div',null,'page-actions');actions.append(apply,button('Cancel',close));form.append(feedback,actions);form.addEventListener('submit',e=>e.preventDefault());dialog.addEventListener('cancel',e=>{e.preventDefault();close();});document.body.append(dialog);dialog.showModal();
  }
  // Display-only finished fallback. Never used by serialization or publishers.
  function sourceDocument(shared,own){
    const result=merge(shared,own),modifiers=own?.variation_modifiers;
    if(modifiers && (!Object.keys(modifiers).length || Object.values(modifiers).some(v=>Object.keys(v).some(k=>!['packaging','finished_dimensions'].includes(k)))))result.variation_modifiers=clone(modifiers);
    return result;
  }
  function finishedFor(document,attributes){
    let original=document.dimensions || {}, alias=document.finished_dimensions;
    const matches=Object.entries(document.variation_modifiers || {}).filter(([key])=>attributes && key.split('|').every(part=>{const [k,v]=part.split('=');return attributes[k]===v;}));
    const selected=field=>matches.filter(([,v])=>v[field]!==undefined).sort((a,b)=>b[0].split('|').length-a[0].split('|').length)[0]?.[1];
    const commercial=matches.filter(([,v])=>!Object.keys(v).length || Object.keys(v).some(k=>!['packaging','finished_dimensions'].includes(k))).sort((a,b)=>b[0].split('|').length-a[0].split('|').length)[0]?.[1];
    if(commercial?.dimensions){original=commercial.dimensions;if(JSON.stringify(original)!==JSON.stringify(document.dimensions || {}))alias=undefined;}
    const explicit=selected('finished_dimensions');if(explicit)alias=explicit.finished_dimensions;
    return alias?.unit?clone(alias):{...original,unit:'mm'};
  }
  class Editor {
    constructor(root,{value,shared,onChange,getDocument,getAttributes,getShared,inherited=false}={}){
      Object.assign(this,{root,value:clone(value),shared:clone(shared),onChange:onChange || (()=>{}),getDocument,getAttributes,getShared,inherited});
      this.originalAssignment=value?.assignment_kind;
      this.controls=[];this.render();
    }
    render(){
      this.root.replaceChildren();
      const details=element('details');details.open=true;details.append(element('summary','Packed dimensions and optional solo package'));
      const grid=element('div',null,'editor-field-grid');details.append(grid);this.root.append(details);
      const own=this.value.packed_dimensions || this.value.packed_dimensions_cm;
      const base=this.shared.packed_dimensions || this.shared.packed_dimensions_cm || {};
      this.sizePath=this.value.packed_dimensions || !this.value.packed_dimensions_cm?'packed_dimensions':'packed_dimensions_cm';
      const source=element('select',null,'form-select');source.setAttribute('aria-label','Packed dimensions source');
      source.add(new Option('Inherit / provisional finished size','inherit'));source.add(new Option('Override packed dimensions','own'));source.value=own?'own':'inherit';this.source=source;
      const scope=element('label','Packed dimensions');scope.append(source);grid.append(scope);
      const unit=element('select',null,'form-select');['mm','cm','in'].forEach(v=>unit.add(new Option(v,v)));unit.value=own?.unit || (this.value.packed_dimensions_cm?'cm':base.unit || (this.shared.packed_dimensions_cm?'cm':'mm'));
      unit.setAttribute('aria-label','Packed measurement units');const units=element('label','Measurement unit');units.append(unit);grid.append(units);this.unit=unit;this.previousUnit=unit.value;
      for(const [axis,label] of [['length','Packed length'],['width','Packed width'],['height','Packed height / thickness']]){
        const field=element('label',label),input=element('input',null,'form-control');input.type='number';input.min='0';input.step='any';input.setAttribute('aria-label',label);
        const row={path:this.sizePath+'.'+axis,axis,kind:'number',input,inherit:{value:own?.[axis]===undefined?'inherit':'own'},original:own?.[axis]};
        input.value=own?.[axis]===undefined?'':String(own[axis]);this.controls.push(row);field.append(input);grid.append(field);
        const changed=()=>{source.value='own';row.inherit.value=input.value===''?'inherit':'own';this.onChange();this.refreshFallback();this.queueRecommendation?.();};
        input.addEventListener('input',changed);input.addEventListener('change',changed);
      }
      this.sizeNotice=element('p',null,'field-helper');details.append(this.sizeNotice);
      source.addEventListener('change',()=>{if(source.value==='inherit')this.controls.forEach(r=>{r.inherit.value='inherit';r.input.value='';});this.onChange();this.refreshFallback();this.queueRecommendation?.();});
      unit.addEventListener('change',()=>{
        const ratio=sizeFactors[this.previousUnit]/sizeFactors[unit.value];
        this.controls.forEach(r=>{if(r.inherit.value==='own' && r.input.value!=='')r.input.value=String(Number((Number(r.input.value)*ratio).toPrecision(12)));r.path='packed_dimensions.'+r.axis;});
        this.sizePath='packed_dimensions';this.previousUnit=unit.value;this.onChange();this.refreshFallback();this.queueRecommendation?.();
      });
      const packageLabel=element('label','Optional shipping package — use when shipped alone');
      const select=element('select',null,'form-select');select.setAttribute('aria-label','Optional shipping package');
      this.packageChoice=Object.hasOwn(this.value,'selected_package_id')?(this.value.selected_package_id ?? ''):'__inherit__';
      // Keep the authored identity separate from HTMLSelectElement.value while options load.
      this.packageSelect=select;this.assignmentStatus=element('p',null,'field-helper');
      const state=()=>{
        this.assignmentStatus.textContent=this.packageChoice==='__inherit__'
          ?this.shared.selected_package_id?'Inherited collection package · fit unverified':'No package selected · recommendations are optional'
          :this.packageChoice===''?'Explicitly no package · collection choice not inherited':'Selected package for this scope · review Shopify assignment separately';
      };
      const choices=(packages=[])=>{
        const wanted=this.packageChoice;select.replaceChildren();
        select.add(new Option(this.inherited || Object.keys(this.shared).length?'Inherit collection / parent package':'No package selected (default)','__inherit__'));
        select.add(new Option(this.inherited?'No package for this product':'Explicitly leave unassigned',''));
        packages.forEach(p=>select.add(new Option(p.name,p.id)));
        if(wanted && wanted!=='__inherit__' && !packages.some(p=>p.id===wanted))select.add(new Option('Saved package — details unavailable',wanted));
        select.value=wanted;state();
      };
      choices();packageLabel.append(select);details.append(packageLabel,this.assignmentStatus);
      this.selectPackage=(id,kind='manual')=>{
        this.packageChoice=id===undefined?'__inherit__':id || '';
        if(this.packageChoice && this.packageChoice!=='__inherit__')this.value.assignment_kind=this.packageChoice===this.value.selected_package_id?(this.originalAssignment || kind):kind;
        // Include a pending recommendation even if choices have not returned.
        choices(this.packages);this.onChange();
      };
      select.addEventListener('change',()=>this.selectPackage(select.value==='__inherit__'?undefined:select.value));
      this.refreshChoices=async()=>{const response=await api('/packaging/choices');this.packages=response.packages || [];choices(this.packages);};
      this.refreshChoices().catch(()=>{state();this.assignmentStatus.textContent+=' · Package list unavailable; saved identity retained.';});
      details.append(button('+ Add new package',()=>inlinePackage(async()=>{await this.refreshChoices();this.queueRecommendation?.();}).catch(e=>{this.assignmentStatus.textContent=e.message;})));
      let comparison=0,timer;const result=element('div');
      const recalculate=button('Recalculate recommendation',async()=>{
        const generation=++comparison;try{
          recalculate.disabled=true;
          const document=this.getDocument?this.getDocument():{packaging:merge(this.shared,this.get())};
          const answer=await api('/packaging/recommend',{document,display_finished_fallback:true,...(this.getAttributes?{attributes:this.getAttributes()}: {})});
          if(generation!==comparison)return;output(result,answer);this.currentRecommendation=answer.recommendation;
          if(answer.recommendation)result.append(button('Use recommended package',()=>{if(generation===comparison)this.selectPackage(answer.recommendation.package_id,'accepted');}));
          (answer.alternatives || []).forEach(p=>result.append(button('Use alternative: '+p.name,()=>{if(generation===comparison)this.selectPackage(p.package_id); })));
        }catch(error){if(generation===comparison)result.textContent=error.message;}
        finally{recalculate.disabled=false;if(generation!==comparison)this.queueRecommendation();}
      });details.append(recalculate,result);
      this.queueRecommendation=()=>{comparison++;this.currentRecommendation=null;clearTimeout(timer);timer=setTimeout(()=>{if(!recalculate.disabled && details.isConnected)recalculate.click();},400);};
      details.append(element('p','Manual choice is allowed with an unverified fit. Linked selections use signed Product Review & Sync; Shopify acceptance is not independent readback. Clearing locally does not clear Shopify.','field-helper'));
      // Populate calls refreshFallback after all surrounding controls exist.
    }
    refreshFallback(){
      if(this.getShared)this.shared=clone(this.getShared());
      const document=this.getDocument?this.getDocument():{};
      const finished=finishedFor(document,this.getAttributes?.());
      const base=this.source.value==='inherit'?(this.shared.packed_dimensions || this.shared.packed_dimensions_cm || {}):this.sizePath==='packed_dimensions_cm'?(this.shared.packed_dimensions_cm || {}):{};
      const baseUnit=this.shared.packed_dimensions?.unit || 'cm';let provisional=false;
      this.controls.forEach(row=>{
        if(row.inherit.value==='own')return;
        const inherited=base[row.axis],value=inherited ?? finished[row.axis],from=inherited===undefined?finished.unit:baseUnit;
        row.input.placeholder=value==null || value===''?'Unknown':String(Number((Number(value)*sizeFactors[from]/sizeFactors[this.unit.value]).toPrecision(12)))+' ('+(inherited===undefined?'provisional':'inherited')+')';
        if(inherited===undefined)provisional=true;
      });
      this.sizeNotice.textContent=provisional?'Blank packed fields show provisional finished-size placeholders only. They are not saved or sent to Shopify. Copy or enter packed measurements explicitly.':'Blank packed fields inherit collection / parent packed measurements. Package choice is independent.';
    }
    setEnabled(enabled){this.source.disabled=!enabled;this.unit.disabled=!enabled;this.packageSelect.disabled=!enabled;this.controls.forEach(r=>r.input.disabled=!enabled);this.root.querySelectorAll('button').forEach(n=>n.disabled=!enabled);}
    get(){
      const result=clone(this.value);delete result.packed_dimensions;delete result.packed_dimensions_cm;
      const size={};
      this.controls.forEach(({axis,input,inherit,original})=>{
        if(this.source.value==='own' && inherit.value==='own' && input.value!=='')size[axis]=original!==undefined && String(original)===String(input.value)?original:input.value;
      });
      if(Object.keys(size).length)result[this.sizePath]=this.sizePath==='packed_dimensions'?{...size,unit:this.unit.value}:size;
      if(this.packageChoice==='__inherit__')delete result.selected_package_id;
      else result.selected_package_id=this.packageChoice || null;
      if(!result.selected_package_id)delete result.assignment_kind;
      return Object.keys(result).length?result:undefined;
    }
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
  window.TLCPackaging={Editor,FinishedEditor,api,output,merge,sourceDocument,packageData,packageSaveData,populatePackage,copyDimensions,applyDimensionCopy};
  const form=document.querySelector('[data-package-form]');
  if(form){const data=JSON.parse(document.getElementById('package-bootstrap').textContent || 'null') || {};populatePackage(form,data);
    const copy=document.querySelector('[data-native-package-copy]');if(copy)copy.textContent=JSON.stringify({name:data.name,type:data.type,dimensions:data.external_cm,diameter_cm:data.external_diameter_cm,weight_g:data.tare_g},null,2);
    form.addEventListener('submit',async e=>{e.preventDefault();if(form.dataset.saving==='yes')return;form.dataset.saving='yes';
      const submit=form.querySelector('button:not([type])');if(submit)submit.disabled=true;
      try{const result=await api(form.dataset.saveUrl,{...packageSaveData(form),version:Number(form.dataset.version || data.version),...(packageIdRequest?{shopify_link:packageIdRequest()}: {})});window.location.assign(result.url);}
      catch(error){form.querySelector('[data-package-feedback]').textContent=error.message;}
      finally{delete form.dataset.saving;if(submit)submit.disabled=false;}});
    form.querySelector('[data-package-duplicate]')?.addEventListener('click',async()=>{try{const r=await api(`/packaging/${data.id}/duplicate`,{});window.location.assign(r.url);}catch(e){form.querySelector('[data-package-feedback]').textContent=e.message;}});
  }
  document.querySelector('[data-package-archive]')?.addEventListener('click',async e=>{const n=e.currentTarget;if(!confirm(`Archive locally? ${n.dataset.dependentCount} assignments may need review. Shopify packages stay unchanged.`))return;try{await api(`/packaging/${n.dataset.packageArchive}/archive`,{confirm:true,version:Number(n.dataset.packageVersion)});window.location.assign('/packaging/');}catch(error){document.querySelector('[data-package-feedback]').textContent=error.message;}});
})();
