// JSON-owner adapter only. Row modals, timing and all arithmetic are shared.
import {initBomRows} from './bom-planner-rows.js';
const keys=['labour_minutes','fee_profile','price_rounding','labour_rate','buffer','print_waste_rate','markup','other'];
const clone=value=>structuredClone(value);
const make=(tag,text)=>{const el=document.createElement(tag);if(text!=null)el.textContent=text;return el;};
const id=()=> 'line-'+crypto.randomUUID().replaceAll('-','').slice(0,24);
const values=row=>Object.fromEntries([...row.querySelectorAll('input,select')].map(c=>[c.name,c.value]));
function restore(control,value){
  if(control.tagName==='SELECT'&&value!=null&&value!==''&&![...control.options].some(o=>o.value===String(value)))control.add(new Option('Unavailable retained reference: '+value,String(value)));
  control.value=value??'';
}
const token=()=>document.querySelector('meta[name="csrf-token"]')?.content||'';
async function post(url,body){const r=await fetch(url,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':token()},body:JSON.stringify(body)});const data=await r.json();if(!r.ok)throw new Error(data.error||'Unable to validate BOM.');return data;}
function reviewTable(parent,title,headings,rows){
  parent.append(make('h3',title));if(!rows.length){parent.append(make('p','None recorded.'));return;}
  const wrap=make('div');wrap.className='table-responsive';const table=make('table');table.className='table';
  const header=table.createTHead().insertRow();for(const text of headings)header.append(make('th',text));
  const body=table.createTBody();for(const row of rows){const tr=body.insertRow();for(const value of row)tr.insertCell().textContent=value??'Not recorded';}wrap.append(table);parent.append(wrap);
}

export async function mountCatalogueEditor(editor,box,view,generation) {
  try {
    editor.workspaceMarkup ??= fetch(editor.workspaceUrl,{credentials:'same-origin'}).then(async r=>{if(!r.ok)throw new Error('BOM workspace unavailable.');return r.text();});
    const html=await editor.workspaceMarkup;
    if(generation!==editor.renderGeneration)return;
    box.innerHTML=html;
    const scope=box.querySelector('[data-catalogue-costing]'), local=editor.value;
    const product=editor.kind==='override', selected=local.mode||(Object.keys(local).some(key=>key!=='mode')?'add':'inherit');
    const writable=!product||selected!=='inherit';
    const policyDetails=make('details');policyDetails.append(make('summary','Advanced inventory / legacy production defaults'));
    const policy=make('fieldset');policy.className='workshop-fields';policy.append(make('legend','Optional reference metadata — not required to process orders'));
    for(const [key,label,choices] of [
      ['type','Manufacturing reference (optional)',[['','Inherited / unspecified'],['card','Greeting card'],['print','Direct 3D production'],['assembly','3D component assembly'],['laser','Laser production'],['stock','Ready-made stock'],['mixed','Multi-process production']]],
      ...['personalised','needs_picking'].map(key=>[key,({personalised:'Genuinely personalised / bespoke',needs_picking:'Legacy pre-production picking preference'})[key],[['','Inherited / unspecified'],['true','Yes'],['false','No']]])]) {
      const labelEl=make('label',label),select=make('select');select.className='form-select';select.dataset.workflow=key;
      for(const [value,text]of choices)select.add(new Option(text,value));restore(select,local.workflow?.[key]);select.disabled=!writable;
      select.onchange=()=>{const next={...(local.workflow||{})};if(select.value==='')delete next[key];else next[key]=key==='type'?select.value:select.value==='true';if(Object.keys(next).length)local.workflow=next;else delete local.workflow;editor.changed(false);};
      labelEl.append(select);policy.append(labelEl);
    }
    policyDetails.append(policy);scope.prepend(policyDetails);
    const stockLabel=make('label','Finished inventory to pick (optional — replaces component picking only)'),stockPicker=make('select');stockPicker.className='form-select';stockPicker.dataset.workflow='stock_item_id';
    stockPicker.add(new Option('Use effective BOM requirements / inherited selection',''));
    for(const item of editor.items.filter(i=>i.kind!=='material'&&i.unit==='piece'))stockPicker.add(new Option(item.name,String(item.id)));
    restore(stockPicker,local.workflow?.stock_item_id);stockPicker.disabled=!writable;
    stockPicker.onchange=()=>{local.workflow??={};if(stockPicker.value)local.workflow.stock_item_id=Number(stockPicker.value);else delete local.workflow.stock_item_id;editor.changed(false);};stockLabel.append(stockPicker);policy.append(stockLabel);
    const status=make('p');status.setAttribute('role','status');scope.prepend(status);
    if(product) {
      const label=make('label','Product BOM inheritance'), mode=make('select');mode.className='form-select';mode.dataset.productBomMode='';
      for(const [v,label]of [['inherit','Inherit All'],['add','Inherit Plus'],['replace','Rewrite']])mode.add(new Option(label,v));mode.value=selected;label.append(mode);scope.prepend(label);
      mode.addEventListener('change',()=>{
        const next=mode.value;editor.modeDrafts??={};
        if((Object.keys(local.lines||{}).length||local.costing)&&!confirm('Switch BOM mode? Your current working BOM will be retained as an unsaved draft for this editor session; only the selected mode is saved.')){mode.value=selected;return;}
        editor.modeDrafts[selected]=clone(Object.fromEntries(Object.entries(local).filter(([key])=>key!=='mode')));
        for(const key of Object.keys(local))delete local[key];local.mode=next;
        if(next!=='inherit')Object.assign(local,clone(editor.modeDrafts[next]||{}));
        editor.changed();
      });
    }
    const costLabel=make('label','Shopify Cost per item source'),cost=make('select');cost.className='form-select';
    for(const [v,label]of [['','Automatic / inherited full BOM production cost'],['unconfigured','Unconfigured — leave Shopify unchanged'],['calculated','Calculated from full BOM production cost'],['manual','Manual cost override']])cost.add(new Option(label,v));cost.value=local.cost?.mode||'';costLabel.append(cost);scope.append(costLabel);
    const manual=make('input');manual.className='form-control';manual.inputMode='decimal';manual.value=local.cost?.amount??'';manual.setAttribute('aria-label','Manual Shopify cost £');manual.hidden=cost.value!=='manual';scope.append(manual);
    cost.disabled=!writable;manual.disabled=!writable;
    cost.onchange=()=>{manual.hidden=cost.value!=='manual';if(cost.value)local.cost=cost.value==='manual'?{mode:'manual',amount:manual.value}:{mode:cost.value};else delete local.cost;editor.changed(false);};
    manual.oninput=()=>{local.cost={mode:'manual',amount:manual.value};editor.changed(false);};
    // Packaging remains inventory-backed; no hypothetical entry controls exist.
    for(const select of scope.querySelectorAll('[data-bom-rows="packaging"] [name=item_id],template[data-row-template="packaging"]')) {
      const picker=select.tagName==='TEMPLATE'?select.content.querySelector('[name=item_id]'):select;
      for(const option of [...picker.options])if(option.value&&option.dataset.kind!=='purchased')option.remove();
    }
    const preserved={}, baseLines=window.TLCProduction.rows([editor.shared||{}]);
    // Display the inherited source in its normal section, never as local JSON.
    if(product&&selected!=='replace')for(const [key,line]of baseLines){
      if(line.excluded||(writable&&Object.hasOwn(local.lines||{},key)))continue;
      const kind=line.purpose==='packaging'?'packaging':'inventory';
      const row=scope.querySelector(`template[data-row-template="${kind}"]`).content.firstElementChild.cloneNode(true);
      row.dataset.inherited='yes';row.dataset.lineId=key;
      for(const control of row.querySelectorAll('input,select'))restore(control,line[control.name]);
      scope.querySelector(`[data-bom-rows="${kind}"] tbody`).append(row);
    }
    for(const [key,line]of Object.entries(writable?local.lines||{}:{})) {
      if(line.op==='remove'||line.op==='modify'&&!baseLines.has(key)){preserved[key]=clone(line);continue;}
      const displayed=line.op==='modify'?{...baseLines.get(key),...line}:line;
      const kind=displayed.purpose==='packaging'?'packaging':'inventory';
      const row=scope.querySelector(`template[data-row-template="${kind}"]`).content.firstElementChild.cloneNode(true);row.dataset.lineId=key;
      row.dataset.operation=line.op;
      for(const control of row.querySelectorAll('input,select'))restore(control,displayed[control.name]);
      scope.querySelector(`table[data-bom-rows="${kind}"] tbody`).append(row);
    }
    const inheritedProcesses=product&&selected!=='replace'?editor.shared?.costing?.equipment||[]:[];
    for(const [line,isInherited]of [...inheritedProcesses.map(line=>[line,true]),...(writable?local.costing?.equipment||[]:[]).map(line=>[line,false])]) {
      const row=scope.querySelector('template[data-row-template="process"]').content.firstElementChild.cloneNode(true);
      if(isInherited)row.dataset.inherited='yes';
      for(const c of row.querySelectorAll('input,select'))restore(c,line[c.name]??c.value);
      scope.querySelector('[data-bom-rows="process"] tbody').append(row);
    }
    for(const key of keys){
      const control=scope.querySelector(`[name="${key}"]`);
      restore(control,(!writable?editor.shared:local)?.costing?.[key]);
      if(product&&selected!=='replace'&&editor.shared?.costing?.[key]!=null){
        const note=make('small',`Inherited: ${control.tagName==='SELECT'?[...control.options].find(o=>o.value===String(editor.shared.costing[key]))?.textContent||editor.shared.costing[key]:editor.shared.costing[key]} (read-only)`);
        note.dataset.inheritedValue=key;control.closest('label').append(note);
      }
    }
    function overrideCount(){scope.querySelector('[data-override-count]').textContent=`(${writable?[...scope.querySelectorAll('[data-override]')].filter(c=>c.value.trim()!=='').length:0} active overrides)`;}
    overrideCount();
    const inherited=scope.querySelector('[data-inherited-costing]');
    const show=result=>{
      status.textContent=product&&!writable?'Inherit All: collection requirements and costing are read-only. No inherited copy is saved in this product override.':'';
      inherited.replaceChildren();
      const h=make('h4',product?(selected==='replace'?'Effective replacement BOM':'Effective BOM — inherited lines are read-only'):'Effective collection BOM');inherited.append(h);
      if(product&&!writable&&result?.legacy_costing){
        status.textContent='Legacy local processing assumptions are retained for compatibility. Explicitly confirm Inherit All below to use only collection processing/defaults, or choose Inherit Plus / Rewrite to author JSON costing.';
        const confirmAll=make('button','Confirm Inherit All — use collection costing');confirmAll.type='button';confirmAll.className='btn btn-outline-primary';
        confirmAll.onclick=()=>{if(!confirm('Use only collection costing/defaults? The legacy local profile is retained, but will no longer apply to this explicit Inherit All mode.'))return;editor.value={...editor.value,mode:'inherit'};editor.changed();};inherited.append(confirmAll);
      }
      const wrap=make('div');wrap.className='table-responsive';const table=make('table');table.className='table bom-lines';
      const head=table.createTHead().insertRow();for(const name of ['Requirement','Quantity','Unit','Source','Material cost'])head.append(make('th',name));
      const body=table.createTBody();for(const row of result?.rows||[]){const tr=body.insertRow();for(const [i,text]of [row.name||'Inventory #'+row.item_id,row.quantity,row.unit,row.excluded?'Excluded':row.origin||'Inherited',row.problem||(row.cost==null?'Unknown':'£'+row.cost)].entries()){const cell=tr.insertCell();cell.textContent=text;cell.dataset.column=head.cells[i].textContent;}}
      wrap.append(table);inherited.append(wrap);
      if(product&&selected!=='replace'&&editor.shared?.costing){
        const heading=make('h4','Inherited collection processing — read-only');inherited.append(heading);
        const equipmentPicker=scope.querySelector('[name=equipment_id]')||scope.querySelector('template[data-row-template=process]').content.querySelector('[name=equipment_id]');
        const assumptions=make('ul');for(const process of editor.shared.costing.equipment||[]){const option=[...equipmentPicker.options].find(o=>o.value===String(process.equipment_id));assumptions.append(make('li',`${option?.textContent||'Equipment #'+process.equipment_id} · ${process.minutes} minutes · ${process.timing_mode==='batch'?'whole batch, '+process.usable_units+' usable items, '+process.allocation:'one finished item'}`));}
        for(const [key,value]of Object.entries(editor.shared.costing))if(key!=='equipment')assumptions.append(make('li',`${key.replaceAll('_',' ')}: ${value}`));inherited.append(assumptions);
      }
      if(Object.keys(preserved).length){inherited.append(make('p','Retained exclusions/conflicts remain unchanged unless explicitly reset.'));
        for(const [key,line]of Object.entries(preserved)){const note=make('p',`${key} · ${line.op} `);if(writable){const reset=make('button','Reset to inherited');reset.type='button';reset.className='btn btn-sm btn-outline-primary';reset.onclick=()=>{delete editor.value.lines[key];editor.changed();};note.append(reset);}inherited.append(note);}}
      const resultBox=scope.querySelector('[data-catalogue-results]');
      if(result?.profitability_html)resultBox.innerHTML=result.profitability_html;
      else resultBox.textContent=[...(result?.warnings||[]),...(result?.profitability?.missing||[])].join(' ')||'Add registered requirements to calculate the effective BOM.';
    };
    view.workspace=show;
    view.workspaceError=message=>{status.textContent=message;scope.querySelector('[data-catalogue-advanced]').open=true;scope.querySelector('[data-catalogue-results]').textContent='Costing preview unavailable until the inputs above are valid.';};
    show(editor.result(null));
    if(!writable) {
      initBomRows(scope,{readOnly:true});
      for(const key of keys)scope.querySelector(`[name="${key}"]`).disabled=true;
      scope.querySelector('[data-import-test]').disabled=true;
      scope.querySelector('[data-import-scenario]').disabled=true;
    } else {
      initBomRows(scope);
      scope.addEventListener('bom:before-apply',event=>{
        const {row,values:v}=event.detail;
        const sameItem=v.item_id&&(editor.result(null)?.rows||[]).some(r=>!r.excluded&&String(r.item_id)===v.item_id&&r.line_id!==row.dataset.lineId);
        const sameProcess=v.equipment_id&&(editor.result(null)?.costing_inputs?.equipment||[]).filter(p=>String(p.equipment_id)===v.equipment_id).length>(row.isConnected?1:0);
        if((sameItem||sameProcess)&&!confirm('This inventory item or equipment profile is already in the effective BOM. This adds another requirement and its cost. Continue only if the extra quantity/runtime is intentional.'))event.preventDefault();
      });
      function serialize(){
        const lines=clone(preserved);
        for(const kind of ['inventory','packaging'])for(const row of scope.querySelectorAll(`[data-bom-rows="${kind}"] tbody tr:not([data-inherited])`)){
          row.dataset.lineId ||= id();const v=values(row), line={item_id:Number(v.item_id),quantity:v.quantity,unit:v.unit};
          if(kind==='packaging')line.purpose='packaging';
          if(v.print_material)line.print_material=v.print_material;
          if(row.dataset.operation==='modify'){
            const base=baseLines.get(row.dataset.lineId);line.print_material=v.print_material||'';
            for(const key of Object.keys(line))if(String(line[key])===String(base[key]??''))delete line[key];
            if(Object.keys(line).length)lines[row.dataset.lineId]={op:'modify',...line};
          }else lines[row.dataset.lineId]={op:'add',...line};
        }
        local.lines=lines;
        const costing={};for(const key of keys){const c=scope.querySelector(`[name="${key}"]`);if(c.value.trim()!=='')costing[key]=c.value;}
        costing.equipment=[...scope.querySelectorAll('[data-bom-rows="process"] tbody tr:not([data-inherited])')].map(row=>{const v=values(row);return {...v,equipment_id:Number(v.equipment_id)};});
        local.costing=costing;
        overrideCount();
        editor.changed(false);
      }
      scope.addEventListener('input',event=>{if(event.target.closest('table[data-bom-rows]')||keys.includes(event.target.name))serialize();});
      scope.addEventListener('change',event=>{if(keys.includes(event.target.name))serialize();});
    }
    scope.querySelector('[data-import-test]').onclick=async()=>{
      const scenario=Number(scope.querySelector('[data-import-scenario]').value);if(!scenario){status.textContent='Choose a saved test BOM first.';return;}
      try {
        const data=await post(editor.importUrl,{kind:editor.kind,scenario_id:scenario,production:editor.value});
        const dialog=make('dialog');dialog.className='packaging-dialog bom-dialog';dialog.setAttribute('aria-label','Review saved test BOM import');
        dialog.append(make('h2',data.scenario.name),make('p',`Saved revision ${data.scenario.version}. ${data.notice}`));
        if(data.inputs._snapshot?.result?.cost!=null)dialog.append(make('p',`Retained historical manufacturing snapshot: £${data.inputs._snapshot.result.cost}. The reviewed import below uses current costs, not a rewrite of that snapshot.`));
        const eligibility=make('p',data.eligible?'Eligible — review and explicitly apply to the unsaved working BOM.':'Import blocked — no changes will be applied.');eligibility.className=data.eligible?'alert alert-info':'alert alert-warning';dialog.append(eligibility);
        const problems=make('ul');for(const reason of data.problems)problems.append(make('li',reason));dialog.append(problems);
        const name=line=>editor.items.find(i=>i.id===line.item_id)?.name||line.name||'Unresolved inventory #'+line.item_id;
        const lines=Array.isArray(data.inputs.lines)?data.inputs.lines.filter(line=>line&&typeof line==='object'):[];
        for(const packaging of [false,true])reviewTable(dialog,packaging?'Packaging':'Materials & components',['Requirement','Quantity','Unit','Source'],lines.filter(line=>(line.purpose==='packaging')===packaging).map(line=>[name(line),line.quantity,line.unit,line.source==='manual'?'Hypothetical — cannot import':'Inventory #'+line.item_id]));
        const picker=scope.querySelector('template[data-row-template=process]').content.querySelector('[name=equipment_id]');
        reviewTable(dialog,'Production processes',['Equipment / profile','Total minutes','Timing / allocation'],(Array.isArray(data.inputs.equipment)?data.inputs.equipment:[]).filter(line=>line&&typeof line==='object').map(line=>[[...picker.options].find(o=>o.value===String(line.equipment_id))?.textContent||'Unresolved equipment #'+line.equipment_id,line.minutes,line.timing_mode==='batch'?`${line.usable_units} usable items · ${line.allocation}${line.allocation==='share'?' · '+line.share_percent+'%':''}`:'One finished item']));
        const assumptions=make('dl');assumptions.className='workshop-facts';dialog.append(make('h3','Labour, allowances and overrides'));
        for(const key of keys){assumptions.append(make('dt',key.replaceAll('_',' ')),make('dd',data.inputs[key]??'Inherited / default'));}dialog.append(assumptions);
        if(data.effective)reviewTable(dialog,'Resulting effective BOM',['Requirement','Quantity','Unit','Source'],data.effective.rows.filter(line=>!line.excluded).map(line=>[name(line),line.quantity,line.unit,line.origin]));
        if(data.result)dialog.append(make('p',`Current manufacturing estimate: ${data.result.cost===null?'Incomplete':'£'+data.result.cost} · ${data.result.state}. Suggested selling price: ${data.result.recommended_price===null?'Not calculated':'£'+data.result.recommended_price}.`));
        dialog.append(make('p','Scenario selling prices are not imported. Collection/product exceptions and the separate Shopify Cost source are retained.'));
        const actions=make('div');actions.className='page-actions';const apply=make('button','Apply Import'),cancel=make('button','Cancel');apply.type=cancel.type='button';apply.className='btn btn-primary';cancel.className='btn btn-ghost';apply.disabled=!data.eligible;
        cancel.onclick=()=>dialog.close();apply.onclick=()=>{if((Object.keys(editor.value.lines||{}).length||editor.value.costing)&&!confirm('Apply this reviewed import to the working BOM? This replaces or adds to the selected mode only. Nothing is saved until Save Metadata.'))return;editor.value=clone(data.production);dialog.close();editor.changed();};
        actions.append(apply,cancel);dialog.append(actions);dialog.addEventListener('close',()=>dialog.remove());dialog.addEventListener('click',e=>{if(e.target===dialog){const b=dialog.getBoundingClientRect();if(e.clientX<b.left||e.clientX>b.right||e.clientY<b.top||e.clientY>b.bottom)dialog.close();}});document.body.append(dialog);dialog.showModal();
      }catch(error){status.textContent=error.message;}
    };
    // Existing fixed navigation is retained; measure its real viewport footprint.
    const nav=document.querySelector('.mobile-bottom-nav');if(nav){const measure=()=>{if(!scope.isConnected)return;scope.style.setProperty('--bom-nav-clearance',getComputedStyle(nav).position==='fixed'?`${Math.max(0,innerHeight-nav.getBoundingClientRect().top)}px`:'0px');};measure();}
  }catch(error){if(generation===editor.renderGeneration)box.textContent=error.message;}
}
